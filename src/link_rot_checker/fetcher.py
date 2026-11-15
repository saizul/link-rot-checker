"""HTTP requests with timeouts, retries, per-host rate limits and hand-followed redirects."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from link_rot_checker._version import __version__
from link_rot_checker.model import ErrorKind, FetchResult, Hop
from link_rot_checker.ratelimit import HostRateLimiter
from link_rot_checker.urls import UrlError, normalize

log = logging.getLogger("link_rot_checker")

DEFAULT_USER_AGENT = f"link-rot-checker/{__version__} (+https://github.com/saizul/link-rot-checker)"
RETRY_STATUSES = frozenset({429, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_DNS_MARKERS = (
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "no address associated",
    "name does not resolve",
    "name resolution",
    "errno -2]",
    "errno -3]",
    "errno -5]",
    "errno 8]",
    "errno 11001]",
    "errno 11004]",
)
_TLS_MARKERS = ("ssl", "certificate", "tls", "handshake", "wrong version number")


@dataclass
class FetchSettings:
    timeout: float = 15.0
    retries: int = 2
    backoff: float = 0.5
    max_retry_after: float = 30.0
    max_redirects: int = 10
    max_body_bytes: int = 262_144
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "en"
    extra_headers: dict[str, str] = field(default_factory=dict)


def classify_exception(exc: BaseException) -> tuple[ErrorKind, str]:
    """Map an httpx exception (and its causes) to an :class:`ErrorKind` and a short message."""
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < 8:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    message = str(exc) or type(exc).__name__
    text = " ".join(str(e) for e in chain).lower()
    if isinstance(exc, httpx.TimeoutException):
        return ErrorKind.TIMEOUT, f"timed out ({type(exc).__name__})"
    if isinstance(exc, httpx.TooManyRedirects):
        return ErrorKind.TOO_MANY_REDIRECTS, message
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, UrlError)):
        return ErrorKind.INVALID_URL, message
    if any(isinstance(e, socket.gaierror) for e in chain) or any(m in text for m in _DNS_MARKERS):
        return ErrorKind.DNS, f"DNS lookup failed: {message}"
    if any(isinstance(e, ssl.SSLError) for e in chain) or any(m in text for m in _TLS_MARKERS):
        return ErrorKind.TLS, f"TLS error: {message}"
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.DecodingError)):
        return ErrorKind.PROTOCOL, message
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, OSError)):
        return ErrorKind.CONNECTION, message
    return ErrorKind.OTHER, message


def _retryable(kind: ErrorKind) -> bool:
    return kind in (ErrorKind.TIMEOUT, ErrorKind.CONNECTION, ErrorKind.PROTOCOL)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - (now or datetime.now(timezone.utc))).total_seconds(), 0.0)


def _decode(data: bytes, response: httpx.Response) -> str:
    encoding = response.charset_encoding or "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


class Fetcher:
    """Requests URLs one hop at a time so redirect chains and loops are visible."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: FetchSettings | None = None,
        limiter: HostRateLimiter | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.settings = settings or FetchSettings()
        self.limiter = limiter or HostRateLimiter(rate=0, per_host=8)
        self._sleep = sleep
        self.requests = 0

    def headers(self, method: str) -> dict[str, str]:
        headers = {"User-Agent": self.settings.user_agent, "Accept-Language": self.settings.accept_language}
        headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8" if method == "GET" else "*/*"
        headers.update(self.settings.extra_headers)
        return headers

    async def _request_once(self, method: str, url: str, read_body: bool) -> tuple[httpx.Response, bytes]:
        host = urlsplit(url).hostname or ""
        async with self.limiter.slot(host):
            self.requests += 1
            request = self.client.build_request(method, url, headers=self.headers(method))
            response = await self.client.send(request, stream=True, follow_redirects=False)
            try:
                data = b""
                if read_body and method == "GET":
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= self.settings.max_body_bytes:
                            break
                    data = b"".join(chunks)[: self.settings.max_body_bytes]
            finally:
                await response.aclose()
        return response, data

    async def _request_with_retries(
        self, method: str, url: str, read_body: bool
    ) -> tuple[httpx.Response | None, bytes, BaseException | None, int]:
        attempts = 0
        host = urlsplit(url).hostname or ""
        while True:
            attempts += 1
            try:
                response, data = await self._request_once(method, url, read_body)
            except Exception as exc:  # httpx raises many types; all are classified below
                kind, _ = classify_exception(exc)
                if attempts <= self.settings.retries and _retryable(kind):
                    await self._sleep(self.settings.backoff * 2 ** (attempts - 1))
                    continue
                return None, b"", exc, attempts
            if response.status_code in RETRY_STATUSES and attempts <= self.settings.retries:
                delay = parse_retry_after(response.headers.get("retry-after"))
                if delay is None:
                    delay = self.settings.backoff * 2 ** (attempts - 1)
                if delay > self.settings.max_retry_after:
                    return response, data, None, attempts
                self.limiter.penalize(host, delay)
                await self._sleep(delay)
                continue
            return response, data, None, attempts

    async def fetch(self, url: str, method: str = "GET", *, read_body: bool = True) -> FetchResult:
        """Request ``url`` and follow redirects by hand, recording every hop."""
        started = time.perf_counter()
        result = FetchResult(url=url, method=method)
        current = url
        visited: set[str] = set()
        attempts = 0
        while True:
            visited.add(current)
            response, data, exc, tries = await self._request_with_retries(method, current, read_body)
            attempts += tries
            if exc is not None or response is None:
                kind, message = classify_exception(exc) if exc else (ErrorKind.OTHER, "no response")
                result.error_kind, result.error = kind, message
                result.final_url = current
                break
            result.hops.append(Hop(current, response.status_code))
            result.status = response.status_code
            result.final_url = current
            result.content_type = response.headers.get("content-type", "")
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location:
                try:
                    nxt = normalize(urljoin(current, location))
                except UrlError as err:
                    result.error_kind, result.error = ErrorKind.INVALID_URL, f"bad redirect target {location!r}: {err}"
                    break
                if nxt in visited:
                    result.loop = True
                    result.error_kind = ErrorKind.TOO_MANY_REDIRECTS
                    result.error = f"redirect loop back to {nxt}"
                    break
                if len(result.hops) > self.settings.max_redirects:
                    result.error_kind = ErrorKind.TOO_MANY_REDIRECTS
                    result.error = f"more than {self.settings.max_redirects} redirects"
                    break
                current = nxt
                continue
            if data:
                result.body = _decode(data, response)
            break
        result.attempts = attempts
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return result

    async def fetch_bytes(self, url: str, *, limit: int = 50_000_000) -> tuple[int | None, bytes, str]:
        """GET ``url`` following redirects and return ``(status, body, error)``; reading stops at ``limit`` bytes."""
        host = urlsplit(url).hostname or ""
        try:
            async with self.limiter.slot(host):
                self.requests += 1
                async with self.client.stream(
                    "GET", url, headers=self.headers("GET"), follow_redirects=True, timeout=self.settings.timeout
                ) as response:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= limit:
                            break
                    status = response.status_code
        except Exception as exc:
            return None, b"", classify_exception(exc)[1]
        return status, b"".join(chunks)[:limit], ""


def make_client(
    *,
    timeout: float = 15.0,
    verify: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
    max_connections: int = 50,
) -> httpx.AsyncClient:
    """An :class:`httpx.AsyncClient` configured for checking links (redirects are followed by :class:`Fetcher`)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        verify=verify,
        follow_redirects=False,
        transport=transport,
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections // 2 or 1),
    )
