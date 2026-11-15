"""Breadth-first, same-host crawler that collects every link with its source page and anchor text."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from link_rot_checker.fetcher import Fetcher
from link_rot_checker.html_extract import parse_html
from link_rot_checker.model import FetchResult, Link, PageInfo
from link_rot_checker.robots import RobotsPolicy
from link_rot_checker.urls import Scope, UrlError, host_of, is_checkable, looks_like_page, normalize

log = logging.getLogger("link_rot_checker")


@dataclass
class CrawlSettings:
    max_pages: int = 500
    max_depth: int = 10
    concurrency: int = 10
    check_assets: bool = True
    follow_nofollow: bool = True
    follow_redirect_hosts: bool = True


@dataclass
class CrawlOutput:
    pages: dict[str, PageInfo] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    fetches: dict[str, FetchResult] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    non_http: int = 0


class Crawler:
    def __init__(
        self,
        fetcher: Fetcher,
        scope: Scope,
        robots: RobotsPolicy | None = None,
        settings: CrawlSettings | None = None,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.scope = scope
        self.robots = robots
        self.settings = settings or CrawlSettings()
        self.progress = progress
        self.out = CrawlOutput()
        self._seen: set[str] = set()
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._crawled = 0

    def _enqueue(self, url: str, depth: int) -> None:
        if url in self._seen or len(self._seen) >= self.settings.max_pages:
            return
        self._seen.add(url)
        self._queue.put_nowait((url, depth))

    async def crawl(self, start_urls: list[str]) -> CrawlOutput:
        for url in start_urls:
            self._enqueue(url, 0)
        workers = [asyncio.create_task(self._worker()) for _ in range(max(1, self.settings.concurrency))]
        try:
            await self._queue.join()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        return self.out

    async def _worker(self) -> None:
        while True:
            url, depth = await self._queue.get()
            try:
                await self._visit(url, depth)
            except Exception as exc:  # never let one page stop the crawl
                log.exception("error while crawling %s", url)
                self.out.pages[url] = PageInfo(url=url, status=None, depth=depth, error=f"crawler error: {exc}")
            finally:
                self._crawled += 1
                if self.progress:
                    self.progress(self._crawled, len(self._seen))
                self._queue.task_done()

    async def _visit(self, url: str, depth: int) -> None:
        if self.robots is not None and not await self.robots.allowed(url):
            self.out.skipped[url] = "disallowed by robots.txt"
            return
        fetch = await self.fetcher.fetch(url, "GET")
        self.out.fetches[url] = fetch
        page = PageInfo(url=url, status=fetch.status, depth=depth, final_url=fetch.final_url, error=fetch.error)
        self.out.pages[url] = page
        final = fetch.final_url or url
        if final != url:
            if depth == 0 and self.settings.follow_redirect_hosts and not self.scope.is_internal(final):
                # example.com -> www.example.com: the start URL's redirect target belongs to the site too.
                log.info("start URL redirects to %s; adding it to the crawl scope", host_of(final))
                self.scope.hosts.add(host_of(final))
            if not self.scope.is_internal(final):
                return
            if final in self._seen:
                return  # the target page is (or will be) crawled under its own URL
            self._seen.add(final)
            if fetch.hops:
                # Links straight to the redirect target need no second request.
                self.out.fetches[final] = FetchResult(
                    url=final,
                    method="GET",
                    status=fetch.status,
                    final_url=final,
                    hops=[fetch.hops[-1]],
                    content_type=fetch.content_type,
                    body=fetch.body,
                    elapsed_ms=fetch.elapsed_ms,
                )
        if not fetch.ok or not fetch.is_html or not fetch.body:
            return
        parsed = parse_html(fetch.body)
        page.title = parsed.title
        base = final
        if parsed.base_href:
            with contextlib.suppress(UrlError):
                base = normalize(parsed.base_href, final)
        source = url if final == url else final
        follow_page = self.settings.follow_nofollow or not parsed.nofollow_all
        for raw in parsed.links:
            if not is_checkable(raw.url):
                self.out.non_http += 1
                continue
            if raw.url.strip().startswith("#") or not raw.url.strip():
                continue
            try:
                target = normalize(raw.url, base)
            except UrlError as exc:
                log.debug("skipping %r on %s: %s", raw.url, source, exc)
                continue
            if target == source and raw.tag == "link" and "canonical" in raw.rel:
                continue  # self-referencing canonical
            internal = self.scope.is_internal(target)
            is_asset = raw.tag not in ("a", "area", "iframe", "meta")
            if is_asset and not self.settings.check_assets:
                continue
            self.out.links.append(
                Link(
                    source=source,
                    target=target,
                    anchor=raw.anchor[:300],
                    tag=raw.tag,
                    attribute=raw.attribute,
                    nofollow=raw.nofollow,
                    internal=internal,
                )
            )
            page.links_found += 1
            if (
                internal
                and not is_asset
                and follow_page
                and (self.settings.follow_nofollow or not raw.nofollow)
                and depth + 1 <= self.settings.max_depth
                and looks_like_page(target)
                and self.scope.should_crawl(target)
            ):
                self._enqueue(target, depth + 1)
