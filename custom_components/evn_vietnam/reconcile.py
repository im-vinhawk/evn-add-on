"""Reconcile each bill's kWh with the stored daily kWh and plan the new-bill events. HA-free.

A billing period is the unit: ``(year, month, ky)``.  Invoices of one period are combined; the
reconciliation belongs to the period's first invoice.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
import hashlib
from typing import Any, Collection, Iterable, Mapping, Sequence

from .calculation import bill_payment_status, as_float, bill_period_parts
from .const import BILL_DAY_OFFSET, BILL_UPDATE_DAYS, MAX_BILL_KEYS
from .models import contains_customer_code

_EPSILON = 1e-9


def _iso_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def period_key(year: int, month: int, ky: int) -> str:
    return f"{year:04d}-{month:02d}-{ky}"


def _key_order(key: str) -> tuple[int, int, int]:
    year, month, ky = key.split("-")
    return int(year), int(month), int(ky)


def bill_id(entry_id: str, code: str, key: str) -> str:
    """Opaque identity of one period of one code: usable as a notification id, reveals no code."""
    return hashlib.sha256(f"{entry_id}|{code}|{key}".encode()).hexdigest()[:12]


def safe_label(code: str, nickname: str | None) -> str:
    """The nickname when it holds nothing code-like, else the masked last four characters."""
    name = str(nickname or "").strip()
    if name and not contains_customer_code(name) and code.lower() not in name.lower():
        return name
    return f"…{code[-4:]}"


def period_key_of(bill: Mapping[str, Any]) -> str | None:
    parts = bill_period_parts(bill)
    return None if parts is None else period_key(*parts)


def _owed(bill: Mapping[str, Any]) -> int | None:
    return _int_or_none(bill.get("amount_owed"))


def _fold_payment(period: dict[str, Any], bill: Mapping[str, Any]) -> None:
    """Add an invoice to a period's payment state: unpaid beats unknown beats paid, what is owed adds up."""
    order = ("unpaid", "unknown", "paid")
    status = bill_payment_status(bill)
    if order.index(status) < order.index(period["payment_status"]):
        period["payment_status"] = status
    if _owed(bill) is not None:
        period["amount_owed"] = (period["amount_owed"] or 0) + _owed(bill)
    if bill.get("due_date"):
        period["due_date"] = min(period["due_date"] or str(bill["due_date"]), str(bill["due_date"]))
    period["from_unpaid"] = period["from_unpaid"] or bill.get("bill_source") == "unpaid"


def group_periods(bills: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Billing periods, oldest first. Kwh, dates and calculated amount come from the first invoice; amounts add up."""
    periods: dict[str, dict[str, Any]] = {}
    for bill in bills:
        key = period_key_of(bill)
        if key is None:
            continue
        amount = int(as_float(bill.get("total_amount")))
        period = periods.get(key)
        if period is None:
            year, month, ky = _key_order(key)
            kwh = bill.get("total_kwh")
            periods[key] = {
                "key": key, "year": year, "month": month, "ky": ky,
                "period_start": str(bill.get("period_start") or ""), "period_end": str(bill.get("period_end") or ""),
                "bill_kwh": None if kwh is None else float(kwh),
                "total_amount": amount, "calculated_amount": bill.get("calculated_amount"),
                "payment_status": "paid", "due_date": "", "amount_owed": None, "from_unpaid": False,
            }
            period = periods[key]
        else:
            period["total_amount"] += amount
        _fold_payment(period, bill)
    return sorted(periods.values(), key=lambda item: _key_order(item["key"]))


def _blank_result(period: Mapping[str, Any], threshold_kwh: float) -> dict[str, Any]:
    return {
        "key": period["key"], "period_start": period["period_start"], "period_end": period["period_end"],
        "window_start": None, "window_end": None, "collected_kwh": None, "diff_kwh": None, "missing_days": None,
        "status": "no_kwh", "threshold_kwh": threshold_kwh, "paired_with": None, "compensates_previous": False,
    }


def reconcile_period(
    period: Mapping[str, Any], days: Mapping[str, float], threshold_kwh: float, previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a period's bill kWh with the stored days of its window; `previous` is the adjacent earlier result."""
    result = _blank_result(period, threshold_kwh)
    start, end = _iso_day(period["period_start"]), _iso_day(period["period_end"])
    if start is None or end is None or end < start:
        return result
    first, last = start + timedelta(days=BILL_DAY_OFFSET), end + timedelta(days=BILL_DAY_OFFSET)
    window = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    present = [days[day] for day in window if day in days]
    result["window_start"], result["window_end"] = first.isoformat(), last.isoformat()
    result["collected_kwh"] = round(sum(present), 2)
    result["missing_days"] = len(window) - len(present)
    bill_kwh = period["bill_kwh"]
    if bill_kwh is None:
        return result
    result["diff_kwh"] = round(result["collected_kwh"] - bill_kwh, 2)
    if abs(result["diff_kwh"]) <= threshold_kwh + _EPSILON:
        result["status"] = "match"
    elif _cancels_with(result, start, previous, threshold_kwh):
        result["status"], result["paired_with"], result["compensates_previous"] = "boundary", previous["key"], True
    else:
        result["status"] = "incomplete" if result["missing_days"] > 0 else "mismatch"
    return result


def _cancels_with(result: Mapping[str, Any], start: date, previous: Mapping[str, Any] | None, threshold_kwh: float) -> bool:
    """EVN cut the periods one day apart: complete, adjacent, opposite and cancelling within the threshold."""
    if previous is None or previous["diff_kwh"] is None:
        return False
    previous_end = _iso_day(previous["period_end"])
    if previous_end is None or previous_end + timedelta(days=1) != start:
        return False
    if result["missing_days"] != 0 or previous["missing_days"] != 0:
        return False
    diff, other = result["diff_kwh"], previous["diff_kwh"]
    return (
        abs(diff) > threshold_kwh and abs(other) > threshold_kwh and diff * other < 0
        and abs(diff + other) <= threshold_kwh + _EPSILON
    )


def reconcile_all(periods: Sequence[Mapping[str, Any]], days: Mapping[str, float], threshold_kwh: float) -> dict[str, dict[str, Any]]:
    """Result per period key; a boundary pair is shown as `boundary` on both of its periods."""
    results = {period["key"]: reconcile_period(period, days, threshold_kwh) for period in periods}
    by_end = {result["period_end"]: result for result in results.values() if result["period_end"]}
    for period in periods:
        result = results[period["key"]]
        start = _iso_day(period["period_start"])
        if start is None or result["status"] in ("match", "no_kwh"):
            continue
        before = by_end.get((start - timedelta(days=1)).isoformat())
        if before is None or before["key"] == result["key"]:
            continue
        previous = results[before["key"]]  # current result: it may have been paired already
        if previous["status"] == "boundary":
            continue
        paired = reconcile_period(period, days, threshold_kwh, previous=previous)
        if paired["status"] == "boundary":
            results[period["key"]] = paired
            previous["status"], previous["paired_with"] = "boundary", paired["key"]
    return results


_ROW_FIELDS = ("collected_kwh", "diff_kwh", "missing_days", "reconcile_status", "paired_with")


def annotate_bills(
    bills: Iterable[Mapping[str, Any]], days: Mapping[str, float] | None, threshold_kwh: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Copies of the bill rows with the canonical period and, when `days` is known, the reconciliation.

    Returns (rows, periods, results).  Without `days` no result exists, so no status is invented.
    """
    rows = [dict(bill) for bill in bills]
    periods = group_periods(rows)
    results = reconcile_all(periods, days, threshold_kwh) if days is not None else {}
    announced: set[str] = set()
    for row in rows:
        row["year"], row["month"], row["ky"] = _int_or_none(row.get("NAM")), _int_or_none(row.get("THANG")), _int_or_none(row.get("KY"))
        for field in _ROW_FIELDS:
            row[field] = None
        key = period_key_of(row)
        if key is None:
            row["reconcile_status"] = "no_period"
            continue
        if key in announced:
            continue
        announced.add(key)
        result = results.get(key)
        if result is not None:
            row["collected_kwh"], row["diff_kwh"], row["missing_days"] = (
                result["collected_kwh"], result["diff_kwh"], result["missing_days"],
            )
            row["reconcile_status"], row["paired_with"] = result["status"], result["paired_with"]
    return rows, periods, results


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def _event(
    entry_id: str, label: str, period: Mapping[str, Any], result: Mapping[str, Any], *,
    identity: str, reason: str, previous_status: str | None,
) -> dict[str, Any]:
    return {
        "bill_id": identity, "entry_id": entry_id, "label": label,
        "period": f"{period['month']:02d}/{period['year']}", "ky": period["ky"],
        "period_start": period["period_start"] or None, "period_end": period["period_end"] or None,
        "window_start": result["window_start"], "window_end": result["window_end"],
        "bill_kwh": period["bill_kwh"], "collected_kwh": result["collected_kwh"], "diff_kwh": result["diff_kwh"],
        "missing_days": result["missing_days"], "status": result["status"], "previous_status": previous_status,
        "reason": reason, "compensates_previous": result["compensates_previous"],
        "total_amount": period["total_amount"], "calculated_amount": period["calculated_amount"],
        "threshold_kwh": result["threshold_kwh"],
        "payment_status": period["payment_status"], "due_date": period["due_date"] or None,
        "amount_owed": period["amount_owed"],
    }


def _month_end(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def plan_events(
    entry_id: str, code: str, label: str, periods: Sequence[Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]], state: Mapping[str, Mapping[str, Any]] | None, today: date,
    threshold_kwh: float, *, fresh_keys: Collection[str] | None = None, unpaid_seeding: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Events to fire and the new seen-period state of one code.

    `state` None or empty means the code was never seen (an empty first fetch is not a sighting): every period
    is recorded, and only the previous calendar month or later is announced.  Later, an unseen period is `new`; within BILL_UPDATE_DAYS of being first
    seen, a change of status or amount is one `update`; paying a bill alone is not one.

    `unpaid_seeding` is the first fresh read of the unpaid list for this code: its periods follow the same
    quiet rule as a first sighting, while periods of the paid history keep announcing.  `fresh_keys` limits the
    plan to periods read in this refresh (None: all); the others stay listed but are never announced.
    """
    seeding = not state
    new_state = {key: dict(entry) for key, entry in (state or {}).items()}
    last_quiet = _month_index(today.year, today.month) - 2
    events: list[dict[str, Any]] = []
    for period in periods:
        key, result = period["key"], results[period["key"]]
        if fresh_keys is not None and key not in fresh_keys:
            continue
        status, amount = result["status"], period["total_amount"]
        # The earlier period of a boundary pair is explained by the later one; it never gets its own notice.
        earlier_of_pair = status == "boundary" and not result["compensates_previous"]
        entry = new_state.get(key)
        if entry is None:
            identity = bill_id(entry_id, code, key)
            quiet = seeding or (unpaid_seeding and period["from_unpaid"])
            if earlier_of_pair or (quiet and _month_index(period["year"], period["month"]) <= last_quiet):
                new_state[key] = {
                    "bill_id": identity, "first_seen": _month_end(period["year"], period["month"]),
                    "status": status, "amount": amount,
                }
                continue
            new_state[key] = {"bill_id": identity, "first_seen": today.isoformat(), "status": status, "amount": amount}
            events.append(_event(entry_id, label, period, result, identity=identity, reason="new", previous_status=None))
            continue
        first_seen = _iso_day(entry.get("first_seen"))
        if earlier_of_pair or first_seen is None or (today - first_seen).days > BILL_UPDATE_DAYS:
            continue
        if status == "no_kwh" and entry["status"] != "no_kwh":
            continue  # the readings failing for a poll is not news; the bill comes back with its kWh
        if entry["status"] != status or entry["amount"] != amount:
            events.append(_event(
                entry_id, label, period, result, identity=entry["bill_id"], reason="update",
                previous_status=entry["status"],
            ))
            entry["status"], entry["amount"] = status, amount
    # Only periods that left the bill list are forgotten: one still in it would come back as new.
    listed = {period["key"] for period in periods}
    for key in sorted((key for key in new_state if key not in listed), key=_key_order):
        if len(new_state) <= MAX_BILL_KEYS:
            break
        del new_state[key]
    return events, new_state
