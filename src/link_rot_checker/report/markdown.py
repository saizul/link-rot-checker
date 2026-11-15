"""Markdown report, for pull request comments, wikis and issue trackers."""

from __future__ import annotations

from link_rot_checker._version import __version__
from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import Category, ScanResult
from link_rot_checker.report.common import label, page_groups, summary


def md_escape(text: object) -> str:
    """Escape text for a Markdown table cell (and neutralise HTML)."""
    value = "" if text is None else str(text)
    value = value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    value = value.replace("<", "&lt;").replace(">", "&gt;")
    for ch in "`*_[]":
        value = value.replace(ch, "\\" + ch)
    return value


def md_url(url: str | None) -> str:
    if not url:
        return ""
    safe = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")
    return f"<{safe}>"


def render_markdown(result: ScanResult, *, max_fix: int = 200, max_pages: int = 200) -> str:
    s = summary(result)
    lines = ["# Link rot report", ""]
    if result.start_urls:
        lines.append("Start: " + ", ".join(md_url(u) for u in result.start_urls[:5]))
        lines.append("")
    lines += [
        f"Scanned {s['pages_crawled']} pages, {s['links']} links to {s['unique_targets']} unique targets "
        f"({s['internal_targets']} internal, {s['external_targets']} external) in {result.duration_s}s.",
        "",
        "| Category | Targets |",
        "| --- | ---: |",
    ]
    for cat in Category:
        if s[cat.value]:
            lines.append(f"| {label(cat)} | {s[cat.value]} |")
    lines.append("")
    fixes = build_fix_list(result)
    lines += ["## Fix list", ""]
    if not fixes:
        lines += ["No problems found.", ""]
    else:
        lines += [
            "Sorted by severity, then by the number of pages that link to the target.",
            "",
            "| # | Target | Problem | Pages | Suggestion |",
            "| ---: | --- | --- | ---: | --- |",
        ]
        for rank, item in enumerate(fixes[:max_fix], 1):
            problem = f"{label(item.category)}: {item.reason}" if item.reason else label(item.category)
            lines.append(
                f"| {rank} | {md_url(item.target)} | {md_escape(problem)} | {item.impact} | "
                f"{md_escape(item.suggestion)} |"
            )
        if len(fixes) > max_fix:
            lines.append(f"\n... and {len(fixes) - max_fix} more (see the CSV or JSON report).")
        lines.append("")
    groups = page_groups(result)
    if groups:
        lines += ["## Problems by page", ""]
        for page, items in groups[:max_pages]:
            lines += [
                f"### {md_url(page)}",
                "",
                "| Link | Anchor | Problem | Suggestion |",
                "| --- | --- | --- | --- |",
            ]
            for link, check in items:
                problem = f"{label(check.category)}: {check.reason}" if check.reason else label(check.category)
                lines.append(
                    f"| {md_url(link.target)} | {md_escape(link.anchor) or '-'} | {md_escape(problem)} | "
                    f"{md_escape(check.suggestion)} |"
                )
            lines.append("")
        if len(groups) > max_pages:
            lines.append(f"... and {len(groups) - max_pages} more pages.\n")
    lines.append(f"<sub>link-rot-checker {__version__}, {result.finished_at}</sub>")
    return "\n".join(lines) + "\n"
