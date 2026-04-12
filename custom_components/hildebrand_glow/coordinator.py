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

    def __init__(self, hass: HomeAssistant, api_client: GlowmarktApiClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.api_client = api_client
        self._resources: dict[str, dict[str, dict[str, Any]]] = {}  # {ve_id: {classifier: resource_info}}
        self._last_readings: dict[str, dict[str, float]] = {}       # {ve_id: {classifier: value}}
        self._last_tariffs: dict[str, dict[str, dict[str, float | None]]] = {}  # {ve_id: {classifier: {rate, standing_charge}}}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._resources:
                self._resources = await self.api_client.discover_resources()

            readings_by_ve = await self.api_client.get_all_readings()
            tariffs_by_ve = await self.api_client.get_all_tariffs()

            # Merge readings cache — only update if we got valid data
            for ve_id, readings in readings_by_ve.items():
                if ve_id not in self._last_readings:
                    self._last_readings[ve_id] = {}
                for key, value in readings.items():
                    if value is not None:
                        self._last_readings[ve_id][key] = value
                        _LOGGER.debug("Updated reading %s/%s to %.3f", ve_id, key, value)
                    elif key in self._last_readings[ve_id]:
                        _LOGGER.debug("Keeping cached reading for %s/%s: %.3f", ve_id, key, self._last_readings[ve_id][key])

            # Merge tariff cache — only update fields that came back non-None
            for ve_id, ve_tariffs in tariffs_by_ve.items():
                if ve_id not in self._last_tariffs:
                    self._last_tariffs[ve_id] = {}
                for classifier, tariff in ve_tariffs.items():
                    if classifier not in self._last_tariffs[ve_id]:
                        self._last_tariffs[ve_id][classifier] = {}
                    if tariff.get("rate") is not None:
                        self._last_tariffs[ve_id][classifier]["rate"] = tariff["rate"]
                    if tariff.get("standing_charge") is not None:
                        self._last_tariffs[ve_id][classifier]["standing_charge"] = tariff["standing_charge"]

            # Build per-meter data
            meters: dict[str, Any] = {}
            for ve_id, ve_resources in self._resources.items():
                merged = {k: self._last_readings.get(ve_id, {}).get(k) for k in ve_resources}
                ve_tariffs = self._last_tariffs.get(ve_id, {})
                costs: dict[str, Any] = {}

                elec = merged.get("electricity.consumption")
                elec_tariff = ve_tariffs.get("electricity.consumption", {})
                elec_rate = elec_tariff.get("rate")        # pence/kWh
                elec_standing = elec_tariff.get("standing_charge")  # pence/day
                if elec is not None and elec_rate is not None and elec_standing is not None:
                    costs["electricity"] = round((elec * elec_rate / 100) + (elec_standing / 100), 2)

                gas = merged.get("gas.consumption")
                gas_tariff = ve_tariffs.get("gas.consumption", {})
                gas_rate = gas_tariff.get("rate")
                gas_standing = gas_tariff.get("standing_charge")
                if gas is not None and gas_rate is not None and gas_standing is not None:
                    costs["gas"] = round((gas * gas_rate / 100) + (gas_standing / 100), 2)

                costs["total"] = round(costs.get("electricity", 0) + costs.get("gas", 0), 2)

                elec_standing_gbp = elec_standing / 100 if elec_standing is not None else None
                gas_standing_gbp = gas_standing / 100 if gas_standing is not None else None
                if elec_standing_gbp is not None or gas_standing_gbp is not None:
                    costs["standing_charges_total"] = round(
                        (elec_standing_gbp or 0) + (gas_standing_gbp or 0), 2
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

    def clear_daily_cache(self) -> None:
        """Clear the cached readings (call at midnight)."""
        self._last_readings.clear()
        _LOGGER.debug("Cleared daily reading cache")
