"""Contract tests for the pure calculation layer shipped with the integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "evn_vietnam" / "calculation.py"
SPEC = importlib.util.spec_from_file_location("evn_vietnam_calculation", MODULE_PATH)
assert SPEC and SPEC.loader
calculation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calculation)


def test_aggregate_preserves_per_meter_tier_tariff() -> None:
    """A total cost is sum(per-code estimates), never tariff(sum(kWh))."""
    first = calculation.calculate_tier_cost(150)
    second = calculation.calculate_tier_cost(150)
    aggregate = calculation.aggregate_overviews([
        {"today_consumption": 1, "current_month_consumption": 150, "current_month_amount": first},
        {"today_consumption": 2, "current_month_consumption": 150, "current_month_amount": second},
    ], ["PB000001", "PB000002"])
    assert aggregate["current_month_consumption"] == 300
    assert aggregate["current_month_amount"] == first + second
    assert aggregate["current_month_amount"] != calculation.calculate_tier_cost(300)


def test_aggregate_daily_outer_joins_dates_with_provenance() -> None:
    """Native dashboard history remains attributable to each successful code."""
    rows = calculation.aggregate_daily([
        ("PB000001", [{"date": "2026-08-16", "consumption": 1.5}]),
        ("PB000002", [{"day": "16/08/2026", "consumption": 2.0}, {"date": "2026-08-17", "consumption": 3.0}]),
    ])
    assert rows == [
        {"date": "2026-08-16", "consumption": 3.5, "kwh": 3.5, "customer_codes": ["PB000001", "PB000002"]},
        {"date": "2026-08-17", "consumption": 3.0, "kwh": 3.0, "customer_codes": ["PB000002"]},
    ]
