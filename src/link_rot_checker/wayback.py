"""Wayback Machine availability API client.

API (https://archive.org/help/wayback_api.php)::

    GET https://archive.org/wayback/available?url=example.com[&timestamp=YYYYMMDDhhmmss]

    {"archived_snapshots": {"closest": {"available": true, "url": "http://web.archive.org/web/...",
                                        "timestamp": "20060101064348", "status": "200"}}}

An empty ``archived_snapshots`` object means there is no capture. Without ``timestamp`` the most recent capture
is returned; with it, the capture closest to that time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from link_rot_checker.fetcher import DEFAULT_USER_AGENT
from link_rot_checker.ratelimit import HostRateLimiter

log = logging.getLogger("link_rot_checker")

API_URL = "https://archive.org/wayback/available"
_TIMESTAMP = re.compile(r"^\d{1,14}$")


@dataclass(frozen=True)
class Snapshot:
    url: str
    timestamp: str
    status: str


def parse_availability(data: object) -> Snapshot | None:
    """Return the closest usable (HTTP 200) capture from an API answer."""
    if not isinstance(data, dict):
        return None
    snapshots = data.get("archived_snapshots")
    if not isinstance(snapshots, dict):
        return None
    closest = snapshots.get("closest")
    if not isinstance(closest, dict) or not closest.get("available"):
        return None
    url = closest.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    status = str(closest.get("status", ""))
    if status and not status.startswith("2"):
        return None
    if url.startswith("http://web.archive.org/"):
        url = "https://" + url[len("http://") :]
    return Snapshot(url=url, timestamp=str(closest.get("timestamp", "")), status=status or "200")


class WaybackClient:
    """Looks up archived copies, one request per URL, cached in memory and optionally on disk."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timestamp: str | None = None,
        rate: float = 1.0,
        cache_file: Path | None = None,
        timeout: float = 20.0,
        retries: int = 1,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if timestamp is not None and not _TIMESTAMP.match(timestamp):
            raise ValueError("Wayback timestamp must be 1 to 14 digits (YYYYMMDDhhmmss)")
        self.client = client
        self.timestamp = timestamp
        self.limiter = HostRateLimiter(rate=rate, per_host=2)
        self.cache_file = cache_file
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.lookups = 0
        self.errors: list[str] = []
        self._cache: dict[str, Snapshot | None] = {}
        if cache_file and cache_file.exists():
            self._load_cache(cache_file)

    def _cache_key(self, url: str) -> str:
        return f"{self.timestamp or ''}|{url}"

    def _load_cache(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable Wayback cache %s: %s", path, exc)
            return
        if isinstance(raw, dict):
            for key, value in raw.items():
                if value is None:
                    self._cache[key] = None
                elif isinstance(value, dict) and {"url", "timestamp", "status"} <= value.keys():
                    self._cache[key] = Snapshot(str(value["url"]), str(value["timestamp"]), str(value["status"]))

    def save_cache(self) -> None:
        if not self.cache_file:
            return
        data = {k: (asdict(v) if v else None) for k, v in self._cache.items()}
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    async def lookup(self, url: str) -> Snapshot | None:
        key = self._cache_key(url)
        if key in self._cache:
            return self._cache[key]
        params = {"url": url}
        if self.timestamp:
            params["timestamp"] = self.timestamp
        for attempt in range(self.retries + 1):
            try:
                async with self.limiter.slot("archive.org"):
                    self.lookups += 1
                    response = await self.client.get(
                        API_URL,
                        params=params,
                        headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                        timeout=self.timeout,
                        follow_redirects=True,
                    )
            except httpx.HTTPError as exc:
                if attempt < self.retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                self.errors.append(f"{url}: {exc}")
                return None  # not cached: try again next run
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code != 200:
                self.errors.append(f"{url}: HTTP {response.status_code}")
                return None
            try:
                snapshot = parse_availability(response.json())
            except ValueError:
                self.errors.append(f"{url}: invalid JSON from the availability API")
                return None
            self._cache[key] = snapshot
            return snapshot
        return None  # pragma: no cover
