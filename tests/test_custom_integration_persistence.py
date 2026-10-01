"""Config-entry persistence contracts for EVN silent reauthentication."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_persistence_test"


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

    class ConfigFlow:
        def __init_subclass__(cls, **_kwargs):
            return super().__init_subclass__()

        async def async_set_unique_id(self, _value):
            return None

        def _abort_if_unique_id_configured(self):
            return None

        def async_create_entry(self, *, title, data):
            return {"title": title, "data": data}

        def async_show_form(self, **kwargs):
            return kwargs

        def async_abort(self, **kwargs):
            return kwargs

    class OptionsFlow:
        pass

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    const = types.ModuleType("homeassistant.const")
    const.CONF_PASSWORD = "password"
    const.CONF_SCAN_INTERVAL = "scan_interval"
    const.CONF_USERNAME = "username"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda function: function
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = RuntimeError
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: object()
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs):
            return None

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = RuntimeError
    sys.modules["homeassistant"] = types.ModuleType("homeassistant")
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.exceptions"] = exceptions
    sys.modules["homeassistant.helpers"] = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["aiohttp"] = types.SimpleNamespace(ClientSession=object, ClientError=Exception, ContentTypeError=ValueError)
    sys.modules["voluptuous"] = types.SimpleNamespace(
        Schema=lambda value: value,
        Required=lambda value, **_kwargs: value,
        All=lambda *values: values[-1],
        Coerce=lambda _type: _type,
        Range=lambda **_kwargs: lambda value: value,
        Invalid=ValueError,
    )
    sys.modules["homeassistant.util"] = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = __import__("datetime").datetime.now
    sys.modules["homeassistant.util.dt"] = dt

    _load_module("const")
    _load_module("calculation")
    model = _load_module("models")
    api = _load_module("api")
    return model, api, _load_module("config_flow"), _load_module("coordinator")


def test_new_entry_persists_dummy_username_and_password(modules, monkeypatch) -> None:
    model, api, config_flow, _ = modules
    state = model.SessionState("user", "token", "refresh", "device-1", "PB000001", "PB000001")

    async def login(_cls, *_args):
        return state, {"PB000001": "PB000001009"}

    monkeypatch.setattr(api.EvnClient, "async_login_and_discover", classmethod(login))
    flow = config_flow.EvnVietnamConfigFlow()
    flow.hass = object()
    result = asyncio.run(flow.async_step_user({"username": "user", "password": "secret-pass"}))

    assert result["data"]["username"] == "user"
    assert result["data"]["password"] == "secret-pass"
    assert result["data"]["device_id"] == "device-1"


def test_token_persistence_retains_credentials_and_roster(modules) -> None:
    model, _, _, coordinator = modules
    original = {
        "username": "user", "password": "secret-pass", "access_token": "old", "refresh_token": "old-refresh",
        "device_id": "device-1", "primary_customer_code": "PB000001", "current_customer_code": "PB000001",
        "linked_customers": {"PB000001": "PB000001009", "PB000002": "PB000002009"},
    }
    entry = types.SimpleNamespace(data=original, options={})
    updated: list[dict] = []
    instance = object.__new__(coordinator.EvnDataUpdateCoordinator)
    instance.config_entry = entry
    instance._state = model.SessionState("user", "new", "new-refresh", "device-1", "PB000001", "PB000001")
    instance._linked_customer_meter_points = dict(original["linked_customers"])
    instance._client = types.SimpleNamespace(linked_customer_meter_points={"PB000001": "PB000001009"})
    instance.hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(
        async_update_entry=lambda _entry, *, data: updated.append(data),
    ))

    instance._persist_changed_tokens()

    assert updated[0]["username"] == "user"
    assert updated[0]["password"] == "secret-pass"
    assert updated[0]["access_token"] == "new"
    assert updated[0]["linked_customers"] == original["linked_customers"]


def test_reauth_retains_roster_password_and_device_id(modules, monkeypatch) -> None:
    model, api, config_flow, _ = modules
    entry = types.SimpleNamespace(
        entry_id="entry-1",
        data={
            "username": "user", "password": "secret-pass", "access_token": "old", "refresh_token": "old-refresh",
            "device_id": "device-1", "primary_customer_code": "PB000001", "current_customer_code": "PB000001",
            "linked_customers": {"PB000001": "PB000001009", "PB000002": "PB000002009"},
        },
        options={"customer_codes": ["PB000002"]},
    )
    state = model.SessionState("user", "new", "new-refresh", "device-1", "PB000001", "PB000001")
    received: list[object] = []

    async def login(_cls, _session, username, password, previous_roster, device_id):
        received.extend([username, password, previous_roster, device_id])
        return state, {"PB000001": "PB000001009", "PB000002": "PB000002009"}

    monkeypatch.setattr(api.EvnClient, "async_login_and_discover", classmethod(login))
    updates: list[dict] = []
    flow = config_flow.EvnVietnamConfigFlow()
    flow._reauth_entry = entry
    flow.hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(
        async_update_entry=lambda _entry, *, data: updates.append(data),
        async_reload=lambda _entry_id: asyncio.sleep(0),
    ))

    result = asyncio.run(flow.async_step_reauth_confirm({"username": "user", "password": "secret-pass"}))

    assert result["reason"] == "reauth_successful"
    assert received[-1] == "device-1"
    assert updates[0]["password"] == "secret-pass"
    assert updates[0]["linked_customers"]["PB000002"] == "PB000002009"


def _update_coordinator(modules, client):
    model, _, _, coordinator = modules
    entry = types.SimpleNamespace(
        data={"primary_customer_code": "PB000001", "linked_customers": {"PB000001": "PB000001009"}},
        options={},
    )
    instance = object.__new__(coordinator.EvnDataUpdateCoordinator)
    instance.config_entry = entry
    instance._update_lock = asyncio.Lock()
    instance._client = client
    instance._persist_changed_tokens = lambda: None
    return instance


def _bill_client(api, readings):
    async def overview(_code):
        return {"customer_code": "PB000001", "current_month_consumption": 1.0, "current_month_amount": 1}

    async def bills(_code):
        return [{
            "period": "Tháng 3/2026", "total_kwh": None, "total_amount": 300000, "is_paid": True,
            "issue_date": "", "KY": 1, "THANG": 3, "NAM": 2026, "period_start": "", "period_end": "",
        }]

    async def monthly_readings(_code):
        if isinstance(readings, Exception):
            raise readings
        return readings

    async def bills_with_source(code):
        return await bills(code), True

    return types.SimpleNamespace(
        async_overview=overview, async_bills=bills, async_bills_with_source=bills_with_source,
        async_monthly_readings=monthly_readings, last_shapes={}, linked_customer_meter_points={},
        history_fetched_at=lambda _code: "", cached_history=lambda _code: None,
    )


def test_update_joins_readings_onto_bills(modules) -> None:
    _, api, _, _ = modules
    client = _bill_client(api, [{"year": 2026, "month": 3, "ky": 1, "kwh": 120.0, "start": "2026-03-01", "end": "2026-03-31"}])
    data = asyncio.run(_update_coordinator(modules, client)._async_update_data())
    bill = data["meters"]["PB000001"]["bills"][0]
    assert bill["total_kwh"] == 120.0
    assert bill["period_end"] == "2026-03-31"
    assert data["meters"]["PB000001"]["monthly_history"] == data["meters"]["PB000001"]["bills"]


def test_update_keeps_bills_when_readings_fail(modules) -> None:
    _, api, _, _ = modules
    client = _bill_client(api, api.EvnApiError("HTTP 500", status=500))
    data = asyncio.run(_update_coordinator(modules, client)._async_update_data())
    assert data["partial_errors"] == {}
    bill = data["meters"]["PB000001"]["bills"][0]
    assert bill["total_kwh"] is None
    assert bill["total_amount"] == 300000


class _TwoCodeClient:
    """Stub client for two codes; a code in `failing_overview` fails its live call only."""

    def __init__(self, bills_by_code, stamps, failing_overview=(), readings_by_code=None, cached_bills=()):
        self.bills_by_code, self.stamps, self.failing_overview = bills_by_code, stamps, set(failing_overview)
        self.cached_bills = set(cached_bills)
        self.readings_by_code = readings_by_code or {}
        self.last_shapes, self.linked_customer_meter_points = {}, {}
        self.bills_calls: list[str] = []

    async def async_overview(self, code):
        if code in self.failing_overview:
            raise self.api.EvnApiError("HTTP 500", status=500)
        return {"customer_code": code, "current_month_consumption": 1.0, "current_month_amount": 1}

    async def async_bills(self, code):
        self.bills_calls.append(code)
        return [dict(bill) for bill in self.bills_by_code[code]]

    async def async_bills_with_source(self, code):
        return await self.async_bills(code), code not in self.cached_bills

    async def async_monthly_readings(self, code):
        return list(self.readings_by_code.get(code, []))

    def history_fetched_at(self, code):
        return self.stamps.get(code, "")

    def cached_history(self, code):
        if code not in self.bills_by_code:
            return None
        return [dict(bill) for bill in self.bills_by_code[code]], [], self.stamps.get(code, "")


def _two_code_coordinator(modules, client):
    _, api, _, coordinator = modules
    client.api = api
    entry = types.SimpleNamespace(
        data={
            "primary_customer_code": "PB000001",
            "linked_customers": {"PB000001": "PB000001009", "PB000002": "PB000002009"},
        },
        options={},
    )
    instance = object.__new__(coordinator.EvnDataUpdateCoordinator)
    instance.config_entry = entry
    instance._update_lock = asyncio.Lock()
    instance._client = client
    instance._persist_changed_tokens = lambda: None
    return instance


def _march_bill(kwh, amount):
    return {
        "period": "Tháng 3/2026", "total_kwh": kwh, "total_amount": amount, "is_paid": True, "issue_date": "",
        "KY": 1, "THANG": 3, "NAM": 2026, "period_start": "", "period_end": "", "calculated_amount": None,
    }


def test_update_exposes_history_fetched_at_per_code_and_the_oldest_for_the_aggregate(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(100.0, 200)], "PB000002": [_march_bill(50.0, 100)]},
        {"PB000001": "2026-03-15T10:00:00", "PB000002": "2026-03-14T08:00:00"},
    )
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    assert data["meters"]["PB000001"]["history_fetched_at"] == "2026-03-15T10:00:00"
    assert data["meters"]["PB000002"]["history_fetched_at"] == "2026-03-14T08:00:00"
    assert data["aggregate"]["history_fetched_at"] == "2026-03-14T08:00:00"


def test_aggregate_history_keeps_a_code_whose_live_overview_failed(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(100.0, 200)], "PB000002": [_march_bill(50.0, 100)]},
        {"PB000001": "2026-03-15T10:00:00", "PB000002": "2026-03-14T08:00:00"},
        failing_overview=["PB000002"],
    )
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    assert data["partial_errors"] == {"PB000002": "api_error"}
    assert "PB000002" not in data["meters"]
    row = data["aggregate"]["bills"][0]
    assert (row["total_kwh"], row["total_amount"]) == (150.0, 300)
    assert data["aggregate"]["history_fetched_at"] == "2026-03-14T08:00:00"
    assert data["aggregate"]["successful_customer_codes"] == ["PB000001"]
    assert client.bills_calls == ["PB000001"]


def test_failed_overview_without_any_last_good_history_adds_nothing_to_the_aggregate(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(100.0, 200)]}, {"PB000001": "2026-03-15T10:00:00"}, failing_overview=["PB000002"],
    )
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    assert data["aggregate"]["bills"][0]["total_amount"] == 200
    assert data["aggregate"]["history_fetched_at"] == "2026-03-15T10:00:00"


def test_failed_overview_does_not_affect_live_values_of_the_aggregate(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(100.0, 200)], "PB000002": [_march_bill(50.0, 100)]},
        {"PB000001": "a", "PB000002": "b"},
        failing_overview=["PB000002"],
    )
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    assert data["aggregate"]["current_month_consumption"] == 1.0
    assert data["aggregate"]["is_partial"] is True


def _march_reading(kwh):
    return {"year": 2026, "month": 3, "ky": 1, "kwh": kwh, "start": "2026-03-01", "end": "2026-03-31"}


def test_update_prices_the_current_month_from_the_code_own_bills(modules) -> None:
    """Bills that contradict the tier model make the estimate use the code's effective price."""
    _, api, _, _ = modules

    def bill(month, amount):
        return {
            "period": f"Tháng {month}/2026", "total_kwh": None, "total_amount": amount, "is_paid": True,
            "issue_date": "", "KY": 1, "THANG": month, "NAM": 2026, "period_start": "", "period_end": "",
            "calculated_amount": None,
        }

    def reading(month, kwh):
        end = {1: "2026-01-31", 2: "2026-02-28", 3: "2026-03-31"}[month]
        return {"year": 2026, "month": month, "ky": 1, "kwh": kwh, "start": f"2026-{month:02d}-01", "end": end}

    async def overview(_code):
        return {
            "customer_code": "PB000001", "current_month_consumption": 120.0, "current_month_amount": 1,
            "month_start": "2026-04-01", "month_end": "2026-04-30",
        }

    async def bills(_code):
        return [bill(1, 250000), bill(2, 500000), bill(3, 750000)]

    async def readings(_code):
        return [reading(1, 100.0), reading(2, 200.0), reading(3, 300.0)]

    async def bills_with_source(code):
        return await bills(code), True

    client = types.SimpleNamespace(
        async_overview=overview, async_bills=bills, async_bills_with_source=bills_with_source,
        async_monthly_readings=readings, last_shapes={}, linked_customer_meter_points={},
        history_fetched_at=lambda _code: "", cached_history=lambda _code: None,
    )
    data = asyncio.run(_update_coordinator(modules, client)._async_update_data())
    meter = data["meters"]["PB000001"]
    assert meter["current_month_amount"] == 300000
    assert (meter["tariff_verified"], meter["estimate_method"]) == (False, "effective_price")


def test_aggregate_estimate_sums_the_per_code_estimates_and_flags_an_unverified_code(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {},
        readings_by_code={"PB000001": [_march_reading(100.0)]},
    )
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    aggregate = data["aggregate"]
    assert data["meters"]["PB000001"]["tariff_verified"] is False
    assert data["meters"]["PB000002"]["tariff_verified"] is None
    assert aggregate["tariff_verified"] is False
    assert aggregate["estimate_method"] == "effective_price"
    assert aggregate["current_month_amount"] == sum(m["current_month_amount"] for m in data["meters"].values())


def test_update_hands_live_rows_and_the_selection_to_the_daily_history(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {},
    )
    instance = _two_code_coordinator(modules, client)
    instance.config_entry.options = {"customer_aliases": {"PB000001": "Nhà chính"}}
    calls: list[dict] = []

    class History:
        async def async_update(self, **kwargs):
            calls.append(kwargs)

    instance._history = History()
    instance.data = None
    asyncio.run(instance._async_update_data())
    assert calls[0]["codes"] == ["PB000001", "PB000002"]
    assert calls[0]["selected"] == ["PB000001", "PB000002"]
    assert calls[0]["aliases"] == {"PB000001": "Nhà chính"}
    assert set(calls[0]["meters"]) == {"PB000001", "PB000002"}
    assert calls[0]["allow_backfill"] is False
    instance.data = {"meters": {}}
    asyncio.run(instance._async_update_data())
    assert calls[1]["allow_backfill"] is True


def test_a_failing_daily_history_never_fails_the_update(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)

    class History:
        async def async_update(self, **_kwargs):
            raise RuntimeError("store unavailable")

    instance._history = History()
    instance.data = None
    data = asyncio.run(instance._async_update_data())
    assert set(data["meters"]) == {"PB000001", "PB000002"}


def test_coordinator_exposes_the_backfill_status_masked_later_by_diagnostics(modules) -> None:
    instance = _two_code_coordinator(modules, _TwoCodeClient({}, {}))
    assert instance.backfill_status == {}
    instance._history = types.SimpleNamespace(backfill_status=lambda: {"PB000001": {"earliest": "2024-01-01", "done": True}})
    assert instance.backfill_status == {"PB000001": {"earliest": "2024-01-01", "done": True}}


def test_tokens_are_persisted_again_after_the_daily_history_step(modules) -> None:
    """The backfill can switch customer or refresh the session; those tokens must not wait a cycle."""
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)
    events: list[str] = []
    instance._persist_changed_tokens = lambda: events.append("persist")

    class History:
        async def async_update(self, **_kwargs):
            events.append("history")

    instance._history = History()
    instance.data = None
    asyncio.run(instance._async_update_data())
    assert events == ["persist", "history", "persist"]


class _WindowHistory:
    """The real window rules over a fixed store, so the coordinator wiring is what is under test."""

    def __init__(self, store_days, today):
        self.store_days, self._today = store_days, today
        self.available, self.events = True, []

    def today(self):
        return self._today

    def compose(self, code, live_rows):
        daily_store = sys.modules[f"{PACKAGE}.daily_store"]
        return daily_store.compose_window(self.store_days[code], live_rows, self._today, 31)

    async def async_update(self, **_kwargs):
        return []


def test_update_composes_the_rolling_window_before_the_aggregate_is_built(modules) -> None:
    from datetime import date

    client = _TwoCodeClient(
        {"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {},
    )
    instance = _two_code_coordinator(modules, client)
    instance._history = _WindowHistory({
        "PB000001": {"2026-09-29": 5.0, "2026-09-30": 4.0},
        "PB000002": {"2026-09-29": 1.0},
    }, date(2026, 10, 1))
    instance.data = None
    data = asyncio.run(instance._async_update_data())
    first, second = data["meters"]["PB000001"], data["meters"]["PB000002"]
    assert [row["date"] for row in first["daily_history"]] == ["2026-09-29", "2026-09-30"]
    assert (first["today_consumption"], first["yesterday_consumption"]) == (0.0, 4.0)
    assert second["yesterday_consumption"] is None, "30/09 is not published for this code: unknown, not 0"
    assert [(row["date"], row["consumption"]) for row in data["aggregate"]["daily_history"]] == [
        ("2026-09-29", 6.0), ("2026-09-30", 4.0),
    ]
    assert data["aggregate"]["yesterday_consumption"] is None


def test_a_failing_window_composition_leaves_the_live_rows_in_place(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)

    class History(_WindowHistory):
        def compose(self, code, live_rows):
            raise RuntimeError("boom")

    from datetime import date

    instance._history = History({}, date(2026, 10, 1))
    instance.data = None
    data = asyncio.run(instance._async_update_data())
    assert set(data["meters"]) == {"PB000001", "PB000002"}


class _BillHistory(_WindowHistory):
    """Records what the coordinator hands over and returns scripted events; annotates like the real one."""

    def __init__(self, events=()):
        super().__init__({"PB000001": {}, "PB000002": {}}, __import__("datetime").date(2026, 10, 2))
        self.scripted, self.calls, self.log = list(events), [], []

    async def async_update(self, **kwargs):
        self.calls.append(kwargs)
        self.log.append("history")
        return list(self.scripted)

    def annotate_bills(self, code, bills, threshold_kwh):
        return [{**bill, "year": bill["NAM"], "month": bill["THANG"], "ky": bill["KY"], "reconcile_status": "match"} for bill in bills]


def _bus(log):
    return types.SimpleNamespace(async_fire=lambda name, data: log.append(("fire", name, data)))


def test_bills_carry_their_provenance_and_the_reconciliation_before_the_aggregate(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {}, cached_bills=["PB000002"],
    )
    instance = _two_code_coordinator(modules, client)
    instance._history = _BillHistory()
    instance.data = None
    data = asyncio.run(instance._async_update_data())
    assert (data["meters"]["PB000001"]["bills_fresh"], data["meters"]["PB000002"]["bills_fresh"]) == (True, False)
    first = data["meters"]["PB000001"]
    assert first["bills"][0]["reconcile_status"] == "match" and first["monthly_history"] is first["bills"]
    assert data["aggregate"]["bills"][0]["reconcile_status"] == "match"
    assert data["aggregate"]["bills"][0]["year"] == 2026


def test_a_failed_overview_keeps_its_last_good_bills_annotated_in_the_aggregate(modules) -> None:
    client = _TwoCodeClient(
        {"PB000001": [_march_bill(100.0, 200)], "PB000002": [_march_bill(50.0, 100)]}, {}, failing_overview=["PB000002"],
    )
    instance = _two_code_coordinator(modules, client)
    instance._history = _BillHistory()
    instance.data = None
    data = asyncio.run(instance._async_update_data())
    assert data["aggregate"]["bills"][0]["total_amount"] == 300 and data["aggregate"]["bills"][0]["month"] == 3


def test_events_are_fired_on_the_bus_only_after_the_history_step_returned(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)
    history = _BillHistory(events=[{"bill_id": "0123456789ab", "reason": "new"}])
    instance._history = history
    instance.hass = types.SimpleNamespace(bus=_bus(history.log))
    instance.data = None
    asyncio.run(instance._async_update_data())
    assert history.log == ["history", ("fire", "evn_vietnam_bill", {"bill_id": "0123456789ab", "reason": "new"})]


def test_a_bus_error_never_fails_the_update(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)
    instance._history = _BillHistory(events=[{"bill_id": "0123456789ab"}])

    def boom(_name, _data):
        raise RuntimeError("bus closed")

    instance.hass = types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=boom))
    instance.data = None
    assert set(asyncio.run(instance._async_update_data())["meters"]) == {"PB000001", "PB000002"}


def test_the_threshold_option_reaches_the_history_and_a_bad_value_falls_back(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    instance = _two_code_coordinator(modules, client)
    history = _BillHistory()
    instance._history = history
    instance.data = None
    asyncio.run(instance._async_update_data())
    assert history.calls[-1]["threshold_kwh"] == 1.0
    for option, expected in ((2.5, 2.5), ("3", 3.0), ("x", 1.0), (-4, 1.0), (500, 1.0), (None, 1.0)):
        instance.config_entry.options = {"reconcile_threshold_kwh": option}
        asyncio.run(instance._async_update_data())
        assert history.calls[-1]["threshold_kwh"] == expected


def test_without_a_history_the_bill_rows_still_get_the_canonical_period(modules) -> None:
    client = _TwoCodeClient({"PB000001": [_march_bill(None, 200)], "PB000002": [_march_bill(None, 100)]}, {})
    data = asyncio.run(_two_code_coordinator(modules, client)._async_update_data())
    row = data["meters"]["PB000001"]["bills"][0]
    assert (row["year"], row["month"], row["ky"], row["reconcile_status"]) == (2026, 3, 1, None)


def test_the_options_flow_offers_the_threshold_with_a_default_and_a_range(modules, monkeypatch) -> None:
    _, _, config_flow, _ = modules
    recorded = []
    monkeypatch.setattr(config_flow.vol, "Range", lambda **kwargs: recorded.append(kwargs) or (lambda value: value))
    monkeypatch.setattr(config_flow.vol, "Optional", lambda value, **_kwargs: value, raising=False)
    flow = config_flow.EvnVietnamOptionsFlow()
    flow.async_show_form = lambda **kwargs: kwargs
    flow.async_create_entry = lambda **kwargs: kwargs
    flow.config_entry = types.SimpleNamespace(
        data={"primary_customer_code": "PB000001", "linked_customers": {"PB000001": "PB000001009"}}, options={},
    )
    form = asyncio.run(flow.async_step_init(None))
    assert "reconcile_threshold_kwh" in form["data_schema"]
    assert {"min": 0, "max": 100} in recorded
    flow.config_entry.options = {"reconcile_threshold_kwh": 2.0}
    asyncio.run(flow.async_step_init(None))
    result = asyncio.run(flow.async_step_init({
        "customer_codes": "", "selected_customer_codes": "PB000001", "scan_interval": 30, "reconcile_threshold_kwh": "1.5",
    }))
    assert result["step_id"] == "aliases" and flow._pending["reconcile_threshold_kwh"] == 1.5
    saved = asyncio.run(flow.async_step_aliases({}))
    assert saved["data"]["reconcile_threshold_kwh"] == 1.5
