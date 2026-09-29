"""Redacted diagnostics for the EVN Vietnam integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.redact import async_redact_data

from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, DOMAIN

_TO_REDACT = {"username", "password", CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN}


async def async_get_config_entry_diagnostics(hass: Any, config_entry: Any) -> dict[str, Any]:
    """Return config-entry metadata and raw-row shapes without credentials, tokens or values."""
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    return {
        "data": async_redact_data(dict(config_entry.data), _TO_REDACT),
        "shapes": dict(coordinator.shapes) if coordinator else {},
    }
