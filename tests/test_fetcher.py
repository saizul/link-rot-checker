import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from fakeweb import FakeWeb, Sequence, dns_error, html, redirect, tls_error
from link_rot_checker.fetcher import Fetcher, FetchSettings, classify_exception, make_client, parse_retry_after
from link_rot_checker.model import ErrorKind
from link_rot_checker.ratelimit import HostRateLimiter


def run_fetch(web: FakeWeb, url: str, method: str = "GET", **settings):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def go():
        async with make_client(transport=web.transport()) as client:
            fetcher = Fetcher(
                client,
                FetchSettings(**{"backoff": 0.1, "retries": 2, **settings}),
                HostRateLimiter(rate=0, per_host=4, sleep=fake_sleep),
                sleep=fake_sleep,
            )
            return await fetcher.fetch(url, method), fetcher

    result, fetcher = asyncio.run(go())
    return result, fetcher, sleeps


def test_redirect_chain_is_recorded():
    web = FakeWeb()
    web.add("https://a.test/1", redirect("/2"))
    web.add("https://a.test/2", redirect("https://b.test/3", 302))
    web.add("https://b.test/3", html("ok"))
    result, _, _ = run_fetch(web, "https://a.test/1")
    assert [h.status for h in result.hops] == [301, 302, 200]
    assert result.final_url == "https://b.test/3"
    assert result.redirect_count == 2
    assert result.ok and "ok" in result.body


def test_redirect_loop_detected():
    web = FakeWeb()
    web.add("https://a.test/x", redirect("/y"))
    web.add("https://a.test/y", redirect("/x"))
    result, _, _ = run_fetch(web, "https://a.test/x")
    assert result.loop
    assert result.error_kind is ErrorKind.TOO_MANY_REDIRECTS
    assert "loop" in result.error


def test_too_many_redirects():
    web = FakeWeb()
    for i in range(20):
        web.add(f"https://a.test/{i}", redirect(f"/{i + 1}"))
    result, _, _ = run_fetch(web, "https://a.test/0", max_redirects=5)
    assert not result.loop
    assert result.error_kind is ErrorKind.TOO_MANY_REDIRECTS
    assert len(result.hops) == 6


def test_retry_on_503_then_success_uses_retry_after():
    web = FakeWeb()
    seq = Sequence(httpx.Response(503, headers={"retry-after": "3"}), html("back"))
    web.add("https://a.test/", seq)
    result, _, sleeps = run_fetch(web, "https://a.test/")
    assert result.status == 200
    assert result.attempts == 2
    assert 3.0 in sleeps


def test_retry_after_too_long_gives_up():
    web = FakeWeb()
    web.add("https://a.test/", httpx.Response(429, headers={"retry-after": "3600"}))
    result, _, _ = run_fetch(web, "https://a.test/")
    assert result.status == 429
    assert result.attempts == 1


def test_timeout_retried_then_reported():
    web = FakeWeb()

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    web.host("https://slow.test", slow)
    result, _, sleeps = run_fetch(web, "https://slow.test/")
    assert result.error_kind is ErrorKind.TIMEOUT
    assert result.attempts == 3
    assert sleeps == [0.1, 0.2]


def test_timeout_then_success():
    web = FakeWeb()

    def boom(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    web.add("https://a.test/", Sequence(boom, html("ok")))
    result, _, _ = run_fetch(web, "https://a.test/")
    assert result.ok and result.attempts == 2


def test_dns_is_not_retried():
    web = FakeWeb().host("https://nx.test", dns_error)
    result, _, _ = run_fetch(web, "https://nx.test/")
    assert result.error_kind is ErrorKind.DNS
    assert result.attempts == 1


def test_tls_error_classified():
    web = FakeWeb().host("https://tls.test", tls_error)
    result, _, _ = run_fetch(web, "https://tls.test/")
    assert result.error_kind is ErrorKind.TLS


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ConnectError("[Errno -2] Name or service not known"), ErrorKind.DNS),
        (httpx.ConnectError("[Errno -3] Temporary failure in name resolution"), ErrorKind.DNS),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), ErrorKind.TLS),
        (httpx.ConnectError("[Errno 111] Connection refused"), ErrorKind.CONNECTION),
        (httpx.ReadTimeout("x"), ErrorKind.TIMEOUT),
        (httpx.RemoteProtocolError("Server disconnected"), ErrorKind.PROTOCOL),
        (httpx.UnsupportedProtocol("ftp"), ErrorKind.INVALID_URL),
        (RuntimeError("weird"), ErrorKind.OTHER),
    ],
)
def test_classify_exception(exc, kind):
    assert classify_exception(exc)[0] is kind


def test_body_is_capped_and_head_has_no_body():
    web = FakeWeb().add("https://a.test/", html("x" * 10_000))
    result, _, _ = run_fetch(web, "https://a.test/", max_body_bytes=500)
    assert len(result.body) == 500
    head, _, _ = run_fetch(web, "https://a.test/", method="HEAD")
    assert head.body == "" and head.status == 200 and head.method == "HEAD"


def test_user_agent_and_extra_headers():
    seen = {}

    def capture(request):
        seen.update(request.headers)
        return html("ok")

    web = FakeWeb().host("https://a.test", capture)
    run_fetch(web, "https://a.test/", extra_headers={"X-Test": "1"}, user_agent="ua/1")
    assert seen["user-agent"] == "ua/1"
    assert seen["x-test"] == "1"


def test_bad_redirect_location():
    web = FakeWeb().add("https://a.test/", redirect("mailto:x@y.z"))
    result, _, _ = run_fetch(web, "https://a.test/")
    assert result.error_kind is ErrorKind.INVALID_URL


def test_parse_retry_after():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None
