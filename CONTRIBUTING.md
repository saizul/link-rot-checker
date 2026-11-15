# Contributing

Thanks for taking the time to improve link-rot-checker. Bug reports, better
heuristics, new link sources and documentation fixes are all welcome.

## Reporting a bug

Open an issue with:

- the command you ran and its output (`-vv` adds debug logging)
- your Python version and operating system
- for a wrong category: the target URL, what the tool reported, what you
  expected, and the JSON entry for that target from `-f json` (it has every
  redirect hop and the error)
- for crawling problems: a minimal HTML snippet that shows the problem

Replace private URLs with `example.com`. Never paste tokens or cookies, including
values you pass through `--header-env`.

## Development setup

You need Python 3.10 or newer.

```console
$ git clone https://github.com/saizul/link-rot-checker.git
$ cd link-rot-checker
$ python -m venv .venv
$ . .venv/bin/activate
$ python -m pip install -e ".[dev]"
```

Before every commit:

```console
$ ruff check .
$ ruff format --check .
$ pytest
```

All three must pass; CI runs them on Python 3.10 to 3.13.

## Guidelines

- **Tests never use the network.** Add routes to the fixture sites in
  `tests/fakeweb.py` (`FakeWeb.add`, `FakeWeb.host`, `Sequence`) and go through
  `httpx.MockTransport`.
- **Heuristics need both kinds of examples.** A new soft-404 or parking pattern
  comes with a test that it matches and a test of a similar page it must not
  match. False positives are worse than misses.
- **Be polite by default.** Do not raise default concurrency or rates, and do
  not weaken robots.txt handling.
- **API details need a source.** The Wayback availability API follows
  https://archive.org/help/wayback_api.php; link the documentation when you
  change a request or response field.
- **The JSON report is a contract.** Do not rename keys without a changelog
  entry and a schema bump.
- **Escape everything** that comes from crawled pages in HTML, Markdown and CSV.
- **Every change comes with a test** that fails without it.
- Add a line to the `Unreleased` section of `CHANGELOG.md` for anything a user
  would notice.

## Project layout

```text
src/link_rot_checker/
    urls.py           normalisation, scope, page-or-asset guess
    html_extract.py   links, anchor text, title, base, meta robots/refresh
    ratelimit.py      per-host rate and concurrency limits
    fetcher.py        requests, retries, Retry-After, redirect hops, error kinds
    robots.py         robots.txt (RFC 9309), Crawl-delay
    sitemap.py        sitemaps, indexes, gzip, text lists
    crawler.py        breadth-first crawl
    heuristics.py     soft-404 and parked-domain detection
    classify.py       categories and reasons
    checker.py        HEAD/GET strategy, one check per target, Wayback lookups
    wayback.py        availability API client and cache
    fixlist.py        fix list by severity and impact
    scan.py           runs everything
    report/           csv, json, markdown, html
    config.py         settings and the TOML file
    cli.py            command-line interface
tests/                pytest suite and the fake web
examples/             demo site, config, CI workflow, URL list, library use
```

## Pull requests

- Keep each pull request focused on one change.
- Describe what changed and why, and how you tested it.
- Do not bump the version number; that happens at release time.

By contributing you agree that your work is released under the MIT License.
