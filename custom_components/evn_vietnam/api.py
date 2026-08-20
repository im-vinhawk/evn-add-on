"""Async client for the EVN CSKH Android-app API contract."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import base64
import json
import logging
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import aiohttp
from homeassistant.util import dt as dt_util

from .calculation import as_float, calculate_tier_cost, normalize_bills, normalize_daily
from .const import DAILY_HISTORY_DAYS, DEFAULT_TIMEOUT, NATIONAL_BASE_URL, REGIONAL_GATEWAYS
from .models import (
    SessionState,
    extract_linked_customer_meter_points,
    merge_linked_customer_meter_points,
    normalize_customer_code,
    normalize_linked_customer_meter_points,
    normalize_meter_point,
)

_LOGGER = logging.getLogger(__name__)


class EvnError(Exception):
    """Base EVN integration error."""


class EvnAuthenticationError(EvnError):
    """Authentication expired or credentials were rejected."""


class EvnApiError(EvnError):
    """EVN returned an invalid request or server response."""


class EvnCustomerSwitchError(EvnApiError):
    """EVN could not switch to a linked customer without exposing its detail."""


class EvnMeterPointError(EvnApiError):
    """EVN did not provide a verified meter point for a customer."""


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode untrusted JWT metadata solely to obtain expiry/customer claims."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}


def _error_message(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error_description", "detail", "error"):
            value = payload.get(key)
            if value and str(value).lower() not in {"none", "null"}:
                return str(value)
    return f"HTTP {status}"


def _extract_rows(
    payload: Any,
    envelope_keys: tuple[str, ...],
    root_row_fields: tuple[tuple[str, ...], ...],
) -> list[Any]:
    """Unwrap EVN list envelopes and retain only a plausible root-level row."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    saw_envelope = False
    for key in envelope_keys:
        if key not in payload:
            continue
        saw_envelope = True
        candidate = payload[key]
        if not candidate:
            continue
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            return [candidate]
        return []
    if saw_envelope:
        return []
    return [payload] if all(any(key in payload for key in fields) for fields in root_row_fields) else []


class EvnClient:
    """National-auth plus regional-meter-query EVN client.

    EVN's app API issues a customer-specific JWT.  The client changes token for
    each configured, already-linked customer code before using that code's
    regional gateway.  Tokens are supplied by Home Assistant, never files.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        state: SessionState,
        linked_customer_meter_points: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self.state = state
        self._meter_points = {
            code: meter_point
            for code, meter_point in normalize_linked_customer_meter_points(linked_customer_meter_points).items()
            if meter_point
        }

    @staticmethod
    def new_device_id() -> str:
        """Create a stable device id for a new config entry."""
        return str(uuid4())

    @classmethod
    async def _async_login_with_roster(
        cls,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> tuple[SessionState, dict[str, str]]:
        """Authenticate and extract only privacy-safe roster data from login."""
        device_id = cls.new_device_id()
        headers = cls._headers(None)
        payload = {
            "username": username.strip(),
            "password": password,
            "deviceInfo": {
                "deviceId": device_id,
                "deviceName": "Home Assistant",
                "deviceType": "ANDROID",
                "osVersion": "14",
                "appVersion": "1.1.260814",
            },
        }
        response = await cls._request_raw(session, "POST", f"{NATIONAL_BASE_URL}/auth/login", headers, payload)
        content = response.get("data", response) if isinstance(response, dict) else {}
        token = content.get("token") or content.get("accessToken") or content.get("access_token")
        if not token:
            raise EvnAuthenticationError("EVN did not return an access token")
        claims = _jwt_claims(str(token))
        customer_code = normalize_customer_code(
            claims.get("makhachhang")
            or claims.get("maKhachHang")
            or content.get("maKhachHang")
            or (username if username.strip().upper().startswith("P") else "")
        )
        if not customer_code:
            raise EvnAuthenticationError("EVN did not return a customer code")
        state = SessionState(
            username=username.strip(), access_token=str(token),
            refresh_token=content.get("refreshToken") or content.get("refresh_token"),
            device_id=device_id, primary_customer_code=customer_code, current_customer_code=customer_code,
        )
        return state, extract_linked_customer_meter_points(response)

    @classmethod
    async def async_login(cls, session: aiohttp.ClientSession, username: str, password: str) -> SessionState:
        """Authenticate once. Password is intentionally not returned or persisted."""
        state, _ = await cls._async_login_with_roster(session, username, password)
        return state

    @classmethod
    async def async_login_and_discover(
        cls,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        previous_roster: dict[str, str] | None = None,
    ) -> tuple[SessionState, dict[str, str]]:
        """Log in, then merge mobile-app linked customers into a safe roster."""
        state, login_roster = await cls._async_login_with_roster(session, username, password)
        client = cls(session, state, previous_roster)
        discovered = await client.async_discover_linked_customers()
        roster = merge_linked_customer_meter_points(previous_roster, login_roster)
        roster = merge_linked_customer_meter_points(roster, discovered)
        return state, merge_linked_customer_meter_points(roster, {state.primary_customer_code: ""})

    @property
    def linked_customer_meter_points(self) -> dict[str, str]:
        """Return the cache in a config-entry safe form."""
        return dict(self._meter_points)

    async def async_discover_linked_customers(self) -> dict[str, str]:
        """Probe the mobile-app roster endpoints without retaining profile data."""
        discovered: dict[str, str] = {}
        for endpoint, body in (
            ("customer/suggest/khachhang", {"keyword": self.state.username}),
            ("customer/list/share", {}),
            ("user/info", {}),
        ):
            try:
                payload = await self._async_request("POST", f"{NATIONAL_BASE_URL}/{endpoint}", body)
            except EvnAuthenticationError:
                raise
            except EvnApiError:
                # A disabled endpoint must not clear a previously stored roster.
                _LOGGER.debug("EVN linked-customer discovery endpoint unavailable")
                continue
            discovered = merge_linked_customer_meter_points(
                discovered, extract_linked_customer_meter_points(payload)
            )
        return discovered

    @staticmethod
    def _headers(token: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": "EVN-CSKH/1.1.260814 (Linux; Android 14)",
            "Accept": "application/json, text/plain, */*",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    async def _request_raw(
        session: aiohttp.ClientSession, method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with session.request(method, url, headers=headers, json=json_body, timeout=DEFAULT_TIMEOUT) as response:
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    payload = None
                if response.status in (200, 201):
                    return payload or {}
                if response.status == 401:
                    raise EvnAuthenticationError("EVN session expired")
                raise EvnApiError(_error_message(payload, response.status))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise EvnApiError("Cannot connect to EVN") from err

    async def _async_refresh(self) -> None:
        if not self.state.refresh_token:
            raise EvnAuthenticationError("EVN session expired")
        body = {"refreshToken": self.state.refresh_token, "deviceId": self.state.device_id}
        try:
            payload = await self._request_raw(
                self._session,
                "POST",
                f"{NATIONAL_BASE_URL}/auth/refresh",
                self._headers(self.state.access_token),
                body,
            )
        except EvnApiError as err:
            # EVN uses non-401 statuses (observed 417) for expired refresh
            # tokens. Keep upstream detail out of HA entity attributes/logs and
            # let the coordinator trigger its normal config-entry reauth flow.
            raise EvnAuthenticationError("EVN session expired; reauthenticate this integration") from err
        content = payload.get("data", payload) if isinstance(payload, dict) else {}
        token = content.get("token") or content.get("accessToken")
        if not token:
            raise EvnAuthenticationError("EVN refresh did not return an access token")
        self.state.access_token = str(token)
        self.state.refresh_token = content.get("refreshToken") or content.get("refresh_token") or self.state.refresh_token

    async def _async_request(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        try:
            return await self._request_raw(self._session, method, url, self._headers(self.state.access_token), body)
        except EvnAuthenticationError:
            await self._async_refresh()
            return await self._request_raw(self._session, method, url, self._headers(self.state.access_token), body)

    async def _async_switch_customer(self, customer_code: str) -> None:
        """Switch only real codes; an aggregate is computed locally, never sent."""
        customer_code = normalize_customer_code(customer_code)
        if not customer_code or customer_code == self.state.current_customer_code:
            return
        try:
            payload = await self._async_request("GET", f"{NATIONAL_BASE_URL}/user/switch/{customer_code}")
        except EvnAuthenticationError:
            raise
        except EvnApiError as err:
            raise EvnCustomerSwitchError("EVN customer switch is unavailable") from err
        content = payload.get("data", payload) if isinstance(payload, dict) else {}
        token = content.get("token") or content.get("accessToken")
        if not token:
            raise EvnCustomerSwitchError("EVN customer switch is unavailable")
        self.state.access_token = str(token)
        self.state.refresh_token = content.get("refreshToken") or content.get("refresh_token") or self.state.refresh_token
        self.state.current_customer_code = customer_code

    @staticmethod
    def _regional_url(customer_code: str, endpoint: str) -> str:
        base = REGIONAL_GATEWAYS.get(customer_code[:2].upper(), REGIONAL_GATEWAYS["PB"])
        return f"{base.rstrip('/')}/api/evn/{endpoint}"

    async def _async_meter_point(self, customer_code: str) -> str:
        """Return the EVN-provided meter point for one configured customer."""
        customer_code = normalize_customer_code(customer_code)
        if not customer_code:
            raise EvnMeterPointError("EVN meter point is unavailable for configured customer")
        await self._async_switch_customer(customer_code)
        if customer_code in self._meter_points:
            return self._meter_points[customer_code]
        try:
            query = urlencode({"MA_KHANG": customer_code, "maKhachHang": customer_code})
            payload = await self._async_request(
                "GET", self._regional_url(customer_code, f"customers/diemdo?{query}")
            )
            rows: list[Any]
            if isinstance(payload, list):
                rows = payload
            elif isinstance(payload, dict):
                candidate = payload.get("data") or payload.get("items") or payload.get("danhSachDiemDo")
                if isinstance(candidate, list):
                    rows = candidate
                elif isinstance(candidate, dict):
                    rows = [candidate]
                elif any(key in payload for key in ("MA_DDO", "maDdo")):
                    rows = [payload]
                else:
                    rows = []
            else:
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                point = normalize_meter_point(row.get("MA_DDO") or row.get("maDdo"))
                if point:
                    self._meter_points[customer_code] = point
                    return point
        except EvnAuthenticationError:
            raise
        except EvnApiError as err:
            _LOGGER.warning("EVN meter point is unavailable for configured customer")
            raise EvnMeterPointError("EVN meter point is unavailable for configured customer") from err
        raise EvnMeterPointError("EVN did not return a meter point for configured customer")

    async def async_daily(self, customer_code: str, start: date, end: date) -> list[dict[str, Any]]:
        customer_code = customer_code.strip().upper()
        meter_point = await self._async_meter_point(customer_code)
        body = {"MA_KHANG": customer_code, "MA_DDO": meter_point, "TU_NGAY": start.strftime("%d/%m/%Y"), "DEN_NGAY": end.strftime("%d/%m/%Y")}
        payload = await self._async_request("POST", self._regional_url(customer_code, "tracuu/diennangngay"), body)
        rows = _extract_rows(
            payload,
            ("data", "danhSachSanLuong", "danhSachDienNang", "items", "sanLuong"),
            (
                ("NGAY", "NGAY_HTHI", "ngay", "ngayGhi", "date", "ngayDoc"),
                ("DIEN_TTHU", "dienTthu", "sanLuong", "consumption"),
            ),
        )
        return normalize_daily(row for row in rows if isinstance(row, dict))

    async def _async_readings(self, customer_code: str, start: date, end: date, meter_point: str) -> list[dict[str, Any]]:
        body = {"MA_KHANG": customer_code, "MA_DDO": meter_point, "TU_NGAY": start.strftime("%d/%m/%Y"), "DEN_NGAY": end.strftime("%d/%m/%Y")}
        payload = await self._async_request("POST", self._regional_url(customer_code, "tracuu/chisongay"), body)
        return payload if isinstance(payload, list) else payload.get("data", payload.get("items", [])) if isinstance(payload, dict) else []

    async def _async_monthly_fallback(self, customer_code: str, month: int, year: int, meter_point: str) -> float:
        body = {"MA_KHANG": customer_code, "MA_DDO": meter_point, "TU_THANG_NAM": f"01/{year}", "DEN_THANG_NAM": f"{month:02d}/{year}"}
        payload = await self._async_request("POST", self._regional_url(customer_code, "tracuu/diennangthang"), body)
        rows = _extract_rows(
            payload,
            ("data", "danhSachSanLuong", "danhSachDienNang", "items", "sanLuong"),
            (
                ("THANG", "thang"),
                ("DIEN_TTHU", "dienTthu", "sanLuong", "consumption"),
            ),
        )
        for row in rows:
            if isinstance(row, dict) and int(as_float(row.get("THANG", row.get("thang", 0)))) == month:
                return as_float(row.get("DIEN_TTHU", row.get("sanLuong", 0)))
        return 0.0

    async def async_overview(self, customer_code: str) -> dict[str, Any]:
        """Match legacy overview: current-month daily sum, per-meter tier estimate."""
        today = dt_util.now().date()
        start = today.replace(day=1)
        daily = await self.async_daily(customer_code, start, today)
        meter_point = self._meter_points[customer_code]
        month_kwh = round(sum(as_float(row.get("consumption")) for row in daily), 2)
        if month_kwh == 0:
            month_kwh = await self._async_monthly_fallback(customer_code, today.month, today.year, meter_point)
        latest_index, latest_date = None, ""
        try:
            readings = await self._async_readings(customer_code, start, today, meter_point)
            if readings and isinstance(readings[-1], dict):
                latest = readings[-1]
                latest_index = latest.get("CHISO_MOI") or latest.get("CHISO_CUOI")
                latest_date = str(latest.get("NGAY") or latest.get("ngayGhi") or "")
        except EvnApiError:
            pass
        values = {row["date"]: as_float(row["consumption"]) for row in daily}
        today_kwh = values.get(today.isoformat(), 0.0)
        yesterday_kwh = values.get((today - timedelta(days=1)).isoformat(), 0.0)
        return {
            "customer_code": customer_code, "latest_index": latest_index, "latest_date": latest_date,
            "today_consumption": round(today_kwh, 2), "yesterday_consumption": round(yesterday_kwh, 2),
            "current_month_consumption": month_kwh, "current_month_amount": calculate_tier_cost(month_kwh),
            "daily_history": daily[-DAILY_HISTORY_DAYS:],
        }

    async def async_bills(self, customer_code: str) -> list[dict[str, Any]]:
        year = date.today().year
        await self._async_switch_customer(customer_code)
        body = {"MA_KHANG": customer_code, "TU_THANG_NAM": f"01/{year}", "DEN_THANG_NAM": f"12/{year}"}
        payload = await self._async_request("POST", self._regional_url(customer_code, "tracuu/lichsu-hoadon"), body)
        rows = _extract_rows(
            payload,
            ("data", "danhSachHoaDon", "bills", "items"),
            (
                ("THANG", "thang", "month"),
                ("NAM", "nam", "year"),
                ("TONG_TIEN", "totalAmount"),
            ),
        )
        return normalize_bills(row for row in rows if isinstance(row, dict))
