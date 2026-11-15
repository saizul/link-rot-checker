"""The fix list: problem targets sorted by how many pages they hurt."""

from __future__ import annotations

from collections import defaultdict

from link_rot_checker.model import PROBLEM_CATEGORIES, SEVERITY, Category, FixItem, ScanResult


def build_fix_list(result: ScanResult, categories: frozenset[Category] | set[Category] | None = None) -> list[FixItem]:
    """One item per problem target, most severe first, then by the number of linking pages.

    ``impact`` is the number of distinct pages that link to the target; ``occurrences`` counts every link,
    including repeats on the same page (menus, footers).
    """
    wanted = categories if categories is not None else PROBLEM_CATEGORIES
    pages: dict[str, set[str]] = defaultdict(set)
    occurrences: dict[str, int] = defaultdict(int)
    for link in result.links:
        pages[link.target].add(link.source)
        occurrences[link.target] += 1
    items: list[FixItem] = []
    for url, check in result.results.items():
        if check.category not in wanted:
            continue
        items.append(
            FixItem(
                target=url,
                category=check.category,
                pages=sorted(pages.get(url, set())),
                occurrences=occurrences.get(url, 0),
                status=check.status,
                reason=check.reason,
                suggestion=check.suggestion,
                archived_url=check.archived_url,
                internal=check.internal,
            )
        )
    items.sort(key=lambda i: (SEVERITY[i.category], -i.impact, -i.occurrences, i.target))
    return items


def impact_score(item: FixItem) -> float:
    """A single number for sorting across categories: pages times severity weight."""
    weight = {0: 10, 1: 9, 2: 8, 3: 7, 4: 3, 5: 2, 6: 1}.get(SEVERITY[item.category], 0)
    return float(item.impact * weight)
