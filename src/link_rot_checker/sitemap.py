"""Read XML sitemaps, sitemap indexes, gzip sitemaps and plain-text URL lists."""

from __future__ import annotations

import gzip
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from link_rot_checker.fetcher import Fetcher
from link_rot_checker.urls import UrlError, normalize

log = logging.getLogger("link_rot_checker")

_DTD = re.compile(rb"<!(DOCTYPE|ENTITY)", re.I)


class SitemapError(ValueError):
    pass


@dataclass
class SitemapResult:
    urls: list[str] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(data: bytes) -> tuple[str, list[str]]:
    """Return ``("urlset" | "sitemapindex" | "text", locations)``.

    Documents with a DTD or entity declarations are rejected, so entity expansion attacks cannot work.
    """
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise SitemapError(f"bad gzip data: {exc}") from exc
    stripped = data.lstrip()
    if not stripped.startswith(b"<"):
        text = data.decode("utf-8", errors="replace")
        return "text", [line.strip() for line in text.splitlines() if line.strip().startswith(("http://", "https://"))]
    if _DTD.search(data[:4096]) or b"<!ENTITY" in data:
        raise SitemapError("sitemap contains a DTD or entity declarations")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SitemapError(f"invalid XML: {exc}") from exc
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"not a sitemap (root element <{kind}>)")
    locs = [(el.text or "").strip() for el in root.iter() if _local(el.tag) == "loc"]
    # In a urlset, <loc> can also appear inside image/video extensions; keep only <url><loc>.
    if kind == "urlset":
        locs = []
        for url_el in root:
            if _local(url_el.tag) != "url":
                continue
            for child in url_el:
                if _local(child.tag) == "loc" and child.text:
                    locs.append(child.text.strip())
    return kind, [loc for loc in locs if loc]


async def load_sitemaps(
    fetcher: Fetcher, sitemap_urls: list[str], *, max_urls: int = 50_000, max_sitemaps: int = 50
) -> SitemapResult:
    """Fetch sitemaps (following sitemap indexes) and collect page URLs."""
    out = SitemapResult()
    queue = list(sitemap_urls)
    seen_maps: set[str] = set()
    seen_urls: set[str] = set()
    while queue and len(seen_maps) < max_sitemaps and len(out.urls) < max_urls:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        out.sitemaps.append(sm)
        status, data, error = await fetcher.fetch_bytes(sm)
        if error or status is None or status >= 400:
            out.errors.append(f"{sm}: {error or f'HTTP {status}'}")
            continue
        try:
            kind, locs = parse_sitemap(data)
        except SitemapError as exc:
            out.errors.append(f"{sm}: {exc}")
            continue
        if kind == "sitemapindex":
            queue.extend(loc for loc in locs if loc not in seen_maps)
            continue
        for loc in locs:
            try:
                url = normalize(loc)
            except UrlError:
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                out.urls.append(url)
                if len(out.urls) >= max_urls:
                    break
    for err in out.errors:
        log.warning("sitemap: %s", err)
    return out
