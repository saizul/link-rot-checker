from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakeweb import demo_site, wayback_api
from link_rot_checker.config import Settings
from link_rot_checker.scan import run_scan


async def _no_sleep(_seconds: float) -> None:
    return None


def fast_settings(**overrides) -> Settings:
    base = {"rate_per_host": 0, "wayback_rate": 0, "backoff": 0, "retries": 1, "timeout": 5, "concurrency": 4}
    base.update(overrides)
    return Settings(**base)


def scan(web, settings: Settings, wayback=None, **kwargs):
    return asyncio.run(
        run_scan(settings, transport=web.transport(), wayback_transport=wayback, sleep=_no_sleep, **kwargs)
    )


@pytest.fixture
def site():
    return demo_site()


@pytest.fixture
def wayback():
    return wayback_api({"https://ext.test/gone": "http://web.archive.org/web/20200101000000/https://ext.test/gone"})


@pytest.fixture
def demo_result(site, wayback):
    return scan(site, fast_settings(start_urls=["https://example.com/"]), wayback)
