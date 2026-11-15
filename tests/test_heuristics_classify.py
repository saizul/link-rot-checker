import pytest

from link_rot_checker.classify import ClassifySettings, classify
from link_rot_checker.heuristics import parked_reason, soft_404_reason
from link_rot_checker.model import Category, ErrorKind, FetchResult, Hop


def page(url, status=200, body="", title="Page", hops=None, content_type="text/html"):
    doc = f"<html><head><title>{title}</title></head><body>{body}</body></html>" if body or title else ""
    return FetchResult(
        url=url,
        status=status,
        final_url=(hops or [Hop(url, status)])[-1].url,
        hops=hops or [Hop(url, status)],
        content_type=content_type,
        body=doc,
    )


@pytest.mark.parametrize(
    ("title", "body", "flagged"),
    [
        ("Page Not Found - Example", "", True),
        ("404", "", True),
        ("Welcome", "<p>The page you are looking for could not be found.</p>", True),
        ("Welcome", "<p>Sorry, this page doesn't exist</p>", True),
        ("Error 404 | Example", "", True),
        ("Our products", "<p>404 products in stock</p>", False),
        ("Blog", "<p>Nothing to see</p>", False),
    ],
)
def test_soft_404_text(title, body, flagged):
    assert (soft_404_reason("https://e.com/x", page("https://e.com/x", title=title, body=body)) is not None) is flagged


def test_soft_404_title_matching_url_is_not_flagged():
    fetch = page("https://e.com/blog/fixing-404-not-found-errors", title="Fixing 404 Not Found errors")
    assert soft_404_reason(fetch.url, fetch) is None


def test_soft_404_redirect_to_home_and_error_page():
    hops = [Hop("https://e.com/deep/page", 301), Hop("https://www.e.com/", 200)]
    assert "home page" in soft_404_reason("https://e.com/deep/page", page("x", hops=hops))
    hops = [Hop("https://e.com/a", 302), Hop("https://e.com/errors/404.html", 200)]
    assert "error page" in soft_404_reason("https://e.com/a", page("x", hops=hops))
    hops = [Hop("http://e.com/", 301), Hop("https://e.com/", 200)]
    assert soft_404_reason("http://e.com/", page("x", hops=hops)) is None
    assert soft_404_reason("https://e.com/x", page("https://e.com/x", status=404)) is None


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("<p>This domain is for sale!</p>", True),
        ("<p>Buy this domain today</p>", True),
        ("<script src='https://www.sedoparking.com/frmpark/x.js'></script>", True),
        ("<p>Parked free, courtesy of a registrar</p>", True),
        ("<p>We sell domains and hosting.</p>", False),
    ],
)
def test_parked_body(body, flagged):
    fetch = page("https://p.test/", body=body, title="p.test")
    assert (parked_reason(fetch.url, fetch) is not None) is flagged


def test_parked_redirect_to_service():
    hops = [Hop("https://old-brand.test/", 302), Hop("https://www.hugedomains.com/domain_profile.cfm?d=old", 200)]
    assert "hugedomains.com" in parked_reason("https://old-brand.test/", page("x", hops=hops, body="x"))
    # A link to the marketplace itself is fine.
    fetch = page("https://www.hugedomains.com/", body="<p>Buy this domain</p>")
    assert parked_reason(fetch.url, fetch) is None


def test_classify_errors_and_statuses():
    s = ClassifySettings()
    err = FetchResult(url="https://x.test/", error_kind=ErrorKind.DNS, error="DNS lookup failed: x")
    assert classify(err.url, err, internal=False, settings=s).category is Category.BROKEN
    gone = page("https://x.test/", status=410)
    result = classify(gone.url, gone, internal=False, settings=s)
    assert result.category is Category.BROKEN and "410 Gone" in result.reason
    blocked = page("https://x.test/", status=403)
    assert classify(blocked.url, blocked, internal=False, settings=s).category is Category.SKIPPED
    assert classify(blocked.url, blocked, internal=True, settings=s).category is Category.BROKEN
    server = page("https://x.test/", status=500)
    assert "temporary" in classify(server.url, server, internal=False, settings=s).reason
    no_location = page("https://x.test/", status=302)
    assert classify(no_location.url, no_location, internal=True).category is Category.BROKEN


def test_classify_redirects():
    one = page("x", hops=[Hop("https://e.com/a", 301), Hop("https://e.com/b", 200)])
    assert classify("https://e.com/a", one, internal=True).category is Category.REDIRECT
    temp = page("x", hops=[Hop("https://e.com/a", 307), Hop("https://e.com/b", 200)])
    assert "temporary" in classify("https://e.com/a", temp, internal=True).reason
    https = page("x", hops=[Hop("http://e.com/a", 301), Hop("https://www.e.com/a", 200)])
    assert classify("http://e.com/a", https, internal=True).category is Category.HTTP_TO_HTTPS
    chain = page("x", hops=[Hop("http://e.com/a", 301), Hop("https://e.com/a", 301), Hop("https://e.com/a/", 200)])
    result = classify("http://e.com/a", chain, internal=True)
    assert result.category is Category.REDIRECT_CHAIN and "http -> https" in result.reason
    assert result.suggestion == "Link directly to https://e.com/a/"
    assert (
        classify("http://e.com/a", chain, internal=True, settings=ClassifySettings(chain_threshold=3)).category
        is Category.HTTP_TO_HTTPS
    )


def test_classify_loop_and_heuristics_toggle():
    loop = FetchResult(
        url="https://e.com/a",
        status=301,
        hops=[Hop("https://e.com/a", 301)],
        loop=True,
        error_kind=ErrorKind.TOO_MANY_REDIRECTS,
        error="redirect loop back to https://e.com/a",
    )
    assert classify(loop.url, loop, internal=True).category is Category.REDIRECT_LOOP
    soft = page("https://e.com/x", title="Not Found")
    assert classify(soft.url, soft, internal=True).category is Category.SOFT_404
    assert classify(soft.url, soft, internal=True, settings=ClassifySettings(soft_404=False)).category is Category.OK
    parked = page("https://p.test/", body="This domain is for sale")
    assert classify(parked.url, parked, internal=False).category is Category.PARKED
    assert classify(parked.url, parked, internal=True).category is Category.OK  # never "parked" for your own site
