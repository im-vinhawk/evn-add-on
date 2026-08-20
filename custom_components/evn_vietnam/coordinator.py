"""DataUpdateCoordinator for EVN Vietnam sensors."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EvnApiError, EvnAuthenticationError, EvnClient, EvnCustomerSwitchError, EvnMeterPointError
from .calculation import aggregate_selected_overviews
from .const import (
    CONF_ACCESS_TOKEN, CONF_CURRENT_CUSTOMER_CODE, CONF_CUSTOMER_CODES, CONF_DEVICE_ID, CONF_LINKED_CUSTOMERS, CONF_PRIMARY_CUSTOMER_CODE,
    CONF_REFRESH_TOKEN, DEFAULT_SCAN_INTERVAL, DOMAIN, SESSION_KEEPALIVE_INTERVAL,
    CONF_SELECTED_CUSTOMER_CODES,
)
from .models import (
    merge_linked_customer_meter_points,
    normalize_linked_customer_meter_points,
    selected_customer_codes,
    SessionState,
)

_LOGGER = logging.getLogger(__name__)


def configured_customer_codes(entry: ConfigEntry) -> list[str]:
    """Return unique, real customer codes with the primary code always included."""
    roster = normalize_linked_customer_meter_points(entry.data.get(CONF_LINKED_CUSTOMERS))
    raw_codes = [
        entry.data.get(CONF_PRIMARY_CUSTOMER_CODE, ""),
        *roster,
        *entry.options.get(CONF_CUSTOMER_CODES, []),
    ]
    codes: list[str] = []
    for value in raw_codes:
        code = str(value or "").strip().upper()
        if code and code != "__AGGREGATE__" and code not in codes:
            codes.append(code)
    return codes


def aggregate_customer_codes(entry: ConfigEntry) -> list[str]:
    """Return the persisted aggregate scope without changing the EVN roster."""
    return selected_customer_codes(
        configured_customer_codes(entry),
        entry.options.get(CONF_SELECTED_CUSTOMER_CODES),
        entry.data.get(CONF_PRIMARY_CUSTOMER_CODE),
    )


class EvnDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch all configured customers and calculate an optional local total."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        self._state = SessionState.from_mapping(entry.data)
        self._linked_customer_meter_points = merge_linked_customer_meter_points(
            entry.data.get(CONF_LINKED_CUSTOMERS),
            {code: "" for code in configured_customer_codes(entry)},
        )
        self._client = EvnClient(
            async_get_clientsession(hass), self._state, self._linked_customer_meter_points,
            password=entry.data.get(CONF_PASSWORD),
        )
        self._update_lock = asyncio.Lock()
        self._unsub_keepalive = None
        interval = timedelta(minutes=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60)))
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=interval)
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub_keepalive = async_track_time_interval(
            hass, self._async_keepalive_session, SESSION_KEEPALIVE_INTERVAL
        )

    async def _async_update_data(self) -> dict[str, Any]:
        # Customer switching mutates the EVN JWT. Keep all update paths strictly
        # serial even when HA receives simultaneous refresh requests.
        async with self._update_lock:
            codes = configured_customer_codes(self.config_entry)
            if not codes:
                raise UpdateFailed("No EVN customer code is configured")
            meters: dict[str, dict[str, Any]] = {}
            partial_errors: dict[str, str] = {}
            for code in codes:
                try:
                    overview = await self._client.async_overview(code)
                    overview["bills"] = await self._client.async_bills(code)
                    # The legacy monthly history is derived from official bills.
                    overview["monthly_history"] = overview["bills"]
                    meters[code] = overview
                except EvnAuthenticationError as err:
                    raise ConfigEntryAuthFailed("EVN session expired; reauthenticate this integration") from err
                except EvnCustomerSwitchError:
                    _LOGGER.debug("EVN update skipped a customer because switching is unavailable")
                    partial_errors[code] = "customer_switch"
                except EvnMeterPointError:
                    _LOGGER.debug("EVN update skipped a customer because no verified meter point is available")
                    partial_errors[code] = "meter_point"
                except EvnApiError:
                    _LOGGER.debug("EVN update skipped a customer because EVN data is unavailable")
                    partial_errors[code] = "api_error"
            self._persist_changed_tokens()
            if not meters:
                raise UpdateFailed("EVN could not return data for any configured customer")
            aggregate_codes = aggregate_customer_codes(self.config_entry)
            aggregate = aggregate_selected_overviews(meters, aggregate_codes, partial_errors)
            return {"meters": meters, "aggregate": aggregate, "partial_errors": partial_errors}

    def _persist_changed_tokens(self) -> None:
        """Persist refreshed/switched tokens while retaining stored credentials."""
        roster = merge_linked_customer_meter_points(
            self._linked_customer_meter_points,
            self._client.linked_customer_meter_points,
        )
        roster = merge_linked_customer_meter_points(
            roster,
            {code: "" for code in configured_customer_codes(self.config_entry)},
        )
        self._linked_customer_meter_points = roster
        updated = {**self.config_entry.data, **self._state.as_dict(), CONF_LINKED_CUSTOMERS: roster}
        for key in (CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_DEVICE_ID, CONF_PRIMARY_CUSTOMER_CODE, CONF_CURRENT_CUSTOMER_CODE):
            updated.setdefault(key, self.config_entry.data.get(key))
        if updated != self.config_entry.data:
            self.hass.config_entries.async_update_entry(self.config_entry, data=updated)

    async def _async_keepalive_session(self, _now=None) -> None:
        """Refresh EVN JWT between data polls, matching the mobile-app session."""
        async with self._update_lock:
            if not self._client._needs_proactive_refresh():
                return
            try:
                await self._client._async_restore_after_auth_failure()
            except EvnAuthenticationError as err:
                raise ConfigEntryAuthFailed("EVN session expired; reauthenticate this integration") from err
            except EvnApiError:
                _LOGGER.debug("EVN session keepalive skipped because EVN is temporarily unavailable")
                return
            self._persist_changed_tokens()

    async def async_shutdown(self) -> None:
        """Stop JWT keepalive when the config entry unloads."""
        if self._unsub_keepalive:
            self._unsub_keepalive()
            self._unsub_keepalive = None
        await super().async_shutdown()
