"""Config flow for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import GlowmarktApiClient, GlowmarktAuthError, GlowmarktApiError
from .const import DOMAIN

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
                    await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Smart Meter ({user_input[CONF_USERNAME]})",
                        data=user_input,
                    )
                else:
                    errors["base"] = "no_resources"
            except GlowmarktAuthError:
                errors["base"] = "invalid_auth"
            except GlowmarktApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
