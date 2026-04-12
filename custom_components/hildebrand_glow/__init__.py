"""The Hildebrand Glow integration."""
from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import GlowmarktApiClient
from .const import DOMAIN, CONF_ELECTRICITY_RATE, CONF_GAS_RATE, CONF_ELECTRICITY_STANDING_CHARGE, CONF_GAS_STANDING_CHARGE, DEFAULT_ELECTRICITY_RATE, DEFAULT_GAS_RATE, DEFAULT_ELECTRICITY_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE
from .coordinator import GlowmarktDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


def _get_tariff_config(entry: ConfigEntry) -> dict[str, dict[str, float]]:
    """Return tariff config keyed by VE ID.

    New installs store tariffs as {"tariffs": {ve_id: {rate_keys}}}.
    Legacy installs have flat rate keys at the top level — these are wrapped
    under "_legacy" so the coordinator can fall back to them for any VE.
    """
    if "tariffs" in entry.data:
        return entry.data["tariffs"]
    return {"_legacy": {
        CONF_ELECTRICITY_RATE: entry.data.get(CONF_ELECTRICITY_RATE, DEFAULT_ELECTRICITY_RATE),
        CONF_GAS_RATE: entry.data.get(CONF_GAS_RATE, DEFAULT_GAS_RATE),
        CONF_ELECTRICITY_STANDING_CHARGE: entry.data.get(CONF_ELECTRICITY_STANDING_CHARGE, DEFAULT_ELECTRICITY_STANDING_CHARGE),
        CONF_GAS_STANDING_CHARGE: entry.data.get(CONF_GAS_STANDING_CHARGE, DEFAULT_GAS_STANDING_CHARGE),
    }}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)
    client = GlowmarktApiClient(username=entry.data[CONF_USERNAME], password=entry.data[CONF_PASSWORD], session=session)
    coordinator = GlowmarktDataUpdateCoordinator(hass=hass, api_client=client, tariff_config=_get_tariff_config(entry))
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: GlowmarktDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.update_tariff_config(_get_tariff_config(entry))
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
