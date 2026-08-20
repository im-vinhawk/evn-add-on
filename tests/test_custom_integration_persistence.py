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
