"""Glowmarkt API client for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
import aiohttp
from aiohttp import ClientError, ClientResponseError
from .const import GLOWMARKT_API_BASE, GLOWMARKT_APP_ID

_LOGGER = logging.getLogger(__name__)

# UK timezone for proper day boundaries
UK_TZ = ZoneInfo("Europe/London")

class GlowmarktAuthError(Exception):
    """Exception for authentication errors."""

class GlowmarktApiError(Exception):
    """Exception for API errors."""

class GlowmarktApiClient:
    """Async client for the Glowmarkt API."""

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._resources: dict[str, dict[str, dict[str, Any]]] = {}  # {ve_id: {classifier: resource_info}}
        self._ve_names: dict[str, str] = {}  # {ve_id: ve_name}

    async def authenticate(self) -> bool:
        headers = {"Content-Type": "application/json", "applicationId": GLOWMARKT_APP_ID}
        payload = {"username": self._username, "password": self._password}
        try:
            async with self._session.post(f"{GLOWMARKT_API_BASE}/auth", headers=headers, json=payload) as response:
                if response.status == 401:
                    raise GlowmarktAuthError("Invalid username or password")
                response.raise_for_status()
                data = await response.json()
                if data.get("valid"):
                    self._token = data["token"]
                    self._token_expiry = datetime.now() + timedelta(days=6)
                    _LOGGER.debug("Authentication successful, token expires in 6 days")
                    return True
                else:
                    raise GlowmarktAuthError("Authentication failed: invalid response")
        except ClientResponseError as err:
            raise GlowmarktAuthError(f"Authentication failed: {err}") from err
        except ClientError as err:
            raise GlowmarktApiError(f"Connection error: {err}") from err

    async def _ensure_authenticated(self) -> None:
        if self._token is None or self._token_expiry is None or datetime.now() > self._token_expiry:
            await self.authenticate()

    def _get_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "applicationId": GLOWMARKT_APP_ID, "token": self._token or ""}

    async def get_virtual_entities(self) -> list[dict[str, Any]]:
        await self._ensure_authenticated()
        try:
            async with self._session.get(f"{GLOWMARKT_API_BASE}/virtualentity", headers=self._get_headers()) as response:
                response.raise_for_status()
                data = await response.json()
                return data if isinstance(data, list) else []
        except ClientError as err:
            raise GlowmarktApiError(f"Failed to get virtual entities: {err}") from err

    async def discover_resources(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Discover resources per virtual entity. Returns {ve_id: {classifier: resource_info}}."""
        await self._ensure_authenticated()
        virtual_entities = await self.get_virtual_entities()
        if not virtual_entities:
            return {}
        self._resources = {}
        self._ve_names = {}
        for ve in virtual_entities:
            ve_id = ve.get("veId")
            if not ve_id:
                continue
            self._ve_names[ve_id] = ve.get("name", ve_id)
            self._resources[ve_id] = {}
            try:
                async with self._session.get(f"{GLOWMARKT_API_BASE}/virtualentity/{ve_id}/resources", headers=self._get_headers()) as response:
                    response.raise_for_status()
                    data = await response.json()
                    resources = data.get("resources", [])
                    for resource in resources:
                        resource_id = resource.get("resourceId")
                        classifier = resource.get("classifier")
                        if resource_id and classifier:
                            self._resources[ve_id][classifier] = {"resource_id": resource_id, "name": resource.get("name", classifier), "classifier": classifier, "base_unit": resource.get("baseUnit", "")}
                            _LOGGER.debug("Found resource: %s (%s) for VE %s", classifier, resource_id, ve_id)
            except ClientError as err:
                _LOGGER.error("Failed to get resources for %s: %s", ve_id, err)
        return self._resources

    async def get_daily_reading(self, resource_id: str) -> float | None:
        """Get daily reading by fetching 30-min intervals and summing them."""
        await self._ensure_authenticated()
        
        # Use UK timezone for proper day boundaries
        now_uk = datetime.now(UK_TZ)
        today_start_uk = now_uk.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Convert to UTC for API call (API expects UTC)
        today_start_utc = today_start_uk.astimezone(timezone.utc)
        now_utc = now_uk.astimezone(timezone.utc)
        
        _LOGGER.debug(
            "Fetching readings for %s from %s to %s (UK: %s to %s)",
            resource_id,
            today_start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            today_start_uk.strftime("%Y-%m-%d %H:%M"),
            now_uk.strftime("%H:%M")
        )
        
        try:
            # Fetch 30-minute interval data for today and sum it
            params = {
                "from": today_start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                "to": now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                "period": "PT30M",
                "offset": 0,
                "function": "sum"
            }
            _LOGGER.debug("API params: %s", params)
            
            async with self._session.get(
                f"{GLOWMARKT_API_BASE}/resource/{resource_id}/readings",
                headers=self._get_headers(),
                params=params
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
                _LOGGER.debug("API response status: %s, data points: %s", 
                    data.get("status"), 
                    len(data.get("data", [])) if data.get("data") else 0
                )
                
                if data.get("status") == "OK" and data.get("data"):
                    readings = data["data"]
                    # Log each reading for debugging
                    for reading in readings[-5:]:  # Log last 5 readings
                        ts = datetime.fromtimestamp(reading[0], tz=UK_TZ).strftime("%H:%M")
                        val = reading[1]
                        _LOGGER.debug("  %s: %s kWh", ts, val)
                    
                    # Sum all the 30-minute readings
                    total = sum(reading[1] for reading in readings if reading[1] is not None)
                    _LOGGER.info("Resource %s: summed %d readings = %.3f kWh", 
                        resource_id, len(readings), total)
                    return round(total, 3)
                else:
                    _LOGGER.warning("No data returned for %s. Status: %s, Response: %s", 
                        resource_id, data.get("status"), data)
                    return None  # Return None instead of 0 when no data
                    
        except ClientResponseError as err:
            _LOGGER.error("API error for %s: %s %s", resource_id, err.status, err.message)
            return None
        except ClientError as err:
            _LOGGER.error("Failed to get reading for %s: %s", resource_id, err)
            return None

    async def get_all_readings(self) -> dict[str, dict[str, float | None]]:
        """Fetch readings for all VEs. Returns {ve_id: {classifier: value}}."""
        if not self._resources:
            await self.discover_resources()
        readings: dict[str, dict[str, float | None]] = {}
        for ve_id, ve_resources in self._resources.items():
            readings[ve_id] = {}
            for classifier, resource in ve_resources.items():
                readings[ve_id][classifier] = await self.get_daily_reading(resource["resource_id"])
        return readings

    async def get_tariff(self, resource_id: str) -> dict[str, float | None]:
        """Get current tariff for a resource. Returns rate and standing_charge in pence."""
        await self._ensure_authenticated()
        try:
            async with self._session.get(
                f"{GLOWMARKT_API_BASE}/resource/{resource_id}/tariff",
                headers=self._get_headers()
            ) as response:
                response.raise_for_status()
                data = await response.json()
                current = data.get("current_rates", {})
                rate_obj = current.get("rate", {})
                standing_obj = current.get("standing_charge", {})
                # API may return {"value": X, "units": "pence"} or a bare number
                rate = rate_obj.get("value") if isinstance(rate_obj, dict) else rate_obj
                standing = standing_obj.get("value") if isinstance(standing_obj, dict) else standing_obj
                _LOGGER.debug("Tariff for %s: rate=%s p, standing=%s p", resource_id, rate, standing)
                return {
                    "rate": float(rate) if rate is not None else None,
                    "standing_charge": float(standing) if standing is not None else None,
                }
        except ClientResponseError as err:
            _LOGGER.warning("Could not fetch tariff for %s: %s %s", resource_id, err.status, err.message)
            return {"rate": None, "standing_charge": None}
        except ClientError as err:
            _LOGGER.warning("Connection error fetching tariff for %s: %s", resource_id, err)
            return {"rate": None, "standing_charge": None}

    async def get_all_tariffs(self) -> dict[str, dict[str, dict[str, float | None]]]:
        """Fetch tariffs for all resources. Returns {ve_id: {classifier: {rate, standing_charge}}}."""
        if not self._resources:
            await self.discover_resources()
        tariffs: dict[str, dict[str, dict[str, float | None]]] = {}
        for ve_id, ve_resources in self._resources.items():
            tariffs[ve_id] = {}
            for classifier, resource in ve_resources.items():
                tariffs[ve_id][classifier] = await self.get_tariff(resource["resource_id"])
        return tariffs

    @property
    def resources(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self._resources

    @property
    def ve_names(self) -> dict[str, str]:
        return self._ve_names

    async def test_connection(self) -> bool:
        try:
            await self.authenticate()
            await self.discover_resources()
            return any(len(ve_resources) > 0 for ve_resources in self._resources.values())
        except (GlowmarktAuthError, GlowmarktApiError):
            return False
