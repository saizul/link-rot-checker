# link-rot-checker

[![CI](https://github.com/saizul/link-rot-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/link-rot-checker/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Find the broken and decaying links on a website, and fix the ones that hurt the most pages first. It crawls a
site from a start URL or a sitemap, collects every internal and external link with its source page and anchor
text, checks each unique target once (HEAD with a GET fallback, retries, per-host rate limits), and sorts the
results into **broken**, **redirect loops**, **parked domains**, **soft 404s**, **redirect chains**,
**http to https** upgrades and plain **redirects**. For broken external links it asks the Wayback Machine for an
archived copy you can link to instead.

Links rot quietly. A source you cited three years ago now answers 404, a partner's domain expired and shows a
"this domain is for sale" page with HTTP 200, a CMS migration turned one redirect into four. None of this shows up
in your own analytics, and a plain status-code checker misses the 200-but-gone cases. This tool reports them, per
page, with a fix list ordered by impact.

```console
$ link-rot-checker scan http://127.0.0.1:8765/ --no-wayback
http://127.0.0.1:8765/
7 pages, 24 links, 12 unique targets (11 internal, 1 external) in 2.49s
  4 broken, 1 redirect, 6 ok, 1 skipped

fix first:
   1. [broken] http://127.0.0.1:8765/pricing.html (3 pages)
      HTTP 404 Not Found
   2. [broken] https://broken-link.invalid/article (2 pages)
      DNS lookup failed
   3. [broken] http://127.0.0.1:8765/blog/second-post.html (1 page)
      HTTP 404 Not Found
   4. [broken] http://127.0.0.1:8765/img/banner.png (1 page)
      HTTP 404 Not Found
   5. [redirect] http://127.0.0.1:8765/blog (1 page)
      permanent redirect (301)

reports:
  link-rot-report/link-rot.html
  link-rot-report/link-rot.json

FAIL: 4 target(s) in broken,redirect_loop
```

That is the bundled demo site (`examples/demo-site/`), served with `python -m http.server 8765 --bind 127.0.0.1
--directory examples/demo-site`.

## Contents

- [Features](#features)
- [Installation](#installation)
- [Docker image](#docker-image)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Categories](#categories)
- [Configuration](#configuration)
- [Reports](#reports)
- [Using it in CI](#using-it-in-ci)
- [Library use](#library-use)
- [Examples](#examples)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Crawler**: asynchronous (httpx), breadth-first from one or more start URLs and/or XML sitemaps (sitemap
  indexes, `.xml.gz`, plain-text URL lists), a global concurrency limit, `--max-pages` and `--max-depth`, same-host
  scope with optional subdomains and extra hosts, include/exclude regular expressions, `<base href>`, meta
  refresh, `rel=nofollow` and meta robots handling.
- **robots.txt**: respected for crawled pages (RFC 9309: 4xx = allow all, 5xx or unreachable = disallow all),
  `Crawl-delay` is applied to the per-host rate limit. `--no-robots` for sites you own.
- **Links collected**: `<a>`, `<area>`, `<img>`, `<source>`, `<video>`, `<audio>`, `<iframe>`, `<script>`,
  `<embed>`, `<object>`, `<link rel=stylesheet|icon|canonical|alternate|...>` and meta refresh, with the source
  page, anchor text (or `alt`, `aria-label`, `title`), the tag and `nofollow`. `mailto:`, `tel:`, `javascript:` and
  fragments are ignored.
- **Checker**: every unique target exactly once. Pages already fetched by the crawler are not requested again.
  HEAD first, GET when HEAD fails or is refused (403/404/405/501, dropped connections) or when the body is needed
  for content checks. Redirects are followed by hand so every hop is recorded.
- **Resilience**: timeouts, retries with exponential backoff on timeouts, connection errors, 429 and 502-504,
  `Retry-After` (seconds or HTTP date), per-host rate limits and per-host concurrency limits.
- **Classification**: broken (4xx/5xx, DNS, TLS, timeouts, connection errors), redirect loops and too many
  redirects, redirect chains, http to https, temporary/permanent redirects, soft-404 heuristics, parked-domain
  heuristics; bot walls (401/403/429/999 on external sites) are reported as "verify by hand" instead of broken.
- **Wayback Machine**: for broken, parked and soft-404 external links, the availability API suggests the closest
  archived capture (HTTP 200 captures only), with an optional preferred timestamp and an on-disk cache.
- **Reports**: CSV (every link occurrence, and the fix list), JSON, Markdown and a self-contained HTML page, all
  grouped by source page, plus a **fix list** sorted by severity and by how many pages link to the target.
- **CI**: `--fail-on` categories and `--fail-threshold`, exit codes 0/1/2, a `check` command for URL lists.

## Installation

Python 3.10 or newer. The only dependency is `httpx`.

```bash
pip install "link-rot-checker @ git+https://github.com/saizul/link-rot-checker"
```

This installs the `link-rot-checker` command (also available as `python -m link_rot_checker`).

## Docker image

A multi-arch image (linux/amd64, linux/arm64) is published to GitHub Packages:

```bash
docker pull ghcr.io/saizul/link-rot-checker:latest

# Reports are written to /work, the working directory of the image
docker run --rm -v "$PWD:/work" --user "$(id -u)" ghcr.io/saizul/link-rot-checker:latest \
  scan https://www.example.com/ -f all -o link-rot-report

# A site running on the host (development server, staging behind a VPN)
docker run --rm --network host -v "$PWD:/work" --user "$(id -u)" ghcr.io/saizul/link-rot-checker:latest \
  scan http://127.0.0.1:8000/ --no-wayback
```

Release images are also tagged with the version (`ghcr.io/saizul/link-rot-checker:0.1.0`) and every build with
its commit SHA. The container runs as an unprivileged user (uid 10001); pass `--user "$(id -u)"` if it has to
write into a mounted directory you own.

## Usage

```text
link-rot-checker scan [URL ...] [--sitemap URL] [options]
link-rot-checker check FILE|- [options]
```

`scan` crawls and checks; `check` reads URLs (one per line, `#` comments) from a file or stdin and checks them
without crawling.

```bash
# The whole site, all report formats
link-rot-checker scan https://www.example.com/ -f all -o link-rot-report

# Start from the sitemap (and keep crawling from those pages), treat example.com and www as one site
link-rot-checker scan --sitemap https://www.example.com/sitemap.xml --allow-host example.com

# Only the pages in the sitemap, no crawling beyond them
link-rot-checker scan --sitemap https://www.example.com/sitemap_index.xml --no-crawl

# Only the blog, skip tag archives, never check LinkedIn
link-rot-checker scan https://www.example.com/blog/ --include '/blog/' --exclude '/tag/' --ignore 'linkedin\.com'

# Gentle on a small shared host: 1 request/second, one at a time
link-rot-checker scan https://www.example.com/ --rate 1 --per-host 1

# A list of URLs, for example from a spreadsheet or another crawler
link-rot-checker check urls.txt -f csv
grep -o 'https://[^" ]*' export.html | link-rot-checker check -
```

Main options (run `link-rot-checker scan --help` for all of them):

| Option | Default | Meaning |
| --- | --- | --- |
| `--sitemap URL` | | sitemap, sitemap index or text list to seed the crawl (repeatable) |
| `--no-crawl` | | only check the start/sitemap pages, do not follow links |
| `--max-pages N` / `--max-depth N` | 500 / 10 | crawl limits |
| `--include-subdomains`, `--allow-host HOST` | | widen the internal scope |
| `--include REGEX`, `--exclude REGEX` | | which internal pages are crawled (excluded pages are still checked as links) |
| `--ignore REGEX` | | targets that are never checked |
| `--no-robots` | | ignore robots.txt (only on sites you own) |
| `--no-external`, `--no-assets` | | skip external links, or images/scripts/stylesheets |
| `--concurrency N` / `--per-host N` | 10 / 2 | requests in flight, overall and per host |
| `--rate R`, `--host-rate HOST=R` | 5 | requests per second per host, 0 = no limit |
| `--timeout S`, `--retries N` | 15 / 2 | per request |
| `--max-redirects N`, `--chain-threshold N` | 10 / 2 | redirect limits |
| `--no-head`, `--no-content-checks` | | GET only, or HEAD only (no soft-404/parked checks) |
| `--no-wayback`, `--wayback-timestamp YYYYMMDD`, `--wayback-cache FILE` | | Wayback Machine lookups |
| `--header-env HEADER=ENV_VAR` | | send a header whose value is read from an environment variable |
| `-f FMT` | `html,json` | `csv`, `json`, `markdown` (`md`), `html` or `all` |
| `-o DIR`, `--basename NAME` | `link-rot-report`, `link-rot` | where reports go |
| `--fail-on CATS`, `--fail-threshold N` | `broken,redirect_loop`, 1 | exit code 1 rules |
| `-q`, `-v`, `--progress` | | quiet, verbose logging, progress on stderr |

Exit codes: **0** no failing targets, **1** at least `--fail-threshold` targets in a `--fail-on` category,
**2** usage or runtime error (bad option, unreachable start URL, empty sitemap).

## How it works

1. **Seeds.** Start URLs are normalised (lower-case scheme and host, default ports and fragments removed,
   unsafe characters percent-encoded, IDN hosts to punycode). Sitemaps are fetched and expanded (indexes are
   followed, up to 50 sitemaps). The hosts of the start URLs and sitemaps are the internal scope; if a start URL
   redirects to another host (`example.com` to `www.example.com`), that host joins the scope.
2. **Crawl.** Workers take URLs from a breadth-first queue. Each internal page is checked against robots.txt,
   fetched with GET (body capped at 256 KB), parsed with the standard library HTML parser, and its links are
   recorded. Internal `<a>`/`<iframe>` targets that look like pages (not `.pdf`, `.jpg`, ...) are queued until
   `--max-depth` or `--max-pages` is reached.
3. **Check.** All unique targets are checked concurrently. Targets the crawler already fetched reuse that answer.
   Others get a HEAD request; a failed or refused HEAD is retried as GET; an HTML answer is fetched with GET when
   content checks are on (the default) so soft 404s and parked domains can be recognised. Redirects are followed
   one hop at a time (up to `--max-redirects`); a URL seen twice in a chain is a loop.
4. **Rate limits.** Requests to the same host are spaced at least `1 / --rate` seconds apart, at most `--per-host`
   at a time. `Crawl-delay` and `Retry-After` slow a host down further. DNS and TLS errors are not retried.
5. **Wayback.** Broken, parked and soft-404 external targets are looked up with
   `GET https://archive.org/wayback/available?url=...[&timestamp=...]`. Only captures with status 200 are
   suggested. Lookups are spaced 1 second apart (`--wayback-rate`) and can be cached across runs.
6. **Reports.** Links are grouped by source page, and problem targets become the fix list.

## Categories

From most to least severe:

| Category | Meaning | Suggestion |
| --- | --- | --- |
| `broken` | 4xx or 5xx status, DNS failure, TLS/certificate error, timeout, connection error | update or remove; archived copy for external links |
| `redirect_loop` | a redirect leads back to a URL already in the chain, or more than `--max-redirects` hops | fix the redirect rules or remove |
| `parked` | an external domain now shows a parking or "for sale" page (heuristic) | find a new source, archived copy |
| `soft_404` | HTTP 200 but the page says "not found", or a deep URL redirects to the home page (heuristic) | update or remove, archived copy |
| `redirect_chain` | two or more redirects (`--chain-threshold`) | link to the final URL |
| `http_to_https` | an `http://` link redirects to the same site on `https://` | link to the https URL |
| `redirect` | one redirect (permanent or temporary) | link to the final URL |
| `ok` | 2xx without redirects | |
| `skipped` | not checked: robots.txt, `--no-external`, or a bot wall (401/403/429/999 from an external site) | verify by hand |

**Soft-404 heuristics**: a 2xx page whose title matches "not found", "404", "page does not exist" and similar
(unless the URL itself contains those words, as in an article about 404 errors), whose text says "the page you
are looking for could not be found" and similar, a redirect from a URL with a path to the bare home page of the
same site, or a redirect to an error path such as `/404.html`.

**Parked-domain heuristics** (external links only): a redirect to a known parking or domain marketplace service
(Sedo, Bodis, ParkingCrew, Afternic, Dan, HugeDomains and others), parking scripts in the HTML, or phrases such
as "this domain is for sale", "buy this domain", "parked free, courtesy of". A link to the marketplace itself is
not flagged.

## Configuration

Every option can also be set in a TOML file, in a `[link-rot-checker]` table (dashes or underscores in keys);
command-line flags win. See [`examples/link-rot-checker.toml`](examples/link-rot-checker.toml):

```toml
[link-rot-checker]
max_pages = 2000
extra_hosts = ["example.com"]
exclude = ['/wp-admin/', '\?replytocom=']
ignore = ['^https://(www\.)?linkedin\.com/']
rate_per_host = 3.0
host_rates = { "github.com" = 1.0 }
wayback_cache = ".link-rot-cache/wayback.json"
fail_on = ["broken", "redirect_loop", "parked"]
# Values are read from the environment; only the variable name is stored here:
# header_env = { "X-Preview-Token" = "SITE_PREVIEW_TOKEN" }
```

```bash
link-rot-checker scan https://www.example.com/ --config link-rot-checker.toml
```

Unknown keys and wrong types are errors, so typos do not go unnoticed.

## Reports

| File | Contents |
| --- | --- |
| `link-rot.html` | summary cards, the fix list, problems grouped by source page with anchor text and redirect chains; no scripts or external resources, works offline and in dark mode |
| `link-rot.md` | the same as Markdown, for pull request comments, wikis and tickets |
| `link-rot.json` | `summary`, `fix_list`, `pages_with_problems`, every `target` with hops and errors, crawled `pages`, every `link`, `skipped_pages` and the `settings` used (schema version 1) |
| `link-rot-links.csv` | one row per link occurrence: source, target, anchor, tag, internal, nofollow, category, status, reason, final URL, redirects, chain, error kind, archived URL, suggestion |
| `link-rot-fix-list.csv` | one row per problem target: rank, category, impact (distinct linking pages), occurrences, reason, suggestion, archived URL and the linking pages |

The **fix list** puts the most severe category first and, within a category, the target linked from the most
pages: a 404 in the footer of 300 pages comes before a 404 in one old post.

## Using it in CI

[`examples/github-actions-weekly.yml`](examples/github-actions-weekly.yml) runs a weekly scan of your site, caches
Wayback lookups and uploads the reports as a build artifact. The job fails (exit code 1) when broken links or
redirect loops are found; use `--fail-on none` to only report, or `--fail-threshold 10` to tolerate a few.

## Library use

```python
import asyncio
from link_rot_checker import Category, Settings, build_fix_list, run_scan

result = asyncio.run(run_scan(Settings(start_urls=["https://www.example.com/"], max_pages=100)))
for item in build_fix_list(result, {Category.BROKEN}):
    print(item.impact, item.target, item.reason, item.archived_url)
```

`run_scan` accepts `transport=` (any `httpx.AsyncBaseTransport`), which is how the tests run against fake sites.
See [`examples/library_usage.py`](examples/library_usage.py).

## Examples

- [`examples/demo-site/`](examples/demo-site/): a static site with broken links on purpose (used by the CI smoke
  test)
- [`examples/link-rot-checker.toml`](examples/link-rot-checker.toml): a commented configuration file
- [`examples/github-actions-weekly.yml`](examples/github-actions-weekly.yml): a scheduled scan workflow
- [`examples/urls.txt`](examples/urls.txt): input for the `check` command
- [`examples/library_usage.py`](examples/library_usage.py): the Python API

## Testing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

The tests never touch the network: `tests/fakeweb.py` serves fixture sites through `httpx.MockTransport` (404s,
redirect chains and loops, http to https, DNS and TLS failures, timeouts, 503 with `Retry-After`, servers that
refuse HEAD, parked and soft-404 pages, robots.txt, sitemaps and a fake Wayback API). CI also runs a smoke test
against the demo site served on `127.0.0.1`, and builds and runs the Docker image.

## Security notes

- The tool only sends GET and HEAD requests and never submits forms, but crawling still costs the target server
  resources. Keep the default rate limits for sites you do not own, and respect robots.txt (the default).
- External links are requested from the machine that runs the scan. Scanning untrusted pages from inside a
  private network can make the tool request internal addresses those pages link to (`http://10.0.0.5/`,
  `http://169.254.169.254/`). Run it where that does not matter, or use `--ignore` for private ranges.
- Report contents (anchor text, URLs, titles) come from the crawled site and are treated as untrusted: HTML and
  Markdown output is escaped, report links get `rel="noopener noreferrer nofollow"`, and CSV cells that start with
  `=`, `+`, `-` or `@` are prefixed with `'` so spreadsheets do not run them as formulas.
- Sitemaps with a DTD or entity declarations are rejected (no entity expansion), response bodies are capped at
  256 KB and sitemaps at 50 MB.
- Header values for authenticated staging sites are read from environment variables (`--header-env`); only the
  variable names appear in settings and reports. Do not pass credentials in URLs.
- `--insecure` disables certificate verification; TLS errors are then no longer reported.

## Limitations

- No JavaScript rendering: links that only exist after client-side rendering are not found.
- Soft-404 and parked-domain detection are heuristics. They are tuned against false positives, so some cases
  are missed; English phrases are best covered.
- Some sites answer automated requests with 403, 429 or 999 (LinkedIn) even for live pages; external ones are
  reported as `skipped` so you can verify them by hand.
- `<form action>`, `srcset` and CSS `url(...)` references are not checked.
- The Wayback Machine availability API returns only the closest capture; if that capture is not a 200, no
  suggestion is made even if an older good capture exists (use `--wayback-timestamp` to look further back). The
  API is a free public service and can be slow or return empty answers.
- Results reflect one moment: a 503 can be temporary, which is why 5xx reasons say so.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security problems: see [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Saizul Amin.
