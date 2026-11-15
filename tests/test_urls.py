import pytest

from link_rot_checker.urls import Scope, UrlError, is_checkable, looks_like_page, normalize, scheme_of


@pytest.mark.parametrize(
    ("raw", "base", "expected"),
    [
        ("HTTPS://Example.COM", None, "https://example.com/"),
        ("http://example.com:80/a", None, "http://example.com/a"),
        ("https://example.com:443/a#frag", None, "https://example.com/a"),
        ("https://example.com:8443/a", None, "https://example.com:8443/a"),
        ("../c?x=1&y=2", "https://example.com/a/b/", "https://example.com/a/c?x=1&y=2"),
        ("/p a t h", "https://example.com/", "https://example.com/p%20a%20t%20h"),
        ("/caf%C3%A9", "https://example.com/", "https://example.com/caf%C3%A9"),
        ("/café", "https://example.com/", "https://example.com/caf%C3%A9"),
        ("/a%2Fb", "https://example.com/", "https://example.com/a%2Fb"),
        ("//cdn.example.net/x.js", "https://example.com/", "https://cdn.example.net/x.js"),
        ("https://example.com.", None, "https://example.com/"),
        ("https://bücher.example/", None, "https://xn--bcher-kva.example/"),
        ("https://[::1]:8080/x", None, "https://[::1]:8080/x"),
    ],
)
def test_normalize(raw, base, expected):
    assert normalize(raw, base) == expected


@pytest.mark.parametrize("raw", ["", "mailto:a@b.c", "javascript:void(0)", "ftp://x/", "https://", "http://h:99999/"])
def test_normalize_rejects(raw):
    with pytest.raises(UrlError):
        normalize(raw)


def test_schemes():
    assert scheme_of("MailTo:x") == "mailto"
    assert scheme_of("/relative") == ""
    assert is_checkable("/x") and is_checkable("https://x") and not is_checkable("tel:123")


def test_scope_exact_host_and_subdomains():
    scope = Scope.for_start_urls(["https://www.example.com/"])
    assert scope.is_internal("https://www.example.com/a")
    assert not scope.is_internal("https://blog.example.com/")
    sub = Scope.for_start_urls(["https://www.example.com/"], include_subdomains=True)
    assert sub.is_internal("https://blog.example.com/")
    assert sub.is_internal("https://example.com/")
    assert not sub.is_internal("https://notexample.com/")


def test_scope_include_exclude():
    scope = Scope.for_start_urls(["https://example.com/"], include=[r"/blog/"], exclude=[r"\?page="])
    assert scope.should_crawl("https://example.com/blog/a")
    assert not scope.should_crawl("https://example.com/shop/")
    assert not scope.should_crawl("https://example.com/blog/?page=2")
    assert not scope.should_crawl("https://other.com/blog/")


def test_extra_hosts():
    scope = Scope.for_start_urls(["https://example.com/"], extra_hosts=["Example.ORG"])
    assert scope.is_internal("https://example.org/x")


@pytest.mark.parametrize(
    ("url", "page"),
    [
        ("https://e.com/", True),
        ("https://e.com/a.html", True),
        ("https://e.com/a.php?x", True),
        ("https://e.com/v1.2/docs", True),
        ("https://e.com/a.pdf", False),
        ("https://e.com/logo.PNG", False),
    ],
)
def test_looks_like_page(url, page):
    assert looks_like_page(url) is page
