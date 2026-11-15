import asyncio

import pytest

from link_rot_checker.ratelimit import HostRateLimiter


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def test_spacing_per_host():
    clock = Clock()
    limiter = HostRateLimiter(rate=2, per_host=1, clock=clock, sleep=clock.sleep)

    async def go():
        for _ in range(3):
            async with limiter.slot("a.test"):
                pass
        async with limiter.slot("b.test"):
            pass

    asyncio.run(go())
    assert clock.sleeps == [0.5, 0.5]  # b.test is not delayed by a.test


def test_unlimited_rate_never_sleeps():
    clock = Clock()
    limiter = HostRateLimiter(rate=0, clock=clock, sleep=clock.sleep)

    async def go():
        for _ in range(5):
            async with limiter.slot("a.test"):
                pass

    asyncio.run(go())
    assert clock.sleeps == []


def test_override_crawl_delay_and_penalty():
    clock = Clock()
    limiter = HostRateLimiter(rate=10, overrides={"slow.test": 1}, clock=clock, sleep=clock.sleep)
    assert limiter.interval("slow.test") == 1.0
    limiter.set_interval("a.test", 2.0)
    limiter.set_interval("a.test", 0.5)  # never lowers
    assert limiter.interval("a.test") == 2.0
    limiter.penalize("b.test", 7)

    async def go():
        async with limiter.slot("b.test"):
            pass

    asyncio.run(go())
    assert clock.sleeps == [7.0]


def test_per_host_concurrency():
    limiter = HostRateLimiter(rate=0, per_host=2)
    active = 0
    peak = 0

    async def worker():
        nonlocal active, peak
        async with limiter.slot("a.test"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    async def go():
        await asyncio.gather(*(worker() for _ in range(6)))

    asyncio.run(go())
    assert peak == 2


def test_invalid_arguments():
    with pytest.raises(ValueError):
        HostRateLimiter(rate=-1)
    with pytest.raises(ValueError):
        HostRateLimiter(per_host=0)
