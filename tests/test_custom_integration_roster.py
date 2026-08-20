"""TDD contracts for the privacy-safe linked EVN customer roster."""

from __future__ import annotations

import asyncio
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_roster_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules():
    """Load pure roster logic and the client without a Home Assistant runtime."""
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    sys.modules.setdefault("aiohttp", types.SimpleNamespace(
        ClientSession=object,
        ClientError=Exception,
        ContentTypeError=ValueError,
    ))
    homeassistant = types.ModuleType("homeassistant")
    homeassistant_util = types.ModuleType("homeassistant.util")
    homeassistant_dt = types.ModuleType("homeassistant.util.dt")
    homeassistant_dt.now = datetime.now
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.util", homeassistant_util)
    sys.modules.setdefault("homeassistant.util.dt", homeassistant_dt)
    _load_module("const")
    _load_module("calculation")
    models = _load_module("models")
    api = _load_module("api")
    return models, api


@pytest.mark.parametrize("payload", [
    {"maKhachHang": "pb000001", "maDdo": "pb000001009", "tenKhachHang": "Private Name"},
    {"data": [{"MA_KHANG": "PB000001", "MA_DDO": "PB000001009", "diaChi": "Private Address"}]},
    {"data": {"danhSachKhachHang": [{"customerCode": "PB000001", "maDdo": "PB000001009", "soDienThoai": "phone-value"}]}},
    {"result": {"customers": [{"makhachhang": "PB000001", "MA_DDO": "PB000001009", "name": "Private Name"}]}},
])
def test_roster_parser_accepts_root_data_and_nested_customer_shapes_without_pii(modules, payload) -> None:
    """Only normalized code and verified meter point are retained from EVN payloads."""
    models, _ = modules

    roster = models.extract_linked_customer_meter_points(payload)

    assert roster == {"PB000001": "PB000001009"}
    persisted = repr(roster)
    for private_value in ("Private Name", "Private Address", "phone-value"):
        assert private_value not in persisted


def test_roster_parser_rejects_unicode_names_and_phone_numbers_in_identifier_fields(modules) -> None:
    """Mislabelled profile values cannot cross the config-entry persistence boundary."""
    models, _ = modules

    assert models.extract_linked_customer_meter_points({
        "data": [
            {"customerCode": "ĐỖVĂNA", "maDdo": "PB000001009"},
            {"customerCode": "0900000000", "maDdo": "0900000000001"},
        ]
    }) == {}


def test_roster_parser_rejects_ascii_display_names_in_identifier_fields(modules) -> None:
    """ASCII-only profile names are not valid EVN customer or meter identifiers."""
    models, _ = modules

    assert models.extract_linked_customer_meter_points({
        "data": [{"customerCode": "PETERSON", "maDdo": "PETERSONMETER"}],
    }) == {}


def test_session_serialization_persists_username_but_never_password(modules) -> None:
    """Silent reauthentication needs the login identifier but never a password dump."""
    models, _ = modules
    state = models.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")

    persisted = state.as_dict()
    assert persisted["username"] == "user"
    assert "password" not in persisted


def test_diagnostics_redacts_credentials_and_tokens(modules) -> None:
    """A config-entry diagnostics dump must never contain login secrets."""
    helpers = types.ModuleType("homeassistant.helpers")
    redact = types.ModuleType("homeassistant.helpers.redact")

    def redact_data(data, keys):
        return {key: "**REDACTED**" if key in keys else value for key, value in data.items()}

    redact.async_redact_data = redact_data
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.redact"] = redact
    diagnostics = _load_module("diagnostics")
    entry = types.SimpleNamespace(data={
        "username": "login-value", "password": "secret-pass", "access_token": "access-value", "refresh_token": "refresh-value",
    })

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(None, entry))
    rendered = repr(result)
    for secret in ("login-value", "secret-pass", "access-value", "refresh-value"):
        assert secret not in rendered


def test_roster_merge_keeps_previous_codes_when_discovery_is_empty(modules) -> None:
    """Transient gateway failures cannot delete linked or manually configured customers."""
    models, _ = modules

    assert models.merge_linked_customer_meter_points(
        {"pb000001": "PB000001009", "PB000002": ""},
        {},
    ) == {"PB000001": "PB000001009", "PB000002": ""}


def test_registry_migration_recovers_only_this_entrys_valid_legacy_customer_codes(modules) -> None:
    """Stale HA sensors can restore a lost roster without importing other entries."""
    models, _ = modules

    recovered = models.extract_customer_codes_from_entity_unique_ids(
        "entry-a",
        [
            "entry-a_PB000001_current_month_consumption",
            "entry-a_PB000002_latest_index",
            "entry-a_aggregate_current_month_consumption",
            "entry-a_PrivateName_current_month_consumption",
            "entry-b_PB000003_current_month_consumption",
            "entry-a_PB000004_unknown_metric",
        ],
    )

    assert recovered == {"PB000001", "PB000002"}


def test_selected_customer_codes_preserves_legacy_full_roster_and_validates_saved_scope(modules) -> None:
    """The aggregate scope is persisted separately and always owns its primary."""
    models, _ = modules
    configured = ["PB000001", "PB000002", "PB000003"]

    assert models.selected_customer_codes(configured, None, "PB000001") == configured
    assert models.selected_customer_codes(
        configured, ["PB000003", "not-a-customer"], "PB000001"
    ) == ["PB000001", "PB000003"]


def test_client_discovers_all_mobile_roster_endpoints_and_skips_failed_probe(modules) -> None:
    """One unavailable optional endpoint must not discard other linked customers."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state)
    calls: list[tuple[str, str, dict[str, str]]] = []

    async def request(method: str, url: str, body: dict[str, str] | None = None):
        calls.append((method, url, body or {}))
        if url.endswith("customer/suggest/khachhang"):
            return {"data": [{"maKhachHang": "PB000002", "maDdo": "PB000002009"}]}
        if url.endswith("customer/list/share"):
            raise api.EvnApiError("unavailable")
        return {"data": {"customers": [{"MA_KHANG": "PB000003"}]}}

    client._async_request = request

    assert asyncio.run(client.async_discover_linked_customers()) == {
        "PB000002": "PB000002009",
        "PB000003": "",
    }
    assert [url.removeprefix(f"{api.NATIONAL_BASE_URL}/") for _, url, _ in calls] == [
        "customer/suggest/khachhang",
        "customer/list/share",
        "user/info",
    ]
    assert [body for _, _, body in calls] == [{"keyword": "user"}, {}, {}]


def test_client_discovery_reraises_authentication_error(modules) -> None:
    """An expired token during discovery must start HA reauthentication."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state)

    async def rejected_request(*_args, **_kwargs):
        raise api.EvnAuthenticationError("EVN session expired")

    client._async_request = rejected_request

    with pytest.raises(api.EvnAuthenticationError, match="session expired"):
        asyncio.run(client.async_discover_linked_customers())


def test_login_roster_is_merged_with_previous_and_endpoint_discovery(modules, monkeypatch) -> None:
    """Login response customers are retained even if follow-up discovery is incomplete."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")

    async def login_with_roster(_cls, *_args, **_kwargs):
        return state, {"PB000002": "PB000002009"}

    async def discover(_self):
        return {"PB000003": "PB000003009"}

    monkeypatch.setattr(api.EvnClient, "_async_login_with_roster", classmethod(login_with_roster))
    monkeypatch.setattr(api.EvnClient, "async_discover_linked_customers", discover)

    _, roster = asyncio.run(api.EvnClient.async_login_and_discover(
        object(), "user", "password", {"PB000004": "PB000004009"}
    ))

    assert roster == {
        "PB000004": "PB000004009",
        "PB000002": "PB000002009",
        "PB000003": "PB000003009",
        "PB000001": "",
    }


def test_verified_roster_meter_point_bypasses_remote_lookup_for_fresh_client(modules) -> None:
    """Persisted meter points work after coordinator/client recreation."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")

    for _ in range(2):
        client = api.EvnClient(object(), state, {"PB000001": "PB000001009"})

        async def unexpected_request(*_args, **_kwargs):
            raise AssertionError("known meter point must not be queried remotely")

        client._async_request = unexpected_request
        assert asyncio.run(client._async_meter_point("PB000001")) == "PB000001009"


def test_cached_meter_point_still_switches_to_the_requested_customer(modules) -> None:
    """A meter cache skips only meter discovery, never the customer JWT switch."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000002")
    client = api.EvnClient(object(), state, {"PB000001": "PB000001009"})
    events: list[str] = []

    async def switch_customer(code: str) -> None:
        events.append(f"switch:{code}")

    async def request(*_args, **_kwargs):
        events.append("daily")
        return {"data": []}

    client._async_switch_customer = switch_customer
    client._async_request = request

    assert asyncio.run(client.async_daily("PB000001", datetime(2026, 8, 1).date(), datetime(2026, 8, 16).date())) == []
    assert events == ["switch:PB000001", "daily"]


def test_missing_meter_point_never_fabricates_customer_code_suffix(modules) -> None:
    """A code alone is not evidence for a corresponding EVN meter point."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state)

    async def switch_customer(_: str) -> None:
        return None

    async def request(*_args, **_kwargs):
        return {"data": [{"MA_KHANG": "PB000001"}]}

    client._async_switch_customer = switch_customer
    client._async_request = request

    with pytest.raises(api.EvnApiError, match="meter point"):
        asyncio.run(client._async_meter_point("PB000001"))
    assert "PB000001001" not in client._meter_points.values()


def test_customer_switch_failure_is_not_reported_as_a_meter_point_failure(modules) -> None:
    """The coordinator can expose a safe partial cause without upstream details."""
    _, api = modules
    state = api.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api.EvnClient(object(), state)

    async def failed_switch(_: str) -> None:
        raise api.EvnCustomerSwitchError("EVN customer switch is unavailable")

    client._async_switch_customer = failed_switch

    with pytest.raises(api.EvnCustomerSwitchError):
        asyncio.run(client._async_meter_point("PB000002"))
