"""Planned outages: parsing, time zone, the client's window and cadence, and what is dropped.

Every code, place and name here is synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_outages_test"
CODE = "PB00000000001"
ICT = timezone(timedelta(hours=7))


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


# A row shaped like the probe result: the field names are real, the values are invented.
def _row(start="05/10/2026 08:00", end="05/10/2026 11:30", status="D", **extra):
    return {
        "TGIAN_BDAU": start, "TGIAN_KTHUC": end, "TTHAI_HOAN": status,
        "TENPHANTU": "Trạm Giả Định 9", "LY_DO": "Sửa chữa nhà ông Thử", "KHUVUCMATDIEN": "Khu vực Giả Định",
        "MA_KHANG": CODE, **extra,
    }


# ---------------------------------------------------------------- parsing

def test_a_row_keeps_only_start_end_and_status_in_the_given_time_zone(modules) -> None:
    out = modules.calculation.normalize_outages([_row()], ICT)
    assert out == [{"start": "2026-10-05T08:00:00+07:00", "end": "2026-10-05T11:30:00+07:00", "status": "D"}]


def test_no_place_name_reason_or_code_survives(modules) -> None:
    dumped = json.dumps(modules.calculation.normalize_outages([_row()], ICT), ensure_ascii=False)
    for private in ("Trạm Giả Định 9", "Sửa chữa nhà ông Thử", "Khu vực Giả Định", CODE):
        assert private not in dumped


@pytest.mark.parametrize("row", [
    _row(start=""), _row(end=None), _row(start="2026-10-05 08:00"), _row(start="31/02/2026 08:00"),
    _row(end="05/10/2026"), "not a row", None, {},
])
def test_rows_that_cannot_be_parsed_are_dropped(modules, row) -> None:
    assert modules.calculation.normalize_outages([row, _row()], ICT) == modules.calculation.normalize_outages([_row()], ICT)


def test_outages_are_sorted_by_start(modules) -> None:
    out = modules.calculation.normalize_outages([_row(start="09/10/2026 08:00", end="09/10/2026 09:00"), _row()], ICT)
    assert [item["start"][:10] for item in out] == ["2026-10-05", "2026-10-09"]


@pytest.mark.parametrize("status, expected", [("D", "D"), (" d ", "d"), (None, ""), (3, "3")])
def test_the_status_is_kept_as_the_raw_short_code(modules, status, expected) -> None:
    assert modules.calculation.normalize_outages([_row(status=status)], ICT)[0]["status"] == expected


def test_past_outages_are_dropped_when_asked_for_the_upcoming_ones(modules) -> None:
    calc = modules.calculation
    outages = calc.normalize_outages([
        _row(start="01/10/2026 08:00", end="01/10/2026 09:00"),
        _row(start="02/10/2026 08:00", end="02/10/2026 18:00"),
        _row(start="05/10/2026 08:00", end="05/10/2026 09:00"),
    ], ICT)
    now = datetime(2026, 10, 2, 12, 0, tzinfo=ICT)
    assert [item["start"][:10] for item in calc.upcoming_outages(outages, now)] == ["2026-10-02", "2026-10-05"], "one in progress still counts"
    assert calc.upcoming_outages(outages, datetime(2026, 10, 2, 18, 0, 1, tzinfo=ICT))[0]["start"][:10] == "2026-10-05"


def test_the_summary_names_the_next_outage_and_counts_the_upcoming_ones(modules) -> None:
    calc = modules.calculation
    outages = calc.normalize_outages([_row(), _row(start="09/10/2026 08:00", end="09/10/2026 09:00", status="K")], ICT)
    summary = calc.outage_summary(outages, loaded=True)
    assert summary["next_planned_outage"] == "2026-10-05T08:00:00+07:00"
    assert (summary["outage_end"], summary["outage_status"], summary["upcoming_outage_count"]) == ("2026-10-05T11:30:00+07:00", "D", 2)
    assert summary["outages"] == outages


def test_the_summary_with_nothing_planned_is_zero_and_never_loaded_is_unknown(modules) -> None:
    calc = modules.calculation
    none = calc.outage_summary([], loaded=True)
    assert (none["next_planned_outage"], none["upcoming_outage_count"], none["outages"]) == (None, 0, [])
    unknown = calc.outage_summary([], loaded=False)
    assert (unknown["next_planned_outage"], unknown["upcoming_outage_count"], unknown["outages"]) == (None, None, [])


# ---------------------------------------------------------------- the client

def _client(modules, monkeypatch):
    state = modules.api.SessionState("user", "tok", "ref", "dev", CODE, CODE)
    client = modules.api.EvnClient(object(), state, {CODE: CODE + "009"})
    clock = {"now": 1000.0}
    client._clock = lambda: clock["now"]
    # A Home Assistant set to UTC must not move EVN's Vietnam clock times.
    monkeypatch.setattr(modules.api.dt_util, "now", lambda: datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc))
    return client, clock


def _script(monkeypatch, client, answers):
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


def test_the_client_sends_the_dates_and_reads_the_outages_after_switching_customer(modules, monkeypatch) -> None:
    client, _ = _client(modules, monkeypatch)
    calls, switched = _script(monkeypatch, client, [{"data": [_row()]}])
    out = asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15)))
    assert switched == [CODE]
    method, url, body = calls[0]
    assert method == "POST" and url.endswith("/tracuu/ngungcapdien")
    assert body == {"MA_KHANG": CODE, "TU_NGAY": "01/10/2026", "DEN_NGAY": "15/10/2026"}
    assert out == [{"start": "2026-10-05T08:00:00+07:00", "end": "2026-10-05T11:30:00+07:00", "status": "D"}]
    assert "Trạm Giả Định 9" not in json.dumps(client.last_shapes, ensure_ascii=False)
    assert set(client.last_shapes["outages"]) >= {"TGIAN_BDAU", "TGIAN_KTHUC", "TTHAI_HOAN"}


def test_the_outages_are_asked_at_most_once_in_six_hours(modules, monkeypatch) -> None:
    client, clock = _client(modules, monkeypatch)
    calls, _ = _script(monkeypatch, client, [{"data": [_row()]}, {"data": []}])
    first = asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15)))
    clock["now"] += 5 * 3600
    assert asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15))) == first and len(calls) == 1
    clock["now"] += 3601
    assert asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15))) == [] and len(calls) == 2


def test_a_failed_fetch_keeps_the_last_good_copy_and_a_new_code_raises(modules, monkeypatch) -> None:
    client, clock = _client(modules, monkeypatch)
    _script(monkeypatch, client, [
        {"data": [_row()]}, modules.api.EvnApiError("HTTP 500", status=500), modules.api.EvnApiError("HTTP 400", status=400),
    ])
    window = (date(2026, 10, 1), date(2026, 10, 15))
    good = asyncio.run(client.async_outages(CODE, *window))
    clock["now"] += 7 * 3600
    assert asyncio.run(client.async_outages(CODE, *window)) == good
    other = "PB00000000002"
    with pytest.raises(modules.api.EvnApiError):
        asyncio.run(client.async_outages(other, *window))


def test_an_authentication_error_is_never_swallowed(modules, monkeypatch) -> None:
    client, clock = _client(modules, monkeypatch)
    _script(monkeypatch, client, [{"data": []}, modules.api.EvnAuthenticationError("expired")])
    window = (date(2026, 10, 1), date(2026, 10, 15))
    asyncio.run(client.async_outages(CODE, *window))
    clock["now"] += 7 * 3600
    with pytest.raises(modules.api.EvnAuthenticationError):
        asyncio.run(client.async_outages(CODE, *window))


def test_the_cache_returns_copies(modules, monkeypatch) -> None:
    client, _ = _client(modules, monkeypatch)
    _script(monkeypatch, client, [{"data": [_row()]}])
    window = (date(2026, 10, 1), date(2026, 10, 15))
    asyncio.run(client.async_outages(CODE, *window))[0]["status"] = "X"
    assert asyncio.run(client.async_outages(CODE, *window))[0]["status"] == "D"


def test_the_client_logs_no_place_name_or_reason(modules, monkeypatch, caplog) -> None:
    client, _ = _client(modules, monkeypatch)
    _script(monkeypatch, client, [{"data": [_row(), _row(start="bad")]}])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15)))
    for private in ("Trạm Giả Định 9", "Sửa chữa nhà ông Thử", "Khu vực Giả Định", CODE):
        assert private not in caplog.text


def test_the_window_constants(modules) -> None:
    assert modules.const.OUTAGE_LOOKAHEAD_DAYS == 14 and modules.const.OUTAGE_REFRESH == timedelta(hours=6)


def test_an_outage_that_crosses_local_midnight_is_upcoming_on_both_sides_of_it(modules) -> None:
    calc = modules.calculation
    outages = calc.normalize_outages([_row(start="04/10/2026 23:30", end="05/10/2026 01:00")], ICT)
    assert calc.upcoming_outages(outages, datetime(2026, 10, 4, 23, 0, tzinfo=ICT))
    assert calc.upcoming_outages(outages, datetime(2026, 10, 5, 0, 30, tzinfo=ICT)), "in progress after midnight"
    assert not calc.upcoming_outages(outages, datetime(2026, 10, 5, 1, 0, 1, tzinfo=ICT))


def test_an_outage_in_progress_is_still_the_next_one_with_its_own_end(modules) -> None:
    calc = modules.calculation
    outages = calc.normalize_outages([_row(start="02/10/2026 08:00", end="02/10/2026 18:00")], ICT)
    summary = calc.outage_summary(calc.upcoming_outages(outages, datetime(2026, 10, 2, 12, 0, tzinfo=ICT)), loaded=True)
    assert (summary["next_planned_outage"], summary["outage_end"], summary["upcoming_outage_count"]) == (
        "2026-10-02T08:00:00+07:00", "2026-10-02T18:00:00+07:00", 1,
    )


def test_a_failed_outage_fetch_waits_before_asking_again(modules, monkeypatch) -> None:
    client, clock = _client(modules, monkeypatch)
    calls, _ = _script(monkeypatch, client, [
        {"data": [_row()]}, modules.api.EvnApiError("HTTP 500", status=500), {"data": []},
    ])
    window = (date(2026, 10, 1), date(2026, 10, 15))
    good = asyncio.run(client.async_outages(CODE, *window))
    clock["now"] += 7 * 3600
    assert asyncio.run(client.async_outages(CODE, *window)) == good
    clock["now"] += 1800
    assert asyncio.run(client.async_outages(CODE, *window)) == good and len(calls) == 2
    clock["now"] += 6 * 3600
    assert asyncio.run(client.async_outages(CODE, *window)) == [] and len(calls) == 3


def test_a_refused_code_is_not_asked_for_outages_every_refresh(modules, monkeypatch) -> None:
    client, clock = _client(modules, monkeypatch)
    calls, _ = _script(monkeypatch, client, [modules.api.EvnApiError("HTTP 400", status=400)] * 2)
    for _ in range(3):
        with pytest.raises(modules.api.EvnApiError):
            asyncio.run(client.async_outages(CODE, date(2026, 10, 1), date(2026, 10, 15)))
        clock["now"] += 1800
    assert len(calls) == 1


def test_the_status_is_a_short_code(modules) -> None:
    long = modules.calculation.normalize_outages([_row(status="X" * 200)], ICT)[0]["status"]
    assert len(long) <= 4
