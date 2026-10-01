"""Unpaid bills: payment state, the merge with the paid history, the unpaid endpoint and its cadence.

Every code and figure here is synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_unpaid_test"
CODE = "PB00000000001"


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
    const = _load_module("const")
    _load_module("tariff")
    calculation = _load_module("calculation")
    _load_module("models")
    api = _load_module("api")
    return types.SimpleNamespace(const=const, calculation=calculation, api=api)


# Rows shaped like the probe results: the field names are real, the values are invented.
def _unpaid_row(year=2026, month=9, ky=1, *, owed=250000, total=250000, kwh=100, due="15/10/2026", **extra):
    return {
        "TTRANG_TTOAN": "CHUATT", "HAN_TTOAN": due, "TIEN_NO": owed - 18518, "THUE_NO": 18518, "TONG_NO": owed,
        "NGAY_DKY": "01/09/2026", "NGAY_CKY": "30/09/2026", "DIEN_TTHU": kwh,
        "SO_TIEN": total - 18518, "TIEN_GTGT": 18518, "TONG_TIEN": total,
        "KY": ky, "THANG": month, "NAM": year, "ID_HDON": 111222333,
        "TEN_KHANG": "Nguyen Test Person", "DCHI_KHANG": "1 Test Street", "MA_KHANG": CODE, "MA_DVIQLY": "PB0000",
        **extra,
    }


def _paid_row(year=2026, month=8, ky=1, *, total=240000, paid="05/09/2026", **extra):
    return {
        "NAM": year, "THANG": month, "KY": ky, "DIEN_TTHU": 0, "TONG_TIEN": total, "NGAY_TTOAN": paid,
        "TEN_KHANG": "Nguyen Test Person", "DCHI_KHANG": "1 Test Street", "MA_KHANG": CODE, "ID_HDON": 444555666,
        **extra,
    }


# ---------------------------------------------------------------- payment state

@pytest.mark.parametrize("row, expected", [
    ({"TTRANG_TTOAN": "CHUATT"}, ("unpaid", False)),
    ({"TTRANG_TTOAN": "DATT"}, ("paid", True)),
    ({"TTRANG_TTOAN": " chuatt "}, ("unpaid", False)),
    ({"NGAY_TTOAN": "05/09/2026"}, ("paid", True)),
    ({"TTRANG_TTOAN": "KHAC"}, ("unknown", None)),
    ({"TTRANG_TTOAN": "KHAC", "NGAY_TTOAN": "05/09/2026"}, ("paid", True)),
    ({}, ("unknown", None)),
    ({"isPaid": False}, ("unpaid", False)),
])
def test_payment_state_comes_from_the_status_code_never_from_a_default(modules, row, expected) -> None:
    bill = modules.calculation.normalize_bills([{"THANG": 9, "NAM": 2026, "KY": 1, "TONG_TIEN": 10, **row}])[0]
    assert (bill["payment_status"], bill["is_paid"]) == expected


def test_an_unpaid_row_carries_due_date_owed_amount_period_and_kwh(modules) -> None:
    bill = modules.calculation.normalize_bills([_unpaid_row(owed=123456, total=250000)], source="unpaid")[0]
    assert bill["payment_status"] == "unpaid" and bill["is_paid"] is False
    assert (bill["due_date"], bill["amount_owed"], bill["total_amount"]) == ("2026-10-15", 123456, 250000)
    assert (bill["period_start"], bill["period_end"], bill["total_kwh"]) == ("2026-09-01", "2026-09-30", 100.0)
    assert (bill["paid_on"], bill["bill_source"], bill["payment_checked"]) == ("", "unpaid", True)


def test_a_paid_row_carries_the_payment_date_and_no_amount_owed(modules) -> None:
    bill = modules.calculation.normalize_bills([_paid_row(paid="05/09/2026")])[0]
    assert bill["payment_status"] == "paid" and bill["is_paid"] is True
    assert (bill["paid_on"], bill["due_date"], bill["amount_owed"], bill["bill_source"]) == ("2026-09-05", "", None, "history")
    assert bill["total_kwh"] is None, "the history rows carry no real kWh"


@pytest.mark.parametrize("bad", ["", None, "soon", "31/02/2026"])
def test_an_unusable_date_or_amount_is_unknown_not_invented(modules, bad) -> None:
    bill = modules.calculation.normalize_bills([_unpaid_row(due=bad, NGAY_DKY=bad, NGAY_CKY=bad, TONG_NO=bad)])[0]
    assert (bill["due_date"], bill["period_start"], bill["period_end"], bill["amount_owed"]) == ("", "", "", None)


def test_no_identity_field_survives_normalization(modules) -> None:
    dumped = json.dumps(modules.calculation.normalize_bills([_unpaid_row(), _paid_row()], source="unpaid"), ensure_ascii=False)
    for private in ("Nguyen Test Person", "1 Test Street", CODE, "PB0000", "111222333", "444555666"):
        assert private not in dumped


def test_there_is_no_default_paid_left_in_the_code() -> None:
    assert 'isPaid", True' not in (INTEGRATION_DIR / "calculation.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- merge

def _rows(modules, rows, source):
    return modules.calculation.normalize_bills(rows, source=source)


def _merge(modules, history, unpaid, *, fresh=True):
    return modules.calculation.merge_bill_sources(
        _rows(modules, history, "history"), _rows(modules, unpaid, "unpaid"), unpaid_fresh=fresh,
    )


def test_a_period_only_in_the_unpaid_list_is_added_as_unpaid(modules) -> None:
    merged = _merge(modules, [_paid_row(month=8)], [_unpaid_row(month=9)])
    assert [(b["THANG"], b["payment_status"], b["payment_checked"]) for b in merged] == [(9, "unpaid", True), (8, "paid", True)]


def test_a_fresh_unpaid_row_stands_for_its_period_even_when_the_history_lists_it_too(modules) -> None:
    merged = _merge(modules, [_paid_row(month=9)], [_unpaid_row(month=9)])
    assert len(merged) == 1 and merged[0]["payment_status"] == "unpaid" and merged[0]["bill_source"] == "unpaid"


def test_a_paid_history_row_beats_a_cached_unpaid_row(modules) -> None:
    merged = _merge(modules, [_paid_row(month=9)], [_unpaid_row(month=9)], fresh=False)
    assert len(merged) == 1 and merged[0]["payment_status"] == "paid" and merged[0]["bill_source"] == "history"
    assert merged[0]["payment_checked"] is True


def test_a_cached_unpaid_row_without_a_history_row_stays_unpaid_but_unchecked(modules) -> None:
    merged = _merge(modules, [_paid_row(month=8)], [_unpaid_row(month=9)], fresh=False)
    assert (merged[0]["THANG"], merged[0]["payment_status"], merged[0]["payment_checked"]) == (9, "unpaid", False)
    assert merged[1]["payment_checked"] is True


def test_a_period_only_in_the_history_is_kept_as_it_is(modules) -> None:
    merged = _merge(modules, [_paid_row(month=8), _paid_row(month=7)], [])
    assert [b["THANG"] for b in merged] == [8, 7] and all(b["payment_status"] == "paid" for b in merged)


def test_two_invoices_of_one_history_period_both_stay(modules) -> None:
    merged = _merge(modules, [_paid_row(month=8, total=100), _paid_row(month=8, total=50)], [])
    assert [b["total_amount"] for b in merged] == [100, 50]


def test_rows_without_a_period_are_listed_after_the_others_and_never_merged(modules, caplog) -> None:
    no_ky = _paid_row(month=8)
    del no_ky["KY"]
    unpaid_no_ky = _unpaid_row(month=9)
    unpaid_no_ky["KY"] = None
    with caplog.at_level(logging.DEBUG):
        merged = _merge(modules, [no_ky, _paid_row(month=7)], [unpaid_no_ky, _unpaid_row(month=9)])
    assert [(b["THANG"], b["KY"]) for b in merged] == [(9, 1), (7, 1), (8, None), (9, None)]
    assert [b["payment_status"] for b in merged] == ["unpaid", "paid", "paid", "unpaid"]
    assert any("without a billing period" in record.getMessage() for record in caplog.records)
    assert CODE not in caplog.text


def test_the_merge_never_changes_its_inputs(modules) -> None:
    history, unpaid = _rows(modules, [_paid_row()], "history"), _rows(modules, [_unpaid_row()], "unpaid")
    before = json.dumps([history, unpaid], sort_keys=True)
    modules.calculation.merge_bill_sources(history, unpaid, unpaid_fresh=True)
    assert json.dumps([history, unpaid], sort_keys=True) == before


def test_readings_still_override_kwh_and_period_without_counting_twice(modules) -> None:
    calc = modules.calculation
    merged = _merge(modules, [_paid_row(month=9)], [_unpaid_row(month=9, kwh=100)])
    readings = calc.normalize_readings([
        {"NAM": 2026, "THANG": 9, "KY": 1, "DIEN_TTHU": 97, "NGAY_DKY": "01/09/2026", "NGAY_CKY": "30/09/2026"},
    ])
    bills = calc.attach_readings(merged, readings)
    assert len(bills) == 1 and bills[0]["total_kwh"] == 97.0


# ---------------------------------------------------------------- per-code summary and aggregate

def test_the_summary_counts_unpaid_bills_and_takes_the_earliest_due_date(modules) -> None:
    bills = _merge(modules, [_paid_row(month=7)], [
        _unpaid_row(month=8, owed=100000, due="10/10/2026"), _unpaid_row(month=9, owed=200000, due="05/10/2026"),
    ])
    summary = modules.calculation.unpaid_summary(bills, loaded=True)
    assert summary == {"unpaid_count": 2, "unpaid_amount": 300000, "next_due_date": "2026-10-05"}


def test_the_summary_is_zero_when_loaded_and_nothing_is_unpaid(modules) -> None:
    bills = _merge(modules, [_paid_row()], [])
    assert modules.calculation.unpaid_summary(bills, loaded=True) == {"unpaid_count": 0, "unpaid_amount": 0, "next_due_date": None}


def test_the_summary_is_unknown_when_the_unpaid_list_never_loaded(modules) -> None:
    bills = _merge(modules, [_paid_row()], [])
    assert modules.calculation.unpaid_summary(bills, loaded=False) == {
        "unpaid_count": None, "unpaid_amount": None, "next_due_date": None,
    }


def test_an_unpaid_bill_without_an_owed_amount_counts_its_total(modules) -> None:
    row = _unpaid_row(total=250000)
    del row["TONG_NO"]
    summary = modules.calculation.unpaid_summary(_merge(modules, [], [row]), loaded=True)
    assert summary["unpaid_amount"] == 250000


def _overview(count, amount, due, **extra):
    return {"unpaid_count": count, "unpaid_amount": amount, "next_due_date": due, **extra}


def test_the_aggregate_sums_the_codes_and_takes_the_earliest_due_date(modules) -> None:
    aggregate = modules.calculation.aggregate_overviews(
        [_overview(1, 100, "2026-10-09"), _overview(2, 50, "2026-10-03"), _overview(0, 0, None)], ["A", "B", "C"],
    )
    assert (aggregate["unpaid_count"], aggregate["unpaid_amount"], aggregate["next_due_date"]) == (3, 150, "2026-10-03")


def test_the_aggregate_is_unknown_as_soon_as_one_code_is(modules) -> None:
    aggregate = modules.calculation.aggregate_overviews([_overview(1, 100, "2026-10-09"), _overview(None, None, None)], ["A", "B"])
    assert (aggregate["unpaid_count"], aggregate["unpaid_amount"]) == (None, None)
    assert aggregate["next_due_date"] == "2026-10-09"


def test_aggregate_bills_keep_the_worst_payment_state_and_add_what_is_owed(modules) -> None:
    calc = modules.calculation
    paid = _merge(modules, [_paid_row(month=9, total=100)], [])
    unpaid = _merge(modules, [], [_unpaid_row(month=9, owed=70, due="05/10/2026")])
    row = calc.aggregate_bills([paid, unpaid])[0]
    assert (row["payment_status"], row["is_paid"], row["amount_owed"], row["due_date"]) == ("unpaid", False, 70, "2026-10-05")
    assert row["payment_checked"] is True
    both_paid = calc.aggregate_bills([paid, paid])[0]
    assert (both_paid["payment_status"], both_paid["is_paid"], both_paid["amount_owed"]) == ("paid", True, None)
    unknown = _merge(modules, [{"THANG": 9, "NAM": 2026, "KY": 1, "TONG_TIEN": 5}], [])
    assert calc.aggregate_bills([paid, unknown])[0]["payment_status"] == "unknown"


def test_aggregate_bills_of_older_rows_without_a_payment_status_still_work(modules) -> None:
    old = {"period": "Tháng 3/2026", "total_kwh": 1.0, "total_amount": 1, "is_paid": True, "KY": 1}
    row = modules.calculation.aggregate_bills([[old]])[0]
    assert (row["payment_status"], row["is_paid"]) == ("paid", True)


def test_an_unchecked_period_stays_unchecked_in_the_aggregate(modules) -> None:
    rows = _merge(modules, [], [_unpaid_row(month=9)], fresh=False)
    assert modules.calculation.aggregate_bills([rows])[0]["payment_checked"] is False


# ---------------------------------------------------------------- the client

def _client(modules):
    state = modules.api.SessionState("user", "tok", "ref", "dev", CODE, CODE)
    client = modules.api.EvnClient(object(), state, {CODE: CODE + "009"})
    clock = {"now": 1000.0}
    client._clock = lambda: clock["now"]
    return client, clock


def _script(modules, monkeypatch, client, answers):
    """Answer each request in turn: a list payload, or an exception to raise."""
    calls: list[tuple[str, str, dict | None]] = []
    switched: list[str] = []

    async def switch(code):
        switched.append(code)

    async def request(method, url, body=None):
        calls.append((method, url, body))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "_async_switch_customer", switch)
    monkeypatch.setattr(client, "_async_request", request)
    return calls, switched


def test_the_client_reads_the_unpaid_list_after_switching_customer(modules, monkeypatch) -> None:
    client, _ = _client(modules)
    calls, switched = _script(modules, monkeypatch, client, [{"data": [_unpaid_row()]}])
    bills = asyncio.run(client.async_unpaid_bills(CODE))
    assert switched == [CODE]
    assert len(calls) == 1 and calls[0][0] == "POST" and calls[0][1].endswith("/tracuu/hoadon-thanhtoan")
    assert calls[0][2] == {"MA_KHANG": CODE}
    assert bills[0]["payment_status"] == "unpaid" and bills[0]["bill_source"] == "unpaid"
    assert "Nguyen Test Person" not in json.dumps(client.last_shapes), "shapes hold key names and types only"
    assert set(client.last_shapes["unpaid_bills"]) >= {"TTRANG_TTOAN", "HAN_TTOAN", "TONG_NO"}


def test_the_unpaid_list_is_asked_at_most_once_in_two_hours(modules, monkeypatch) -> None:
    client, clock = _client(modules)
    calls, _ = _script(modules, monkeypatch, client, [{"data": [_unpaid_row()]}, {"data": []}])
    first = asyncio.run(client.async_unpaid_bills_with_source(CODE))
    clock["now"] += 3600
    second = asyncio.run(client.async_unpaid_bills_with_source(CODE))
    assert len(calls) == 1 and first[1] is True and second[1] is True and second[0] == first[0]
    clock["now"] += 3601
    third = asyncio.run(client.async_unpaid_bills_with_source(CODE))
    assert len(calls) == 2 and third == ([], True)


def test_a_failed_fetch_falls_back_to_the_last_good_copy_and_waits_before_asking_again(modules, monkeypatch) -> None:
    client, clock = _client(modules)
    calls, _ = _script(modules, monkeypatch, client, [
        {"data": [_unpaid_row()]}, modules.api.EvnApiError("HTTP 500", status=500), {"data": []},
    ])
    good = asyncio.run(client.async_unpaid_bills_with_source(CODE))
    clock["now"] += 7300
    stale = asyncio.run(client.async_unpaid_bills_with_source(CODE))
    assert stale[0] == good[0] and stale[1] is False and len(calls) == 2
    clock["now"] += 1800
    assert asyncio.run(client.async_unpaid_bills_with_source(CODE)) == stale and len(calls) == 2, "no new request within the cadence"
    clock["now"] += 5400
    assert asyncio.run(client.async_unpaid_bills_with_source(CODE)) == ([], True) and len(calls) == 3


def test_a_code_that_keeps_being_refused_is_not_asked_every_refresh(modules, monkeypatch) -> None:
    client, clock = _client(modules)
    calls, _ = _script(modules, monkeypatch, client, [modules.api.EvnApiError("HTTP 400", status=400)] * 3 + [{"data": []}])
    for _ in range(2):
        with pytest.raises(modules.api.EvnApiError):
            asyncio.run(client.async_unpaid_bills(CODE))
        clock["now"] += 1800
    assert len(calls) == 1
    clock["now"] += 5400
    with pytest.raises(modules.api.EvnApiError):
        asyncio.run(client.async_unpaid_bills(CODE))
    assert len(calls) == 2


def test_a_code_that_was_never_read_raises(modules, monkeypatch) -> None:
    client, _ = _client(modules)
    _script(modules, monkeypatch, client, [modules.api.EvnApiError("HTTP 400", status=400)])
    with pytest.raises(modules.api.EvnApiError):
        asyncio.run(client.async_unpaid_bills(CODE))


def test_an_authentication_error_is_never_swallowed(modules, monkeypatch) -> None:
    client, clock = _client(modules)
    _script(modules, monkeypatch, client, [{"data": []}, modules.api.EvnAuthenticationError("expired")])
    asyncio.run(client.async_unpaid_bills(CODE))
    clock["now"] += 7300
    with pytest.raises(modules.api.EvnAuthenticationError):
        asyncio.run(client.async_unpaid_bills(CODE))


def test_the_cache_returns_copies(modules, monkeypatch) -> None:
    client, _ = _client(modules)
    _script(modules, monkeypatch, client, [{"data": [_unpaid_row()]}])
    first = asyncio.run(client.async_unpaid_bills(CODE))
    first[0]["payment_status"] = "paid"
    assert asyncio.run(client.async_unpaid_bills(CODE))[0]["payment_status"] == "unpaid"


def test_the_cadence_constant_is_two_hours(modules) -> None:
    assert modules.const.UNPAID_REFRESH == timedelta(hours=2)


def test_the_aggregate_period_is_unpaid_when_any_code_is_whatever_the_others_are(modules) -> None:
    calc = modules.calculation
    paid = _merge(modules, [_paid_row(month=9, total=100)], [])
    unknown = _merge(modules, [{"THANG": 9, "NAM": 2026, "KY": 1, "TONG_TIEN": 5}], [])
    unpaid = _merge(modules, [], [_unpaid_row(month=9, owed=70, due="05/10/2026")])
    for order in ([unknown, unpaid], [unpaid, unknown], [paid, unknown, unpaid], [unpaid, paid, unknown]):
        row = calc.aggregate_bills(order)[0]
        assert (row["payment_status"], row["is_paid"], row["amount_owed"], row["due_date"]) == ("unpaid", False, 70, "2026-10-05")
