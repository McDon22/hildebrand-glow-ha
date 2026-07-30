"""Data update coordinator for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import DailyReading, GlowmarktApiClient, GlowmarktApiError, GlowmarktAuthError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Classifiers whose readings represent a single day's usage, not a running
# total. Their sensors use state_class total_increasing, which HA's Energy
# dashboard statistics treat as a cumulative meter reading -- so they need a
# persisted running counter (see _accumulate), not the raw daily value.
CUMULATIVE_CLASSIFIERS = ("electricity.consumption", "gas.consumption")
CUMULATIVE_STORAGE_VERSION = 1

class GlowmarktDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Glowmarkt data."""

    def __init__(self, hass: HomeAssistant, api_client: GlowmarktApiClient, tariff_config: dict[str, float], entry_id: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.api_client = api_client
        self.tariff_config = tariff_config
        self._resources: dict[str, dict[str, Any]] = {}
        self._last_readings: dict[str, DailyReading] = {}  # Cache last known good readings
        self._store: Store = Store(hass, CUMULATIVE_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_cumulative")
        self._cumulative: dict[str, dict[str, Any]] | None = None

    async def _accumulate(self, classifier: str, day: str, value: float) -> float:
        """Add `value` to classifier's running total, once per distinct day.

        Glowmarkt only ever hands us "the total for day X", never a
        continuously-increasing meter reading, but state_class
        total_increasing requires a genuine monotonic counter to behave
        correctly on the Energy dashboard. We persist one here, keyed by
        the UK-local day the value covers, and only add it the first time
        that day is seen -- the coordinator polls every 5 minutes and would
        otherwise re-add the same day's value on every poll until the
        API's window rolls over to the next day.
        """
        if self._cumulative is None:
            self._cumulative = await self._store.async_load() or {}

        entry = self._cumulative.get(classifier, {"day": None, "cumulative": 0.0})
        if entry["day"] != day:
            entry = {"day": day, "cumulative": entry["cumulative"] + value}
            self._cumulative[classifier] = entry
            await self._store.async_save(self._cumulative)
        return entry["cumulative"]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._resources:
                self._resources = await self.api_client.discover_resources()

            readings = await self.api_client.get_all_readings()

            # Merge with cached readings - only update if we got valid data
            for key, reading in readings.items():
                if reading is not None:
                    self._last_readings[key] = reading
                    _LOGGER.debug("Updated %s to %.3f (day %s)", key, reading.value, reading.day)
                elif key in self._last_readings:
                    _LOGGER.debug("Keeping cached value for %s: %.3f (API returned None)",
                        key, self._last_readings[key].value)

            # Use cached readings for the data
            merged_readings = {k: self._last_readings.get(k) for k in readings.keys()}

            values = {k: (r.value if r is not None else None) for k, r in merged_readings.items()}
            cumulative_readings: dict[str, float | None] = {}
            for classifier in CUMULATIVE_CLASSIFIERS:
                reading = merged_readings.get(classifier)
                cumulative_readings[classifier] = (
                    await self._accumulate(classifier, reading.day, reading.value)
                    if reading is not None else None
                )

            data: dict[str, Any] = {"readings": values, "cumulative_readings": cumulative_readings, "resources": self._resources, "costs": {}}

            elec = values.get("electricity.consumption")
            if elec is not None:
                elec_rate = self.tariff_config.get("electricity_rate", 0)
                elec_standing = self.tariff_config.get("electricity_standing_charge", 0)
                data["costs"]["electricity"] = round((elec * elec_rate) + elec_standing, 2)

            gas = values.get("gas.consumption")
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
