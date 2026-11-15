"""A tiny fake internet for tests, served through :class:`httpx.MockTransport`."""

from __future__ import annotations

import json
import socket
import ssl
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx

Handler = Callable[[httpx.Request], httpx.Response]


def html(body: str, title: str = "Page", status: int = 200) -> httpx.Response:
    doc = f"<!DOCTYPE html><html><head><title>{title}</title></head><body>{body}</body></html>"
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8"}, text=doc)


def redirect(location: str, status: int = 301) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def text(body: str, status: int = 200, content_type: str = "text/plain") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, text=body)


def dns_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known", request=request) from (
        socket.gaierror(8, "nodename nor servname provided, or not known")
    )


def tls_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request) from (
        ssl.SSLCertVerificationError("certificate verify failed")
    )


@dataclass
class FakeWeb:
    """Routes ``(host, path)`` to static responses or handler functions and records every request."""

    routes: dict[tuple[str, str], httpx.Response | Handler] = field(default_factory=dict)
    hosts: dict[str, Handler] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)

    def add(self, url: str, response: httpx.Response | Handler) -> FakeWeb:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        self.routes[(f"{parts.scheme}://{parts.netloc}", path)] = response
        return self

    def host(self, origin: str, handler: Handler) -> FakeWeb:
        self.hosts[origin] = handler
        return self

    def counts(self) -> Counter[tuple[str, str]]:
        return Counter(self.requests)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        path = request.url.raw_path.decode()
        if origin in self.hosts:
            return self.hosts[origin](request)
        route = self.routes.get((origin, path))
        if route is None:
            if path == "/robots.txt":
                return text("", status=404)
            return html("<h1>Not Found</h1>", title="404 Not Found", status=404)
        response = route(request) if callable(route) else route
        if request.method == "HEAD":
            return httpx.Response(response.status_code, headers=response.headers)
        # Responses are reused across requests, so hand out a fresh copy.
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Sequence:
    """A handler that answers with each response in turn, then repeats the last one."""

    def __init__(self, *responses: httpx.Response | Callable[[httpx.Request], httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return item(request) if callable(item) else item


def wayback_api(snapshots: dict[str, str], *, status: str = "200") -> httpx.MockTransport:
    """Fake availability API: ``snapshots`` maps original URL -> archived URL."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "archive.org" and request.url.path == "/wayback/available"
        url = parse_qs(request.url.query.decode())["url"][0]
        calls.append(url)
        if url in snapshots:
            body = {
                "archived_snapshots": {
                    "closest": {
                        "available": True,
                        "url": snapshots[url],
                        "timestamp": "20200101000000",
                        "status": status,
                    }
                }
            }
        else:
            body = {"archived_snapshots": {}}
        return httpx.Response(200, headers={"content-type": "application/json"}, text=json.dumps(body))

    transport = httpx.MockTransport(handle)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def demo_site() -> FakeWeb:
    """A small site with one of every problem."""
    web = FakeWeb()
    s = "https://example.com"
    web.add(f"{s}/robots.txt", text("User-agent: *\nDisallow: /private/\n"))
    web.add(
        f"{s}/",
        html(
            """
            <nav><a href="/about">About us</a> <a href="/blog/">Blog</a></nav>
            <a href="/missing">A missing page</a>
            <a href="/old-page">Old page</a>
            <a href="/chain-1">Chained</a>
            <a href="/loop-a">Loop</a>
            <a href="/private/secret">Secret</a>
            <a href="http://example.com/insecure">Insecure link</a>
            <a href="https://ext.test/ok">External OK</a>
            <a href="https://ext.test/gone">Gone article</a>
            <a href="https://nxdomain.test/">Dead domain</a>
            <a href="https://badtls.test/">Bad certificate</a>
            <a href="https://parked.test/">Parked</a>
            <a href="https://ext.test/soft">Soft</a>
            <a href="https://nohead.test/page">No HEAD</a>
            <a href="https://blocked.test/">Blocked</a>
            <a href="mailto:hello@example.com">Mail</a>
            <a href="#top">Top</a>
            <img src="/img/logo.png" alt="Logo">
            """,
            title="Home",
        ),
    )
    web.add(
        f"{s}/about",
        html(
            '<a href="/">Home</a> <a href="/missing">Missing again</a> <a href="https://ext.test/gone">Gone</a>',
            title="About",
        ),
    )
    web.add(f"{s}/blog/", html('<a href="/blog/post-1">First post</a> <a href="/about">About</a>', title="Blog"))
    web.add(
        f"{s}/blog/post-1",
        html('<p>Read <a href="https://ext.test/gone">this</a> and <a href="/missing">that</a>.</p>', title="Post 1"),
    )
    web.add(f"{s}/old-page", redirect("/new-page"))
    web.add(f"{s}/new-page", html("<p>new</p>", title="New"))
    web.add(f"{s}/chain-1", redirect("/chain-2"))
    web.add(f"{s}/chain-2", redirect("/chain-3", 302))
    web.add(f"{s}/chain-3", html("<p>end of chain</p>", title="Chain end"))
    web.add(f"{s}/loop-a", redirect("/loop-b"))
    web.add(f"{s}/loop-b", redirect("/loop-a"))
    web.add(f"{s}/private/secret", html("<p>secret</p>"))
    web.add("http://example.com/insecure", redirect("https://example.com/insecure"))
    web.add(f"{s}/insecure", html("<p>secure now</p>", title="Insecure"))
    web.add(f"{s}/img/logo.png", httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG"))
    web.add("https://ext.test/ok", html("<p>fine</p>", title="Fine"))
    web.add("https://ext.test/soft", html("<p>Sorry, this page does not exist.</p>", title="Page Not Found"))
    web.host("https://nxdomain.test", dns_error)
    web.host("https://badtls.test", tls_error)
    web.add(
        "https://parked.test/",
        html("<h1>parked.test</h1><p>This domain is for sale! Make an offer.</p>", title="parked.test"),
    )
    web.host(
        "https://nohead.test",
        lambda r: httpx.Response(405) if r.method == "HEAD" else html("<p>works with GET</p>", title="GET only"),
    )
    web.add("https://blocked.test/", html("<p>Access denied</p>", title="Forbidden", status=403))
    return web
