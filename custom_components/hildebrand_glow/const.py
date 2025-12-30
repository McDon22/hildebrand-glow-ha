"""Constants for the Hildebrand Glow integration."""
from __future__ import annotations
from datetime import timedelta
from typing import Final

DOMAIN: Final = "hildebrand_glow"
GLOWMARKT_API_BASE: Final = "https://api.glowmarkt.com/api/v0-1"
GLOWMARKT_APP_ID: Final = "b0f1b774-a586-4f72-9edd-27ead8aa7a8d"
CONF_APP_ID: Final = "app_id"
CONF_VIRTUAL_ENTITY: Final = "virtual_entity_id"
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
DEFAULT_ELECTRICITY_RATE: Final = 0.245
DEFAULT_GAS_RATE: Final = 0.065
DEFAULT_ELECTRICITY_STANDING_CHARGE: Final = 0.45
DEFAULT_GAS_STANDING_CHARGE: Final = 0.30
CONF_ELECTRICITY_RATE: Final = "electricity_rate"
CONF_GAS_RATE: Final = "gas_rate"
CONF_ELECTRICITY_STANDING_CHARGE: Final = "electricity_standing_charge"
CONF_GAS_STANDING_CHARGE: Final = "gas_standing_charge"
CLASSIFIER_ELECTRICITY_CONSUMPTION: Final = "electricity.consumption"
CLASSIFIER_ELECTRICITY_COST: Final = "electricity.consumption.cost"
CLASSIFIER_GAS_CONSUMPTION: Final = "gas.consumption"
CLASSIFIER_GAS_COST: Final = "gas.consumption.cost"
PLATFORMS: Final = ["sensor"]
ATTRIBUTION: Final = "Data provided by Hildebrand Technology via Glowmarkt API"
