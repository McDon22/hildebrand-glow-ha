"""Data update coordinator for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import GlowmarktApiClient, GlowmarktApiError, GlowmarktAuthError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, CONF_ELECTRICITY_RATE, CONF_GAS_RATE, CONF_ELECTRICITY_STANDING_CHARGE, CONF_GAS_STANDING_CHARGE

_LOGGER = logging.getLogger(__name__)

class GlowmarktDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Glowmarkt data."""

    def __init__(self, hass: HomeAssistant, api_client: GlowmarktApiClient, tariff_config: dict[str, dict[str, float]]) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.api_client = api_client
        self.tariff_config = tariff_config  # {ve_id: {rate_keys}} or {"_legacy": {rate_keys}}
        self._resources: dict[str, dict[str, dict[str, Any]]] = {}  # {ve_id: {classifier: resource_info}}
        self._last_readings: dict[str, dict[str, float]] = {}  # {ve_id: {classifier: value}}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._resources:
                self._resources = await self.api_client.discover_resources()

            readings_by_ve = await self.api_client.get_all_readings()

            # Merge with per-VE cache — only update if we got valid data
            for ve_id, readings in readings_by_ve.items():
                if ve_id not in self._last_readings:
                    self._last_readings[ve_id] = {}
                for key, value in readings.items():
                    if value is not None:
                        self._last_readings[ve_id][key] = value
                        _LOGGER.debug("Updated %s/%s to %.3f", ve_id, key, value)
                    elif key in self._last_readings[ve_id]:
                        _LOGGER.debug("Keeping cached value for %s/%s: %.3f (API returned None)",
                            ve_id, key, self._last_readings[ve_id][key])

            # Build per-meter data
            meters: dict[str, Any] = {}
            for ve_id, ve_resources in self._resources.items():
                merged = {k: self._last_readings.get(ve_id, {}).get(k) for k in ve_resources}
                # Per-VE tariff, falling back to legacy single-tariff for old installs
                tariff = self.tariff_config.get(ve_id) or self.tariff_config.get("_legacy", {})
                costs: dict[str, float] = {}

                elec = merged.get("electricity.consumption")
                if elec is not None:
                    elec_rate = tariff.get(CONF_ELECTRICITY_RATE, 0)
                    elec_standing = tariff.get(CONF_ELECTRICITY_STANDING_CHARGE, 0)
                    costs["electricity"] = round((elec * elec_rate) + elec_standing, 2)

                gas = merged.get("gas.consumption")
                if gas is not None:
                    gas_rate = tariff.get(CONF_GAS_RATE, 0)
                    gas_standing = tariff.get(CONF_GAS_STANDING_CHARGE, 0)
                    costs["gas"] = round((gas * gas_rate) + gas_standing, 2)

                costs["total"] = round(costs.get("electricity", 0) + costs.get("gas", 0), 2)
                costs["standing_charges_total"] = round(
                    tariff.get(CONF_ELECTRICITY_STANDING_CHARGE, 0) +
                    tariff.get(CONF_GAS_STANDING_CHARGE, 0), 2
                )

                meters[ve_id] = {
                    "name": self.api_client.ve_names.get(ve_id, ve_id),
                    "readings": merged,
                    "costs": costs,
                }

            return {"meters": meters, "resources": self._resources}

        except GlowmarktAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except GlowmarktApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    @property
    def resources(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self._resources

    def update_tariff_config(self, tariff_config: dict[str, dict[str, float]]) -> None:
        self.tariff_config = tariff_config

    def clear_daily_cache(self) -> None:
        """Clear the cached readings (call at midnight)."""
        self._last_readings.clear()
        _LOGGER.debug("Cleared daily reading cache")
