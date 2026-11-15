import asyncio
import gzip

import httpx
import pytest

from fakeweb import FakeWeb, html, text
from link_rot_checker.fetcher import Fetcher, FetchSettings, make_client
from link_rot_checker.ratelimit import HostRateLimiter
from link_rot_checker.robots import RobotsPolicy, parse_robots
from link_rot_checker.sitemap import SitemapError, load_sitemaps, parse_sitemap

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://example.com/</loc><image:image><image:loc>https://example.com/i.png</image:loc></image:image></url>
  <url><loc> https://example.com/a </loc></url>
</urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sm-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sm-2.xml.gz</loc></sitemap>
</sitemapindex>"""


def with_fetcher(web, coro_fn):
    async def go():
        async with make_client(transport=web.transport()) as client:
            limiter = HostRateLimiter(rate=0)
            return await coro_fn(Fetcher(client, FetchSettings(retries=0), limiter), limiter)

    return asyncio.run(go())


def test_parse_robots_rules():
    rules = parse_robots(
        "https://e.com",
        "User-agent: *\nDisallow: /admin\n\nUser-agent: link-rot-checker\n"
        "Disallow: /no-bots\nCrawl-delay: 2\nSitemap: https://e.com/sm.xml\n",
    )
    assert not rules.allowed("https://e.com/no-bots/x")
    assert rules.allowed("https://e.com/admin")  # the specific group wins over *
    assert rules.crawl_delay() == 2.0
    assert rules.sitemaps() == ["https://e.com/sm.xml"]


@pytest.mark.parametrize(
    ("response", "allowed"),
    [(text("", status=404), True), (text("", status=503), False), (text("User-agent: *\nDisallow: /\n"), False)],
)
def test_robots_status_handling(response, allowed):
    web = FakeWeb().add("https://e.com/robots.txt", response)
    result = with_fetcher(web, lambda f, _: RobotsPolicy(f).allowed("https://e.com/page"))
    assert result is allowed


def test_robots_network_error_disallows():
    def fail(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    web = FakeWeb().host("https://e.com", fail)
    assert with_fetcher(web, lambda f, _: RobotsPolicy(f).allowed("https://e.com/")) is False


def test_robots_is_cached_and_crawl_delay_applied():
    web = FakeWeb().add("https://e.com/robots.txt", text("User-agent: *\nCrawl-delay: 3\n"))

    async def go(fetcher, limiter):
        policy = RobotsPolicy(fetcher)
        await asyncio.gather(*(policy.allowed(f"https://e.com/{i}") for i in range(5)))
        return limiter.interval("e.com")

    assert with_fetcher(web, go) == 3.0
    assert web.counts()[("GET", "https://e.com/robots.txt")] == 1


def test_robots_disabled():
    web = FakeWeb().add("https://e.com/robots.txt", text("User-agent: *\nDisallow: /\n"))
    assert with_fetcher(web, lambda f, _: RobotsPolicy(f, enabled=False).allowed("https://e.com/")) is True
    assert web.requests == []


def test_parse_urlset_ignores_image_locs():
    kind, locs = parse_sitemap(URLSET)
    assert kind == "urlset"
    assert locs == ["https://example.com/", "https://example.com/a"]


def test_parse_gzip_and_text():
    assert parse_sitemap(gzip.compress(URLSET))[1][1] == "https://example.com/a"
    assert parse_sitemap(b"https://e.com/1\n# no\nhttps://e.com/2\n") == (
        "text",
        ["https://e.com/1", "https://e.com/2"],
    )


@pytest.mark.parametrize(
    "data",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>',
        b"<html><body>nope</body></html>",
        b"<urlset><url><loc>broken",
        b"\x1f\x8bnot really gzip",
    ],
)
def test_parse_rejects(data):
    with pytest.raises(SitemapError):
        parse_sitemap(data)


def test_load_sitemap_index_recursively():
    web = FakeWeb()
    web.add("https://example.com/sitemap.xml", httpx.Response(200, content=INDEX))
    web.add("https://example.com/sm-1.xml", httpx.Response(200, content=URLSET))
    web.add(
        "https://example.com/sm-2.xml.gz",
        httpx.Response(200, content=gzip.compress(b"<urlset><url><loc>https://example.com/b</loc></url></urlset>")),
    )
    result = with_fetcher(web, lambda f, _: load_sitemaps(f, ["https://example.com/sitemap.xml"]))
    assert result.urls == ["https://example.com/", "https://example.com/a", "https://example.com/b"]
    assert result.errors == []


def test_load_sitemap_errors_and_limit():
    web = FakeWeb().add("https://example.com/ok.xml", httpx.Response(200, content=URLSET))
    result = with_fetcher(
        web,
        lambda f, _: load_sitemaps(f, ["https://example.com/missing.xml", "https://example.com/ok.xml"], max_urls=1),
    )
    assert result.urls == ["https://example.com/"]
    assert "missing.xml" in result.errors[0]


def test_html_is_not_a_sitemap():
    web = FakeWeb().add("https://example.com/sitemap.xml", html("<p>oops</p>"))
    result = with_fetcher(web, lambda f, _: load_sitemaps(f, ["https://example.com/sitemap.xml"]))
    assert result.urls == [] and result.errors


def test_fetch_bytes_limit_and_errors():
    web = FakeWeb().add("https://e.test/big", httpx.Response(200, content=b"x" * 5000))

    async def go(fetcher, _):
        big = await fetcher.fetch_bytes("https://e.test/big", limit=100)
        bad = await fetcher.fetch_bytes("mailto:nope")
        return big, bad

    (status, data, error), (bad_status, _, bad_error) = with_fetcher(web, go)
    assert status == 200 and len(data) == 100 and error == ""
    assert bad_status is None and bad_error
