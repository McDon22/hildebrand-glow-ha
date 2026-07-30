"""Glowmarkt API client for Hildebrand Glow integration."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
import aiohttp
from aiohttp import ClientError, ClientResponseError
from .const import GLOWMARKT_API_BASE, GLOWMARKT_APP_ID

_LOGGER = logging.getLogger(__name__)

# UK timezone for proper day boundaries
UK_TZ = ZoneInfo("Europe/London")

# The Glowmarkt API rejects daily/hourly-aggregated queries spanning more
# than 31 days, so backfill history-discovery probes in chunks under that.
CHUNK_PROBE_DAYS = 30
CHUNK_PROBE_LIMIT = 24  # ~2 years of chunks before giving up

@dataclass
class DailyReading:
    """A single complete day's summed reading, plus its raw 30-min intervals.

    `day` identifies which UK-local calendar day (YYYY-MM-DD) `value`
    covers. Callers that need a genuine running total (e.g. total_increasing
    sensors feeding the Energy dashboard) use `day` to detect whether a
    given day's value has already been accounted for, since polling
    repeatedly within the same day must not add it again. `intervals` holds
    each 30-min reading's (UTC start, kWh) pair, used to import real
    hour-by-hour statistics instead of dumping the whole day's total into a
    single hour.
    """
    day: str
    value: float
    intervals: list[tuple[datetime, float]]

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

    async def discover_resources(self) -> dict[str, dict[str, Any]]:
        await self._ensure_authenticated()
        virtual_entities = await self.get_virtual_entities()
        if not virtual_entities:
            return {}
        self._resources = {}
        for ve in virtual_entities:
            ve_id = ve.get("veId")
            if not ve_id:
                continue
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

    async def _fetch_day_reading(self, resource_id: str, day_start_uk: datetime, day_end_uk: datetime, days_back: int | None = None) -> DailyReading | None:
        """Fetch and sum 30-min interval readings for one explicit UK-local
        day window. Returns None on any error or if the window has no real
        (non-zero) data -- callers treat both cases the same way (try a
        different window), so errors are logged here, not raised.
        """
        # Convert to UTC for API call (API expects UTC)
        day_start_utc = day_start_uk.astimezone(timezone.utc)
        day_end_utc = day_end_uk.astimezone(timezone.utc)

        params = {
            "from": day_start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": day_end_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "period": "PT30M",
            "offset": 0,
            "function": "sum"
        }
        _LOGGER.debug(
            "Fetching readings for %s (%s) from %s to %s (UK: %s to %s)",
            resource_id, f"{days_back} day(s) back" if days_back is not None else day_start_uk.date(),
            params["from"], params["to"],
            day_start_uk.strftime("%Y-%m-%d %H:%M"),
            day_end_uk.strftime("%Y-%m-%d %H:%M")
        )

        try:
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
                    valid = [r for r in data["data"] if r[1] is not None]
                    # Log each reading for debugging
                    for reading in valid[-5:]:  # Log last 5 readings
                        ts = datetime.fromtimestamp(reading[0], tz=UK_TZ).strftime("%Y-%m-%d %H:%M")
                        _LOGGER.debug("  %s: %s kWh", ts, reading[1])

                    total = sum(r[1] for r in valid)
                    if total > 0:
                        intervals = [(datetime.fromtimestamp(r[0], tz=timezone.utc), r[1]) for r in valid]
                        _LOGGER.info("Resource %s: summed %d readings (%s) = %.3f kWh",
                            resource_id, len(valid), day_start_uk.date(), total)
                        return DailyReading(day=day_start_uk.date().isoformat(), value=round(total, 3), intervals=intervals)
                    _LOGGER.debug("Resource %s (%s): only zero-valued readings", resource_id, day_start_uk.date())
                else:
                    _LOGGER.warning("No data returned for %s (%s). Status: %s, Response: %s",
                        resource_id, day_start_uk.date(), data.get("status"), data)

        except ClientResponseError as err:
            _LOGGER.error("API error for %s (%s): %s %s", resource_id, day_start_uk.date(), err.status, err.message)
        except ClientError as err:
            _LOGGER.error("Failed to get reading for %s (%s): %s", resource_id, day_start_uk.date(), err)
        return None

    async def get_daily_reading(self, resource_id: str) -> DailyReading | None:
        """Get the most recent complete day's reading by fetching 30-min
        intervals and summing them.

        Glowmarkt's API has a documented ~24-48h processing delay: the
        "today" window always comes back as a run of zero-valued
        placeholder readings, never real data. Querying only "today" (as
        this used to do) therefore reports no data forever, not just until
        data catches up -- this was the root cause of long-standing "No
        data" reports. Walk backwards day by day (starting from yesterday,
        UK-local) until a day with real non-zero data is found, since a
        single day can occasionally still be incomplete.
        """
        await self._ensure_authenticated()

        # Use UK timezone for proper day boundaries
        now_uk = datetime.now(UK_TZ)
        today_start_uk = now_uk.replace(hour=0, minute=0, second=0, microsecond=0)

        for days_back in range(1, 4):
            day_start_uk = today_start_uk - timedelta(days=days_back)
            day_end_uk = today_start_uk - timedelta(days=days_back - 1)
            reading = await self._fetch_day_reading(resource_id, day_start_uk, day_end_uk, days_back)
            if reading is not None:
                return reading

        _LOGGER.warning("No non-zero data found for %s in the last 3 days", resource_id)
        return None

    async def _find_data_start(self, resource_id: str) -> datetime | None:
        """Find the earliest UK-local day this resource has real (non-zero)
        data for.

        Smart meters only have data from whenever the meter/account was
        commissioned or linked, which varies wildly per user -- some
        accounts have years of history, others just a couple of weeks.
        Rather than guess a fixed backfill window, probe backwards in
        ~30-day chunks (the API rejects wider daily/hourly ranges in a
        single call) using the cheap per-day `function=sum` aggregate, and
        stop once two consecutive chunks come back fully empty -- a strong
        signal we've gone past where data starts. Capped at
        CHUNK_PROBE_LIMIT chunks (~2 years) so a genuinely data-free
        resource doesn't probe forever.
        """
        now_uk = datetime.now(UK_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        earliest: datetime | None = None
        consecutive_empty = 0
        for chunk in range(CHUNK_PROBE_LIMIT):
            chunk_end_uk = now_uk - timedelta(days=chunk * CHUNK_PROBE_DAYS)
            chunk_start_uk = chunk_end_uk - timedelta(days=CHUNK_PROBE_DAYS)
            chunk_start_utc = chunk_start_uk.astimezone(timezone.utc)
            chunk_end_utc = chunk_end_uk.astimezone(timezone.utc)
            try:
                async with self._session.get(
                    f"{GLOWMARKT_API_BASE}/resource/{resource_id}/readings",
                    headers=self._get_headers(),
                    params={
                        "from": chunk_start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                        "to": chunk_end_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                        "period": "P1D",
                        "offset": 0,
                        "function": "sum"
                    }
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
            except ClientError as err:
                _LOGGER.error("Failed to probe %s (%s to %s): %s", resource_id, chunk_start_uk.date(), chunk_end_uk.date(), err)
                break

            rows = data.get("data", []) if data.get("status") == "OK" else []
            nonzero = [r for r in rows if r[1] is not None and r[1] > 0]
            _LOGGER.debug("Probed %s chunk %d (%s to %s): %d/%d non-zero", resource_id, chunk, chunk_start_uk.date(), chunk_end_uk.date(), len(nonzero), len(rows))
            if nonzero:
                earliest = datetime.fromtimestamp(min(r[0] for r in nonzero), tz=UK_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
        return earliest

    async def get_available_daily_readings(self, resource_id: str) -> list[DailyReading]:
        """Fetch every complete day this resource has real data for, oldest
        first -- however far back that turns out to be. Used for a one-time
        backfill of accurate hourly statistics history on first setup.
        """
        await self._ensure_authenticated()

        start_uk = await self._find_data_start(resource_id)
        if start_uk is None:
            return []

        now_uk = datetime.now(UK_TZ)
        today_start_uk = now_uk.replace(hour=0, minute=0, second=0, microsecond=0)

        readings: list[DailyReading] = []
        day_start_uk = start_uk
        while day_start_uk < today_start_uk:
            day_end_uk = day_start_uk + timedelta(days=1)
            reading = await self._fetch_day_reading(resource_id, day_start_uk, day_end_uk)
            if reading is not None:
                readings.append(reading)
            day_start_uk = day_end_uk
        return readings

    async def get_all_readings(self) -> dict[str, DailyReading | None]:
        if not self._resources:
            await self.discover_resources()
        readings = {}
        for classifier, resource in self._resources.items():
            readings[classifier] = await self.get_daily_reading(resource["resource_id"])
        return readings

    async def get_available_readings(self, classifiers: set[str] | None = None) -> dict[str, list[DailyReading]]:
        """Get every available day of history for each discovered resource.

        `classifiers` restricts this to a subset (e.g. just the consumption
        classifiers backfill actually needs) -- without it, this would also
        walk the full history of cost resources nobody asked for, which can
        be needlessly slow (one account seen with real cost-resource data
        going back over 9 months, vs. under 2 weeks of consumption data).
        """
        if not self._resources:
            await self.discover_resources()
        result = {}
        for classifier, resource in self._resources.items():
            if classifiers is not None and classifier not in classifiers:
                continue
            result[classifier] = await self.get_available_daily_readings(resource["resource_id"])
        return result

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
