"""Constants for the Hildebrand Glow integration."""
from __future__ import annotations
from datetime import timedelta
from typing import Final

DOMAIN: Final = "hildebrand_glow"
GLOWMARKT_API_BASE: Final = "https://api.glowmarkt.com/api/v0-1"
GLOWMARKT_APP_ID: Final = "b0f1b774-a586-4f72-9edd-27ead8aa7a8d"
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
CLASSIFIER_ELECTRICITY_CONSUMPTION: Final = "electricity.consumption"
CLASSIFIER_ELECTRICITY_COST: Final = "electricity.consumption.cost"
CLASSIFIER_GAS_CONSUMPTION: Final = "gas.consumption"
CLASSIFIER_GAS_COST: Final = "gas.consumption.cost"
PLATFORMS: Final = ["sensor"]
ATTRIBUTION: Final = "Data provided by Hildebrand Technology via Glowmarkt API"
