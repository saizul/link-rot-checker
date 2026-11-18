"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys
from pathlib import Path
from typing import TextIO

from link_rot_checker._version import __version__
from link_rot_checker.baseline import Baseline, BaselineDiff, compare, load_baseline
from link_rot_checker.config import ConfigError, Settings, load_config_file
from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import PROBLEM_CATEGORIES, Category, ScanResult
from link_rot_checker.report import FORMATS, write_reports
from link_rot_checker.report.common import label, summary
from link_rot_checker.scan import ScanError, run_scan

log = logging.getLogger("link_rot_checker")
EXIT_OK, EXIT_PROBLEMS, EXIT_ERROR = 0, 1, 2


def _formats(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in value.split(","):
            item = {"md": "markdown", "htm": "html"}.get(item.strip().lower(), item.strip().lower())
            if not item:
                continue
            if item == "all":
                out.extend(f for f in FORMATS if f not in out)
            elif item in FORMATS:
                if item not in out:
                    out.append(item)
            else:
                raise argparse.ArgumentTypeError(f"unknown format {item!r} (choose from {', '.join(FORMATS)}, all)")
    return out


def _csv_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("output")
    g.add_argument("--config", type=Path, help="TOML file with a [link-rot-checker] table")
    g.add_argument(
        "-f",
        "--format",
        action="append",
        metavar="FMT",
        help="csv, json, markdown (md), html or all; repeatable or comma-separated",
    )
    g.add_argument("-o", "--output", type=Path, default=Path("link-rot-report"), help="report directory")
    g.add_argument("--basename", default="link-rot", help="report file name prefix (default: link-rot)")
    g.add_argument("-q", "--quiet", action="store_true", help="no summary on stdout")
    g.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv for debug)")
    g.add_argument("--progress", action="store_true", help="show progress on stderr")

    g = common.add_argument_group("checking")
    g.add_argument("--concurrency", type=int, help="requests in flight (default 10)")
    g.add_argument("--per-host", dest="per_host_concurrency", type=int, help="requests in flight per host (default 2)")
    g.add_argument(
        "--rate", dest="rate_per_host", type=float, help="requests per second per host, 0 = no limit (default 5)"
    )
    g.add_argument("--host-rate", action="append", metavar="HOST=RATE", help="rate for one host, repeatable")
    g.add_argument("--timeout", type=float, help="seconds per request (default 15)")
    g.add_argument("--retries", type=int, help="retries on timeouts, connection errors, 429 and 502-504 (default 2)")
    g.add_argument("--max-redirects", type=int, help="redirects to follow before giving up (default 10)")
    g.add_argument("--chain-threshold", type=int, help="redirects that make a chain (default 2)")
    g.add_argument(
        "--no-external", dest="check_external", action="store_false", default=None, help="do not check external links"
    )
    g.add_argument("--no-head", dest="use_head", action="store_false", default=None, help="always use GET")
    g.add_argument(
        "--no-content-checks",
        dest="content_checks",
        action="store_false",
        default=None,
        help="do not download HTML bodies of external pages (disables soft-404 and parked checks for them)",
    )
    g.add_argument("--no-soft404", dest="soft_404", action="store_false", default=None, help="disable soft-404 checks")
    g.add_argument(
        "--no-parked", dest="parked", action="store_false", default=None, help="disable parked-domain checks"
    )
    g.add_argument("--ignore", action="append", metavar="REGEX", help="do not check targets matching REGEX")
    g.add_argument(
        "--insecure",
        dest="verify_tls",
        action="store_false",
        default=None,
        help="do not verify TLS certificates (TLS errors are then not reported)",
    )
    g.add_argument("--user-agent", help="User-Agent header")
    g.add_argument(
        "--header-env",
        action="append",
        metavar="HEADER=ENV_VAR",
        help="send HEADER with the value of environment variable ENV_VAR, repeatable",
    )

    g = common.add_argument_group("Wayback Machine")
    g.add_argument(
        "--no-wayback",
        dest="wayback",
        action="store_false",
        default=None,
        help="do not look up archived copies of broken external links",
    )
    g.add_argument("--wayback-timestamp", metavar="YYYYMMDD", help="prefer captures close to this time")
    g.add_argument("--wayback-cache", metavar="FILE", help="JSON file to cache lookups between runs")
    g.add_argument(
        "--wayback-rate", type=float, metavar="RATE", help="availability API requests per second (default 1)"
    )

    g = common.add_argument_group("CI")
    g.add_argument(
        "--fail-on",
        type=_csv_list,
        metavar="CATEGORIES",
        help="categories that fail the run (default: broken,redirect_loop; 'none' never fails)",
    )
    g.add_argument("--fail-threshold", type=int, metavar="N", help="fail when at least N targets match (default 1)")
    g.add_argument(
        "--baseline",
        metavar="FILE",
        help="an earlier link-rot.json report: only targets that became problems since then fail the run",
    )

    parser = argparse.ArgumentParser(
        prog="link-rot-checker",
        description="Find broken and decaying links on a website.",
        epilog="Exit codes: 0 no failing links, 1 failing links found (--fail-on), 2 error.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan = sub.add_parser(
        "scan",
        parents=[common],
        help="crawl a site and check every link",
        description="Crawl a site from start URLs and/or sitemaps and check every link found.",
    )
    scan.add_argument("urls", nargs="*", metavar="URL", help="start URL(s)")
    g = scan.add_argument_group("crawling")
    g.add_argument("--sitemap", dest="sitemaps", action="append", metavar="URL", help="sitemap or sitemap index")
    g.add_argument(
        "--no-crawl",
        dest="crawl",
        action="store_false",
        default=None,
        help="only check the start/sitemap pages, do not follow links",
    )
    g.add_argument("--max-pages", type=int, help="pages to crawl (default 500)")
    g.add_argument("--max-depth", type=int, help="link depth from the start URLs (default 10)")
    g.add_argument("--include-subdomains", action="store_true", default=None, help="crawl subdomains too")
    g.add_argument(
        "--allow-host", dest="extra_hosts", action="append", metavar="HOST", help="treat HOST as internal, repeatable"
    )
    g.add_argument("--include", action="append", metavar="REGEX", help="only crawl pages matching REGEX")
    g.add_argument("--exclude", action="append", metavar="REGEX", help="do not crawl pages matching REGEX")
    g.add_argument(
        "--no-robots",
        dest="respect_robots",
        action="store_false",
        default=None,
        help="ignore robots.txt (only on sites you own)",
    )
    g.add_argument(
        "--no-assets",
        dest="check_assets",
        action="store_false",
        default=None,
        help="only check <a>, <area> and <iframe> links, not images, scripts and stylesheets",
    )
    g.add_argument(
        "--skip-nofollow",
        dest="follow_nofollow",
        action="store_false",
        default=None,
        help="do not crawl rel=nofollow links",
    )

    check = sub.add_parser(
        "check",
        parents=[common],
        help="check a list of URLs without crawling",
        description="Check URLs from a file (one per line, '#' comments) or '-' for stdin.",
    )
    check.add_argument("file", metavar="FILE", help="file with one URL per line, or - for stdin")
    check.add_argument(
        "--allow-host",
        dest="extra_hosts",
        action="append",
        metavar="HOST",
        help="treat HOST as internal (affects blocked-status handling), repeatable",
    )
    return parser


_DIRECT = (
    "max_pages",
    "max_depth",
    "concurrency",
    "per_host_concurrency",
    "rate_per_host",
    "timeout",
    "retries",
    "max_redirects",
    "chain_threshold",
    "check_external",
    "use_head",
    "content_checks",
    "soft_404",
    "parked",
    "verify_tls",
    "user_agent",
    "wayback",
    "wayback_timestamp",
    "wayback_cache",
    "wayback_rate",
    "fail_on",
    "fail_threshold",
    "baseline",
    "crawl",
    "include_subdomains",
    "respect_robots",
    "check_assets",
    "follow_nofollow",
)
_LISTS = ("sitemaps", "extra_hosts", "include", "exclude", "ignore")


def _pairs(values: list[str] | None, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values or []:
        key, sep, val = value.partition("=")
        if not sep or not key.strip() or not val.strip():
            raise ConfigError(f"{what} must look like KEY=VALUE, got {value!r}")
        out[key.strip()] = val.strip()
    return out


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = load_config_file(args.config) if args.config else Settings()
    for name in _DIRECT:
        value = getattr(args, name, None)
        if value is not None:
            setattr(settings, name, value)
    for name in _LISTS:
        value = getattr(args, name, None)
        if value:
            setattr(settings, name, [*getattr(settings, name), *value])
    if getattr(args, "urls", None):
        settings.start_urls = list(args.urls)
    if args.host_rate:
        try:
            settings.host_rates.update({k: float(v) for k, v in _pairs(args.host_rate, "--host-rate").items()})
        except ValueError as exc:
            raise ConfigError(f"--host-rate: {exc}") from exc
    if args.header_env:
        settings.header_env.update(_pairs(args.header_env, "--header-env"))
    settings.validate()
    return settings


def read_url_file(name: str, stdin: TextIO) -> list[str]:
    text = stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line.split()[0])
    return urls


def failing(result: ScanResult, settings: Settings, baseline: Baseline | None = None) -> list[str]:
    """Targets that fail the run; with a baseline, only the new or worse ones."""
    cats = settings.fail_categories()
    if baseline is not None:
        return compare(result, baseline, cats).regressions
    return [r.url for r in result.results.values() if r.category in cats]


def print_baseline(diff: BaselineDiff, out: TextIO, limit: int = 10) -> None:
    out.write(
        f"\ncompared with {diff.source}: {len(diff.new)} new, {len(diff.worse)} worse, "
        f"{len(diff.fixed)} fixed, {len(diff.unchanged)} unchanged problem target(s)\n"
    )
    for title, urls in (("new", diff.new), ("worse", diff.worse), ("fixed", diff.fixed)):
        for url in urls[:limit]:
            out.write(f"  {title}: {url}\n")
        if len(urls) > limit:
            out.write(f"  ... {len(urls) - limit} more {title}\n")


def print_summary(result: ScanResult, settings: Settings, written: list[Path], out: TextIO) -> None:
    s = summary(result)
    if result.start_urls:
        out.write(f"{', '.join(result.start_urls[:3])}{' ...' if len(result.start_urls) > 3 else ''}\n")
    out.write(
        f"{s['pages_crawled']} pages, {s['links']} links, {s['unique_targets']} unique targets "
        f"({s['internal_targets']} internal, {s['external_targets']} external) in {result.duration_s}s\n"
    )
    parts = [f"{s[c.value]} {label(c).lower()}" for c in Category if s[c.value]]
    out.write(("  " + ", ".join(parts) + "\n") if parts else "  nothing checked\n")
    fixes = build_fix_list(result)
    if fixes:
        out.write("\nfix first:\n")
        for rank, item in enumerate(fixes[:10], 1):
            pages = f"{item.impact} page{'s' if item.impact != 1 else ''}"
            out.write(f"  {rank:>2}. [{item.category.value}] {item.target} ({pages})\n")
            detail = item.reason + (f" -> {item.archived_url}" if item.archived_url else "")
            if detail:
                out.write(f"      {detail}\n")
        if len(fixes) > 10:
            out.write(f"  ... {len(fixes) - 10} more in the report\n")
    if written:
        out.write("\nreports:\n" + "".join(f"  {p}\n" for p in written))


def _progress(stream: TextIO):
    def show(stage: str, done: int, total: int) -> None:
        stream.write(f"\r{stage}: {done}/{total}   ")
        if done >= total:
            stream.write("\n")
        stream.flush()

    return show


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stdin: TextIO | None = None,
    transport=None,
    wayback_transport=None,
) -> int:
    stdout = stdout or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(stdout)
        return EXIT_ERROR
    logging.basicConfig(
        level=logging.WARNING - 10 * min(args.verbose, 2),
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    try:
        formats = _formats(args.format) or ["html", "json"]
        settings = settings_from_args(args)
        urls_to_check = None
        if args.command == "check":
            urls_to_check = read_url_file(args.file, stdin or sys.stdin)
            if not urls_to_check:
                raise ConfigError("no URLs to check")
            settings = dataclasses.replace(settings, start_urls=[])
        elif not settings.start_urls and not settings.sitemaps:
            raise ConfigError("give at least one start URL or --sitemap")
        baseline = load_baseline(settings.baseline) if settings.baseline else None
        result = asyncio.run(
            run_scan(
                settings,
                transport=transport,
                wayback_transport=wayback_transport,
                urls_to_check=urls_to_check,
                progress=_progress(sys.stderr) if args.progress else None,
            )
        )
        diff = compare(result, baseline, PROBLEM_CATEGORIES) if baseline is not None else None
        if diff is not None:
            result.baseline = diff.to_dict()
        written = write_reports(result, formats, args.output, args.basename)
    except (ConfigError, ScanError, argparse.ArgumentTypeError, OSError) as exc:
        print(f"link-rot-checker: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("link-rot-checker: interrupted", file=sys.stderr)
        return EXIT_ERROR
    if not args.quiet:
        print_summary(result, settings, written, stdout)
        if diff is not None:
            print_baseline(diff, stdout)
    bad = failing(result, settings, baseline)
    if len(bad) >= settings.fail_threshold and bad:
        if not args.quiet:
            cats = ",".join(sorted(c.value for c in settings.fail_categories()))
            since = " since the baseline" if baseline is not None else ""
            stdout.write(f"\nFAIL: {len(bad)} target(s) in {cats}{since}\n")
        return EXIT_PROBLEMS
    return EXIT_OK
