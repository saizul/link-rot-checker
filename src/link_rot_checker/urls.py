"""URL normalisation and scope rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

SKIP_SCHEMES = frozenset({"mailto", "tel", "javascript", "data", "sms", "ftp", "file", "about", "blob", "callto"})
DEFAULT_PORTS = {"http": 80, "https": 443}
# Characters that may stay unescaped in a path or query (RFC 3986 reserved + unreserved + '%').
_SAFE_PATH = "/:@!$&'()*+,;=-._~%"
_SAFE_QUERY = _SAFE_PATH + "?"


class UrlError(ValueError):
    """Raised for URLs that cannot be checked."""


def scheme_of(url: str) -> str:
    match = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    return match.group(1).lower() if match else ""


def is_checkable(url: str) -> bool:
    """True for http(s) URLs and relative references."""
    scheme = scheme_of(url)
    return scheme in ("", "http", "https")


def normalize(url: str, base: str | None = None) -> str:
    """Resolve ``url`` against ``base`` and return a canonical absolute http(s) URL.

    The fragment is dropped, the scheme and host are lower-cased, default ports are removed,
    an empty path becomes ``/`` and unsafe characters are percent-encoded. The query is kept as is
    (its order can matter to the server).
    """
    url = url.strip()
    if not url:
        raise UrlError("empty URL")
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UrlError(f"unsupported scheme: {scheme or '(none)'}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UrlError("missing host")
    try:
        host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError as exc:
        raise UrlError(f"invalid host: {host}") from exc
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlError(f"invalid port in {url}") from exc
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = quote(unquote_safe(parts.path), safe=_SAFE_PATH) or "/"
    query = quote(parts.query, safe=_SAFE_QUERY + "=")
    return urlunsplit((scheme, netloc, path, query, ""))


def unquote_safe(path: str) -> str:
    """Decode percent-escapes except the ones that change the meaning of a path (``/``, ``?``, ``#``, ``%``)."""
    path = re.sub(r"%(2[fF]|3[fF]|23|25)", lambda m: "%25" + m.group(1), path)
    return unquote(path)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


@dataclass
class Scope:
    """Decides which URLs are internal (crawled) and which are external (only checked)."""

    hosts: set[str] = field(default_factory=set)
    include_subdomains: bool = False
    include_patterns: list[re.Pattern[str]] = field(default_factory=list)
    exclude_patterns: list[re.Pattern[str]] = field(default_factory=list)

    @classmethod
    def for_start_urls(
        cls,
        urls: list[str],
        *,
        include_subdomains: bool = False,
        extra_hosts: list[str] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> Scope:
        hosts = {host_of(u) for u in urls}
        hosts.update(h.lower() for h in extra_hosts or [])
        return cls(
            hosts={h for h in hosts if h},
            include_subdomains=include_subdomains,
            include_patterns=[re.compile(p) for p in include or []],
            exclude_patterns=[re.compile(p) for p in exclude or []],
        )

    def is_internal(self, url: str) -> bool:
        host = host_of(url)
        if host in self.hosts:
            return True
        if self.include_subdomains:
            return any(host.endswith("." + strip_www(h)) or host == strip_www(h) for h in self.hosts)
        return False

    def should_crawl(self, url: str) -> bool:
        """Internal pages that match the include patterns (if any) and no exclude pattern."""
        if not self.is_internal(url):
            return False
        if self.include_patterns and not any(p.search(url) for p in self.include_patterns):
            return False
        return not self.is_excluded(url)

    def is_excluded(self, url: str) -> bool:
        return any(p.search(url) for p in self.exclude_patterns)


ASSET_EXTENSIONS = frozenset(
    [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".avif",
        ".svg",
        ".ico",
        ".bmp",
        ".tif",
        ".tiff",
        ".mp4",
        ".webm",
        ".mov",
        ".mp3",
        ".wav",
        ".ogg",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".rar",
        ".7z",
        ".exe",
        ".dmg",
        ".msi",
        ".apk",
        ".iso",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".json",
        ".xml",
        ".txt",
        ".csv",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    ]
)


def looks_like_page(url: str) -> bool:
    """Guess whether a URL is an HTML page worth crawling, from its extension."""
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    if dot == -1 or "/" in path[dot:]:
        return True
    return path[dot:] not in ASSET_EXTENSIONS
