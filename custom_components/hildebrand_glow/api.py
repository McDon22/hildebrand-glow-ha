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
        self._virtual_entity_id: str | None = None
        self._resources: dict[str, dict[str, Any]] = {}

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

    async def discover_resources(self, virtual_entity_id: str | None = None) -> dict[str, dict[str, Any]]:
        """Discover resources for a specific virtual entity, or all if not specified."""
        await self._ensure_authenticated()
        
        if virtual_entity_id:
            # Use specific virtual entity
            ve_ids = [virtual_entity_id]
        else:
            # Get all virtual entities
            virtual_entities = await self.get_virtual_entities()
            if not virtual_entities:
                return {}
            ve_ids = [ve.get("veId") for ve in virtual_entities if ve.get("veId")]
        
        self._resources = {}
        for ve_id in ve_ids:
            self._virtual_entity_id = ve_id
            try:
                async with self._session.get(f"{GLOWMARKT_API_BASE}/virtualentity/{ve_id}/resources", headers=self._get_headers()) as response:
                    response.raise_for_status()
                    data = await response.json()
                    resources = data.get("resources", [])
                    for resource in resources:
                        resource_id = resource.get("resourceId")
                        classifier = resource.get("classifier")
                        if resource_id and classifier:
                            self._resources[classifier] = {"resource_id": resource_id, "name": resource.get("name", classifier), "classifier": classifier, "base_unit": resource.get("baseUnit", "")}
                            _LOGGER.debug("Found resource: %s (%s)", classifier, resource_id)
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

    async def get_all_readings(self) -> dict[str, float | None]:
        if not self._resources:
            await self.discover_resources()
        readings = {}
        for classifier, resource in self._resources.items():
            readings[classifier] = await self.get_daily_reading(resource["resource_id"])
        return readings

    @property
    def resources(self) -> dict[str, dict[str, Any]]:
        return self._resources

    async def test_connection(self) -> bool:
        try:
            await self.authenticate()
            await self.discover_resources()
            return len(self._resources) > 0
        except (GlowmarktAuthError, GlowmarktApiError):
            return False
