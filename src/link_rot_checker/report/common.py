"""Helpers shared by the report writers."""

from __future__ import annotations

from link_rot_checker.model import LABELS, PROBLEM_CATEGORIES, SEVERITY, Category, CheckResult, Link, ScanResult


def label(category: Category) -> str:
    return LABELS[category]


def hops_text(result: CheckResult | None) -> str:
    if result is None or len(result.hops) < 2:
        return ""
    return " -> ".join(f"{h.status} {h.url}" for h in result.hops)


def page_groups(result: ScanResult, *, problems_only: bool = True) -> list[tuple[str, list[tuple[Link, CheckResult]]]]:
    """``[(source page, [(link, result), ...]), ...]``: pages with the most severe problems first."""
    groups: dict[str, list[tuple[Link, CheckResult]]] = {}
    for link in result.links:
        check = result.results.get(link.target)
        if check is None:
            continue
        if problems_only and check.category not in PROBLEM_CATEGORIES:
            continue
        groups.setdefault(link.source, []).append((link, check))
    for items in groups.values():
        items.sort(key=lambda lc: (SEVERITY[lc[1].category], lc[0].target))
    return sorted(
        groups.items(),
        key=lambda kv: (min(SEVERITY[c.category] for _, c in kv[1]), -len(kv[1]), kv[0]),
    )


def summary(result: ScanResult) -> dict[str, int]:
    counts = result.counts()
    return {
        "pages_crawled": len(result.pages),
        "links": len(result.links),
        "unique_targets": len(result.results),
        "internal_targets": sum(1 for r in result.results.values() if r.internal),
        "external_targets": sum(1 for r in result.results.values() if not r.internal),
        "problems": sum(counts[c.value] for c in PROBLEM_CATEGORIES),
        "pages_with_problems": len(page_groups(result)),
        **counts,
    }


SPREADSHEET_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formulas in untrusted text (anchor text, URLs from the crawled site)."""
    text = "" if value is None else str(value)
    if text.startswith(SPREADSHEET_TRIGGERS):
        return "'" + text
    return text
