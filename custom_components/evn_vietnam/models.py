"""Typed state used by the EVN Vietnam API client."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
        """Return data suitable for the Home Assistant config entry store."""
        return asdict(self)
