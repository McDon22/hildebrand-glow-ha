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
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode, SelectOptionDict
from .api import GlowmarktApiClient, GlowmarktAuthError, GlowmarktApiError
from .const import DOMAIN, CONF_VIRTUAL_ENTITY, CONF_ELECTRICITY_RATE, CONF_GAS_RATE, CONF_ELECTRICITY_STANDING_CHARGE, CONF_GAS_STANDING_CHARGE, DEFAULT_ELECTRICITY_RATE, DEFAULT_GAS_RATE, DEFAULT_ELECTRICITY_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE

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
                await client.authenticate()
                virtual_entities = await client.get_virtual_entities()
                
                if not virtual_entities:
                    errors["base"] = "no_resources"
                else:
                    # Store credentials and VE list for next step
                    self._user_data = user_input
                    self._virtual_entities = virtual_entities
                    self._client = client
                    return await self.async_step_select_entity()
                    
            except GlowmarktAuthError:
                errors["base"] = "invalid_auth"
            except GlowmarktApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}), errors=errors)

    async def async_step_select_entity(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle virtual entity selection."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            selected_ve_id = user_input[CONF_VIRTUAL_ENTITY]
            
            # Verify the selected entity has resources
            try:
                resources = await self._client.discover_resources(selected_ve_id)
                if resources:
                    self._user_data[CONF_VIRTUAL_ENTITY] = selected_ve_id
                    # Find the VE name for the title
                    ve_name = next(
                        (ve.get("name", "Smart Meter") for ve in self._virtual_entities if ve.get("veId") == selected_ve_id),
                        "Smart Meter"
                    )
                    self._ve_name = ve_name
                    return await self.async_step_tariff()
                else:
                    errors["base"] = "no_resources"
            except GlowmarktApiError:
                errors["base"] = "cannot_connect"
        
        # Build dropdown options with nice labels
        ve_options = [
            SelectOptionDict(value=ve.get("veId"), label=ve.get("name", "Unknown Location"))
            for ve in self._virtual_entities
            if ve.get("veId")
        ]
        
        return self.async_show_form(
            step_id="select_entity",
            data_schema=vol.Schema({
                vol.Required(CONF_VIRTUAL_ENTITY): SelectSelector(
                    SelectSelectorConfig(
                        options=ve_options,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }),
            errors=errors
        )

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            data = {**self._user_data, **user_input}
            await self.async_set_unique_id(self._user_data[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            # Use VE name if available, otherwise fall back to username
            title = getattr(self, '_ve_name', None) or f"Smart Meter ({self._user_data[CONF_USERNAME]})"
            return self.async_create_entry(title=title, data=data)
        
        data_schema = vol.Schema({
            vol.Required(CONF_ELECTRICITY_RATE, default=DEFAULT_ELECTRICITY_RATE): vol.Coerce(float),
            vol.Required(CONF_ELECTRICITY_STANDING_CHARGE, default=DEFAULT_ELECTRICITY_STANDING_CHARGE): vol.Coerce(float),
            vol.Required(CONF_GAS_RATE, default=DEFAULT_GAS_RATE): vol.Coerce(float),
            vol.Required(CONF_GAS_STANDING_CHARGE, default=DEFAULT_GAS_STANDING_CHARGE): vol.Coerce(float),
        })
        return self.async_show_form(step_id="tariff", data_schema=data_schema)

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
        
        # Get current values or fall back to defaults
        current_data = self.config_entry.data
        electricity_rate = current_data.get(CONF_ELECTRICITY_RATE, DEFAULT_ELECTRICITY_RATE)
        electricity_standing_charge = current_data.get(CONF_ELECTRICITY_STANDING_CHARGE, DEFAULT_ELECTRICITY_STANDING_CHARGE)
        gas_rate = current_data.get(CONF_GAS_RATE, DEFAULT_GAS_RATE)
        gas_standing_charge = current_data.get(CONF_GAS_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE)
        
        # Build the form schema
        data_schema = vol.Schema({
            vol.Required(CONF_ELECTRICITY_RATE, default=electricity_rate): vol.Coerce(float),
            vol.Required(CONF_ELECTRICITY_STANDING_CHARGE, default=electricity_standing_charge): vol.Coerce(float),
            vol.Required(CONF_GAS_RATE, default=gas_rate): vol.Coerce(float),
            vol.Required(CONF_GAS_STANDING_CHARGE, default=gas_standing_charge): vol.Coerce(float),
        })
        
        return self.async_show_form(step_id="init", data_schema=data_schema)
