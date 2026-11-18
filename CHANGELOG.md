# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `--baseline FILE` (and `baseline` in the configuration file): compares the run with an earlier `link-rot.json`
  report and fails only on targets that became a problem since then, or became more severe. The summary lists new,
  worse and fixed targets, and the JSON report gains an optional `baseline` object (`source`, `new`, `worse`,
  `fixed`, `unchanged`); schema 1 is otherwise unchanged.
- Contributor community files: `CODE_OF_CONDUCT.md`, issue templates and a pull request template.

## [0.1.0] - 2026-11-15

### Added

- Asynchronous same-host crawler (httpx) from start URLs and/or sitemaps (indexes, `.xml.gz`, text lists) with a
  concurrency limit, `--max-pages`, `--max-depth`, subdomain and extra-host scope, include/exclude patterns,
  `<base href>`, meta refresh, `rel=nofollow` and meta robots handling.
- robots.txt support following RFC 9309 (4xx allows all, 5xx or unreachable disallows all) and `Crawl-delay`.
- Link extraction from `<a>`, `<area>`, `<img>`, `<source>`, `<video>`, `<audio>`, `<iframe>`, `<script>`,
  `<embed>`, `<object>`, `<link>` and meta refresh, with source page, anchor text, tag and `nofollow`.
- Checker that requests every unique target once: HEAD with GET fallback, hand-followed redirects, retries with
  backoff, `Retry-After`, per-host rate and concurrency limits, reuse of pages already fetched by the crawler.
- Categories: `broken` (4xx/5xx, DNS, TLS, timeout, connection), `redirect_loop`, `parked`, `soft_404`,
  `redirect_chain`, `http_to_https`, `redirect`, `ok` and `skipped` (robots.txt, bot walls).
- Soft-404 and parked-domain heuristics.
- Wayback Machine availability API lookups for broken, parked and soft-404 external links, with a preferred
  timestamp, rate limit and on-disk cache.
- Reports: CSV (links and fix list), JSON (schema 1), Markdown and self-contained HTML, grouped by source page,
  with a fix list sorted by severity and impact.
- `scan` and `check` commands, TOML configuration, `--fail-on` / `--fail-threshold` and exit codes for CI,
  headers from environment variables, Docker image published to `ghcr.io/saizul/link-rot-checker`.

[Unreleased]: https://github.com/saizul/link-rot-checker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/link-rot-checker/releases/tag/v0.1.0
