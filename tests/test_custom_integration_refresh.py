"""TDD contracts for EVN token refresh and one silent password login."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sys
import time
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_refresh_test"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api_module():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    sys.modules.setdefault("aiohttp", types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ContentTypeError=ValueError,
    ))
    homeassistant = types.ModuleType("homeassistant")
    homeassistant_util = types.ModuleType("homeassistant.util")
    homeassistant_dt = types.ModuleType("homeassistant.util.dt")
    homeassistant_dt.now = lambda: __import__("datetime").datetime.now()
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.util", homeassistant_util)
    sys.modules.setdefault("homeassistant.util.dt", homeassistant_dt)
    _load_module("const")
    _load_module("calculation")
    _load_module("models")
    return _load_module("api")


def _jwt(expiry: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _client(api_module, *, expires_in: int = 3600):
    state = api_module.SessionState(
        "user", _jwt(int(time.time()) + expires_in), "refresh", "device-1", "PB000001", "PB000001",
    )
    return api_module.EvnClient(object(), state, {"PB000001": "PB000001009"}, password="secret-pass")


def test_proactive_refresh_uses_no_bearer_and_persisted_device_id(api_module, monkeypatch) -> None:
    client = _client(api_module, expires_in=119)
    calls: list[tuple[str, str, tuple[str, ...], bool, dict[str, object] | None]] = []

    async def request(_session, method, url, headers, body=None):
        calls.append((method, url.rsplit("/", 2)[-2:], tuple(headers), "Authorization" in headers, body))
        if url.endswith("/auth/refresh"):
            return {"data": {"token": _jwt(int(time.time()) + 3600), "refreshToken": "refresh-2"}}
        return {"data": "ok"}

    monkeypatch.setattr(api_module.EvnClient, "_request_raw", staticmethod(request))
    assert asyncio.run(client._async_request("GET", "https://example.invalid/data")) == {"data": "ok"}
    refresh = calls[0]
    assert refresh[0] == "POST"
    assert refresh[2] == ("Content-Type", "User-Agent", "Accept")
    assert not refresh[3]
    assert refresh[4] == {"refreshToken": "refresh", "deviceId": "device-1"}


@pytest.mark.parametrize("refresh_error", ["401", "417"])
def test_dead_refresh_silently_logs_in_once_reuses_device_and_retries(api_module, monkeypatch, refresh_error) -> None:
    client = _client(api_module)
    calls: list[tuple[str, str, bool]] = []

    async def request(_session, method, url, headers, body=None):
        calls.append((method, url.rsplit("/", 2)[-2:], "Authorization" in headers))
        if url.endswith("/data") and len([call for call in calls if call[1][-1] == "data"]) == 1:
            raise api_module.EvnAuthenticationError("expired")
        if url.endswith("/auth/refresh"):
            if refresh_error == "401":
                raise api_module.EvnAuthenticationError("expired")
            raise api_module.EvnApiError("HTTP 417", status=417)
        if url.endswith("/auth/login"):
            assert body["username"] == "user"
            assert body["password"] == "secret-pass"
            assert body["deviceInfo"]["deviceId"] == "device-1"
            return {"data": {"token": _jwt(int(time.time()) + 3600), "refreshToken": "refresh-2", "maKhachHang": "PB000001"}}
        return {"data": "ok"}

    monkeypatch.setattr(api_module.EvnClient, "_request_raw", staticmethod(request))
    assert asyncio.run(client._async_request("GET", "https://example.invalid/data")) == {"data": "ok"}
    assert [call[1][-1] for call in calls] == ["data", "refresh", "login", "data"]
    assert client.linked_customer_meter_points == {"PB000001": "PB000001009"}


@pytest.mark.parametrize("refresh_error", ["server", "timeout"])
def test_transient_refresh_errors_do_not_become_auth_errors(api_module, monkeypatch, refresh_error) -> None:
    client = _client(api_module)

    async def request(*_args, **_kwargs):
        if refresh_error == "timeout":
            raise asyncio.TimeoutError
        raise api_module.EvnApiError("HTTP 500")

    monkeypatch.setattr(api_module.EvnClient, "_request_raw", staticmethod(request))
    with pytest.raises(api_module.EvnApiError):
        asyncio.run(client._async_refresh())


def test_bad_password_after_dead_refresh_becomes_authentication_error(api_module, monkeypatch) -> None:
    client = _client(api_module)

    async def request(_session, _method, url, _headers, _body=None):
        if url.endswith("/data"):
            raise api_module.EvnAuthenticationError("expired")
        if url.endswith("/auth/refresh"):
            raise api_module.EvnAuthenticationError("expired")
        raise api_module.EvnAuthenticationError("bad password")

    monkeypatch.setattr(api_module.EvnClient, "_request_raw", staticmethod(request))
    with pytest.raises(api_module.EvnAuthenticationError):
        asyncio.run(client._async_request("GET", "https://example.invalid/data"))


def test_transient_silent_login_error_after_dead_refresh_stays_api_error(api_module, monkeypatch) -> None:
    """A temporary login outage after a dead refresh must not trigger reauth."""
    client = _client(api_module)

    async def request(_session, _method, url, _headers, _body=None):
        if url.endswith("/data"):
            raise api_module.EvnAuthenticationError("expired")
        if url.endswith("/auth/refresh"):
            raise api_module.EvnAuthenticationError("expired")
        raise api_module.EvnApiError("HTTP 500", status=500)

    monkeypatch.setattr(api_module.EvnClient, "_request_raw", staticmethod(request))
    with pytest.raises(api_module.EvnApiError):
        asyncio.run(client._async_request("GET", "https://example.invalid/data"))


def test_fresh_jwt_does_not_need_keepalive_refresh(api_module) -> None:
    """Keepalive must not hit EVN while the access token is still valid."""
    client = _client(api_module, expires_in=15 * 60)
    assert client._needs_proactive_refresh() is False


def test_card_module_url_is_a_frontend_extra_module_path() -> None:
    """The Lovelace card is loaded as extra_module_url, not YAML resources."""
    const = _load_module("const")
    assert const.CARD_MODULE_URL == "/evn_vietnam/evn-vietnam-energy-card.js"
    assert const.SESSION_KEEPALIVE_INTERVAL.total_seconds() == 8 * 60
