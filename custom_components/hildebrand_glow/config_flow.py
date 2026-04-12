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

def _tariff_schema(defaults: dict[str, float]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_ELECTRICITY_RATE, default=defaults.get(CONF_ELECTRICITY_RATE, DEFAULT_ELECTRICITY_RATE)): vol.Coerce(float),
        vol.Required(CONF_ELECTRICITY_STANDING_CHARGE, default=defaults.get(CONF_ELECTRICITY_STANDING_CHARGE, DEFAULT_ELECTRICITY_STANDING_CHARGE)): vol.Coerce(float),
        vol.Required(CONF_GAS_RATE, default=defaults.get(CONF_GAS_RATE, DEFAULT_GAS_RATE)): vol.Coerce(float),
        vol.Required(CONF_GAS_STANDING_CHARGE, default=defaults.get(CONF_GAS_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE)): vol.Coerce(float),
    })


class HildebrandGlowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hildebrand Glow."""
    VERSION = 1

    def __init__(self) -> None:
        self._user_data: dict[str, Any] = {}
        self._ve_names: dict[str, str] = {}
        self._pending_ve_ids: list[str] = []
        self._tariffs: dict[str, dict[str, float]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = GlowmarktApiClient(username=user_input[CONF_USERNAME], password=user_input[CONF_PASSWORD], session=session)
            try:
                if await client.test_connection():
                    self._user_data = user_input
                    self._ve_names = client.ve_names
                    self._pending_ve_ids = list(client.resources.keys())
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
        ve_id = self._pending_ve_ids[0]
        ve_name = self._ve_names.get(ve_id, ve_id)

        if user_input is not None:
            self._tariffs[ve_id] = user_input
            self._pending_ve_ids.pop(0)
            if self._pending_ve_ids:
                return await self.async_step_tariff()
            await self.async_set_unique_id(self._user_data[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Smart Meter ({self._user_data[CONF_USERNAME]})",
                data={**self._user_data, "tariffs": self._tariffs},
            )

        return self.async_show_form(
            step_id="tariff",
            data_schema=_tariff_schema({}),
            description_placeholders={"meter_name": ve_name},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> HildebrandGlowOptionsFlow:
        return HildebrandGlowOptionsFlow(config_entry)


class HildebrandGlowOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Hildebrand Glow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._ve_names: dict[str, str] = {}
        self._pending_ve_ids: list[str] = []
        self._tariffs: dict[str, dict[str, float]] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]
        self._ve_names = coordinator.api_client.ve_names
        self._pending_ve_ids = list(coordinator.resources.keys())
        self._tariffs = dict(self.config_entry.data.get("tariffs", {}))
        return await self.async_step_tariff()

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        ve_id = self._pending_ve_ids[0]
        ve_name = self._ve_names.get(ve_id, ve_id)
        current = self._tariffs.get(ve_id, {})

        if user_input is not None:
            self._tariffs[ve_id] = user_input
            self._pending_ve_ids.pop(0)
            if self._pending_ve_ids:
                return await self.async_step_tariff()
            new_data = {**self.config_entry.data, "tariffs": self._tariffs}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="tariff",
            data_schema=_tariff_schema(current),
            description_placeholders={"meter_name": ve_name},
        )
