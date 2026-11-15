from link_rot_checker.html_extract import parse_html


def test_links_anchor_text_and_tags():
    page = parse_html(
        """<html><head><title> My  Title </title>
        <link rel="stylesheet" href="/s.css"><link rel="preconnect" href="https://fonts.example">
        <link rel="canonical" href="https://example.com/x"><script src="/app.js"></script>
        <script>var a = "<a href='/not-a-link'>";</script></head>
        <body><a href="/one">First <b>link</b></a>
        <a href="/two" rel="nofollow sponsored"><img src="/i.png" alt="Logo image"></a>
        <a href="/three" aria-label="Third"></a>
        <area href="/map" alt="Map area"><iframe src="https://video.example/embed"></iframe>
        <video src="/v.mp4" poster="/p.jpg"></video></body></html>"""
    )
    assert page.title == "My Title"
    by_url = {link.url: link for link in page.links}
    assert by_url["/one"].anchor == "First link"
    assert by_url["/two"].anchor == "Logo image" and by_url["/two"].nofollow
    assert by_url["/three"].anchor == "Third"
    assert by_url["/i.png"].tag == "img"
    assert by_url["/s.css"].tag == "link"
    assert "https://fonts.example" not in by_url
    assert "/not-a-link" not in by_url
    assert by_url["/app.js"].tag == "script"
    assert by_url["/map"].anchor == "Map area"
    assert by_url["https://video.example/embed"].tag == "iframe"
    assert {"/v.mp4", "/p.jpg"} <= by_url.keys()
    assert page.canonical == "https://example.com/x"


def test_base_href_meta_robots_and_refresh():
    page = parse_html(
        '<head><base href="https://cdn.example/"><base href="/ignored/">'
        '<meta name="robots" content="NOINDEX, NOFOLLOW">'
        '<meta http-equiv="refresh" content="0; url=\'/moved\'"></head>'
    )
    assert page.base_href == "https://cdn.example/"
    assert page.nofollow_all
    assert page.meta_refresh == "/moved"
    assert any(link.tag == "meta" and link.url == "/moved" for link in page.links)


def test_malformed_html_does_not_raise():
    page = parse_html("<a href='/x'>unclosed <a href=/y>nested</div></p><<<>>")
    urls = [link.url for link in page.links]
    assert "/x" in urls and "/y" in urls


def test_text_excludes_scripts_and_is_capped():
    page = parse_html("<style>.x{}</style><p>" + "word " * 10000 + "</p>", max_text=100)
    assert len(page.text) <= 100
    assert ".x" not in page.text
