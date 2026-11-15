"""robots.txt handling (RFC 9309) for crawled pages."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from link_rot_checker.fetcher import Fetcher

log = logging.getLogger("link_rot_checker")

PRODUCT_TOKEN = "link-rot-checker"


@dataclass
class RobotsRules:
    origin: str
    parser: RobotFileParser | None
    allow_all: bool = False
    disallow_all: bool = False
    note: str = ""

    def allowed(self, url: str, agent: str = PRODUCT_TOKEN) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all or self.parser is None:
            return True
        return self.parser.can_fetch(agent, url)

    def crawl_delay(self, agent: str = PRODUCT_TOKEN) -> float | None:
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(agent)
        return float(delay) if delay is not None else None

    def sitemaps(self) -> list[str]:
        if self.parser is None:
            return []
        return list(self.parser.site_maps() or [])


def parse_robots(origin: str, text: str) -> RobotsRules:
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return RobotsRules(origin, parser)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class RobotsPolicy:
    """Fetches and caches robots.txt per origin.

    Following RFC 9309: a 4xx answer means everything is allowed, a 5xx answer or a network error means nothing
    is allowed (the site is "unreachable"). ``Crawl-delay`` is applied to the rate limiter.
    """

    def __init__(self, fetcher: Fetcher, *, agent: str = PRODUCT_TOKEN, enabled: bool = True) -> None:
        self.fetcher = fetcher
        self.agent = agent
        self.enabled = enabled
        self._cache: dict[str, RobotsRules] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def rules_for(self, url: str) -> RobotsRules:
        origin = origin_of(url)
        if origin in self._cache:
            return self._cache[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                self._cache[origin] = await self._load(origin)
        return self._cache[origin]

    async def _load(self, origin: str) -> RobotsRules:
        result = await self.fetcher.fetch(origin + "/robots.txt", "GET")
        if result.error_kind is not None:
            log.warning("robots.txt at %s could not be fetched (%s); not crawling it", origin, result.error)
            return RobotsRules(origin, None, disallow_all=True, note=f"robots.txt unreachable: {result.error}")
        status = result.status or 0
        if 400 <= status < 500:
            return RobotsRules(origin, None, allow_all=True, note=f"robots.txt HTTP {status}")
        if status >= 500:
            log.warning("robots.txt at %s answered HTTP %s; not crawling it", origin, status)
            return RobotsRules(origin, None, disallow_all=True, note=f"robots.txt HTTP {status}")
        rules = parse_robots(origin, result.body)
        delay = rules.crawl_delay(self.agent)
        if delay:
            host = urlsplit(origin).hostname or ""
            self.fetcher.limiter.set_interval(host, min(delay, 60.0))
            log.info("robots.txt Crawl-delay for %s: %ss", host, delay)
        return rules

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        return (await self.rules_for(url)).allowed(url, self.agent)
