"""Data model shared by the crawler, the checker and the reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    """Final classification of a link target, from most to least severe."""

    BROKEN = "broken"
    REDIRECT_LOOP = "redirect_loop"
    PARKED = "parked"
    SOFT_404 = "soft_404"
    REDIRECT_CHAIN = "redirect_chain"
    HTTP_TO_HTTPS = "http_to_https"
    REDIRECT = "redirect"
    OK = "ok"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


#: Lower number = more severe. Used for sorting and for the fix list.
SEVERITY: dict[Category, int] = {
    Category.BROKEN: 0,
    Category.REDIRECT_LOOP: 1,
    Category.PARKED: 2,
    Category.SOFT_404: 3,
    Category.REDIRECT_CHAIN: 4,
    Category.HTTP_TO_HTTPS: 5,
    Category.REDIRECT: 6,
    Category.OK: 7,
    Category.SKIPPED: 8,
}

#: Categories that need an edit to the linking page.
PROBLEM_CATEGORIES = frozenset(c for c in Category if c not in (Category.OK, Category.SKIPPED))

LABELS: dict[Category, str] = {
    Category.BROKEN: "Broken",
    Category.REDIRECT_LOOP: "Redirect loop",
    Category.PARKED: "Parked domain",
    Category.SOFT_404: "Soft 404",
    Category.REDIRECT_CHAIN: "Redirect chain",
    Category.HTTP_TO_HTTPS: "HTTP to HTTPS",
    Category.REDIRECT: "Redirect",
    Category.OK: "OK",
    Category.SKIPPED: "Skipped",
}


class ErrorKind(str, Enum):
    """Why a request failed without an HTTP status."""

    DNS = "dns"
    TLS = "tls"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    INVALID_URL = "invalid_url"
    PROTOCOL = "protocol"
    OTHER = "other"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Link:
    """One occurrence of a link on a page."""

    source: str
    target: str
    anchor: str = ""
    tag: str = "a"
    attribute: str = "href"
    nofollow: bool = False
    internal: bool = True


@dataclass
class Hop:
    """One response in a redirect chain."""

    url: str
    status: int


@dataclass
class FetchResult:
    """The outcome of requesting one URL (with redirects followed by hand)."""

    url: str
    method: str = "GET"
    status: int | None = None
    final_url: str | None = None
    hops: list[Hop] = field(default_factory=list)
    content_type: str = ""
    body: str = ""
    error_kind: ErrorKind | None = None
    error: str = ""
    loop: bool = False
    elapsed_ms: float = 0.0
    attempts: int = 1

    @property
    def redirect_count(self) -> int:
        return max(len(self.hops) - 1, 0)

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower()

    @property
    def ok(self) -> bool:
        return self.error_kind is None and self.status is not None and 200 <= self.status < 400


@dataclass
class CheckResult:
    """Classification of one unique link target."""

    url: str
    category: Category
    status: int | None = None
    final_url: str | None = None
    hops: list[Hop] = field(default_factory=list)
    error_kind: ErrorKind | None = None
    error: str = ""
    reason: str = ""
    method: str = ""
    internal: bool = True
    archived_url: str | None = None
    archived_timestamp: str | None = None
    elapsed_ms: float = 0.0

    @property
    def redirect_count(self) -> int:
        return max(len(self.hops) - 1, 0)

    @property
    def suggestion(self) -> str:
        """A short, human readable fix."""
        if self.category in (Category.REDIRECT, Category.REDIRECT_CHAIN, Category.HTTP_TO_HTTPS) and self.final_url:
            return f"Link directly to {self.final_url}"
        if self.category in (Category.BROKEN, Category.PARKED, Category.SOFT_404):
            if self.archived_url:
                return f"Replace with the archived copy {self.archived_url} or remove the link"
            return "Update or remove the link" if self.internal else "Find a new source or remove the link"
        if self.category is Category.REDIRECT_LOOP:
            return "Fix the redirect rules on the target server or remove the link"
        return ""


@dataclass
class PageInfo:
    """A crawled page."""

    url: str
    status: int | None
    depth: int
    title: str = ""
    final_url: str | None = None
    error: str = ""
    links_found: int = 0


@dataclass
class FixItem:
    """One entry of the fix list: a problem target and every page that links to it."""

    target: str
    category: Category
    pages: list[str]
    occurrences: int
    status: int | None
    reason: str
    suggestion: str
    archived_url: str | None
    internal: bool

    @property
    def impact(self) -> int:
        return len(self.pages)


@dataclass
class ScanResult:
    """Everything a scan produced."""

    start_urls: list[str]
    pages: dict[str, PageInfo]
    links: list[Link]
    results: dict[str, CheckResult]
    skipped: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    settings: dict[str, object] = field(default_factory=dict)
    #: Set by the CLI when ``--baseline`` is used: new, worse, fixed and unchanged problem targets.
    baseline: dict[str, object] | None = None

    def result_for(self, link: Link) -> CheckResult | None:
        return self.results.get(link.target)

    def links_by_source(self) -> dict[str, list[Link]]:
        out: dict[str, list[Link]] = {}
        for link in self.links:
            out.setdefault(link.source, []).append(link)
        return out

    def counts(self) -> dict[str, int]:
        counts = {c.value: 0 for c in Category}
        for result in self.results.values():
            counts[result.category.value] += 1
        return counts

    def problem_results(self) -> list[CheckResult]:
        return [r for r in self.results.values() if r.category in PROBLEM_CATEGORIES]
