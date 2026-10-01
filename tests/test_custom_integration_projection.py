"""Projection of the running billing period and the tier warning, against hand-computed values.

Hand arithmetic, tariff of 10/05/2025 (VND per kWh before VAT): 1984, 2050, 2380, 2998, 3350, 3460; VAT 8 %.
A bill period [start, end] holds the daily rows dated [start - 1 day, end - 1 day].
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_projection_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def m():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    _load_module("const")
    _load_module("tariff")
    calculation = _load_module("calculation")
    pricing = _load_module("pricing")
    projection = _load_module("projection")
    return types.SimpleNamespace(calculation=calculation, pricing=pricing, projection=projection)


def _days(first: date, last: date, kwh: float) -> dict[str, float]:
    return {(first + timedelta(days=i)).isoformat(): kwh for i in range((last - first).days + 1)}


def _bill(year, month, start, end, *, ky=1):
    return {"NAM": year, "THANG": month, "KY": ky, "period_start": start, "period_end": end}


def _tiered(m):
    return m.pricing.PriceModel(True, "tiered", None)


SEPT = _bill(2026, 9, "2026-09-01", "2026-09-30")


def _project(m, days, bills, today, model=None, readings=()):
    return m.projection.project_running_period(days, bills, list(readings), today, model or _tiered(m))


# ---------------------------------------------------------------- the running period

def test_a_calendar_month_is_followed_by_the_whole_next_calendar_month(m) -> None:
    """September is 30 days and October 31: the next period is the calendar month, not 30 days."""
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [SEPT], date(2026, 10, 11))
    assert (got["period_start"], got["expected_end"], got["data_until"]) == ("2026-10-01", "2026-10-31", "2026-10-10")


def test_a_non_calendar_period_is_followed_by_one_of_the_same_length(m) -> None:
    older = _bill(2026, 8, "2026-07-26", "2026-08-25")
    last = _bill(2026, 9, "2026-08-26", "2026-09-25")
    got = _project(m, _days(date(2026, 9, 25), date(2026, 10, 5), 3.0), [older, last], date(2026, 10, 6))
    assert (got["period_start"], got["expected_end"]) == ("2026-09-26", "2026-10-26"), "31 days like 26/08 to 25/09"


def test_the_newest_bill_with_dates_decides_even_when_listed_first(m) -> None:
    no_dates = _bill(2026, 10, "", "")
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [SEPT, no_dates, _bill(2026, 8, "2026-08-01", "2026-08-31")], date(2026, 10, 11))
    assert got["period_start"] == "2026-10-01"


def test_without_a_bill_period_the_newest_reading_end_is_used(m) -> None:
    readings = [{"year": 2026, "month": 9, "ky": 1, "kwh": 90.0, "start": "2026-09-01", "end": "2026-09-30"}]
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [], date(2026, 10, 11), readings=readings)
    assert (got["period_start"], got["expected_end"]) == ("2026-10-01", "2026-10-31")


def test_with_neither_the_period_is_the_current_calendar_month(m) -> None:
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [], date(2026, 10, 11))
    assert (got["period_start"], got["expected_end"]) == ("2026-10-01", "2026-10-31")


# ---------------------------------------------------------------- collected, rate and projected kWh

def test_the_projection_adds_the_rate_of_the_last_seven_days_for_the_days_left(m) -> None:
    """33 kWh in 11 days (30/09 to 10/10) and 3 kWh a day for the 20 days left (11/10 to 30/10): 93 kWh."""
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [SEPT], date(2026, 10, 11))
    assert (got["collected_kwh"], got["rate_kwh_per_day"], got["projected_kwh"]) == (33.0, 3.0, 93.0)


def test_only_the_last_seven_days_with_data_set_the_rate(m) -> None:
    days = _days(date(2026, 9, 30), date(2026, 10, 3), 10.0)
    days.update(_days(date(2026, 10, 4), date(2026, 10, 10), 2.0))
    got = _project(m, days, [SEPT], date(2026, 10, 11))
    assert got["rate_kwh_per_day"] == 2.0 and got["collected_kwh"] == 4 * 10.0 + 7 * 2.0
    assert got["projected_kwh"] == 54.0 + 2.0 * 20


def test_fewer_than_seven_days_use_the_days_there_are(m) -> None:
    got = _project(m, {"2026-09-30": 2.0, "2026-10-01": 4.0}, [SEPT], date(2026, 10, 2))
    assert got["rate_kwh_per_day"] == 3.0


def test_on_the_first_day_the_last_stored_day_already_belongs_to_the_new_period(m) -> None:
    """The rows of [30/09, 30/10] are the October bill: 1 collected day and 30 to go, 31 in all."""
    got = _project(m, _days(date(2026, 9, 24), date(2026, 9, 30), 4.0), [SEPT], date(2026, 10, 1))
    assert (got["collected_kwh"], got["projected_kwh"]) == (4.0, 4.0 + 4.0 * 30)


def test_when_the_new_period_has_no_row_yet_nothing_is_collected_and_the_rate_comes_from_before(m) -> None:
    got = _project(m, _days(date(2026, 9, 23), date(2026, 9, 29), 4.0), [SEPT], date(2026, 10, 1))
    assert (got["collected_kwh"], got["data_until"], got["projected_kwh"]) == (0.0, "2026-09-29", 4.0 * 31)


def test_a_period_whose_window_is_over_is_never_the_running_one(m) -> None:
    """Data past the window of the period after the newest bill means that period has ended: step forward."""
    got = _project(m, _days(date(2026, 9, 30), date(2026, 11, 2), 1.0), [SEPT], date(2026, 11, 3))
    assert (got["period_start"], got["expected_end"]) == ("2026-11-01", "2026-11-30")
    assert got["collected_kwh"] == 3.0 and got["projected_kwh"] == 3.0 + 1.0 * 27


def test_a_bill_that_is_late_does_not_pin_the_projection_to_a_closed_month(m) -> None:
    """The newest listed bill is August; on 03/10 September is over and its bill is not listed yet."""
    august = _bill(2026, 8, "2026-08-01", "2026-08-31")
    got = _project(m, _days(date(2026, 8, 31), date(2026, 10, 2), 3.0), [august], date(2026, 10, 3))
    assert (got["period_start"], got["expected_end"]) == ("2026-10-01", "2026-10-31")
    assert got["collected_kwh"] == 3 * 3.0 and got["tier"] == 1


def test_readings_newer_than_the_bills_set_the_running_period_too(m) -> None:
    august = _bill(2026, 8, "2026-08-01", "2026-08-31")
    readings = [{"year": 2026, "month": 9, "ky": 1, "kwh": 90.0, "start": "2026-09-01", "end": "2026-09-30"}]
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [august], date(2026, 10, 11), readings=readings)
    assert got["period_start"] == "2026-10-01"


def test_a_period_still_running_is_not_stepped_over(m) -> None:
    got = _project(m, _days(date(2026, 8, 31), date(2026, 9, 28), 3.0), [_bill(2026, 8, "2026-08-01", "2026-08-31")], date(2026, 9, 29))
    assert got["period_start"] == "2026-09-01" and got["expected_end"] == "2026-09-30"


def test_no_data_means_no_projection(m) -> None:
    assert _project(m, {}, [SEPT], date(2026, 10, 11)) is None


# ---------------------------------------------------------------- amount and tier

def test_the_tiered_amount_is_the_bill_rule_on_the_projected_kwh(m) -> None:
    """93 kWh: 50 x 1984 + 43 x 2050 = 187 350, plus 8 % = 14 988: 202 338."""
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [SEPT], date(2026, 10, 11))
    assert got["projected_amount"] == 202338 and got["method"] == "tiered" and got["calendar_month"] is True
    assert m.calculation.calculate_bill_amount(93.0, date(2026, 10, 1), date(2026, 10, 31)) == 202338


@pytest.mark.parametrize("collected, tier, to_next, price", [
    (49.9, 1, 0.1, 2050), (50.0, 2, 50.0, 2380), (99.9, 2, 0.1, 2380), (100.0, 3, 100.0, 2998),
    (199.9, 3, 0.1, 2998), (200.0, 4, 100.0, 3350), (300.0, 5, 100.0, 3460), (399.9, 5, 0.1, 3460),
])
def test_the_tier_follows_the_collected_kwh_at_each_boundary(m, collected, tier, to_next, price) -> None:
    got = _project(m, {"2026-10-01": collected}, [SEPT], date(2026, 10, 2))
    assert (got["tier"], got["kwh_to_next_tier"], got["next_tier_price"]) == (tier, to_next, price)


def test_the_last_tier_has_no_next_one(m) -> None:
    got = _project(m, {"2026-10-01": 400.0}, [SEPT], date(2026, 10, 2))
    assert (got["tier"], got["kwh_to_next_tier"], got["next_tier_price"]) == (6, None, None)
    assert _project(m, {"2026-10-01": 450.0}, [SEPT], date(2026, 10, 2))["tier"] == 6


def test_a_non_calendar_period_has_no_tiered_amount_and_no_tier(m) -> None:
    last = _bill(2026, 9, "2026-08-26", "2026-09-25")
    got = _project(m, _days(date(2026, 9, 25), date(2026, 10, 5), 3.0), [last], date(2026, 10, 6))
    assert (got["projected_amount"], got["tier"], got["kwh_to_next_tier"], got["next_tier_price"]) == (None, None, None, None)
    assert got["calendar_month"] is False and got["projected_kwh"] is not None


def test_an_effective_price_code_is_priced_per_kwh_and_has_no_tier_warning(m) -> None:
    model = m.pricing.PriceModel(False, "effective_price", Decimal("2500"))
    got = _project(m, _days(date(2026, 9, 30), date(2026, 10, 10), 3.0), [SEPT], date(2026, 10, 11), model)
    assert got["projected_amount"] == 232500 and got["method"] == "effective_price"
    assert (got["tier"], got["kwh_to_next_tier"], got["next_tier_price"]) == (None, None, None)


def test_a_tier_model_nobody_has_verified_gets_no_tier_warning(m) -> None:
    for verified in (None, False):
        model = m.pricing.PriceModel(verified, "tiered", None)
        got = _project(m, {"2026-10-01": 30.0}, [SEPT], date(2026, 10, 2), model)
        assert got["tier"] is None and got["projected_amount"] is not None


def test_a_tariff_change_inside_the_month_prices_it_by_the_bill_rule_but_gives_no_tier(m) -> None:
    april = _bill(2025, 4, "2025-04-01", "2025-04-30")
    got = _project(m, _days(date(2025, 4, 30), date(2025, 5, 11), 3.0), [april], date(2025, 5, 12))
    assert got["projected_kwh"] == 36.0 + 3.0 * 19
    assert got["projected_amount"] == m.calculation.calculate_bill_amount(93.0, date(2025, 5, 1), date(2025, 5, 31))
    assert got["tier"] is None and got["kwh_to_next_tier"] is None


def test_a_period_before_the_tariff_table_has_no_amount(m) -> None:
    early = _bill(2023, 9, "2023-09-01", "2023-09-30")
    got = _project(m, {"2023-10-01": 10.0}, [early], date(2023, 10, 2))
    assert got["projected_amount"] is None and got["tier"] is None


# ---------------------------------------------------------------- sums

def test_the_aggregate_sums_the_codes_and_is_unknown_if_one_is(m) -> None:
    calc = m.calculation
    total = calc.aggregate_overviews([{"projected_period_amount": 100}, {"projected_period_amount": 50}], ["A", "B"])
    assert total["projected_period_amount"] == 150
    partial = calc.aggregate_overviews([{"projected_period_amount": 100}, {"projected_period_amount": None}], ["A", "B"])
    assert partial["projected_period_amount"] is None
