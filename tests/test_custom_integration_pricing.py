"""The residential tier model is trusted only where it reproduces a code's real bills."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_pricing_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    _load_module("tariff")
    calculation = _load_module("calculation")
    return calculation, _load_module("pricing")


def _bill(calculation, year: int, month: int, kwh, amount: int, *, ky: int = 1) -> dict:
    """A bill row whose calculated_amount is the tier model's price for that calendar month."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    end = date.fromordinal(end.toordinal() - 1)
    calculated = None if kwh is None else calculation.calculate_bill_amount(kwh, start, end)
    return {
        "period": f"Tháng {month}/{year}", "total_kwh": kwh, "total_amount": amount, "KY": ky, "THANG": month,
        "NAM": year, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "calculated_amount": calculated,
    }


def _matching(calculation, year: int, month: int, kwh: int, scale: float = 1.0) -> dict:
    bill = _bill(calculation, year, month, kwh, 0)
    bill["total_amount"] = round(bill["calculated_amount"] * scale)
    return bill


def test_bills_priced_by_the_tier_model_verify_it(modules) -> None:
    calculation, pricing = modules
    bills = [_matching(calculation, 2026, m, 100 + m) for m in (1, 2, 3)]
    model = pricing.build_price_model(bills)
    assert model.tariff_verified is True
    assert model.estimate_method == "tiered"
    start, end = date(2026, 4, 1), date(2026, 4, 30)
    assert pricing.month_amount(150, start, end, model) == calculation.calculate_bill_amount(150, start, end)


def test_bills_that_differ_from_the_tier_model_switch_to_the_effective_price(modules) -> None:
    calculation, pricing = modules
    bills = [_matching(calculation, 2026, m, 100 + m, scale=1.10) for m in (1, 2, 3)]
    model = pricing.build_price_model(bills)
    assert model.tariff_verified is False
    assert model.estimate_method == "effective_price"
    start, end = date(2026, 4, 1), date(2026, 4, 30)
    assert pricing.month_amount(150, start, end, model) != calculation.calculate_bill_amount(150, start, end)


def test_effective_price_is_total_amount_over_total_kwh_of_the_latest_three(modules) -> None:
    calculation, pricing = modules
    bills = [
        _bill(calculation, 2026, 1, 100, 250000),
        _bill(calculation, 2026, 2, 200, 500000),
        _bill(calculation, 2026, 3, 300, 750000),
    ]
    model = pricing.build_price_model(bills)
    assert model.estimate_method == "effective_price"
    assert model.effective_price == Decimal(2500)
    assert pricing.month_amount(120, date(2026, 4, 1), date(2026, 4, 30), model) == 300000


def test_effective_price_uses_only_the_latest_three_bills_in_any_input_order(modules) -> None:
    calculation, pricing = modules
    bills = [
        _bill(calculation, 2026, 3, 300, 750000),
        _bill(calculation, 2025, 12, 100, 900000),
        _bill(calculation, 2026, 1, 100, 250000),
        _bill(calculation, 2026, 2, 200, 500000),
    ]
    assert pricing.build_price_model(bills).effective_price == Decimal(2500)


def test_effective_price_rounds_half_up(modules) -> None:
    calculation, pricing = modules
    model = pricing.build_price_model([_bill(calculation, 2026, 3, 2, 1)])
    assert model.effective_price == Decimal("0.5")
    assert pricing.month_amount(1, date(2026, 4, 1), date(2026, 4, 30), model) == 1


def test_fewer_than_three_bills_are_all_used(modules) -> None:
    calculation, pricing = modules
    two_good = pricing.build_price_model([_matching(calculation, 2026, 2, 100), _matching(calculation, 2026, 3, 120)])
    assert two_good.tariff_verified is True
    one_bad = pricing.build_price_model([_bill(calculation, 2026, 3, 100, 250000)])
    assert one_bad.tariff_verified is False
    assert one_bad.effective_price == Decimal(2500)


def test_without_modelled_bills_the_tier_model_stays_with_unknown_verification(modules) -> None:
    calculation, pricing = modules
    assert pricing.build_price_model([]).tariff_verified is None
    assert pricing.build_price_model([]).estimate_method == "tiered"
    unmodelled = [_bill(calculation, 2023, 10, 100, 250000), _bill(calculation, 2026, 3, None, 250000)]
    assert [bill["calculated_amount"] for bill in unmodelled] == [None, None]
    model = pricing.build_price_model(unmodelled)
    assert model.tariff_verified is None
    assert model.estimate_method == "tiered"
    # Bills before the table still yield a price, but it is not used while verification is unknown.
    assert model.effective_price == Decimal(2500)


def test_one_old_mismatch_outside_the_latest_three_does_not_matter(modules) -> None:
    calculation, pricing = modules
    bills = [_bill(calculation, 2025, 12, 100, 1)] + [_matching(calculation, 2026, m, 100 + m) for m in (1, 2, 3)]
    assert pricing.build_price_model(bills).tariff_verified is True


def test_a_mismatch_among_the_latest_three_fails_verification(modules) -> None:
    calculation, pricing = modules
    bills = [_matching(calculation, 2026, m, 100 + m) for m in (1, 2)] + [_bill(calculation, 2026, 3, 100, 1)]
    assert pricing.build_price_model(bills).tariff_verified is False


def test_bills_without_a_calculated_or_positive_amount_are_skipped_when_picking_the_three(modules) -> None:
    calculation, pricing = modules
    bills = [
        _matching(calculation, 2026, 1, 100), _matching(calculation, 2026, 2, 110), _matching(calculation, 2026, 3, 120),
        _bill(calculation, 2026, 4, None, 500),        # newest, but kWh unknown
        _bill(calculation, 2026, 5, 100, 0),           # newest, but nothing billed
    ]
    assert pricing.build_price_model(bills).tariff_verified is True


def test_false_without_any_kwh_bill_keeps_the_tier_estimate(modules) -> None:
    calculation, pricing = modules
    model = pricing.build_price_model([_bill(calculation, 2026, 3, 0.0, 250000)])
    assert model.tariff_verified is False
    assert model.effective_price is None
    assert model.estimate_method == "tiered"


def test_month_amount_is_none_for_a_month_the_tier_model_cannot_price(modules) -> None:
    _, pricing = modules
    assert pricing.month_amount(100, date(2023, 10, 1), date(2023, 10, 31), pricing.build_price_model([])) is None


def test_price_overview_replaces_the_estimate_and_records_the_method(modules) -> None:
    calculation, pricing = modules
    overview = {
        "current_month_consumption": 120.0, "current_month_amount": 1,
        "month_start": "2026-04-01", "month_end": "2026-04-30",
        "bills": [
            _bill(calculation, 2026, 1, 100, 250000),
            _bill(calculation, 2026, 2, 200, 500000),
            _bill(calculation, 2026, 3, 300, 750000),
        ],
    }
    pricing.price_overview(overview)
    assert overview["current_month_amount"] == 300000
    assert (overview["tariff_verified"], overview["estimate_method"]) == (False, "effective_price")
    assert overview["price_model"].effective_price == Decimal(2500)


def test_price_overview_keeps_the_tier_estimate_for_verified_bills(modules) -> None:
    calculation, pricing = modules
    overview = {
        "current_month_consumption": 150.0, "current_month_amount": 346356,
        "month_start": "2026-03-01", "month_end": "2026-03-31",
        "bills": [_matching(calculation, 2026, 2, 100)],
    }
    pricing.price_overview(overview)
    assert overview["current_month_amount"] == 346356
    assert (overview["tariff_verified"], overview["estimate_method"]) == (True, "tiered")


def test_price_overview_without_month_dates_leaves_the_amount_alone(modules) -> None:
    calculation, pricing = modules
    overview = {
        "current_month_consumption": 1.0, "current_month_amount": 7,
        "bills": [_bill(calculation, 2026, 3, 100, 250000)],
    }
    pricing.price_overview(overview)
    assert overview["current_month_amount"] == 7
    assert overview["tariff_verified"] is False


def test_aggregate_is_false_when_any_selected_code_is_false(modules) -> None:
    calculation, _ = modules

    def item(verified, method="tiered", amount=10):
        return {"tariff_verified": verified, "estimate_method": method, "current_month_amount": amount}

    agg = calculation.aggregate_overviews([item(True), item(False, "effective_price", 25)], ["A", "B"])
    assert (agg["tariff_verified"], agg["estimate_method"], agg["current_month_amount"]) == (False, "effective_price", 35)
    agg = calculation.aggregate_overviews([item(True), item(None)], ["A", "B"])
    assert (agg["tariff_verified"], agg["estimate_method"]) == (None, "tiered")
    agg = calculation.aggregate_overviews([item(True), item(True)], ["A", "B"])
    assert agg["tariff_verified"] is True
