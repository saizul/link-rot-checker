"""Check every unique link target once."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from link_rot_checker.classify import ClassifySettings, classify, skipped
from link_rot_checker.fetcher import Fetcher
from link_rot_checker.model import Category, CheckResult, ErrorKind, FetchResult
from link_rot_checker.robots import RobotsPolicy
from link_rot_checker.wayback import WaybackClient

log = logging.getLogger("link_rot_checker")

#: Errors for which a GET retry after a failed HEAD cannot help.
_FINAL_ERRORS = frozenset({ErrorKind.DNS, ErrorKind.TLS, ErrorKind.INVALID_URL, ErrorKind.TOO_MANY_REDIRECTS})


@dataclass
class CheckSettings:
    concurrency: int = 10
    check_external: bool = True
    content_checks: bool = True
    use_head: bool = True
    wayback: bool = True
    wayback_for: frozenset[Category] = frozenset({Category.BROKEN, Category.PARKED, Category.SOFT_404})


class Checker:
    def __init__(
        self,
        fetcher: Fetcher,
        settings: CheckSettings | None = None,
        classify_settings: ClassifySettings | None = None,
        *,
        known: dict[str, FetchResult] | None = None,
        robots: RobotsPolicy | None = None,
        wayback: WaybackClient | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.settings = settings or CheckSettings()
        self.classify_settings = classify_settings or ClassifySettings()
        self.known = known or {}
        self.robots = robots
        self.wayback = wayback
        self.progress = progress
        self.head_fallbacks = 0

    async def fetch_target(self, url: str) -> FetchResult:
        """HEAD first; fall back to GET when HEAD fails or the body is needed for content checks."""
        if not self.settings.use_head:
            return await self.fetcher.fetch(url, "GET")
        head = await self.fetcher.fetch(url, "HEAD", read_body=False)
        if head.error_kind in _FINAL_ERRORS:
            return head
        if head.error_kind is not None or (head.status or 0) >= 400 or (head.status or 0) < 200:
            # Many servers answer HEAD with 403/404/405/501 or drop the connection; GET is the real test.
            self.head_fallbacks += 1
            return await self.fetcher.fetch(url, "GET")
        if self.settings.content_checks and head.is_html:
            return await self.fetcher.fetch(url, "GET")
        return head

    async def check(self, url: str, internal: bool) -> CheckResult:
        if url in self.known:
            result = classify(url, self.known[url], internal=internal, settings=self.classify_settings)
        elif not internal and not self.settings.check_external:
            return skipped(url, "external links not checked", internal=internal)
        elif internal and self.robots is not None and not await self.robots.allowed(url):
            return skipped(url, "disallowed by robots.txt", internal=internal)
        else:
            fetch = await self.fetch_target(url)
            result = classify(url, fetch, internal=internal, settings=self.classify_settings)
        if (
            self.wayback is not None
            and self.settings.wayback
            and not internal
            and result.category in self.settings.wayback_for
        ):
            snapshot = await self.wayback.lookup(url)
            if snapshot:
                result.archived_url, result.archived_timestamp = snapshot.url, snapshot.timestamp
        return result

    async def check_all(self, targets: dict[str, bool]) -> dict[str, CheckResult]:
        """Check ``{url: internal}`` with a concurrency limit; results keep the input order."""
        sem = asyncio.Semaphore(max(1, self.settings.concurrency))
        done = 0
        total = len(targets)

        async def one(url: str, internal: bool) -> CheckResult:
            nonlocal done
            async with sem:
                try:
                    return await self.check(url, internal)
                except Exception as exc:  # a bug in one check must not stop the run
                    log.exception("error while checking %s", url)
                    return CheckResult(
                        url=url, category=Category.SKIPPED, reason=f"checker error: {exc}", internal=internal
                    )
                finally:
                    done += 1
                    if self.progress:
                        self.progress(done, total)

        results = await asyncio.gather(*(one(u, i) for u, i in targets.items()))
        return {r.url: r for r in results}
