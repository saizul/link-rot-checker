import httpx
import pytest

from conftest import fast_settings, scan
from fakeweb import FakeWeb, html, redirect, text
from link_rot_checker.model import Category
from link_rot_checker.scan import ScanError

EXPECTED = {
    "https://example.com/missing": Category.BROKEN,
    "https://example.com/old-page": Category.REDIRECT,
    "https://example.com/chain-1": Category.REDIRECT_CHAIN,
    "https://example.com/loop-a": Category.REDIRECT_LOOP,
    "https://example.com/private/secret": Category.SKIPPED,
    "http://example.com/insecure": Category.HTTP_TO_HTTPS,
    "https://ext.test/ok": Category.OK,
    "https://ext.test/gone": Category.BROKEN,
    "https://nxdomain.test/": Category.BROKEN,
    "https://badtls.test/": Category.BROKEN,
    "https://parked.test/": Category.PARKED,
    "https://ext.test/soft": Category.SOFT_404,
    "https://nohead.test/page": Category.OK,
    "https://blocked.test/": Category.SKIPPED,
    "https://example.com/img/logo.png": Category.OK,
}


def test_demo_site_categories(demo_result):
    got = {url: r.category for url, r in demo_result.results.items()}
    for url, category in EXPECTED.items():
        assert got[url] is category, url
    assert demo_result.results["https://nxdomain.test/"].error_kind.value == "dns"
    assert demo_result.results["https://badtls.test/"].error_kind.value == "tls"


def test_mailto_and_fragments_are_not_links(demo_result):
    assert not any(t.startswith("mailto") for t in demo_result.results)
    assert "https://example.com/#top" not in demo_result.results
    assert demo_result.settings["non_http_links"] == 1


def test_sources_and_anchor_text(demo_result):
    gone = [lk for lk in demo_result.links if lk.target == "https://ext.test/gone"]
    assert {lk.source for lk in gone} == {
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/blog/post-1",
    }
    assert {lk.anchor for lk in gone} == {"Gone article", "Gone", "this"}
    assert all(not lk.internal for lk in gone)


def test_wayback_suggestion_only_for_external(demo_result, wayback):
    gone = demo_result.results["https://ext.test/gone"]
    assert gone.archived_url == "https://web.archive.org/web/20200101000000/https://ext.test/gone"
    assert "archived copy" in gone.suggestion
    assert "https://example.com/missing" not in wayback.calls
    assert set(wayback.calls) == {
        "https://ext.test/gone",
        "https://nxdomain.test/",
        "https://badtls.test/",
        "https://parked.test/",
        "https://ext.test/soft",
    }


def test_each_target_checked_once(site, wayback):
    scan(site, fast_settings(start_urls=["https://example.com/"]), wayback)
    counts = site.counts()
    # crawled pages are fetched once and never re-checked
    assert counts[("GET", "https://example.com/missing")] == 1
    assert counts[("GET", "https://example.com/about")] == 1
    assert ("HEAD", "https://example.com/about") not in counts
    # external: HEAD, then GET for the content checks; never again although three pages link to it
    assert counts[("HEAD", "https://ext.test/gone")] == 1
    assert counts[("GET", "https://ext.test/gone")] == 1
    # HEAD 405 falls back to GET
    assert counts[("HEAD", "https://nohead.test/page")] == 1
    assert counts[("GET", "https://nohead.test/page")] == 1
    # robots.txt fetched once, disallowed page never requested
    assert counts[("GET", "https://example.com/robots.txt")] == 1
    assert not any("private" in url for _, url in counts)
    # images are checked with HEAD only (not HTML)
    assert counts[("HEAD", "https://example.com/img/logo.png")] == 1
    assert ("GET", "https://example.com/img/logo.png") not in counts


def test_no_robots_flag(site):
    result = scan(site, fast_settings(start_urls=["https://example.com/"], respect_robots=False, wayback=False))
    assert result.results["https://example.com/private/secret"].category is Category.OK


def test_no_external_and_no_assets(site):
    result = scan(
        site,
        fast_settings(start_urls=["https://example.com/"], check_external=False, check_assets=False, wayback=False),
    )
    assert result.results["https://ext.test/gone"].category is Category.SKIPPED
    assert "https://example.com/img/logo.png" not in result.results
    assert not any(url.startswith("https://ext.test") for _, url in site.requests)


def test_head_only_mode_skips_content_checks(site):
    result = scan(site, fast_settings(start_urls=["https://example.com/"], content_checks=False, wayback=False))
    assert result.results["https://parked.test/"].category is Category.OK
    assert ("GET", "https://parked.test/") not in site.counts()


def test_max_depth_and_max_pages(site):
    shallow = scan(site, fast_settings(start_urls=["https://example.com/"], max_depth=1, wayback=False))
    assert "https://example.com/blog/post-1" not in shallow.pages
    assert "https://example.com/blog/" in shallow.pages
    # post-1 is still checked as a link target
    assert shallow.results["https://example.com/blog/post-1"].category is Category.OK
    tiny = scan(site, fast_settings(start_urls=["https://example.com/"], max_pages=2, wayback=False))
    assert len(tiny.pages) <= 2


def test_ignore_and_exclude(site):
    result = scan(
        site,
        fast_settings(start_urls=["https://example.com/"], ignore=[r"ext\.test"], exclude=["/blog/"], wayback=False),
    )
    assert not any("ext.test" in u for u in result.results)
    assert "https://example.com/blog/" not in result.pages
    assert "https://example.com/blog/" in result.results  # excluded from crawling, still checked


def test_start_url_redirect_extends_scope():
    web = FakeWeb()
    web.add("https://e.test/", redirect("https://www.e.test/"))
    web.add("https://www.e.test/", html('<a href="/a">a</a> <a href="https://www.e.test/b">b</a>'))
    web.add("https://www.e.test/a", html('<a href="/b">b</a>'))
    web.add("https://www.e.test/b", html("b"))
    result = scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False))
    assert "https://www.e.test/a" in result.pages
    assert all(lk.internal for lk in result.links)
    assert result.links[0].source == "https://www.e.test/"


def test_nofollow_handling():
    web = FakeWeb()
    web.add("https://e.test/", html('<a href="/a" rel="nofollow">a</a>'))
    web.add("https://e.test/a", html('<a href="/b">b</a>'))
    web.add("https://e.test/b", html("b"))
    follow = scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False))
    assert "https://e.test/b" in follow.results
    skip = scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False, follow_nofollow=False))
    assert "https://e.test/a" in skip.results and "https://e.test/b" not in skip.results


def test_base_href_is_used():
    web = FakeWeb()
    web.add("https://e.test/dir/page", html('<base href="https://e.test/other/"><a href="x">x</a>'))
    web.add("https://e.test/other/x", html("x"))
    result = scan(web, fast_settings(start_urls=["https://e.test/dir/page"], wayback=False))
    assert "https://e.test/other/x" in result.results


def test_sitemap_start():
    web = FakeWeb()
    web.add(
        "https://e.test/sitemap.xml",
        httpx.Response(
            200,
            content=b"<urlset><url><loc>https://e.test/a</loc>"
            b"</url><url><loc>https://e.test/b</loc></url>"
            b"<url><loc>https://other.test/x</loc></url>"
            b"</urlset>",
        ),
    )
    web.add("https://e.test/a", html('<a href="/c">c</a>'))
    web.add("https://e.test/b", html("b"))
    web.add("https://e.test/c", html("c"))
    result = scan(web, fast_settings(sitemaps=["https://e.test/sitemap.xml"], wayback=False))
    assert set(result.start_urls) == {"https://e.test/a", "https://e.test/b"}
    assert "https://e.test/c" in result.pages
    only = scan(web, fast_settings(sitemaps=["https://e.test/sitemap.xml"], crawl=False, wayback=False))
    assert set(only.pages) == {"https://e.test/a", "https://e.test/b"}
    assert only.results["https://e.test/c"].category is Category.OK  # checked, not crawled


def test_empty_sitemap_is_an_error():
    web = FakeWeb().add("https://e.test/sitemap.xml", text("", status=404))
    with pytest.raises(ScanError):
        scan(web, fast_settings(sitemaps=["https://e.test/sitemap.xml"], wayback=False))


def test_unreachable_start_is_an_error():
    def fail(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    web = FakeWeb().host("https://down.test", fail)
    with pytest.raises(ScanError, match="could not fetch"):
        scan(web, fast_settings(start_urls=["https://down.test/"], respect_robots=False, wayback=False))


def test_check_mode_without_crawl(site):
    result = scan(
        site, fast_settings(wayback=False), urls_to_check=["https://ext.test/gone", "https://ext.test/ok", "not a url"]
    )
    assert result.pages == {}
    assert result.results["https://ext.test/gone"].category is Category.BROKEN
    assert {lk.source for lk in result.links} == {"(input)"}


def test_retry_on_flaky_external():
    web = FakeWeb()
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else html("ok")

    web.add("https://e.test/", html('<a href="https://flaky.test/">f</a>'))
    web.host("https://flaky.test", flaky)
    result = scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False, use_head=False))
    assert result.results["https://flaky.test/"].category is Category.OK


def test_header_env(monkeypatch):
    seen = []

    def capture(request):
        seen.append(request.headers.get("x-preview-token"))
        return html("ok")

    monkeypatch.setenv("LRC_TEST_PREVIEW", "abc")
    web = FakeWeb().host("https://e.test", capture)
    result = scan(
        web,
        fast_settings(
            start_urls=["https://e.test/"],
            respect_robots=False,
            wayback=False,
            header_env={"X-Preview-Token": "LRC_TEST_PREVIEW"},
        ),
    )
    assert seen and set(seen) == {"abc"}
    assert "abc" not in str(result.settings)
