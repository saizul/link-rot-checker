"""Soft-404 and parked-domain heuristics.

Both are guesses from the response alone. They are tuned to avoid false positives: a page needs a strong
signal (a "not found" title, a redirect from a deep URL to the home page, a known parking service) to be flagged.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from link_rot_checker.html_extract import parse_html
from link_rot_checker.model import FetchResult
from link_rot_checker.urls import host_of, strip_www

SOFT_404_TITLE = re.compile(
    r"(\b404\b|page not found|not found|no longer (exists|available)|page (does not|doesn't|cannot|can't|could not)"
    r" (exist|be found)|nothing (was )?found|error 404|seite nicht gefunden|página no encontrada|page introuvable)",
    re.I,
)
SOFT_404_TEXT = re.compile(
    r"(the page you (are|were|'re) looking for (could not be found|can(no|')t be found|does(n't| not) exist|"
    r"(is|was) not found|has been (removed|moved|deleted))|sorry,? (but )?(this|that|the) page (does(n't| not) exist"
    r"|(can(no|')t|could not) be found)|we (couldn't|could not|can't|cannot) find (the|that|this) page)",
    re.I,
)
ERROR_PATH = re.compile(r"(^|[/_.-])(404|not[-_]?found|page[-_]?not[-_]?found|error)([/_.-]|$)", re.I)

#: Hosts that only serve parking or "domain for sale" pages.
PARKING_HOSTS = (
    "sedoparking.com",
    "sedo.com",
    "bodis.com",
    "parkingcrew.net",
    "parkingpage.namecheap.com",
    "above.com",
    "afternic.com",
    "dan.com",
    "hugedomains.com",
    "undeveloped.com",
    "domainmarket.com",
    "buydomains.com",
    "parklogic.com",
    "skenzo.com",
    "voodoo.com",
    "domainnamesales.com",
    "uniregistry.com",
    "brandbucket.com",
    "squadhelp.com",
    "atom.com",
    "efty.com",
    "domainlore.com",
    "porkbun.com/parked",
)
PARKING_MARKUP = re.compile(
    r"(sedoparking|parkingcrew|bodis\.com|parklogic|dsnextgen|/js/parking|window\.park|park\.js|"
    r"caf\.js|domainpark|parking-lander|ParkingPage|parkedDomain)",
    re.I,
)
PARKING_TEXT = re.compile(
    r"(this domain (name )?(is|may be) for sale|buy this domain|domain (name )?is for sale|the domain [\w.-]+ is for "
    r"sale|make an offer on this domain|this domain is parked|parked free,? courtesy of|this web ?page is parked|"
    r"domain parking|this domain has (recently )?been registered|is this your domain\?|inquire about this domain|"
    r"get this domain|this domain is available)",
    re.I,
)


def _depth(path: str) -> int:
    return len([p for p in path.split("/") if p])


def soft_404_reason(original_url: str, fetch: FetchResult) -> str | None:
    """Why a 2xx response looks like a "not found" page, or ``None``."""
    if fetch.status is None or not 200 <= fetch.status < 300:
        return None
    final = fetch.final_url or original_url
    orig_parts, final_parts = urlsplit(original_url), urlsplit(final)
    if fetch.redirect_count:
        same_site = strip_www(host_of(original_url)) == strip_www(host_of(final))
        if same_site and _depth(orig_parts.path) >= 1 and _depth(final_parts.path) == 0 and not final_parts.query:
            return "redirects a deep URL to the home page"
        if ERROR_PATH.search(final_parts.path) and not ERROR_PATH.search(orig_parts.path):
            return f"redirects to an error page ({final_parts.path})"
    if not fetch.body or not fetch.is_html:
        return None
    page = parse_html(fetch.body, max_text=3000)
    if page.title and SOFT_404_TITLE.search(page.title) and not SOFT_404_TITLE.search(original_url):
        return f"title says {page.title[:80]!r}"
    if SOFT_404_TEXT.search(page.text[:3000]):
        return "page text says the page was not found"
    return None


def _parking_service(url: str) -> str | None:
    host = host_of(url)
    path_url = host + urlsplit(url).path
    for parking in PARKING_HOSTS:
        if "/" in parking:
            if path_url.startswith(parking):
                return parking
        elif host == parking or host.endswith("." + parking):
            return parking
    return None


def parked_reason(original_url: str, fetch: FetchResult) -> str | None:
    """Why the response looks like a parked or for-sale domain, or ``None``."""
    if _parking_service(original_url):
        return None  # a link to the parking service itself
    for hop in fetch.hops:
        service = _parking_service(hop.url)
        if service:
            return f"redirects to a parking service ({service})"
    if not fetch.body or not fetch.is_html:
        return None
    head = fetch.body[:60_000]
    if PARKING_MARKUP.search(head):
        return "page uses a domain parking script"
    page = parse_html(head, max_text=4000)
    text = f"{page.title} {page.text}"
    match = PARKING_TEXT.search(text)
    if match:
        return f"page says {match.group(0)!r}"
    return None
