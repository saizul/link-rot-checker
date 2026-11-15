"""CSV reports: every link occurrence, and the fix list."""

from __future__ import annotations

import csv
from pathlib import Path

from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import SEVERITY, ScanResult
from link_rot_checker.report.common import csv_safe, hops_text

LINK_COLUMNS = [
    "source",
    "target",
    "anchor",
    "tag",
    "internal",
    "nofollow",
    "category",
    "status",
    "reason",
    "final_url",
    "redirects",
    "redirect_chain",
    "error_kind",
    "archived_url",
    "suggestion",
]
FIX_COLUMNS = [
    "rank",
    "target",
    "category",
    "impact_pages",
    "occurrences",
    "status",
    "internal",
    "reason",
    "suggestion",
    "archived_url",
    "pages",
]


def link_rows(result: ScanResult) -> list[dict[str, object]]:
    rows = []
    links = sorted(
        result.links,
        key=lambda lk: (
            lk.source,
            SEVERITY[result.results[lk.target].category] if lk.target in result.results else 9,
            lk.target,
        ),
    )
    for link in links:
        check = result.results.get(link.target)
        rows.append(
            {
                "source": link.source,
                "target": link.target,
                "anchor": link.anchor,
                "tag": link.tag,
                "internal": "yes" if link.internal else "no",
                "nofollow": "yes" if link.nofollow else "no",
                "category": check.category.value if check else "",
                "status": check.status if check and check.status is not None else "",
                "reason": check.reason if check else "",
                "final_url": check.final_url if check and check.final_url != link.target else "",
                "redirects": check.redirect_count if check else "",
                "redirect_chain": hops_text(check),
                "error_kind": check.error_kind.value if check and check.error_kind else "",
                "archived_url": check.archived_url or "" if check else "",
                "suggestion": check.suggestion if check else "",
            }
        )
    return rows


def write_csv(result: ScanResult, outdir: Path, basename: str = "link-rot") -> list[Path]:
    links_path = outdir / f"{basename}-links.csv"
    with links_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LINK_COLUMNS)
        writer.writeheader()
        for row in link_rows(result):
            writer.writerow({k: csv_safe(v) for k, v in row.items()})
    fix_path = outdir / f"{basename}-fix-list.csv"
    with fix_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIX_COLUMNS)
        writer.writeheader()
        for rank, item in enumerate(build_fix_list(result), 1):
            writer.writerow(
                {
                    "rank": rank,
                    "target": csv_safe(item.target),
                    "category": item.category.value,
                    "impact_pages": item.impact,
                    "occurrences": item.occurrences,
                    "status": item.status if item.status is not None else "",
                    "internal": "yes" if item.internal else "no",
                    "reason": csv_safe(item.reason),
                    "suggestion": csv_safe(item.suggestion),
                    "archived_url": csv_safe(item.archived_url or ""),
                    "pages": csv_safe(" ".join(item.pages)),
                }
            )
    return [links_path, fix_path]
