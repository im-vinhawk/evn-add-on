"""Home Assistant integration entry point for EVN Vietnam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CARD_FILENAME, CARD_MODULE_URL, CONF_LINKED_CUSTOMERS, DOMAIN
from .coordinator import EvnDataUpdateCoordinator
from .models import (
    extract_customer_codes_from_entity_unique_ids,
    merge_linked_customer_meter_points,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Serve and register the Lovelace card for every dashboard mode."""
    from homeassistant.components.frontend import add_extra_js_url

    card_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            url_path=f"/{DOMAIN}",
            path=str(card_dir),
            cache_headers=False,
        )
    ])
    # YAML lovelace.resources is ignored while the default dashboard is
    # storage-mode. extra_module_url loads the card without a UI resource.
    #
    # The URL carries the card file's mtime so that shipping a new card always
    # produces a URL the browser has never seen. A fixed URL lets a browser
    # keep serving a cached older build until someone hard-reloads, and this
    # must be the integration's only registration of the card: a second
    # registration under a different query string is a second module instance
    # with its own cache entry, which reintroduces the same staleness.
    stamp = await hass.async_add_executor_job(_card_version, card_dir)
    add_extra_js_url(hass, f"{CARD_MODULE_URL}?v={stamp}")
    return True


def _card_version(card_dir: Path) -> int:
    """Return the card bundle's mtime, or 0 when it cannot be read."""
    try:
        return int((card_dir / CARD_FILENAME).stat().st_mtime)
    except OSError:
        return 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EVN sensors from a config entry."""
    _restore_roster_from_legacy_entities(hass, entry)
    # The coordinator persists refreshed tokens and the linked-customer roster
    # into entry.data on nearly every update cycle (see coordinator.py
    # _persist_changed_tokens), and add_update_listener fires for any entry
    # write, options or data. Binding the listener to a snapshot of options
    # taken here keeps that frequent data-only churn from reloading the
    # integration; only an actual options change (customer codes, scan
    # interval) still triggers a reload.
    entry.async_on_unload(
        entry.add_update_listener(_create_options_update_listener(dict(entry.options)))
    )
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


def _create_options_update_listener(
    options_snapshot: dict,
) -> Callable[[HomeAssistant, ConfigEntry], Awaitable[None]]:
    """Build an update listener that reloads only when options actually changed.

    entry.data is rewritten frequently (session tokens, linked-customer
    roster) and add_update_listener has no way to tell data writes apart
    from options writes. Comparing against the options captured at setup
    time restores that distinction: a data-only write is a no-op here, while
    adding/removing a customer code or changing scan_interval in the UI
    still reloads the integration as before.
    """

    async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        if dict(entry.options) == options_snapshot:
            return
        await hass.config_entries.async_reload(entry.entry_id)

    return _async_update_listener


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and release coordinator state."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
