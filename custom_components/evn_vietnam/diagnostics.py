"""Redacted diagnostics for the EVN Vietnam integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.redact import async_redact_data

from .const import DIAGNOSTICS_TO_REDACT, DOMAIN
from .models import anonymize_customer_codes, mask_customer_keys


async def async_get_config_entry_diagnostics(hass: Any, config_entry: Any) -> dict[str, Any]:
    """Return config-entry metadata, raw-row shapes and backfill depth without credentials, customer codes or values."""
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    return {
        "data": async_redact_data(anonymize_customer_codes(config_entry.data), DIAGNOSTICS_TO_REDACT),
        "shapes": dict(coordinator.shapes) if coordinator else {},
        # How deep EVN's daily history goes per code: oldest stored day and whether the backfill finished.
        "backfill": mask_customer_keys(coordinator.backfill_status, config_entry.data) if coordinator else {},
    }
