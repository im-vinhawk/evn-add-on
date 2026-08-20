"""EVN normalization and per-meter aggregation rules.

EVN exposes one customer code per request.  Totals must therefore add each
meter's already-calculated values: never apply the tiered tariff to a combined
household kWh value.
"""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any, Iterable, Mapping

_PERIOD_RE = re.compile(r"(?:Tháng\s*)?(\d{1,2})\s*/\s*(\d{4})", re.IGNORECASE)


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


def calculate_tier_cost(kwh: float, vat_rate: float = 0.08) -> int:
    """Return the legacy dashboard's six-tier residential estimate in VND."""
    remaining, pretax = max(kwh, 0), 0.0
    for limit, rate in ((50, 1893), (50, 1956), (100, 2271), (100, 2860), (100, 3197), (float("inf"), 3302)):
        used = min(remaining, limit)
        pretax += used * rate
        remaining -= used
        if remaining <= 0:
            break
    return int(round(pretax * (1 + vat_rate)))


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
        "yesterday_consumption": round(sum(as_float(item.get("yesterday_consumption")) for item in values), 2),
        "current_month_consumption": round(sum(as_float(item.get("current_month_consumption")) for item in values), 2),
        "current_month_amount": sum(int(as_float(item.get("current_month_amount"))) for item in values),
    }


def aggregate_selected_overviews(
    meters: Mapping[str, Mapping[str, Any]],
    selected_codes: list[str],
    partial_errors: Mapping[str, str],
) -> dict[str, Any] | None:
    """Aggregate only the selected successful meters with scoped provenance."""
    if len(selected_codes) < 2:
        return None
    successful_codes = [code for code in selected_codes if code in meters]
    aggregate = aggregate_overviews((meters[code] for code in successful_codes), selected_codes)
    aggregate["successful_customer_codes"] = successful_codes
    aggregate["bills"] = aggregate_bills(meters[code].get("bills", []) for code in successful_codes)
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


def normalize_bills(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize official bills; their amount is never re-priced."""
    result = []
    for row in rows:
        month, year = row.get("THANG", row.get("thang")), row.get("NAM", row.get("nam"))
        try:
            period = f"Tháng {int(month)}/{int(year)}"
        except (TypeError, ValueError):
            period = str(row.get("period") or "")
        result.append({
            "period": period,
            "total_kwh": as_float(row.get("DIEN_TTHU", row.get("totalKwh", 0))),
            "total_amount": round(as_float(row.get("TONG_TIEN", row.get("totalAmount", 0)))),
            "is_paid": bool(row.get("isPaid", True)),
            "issue_date": row.get("NGAY_TTOAN", row.get("issueDate", "")),
        })
    return result


def aggregate_bills(bill_series: Iterable[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Join official bills by billing period and add kWh and VND independently."""
    buckets: dict[str, dict[str, Any]] = {}
    for rows in bill_series:
        for bill in rows:
            period = str(bill.get("period") or "")
            bucket = buckets.setdefault(period, {"period": period, "total_kwh": 0.0, "total_amount": 0, "is_paid": True})
            bucket["total_kwh"] = round(bucket["total_kwh"] + as_float(bill.get("total_kwh")), 2)
            bucket["total_amount"] += int(as_float(bill.get("total_amount")))
            bucket["is_paid"] = bucket["is_paid"] and bool(bill.get("is_paid"))
    return sorted(buckets.values(), key=lambda item: _period_sort_key(item["period"]), reverse=True)


def _period_sort_key(period: str) -> tuple[int, int]:
    match = _PERIOD_RE.search(period)
    return (int(match.group(2)), int(match.group(1))) if match else (0, 0)
