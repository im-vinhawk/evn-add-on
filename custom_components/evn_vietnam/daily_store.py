"""Persistent per-code daily kWh and the state of the historical backfill. HA-free.

The dict handled here is what Home Assistant's ``Store`` writes to disk::

    {"daily": {code: {"YYYY-MM-DD": kwh}},
     "meta": {code: {"cursor": "YYYY-MM" | None, "empty": int, "done": bool, "prev_refresh": "YYYY-MM-DD"}},
     "series_start": {statistic_id: "YYYY-MM-DD"}}
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

STORE_VERSION = 1
# A backfill that meets this many months in a row without any row has reached the start of the data.
BACKFILL_EMPTY_LIMIT = 2
# Only the first days of a month can still see EVN correct the month before.
PREVIOUS_MONTH_REFRESH_DAYS = 5


def empty_store() -> dict[str, Any]:
    return {"daily": {}, "meta": {}, "series_start": {}}


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
            if _valid_day(day) and isinstance(kwh, (int, float)) and not isinstance(kwh, bool)
        }
        if kept:
            store["daily"][str(code)] = kept
    meta = raw.get("meta")
    for code, item in (meta.items() if isinstance(meta, Mapping) else ()):
        if isinstance(item, Mapping):
            store["meta"][str(code)] = {**_new_meta(), **{k: item[k] for k in _new_meta() if k in item}}
    series = raw.get("series_start")
    for statistic_id, day in (series.items() if isinstance(series, Mapping) else ()):
        if _valid_day(day):
            store["series_start"][str(statistic_id)] = day
    return store


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
    return {"cursor": None, "empty": 0, "done": False, "prev_refresh": ""}


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
    meta["empty"] = 0 if row_count > 0 else int(meta.get("empty", 0)) + 1
    meta["cursor"] = _month_from_index(_month_index(month_start) - 1).strftime("%Y-%m")
    if meta["empty"] >= BACKFILL_EMPTY_LIMIT:
        meta["done"] = True


def needs_previous_month_refresh(today: date, meta: Mapping[str, Any]) -> bool:
    return today.day <= PREVIOUS_MONTH_REFRESH_DAYS and meta.get("prev_refresh") != today.isoformat()


def mark_previous_month_refreshed(meta: dict[str, Any], today: date) -> None:
    meta["prev_refresh"] = today.isoformat()
