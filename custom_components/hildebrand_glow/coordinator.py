"""Data update coordinator for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import GlowmarktApiClient, GlowmarktApiError, GlowmarktAuthError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

class GlowmarktDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Glowmarkt data."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        api_client: GlowmarktApiClient, 
        tariff_config: dict[str, float],
        virtual_entity_id: str | None = None
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.api_client = api_client
        self.tariff_config = tariff_config
        self._virtual_entity_id = virtual_entity_id
        self._resources: dict[str, dict[str, Any]] = {}
        self._last_readings: dict[str, float] = {}  # Cache last known good readings

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._resources:
                self._resources = await self.api_client.discover_resources(self._virtual_entity_id)
            
            readings = await self.api_client.get_all_readings()
            
            # Merge with cached readings - only update if we got valid data
            for key, value in readings.items():
                if value is not None:
                    self._last_readings[key] = value
                    _LOGGER.debug("Updated %s to %.3f", key, value)
                elif key in self._last_readings:
                    _LOGGER.debug("Keeping cached value for %s: %.3f (API returned None)", 
                        key, self._last_readings[key])
            
            # Use cached readings for the data
            merged_readings = {k: self._last_readings.get(k) for k in readings.keys()}
            
            data: dict[str, Any] = {"readings": merged_readings, "resources": self._resources, "costs": {}}
            
            elec = merged_readings.get("electricity.consumption")
            if elec is not None:
                elec_rate = self.tariff_config.get("electricity_rate", 0)
                elec_standing = self.tariff_config.get("electricity_standing_charge", 0)
                data["costs"]["electricity"] = round((elec * elec_rate) + elec_standing, 2)
            
            gas = merged_readings.get("gas.consumption")
            if gas is not None:
                gas_rate = self.tariff_config.get("gas_rate", 0)
                gas_standing = self.tariff_config.get("gas_standing_charge", 0)
                data["costs"]["gas"] = round((gas * gas_rate) + gas_standing, 2)
            
            data["costs"]["total"] = round(data["costs"].get("electricity", 0) + data["costs"].get("gas", 0), 2)
            data["costs"]["standing_charges_total"] = round(
                self.tariff_config.get("electricity_standing_charge", 0) + 
                self.tariff_config.get("gas_standing_charge", 0), 2
            )
            
            return data
            
        except GlowmarktAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except GlowmarktApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    @property
    def resources(self) -> dict[str, dict[str, Any]]:
        return self._resources

    def update_tariff_config(self, tariff_config: dict[str, float]) -> None:
        self.tariff_config = tariff_config
    
    def clear_daily_cache(self) -> None:
        """Clear the cached readings (call at midnight)."""
        self._last_readings.clear()
        _LOGGER.debug("Cleared daily reading cache")
