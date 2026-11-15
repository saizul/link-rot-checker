"""Turn a :class:`FetchResult` into a :class:`CheckResult` with a category and a reason."""

from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus

from link_rot_checker.heuristics import parked_reason, soft_404_reason
from link_rot_checker.model import Category, CheckResult, ErrorKind, FetchResult
from link_rot_checker.urls import host_of, strip_www

ERROR_LABELS = {
    ErrorKind.DNS: "DNS lookup failed",
    ErrorKind.TLS: "TLS/certificate error",
    ErrorKind.TIMEOUT: "timed out",
    ErrorKind.CONNECTION: "connection failed",
    ErrorKind.TOO_MANY_REDIRECTS: "too many redirects",
    ErrorKind.INVALID_URL: "invalid URL",
    ErrorKind.PROTOCOL: "HTTP protocol error",
    ErrorKind.OTHER: "request failed",
}


@dataclass
class ClassifySettings:
    chain_threshold: int = 2
    soft_404: bool = True
    parked: bool = True
    #: External statuses that usually mean "a bot was blocked", not "the page is gone". Reported as skipped.
    blocked_statuses: set[int] = field(default_factory=lambda: {401, 403, 429, 999})


def status_text(status: int) -> str:
    try:
        return f"HTTP {status} {HTTPStatus(status).phrase}"
    except ValueError:
        return f"HTTP {status}"


def _is_https_upgrade(original: str, final: str) -> bool:
    return (
        original.startswith("http://")
        and final.startswith("https://")
        and strip_www(host_of(original)) == strip_www(host_of(final))
    )


def classify(url: str, fetch: FetchResult, *, internal: bool, settings: ClassifySettings | None = None) -> CheckResult:
    settings = settings or ClassifySettings()
    result = CheckResult(
        url=url,
        category=Category.OK,
        status=fetch.status,
        final_url=fetch.final_url,
        hops=list(fetch.hops),
        error_kind=fetch.error_kind,
        error=fetch.error,
        method=fetch.method,
        internal=internal,
        elapsed_ms=fetch.elapsed_ms,
    )
    if fetch.loop:
        result.category, result.reason = Category.REDIRECT_LOOP, fetch.error or "redirect loop"
        return result
    if fetch.error_kind is ErrorKind.TOO_MANY_REDIRECTS:
        result.category, result.reason = Category.REDIRECT_LOOP, fetch.error or "too many redirects"
        return result
    if fetch.error_kind is not None:
        result.category = Category.BROKEN
        result.reason = ERROR_LABELS[fetch.error_kind]
        return result
    status = fetch.status or 0
    if status >= 400 or status < 100:
        if not internal and status in settings.blocked_statuses:
            result.category = Category.SKIPPED
            result.reason = f"{status_text(status)}: probably blocks automated checks, verify by hand"
            return result
        result.category = Category.BROKEN
        result.reason = status_text(status)
        if status >= 500:
            result.reason += " (server error, may be temporary)"
        return result
    if 300 <= status < 400:  # a redirect without a usable Location header
        result.category, result.reason = Category.BROKEN, f"{status_text(status)} without a Location header"
        return result
    if settings.parked and not internal:
        reason = parked_reason(url, fetch)
        if reason:
            result.category, result.reason = Category.PARKED, reason
            return result
    if settings.soft_404:
        reason = soft_404_reason(url, fetch)
        if reason:
            result.category, result.reason = Category.SOFT_404, reason
            return result
    hops = fetch.redirect_count
    if hops == 0:
        return result
    final = fetch.final_url or url
    statuses = " -> ".join(str(h.status) for h in fetch.hops)
    if hops >= settings.chain_threshold:
        result.category = Category.REDIRECT_CHAIN
        result.reason = f"{hops} redirects ({statuses})"
        if _is_https_upgrade(url, final):
            result.reason += ", including http -> https"
    elif _is_https_upgrade(url, final):
        result.category, result.reason = Category.HTTP_TO_HTTPS, "http link redirects to https"
    else:
        first = fetch.hops[0].status
        kind = "permanent" if first in (301, 308) else "temporary"
        result.category, result.reason = Category.REDIRECT, f"{kind} redirect ({first})"
    return result


def skipped(url: str, reason: str, *, internal: bool) -> CheckResult:
    return CheckResult(url=url, category=Category.SKIPPED, reason=reason, internal=internal)
