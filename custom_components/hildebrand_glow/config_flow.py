"""Config flow for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import GlowmarktApiClient, GlowmarktAuthError, GlowmarktApiError
from .const import DOMAIN, CONF_ELECTRICITY_RATE, CONF_GAS_RATE, CONF_ELECTRICITY_STANDING_CHARGE, CONF_GAS_STANDING_CHARGE, DEFAULT_ELECTRICITY_RATE, DEFAULT_GAS_RATE, DEFAULT_ELECTRICITY_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE

_LOGGER = logging.getLogger(__name__)

class HildebrandGlowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hildebrand Glow."""
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = GlowmarktApiClient(username=user_input[CONF_USERNAME], password=user_input[CONF_PASSWORD], session=session)
            try:
                if await client.test_connection():
                    self._user_data = user_input
                    return await self.async_step_tariff()
                else:
                    errors["base"] = "no_resources"
            except GlowmarktAuthError:
                errors["base"] = "invalid_auth"
            except GlowmarktApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}), errors=errors)

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            data = {**self._user_data, **user_input}
            await self.async_set_unique_id(self._user_data[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=f"Smart Meter ({self._user_data[CONF_USERNAME]})", data=data)
        return self.async_show_form(step_id="tariff", data_schema=vol.Schema({vol.Required(CONF_ELECTRICITY_RATE, default=DEFAULT_ELECTRICITY_RATE): vol.Coerce(float), vol.Required(CONF_ELECTRICITY_STANDING_CHARGE, default=DEFAULT_ELECTRICITY_STANDING_CHARGE): vol.Coerce(float), vol.Required(CONF_GAS_RATE, default=DEFAULT_GAS_RATE): vol.Coerce(float), vol.Required(CONF_GAS_STANDING_CHARGE, default=DEFAULT_GAS_STANDING_CHARGE): vol.Coerce(float)}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> HildebrandGlowOptionsFlow:
        return HildebrandGlowOptionsFlow()

class HildebrandGlowOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Hildebrand Glow."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            new_data = {**self.config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data=user_input)
        current_data = self.config_entry.data
        return self.async_show_form(step_id="init", data_schema=vol.Schema({vol.Required(CONF_ELECTRICITY_RATE, default=current_data.get(CONF_ELECTRICITY_RATE, DEFAULT_ELECTRICITY_RATE)): vol.Coerce(float), vol.Required(CONF_ELECTRICITY_STANDING_CHARGE, default=current_data.get(CONF_ELECTRICITY_STANDING_CHARGE, DEFAULT_ELECTRICITY_STANDING_CHARGE)): vol.Coerce(float), vol.Required(CONF_GAS_RATE, default=current_data.get(CONF_GAS_RATE, DEFAULT_GAS_RATE)): vol.Coerce(float), vol.Required(CONF_GAS_STANDING_CHARGE, default=current_data.get(CONF_GAS_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE)): vol.Coerce(float)}))
