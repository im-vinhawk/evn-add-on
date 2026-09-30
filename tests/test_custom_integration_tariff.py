"""The dated residential tariff must reproduce EVN's bill rule: days split, tier limits scaled, VAT rounded up."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_tariff_test"


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
    sys.modules.setdefault("aiohttp", types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ContentTypeError=ValueError,
    ))
    sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    sys.modules.setdefault("homeassistant.util", types.ModuleType("homeassistant.util"))
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = datetime.now
    sys.modules.setdefault("homeassistant.util.dt", dt)
    _load_module("const")
    tariff = _load_module("tariff")
    calculation = _load_module("calculation")
    _load_module("models")
    return tariff, calculation, _load_module("api")


@pytest.mark.parametrize(("kwh", "start", "end", "expected"), [
    (150, date(2026, 3, 1), date(2026, 3, 31), 346356),
    (450, date(2026, 3, 1), date(2026, 3, 31), 1347300),
    (0, date(2026, 3, 1), date(2026, 3, 31), 0),
    (150, date(2024, 11, 1), date(2024, 11, 30), 330480),
    # Price change on 2025-05-10 inside the month: kWh and tier limits are split by days.
    (200, date(2025, 5, 1), date(2025, 5, 31), 469250),
    # A fractional kWh (current-month estimate) is priced as is and rounded once.
    (123.45, date(2026, 3, 1), date(2026, 3, 31), 278112),
    # February of a leap year is a whole calendar month too.
    (100, date(2024, 2, 1), date(2024, 2, 29), 198288),
])
def test_bill_amount_reproduces_the_evn_rule(modules, kwh, start, end, expected) -> None:
    _, calculation, _ = modules
    assert calculation.calculate_bill_amount(kwh, start, end) == expected


@pytest.mark.parametrize(("start", "end"), [
    (date(2023, 10, 1), date(2023, 10, 31)),   # before the first table row
    (date(2023, 11, 1), date(2023, 11, 30)),   # month starts before the first effective date
    (date(2026, 3, 5), date(2026, 4, 4)),      # not a calendar month
    (date(2026, 3, 1), date(2026, 3, 30)),     # month not finished
    (date(2026, 3, 1), date(2026, 4, 30)),     # spans two months
    (date(2026, 3, 31), date(2026, 3, 1)),     # reversed
])
def test_bill_amount_is_unknown_outside_the_modelled_periods(modules, start, end) -> None:
    _, calculation, _ = modules
    assert calculation.calculate_bill_amount(100, start, end) is None


def test_month_with_a_price_change_lies_between_the_old_and_new_table(modules) -> None:
    _, calculation, _ = modules
    mixed = calculation.calculate_bill_amount(200, date(2024, 10, 1), date(2024, 10, 31))
    before = calculation.calculate_bill_amount(200, date(2024, 9, 1), date(2024, 9, 30))
    after = calculation.calculate_bill_amount(200, date(2024, 11, 1), date(2024, 11, 30))
    assert before < mixed < after


def test_negative_kwh_costs_nothing(modules) -> None:
    _, calculation, _ = modules
    assert calculation.calculate_bill_amount(-5, date(2026, 3, 1), date(2026, 3, 31)) == 0
    assert calculation.calculate_tier_cost(-5) == 0


def test_tier_cost_prices_with_the_tariff_in_force_today(modules) -> None:
    _, calculation, _ = modules
    assert calculation.calculate_tier_cost(150) == 346356
    assert calculation.calculate_tier_cost(150, vat_rate=0.0) == 320700


def test_vat_is_rounded_half_up_not_to_even(modules) -> None:
    """51 kWh = 101250 before VAT; 25 % is 25312.5."""
    _, calculation, _ = modules
    assert calculation.calculate_tier_cost(51, vat_rate=0.25) == 101250 + 25313


def test_tariff_rows_are_ordered_complete_and_sourced(modules) -> None:
    tariff, _, _ = modules
    rows = tariff.TARIFF_ROWS
    assert [row.effective_from for row in rows] == sorted(row.effective_from for row in rows)
    assert rows[0].effective_from == date(2023, 11, 9)
    assert rows[-1].effective_from == date(2025, 5, 10)
    assert rows[-1].prices == (1984, 2050, 2380, 2998, 3350, 3460)
    for row in rows:
        assert len(row.prices) == 6 == len(tariff.TIER_WIDTHS)
        assert list(row.prices) == sorted(row.prices)
        assert isinstance(row.vat_rate, Decimal) and row.vat_rate == Decimal("0.08")
        assert row.source
    assert tariff.TIER_WIDTHS[-1] is None


def test_calculated_amount_sits_next_to_the_real_amount(modules) -> None:
    _, calculation, _ = modules
    bills = calculation.normalize_bills([
        {"NAM": 2026, "THANG": 3, "KY": 1, "TONG_TIEN": 346000},
        {"NAM": 2026, "THANG": 2, "KY": 1, "TONG_TIEN": 100000},
        {"NAM": 2023, "THANG": 10, "KY": 1, "TONG_TIEN": 100000},
    ])
    readings = calculation.normalize_readings([
        {"NAM": 2026, "THANG": 3, "KY": 1, "DIEN_TTHU": 150, "NGAY_DKY": "01/03/2026", "NGAY_CKY": "31/03/2026"},
        {"NAM": 2023, "THANG": 10, "KY": 1, "DIEN_TTHU": 150, "NGAY_DKY": "01/10/2023", "NGAY_CKY": "31/10/2023"},
    ])
    march, february, october = calculation.attach_readings(bills, readings)
    assert (march["total_amount"], march["calculated_amount"]) == (346000, 346356)
    assert february["calculated_amount"] is None      # no reading, kWh unknown
    assert october["calculated_amount"] is None       # before the table
    assert october["total_amount"] == 100000


def test_aggregate_calculated_amount_is_the_sum_of_meters_or_unknown(modules) -> None:
    _, calculation, _ = modules

    def bill(calculated):
        return {"period": "Tháng 3/2026", "total_kwh": 1.0, "total_amount": 10, "is_paid": True,
                "calculated_amount": calculated}

    assert calculation.aggregate_bills([[bill(100)], [bill(250)]])[0]["calculated_amount"] == 350
    assert calculation.aggregate_bills([[bill(100)], [bill(None)]])[0]["calculated_amount"] is None
    assert calculation.aggregate_bills([[bill(None)], [bill(100)]])[0]["calculated_amount"] is None


def test_current_month_estimate_uses_the_dated_tariff(modules, monkeypatch) -> None:
    _, _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2025, 5, 20))
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state, {"PB000001": "PB000001001"})

    async def daily(_code, _start, _end):
        return [{"date": "2025-05-19", "consumption": 200.0}]

    async def readings(*_args):
        return []

    client.async_daily = daily
    client._async_readings = readings
    overview = asyncio.run(client.async_overview("PB000001"))
    # 200 kWh in May 2025 crosses the 2025-05-10 price change.
    assert overview["current_month_amount"] == 469250


def test_overview_reports_the_calendar_month_it_estimates(modules, monkeypatch) -> None:
    _, _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 2, 10))
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state, {"PB000001": "PB000001001"})

    async def daily(_code, _start, _end):
        return [{"date": "2026-02-09", "consumption": 10.0}]

    async def readings(*_args):
        return []

    client.async_daily = daily
    client._async_readings = readings
    overview = asyncio.run(client.async_overview("PB000001"))
    assert (overview["month_start"], overview["month_end"]) == ("2026-02-01", "2026-02-28")
