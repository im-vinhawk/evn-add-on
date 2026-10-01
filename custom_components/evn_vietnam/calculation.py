"""EVN normalization and per-meter aggregation rules.

EVN exposes one customer code per request.  Totals must therefore add each
meter's already-calculated values: never apply the tiered tariff to a combined
household kWh value.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
import logging
import re
from typing import Any, Iterable, Mapping, Sequence

from .tariff import TARIFF_ROWS, TIER_WIDTHS, TariffRow

_PERIOD_RE = re.compile(r"(?:Tháng\s*)?(\d{1,2})\s*/\s*(\d{4})", re.IGNORECASE)
_LOGGER = logging.getLogger(__name__)


def as_float(value: Any, default: float = 0.0) -> float:
    """Parse an EVN numeric value, preserving genuine zeroes."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_iso_date(value: Any) -> str:
    """Normalize EVN dates without silently substituting today's date."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return raw


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _tier_cost(kwh: Decimal, prices: Sequence[int], widths: Sequence[int | None]) -> Decimal:
    """Price kWh through the tiers; a width of None is unlimited."""
    remaining, cost = kwh, Decimal(0)
    for width, price in zip(widths, prices):
        used = remaining if width is None else min(remaining, Decimal(width))
        cost += used * price
        remaining -= used
        if remaining <= 0:
            break
    return cost


def _with_vat(pretax: Decimal, vat_rate: Decimal) -> int:
    pretax_vnd = round_half_up(pretax)
    return pretax_vnd + round_half_up(pretax_vnd * vat_rate)


def _tariff_on(day: date) -> TariffRow:
    """The latest tariff row in force on day; day must not precede the table."""
    return [row for row in TARIFF_ROWS if row.effective_from <= day][-1]


def calculate_tier_cost(kwh: float, vat_rate: float = 0.08) -> int:
    """Return the six-tier residential estimate in VND priced with the latest tariff row."""
    row = TARIFF_ROWS[-1]
    pretax = _tier_cost(max(Decimal(str(kwh)), Decimal(0)), row.prices, TIER_WIDTHS)
    return _with_vat(pretax, Decimal(str(vat_rate)))


def _is_calendar_month(start: date, end: date) -> bool:
    return start.day == 1 and end == date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


def is_calendar_month(start: date, end: date) -> bool:
    """True when [start, end] is one whole calendar month."""
    return _is_calendar_month(start, end)


def tariff_on(day: date) -> TariffRow:
    """The tariff row in force on day; day must not precede the table."""
    return _tariff_on(day)


def calculate_bill_amount(kwh: float, period_start: date, period_end: date) -> int | None:
    """Reproduce an EVN residential bill in VND including VAT, or None when it is not modelled.

    Only a whole calendar month inside the tariff table is modelled.  When a price
    change falls inside the month EVN splits it by days: every segment but the last
    gets round(kWh * days / month_days) kWh and round(limit * days / month_days)
    per tier limit, the last segment takes the remaining kWh, each segment is priced
    with the table in force in it, and VAT (rate of the last segment) is added to
    the summed pre-VAT amount.  Decimal throughout; halves round up.
    """
    if not _is_calendar_month(period_start, period_end) or period_start < TARIFF_ROWS[0].effective_from:
        return None
    total_days = (period_end - period_start).days + 1
    starts = [period_start, *(row.effective_from for row in TARIFF_ROWS if period_start < row.effective_from <= period_end)]
    total_kwh = max(Decimal(str(kwh)), Decimal(0))
    remaining, pretax = total_kwh, Decimal(0)
    for index, segment_start in enumerate(starts):
        is_last = index == len(starts) - 1
        days = ((period_end if is_last else starts[index + 1] - timedelta(days=1)) - segment_start).days + 1
        segment_kwh = remaining if is_last else Decimal(round_half_up(total_kwh * days / total_days))
        remaining = max(remaining - segment_kwh, Decimal(0))
        widths = [None if width is None else round_half_up(Decimal(width) * days / total_days) for width in TIER_WIDTHS]
        pretax += _tier_cost(segment_kwh, _tariff_on(segment_start).prices, widths)
    return _with_vat(pretax, _tariff_on(period_end).vat_rate)


def normalize_daily(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize daily consumption while keeping original EVN rows available."""
    normalized = []
    for row in rows:
        raw_date = row.get("NGAY") or row.get("NGAY_HTHI") or row.get("ngay") or row.get("date") or ""
        consumption = as_float(
            row.get("DIEN_TTHU", row.get("dienTthu", row.get("sanLuong", row.get("consumption", 0))))
        )
        normalized.append({
            "date": to_iso_date(raw_date),
            "day": str(raw_date),
            "consumption": consumption,
            "kwh": consumption,
            "start_index": row.get("CHISO_DAU", row.get("CHISO_CU", "-")),
            "end_index": row.get("CHISO_CUOI", row.get("CHISO_MOI", "-")),
            "meter_point": row.get("MA_DDO", row.get("maDdo", "")),
            "meter_number": row.get("SO_CTO", row.get("soCto", "")),
        })
    return sorted(normalized, key=lambda item: item["date"])


def _combined_tariff_verified(flags: Iterable[bool | None]) -> bool | None:
    """False if any meter's bills contradict the tier model, None if any is unknown, else True."""
    values = list(flags)
    if any(flag is False for flag in values):
        return False
    return None if not values or any(flag is None for flag in values) else True


def _sum_known_yesterday(values: list[Mapping[str, Any]]) -> float | None:
    """A total of yesterday is unknown as soon as one code's yesterday is: a partial sum would look complete."""
    if any("yesterday_consumption" in item and item["yesterday_consumption"] is None for item in values):
        return None
    return round(sum(as_float(item.get("yesterday_consumption")) for item in values), 2)


def _sum_known(values: list[Mapping[str, Any]], field: str) -> int | None:
    """A total is unknown as soon as one code's figure is: a partial sum would look complete."""
    if not values or any(item.get(field) is None for item in values):
        return None
    return sum(int(item[field]) for item in values)


def aggregate_overviews(overviews: Iterable[Mapping[str, Any]], codes: list[str]) -> dict[str, Any]:
    """Sum overview fields after each code's tariff has been calculated."""
    values = list(overviews)
    latest = max(values, key=lambda item: to_iso_date(item.get("latest_date")), default={})
    return {
        "customer_code": "__aggregate__",
        "selected_customer_codes": codes,
        "is_aggregate": True,
        "latest_index": "---",
        "latest_date": str(latest.get("latest_date") or ""),
        "today_consumption": round(sum(as_float(item.get("today_consumption")) for item in values), 2),
        "yesterday_consumption": _sum_known_yesterday(values),
        "current_month_consumption": round(sum(as_float(item.get("current_month_consumption")) for item in values), 2),
        "current_month_amount": sum(int(as_float(item.get("current_month_amount"))) for item in values),
        "tariff_verified": _combined_tariff_verified(item.get("tariff_verified") for item in values),
        "estimate_method": (
            "effective_price" if any(item.get("estimate_method") == "effective_price" for item in values) else "tiered"
        ),
        "unpaid_count": _sum_known(values, "unpaid_count"),
        "unpaid_amount": _sum_known(values, "unpaid_amount"),
        "projected_period_amount": _sum_known(values, "projected_period_amount"),
        "unpaid_fresh": bool(values) and all(item.get("unpaid_fresh") is True for item in values),
        "next_due_date": min((str(item["next_due_date"]) for item in values if item.get("next_due_date")), default=None),
    }


def aggregate_selected_overviews(
    meters: Mapping[str, Mapping[str, Any]],
    selected_codes: list[str],
    partial_errors: Mapping[str, str],
    history_only: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Aggregate only the selected successful meters with scoped provenance.

    history_only carries the last good bills of a selected code whose live call
    failed ({"bills": [...], "history_fetched_at": iso}); only the bill history
    uses it, so the aggregate does not silently lose a meter's past bills.
    """
    if len(selected_codes) < 2:
        return None
    history_only = history_only or {}
    successful_codes = [code for code in selected_codes if code in meters]
    aggregate = aggregate_overviews((meters[code] for code in successful_codes), selected_codes)
    aggregate["successful_customer_codes"] = successful_codes
    history = [
        meters[code] if code in meters else history_only[code]
        for code in selected_codes if code in meters or code in history_only
    ]
    aggregate["bills"] = aggregate_bills(item.get("bills", []) for item in history)
    aggregate["history_fetched_at"] = min(
        (str(item["history_fetched_at"]) for item in history if item.get("history_fetched_at")), default=""
    )
    aggregate["monthly_history"] = aggregate["bills"]
    aggregate["daily_history"] = aggregate_daily(
        (code, meters[code].get("daily_history", [])) for code in successful_codes
    )
    aggregate["partial_errors"] = {
        code: error for code, error in partial_errors.items() if code in selected_codes
    }
    aggregate["is_partial"] = bool(aggregate["partial_errors"])
    return aggregate


def aggregate_daily(daily_series: Iterable[tuple[str, Iterable[Mapping[str, Any]]]]) -> list[dict[str, Any]]:
    """Outer-join daily meter values by calendar date for a native HA chart."""
    by_date: dict[str, dict[str, Any]] = {}
    for code, rows in daily_series:
        for row in rows:
            day = to_iso_date(row.get("date") or row.get("day"))
            if not day:
                continue
            bucket = by_date.setdefault(day, {"date": day, "consumption": 0.0, "kwh": 0.0, "customer_codes": []})
            bucket["consumption"] = round(bucket["consumption"] + as_float(row.get("consumption")), 2)
            bucket["kwh"] = bucket["consumption"]
            if code not in bucket["customer_codes"]:
                bucket["customer_codes"].append(code)
    return [by_date[key] for key in sorted(by_date)]


def _as_int(value: Any) -> int | None:
    """Parse an EVN integer field; unusable values are unknown, not zero."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_PAYMENT_STATUS = {"DATT": "paid", "CHUATT": "unpaid"}
_IS_PAID = {"paid": True, "unpaid": False}


def _payment_status(row: Mapping[str, Any]) -> str:
    """paid / unpaid / unknown from EVN's status code; a payment date proves paid, nothing else is guessed."""
    code = _PAYMENT_STATUS.get(str(row.get("TTRANG_TTOAN") or "").strip().upper())
    if code:
        return code
    if row.get("NGAY_TTOAN"):
        return "paid"
    legacy = row.get("isPaid")
    if isinstance(legacy, bool):
        return "paid" if legacy else "unpaid"
    return "unknown"


def _iso_or_empty(value: Any) -> str:
    """An ISO date from EVN's date text, or "" when it is missing or unusable."""
    iso = to_iso_date(value)
    return iso if _DATE_ISO.match(iso) and _parse_iso_date(iso) is not None else ""


def normalize_bills(rows: Iterable[Mapping[str, Any]], source: str = "history") -> list[dict[str, Any]]:
    """Normalize official bills; their amount is never re-priced.

    The paid history carries no real kWh (DIEN_TTHU is 0 there), so kWh stays
    unknown (None) until attach_readings joins the monthly meter readings; a
    bill that still awaits payment carries its own.  `source` says which EVN list
    the rows came from ("history" or "unpaid").  Names, addresses and invoice ids
    are never copied.
    """
    result = []
    for row in rows:
        month, year = row.get("THANG", row.get("thang")), row.get("NAM", row.get("nam"))
        try:
            period = f"Tháng {int(month)}/{int(year)}"
        except (TypeError, ValueError):
            period = str(row.get("period") or "")
        kwh = as_float(row.get("DIEN_TTHU", row.get("totalKwh", 0)))
        status = _payment_status(row)
        result.append({
            "period": period,
            "total_kwh": kwh if kwh > 0 else None,
            "total_amount": round(as_float(row.get("TONG_TIEN", row.get("totalAmount", 0)))),
            "payment_status": status,
            "is_paid": _IS_PAID.get(status),
            "payment_checked": True,
            "due_date": _iso_or_empty(row.get("HAN_TTOAN")),
            "amount_owed": _as_int(row.get("TONG_NO")),
            "paid_on": _iso_or_empty(row.get("NGAY_TTOAN")),
            "bill_source": source,
            "issue_date": row.get("NGAY_TTOAN", row.get("issueDate", "")),
            "KY": _as_int(row.get("KY", row.get("ky"))),
            "THANG": _as_int(month),
            "NAM": _as_int(year),
            "period_start": _iso_or_empty(row.get("NGAY_DKY")),
            "period_end": _iso_or_empty(row.get("NGAY_CKY")),
            "calculated_amount": None,
        })
    return result


def bill_period_parts(bill: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """(year, month, ky) of a bill row, or None when it lacks a usable billing period."""
    parts = tuple(bill.get(name) for name in ("NAM", "THANG", "KY"))
    if any(not isinstance(part, int) or isinstance(part, bool) for part in parts) or not 1 <= parts[1] <= 12:
        return None
    return parts  # type: ignore[return-value]


def merge_bill_sources(
    history: Iterable[Mapping[str, Any]], unpaid: Iterable[Mapping[str, Any]], *, unpaid_fresh: bool,
) -> list[dict[str, Any]]:
    """One list from the paid history and the unpaid list, keyed by (year, month, period number).

    A period of a fresh unpaid list stands for itself.  Paying cannot be undone, so a
    paid history row beats a cached unpaid row; a cached unpaid row nothing contradicts
    stays unpaid but is marked `payment_checked: False`.  Rows without a usable period
    are listed last, tagged by the reconciliation, and never merged.
    """
    by_key: dict[tuple[int, int, int], dict[str, list[dict[str, Any]]]] = {}
    unkeyed: list[dict[str, Any]] = []
    for label, rows in (("history", history), ("unpaid", unpaid)):
        for row in rows:
            copy = dict(row)
            key = bill_period_parts(copy)
            if key is None:
                unkeyed.append(copy)
            else:
                by_key.setdefault(key, {"history": [], "unpaid": []})[label].append(copy)
    merged: list[dict[str, Any]] = []
    for key in sorted(by_key, reverse=True):
        paid_rows, unpaid_rows = by_key[key]["history"], by_key[key]["unpaid"]
        checked = True
        if unpaid_rows and (unpaid_fresh or not any(row.get("payment_status") == "paid" for row in paid_rows)):
            chosen, checked = unpaid_rows, unpaid_fresh
        else:
            chosen = paid_rows or unpaid_rows
        merged.extend({**row, "payment_checked": checked} for row in chosen)
    if unkeyed:
        _LOGGER.debug("EVN listed %d bill(s) without a billing period; they are shown but not matched", len(unkeyed))
    return merged + unkeyed


def unpaid_summary(bills: Iterable[Mapping[str, Any]], *, loaded: bool) -> dict[str, Any]:
    """Count, amount still owed and earliest due date of the unpaid bills; unknown until the unpaid list loaded."""
    if not loaded:
        return {"unpaid_count": None, "unpaid_amount": None, "next_due_date": None}
    unpaid = [bill for bill in bills if bill.get("payment_status") == "unpaid"]
    owed = sum(
        bill["amount_owed"] if bill.get("amount_owed") is not None else int(as_float(bill.get("total_amount")))
        for bill in unpaid
    )
    return {
        "unpaid_count": len(unpaid), "unpaid_amount": owed,
        "next_due_date": min((str(bill["due_date"]) for bill in unpaid if bill.get("due_date")), default=None),
    }


_OUTAGE_TIME = "%d/%m/%Y %H:%M"


def _outage_time(value: Any, tz: tzinfo | None) -> str | None:
    try:
        return datetime.strptime(str(value).strip(), _OUTAGE_TIME).replace(tzinfo=tz).isoformat()
    except ValueError:
        return None


def normalize_outages(rows: Iterable[Any], tz: tzinfo | None) -> list[dict[str, str]]:
    """Planned outages reduced to start, end (aware ISO in `tz`) and EVN's short status code.

    The reason and the area are free text that can name places or people, so they are dropped here,
    and so is any row whose times cannot be read.  Sorted by start.
    """
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        start, end = _outage_time(row.get("TGIAN_BDAU"), tz), _outage_time(row.get("TGIAN_KTHUC"), tz)
        if start is None or end is None:
            continue
        status = row.get("TTHAI_HOAN")
        result.append({"start": start, "end": end, "status": "" if status is None else str(status).strip()})
    return sorted(result, key=lambda item: item["start"])


def upcoming_outages(outages: Iterable[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The outages that have not ended yet (one in progress still counts)."""
    return [dict(item) for item in outages if datetime.fromisoformat(item["end"]) >= now]


def outage_summary(outages: Sequence[Mapping[str, Any]], *, loaded: bool) -> dict[str, Any]:
    """The next outage and how many are upcoming; unknown (not zero) until the outages were read once."""
    if not loaded:
        return {"next_planned_outage": None, "outage_end": None, "outage_status": None, "upcoming_outage_count": None, "outages": []}
    first = outages[0] if outages else {}
    return {
        "next_planned_outage": first.get("start"), "outage_end": first.get("end"), "outage_status": first.get("status"),
        "upcoming_outage_count": len(outages), "outages": [dict(item) for item in outages],
    }


def _index_number(value: Any) -> float | None:
    """A meter index as a finite number, else None; the meter number itself is never read."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def normalize_readings(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce EVN monthly meter-reading rows to the fields a bill needs."""
    result = []
    for row in rows:
        year, month, period_no = _as_int(row.get("NAM")), _as_int(row.get("THANG")), _as_int(row.get("KY"))
        if year is None or month is None or period_no is None or row.get("DIEN_TTHU") in (None, ""):
            continue
        result.append({
            "year": year, "month": month, "ky": period_no,
            "kwh": as_float(row.get("DIEN_TTHU")),
            "start": to_iso_date(row.get("NGAY_DKY")),
            "end": to_iso_date(row.get("NGAY_CKY")),
            "index_start": _index_number(row.get("CHISO_CU")),
            "index_end": _index_number(row.get("CHISO_MOI")),
        })
    return result


def _calculated_amount(bill: Mapping[str, Any]) -> int | None:
    """The add-on's own price of a bill, shown next to the real total_amount."""
    start, end = _parse_iso_date(str(bill.get("period_start") or "")), _parse_iso_date(str(bill.get("period_end") or ""))
    if bill.get("total_kwh") is None or start is None or end is None:
        return None
    return calculate_bill_amount(as_float(bill["total_kwh"]), start, end)


def attach_readings(
    bills: Iterable[Mapping[str, Any]], readings: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Join monthly readings to bills on (year, month, period number).

    Several reading rows can share one key (meter swap, period change): their kWh
    is added and the period runs from the earliest start to the latest end.
    Only the first bill of a key receives them.
    """
    grouped: dict[tuple[int, int, int], list[Mapping[str, Any]]] = {}
    for reading in readings:
        grouped.setdefault((reading["year"], reading["month"], reading["ky"]), []).append(reading)
    result = []
    for bill in bills:
        joined = dict(bill)
        # A period's readings belong to its first bill; a further invoice for the
        # same period must not add the same kWh again.
        rows = grouped.pop((bill.get("NAM"), bill.get("THANG"), bill.get("KY")), None)
        # After a meter swap the rows belong to different meters, so only a single reading gives indices.
        single = rows[0] if rows and len(rows) == 1 else {}
        joined["index_start"], joined["index_end"] = single.get("index_start"), single.get("index_end")
        if rows:
            joined["total_kwh"] = round(sum(row["kwh"] for row in rows), 2)
            starts = [day for day in (_parse_iso_date(row["start"]) for row in rows) if day]
            ends = [day for day in (_parse_iso_date(row["end"]) for row in rows) if day]
            joined["period_start"] = min(starts).isoformat() if starts else ""
            joined["period_end"] = max(ends).isoformat() if ends else ""
        joined["calculated_amount"] = _calculated_amount(joined)
        result.append(joined)
    return result


_PAYMENT_WORST_FIRST = ("unpaid", "unknown", "paid")


def bill_payment_status(bill: Mapping[str, Any]) -> str:
    """A bill's payment state; a row from before payment states exist is read from its is_paid flag."""
    status = bill.get("payment_status")
    if status in _PAYMENT_WORST_FIRST:
        return str(status)
    return {True: "paid", False: "unpaid"}.get(bill.get("is_paid"), "unknown")


def _add_payment(bucket: dict[str, Any], bill: Mapping[str, Any]) -> None:
    """Fold one code's payment state into the period's: the worst state wins, what is owed adds up."""
    status = bill_payment_status(bill)
    if _PAYMENT_WORST_FIRST.index(status) < _PAYMENT_WORST_FIRST.index(bucket["payment_status"]):
        bucket["payment_status"] = status
    if bill.get("amount_owed") is not None:
        bucket["amount_owed"] = (bucket["amount_owed"] or 0) + int(bill["amount_owed"])
    if bill.get("due_date"):
        bucket["due_date"] = min(bucket["due_date"] or str(bill["due_date"]), str(bill["due_date"]))
    bucket["payment_checked"] = bucket["payment_checked"] and bill.get("payment_checked", True) is not False


def aggregate_bills(bill_series: Iterable[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Join official bills by billing period and add kWh and VND independently.

    kWh and the calculated amount of a period are unknown (None) as soon as one
    bill of that period has no value: a partial sum would look complete.  A code
    with no bill for the period does not take part.  The reconciliation of the
    codes is summed under the same rule and shows the worst status among them.
    """
    buckets: dict[str, dict[str, Any]] = {}
    kwh_known: dict[str, bool] = {}
    takers: dict[str, set[int]] = {}
    reconciled: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, rows in enumerate(bill_series):
        for bill in rows:
            period = str(bill.get("period") or "")
            bucket = buckets.setdefault(period, {
                "period": period, "total_kwh": 0.0, "total_amount": 0, "payment_status": "paid",
                "payment_checked": True, "due_date": "", "amount_owed": None,
                "period_start": "", "period_end": "", "calculated_amount": 0,
            })
            takers.setdefault(period, set()).add(index)
            if bill.get("reconcile_status") is not None:
                reconciled.setdefault(period, []).append((index, bill))
            ky = bill.get("ky", bill.get("KY"))
            if isinstance(ky, int) and not isinstance(ky, bool):
                bucket["ky"] = min(bucket.get("ky", ky), ky)
            if bill.get("total_kwh") is None:
                kwh_known[period] = False
            else:
                kwh_known.setdefault(period, True)
                bucket["total_kwh"] = round(bucket["total_kwh"] + as_float(bill.get("total_kwh")), 2)
            bucket["total_amount"] += int(as_float(bill.get("total_amount")))
            _add_payment(bucket, bill)
            calculated = bill.get("calculated_amount")
            bucket["calculated_amount"] = (
                None if calculated is None or bucket["calculated_amount"] is None
                else bucket["calculated_amount"] + int(calculated)
            )
            if bill.get("period_start"):
                bucket["period_start"] = min(bucket["period_start"] or bill["period_start"], bill["period_start"])
            if bill.get("period_end"):
                bucket["period_end"] = max(bucket["period_end"], bill["period_end"])
    for period, bucket in buckets.items():
        bucket["is_paid"] = _IS_PAID.get(bucket["payment_status"])
        if not kwh_known[period]:
            bucket["total_kwh"] = None
        match = _PERIOD_RE.search(period)
        bucket["year"], bucket["month"] = (int(match.group(2)), int(match.group(1))) if match else (None, None)
        bucket.setdefault("ky", None)
        bucket.update(_aggregate_reconciliation(takers[period], reconciled.get(period, [])))
    return sorted(buckets.values(), key=lambda item: _period_sort_key(item["period"]), reverse=True)


_STATUS_WORST_FIRST = ("mismatch", "incomplete", "boundary", "match", "no_kwh")


def _aggregate_reconciliation(takers: set[int], rows: list[tuple[int, Mapping[str, Any]]]) -> dict[str, Any]:
    """Sum the codes' reconciliation of one period; unknown unless every code that billed it has one."""
    unknown: dict[str, Any] = {
        "collected_kwh": None, "diff_kwh": None, "missing_days": None, "reconcile_status": None, "paired_with": None,
    }
    if not rows or {index for index, _ in rows} != takers:
        return unknown

    def total(field: str) -> float | int | None:
        values = [row.get(field) for _, row in rows]
        return None if any(value is None for value in values) else round(sum(values), 2)

    statuses = {row["reconcile_status"] for _, row in rows}
    worst = next((status for status in _STATUS_WORST_FIRST if status in statuses), None)
    missing = total("missing_days")
    return {
        **unknown, "collected_kwh": total("collected_kwh"), "diff_kwh": total("diff_kwh"),
        "missing_days": None if missing is None else int(missing), "reconcile_status": worst,
    }


def _period_sort_key(period: str) -> tuple[int, int]:
    match = _PERIOD_RE.search(period)
    return (int(match.group(2)), int(match.group(1))) if match else (0, 0)


_DATE_DMY = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _describe_value(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return f"{type(value).__name__}/{len(str(int(abs(value))))}d"
    if isinstance(value, str):
        kind = "/date-dmy" if _DATE_DMY.match(value) else "/date-iso" if _DATE_ISO.match(value) else ""
        return f"str/len{len(value)}{kind}"
    if isinstance(value, (list, tuple)):
        return f"list/{len(value)}"
    if isinstance(value, Mapping):
        return describe_shape(value)
    return type(value).__name__


def describe_shape(row: Any) -> dict[str, Any]:
    """Describe key names and value types of a raw EVN row, never the values."""
    if not isinstance(row, Mapping):
        return {}
    return {str(key): _describe_value(value) for key, value in row.items()}
