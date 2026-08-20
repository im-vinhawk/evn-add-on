"""Config and options flow for EVN Vietnam."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvnAuthenticationError, EvnClient, EvnError
from .const import CONF_ACCESS_TOKEN, CONF_CURRENT_CUSTOMER_CODE, CONF_CUSTOMER_CODES, CONF_DEVICE_ID, CONF_PRIMARY_CUSTOMER_CODE, CONF_REFRESH_TOKEN, DEFAULT_SCAN_INTERVAL, DOMAIN


def _codes(value: str) -> list[str]:
    """Validate comma-separated, already-linked customer codes."""
    codes: list[str] = []
    for raw in value.split(","):
        code = raw.strip().upper()
        if code and code not in codes:
            codes.append(code)
    if any(not code.isalnum() for code in codes):
        raise vol.Invalid("invalid_customer_code")
    return codes


class EvnVietnamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Collect an EVN username and password once, retaining token state only."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input:
            try:
                state = await EvnClient.async_login(async_get_clientsession(self.hass), user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except EvnAuthenticationError:
                errors["base"] = "invalid_auth"
            except EvnError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(state.primary_customer_code)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_USERNAME: state.username,
                    CONF_ACCESS_TOKEN: state.access_token,
                    CONF_REFRESH_TOKEN: state.refresh_token,
                    CONF_DEVICE_ID: state.device_id,
                    CONF_PRIMARY_CUSTOMER_CODE: state.primary_customer_code,
                    CONF_CURRENT_CUSTOMER_CODE: state.current_customer_code,
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
                state = await EvnClient.async_login(async_get_clientsession(self.hass), user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
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
                    CONF_USERNAME: state.username,
                    CONF_ACCESS_TOKEN: state.access_token,
                    CONF_REFRESH_TOKEN: state.refresh_token,
                    CONF_DEVICE_ID: state.device_id,
                    CONF_PRIMARY_CUSTOMER_CODE: state.primary_customer_code,
                    CONF_CURRENT_CUSTOMER_CODE: state.current_customer_code,
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        default_username = self._reauth_entry.data.get(CONF_USERNAME, "") if self._reauth_entry else ""
        schema = vol.Schema({vol.Required(CONF_USERNAME, default=default_username): str, vol.Required(CONF_PASSWORD): str})
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
            except vol.Invalid:
                errors[CONF_CUSTOMER_CODES] = "invalid_customer_code"
            else:
                return self.async_create_entry(title="", data={CONF_CUSTOMER_CODES: codes, CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])})
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_CUSTOMER_CODES, default=", ".join(options.get(CONF_CUSTOMER_CODES, []))): str,
            vol.Required(CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60))): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
