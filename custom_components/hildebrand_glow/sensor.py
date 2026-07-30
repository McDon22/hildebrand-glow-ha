"""Sensor platform for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import ATTRIBUTION, DOMAIN, CLASSIFIER_ELECTRICITY_CONSUMPTION, CLASSIFIER_ELECTRICITY_COST, CLASSIFIER_GAS_CONSUMPTION, CLASSIFIER_GAS_COST
from .coordinator import GlowmarktDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    CLASSIFIER_ELECTRICITY_CONSUMPTION: {"name": "Electricity Consumption", "icon": "mdi:flash", "device_class": SensorDeviceClass.ENERGY, "state_class": SensorStateClass.TOTAL_INCREASING, "native_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR, "data_key": "cumulative_readings", "reading_key": CLASSIFIER_ELECTRICITY_CONSUMPTION},
    CLASSIFIER_GAS_CONSUMPTION: {"name": "Gas Consumption", "icon": "mdi:fire", "device_class": SensorDeviceClass.ENERGY, "state_class": SensorStateClass.TOTAL_INCREASING, "native_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR, "data_key": "cumulative_readings", "reading_key": CLASSIFIER_GAS_CONSUMPTION},
    f"{CLASSIFIER_ELECTRICITY_COST}_api": {"name": "Electricity Cost (API)", "icon": "mdi:currency-gbp", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.TOTAL, "native_unit_of_measurement": "GBP", "data_key": "readings", "reading_key": CLASSIFIER_ELECTRICITY_COST, "convert_pence": True},
    f"{CLASSIFIER_GAS_COST}_api": {"name": "Gas Cost (API)", "icon": "mdi:currency-gbp", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.TOTAL, "native_unit_of_measurement": "GBP", "data_key": "readings", "reading_key": CLASSIFIER_GAS_COST, "convert_pence": True},
    "electricity_daily_cost": {"name": "Electricity Daily Cost", "icon": "mdi:currency-gbp", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.TOTAL, "native_unit_of_measurement": "GBP", "data_key": "costs", "reading_key": "electricity"},
    "gas_daily_cost": {"name": "Gas Daily Cost", "icon": "mdi:currency-gbp", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.TOTAL, "native_unit_of_measurement": "GBP", "data_key": "costs", "reading_key": "gas"},
    "total_daily_cost": {"name": "Total Daily Energy Cost", "icon": "mdi:currency-gbp", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.TOTAL, "native_unit_of_measurement": "GBP", "data_key": "costs", "reading_key": "total"},
    "daily_standing_charges": {"name": "Daily Standing Charges", "icon": "mdi:cash-clock", "device_class": SensorDeviceClass.MONETARY, "state_class": SensorStateClass.MEASUREMENT, "native_unit_of_measurement": "GBP", "data_key": "costs", "reading_key": "standing_charges_total"},
}

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: GlowmarktDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[GlowmarktSensor] = []
    await coordinator.async_config_entry_first_refresh()
    for sensor_key, description in SENSOR_DESCRIPTIONS.items():
        entities.append(GlowmarktSensor(coordinator=coordinator, sensor_key=sensor_key, description=description, entry_id=config_entry.entry_id))
    async_add_entities(entities)

class GlowmarktSensor(CoordinatorEntity[GlowmarktDataUpdateCoordinator], SensorEntity):
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: GlowmarktDataUpdateCoordinator, sensor_key: str, description: dict[str, Any], entry_id: str) -> None:
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._description = description
        self._attr_unique_id = f"{entry_id}_{sensor_key}"
        self._attr_name = description["name"]
        self._attr_icon = description.get("icon")
        self._attr_device_class = description.get("device_class")
        self._attr_state_class = description.get("state_class")
        self._attr_native_unit_of_measurement = description.get("native_unit_of_measurement")
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Smart Meter", manufacturer="Hildebrand Technology", model="SMETS2 via Glow/Bright", configuration_url="https://glowmarkt.com/")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        data_key = self._description.get("data_key", "readings")
        reading_key = self._description.get("reading_key", "")
        data_section = self.coordinator.data.get(data_key, {})
        value = data_section.get(reading_key)
        if value is None:
            return None
        if self._description.get("convert_pence", False):
            value = round(value / 100.0, 2)
        if isinstance(value, float):
            return round(value, 3) if self._attr_device_class != SensorDeviceClass.MONETARY else round(value, 2)
        return value
