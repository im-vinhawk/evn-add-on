"""Cost projection of the billing period that is running now, and the tier warning. Pure: no I/O, no Home Assistant.

A bill period [start, end] holds the daily rows dated [start + BILL_DAY_OFFSET, end + BILL_DAY_OFFSET], so the
projection counts the same rows the bill reconciliation does.  The result is an estimate, never a bill.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from .calculation import bill_period_parts, is_calendar_month, tariff_on
from .const import BILL_DAY_OFFSET
from .pricing import ESTIMATE_TIERED, PriceModel, month_amount
from .tariff import TARIFF_ROWS, TIER_WIDTHS

# How many of the newest days with data set the daily rate.
RATE_DAYS = 7


def _iso_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _last_of_month(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _newest_period(bills: Sequence[Mapping[str, Any]], readings: Sequence[Mapping[str, Any]]) -> tuple[date | None, date] | None:
    """(start or None, end) of the newest bill with an end date, else of the newest reading; None if neither."""
    keyed = [(bill_period_parts(bill), bill) for bill in bills if isinstance(bill, Mapping)]
    dated = sorted(
        ((parts, _iso_day(bill.get("period_start")), _iso_day(bill.get("period_end"))) for parts, bill in keyed if parts is not None),
        key=lambda item: item[0],
    )
    dated = [item for item in dated if item[2] is not None]
    if dated:
        return dated[-1][1], dated[-1][2]
    rows = sorted(
        ((row.get("year", 0), row.get("month", 0), row.get("ky", 0)), _iso_day(row.get("start")), _iso_day(row.get("end")))
        for row in readings if isinstance(row, Mapping)
    )
    rows = [item for item in rows if item[2] is not None]
    return (rows[-1][1], rows[-1][2]) if rows else None


def running_period(bills: Sequence[Mapping[str, Any]], readings: Sequence[Mapping[str, Any]], today: date) -> tuple[date, date]:
    """The period after the newest known one.

    A whole calendar month is followed by the whole next calendar month; any other period by one of the same
    length.  Without a known period it is the current calendar month.
    """
    newest = _newest_period(bills, readings)
    if newest is None:
        return today.replace(day=1), _last_of_month(today)
    previous_start, previous_end = newest
    start = previous_end + timedelta(days=1)
    if previous_start is None or previous_start > previous_end or is_calendar_month(previous_start, previous_end):
        return start, _last_of_month(start)
    return start, start + (previous_end - previous_start)


def _tier(collected_kwh: float, start: date, end: date, model: PriceModel) -> dict[str, Any]:
    """Tier of the collected kWh and the kWh left before the next one; nothing unless that is well defined."""
    none = {"tier": None, "kwh_to_next_tier": None, "next_tier_price": None}
    change_inside = any(start < row.effective_from <= end for row in TARIFF_ROWS)
    if (
        model.estimate_method != ESTIMATE_TIERED or model.tariff_verified is not True or not is_calendar_month(start, end)
        or change_inside or start < TARIFF_ROWS[0].effective_from
    ):
        return none
    bounds, total = [], 0
    for width in TIER_WIDTHS[:-1]:
        total += width
        bounds.append(total)
    tier = 1 + sum(1 for bound in bounds if bound <= collected_kwh)
    if tier > len(bounds):
        return {"tier": tier, "kwh_to_next_tier": None, "next_tier_price": None}
    return {
        "tier": tier, "kwh_to_next_tier": round(bounds[tier - 1] - collected_kwh, 2),
        "next_tier_price": tariff_on(start).prices[tier],
    }


def project_running_period(
    days: Mapping[str, float], bills: Sequence[Mapping[str, Any]], readings: Sequence[Mapping[str, Any]],
    today: date, model: PriceModel,
) -> dict[str, Any] | None:
    """Collected and projected kWh, projected amount and tier of the running period; None without any daily data."""
    stored = sorted(day for day in days if _iso_day(day) is not None)
    if not stored:
        return None
    start, end = running_period(bills, readings, today)
    window_start, window_end = start + timedelta(days=BILL_DAY_OFFSET), end + timedelta(days=BILL_DAY_OFFSET)
    in_window = [days[day] for day in stored if window_start <= _iso_day(day) <= window_end]
    collected = round(sum(in_window), 2)
    data_until = _iso_day(stored[-1])
    rate_days = [days[day] for day in stored[-RATE_DAYS:]]
    rate = round(sum(rate_days) / len(rate_days), 2)
    remaining = max((window_end - max(data_until, window_start - timedelta(days=1))).days, 0)
    projected = round(collected + rate * remaining, 2)
    return {
        "period_start": start.isoformat(), "expected_end": end.isoformat(), "data_until": data_until.isoformat(),
        "collected_kwh": collected, "rate_kwh_per_day": rate, "projected_kwh": projected,
        "projected_amount": month_amount(projected, start, end, model), "method": model.estimate_method,
        "calendar_month": is_calendar_month(start, end), **_tier(collected, start, end, model),
    }
