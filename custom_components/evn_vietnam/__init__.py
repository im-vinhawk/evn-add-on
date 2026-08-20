"""Home Assistant integration entry point for EVN Vietnam."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CARD_MODULE_URL, CONF_LINKED_CUSTOMERS, DOMAIN
from .coordinator import EvnDataUpdateCoordinator
from .models import (
    extract_customer_codes_from_entity_unique_ids,
    merge_linked_customer_meter_points,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Serve and register the Lovelace card for every dashboard mode."""
    from homeassistant.components.frontend import add_extra_js_url

    await hass.http.async_register_static_paths([
        StaticPathConfig(
            url_path=f"/{DOMAIN}",
            path=str(Path(__file__).parent / "www"),
            cache_headers=False,
        )
    ])
    # YAML lovelace.resources is ignored while the default dashboard is
    # storage-mode. extra_module_url loads the card without a UI resource.
    add_extra_js_url(hass, CARD_MODULE_URL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EVN sensors from a config entry."""
    _restore_roster_from_legacy_entities(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    coordinator = EvnDataUpdateCoordinator(hass, entry)
    entry.async_on_unload(coordinator.async_shutdown)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _restore_roster_from_legacy_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate valid customer codes from this entry's existing HA registry rows.

    This one-way migration restores customer membership lost by earlier config
    restructures.  It deliberately does not infer meter points; the EVN client
    must still verify each point before it is used.
    """
    registry = er.async_get(hass)
    unique_ids = [
        registry_entry.unique_id
        for registry_entry in registry.entities.values()
        if registry_entry.platform == DOMAIN and registry_entry.config_entry_id == entry.entry_id
    ]
    recovered_codes = extract_customer_codes_from_entity_unique_ids(entry.entry_id, unique_ids)
    if not recovered_codes:
        return
    roster = merge_linked_customer_meter_points(
        entry.data.get(CONF_LINKED_CUSTOMERS),
        {code: "" for code in recovered_codes},
    )
    if roster != entry.data.get(CONF_LINKED_CUSTOMERS):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_LINKED_CUSTOMERS: roster},
        )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options add or remove customer codes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and release coordinator state."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
