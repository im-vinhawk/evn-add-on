"""Bill kWh comes from EVN's monthly meter readings, not from the bill rows."""

from __future__ import annotations

import asyncio
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_bills_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules():
    """Load calculation and the adapter without a Home Assistant runtime."""
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    sys.modules.setdefault("aiohttp", types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ContentTypeError=ValueError,
    ))
    homeassistant = types.ModuleType("homeassistant")
    homeassistant_util = types.ModuleType("homeassistant.util")
    homeassistant_dt = types.ModuleType("homeassistant.util.dt")
    homeassistant_dt.now = datetime.now
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.util", homeassistant_util)
    sys.modules.setdefault("homeassistant.util.dt", homeassistant_dt)
    _load_module("const")
    calculation = _load_module("calculation")
    _load_module("models")
    return calculation, _load_module("api")


def _reading(year: int, month: int, ky: int, kwh: int, start: str, end: str) -> dict:
    return {"NAM": year, "THANG": month, "KY": ky, "DIEN_TTHU": kwh, "NGAY_DKY": start, "NGAY_CKY": end}


def _bill(year: int, month: int, ky: int = 1, amount: int = 300000, kwh: int = 0) -> dict:
    return {"NAM": year, "THANG": month, "KY": ky, "DIEN_TTHU": kwh, "TONG_TIEN": amount}


def test_bill_without_a_reading_has_unknown_kwh_not_zero(modules) -> None:
    calculation, _ = modules
    bill = calculation.normalize_bills([_bill(2026, 3)])[0]
    assert bill["total_kwh"] is None
    assert (bill["KY"], bill["THANG"], bill["NAM"]) == (1, 3, 2026)
    assert bill["period_start"] == bill["period_end"] == ""
    assert bill["total_amount"] == 300000


def test_bill_row_with_a_real_kwh_value_is_kept_as_fallback(modules) -> None:
    calculation, _ = modules
    assert calculation.normalize_bills([_bill(2026, 3, kwh=42)])[0]["total_kwh"] == 42.0
    assert calculation.normalize_bills([{"THANG": 3, "NAM": 2026, "totalKwh": 7.5}])[0]["total_kwh"] == 7.5


def test_readings_of_one_period_are_summed_and_dated(modules) -> None:
    """A meter swap yields several reading rows for one bill period."""
    calculation, _ = modules
    readings = calculation.normalize_readings([
        _reading(2026, 3, 1, 100, "01/03/2026", "15/03/2026"),
        _reading(2026, 3, 1, 20, "16/03/2026", "31/03/2026"),
        _reading(2026, 2, 1, 90, "01/02/2026", "28/02/2026"),
    ])
    bills = calculation.attach_readings(calculation.normalize_bills([_bill(2026, 3)]), readings)
    assert bills[0]["total_kwh"] == 120.0
    assert bills[0]["period_start"] == "2026-03-01"
    assert bills[0]["period_end"] == "2026-03-31"


def test_reading_is_matched_on_year_month_and_period_number(modules) -> None:
    calculation, _ = modules
    readings = calculation.normalize_readings([
        _reading(2025, 3, 1, 50, "01/03/2025", "31/03/2025"),
        _reading(2026, 3, 2, 60, "01/03/2026", "31/03/2026"),
    ])
    bills = calculation.attach_readings(
        calculation.normalize_bills([_bill(2026, 3, ky=1), _bill(2025, 3, ky=1)]), readings
    )
    assert bills[0]["total_kwh"] is None
    assert bills[1]["total_kwh"] == 50.0


def test_unusable_reading_rows_are_ignored(modules) -> None:
    calculation, _ = modules
    readings = calculation.normalize_readings([
        {"DIEN_TTHU": 5}, {"NAM": "x", "THANG": 3, "KY": 1}, _reading(2026, 3, 1, 10, "not a date", ""),
    ])
    bill = calculation.attach_readings(calculation.normalize_bills([_bill(2026, 3)]), readings)[0]
    assert bill["total_kwh"] == 10.0
    assert bill["period_start"] == bill["period_end"] == ""


def test_attach_readings_does_not_mutate_its_input(modules) -> None:
    calculation, _ = modules
    bills = calculation.normalize_bills([_bill(2026, 3)])
    calculation.attach_readings(
        bills, calculation.normalize_readings([_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")])
    )
    assert bills[0]["total_kwh"] is None


def test_aggregate_sums_known_kwh(modules) -> None:
    calculation, _ = modules
    known = {"period": "Tháng 3/2026", "total_kwh": 120.0, "total_amount": 100, "is_paid": True}
    unknown = {"period": "Tháng 3/2026", "total_kwh": None, "total_amount": 50, "is_paid": True}
    rows = calculation.aggregate_bills([[known], [known]])
    assert rows[0]["total_kwh"] == 240.0
    assert calculation.aggregate_bills([[known], [unknown]])[0]["total_amount"] == 150
    assert calculation.aggregate_bills([[unknown], [unknown]])[0]["total_kwh"] is None


def test_aggregate_period_spans_every_meter(modules) -> None:
    calculation, _ = modules
    first = {"period": "Tháng 3/2026", "total_kwh": 1.0, "total_amount": 1, "is_paid": True,
             "period_start": "2026-03-01", "period_end": "2026-03-30"}
    second = {"period": "Tháng 3/2026", "total_kwh": 1.0, "total_amount": 1, "is_paid": True,
              "period_start": "2026-03-02", "period_end": "2026-03-31"}
    row = calculation.aggregate_bills([[first], [second]])[0]
    assert (row["period_start"], row["period_end"]) == ("2026-03-01", "2026-03-31")


def test_december_sorts_before_the_following_january_newest_first(modules) -> None:
    calculation, _ = modules
    rows = calculation.aggregate_bills([[
        {"period": "Tháng 12/2025", "total_kwh": 1.0, "total_amount": 1, "is_paid": True},
        {"period": "Tháng 1/2026", "total_kwh": 1.0, "total_amount": 1, "is_paid": True},
    ]])
    assert [row["period"] for row in rows] == ["Tháng 1/2026", "Tháng 12/2025"]


def _client(api_module, requests: list, payload):
    state = api_module.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api_module.EvnClient(object(), state, {"PB000001": "PB000001001", "PB000002": "PB000002001"})

    async def switch_customer(_: str) -> None:
        return None

    async def request(method, url, body=None):
        requests.append((method, url, body))
        return payload

    client._async_switch_customer = switch_customer
    client._async_request = request
    return client


def test_bills_are_requested_for_previous_and_current_year_in_one_call(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 1, 10))
    requests: list = []
    client = _client(api, requests, {"data": []})
    asyncio.run(client.async_bills("PB000001"))
    assert len(requests) == 1
    assert requests[0][1].endswith("/api/evn/tracuu/lichsu-hoadon")
    assert requests[0][2] == {"MA_KHANG": "PB000001", "TU_THANG_NAM": "01/2025", "DEN_THANG_NAM": "12/2026"}


def test_monthly_readings_request_shape_and_range(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 3, 15))
    requests: list = []
    payload = {"success": True, "status": "OK", "statusCode": 200, "data": [
        _reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026"),
    ]}
    client = _client(api, requests, payload)
    rows = asyncio.run(client.async_monthly_readings("PB000001"))
    assert requests[0][0] == "POST"
    assert requests[0][1].endswith("/api/evn/tracuu/chisothang")
    assert requests[0][2] == {
        "MA_KHANG": "PB000001", "MA_DDO": "PB000001001", "TU_THANG_NAM": "01/2025", "DEN_THANG_NAM": "03/2026",
    }
    assert [(row["year"], row["month"], row["ky"], row["kwh"]) for row in rows] == [(2026, 3, 1, 10.0)]


def test_monthly_readings_shape_is_recorded_without_values(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 3, 15))
    row = {**_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026"), "MA_KHANG": "PB000001", "SO_CTO": "123456789"}
    client = _client(api, [], {"data": [row]})
    asyncio.run(client.async_monthly_readings("PB000001"))
    shape = client.last_shapes["monthly_readings"]
    assert shape["DIEN_TTHU"] == "int/2d"
    assert shape["NGAY_DKY"] == "str/len10/date-dmy"
    dumped = repr(shape)
    assert "PB000001" not in dumped and "123456789" not in dumped


def test_monthly_readings_are_cached_per_code_for_six_hours(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 3, 15))
    requests: list = []
    client = _client(api, requests, {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]})
    now = [1000.0]
    client._clock = lambda: now[0]

    first = asyncio.run(client.async_monthly_readings("PB000001"))
    now[0] += 6 * 3600 - 1
    assert asyncio.run(client.async_monthly_readings("PB000001")) == first
    assert len(requests) == 1

    asyncio.run(client.async_monthly_readings("PB000002"))
    assert len(requests) == 2

    now[0] += 2
    asyncio.run(client.async_monthly_readings("PB000001"))
    assert len(requests) == 3


def test_monthly_readings_cache_is_dropped_when_the_month_changes(modules, monkeypatch) -> None:
    _, api = modules
    today = [datetime(2026, 3, 31)]
    monkeypatch.setattr(api.dt_util, "now", lambda: today[0])
    requests: list = []
    client = _client(api, requests, {"data": []})
    client._clock = lambda: 1000.0
    asyncio.run(client.async_monthly_readings("PB000001"))
    today[0] = datetime(2026, 4, 1)
    asyncio.run(client.async_monthly_readings("PB000001"))
    assert len(requests) == 2
    assert requests[1][2]["DEN_THANG_NAM"] == "04/2026"


def test_failed_readings_request_is_not_cached(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 3, 15))
    requests: list = []
    client = _client(api, requests, {"data": []})
    client._clock = lambda: 1000.0

    async def failing(*_args, **_kwargs):
        raise api.EvnApiError("HTTP 500", status=500)

    client._async_request = failing
    with pytest.raises(api.EvnApiError):
        asyncio.run(client.async_monthly_readings("PB000001"))

    async def working(method, url, body=None):
        requests.append(url)
        return {"data": []}

    client._async_request = working
    asyncio.run(client.async_monthly_readings("PB000001"))
    assert len(requests) == 1


def test_a_reading_is_attached_to_the_first_bill_of_its_period_only(modules) -> None:
    """A second invoice for the same period must not count the kWh twice."""
    calculation, _ = modules
    readings = calculation.normalize_readings([_reading(2026, 3, 1, 150, "01/03/2026", "31/03/2026")])
    first, second = calculation.attach_readings(
        calculation.normalize_bills([_bill(2026, 3, amount=300000), _bill(2026, 3, amount=20000)]), readings
    )
    assert first["total_kwh"] == 150.0
    assert second["total_kwh"] is None
    assert second["period_start"] == second["period_end"] == ""


def test_reading_without_a_kwh_value_is_ignored_not_zero(modules) -> None:
    calculation, _ = modules
    rows = [
        {"NAM": 2026, "THANG": 3, "KY": 1, "DIEN_TTHU": None, "NGAY_DKY": "01/03/2026", "NGAY_CKY": "31/03/2026"},
        {"NAM": 2026, "THANG": 3, "KY": 1, "NGAY_DKY": "01/03/2026", "NGAY_CKY": "31/03/2026"},
    ]
    assert calculation.normalize_readings(rows) == []
    zero = {"NAM": 2026, "THANG": 3, "KY": 1, "DIEN_TTHU": 0, "NGAY_DKY": "01/03/2026", "NGAY_CKY": "31/03/2026"}
    assert calculation.normalize_readings([zero])[0]["kwh"] == 0.0


def test_cached_readings_cannot_be_changed_by_a_caller(modules, monkeypatch) -> None:
    _, api = modules
    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 3, 15))
    client = _client(api, [], {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]})
    client._clock = lambda: 1000.0
    asyncio.run(client.async_monthly_readings("PB000001")).append({"junk": True})
    assert len(asyncio.run(client.async_monthly_readings("PB000001"))) == 1


def _stamped_client(api, monkeypatch, payloads):
    """Client whose clock and wall time are driven by the test; payloads is a list of (payload|Exception)."""
    wall = [datetime(2026, 3, 15, 10, 0, 0)]
    monkeypatch.setattr(api.dt_util, "now", lambda: wall[0])
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state, {"PB000001": "PB000001001"})
    mono = [1000.0]
    client._clock = lambda: mono[0]
    queue = list(payloads)

    async def switch_customer(_: str) -> None:
        return None

    async def request(method, url, body=None):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client._async_switch_customer = switch_customer
    client._async_request = request
    return client, wall, mono


def test_failed_readings_fetch_returns_the_last_good_rows_at_any_age(modules, monkeypatch) -> None:
    _, api = modules
    good = {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]}
    client, wall, mono = _stamped_client(api, monkeypatch, [good, api.EvnApiError("HTTP 500", status=500)])
    first = asyncio.run(client.async_monthly_readings("PB000001"))
    mono[0] += 30 * 24 * 3600
    wall[0] = datetime(2026, 4, 20, 9, 0, 0)
    assert asyncio.run(client.async_monthly_readings("PB000001")) == first


def test_failed_readings_fetch_without_a_prior_success_still_raises(modules, monkeypatch) -> None:
    _, api = modules
    client, _, _ = _stamped_client(api, monkeypatch, [api.EvnApiError("HTTP 500", status=500)])
    with pytest.raises(api.EvnApiError):
        asyncio.run(client.async_monthly_readings("PB000001"))


def test_readings_authentication_failure_is_not_masked_by_the_last_good_rows(modules, monkeypatch) -> None:
    _, api = modules
    good = {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]}
    client, _, mono = _stamped_client(api, monkeypatch, [good, api.EvnAuthenticationError("expired")])
    asyncio.run(client.async_monthly_readings("PB000001"))
    mono[0] += 7 * 3600
    with pytest.raises(api.EvnAuthenticationError):
        asyncio.run(client.async_monthly_readings("PB000001"))


def test_failed_readings_fetch_is_per_code(modules, monkeypatch) -> None:
    _, api = modules
    good = {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]}
    client, _, _ = _stamped_client(api, monkeypatch, [good, api.EvnApiError("HTTP 500", status=500)])
    client._meter_points["PB000002"] = "PB000002001"
    asyncio.run(client.async_monthly_readings("PB000001"))
    with pytest.raises(api.EvnApiError):
        asyncio.run(client.async_monthly_readings("PB000002"))


def test_failed_bills_fetch_returns_the_last_good_bills(modules, monkeypatch) -> None:
    _, api = modules
    good = {"data": [_bill(2026, 3, amount=300000)]}
    client, _, _ = _stamped_client(api, monkeypatch, [good, api.EvnApiError("HTTP 500", status=500)])
    first = asyncio.run(client.async_bills("PB000001"))
    again = asyncio.run(client.async_bills("PB000001"))
    assert again == first and again[0]["total_amount"] == 300000


def test_failed_bills_fetch_without_a_prior_success_still_raises(modules, monkeypatch) -> None:
    _, api = modules
    client, _, _ = _stamped_client(api, monkeypatch, [api.EvnApiError("HTTP 500", status=500)])
    with pytest.raises(api.EvnApiError):
        asyncio.run(client.async_bills("PB000001"))


def test_bills_authentication_failure_is_not_masked_by_the_last_good_bills(modules, monkeypatch) -> None:
    _, api = modules
    client, _, _ = _stamped_client(
        api, monkeypatch, [{"data": [_bill(2026, 3)]}, api.EvnAuthenticationError("expired")]
    )
    asyncio.run(client.async_bills("PB000001"))
    with pytest.raises(api.EvnAuthenticationError):
        asyncio.run(client.async_bills("PB000001"))


def test_last_good_bills_cannot_be_changed_by_a_caller(modules, monkeypatch) -> None:
    _, api = modules
    client, _, _ = _stamped_client(
        api, monkeypatch, [{"data": [_bill(2026, 3)]}, api.EvnApiError("HTTP 500", status=500)]
    )
    rows = asyncio.run(client.async_bills("PB000001"))
    rows[0]["total_amount"] = -1
    rows.append({"junk": True})
    again = asyncio.run(client.async_bills("PB000001"))
    assert len(again) == 1 and again[0]["total_amount"] == 300000


def test_history_fetched_at_is_the_oldest_successful_fetch_and_survives_fallback(modules, monkeypatch) -> None:
    _, api = modules
    readings = {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]}
    bills = {"data": [_bill(2026, 3)]}
    client, wall, mono = _stamped_client(
        api, monkeypatch, [readings, bills, api.EvnApiError("HTTP 500", status=500), api.EvnApiError("HTTP 500", status=500)],
    )
    assert client.history_fetched_at("PB000001") == ""
    asyncio.run(client.async_monthly_readings("PB000001"))
    wall[0] = datetime(2026, 3, 15, 10, 30, 0)
    asyncio.run(client.async_bills("PB000001"))
    assert client.history_fetched_at("PB000001") == "2026-03-15T10:00:00"
    # Both later fetches fail: the stamp stays with the data that is still shown.
    wall[0] = datetime(2026, 3, 16, 8, 0, 0)
    mono[0] += 7 * 3600
    asyncio.run(client.async_monthly_readings("PB000001"))
    asyncio.run(client.async_bills("PB000001"))
    assert client.history_fetched_at("PB000001") == "2026-03-15T10:00:00"


def test_history_fetched_at_advances_with_a_fresh_fetch(modules, monkeypatch) -> None:
    _, api = modules
    client, wall, _ = _stamped_client(api, monkeypatch, [{"data": [_bill(2026, 3)]}, {"data": [_bill(2026, 3)]}])
    asyncio.run(client.async_bills("PB000001"))
    wall[0] = datetime(2026, 3, 15, 11, 0, 0)
    asyncio.run(client.async_bills("PB000001"))
    assert client.history_fetched_at("PB000001") == "2026-03-15T11:00:00"


def test_cached_history_is_none_until_bills_have_been_fetched(modules, monkeypatch) -> None:
    _, api = modules
    readings = {"data": [_reading(2026, 3, 1, 10, "01/03/2026", "31/03/2026")]}
    client, _, _ = _stamped_client(api, monkeypatch, [readings, {"data": [_bill(2026, 3)]}])
    assert client.cached_history("PB000001") is None
    asyncio.run(client.async_monthly_readings("PB000001"))
    assert client.cached_history("PB000001") is None
    asyncio.run(client.async_bills("PB000001"))
    bills, rows, stamp = client.cached_history("PB000001")
    assert [bill["THANG"] for bill in bills] == [3]
    assert [(row["month"], row["kwh"]) for row in rows] == [(3, 10.0)]
    assert stamp == "2026-03-15T10:00:00"


def test_aggregate_total_kwh_is_unknown_when_any_bill_of_the_period_has_unknown_kwh(modules) -> None:
    calculation, _ = modules
    known = {"period": "Tháng 3/2026", "total_kwh": 120.0, "total_amount": 100, "is_paid": True}
    unknown = {"period": "Tháng 3/2026", "total_kwh": None, "total_amount": 50, "is_paid": True}
    for order in ([[known], [unknown]], [[unknown], [known]]):
        assert calculation.aggregate_bills(order)[0]["total_kwh"] is None
    assert calculation.aggregate_bills([[known], [known]])[0]["total_kwh"] == 240.0


def test_aggregate_kwh_ignores_a_code_without_a_bill_for_that_period(modules) -> None:
    calculation, _ = modules
    march = {"period": "Tháng 3/2026", "total_kwh": 120.0, "total_amount": 100, "is_paid": True}
    april = {"period": "Tháng 4/2026", "total_kwh": 80.0, "total_amount": 70, "is_paid": True}
    unknown_feb = {"period": "Tháng 2/2026", "total_kwh": None, "total_amount": 50, "is_paid": True}
    rows = {row["period"]: row for row in calculation.aggregate_bills([[march, april], [march, unknown_feb]])}
    assert rows["Tháng 3/2026"]["total_kwh"] == 240.0
    assert rows["Tháng 4/2026"]["total_kwh"] == 80.0
    assert rows["Tháng 2/2026"]["total_kwh"] is None


# ------------------------------------------------------------ provenance and reconciliation

def test_bills_with_source_say_fresh_for_a_live_fetch_and_cached_for_the_fallback(modules, monkeypatch) -> None:
    _, api = modules
    good = {"data": [_bill(2026, 3, amount=300000)]}
    client, _, _ = _stamped_client(api, monkeypatch, [good, api.EvnApiError("HTTP 500", status=500)])
    rows, fresh = asyncio.run(client.async_bills_with_source("PB000001"))
    assert fresh is True and rows[0]["total_amount"] == 300000
    again, fresh = asyncio.run(client.async_bills_with_source("PB000001"))
    assert fresh is False and again == rows


def test_bills_with_source_still_raises_without_a_prior_success(modules, monkeypatch) -> None:
    _, api = modules
    client, _, _ = _stamped_client(api, monkeypatch, [api.EvnApiError("HTTP 500", status=500)])
    with pytest.raises(api.EvnApiError):
        asyncio.run(client.async_bills_with_source("PB000001"))


def _annotated(period, kwh, *, status, collected=None, diff=None, missing=0, ky=1, year=2026, month=3):
    return {
        "period": period, "total_kwh": kwh, "total_amount": 100, "is_paid": True, "period_start": "", "period_end": "",
        "calculated_amount": None, "year": year, "month": month, "ky": ky, "collected_kwh": collected,
        "diff_kwh": diff, "missing_days": missing, "reconcile_status": status, "paired_with": None,
    }


def test_aggregate_rows_carry_the_canonical_period_even_from_plain_bill_rows(modules) -> None:
    calculation, _ = modules
    plain = {"period": "Tháng 3/2026", "total_kwh": 10.0, "total_amount": 1, "is_paid": True, "KY": 2}
    row = calculation.aggregate_bills([[plain]])[0]
    assert (row["year"], row["month"], row["ky"]) == (2026, 3, 2)
    assert row["reconcile_status"] is None and row["collected_kwh"] is None and row["diff_kwh"] is None


def test_aggregate_reconciliation_sums_when_every_code_has_a_value_and_shows_the_worst_status(modules) -> None:
    calculation, _ = modules
    first = _annotated("Tháng 3/2026", 100.0, status="match", collected=100.4, diff=0.4)
    second = _annotated("Tháng 3/2026", 50.0, status="mismatch", collected=47.0, diff=-3.0)
    row = calculation.aggregate_bills([[first], [second]])[0]
    assert (row["collected_kwh"], row["diff_kwh"], row["missing_days"], row["reconcile_status"]) == (147.4, -2.6, 0, "mismatch")
    third = _annotated("Tháng 3/2026", 50.0, status="incomplete", collected=45.0, diff=-5.0, missing=2)
    row = calculation.aggregate_bills([[first], [third]])[0]
    assert (row["reconcile_status"], row["missing_days"]) == ("incomplete", 2)
    boundary = _annotated("Tháng 3/2026", 50.0, status="boundary", collected=44.0, diff=-6.0)
    assert calculation.aggregate_bills([[first], [boundary]])[0]["reconcile_status"] == "boundary"
    assert calculation.aggregate_bills([[first], [first]])[0]["reconcile_status"] == "match"


def test_aggregate_reconciliation_is_null_when_a_contributing_code_has_none(modules) -> None:
    calculation, _ = modules
    first = _annotated("Tháng 3/2026", 100.0, status="match", collected=100.4, diff=0.4)
    unreconciled = _annotated("Tháng 3/2026", 50.0, status=None)
    row = calculation.aggregate_bills([[first], [unreconciled]])[0]
    assert (row["collected_kwh"], row["diff_kwh"], row["missing_days"], row["reconcile_status"]) == (None, None, None, None)
    no_kwh = _annotated("Tháng 3/2026", None, status="no_kwh", collected=40.0, diff=None)
    row = calculation.aggregate_bills([[first], [no_kwh]])[0]
    assert (row["collected_kwh"], row["diff_kwh"], row["reconcile_status"]) == (140.4, None, "match")


def test_a_code_with_no_bill_for_the_period_does_not_block_the_aggregate_reconciliation(modules) -> None:
    calculation, _ = modules
    march = _annotated("Tháng 3/2026", 100.0, status="match", collected=100.0, diff=0.0)
    april = _annotated("Tháng 4/2026", 80.0, status="match", collected=80.5, diff=0.5, month=4)
    rows = {row["period"]: row for row in calculation.aggregate_bills([[march, april], [march]])}
    assert rows["Tháng 4/2026"]["collected_kwh"] == 80.5 and rows["Tháng 3/2026"]["collected_kwh"] == 200.0


# ---------------------------------------------------------------- meter indices on readings and bills

def _indexed(year, month, ky, kwh, start, end, old, new, **extra):
    return {**_reading(year, month, ky, kwh, start, end), "CHISO_CU": old, "CHISO_MOI": new, "SO_CTO": "METER-SECRET-77", **extra}


def test_readings_keep_the_start_and_end_index_but_never_the_meter_number(modules) -> None:
    calculation, _ = modules
    rows = calculation.normalize_readings([
        _indexed(2026, 9, 1, 100, "01/09/2026", "30/09/2026", 1200, "1300.5"),
        _indexed(2026, 8, 1, 90, "01/08/2026", "31/08/2026", None, "oops"),
    ])
    assert (rows[0]["index_start"], rows[0]["index_end"]) == (1200.0, 1300.5)
    assert (rows[1]["index_start"], rows[1]["index_end"]) == (None, None)
    assert "METER-SECRET-77" not in repr(rows)


def test_a_bill_gets_the_indices_of_its_single_reading_and_none_otherwise(modules) -> None:
    calculation, _ = modules
    readings = calculation.normalize_readings([
        _indexed(2026, 9, 1, 100, "01/09/2026", "30/09/2026", 1200, 1300),
        _indexed(2026, 8, 1, 60, "01/08/2026", "15/08/2026", 1000, 1060),
        _indexed(2026, 8, 1, 30, "16/08/2026", "31/08/2026", 5, 35),
    ])
    bills = calculation.attach_readings(calculation.normalize_bills([_bill(2026, 9), _bill(2026, 8), _bill(2026, 7)]), readings)
    assert [(b["index_start"], b["index_end"]) for b in bills] == [(1200.0, 1300.0), (None, None), (None, None)], (
        "after a meter swap the two readings belong to different meters, so no index is shown"
    )


# ---------------------------------------------------------------- the latest daily index reads 31 days

def test_the_overview_reads_the_latest_index_from_the_last_31_days(modules, monkeypatch) -> None:
    _, api = modules
    from datetime import date

    monkeypatch.setattr(api.dt_util, "now", lambda: datetime(2026, 10, 1, 9, 0))
    state = api.SessionState("user", "tok", "ref", "dev", "PB000001", "PB000001")
    client = api.EvnClient(object(), state, {"PB000001": "PB000001009"})
    client._meter_points["PB000001"] = "PB000001009"
    asked = []

    async def daily(code, start, end):
        return []

    async def monthly(code, month, year, point):
        return 0.0

    async def readings(code, start, end, point):
        asked.append((start, end))
        return [{"NGAY": "30/09/2026", "CHISO_MOI": 1300.5}]

    monkeypatch.setattr(client, "async_daily", daily)
    monkeypatch.setattr(client, "_async_monthly_fallback", monthly)
    monkeypatch.setattr(client, "_async_readings", readings)
    overview = asyncio.run(client.async_overview("PB000001"))
    assert asked == [(date(2026, 9, 1), date(2026, 10, 1))], "31 days ending today, not from day 1 of a month with no reading yet"
    assert (overview["latest_index"], overview["latest_date"]) == (1300.5, "30/09/2026")
