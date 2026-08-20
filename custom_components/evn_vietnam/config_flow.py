"""Config and options flow for EVN Vietnam."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvnAuthenticationError, EvnClient, EvnError
from .const import CONF_ACCESS_TOKEN, CONF_CURRENT_CUSTOMER_CODE, CONF_CUSTOMER_CODES, CONF_DEVICE_ID, CONF_LINKED_CUSTOMERS, CONF_PRIMARY_CUSTOMER_CODE, CONF_REFRESH_TOKEN, CONF_SELECTED_CUSTOMER_CODES, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import (
    merge_linked_customer_meter_points,
    normalize_customer_code,
    normalize_linked_customer_meter_points,
    selected_customer_codes,
)


def _codes(value: str) -> list[str]:
    """Validate comma-separated, already-linked customer codes."""
    codes: list[str] = []
    for raw in value.split(","):
        raw_code = raw.strip()
        code = normalize_customer_code(raw_code)
        if raw_code and not code:
            raise vol.Invalid("invalid_customer_code")
        if code and code not in codes:
            codes.append(code)
    return codes


class EvnVietnamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Collect EVN credentials for secure Home Assistant entry persistence."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input:
            try:
                state, roster = await EvnClient.async_login_and_discover(
                    async_get_clientsession(self.hass),
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except EvnAuthenticationError:
                errors["base"] = "invalid_auth"
            except EvnError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(state.primary_customer_code)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_ACCESS_TOKEN: state.access_token,
                    CONF_REFRESH_TOKEN: state.refresh_token,
                    CONF_DEVICE_ID: state.device_id,
                    CONF_PRIMARY_CUSTOMER_CODE: state.primary_customer_code,
                    CONF_CURRENT_CUSTOMER_CODE: state.current_customer_code,
                    CONF_LINKED_CUSTOMERS: roster,
                }
                return self.async_create_entry(title=f"EVN {state.primary_customer_code}", data=data)
        schema = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> config_entries.ConfigFlowResult:
        """Start reauthentication after a refresh token expires."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Replace only token state after the user has entered a password again."""
        errors: dict[str, str] = {}
        if user_input and self._reauth_entry:
            try:
                previous_roster = merge_linked_customer_meter_points(
                    normalize_linked_customer_meter_points(self._reauth_entry.data.get(CONF_LINKED_CUSTOMERS)),
                    {code: "" for code in self._reauth_entry.options.get(CONF_CUSTOMER_CODES, [])},
                )
                state, roster = await EvnClient.async_login_and_discover(
                    async_get_clientsession(self.hass),
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    previous_roster,
                    self._reauth_entry.data.get(CONF_DEVICE_ID),
                )
            except EvnAuthenticationError:
                errors["base"] = "invalid_auth"
            except EvnError:
                errors["base"] = "cannot_connect"
            else:
                if state.primary_customer_code != self._reauth_entry.data.get(CONF_PRIMARY_CUSTOMER_CODE):
                    errors["base"] = "wrong_account"
                    return self.async_show_form(
                        step_id="reauth_confirm",
                        data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}),
                        errors=errors,
                    )
                data = {
                    **self._reauth_entry.data,
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_ACCESS_TOKEN: state.access_token,
                    CONF_REFRESH_TOKEN: state.refresh_token,
                    CONF_DEVICE_ID: state.device_id,
                    CONF_PRIMARY_CUSTOMER_CODE: state.primary_customer_code,
                    CONF_CURRENT_CUSTOMER_CODE: state.current_customer_code,
                    CONF_LINKED_CUSTOMERS: roster,
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        schema = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> EvnVietnamOptionsFlow:
        return EvnVietnamOptionsFlow()


class EvnVietnamOptionsFlow(config_entries.OptionsFlow):
    """Configure additional codes already linked in the EVN mobile account."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input:
            try:
                codes = _codes(user_input[CONF_CUSTOMER_CODES])
                configured_codes = list(normalize_linked_customer_meter_points(
                    self.config_entry.data.get(CONF_LINKED_CUSTOMERS)
                ))
                primary = self.config_entry.data.get(CONF_PRIMARY_CUSTOMER_CODE, "")
                if primary and primary not in configured_codes:
                    configured_codes.insert(0, primary)
                configured_codes.extend(code for code in codes if code not in configured_codes)
                requested_selection = _codes(user_input[CONF_SELECTED_CUSTOMER_CODES])
                if any(code not in configured_codes for code in requested_selection):
                    raise vol.Invalid("invalid_selected_customer_code")
                selection = selected_customer_codes(configured_codes, requested_selection, primary)
            except vol.Invalid as err:
                errors[
                    CONF_SELECTED_CUSTOMER_CODES
                    if str(err) == "invalid_selected_customer_code"
                    else CONF_CUSTOMER_CODES
                ] = "invalid_customer_code"
            else:
                return self.async_create_entry(title="", data={
                    CONF_CUSTOMER_CODES: codes,
                    CONF_SELECTED_CUSTOMER_CODES: selection,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                })
        options = self.config_entry.options
        configured_codes = list(normalize_linked_customer_meter_points(
            self.config_entry.data.get(CONF_LINKED_CUSTOMERS)
        ))
        primary = str(self.config_entry.data.get(CONF_PRIMARY_CUSTOMER_CODE) or "").upper()
        if primary and primary not in configured_codes:
            configured_codes.insert(0, primary)
        configured_codes.extend(
            code for code in options.get(CONF_CUSTOMER_CODES, []) if code not in configured_codes
        )
        default_selection = options.get(CONF_SELECTED_CUSTOMER_CODES, configured_codes)
        schema = vol.Schema({
            vol.Required(CONF_CUSTOMER_CODES, default=", ".join(options.get(CONF_CUSTOMER_CODES, []))): str,
            vol.Required(CONF_SELECTED_CUSTOMER_CODES, default=", ".join(default_selection)): str,
            vol.Required(CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60))): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
