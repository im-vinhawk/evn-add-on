"""Regression tests for EVN's documented meter-point response variants."""

from __future__ import annotations

import asyncio
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_meter_point_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api_module():
    """Load the adapter without requiring a Home Assistant runtime locally."""
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
    _load_module("models")
    return _load_module("api")


@pytest.mark.parametrize("payload", [
    [{"MA_DDO": "PB000001001"}],
    {"data": [{"MA_DDO": "PB000001001"}]},
    {"items": [{"MA_DDO": "PB000001001"}]},
    {"danhSachDiemDo": [{"MA_DDO": "PB000001001"}]},
    {"MA_DDO": "PB000001001"},
])
def test_meter_point_uses_both_contract_parameters_and_normalizes_response(api_module, payload) -> None:
    """A real meter point must be extracted without a fabricated fallback."""
    state = api_module.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api_module.EvnClient(object(), state)
    requested_urls: list[str] = []

    async def switch_customer(_: str) -> None:
        return None

    async def request(_: str, url: str, __=None):
        requested_urls.append(url)
        return payload

    client._async_switch_customer = switch_customer
    client._async_request = request

    assert asyncio.run(client._async_meter_point("PB000001")) == "PB000001001"
    assert requested_urls == [
        "https://api.cskh.evnspc.vn/api-cskh-evn/api/evn/customers/diemdo?MA_KHANG=PB000001&maKhachHang=PB000001"
    ]


def test_failed_refresh_is_an_authentication_error_without_upstream_detail(api_module) -> None:
    """An EVN 417 refresh failure must start HA reauth, not a partial update."""
    state = api_module.SessionState("user", "token", "refresh", "device", "PB000001", "PB000001")
    client = api_module.EvnClient(object(), state)

    async def failed_request(*_args, **_kwargs):
        raise api_module.EvnApiError("HTTP 417: internal upstream detail")

    client._request_raw = failed_request

    with pytest.raises(api_module.EvnAuthenticationError, match="EVN session expired") as raised:
        asyncio.run(client._async_refresh())
    assert "internal upstream detail" not in str(raised.value)
