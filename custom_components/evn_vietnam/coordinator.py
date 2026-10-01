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
from homeassistant.util import dt as dt_util

from .api import EvnApiError, EvnAuthenticationError, EvnClient, EvnCustomerSwitchError, EvnMeterPointError
from .calculation import (
    aggregate_selected_overviews, attach_readings, merge_bill_sources, outage_summary, unpaid_summary, upcoming_outages,
)
from .const import (
    CONF_ACCESS_TOKEN, CONF_CURRENT_CUSTOMER_CODE, CONF_CUSTOMER_CODES, CONF_DEVICE_ID, CONF_LINKED_CUSTOMERS, CONF_PRIMARY_CUSTOMER_CODE,
    CONF_REFRESH_TOKEN, DEFAULT_RECONCILE_THRESHOLD_KWH, DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_BILL, OUTAGE_LOOKAHEAD_DAYS,
    SESSION_KEEPALIVE_INTERVAL, CONF_SELECTED_CUSTOMER_CODES, CONF_CUSTOMER_ALIASES, CONF_RECONCILE_THRESHOLD_KWH,
)
from .daily_store import day_values
from .history import DailyHistory, create_daily_history
from .pricing import price_overview
from .projection import project_running_period
from .reconcile import annotate_bills
from .models import (
    merge_linked_customer_meter_points,
    normalize_aliases,
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
    # Daily kWh store and long-term statistics; None until set up (and in tests that skip __init__).
    _history: DailyHistory | None = None

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
        try:
            self._history = create_daily_history(hass, entry.entry_id, self._client)
        except Exception as err:  # noqa: BLE001 - history is optional; the sensors work without it
            _LOGGER.debug("EVN daily history unavailable (%s)", type(err).__name__)
        self._unsub_keepalive = None
        interval = timedelta(minutes=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60)))
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=interval)
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub_keepalive = async_track_time_interval(
            hass, self._async_keepalive_session, SESSION_KEEPALIVE_INTERVAL
        )

    @property
    def shapes(self) -> dict[str, dict[str, Any]]:
        """Key/type-only description of the latest raw EVN rows."""
        return self._client.last_shapes

    @property
    def backfill_status(self) -> dict[str, dict[str, Any]]:
        """Per code: oldest stored day and whether the historical backfill finished."""
        return self._history.backfill_status() if self._history else {}

    async def _async_update_data(self) -> dict[str, Any]:
        # Customer switching mutates the EVN JWT. Keep all update paths strictly
        # serial even when HA receives simultaneous refresh requests.
        async with self._update_lock:
            codes = configured_customer_codes(self.config_entry)
            if not codes:
                raise UpdateFailed("No EVN customer code is configured")
            meters: dict[str, dict[str, Any]] = {}
            partial_errors: dict[str, str] = {}
            readings_by_code: dict[str, list[dict[str, Any]]] = {}
            for code in codes:
                try:
                    overview = await self._client.async_overview(code)
                    history_bills, overview["bills_fresh"] = await self._client.async_bills_with_source(code)
                    unpaid, overview["unpaid_fresh"], unpaid_loaded = await self._async_unpaid_or_empty(code, partial_errors)
                    bills = merge_bill_sources(history_bills, unpaid, unpaid_fresh=overview["unpaid_fresh"])
                    readings_by_code[code] = await self._async_readings_or_empty(code)
                    overview["bills"] = attach_readings(bills, readings_by_code[code])
                    overview.update(unpaid_summary(overview["bills"], loaded=unpaid_loaded))
                    overview.update(await self._async_outage_summary(code, partial_errors))
                    # The legacy monthly history is derived from official bills.
                    overview["monthly_history"] = overview["bills"]
                    overview["history_fetched_at"] = self._client.history_fetched_at(code)
                    price_overview(overview)
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
            events = await self._async_update_history(meters, codes, aggregate_codes)
            self._fire_bill_events(events)
            self._compose_windows(meters)
            self._add_projections(meters, readings_by_code)
            for code, overview in meters.items():
                overview["bills"] = overview["monthly_history"] = self._reconciled(code, overview["bills"])
            aggregate = aggregate_selected_overviews(
                meters, aggregate_codes, partial_errors, self._last_good_history(partial_errors, meters)
            )
            # The backfill can switch customer or refresh the session, so persist those tokens now.
            self._persist_changed_tokens()
            return {"meters": meters, "aggregate": aggregate, "partial_errors": partial_errors}

    async def _async_update_history(
        self, meters: dict[str, dict[str, Any]], codes: list[str], selected: list[str]
    ) -> list[dict[str, Any]]:
        """Store the live days and publish statistics; returns the new-bill events to fire.

        The first refresh leaves the backfill for later.
        """
        if self._history is None:
            return []
        aliases = normalize_aliases(self.config_entry.options.get(CONF_CUSTOMER_ALIASES), codes)
        try:
            events = await self._history.async_update(
                meters=meters, codes=codes, selected=selected, aliases=aliases, allow_backfill=self.data is not None,
                threshold_kwh=self._reconcile_threshold(),
            )
        except Exception as err:  # noqa: BLE001 - a history problem must never fail the sensor update
            _LOGGER.debug("EVN daily history update skipped (%s)", type(err).__name__)
            return []
        return list(events) if isinstance(events, list) else []

    def _reconcile_threshold(self) -> float:
        """The kWh a bill may differ from the collected days; an unusable option falls back to the default."""
        try:
            value = float(self.config_entry.options.get(CONF_RECONCILE_THRESHOLD_KWH, DEFAULT_RECONCILE_THRESHOLD_KWH))
        except (TypeError, ValueError):
            return DEFAULT_RECONCILE_THRESHOLD_KWH
        return value if 0 <= value <= 100 else DEFAULT_RECONCILE_THRESHOLD_KWH

    def _fire_bill_events(self, events: list[dict[str, Any]]) -> None:
        """Announce new bills; their state was saved before they were handed over, so each fires once."""
        for payload in events:
            try:
                self.hass.bus.async_fire(EVENT_BILL, payload)
            except Exception as err:  # noqa: BLE001 - an event problem must never fail the sensor update
                _LOGGER.debug("EVN bill event not fired (%s)", type(err).__name__)

    def _reconciled(self, code: str, bills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bill rows with the canonical period and, when the daily store is loaded, the kWh reconciliation."""
        try:
            if self._history is not None:
                return self._history.annotate_bills(code, bills, self._reconcile_threshold())
        except Exception as err:  # noqa: BLE001 - a reconciliation problem must never fail the sensor update
            _LOGGER.debug("EVN bill reconciliation skipped (%s)", type(err).__name__)
        return annotate_bills(bills, None, self._reconcile_threshold())[0]

    def _compose_windows(self, meters: dict[str, dict[str, Any]]) -> None:
        """Give each code the last 31 days across the month boundary, and today/yesterday read from them."""
        history = self._history
        if history is None:
            return
        try:
            if not history.available:
                return
            today = history.today()
            for code, overview in meters.items():
                rows = history.compose(code, overview.get("daily_history", []))
                overview["daily_history"] = rows
                overview["today_consumption"], overview["yesterday_consumption"] = day_values(rows, today)
        except Exception as err:  # noqa: BLE001 - a history problem must never fail the sensor update
            _LOGGER.debug("EVN rolling window skipped (%s)", type(err).__name__)

    def _add_projections(self, meters: dict[str, dict[str, Any]], readings_by_code: dict[str, list[dict[str, Any]]]) -> None:
        """Estimate each code's running billing period from the stored days; unknown when they are not available."""
        history = self._history
        for code, overview in meters.items():
            overview["projection"], overview["projected_period_amount"] = None, None
            try:
                if history is None or not history.available:
                    continue
                overview["projection"] = project_running_period(
                    history.days(code), overview.get("bills", []), readings_by_code.get(code, []), history.today(),
                    overview["price_model"],
                )
            except Exception as err:  # noqa: BLE001 - a projection problem must never fail the sensor update
                _LOGGER.debug("EVN period projection skipped (%s)", type(err).__name__)
                continue
            if overview["projection"] is not None:
                overview["projected_period_amount"] = overview["projection"]["projected_amount"]

    def _last_good_history(
        self, partial_errors: dict[str, str], meters: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Last good bills of codes whose live call failed, read from the client's memory only."""
        history: dict[str, dict[str, Any]] = {}
        for code in partial_errors:
            cached = None if code in meters else self._client.cached_history(code)
            if cached is not None:
                bills, readings, fetched_at = cached
                history[code] = {
                    "bills": self._reconciled(code, attach_readings(bills, readings)), "history_fetched_at": fetched_at,
                }
        return history

    async def _async_unpaid_or_empty(
        self, code: str, partial_errors: dict[str, str]
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """(unpaid bills, read within the cadence, ever loaded).

        A failure leaves the code's other data alone and its payment state unknown: the bills are then
        the paid history only, never "paid" by default.
        """
        try:
            rows, fresh = await self._client.async_unpaid_bills_with_source(code)
        except EvnAuthenticationError:
            raise
        except EvnApiError:
            _LOGGER.debug("EVN unpaid bills unavailable; the payment state stays unknown")
            partial_errors.setdefault(code, "unpaid_bills")
            return [], False, False
        return rows, fresh, True

    async def _async_outage_summary(self, code: str, partial_errors: dict[str, str]) -> dict[str, Any]:
        """The next planned outage of a code; it never fails the update, and unknown stays unknown."""
        now = dt_util.now()
        try:
            outages = await self._client.async_outages(code, now.date(), now.date() + timedelta(days=OUTAGE_LOOKAHEAD_DAYS))
            return outage_summary(upcoming_outages(outages, now), loaded=True)
        except EvnAuthenticationError:
            raise
        except EvnApiError:
            _LOGGER.debug("EVN planned outages unavailable; they stay unknown")
            partial_errors.setdefault(code, "outages")
        except Exception as err:  # noqa: BLE001 - an outage problem must never fail the sensor update
            _LOGGER.debug("EVN planned outages skipped (%s)", type(err).__name__)
        return outage_summary([], loaded=False)

    async def _async_readings_or_empty(self, code: str) -> list[dict[str, Any]]:
        """Bills stay useful without their kWh, so a readings failure only leaves kWh unknown."""
        try:
            return await self._client.async_monthly_readings(code)
        except EvnAuthenticationError:
            raise
        except EvnApiError:
            _LOGGER.debug("EVN monthly readings unavailable; bill kWh stays unknown")
            return []

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
        if self._history is not None:
            await self._history.async_flush()
        await super().async_shutdown()
