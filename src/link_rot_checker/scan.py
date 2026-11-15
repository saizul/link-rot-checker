"""Run a whole scan: sitemap, crawl, check, Wayback lookups."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

from link_rot_checker.checker import Checker, CheckSettings
from link_rot_checker.classify import ClassifySettings, skipped
from link_rot_checker.config import Settings
from link_rot_checker.crawler import Crawler, CrawlOutput, CrawlSettings
from link_rot_checker.fetcher import Fetcher, FetchSettings, make_client
from link_rot_checker.model import Link, ScanResult
from link_rot_checker.ratelimit import HostRateLimiter
from link_rot_checker.robots import RobotsPolicy
from link_rot_checker.sitemap import load_sitemaps
from link_rot_checker.urls import Scope, UrlError, normalize
from link_rot_checker.wayback import WaybackClient

log = logging.getLogger("link_rot_checker")
Progress = Callable[[str, int, int], None]


class ScanError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def run_scan(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    wayback_transport: httpx.AsyncBaseTransport | None = None,
    sleep=None,
    progress: Progress | None = None,
    urls_to_check: list[str] | None = None,
) -> ScanResult:
    """Crawl ``settings.start_urls`` (and sitemaps) and check every link found.

    With ``urls_to_check``, nothing is crawled: those URLs are checked directly (``check`` command).
    ``transport`` lets tests (and callers) plug in an :class:`httpx.MockTransport`.
    """
    settings.validate()
    started_at, t0 = _now(), time.perf_counter()
    limiter_kwargs = {"sleep": sleep} if sleep else {}
    limiter = HostRateLimiter(
        settings.rate_per_host, settings.per_host_concurrency, overrides=settings.host_rates, **limiter_kwargs
    )
    fetch_settings = FetchSettings(
        timeout=settings.timeout,
        retries=settings.retries,
        backoff=settings.backoff,
        max_redirects=settings.max_redirects,
        max_body_bytes=settings.max_body_bytes,
        user_agent=settings.user_agent,
        extra_headers=settings.extra_headers(),
    )
    ignore = [re.compile(p) for p in settings.ignore]
    async with make_client(
        timeout=settings.timeout,
        verify=settings.verify_tls,
        transport=transport,
        max_connections=max(settings.concurrency * 2, 10),
    ) as client:
        fetcher = Fetcher(client, fetch_settings, limiter, **({"sleep": sleep} if sleep else {}))
        robots = RobotsPolicy(fetcher, enabled=settings.respect_robots)
        start_urls: list[str] = []
        for raw in settings.start_urls:
            try:
                start_urls.append(normalize(raw))
            except UrlError as exc:
                raise ScanError(f"invalid start URL {raw!r}: {exc}") from exc
        sitemap_urls: list[str] = []
        for raw in settings.sitemaps:
            try:
                sitemap_urls.append(normalize(raw))
            except UrlError as exc:
                raise ScanError(f"invalid sitemap URL {raw!r}: {exc}") from exc

        crawl = CrawlOutput()
        targets: dict[str, bool] = {}
        if urls_to_check is not None:
            scope = Scope.for_start_urls(
                start_urls, include_subdomains=settings.include_subdomains, extra_hosts=settings.extra_hosts
            )
            for raw in urls_to_check:
                try:
                    url = normalize(raw)
                except UrlError as exc:
                    log.warning("skipping %r: %s", raw, exc)
                    continue
                crawl.links.append(Link(source="(input)", target=url, internal=scope.is_internal(url)))
        else:
            scope = Scope.for_start_urls(
                start_urls + sitemap_urls,
                include_subdomains=settings.include_subdomains,
                extra_hosts=settings.extra_hosts,
                include=settings.include,
                exclude=settings.exclude,
            )
            if sitemap_urls:
                sm = await load_sitemaps(fetcher, sitemap_urls, max_urls=settings.max_pages)
                if not sm.urls and not start_urls:
                    raise ScanError("no URLs found in the sitemap(s): " + "; ".join(sm.errors or ["empty"]))
                start_urls.extend(u for u in sm.urls if u not in start_urls and scope.is_internal(u))
            if not start_urls:
                raise ScanError("nothing to scan: give a start URL or --sitemap")
            crawl_settings = CrawlSettings(
                max_pages=settings.max_pages if settings.crawl else len(start_urls),
                max_depth=settings.max_depth if settings.crawl else 0,
                concurrency=settings.concurrency,
                check_assets=settings.check_assets,
                follow_nofollow=settings.follow_nofollow,
            )
            crawler = Crawler(
                fetcher,
                scope,
                robots,
                crawl_settings,
                progress=(lambda d, t: progress("crawl", d, t)) if progress else None,
            )
            crawl = await crawler.crawl(start_urls)
            if crawl.pages and not crawl.links and all(p.status is None for p in crawl.pages.values()):
                first = next(iter(crawl.pages.values()))
                raise ScanError(f"could not fetch {first.url}: {first.error or 'no response'}")

        links = [link for link in crawl.links if not any(p.search(link.target) for p in ignore)]
        for link in links:
            targets.setdefault(link.target, link.internal)

        wayback = None
        if settings.wayback and settings.check_external:
            wb_client = httpx.AsyncClient(transport=wayback_transport) if wayback_transport else client
            wayback = WaybackClient(
                wb_client,
                timestamp=settings.wayback_timestamp,
                rate=settings.wayback_rate,
                cache_file=Path(settings.wayback_cache) if settings.wayback_cache else None,
                user_agent=settings.user_agent,
            )
        checker = Checker(
            fetcher,
            CheckSettings(
                concurrency=settings.concurrency,
                check_external=settings.check_external,
                content_checks=settings.content_checks,
                use_head=settings.use_head,
                wayback=settings.wayback,
            ),
            ClassifySettings(
                chain_threshold=settings.chain_threshold,
                soft_404=settings.soft_404,
                parked=settings.parked,
                blocked_statuses=set(settings.blocked_statuses),
            ),
            known=crawl.fetches,
            robots=robots if settings.respect_robots else None,
            wayback=wayback,
            progress=(lambda d, t: progress("check", d, t)) if progress else None,
        )
        results = await checker.check_all(targets)
        for url, reason in crawl.skipped.items():
            if url in results and url not in crawl.fetches:
                results[url] = skipped(url, reason, internal=True)
        if wayback is not None:
            wayback.save_cache()
            if wayback_transport:
                await wayback.client.aclose()
        settings_dict = settings.public_dict()
        settings_dict["requests"] = fetcher.requests
        settings_dict["head_fallbacks"] = checker.head_fallbacks
        settings_dict["wayback_lookups"] = wayback.lookups if wayback else 0
        settings_dict["non_http_links"] = crawl.non_http
    return ScanResult(
        start_urls=start_urls if urls_to_check is None else [],
        pages=crawl.pages,
        links=links,
        results=results,
        skipped=crawl.skipped,
        started_at=started_at,
        finished_at=_now(),
        duration_s=round(time.perf_counter() - t0, 2),
        settings=settings_dict,
    )
