"""DataUpdateCoordinator for EVN Vietnam sensors."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EvnApiError, EvnAuthenticationError, EvnClient, EvnCustomerSwitchError, EvnMeterPointError
from .calculation import aggregate_bills, aggregate_daily, aggregate_overviews
from .const import (
    CONF_ACCESS_TOKEN, CONF_CURRENT_CUSTOMER_CODE, CONF_CUSTOMER_CODES, CONF_DEVICE_ID, CONF_LINKED_CUSTOMERS, CONF_PRIMARY_CUSTOMER_CODE,
    CONF_REFRESH_TOKEN, CONF_USERNAME, DEFAULT_SCAN_INTERVAL, DOMAIN,
)
from .models import merge_linked_customer_meter_points, normalize_linked_customer_meter_points, SessionState

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
        self._client = EvnClient(async_get_clientsession(hass), self._state, self._linked_customer_meter_points)
        self._update_lock = asyncio.Lock()
        interval = timedelta(minutes=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60)))
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=interval)

    async def _async_update_data(self) -> dict[str, Any]:
        # Customer switching mutates the EVN JWT. Keep all update paths strictly
        # serial even when HA receives simultaneous refresh requests.
        async with self._update_lock:
            codes = configured_customer_codes(self.config_entry)
            if not codes:
                raise UpdateFailed("No EVN customer code is configured")
            meters: dict[str, dict[str, Any]] = {}
            partial_errors: dict[str, str] = {}
            bill_series = []
            daily_series = []
            for code in codes:
                try:
                    overview = await self._client.async_overview(code)
                    overview["bills"] = await self._client.async_bills(code)
                    # The legacy monthly history is derived from official bills.
                    overview["monthly_history"] = overview["bills"]
                    meters[code] = overview
                    bill_series.append(overview["bills"])
                    daily_series.append((code, overview["daily_history"]))
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
            aggregate = aggregate_overviews(meters.values(), codes)
            aggregate["successful_customer_codes"] = list(meters)
            aggregate["bills"] = aggregate_bills(bill_series)
            aggregate["monthly_history"] = aggregate["bills"]
            aggregate["daily_history"] = aggregate_daily(daily_series)
            if partial_errors:
                aggregate["partial_errors"] = partial_errors
                aggregate["is_partial"] = True
            else:
                aggregate["is_partial"] = False
            return {"meters": meters, "aggregate": aggregate, "partial_errors": partial_errors}

    def _persist_changed_tokens(self) -> None:
        """Persist refreshed/switched tokens but never a password."""
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
        updated.pop(CONF_USERNAME, None)
        for key in (CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_DEVICE_ID, CONF_PRIMARY_CUSTOMER_CODE, CONF_CURRENT_CUSTOMER_CODE):
            updated.setdefault(key, self.config_entry.data.get(key))
        if updated != self.config_entry.data:
            self.hass.config_entries.async_update_entry(self.config_entry, data=updated)
