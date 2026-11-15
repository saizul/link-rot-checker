"""JSON report (a stable contract: see CHANGELOG before renaming keys)."""

from __future__ import annotations

import json
from typing import Any

from link_rot_checker._version import __version__
from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import CheckResult, ScanResult
from link_rot_checker.report.common import page_groups, summary

SCHEMA_VERSION = 1


def _check(c: CheckResult) -> dict[str, Any]:
    return {
        "url": c.url,
        "category": c.category.value,
        "status": c.status,
        "reason": c.reason,
        "internal": c.internal,
        "final_url": c.final_url,
        "redirects": c.redirect_count,
        "hops": [{"url": h.url, "status": h.status} for h in c.hops],
        "error_kind": c.error_kind.value if c.error_kind else None,
        "error": c.error or None,
        "method": c.method or None,
        "elapsed_ms": c.elapsed_ms,
        "archived_url": c.archived_url,
        "archived_timestamp": c.archived_timestamp,
        "suggestion": c.suggestion or None,
    }


def to_dict(result: ScanResult) -> dict[str, Any]:
    return {
        "tool": "link-rot-checker",
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_s": result.duration_s,
        "start_urls": result.start_urls,
        "summary": summary(result),
        "fix_list": [
            {
                "rank": rank,
                "target": i.target,
                "category": i.category.value,
                "impact": i.impact,
                "occurrences": i.occurrences,
                "status": i.status,
                "internal": i.internal,
                "reason": i.reason,
                "suggestion": i.suggestion,
                "archived_url": i.archived_url,
                "pages": i.pages,
            }
            for rank, i in enumerate(build_fix_list(result), 1)
        ],
        "pages_with_problems": [
            {
                "page": page,
                "links": [
                    {
                        "target": link.target,
                        "anchor": link.anchor,
                        "tag": link.tag,
                        "category": c.category.value,
                        "status": c.status,
                        "reason": c.reason,
                        "suggestion": c.suggestion or None,
                    }
                    for link, c in items
                ],
            }
            for page, items in page_groups(result)
        ],
        "targets": [_check(c) for c in sorted(result.results.values(), key=lambda c: c.url)],
        "pages": [
            {
                "url": p.url,
                "status": p.status,
                "depth": p.depth,
                "title": p.title,
                "final_url": p.final_url,
                "links": p.links_found,
                "error": p.error or None,
            }
            for p in sorted(result.pages.values(), key=lambda p: (p.depth, p.url))
        ],
        "links": [
            {
                "source": lk.source,
                "target": lk.target,
                "anchor": lk.anchor,
                "tag": lk.tag,
                "internal": lk.internal,
                "nofollow": lk.nofollow,
            }
            for lk in result.links
        ],
        "skipped_pages": result.skipped,
        # header_env maps header names to environment variable *names*; values are never stored.
        "settings": result.settings,
    }


def render_json(result: ScanResult) -> str:
    return json.dumps(to_dict(result), indent=2, ensure_ascii=False) + "\n"
