"""Extract links, anchor text, the title and meta tags from an HTML page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: (tag, attribute) pairs that carry a URL we check.
LINK_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "a": ("href",),
    "area": ("href",),
    "img": ("src",),
    "source": ("src",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
    "script": ("src",),
    "link": ("href",),
    "embed": ("src",),
    "object": ("data",),
    "form": (),  # forms are not followed
}
#: <link rel> values worth checking; others (preconnect, dns-prefetch) are hints, not links.
CHECKED_LINK_RELS = frozenset(
    {
        "stylesheet",
        "icon",
        "shortcut",
        "apple-touch-icon",
        "canonical",
        "alternate",
        "prev",
        "next",
        "manifest",
        "preload",
        "amphtml",
    }
)
_WS = re.compile(r"\s+")


@dataclass
class RawLink:
    url: str
    tag: str
    attribute: str
    anchor: str = ""
    rel: str = ""

    @property
    def nofollow(self) -> bool:
        return "nofollow" in self.rel.split()


@dataclass
class ParsedPage:
    title: str = ""
    base_href: str | None = None
    links: list[RawLink] = field(default_factory=list)
    meta_robots: str = ""
    meta_refresh: str | None = None
    text: str = ""
    canonical: str | None = None

    @property
    def nofollow_all(self) -> bool:
        return "nofollow" in self.meta_robots or "none" in self.meta_robots


class _Parser(HTMLParser):
    def __init__(self, max_text: int) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._anchor_stack: list[tuple[RawLink, list[str]]] = []
        self._in_title = False
        self._title: list[str] = []
        self._skip_depth = 0  # inside <script>/<style>
        self._text: list[str] = []
        self._text_len = 0
        self._max_text = max_text

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript", "template"):
            if tag == "script" and attr.get("src"):
                self.page.links.append(RawLink(attr["src"], "script", "src"))
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "base" and attr.get("href") and self.page.base_href is None:
            self.page.base_href = attr["href"]
        elif tag == "meta":
            name = attr.get("name", "").lower()
            if name in ("robots", "googlebot"):
                self.page.meta_robots += " " + attr.get("content", "").lower()
            if attr.get("http-equiv", "").lower() == "refresh":
                match = re.search(r"url\s*=\s*['\"]?([^'\";]+)", attr.get("content", ""), re.I)
                if match:
                    self.page.meta_refresh = match.group(1).strip()
                    self.page.links.append(RawLink(self.page.meta_refresh, "meta", "refresh"))
        elif tag == "img":
            if attr.get("src"):
                self.page.links.append(RawLink(attr["src"], "img", "src", anchor=attr.get("alt", "")))
            # An image inside an <a> gives the link its alt text as anchor text.
            if self._anchor_stack and attr.get("alt"):
                self._anchor_stack[-1][1].append(attr["alt"])
            return
        elif tag == "link":
            rel = attr.get("rel", "").lower()
            if attr.get("href") and any(r in CHECKED_LINK_RELS for r in rel.split()):
                self.page.links.append(RawLink(attr["href"], "link", "href", rel=rel))
                if "canonical" in rel.split():
                    self.page.canonical = attr["href"]
            return
        if tag in ("a", "area"):
            href = attr.get("href")
            if href is not None:
                link = RawLink(href, tag, "href", rel=attr.get("rel", "").lower())
                if tag == "area":
                    link.anchor = attr.get("alt", "")
                    self.page.links.append(link)
                else:
                    # aria-label / title is the fallback when the link has no text
                    link.anchor = attr.get("aria-label") or attr.get("title") or ""
                    self._anchor_stack.append((link, []))
            return
        for attribute in LINK_ATTRIBUTES.get(tag, ()):
            if attr.get(attribute):
                self.page.links.append(RawLink(attr[attribute], tag, attribute))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in ("a", "script", "style", "noscript", "template", "title"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth = max(self._skip_depth - 1, 0)
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor_stack:
            link, parts = self._anchor_stack.pop()
            text = _WS.sub(" ", " ".join(parts)).strip()
            if text:
                link.anchor = text
            self.page.links.append(link)
            if self._anchor_stack:  # nested anchors (invalid HTML): give the text to the parent too
                self._anchor_stack[-1][1].append(text)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title.append(data)
            return
        if self._anchor_stack:
            self._anchor_stack[-1][1].append(data)
        if self._text_len < self._max_text:
            self._text.append(data)
            self._text_len += len(data)

    def finish(self) -> ParsedPage:
        while self._anchor_stack:  # unclosed <a>
            self.handle_endtag("a")
        self.page.title = _WS.sub(" ", "".join(self._title)).strip()
        self.page.text = _WS.sub(" ", " ".join(self._text)).strip()[: self._max_text]
        return self.page


def parse_html(html: str, *, max_text: int = 20_000) -> ParsedPage:
    """Parse ``html`` leniently. Never raises on malformed markup."""
    parser = _Parser(max_text)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is very lenient; keep whatever was parsed
        pass
    return parser.finish()
