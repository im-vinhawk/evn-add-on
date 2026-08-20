"""Typed state used by the EVN Vietnam API client."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_CUSTOMER_CODE_KEYS = ("maKhachHang", "MA_KHANG", "customerCode", "makhachhang")
_METER_POINT_KEYS = ("MA_DDO", "maDdo")
_ROSTER_CONTAINER_KEYS = (
    "data",
    "result",
    "items",
    "danhSachKhachHang",
    "danhSachKhachHangLienKet",
    "listCustomer",
    "customers",
)
_EVN_REGION_PREFIXES = "PA|PB|PC|PD|PE|PH|PK|PM|PN|PP|PQ|PT|HN"
_CUSTOMER_CODE_PATTERN = re.compile(rf"^(?:{_EVN_REGION_PREFIXES})[0-9]{{4,}}$")
_METER_POINT_PATTERN = re.compile(rf"^(?:{_EVN_REGION_PREFIXES})[0-9]{{7,}}$")
_SENSOR_UNIQUE_ID_METRICS = (
    "today_consumption",
    "yesterday_consumption",
    "current_month_consumption",
    "current_month_amount",
    "latest_index",
)


def _normalized_ascii_identifier(value: Any) -> str:
    """Return a normalized ASCII candidate without treating it as valid yet."""
    return str(value or "").strip().upper()


def normalize_customer_code(value: Any) -> str:
    """Return a documented EVN customer code or an empty value.

    Customer and meter identifiers are the only roster values retained.  This
    deliberately excludes display fields such as names, addresses and phones.
    """
    identifier = _normalized_ascii_identifier(value)
    return identifier if _CUSTOMER_CODE_PATTERN.fullmatch(identifier) else ""


def normalize_meter_point(value: Any) -> str:
    """Return a documented EVN meter-point code or an empty value."""
    identifier = _normalized_ascii_identifier(value)
    return identifier if _METER_POINT_PATTERN.fullmatch(identifier) else ""


def normalize_linked_customer_meter_points(value: Any) -> dict[str, str]:
    """Normalize a persisted roster without accepting any ancillary fields."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_code, raw_meter_point in value.items():
        code = normalize_customer_code(raw_code)
        if not code or code == "AGGREGATE":
            continue
        meter_point = normalize_meter_point(raw_meter_point)
        normalized[code] = meter_point
    return normalized


def extract_linked_customer_meter_points(payload: Any) -> dict[str, str]:
    """Extract code/meter pairs from EVN's root, data and nested list shapes.

    The parser walks only response envelope/list fields used by the mobile
    client.  It returns a privacy-safe roster and never returns raw response
    objects or profile fields.
    """
    discovered: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        code = normalize_customer_code(next((value[key] for key in _CUSTOMER_CODE_KEYS if value.get(key)), ""))
        if code and code != "AGGREGATE":
            meter_point = normalize_meter_point(next((value[key] for key in _METER_POINT_KEYS if value.get(key)), ""))
            if code not in discovered or meter_point:
                discovered[code] = meter_point
        for key in _ROSTER_CONTAINER_KEYS:
            if key in value:
                visit(value[key])

    visit(payload)
    return discovered


def merge_linked_customer_meter_points(previous: Any, discovered: Any) -> dict[str, str]:
    """Merge a discovery result without losing known codes or verified points."""
    merged = normalize_linked_customer_meter_points(previous)
    for code, meter_point in normalize_linked_customer_meter_points(discovered).items():
        if code not in merged or meter_point:
            merged[code] = meter_point
    return merged


def extract_customer_codes_from_entity_unique_ids(
    entry_id: str,
    unique_ids: Any,
) -> set[str]:
    """Recover only valid customer codes from this entry's own legacy sensors.

    Older integration versions created one stable entity group per configured
    customer.  A config-entry reset could lose the roster while those entities
    remained registered.  This helper makes that migration explicit without
    reading old sessions, names, addresses, or meter-point guesses.
    """
    prefix = f"{entry_id}_"
    recovered: set[str] = set()
    if not isinstance(unique_ids, (list, tuple, set)):
        return recovered
    for raw_unique_id in unique_ids:
        unique_id = str(raw_unique_id or "")
        if not unique_id.startswith(prefix):
            continue
        suffix = unique_id.removeprefix(prefix)
        for metric in _SENSOR_UNIQUE_ID_METRICS:
            metric_suffix = f"_{metric}"
            if not suffix.endswith(metric_suffix):
                continue
            code = normalize_customer_code(suffix.removesuffix(metric_suffix))
            if code:
                recovered.add(code)
            break
    return recovered


@dataclass(slots=True)
class SessionState:
    """The token-only authentication state persisted by Home Assistant."""

    username: str
    access_token: str
    refresh_token: str | None
    device_id: str
    primary_customer_code: str
    current_customer_code: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SessionState":
        return cls(
            username=str(data.get("username") or ""),
            access_token=str(data.get("access_token") or ""),
            refresh_token=data.get("refresh_token") or None,
            device_id=str(data.get("device_id") or ""),
            primary_customer_code=str(data.get("primary_customer_code") or "").upper(),
            current_customer_code=str(data.get("current_customer_code") or data.get("primary_customer_code") or "").upper(),
        )

    def as_dict(self) -> dict[str, str | None]:
        """Return token state suitable for the Home Assistant config entry store."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "device_id": self.device_id,
            "primary_customer_code": self.primary_customer_code,
            "current_customer_code": self.current_customer_code,
        }
