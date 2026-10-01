"""Persistent per-code daily kWh and the state of the historical backfill. HA-free.

The dict handled here is what Home Assistant's ``Store`` writes to disk::

    {"daily": {code: {"YYYY-MM-DD": kwh}},
     "meta": {code: {"cursor": "YYYY-MM" | None, "empty": int, "done": bool, "prev_refresh": "YYYY-MM-DD",
                     "failures": int, "tail_try": ISO datetime with offset | ""}},
     "series": {statistic_id: {"start": "YYYY-MM-DD", "count": int, "scope": str}},
     "bills": {code: {"YYYY-MM-K": {"bill_id": 12 hex, "first_seen": "YYYY-MM-DD", "status": str, "amount": int}}}}
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import re
from typing import Any, Iterable, Mapping

from .const import PREVIOUS_MONTH_TAIL_DAYS, PREVIOUS_MONTH_TAIL_RETRY

STORE_VERSION = 1
# A backfill that meets this many months in a row without any row has reached the start of the data.
BACKFILL_EMPTY_LIMIT = 2
# Only the first days of a month can still see EVN correct the month before.
PREVIOUS_MONTH_REFRESH_DAYS = 5


_CURSOR = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_BILL_KEY = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-\d+$")
_BILL_ID = re.compile(r"^[0-9a-f]{12}$")
_BILL_STATUSES = frozenset({"match", "boundary", "incomplete", "mismatch", "no_kwh"})


def empty_store() -> dict[str, Any]:
    return {"daily": {}, "meta": {}, "series": {}, "bills": {}}


def _valid_day(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return False
    return isinstance(value, str)


def normalize_store(raw: Any) -> dict[str, Any]:
    """Load a stored dict defensively: anything malformed is dropped, never raised."""
    store = empty_store()
    if not isinstance(raw, Mapping):
        return store
    daily = raw.get("daily")
    for code, days in (daily.items() if isinstance(daily, Mapping) else ()):
        if not isinstance(days, Mapping):
            continue
        kept = {
            day: float(kwh) for day, kwh in days.items()
            if _valid_day(day) and isinstance(kwh, (int, float)) and not isinstance(kwh, bool) and math.isfinite(kwh)
        }
        if kept:
            store["daily"][str(code)] = kept
    meta = raw.get("meta")
    for code, item in (meta.items() if isinstance(meta, Mapping) else ()):
        if isinstance(item, Mapping):
            store["meta"][str(code)] = _clean_meta(item)
    series = raw.get("series")
    for statistic_id, item in (series.items() if isinstance(series, Mapping) else ()):
        if isinstance(item, Mapping) and _valid_day(item.get("start")) and _count(item.get("count")) is not None:
            store["series"][str(statistic_id)] = {
                "start": item["start"], "count": _count(item["count"]), "scope": str(item.get("scope") or ""),
            }
    bills = raw.get("bills")
    for code, periods in (bills.items() if isinstance(bills, Mapping) else ()):
        if isinstance(periods, Mapping):
            store["bills"][str(code)] = {
                str(key): {name: entry[name] for name in ("bill_id", "first_seen", "status", "amount")}
                for key, entry in periods.items()
                if isinstance(key, str) and _BILL_KEY.match(key) and _valid_bill_entry(entry)
            }
    return store


def _valid_bill_entry(entry: Any) -> bool:
    return (
        isinstance(entry, Mapping)
        and isinstance(entry.get("bill_id"), str) and bool(_BILL_ID.match(entry["bill_id"]))
        and _valid_day(entry.get("first_seen"))
        and entry.get("status") in _BILL_STATUSES
        and isinstance(entry.get("amount"), int) and not isinstance(entry.get("amount"), bool)
    )


def _count(value: Any) -> int | None:
    """A non-negative integer, else None (booleans and floats are not counts)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _clean_meta(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only well-formed backfill state; a bad field falls back to its initial value."""
    meta = _new_meta()
    cursor = item.get("cursor")
    if isinstance(cursor, str) and _CURSOR.match(cursor):
        meta["cursor"] = cursor
    for key in ("empty", "failures"):
        meta[key] = _count(item.get(key)) or 0
    meta["done"] = bool(item.get("done"))
    if _valid_day(item.get("prev_refresh")):
        meta["prev_refresh"] = item["prev_refresh"]
    if _parse_aware(item.get("tail_try")) is not None:
        meta["tail_try"] = item["tail_try"]
    return meta


def _parse_aware(value: Any) -> datetime | None:
    """A timezone-aware ISO datetime, else None (a naive stamp cannot be compared with HA-local now)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def merge_daily(
    store: dict[str, Any], code: str, rows: Iterable[Mapping[str, Any]], unreported_from: str | None = None,
) -> bool:
    """Write rows into a code's days (same date overwrites); True if anything changed.

    A zero row dated on or after unreported_from is EVN not having reported that
    day yet, not a day without consumption, so it is not stored.
    """
    days = store["daily"].setdefault(code, {})
    changed = False
    for row in rows:
        day = str(row.get("date") or "")
        if not _valid_day(day):
            continue
        try:
            kwh = float(row.get("consumption"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(kwh):
            continue
        if kwh == 0 and unreported_from is not None and day >= unreported_from:
            continue
        if days.get(day) != kwh:
            days[day] = kwh
            changed = True
    if not days:
        store["daily"].pop(code, None)
    return changed


def code_days(store: Mapping[str, Any], code: str) -> dict[str, float]:
    return dict(store["daily"].get(code, {}))


def earliest_day(store: Mapping[str, Any], code: str) -> str | None:
    days = store["daily"].get(code)
    return min(days) if days else None


def _new_meta() -> dict[str, Any]:
    return {"cursor": None, "empty": 0, "done": False, "prev_refresh": "", "failures": 0, "tail_try": ""}


def backfill_meta(store: dict[str, Any], code: str) -> dict[str, Any]:
    return store["meta"].setdefault(code, _new_meta())


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _month_from_index(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def next_backfill_month(today: date, meta: Mapping[str, Any], cap: int) -> date | None:
    """First day of the next month to fetch walking back from last month; None when finished or capped."""
    if meta.get("done"):
        return None
    cursor = meta.get("cursor")
    target = _month_index(today) - 1 if not cursor else _month_index(date.fromisoformat(f"{cursor}-01"))
    if _month_index(today) - target > cap:
        return None
    return _month_from_index(target)


def record_backfill_month(meta: dict[str, Any], month_start: date, row_count: int) -> None:
    """Note a fetched month and step the cursor one month further back."""
    meta["failures"] = 0
    meta["empty"] = 0 if row_count > 0 else int(meta.get("empty", 0)) + 1
    meta["cursor"] = _month_from_index(_month_index(month_start) - 1).strftime("%Y-%m")
    if meta["empty"] >= BACKFILL_EMPTY_LIMIT:
        meta["done"] = True


def needs_previous_month_refresh(today: date, meta: Mapping[str, Any]) -> bool:
    return today.day <= PREVIOUS_MONTH_REFRESH_DAYS and meta.get("prev_refresh") != today.isoformat()


def needs_previous_month_tail(now: datetime, meta: Mapping[str, Any], days: Mapping[str, float]) -> bool:
    """Last month's final day is absent or a provisional 0 early in the month, and the last try is old enough."""
    if now.day > PREVIOUS_MONTH_TAIL_DAYS:
        return False
    tail_day = (now.date().replace(day=1) - timedelta(days=1)).isoformat()
    if days.get(tail_day):
        return False
    tried = _parse_aware(meta.get("tail_try"))
    return tried is None or now - tried >= PREVIOUS_MONTH_TAIL_RETRY


def mark_tail_tried(meta: dict[str, Any], now: datetime) -> None:
    meta["tail_try"] = now.isoformat(timespec="seconds")


def window_rows(days: Mapping[str, float], end: date, count: int) -> list[dict[str, Any]]:
    """Stored days of the `count` days ending at `end`, ascending, in the shape the live rows have."""
    first = (end - timedelta(days=count - 1)).isoformat()
    return [
        {
            "date": day, "day": day, "consumption": kwh, "kwh": kwh,
            "start_index": "-", "end_index": "-", "meter_point": "", "meter_number": "",
        }
        for day, kwh in sorted(days.items()) if first <= day <= end.isoformat()
    ]


def compose_window(days: Mapping[str, float], live_rows: Iterable[Mapping[str, Any]], end: date, count: int) -> list[dict[str, Any]]:
    """Stored days with this poll's live rows laid over the same dates."""
    rows = {row["date"]: row for row in window_rows(days, end, count)}
    first, last = (end - timedelta(days=count - 1)).isoformat(), end.isoformat()
    for row in live_rows:
        day = row.get("date")
        if _valid_day(day) and first <= day <= last:
            rows[day] = dict(row)
    return [rows[day] for day in sorted(rows)]


def day_values(rows: Iterable[Mapping[str, Any]], today: date) -> tuple[float, float | None]:
    """Today's kWh (0 until the first reading) and yesterday's, None while it is not in the rows."""
    by_date = {row.get("date"): row.get("consumption") for row in rows}
    yesterday = by_date.get((today - timedelta(days=1)).isoformat())
    today_kwh = by_date.get(today.isoformat())
    return (
        round(float(today_kwh), 2) if today_kwh is not None else 0.0,
        round(float(yesterday), 2) if yesterday is not None else None,
    )


def mark_previous_month_refreshed(meta: dict[str, Any], today: date) -> None:
    meta["failures"] = 0
    meta["prev_refresh"] = today.isoformat()


def record_failure(meta: dict[str, Any]) -> None:
    """An EVN error for this code: it goes behind the others until one of its requests works."""
    meta["failures"] = int(meta.get("failures", 0)) + 1
