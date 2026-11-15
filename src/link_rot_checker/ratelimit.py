"""Per-host rate limiting and concurrency limits."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

Sleep = Callable[[float], Awaitable[None]]


class HostRateLimiter:
    """Spaces requests to the same host at least ``1 / rate`` seconds apart.

    ``rate`` is requests per second per host (0 = unlimited). ``per_host`` caps the number of requests in flight to
    one host. ``set_interval`` raises the spacing for a host (robots.txt ``Crawl-delay``) and ``penalize`` pushes the
    next slot back (``Retry-After``).
    """

    def __init__(
        self,
        rate: float = 5.0,
        per_host: int = 2,
        *,
        overrides: dict[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate < 0:
            raise ValueError("rate must be >= 0")
        if per_host < 1:
            raise ValueError("per_host must be >= 1")
        self.default_interval = 1.0 / rate if rate else 0.0
        self.per_host = per_host
        self._intervals: dict[str, float] = {h.lower(): (1.0 / r if r else 0.0) for h, r in (overrides or {}).items()}
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._clock = clock
        self._sleep = sleep
        self.waited: float = 0.0

    def interval(self, host: str) -> float:
        return self._intervals.get(host, self.default_interval)

    def set_interval(self, host: str, seconds: float) -> None:
        """Use at least ``seconds`` between requests to ``host``."""
        host = host.lower()
        self._intervals[host] = max(self.interval(host), seconds)

    def penalize(self, host: str, seconds: float) -> None:
        host = host.lower()
        self._next[host] = max(self._next.get(host, 0.0), self._clock() + seconds)

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[None]:
        host = host.lower()
        sem = self._sems.setdefault(host, asyncio.Semaphore(self.per_host))
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with sem:
            async with lock:
                now = self._clock()
                wait = self._next.get(host, 0.0) - now
                if wait > 0:
                    self.waited += wait
                    await self._sleep(wait)
                    now = self._clock()
                self._next[host] = max(now, self._next.get(host, 0.0)) + self.interval(host)
            yield
