"""Daily kWh and cost as Home Assistant external statistics.

Everything except ``async_import_series`` is HA-free.  A series is always rebuilt
whole from the stored days and imported again (an upsert by start), so late EVN
corrections, older days added by the backfill and a changed selection or price
model can never leave a wrong cumulative sum behind.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from .const import DOMAIN
from .pricing import PriceModel, month_amount

ENERGY_UNIT = "kWh"
COST_UNIT = "VND"
TOTAL_ENERGY_ID = f"{DOMAIN}:total_daily_energy"
TOTAL_COST_ID = f"{DOMAIN}:total_daily_cost"

# (kWh so far in the month, first day of the month, last day of the month) -> month amount in VND.
PriceFunction = Callable[[float, date, date], "int | None"]


@dataclass(frozen=True)
class SeriesSpec:
    """One statistic to import: metadata plus its complete cumulative rows."""

    statistic_id: str
    name: str
    unit: str
    unit_class: str | None
    rows: list[dict[str, Any]]
    # What the rows were built from besides days (the codes a total covers); a change means stale rows.
    scope: str = ""


def energy_statistic_id(customer_code: str) -> str:
    return f"{DOMAIN}:{customer_code.lower()}_daily_energy"


def cost_statistic_id(customer_code: str) -> str:
    return f"{DOMAIN}:{customer_code.lower()}_daily_cost"


def _number(total: Decimal, integral: bool) -> float | int:
    return int(total) if integral else float(total)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def sum_by_day(series: Iterable[Mapping[str, float | int]]) -> dict[str, float | int]:
    """Outer-join several day->value mappings by date and add the values that share a day."""
    totals: dict[str, Decimal] = {}
    integral = True
    for values in series:
        for day, value in values.items():
            totals[day] = totals.get(day, Decimal(0)) + Decimal(str(value))
            integral = integral and _is_int(value)
    return {day: _number(totals[day], integral) for day in sorted(totals)}


def cumulative_series(values: Mapping[str, float | int], tz: tzinfo) -> list[dict[str, Any]]:
    """One row per present day at local midnight; ``sum`` and ``state`` are the running total.

    A missing day has no row and adds nothing, so the sum carries over it.
    """
    rows: list[dict[str, Any]] = []
    total = Decimal(0)
    integral = all(_is_int(value) for value in values.values())
    for day in sorted(values):
        total += Decimal(str(values[day]))
        start = date.fromisoformat(day)
        number = _number(total, integral)
        rows.append({"start": datetime(start.year, start.month, start.day, tzinfo=tz), "state": number, "sum": number})
    return rows


def daily_cost_rows(days: Mapping[str, float], price: PriceFunction) -> dict[str, int]:
    """Marginal daily cost: the month amount at today's running kWh minus at the previous day's.

    The running kWh restarts every calendar month and is priced for the whole
    month, so a month's daily costs add up exactly to that month's amount.
    A month the price function cannot price gets no rows.
    """
    months: dict[tuple[int, int], list[str]] = {}
    for day in sorted(days):
        parsed = date.fromisoformat(day)
        months.setdefault((parsed.year, parsed.month), []).append(day)
    costs: dict[str, int] = {}
    for (year, month), month_days in months.items():
        start, end = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        running, previous, month_costs = Decimal(0), 0, {}
        for day in month_days:
            running += Decimal(str(days[day]))
            amount = price(float(running), start, end)
            if amount is None:
                month_costs = {}
                break
            month_costs[day] = amount - previous
            previous = amount
        costs.update(month_costs)
    return costs


def statistic_label(customer_code: str, aliases: Mapping[str, str]) -> str:
    """A nickname, or the masked last four digits; the full code never reaches a statistic name."""
    return aliases.get(customer_code) or f"…{customer_code[-4:]}"


def build_series(
    days_by_code: Mapping[str, Mapping[str, float]],
    models: Mapping[str, PriceModel],
    selected: Sequence[str],
    aliases: Mapping[str, str],
    tz: tzinfo,
) -> list[SeriesSpec]:
    """Energy and cost series per code, plus totals over the selected codes (empty when fewer than two)."""
    specs: list[SeriesSpec] = []
    costs_by_code: dict[str, dict[str, int]] = {}
    for code, days in days_by_code.items():
        label = statistic_label(code, aliases)
        specs.append(SeriesSpec(
            energy_statistic_id(code), f"EVN {label} daily energy", ENERGY_UNIT, "energy", cumulative_series(days, tz),
        ))
        model = models.get(code)
        if model is None:
            continue  # no price model yet (e.g. its live call failed since the restart): no cost rather than a wrong one
        costs_by_code[code] = daily_cost_rows(days, lambda kwh, start, end, m=model: month_amount(kwh, start, end, m))
        specs.append(SeriesSpec(
            cost_statistic_id(code), f"EVN {label} daily cost (estimate)", COST_UNIT, None,
            cumulative_series(costs_by_code[code], tz),
        ))
    chosen = [code for code in selected if code in days_by_code] if len(selected) > 1 else []
    scope = ",".join(sorted(chosen))
    specs.append(SeriesSpec(
        TOTAL_ENERGY_ID, "EVN total daily energy", ENERGY_UNIT, "energy",
        cumulative_series(sum_by_day(days_by_code[code] for code in chosen), tz), scope,
    ))
    if all(code in costs_by_code for code in chosen):
        specs.append(SeriesSpec(
            TOTAL_COST_ID, "EVN total daily cost (estimate)", COST_UNIT, None,
            cumulative_series(sum_by_day(costs_by_code[code] for code in chosen), tz), scope,
        ))
    return specs  # a total cost missing a code's cost would look complete, so it is left out


def series_to_clear(specs: Iterable[SeriesSpec], imported: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Statistics whose stored rows would outlive the new series.

    Importing only upserts, so a stored row the new series no longer has keeps its
    old sum.  That happens when the series starts later, has fewer rows, covers
    other codes, or is gone.
    """
    clear = []
    for spec in specs:
        recorded = imported.get(spec.statistic_id)
        if recorded is None:
            continue
        if (
            not spec.rows
            or spec.rows[0]["start"].date().isoformat() > recorded["start"]
            or len(spec.rows) < recorded["count"]
            or spec.scope != recorded["scope"]
        ):
            clear.append(spec.statistic_id)
    return clear


async def async_import_series(hass: Any, specs: Iterable[SeriesSpec], to_clear: Sequence[str]) -> None:
    """Hand the series to Home Assistant's recorder as external statistics of this integration."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    try:
        from homeassistant.components.recorder.models import StatisticMetaData

        known = set(getattr(StatisticMetaData, "__annotations__", {}))
    except ImportError:
        known = set()
    if to_clear:
        get_instance(hass).async_clear_statistics(list(to_clear))
    for spec in specs:
        metadata: dict[str, Any] = {
            "has_sum": True, "name": spec.name, "source": DOMAIN, "statistic_id": spec.statistic_id,
            "unit_of_measurement": spec.unit,
        }
        # Older Home Assistant rejects keys it does not know, so send only what its metadata type lists.
        if "mean_type" in known:
            from homeassistant.components.recorder.models import StatisticMeanType

            metadata["mean_type"] = StatisticMeanType.NONE
        else:
            metadata["has_mean"] = False
        if "unit_class" in known:
            metadata["unit_class"] = spec.unit_class
        async_add_external_statistics(
            hass, metadata, [{"start": row["start"], "state": row["state"], "sum": row["sum"]} for row in spec.rows]
        )
