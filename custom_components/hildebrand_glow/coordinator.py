"""Data update coordinator for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
        self._entry_id = entry_id
        self._resources: dict[str, dict[str, Any]] = {}
        self._last_readings: dict[str, DailyReading] = {}  # Cache last known good readings
        self._store: Store = Store(hass, CUMULATIVE_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_cumulative")
        self._cumulative: dict[str, Any] | None = None

    def _entity_id_for(self, classifier: str) -> str | None:
        registry = er.async_get(self.hass)
        unique_id = f"{self._entry_id}_{classifier}"
        return registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    @staticmethod
    def _metadata_for(entity_id: str) -> StatisticMetaData:
        return StatisticMetaData(
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=None,
            source="recorder",
            statistic_id=entity_id,
            unit_class=None,
            unit_of_measurement="kWh",
        )

    def _import_hourly_statistics(self, entity_id: str, reading: DailyReading, baseline: float) -> float:
        """Replace this entity's long-term stats for `reading`'s day with its
        real hour-by-hour breakdown, instead of leaving whatever the normal
        state-based compiler recorded there (a single lump on whichever hour
        the coordinator happened to poll and notice the new day).

        Returns the running cumulative total after this day, which becomes
        the baseline for the next day.
        """
        hourly: dict[datetime, float] = {}
        for ts, value in reading.intervals:
            hour_start = ts.replace(minute=0, second=0, microsecond=0)
            hourly[hour_start] = hourly.get(hour_start, 0.0) + value

        running = baseline
        stats: list[StatisticData] = []
        for hour_start in sorted(hourly):
            running += hourly[hour_start]
            stats.append(StatisticData(start=hour_start, state=round(hourly[hour_start], 3), sum=round(running, 3)))

        async_import_statistics(self.hass, self._metadata_for(entity_id), stats)
        return round(running, 3)

    async def _accumulate(self, classifier: str, reading: DailyReading) -> float:
        """Add one day's reading to classifier's persisted running total,
        importing its real hourly breakdown at the same time -- once per
        distinct day, since the coordinator polls every 5 minutes and would
        otherwise redo this on every poll until the API's window rolls over
        to the next day.
        """
        if self._cumulative is None:
            self._cumulative = await self._store.async_load() or {}

        entry = self._cumulative.get(classifier, {"day": None, "cumulative": 0.0})
        if entry["day"] == reading.day:
            return entry["cumulative"]

        entity_id = self._entity_id_for(classifier)
        if entity_id is None:
            # Entity not registered yet (e.g. the very first refresh, before
            # platforms are forwarded) -- fall back to a plain add for now
            # and let the hourly breakdown be corrected by the backfill pass
            # once the entity exists.
            _LOGGER.debug("No entity registered yet for %s, deferring hourly import", classifier)
            new_cumulative = round(entry["cumulative"] + reading.value, 3)
        else:
            new_cumulative = self._import_hourly_statistics(entity_id, reading, entry["cumulative"])

        self._cumulative[classifier] = {"day": reading.day, "cumulative": new_cumulative}
        await self._store.async_save(self._cumulative)
        return new_cumulative

    async def _async_backfill_history(self) -> None:
        """One-time import of every available day of real hourly data, so
        existing Energy dashboard history looks right immediately instead of
        only newly-arriving days getting the accurate breakdown. How far
        back this reaches depends entirely on how much history Glowmarkt
        actually has for the account (see api.get_available_daily_readings).
        """
        if self._cumulative is None:
            self._cumulative = await self._store.async_load() or {}
        if self._cumulative.get("_backfilled"):
            return

        by_classifier = await self.api_client.get_available_readings()
        for classifier in CUMULATIVE_CLASSIFIERS:
            entity_id = self._entity_id_for(classifier)
            if entity_id is None:
                _LOGGER.debug("No entity registered yet for %s, deferring backfill", classifier)
                return  # retry the whole backfill next poll, once entities exist

            baseline = 0.0
            entry = self._cumulative.get(classifier, {"day": None, "cumulative": 0.0})
            for reading in by_classifier.get(classifier, []):
                baseline = self._import_hourly_statistics(entity_id, reading, baseline)
                entry = {"day": reading.day, "cumulative": baseline}
            self._cumulative[classifier] = entry

            # The normal state-based compiler may already have written stale
            # values for today's hours so far (using whatever flat total the
            # entity showed before this backfill raised its running total).
            # Overwrite the whole of today-so-far with flat, zero-usage
            # points at the corrected baseline, so the dashboard doesn't show
            # a bogus dip/spike between the backfilled history and the
            # currently-forming (not yet complete) day.
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            hours_elapsed_today = int((current_hour - today_start).total_seconds() // 3600) + 1
            gap_stats = [
                StatisticData(start=today_start + timedelta(hours=h), state=0.0, sum=entry["cumulative"])
                for h in range(hours_elapsed_today)
            ]
            async_import_statistics(self.hass, self._metadata_for(entity_id), gap_stats)

        self._cumulative["_backfilled"] = True
        await self._store.async_save(self._cumulative)
        _LOGGER.info(
            "Backfilled hourly statistics history: %s",
            ", ".join(f"{c}={len(r)} day(s)" for c, r in by_classifier.items())
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._resources:
                self._resources = await self.api_client.discover_resources()

            await self._async_backfill_history()

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
                    await self._accumulate(classifier, reading) if reading is not None else None
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
