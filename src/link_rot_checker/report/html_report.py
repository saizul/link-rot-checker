"""Self-contained HTML report (no scripts, no external resources)."""

from __future__ import annotations

from html import escape

from link_rot_checker._version import __version__
from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import Category, ScanResult
from link_rot_checker.report.common import hops_text, label, page_groups, summary

CSS = """
:root{--bg:#fff;--fg:#1d2330;--muted:#5d6676;--line:#e3e6eb;--card:#f6f7f9;--bad:#b42318;--warn:#b54708;
--info:#175cd3;--ok:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--fg:#e6e8ec;--muted:#9aa3b2;--line:#2a2f39;
--card:#1a1e26;--bad:#f97066;--warn:#fdb022;--info:#84adff;--ok:#47cd89}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--fg)}main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 10px}h3{font-size:15px;margin:0;
word-break:break-all}.muted{color:var(--muted)}a{color:var(--info)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:22px}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted)}td.url{word-break:break-all;max-width:420px}td.n{text-align:right}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap;
border:1px solid currentColor}.broken,.redirect_loop,.parked{color:var(--bad)}
.soft_404,.redirect_chain{color:var(--warn)}
.http_to_https,.redirect{color:var(--info)}.ok{color:var(--ok)}.skipped{color:var(--muted)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:8px 12px}
summary{cursor:pointer}summary h3{display:inline}.chain{font-size:12px;color:var(--muted);word-break:break-all}
footer{margin-top:40px;font-size:12px;color:var(--muted)}
"""


def _a(url: str | None) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        return escape(url)
    return f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer nofollow">{escape(url)}</a>'


def _tag(category: Category) -> str:
    return f'<span class="tag {category.value}">{escape(label(category))}</span>'


def render_html(result: ScanResult, *, max_fix: int = 500, max_pages: int = 500) -> str:
    s = summary(result)
    out: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="robots" content="noindex">',
        "<title>Link rot report</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<h1>Link rot report</h1>",
    ]
    if result.start_urls:
        out.append('<p class="muted">' + ", ".join(_a(u) for u in result.start_urls[:5]) + "</p>")
    out.append(
        f'<p class="muted">{s["pages_crawled"]} pages, {s["links"]} links, {s["unique_targets"]} unique targets, '
        f"{escape(str(result.duration_s))}s, finished {escape(result.finished_at)}</p>"
    )
    out.append('<div class="cards">')
    out.append(f'<div class="card"><b>{s["problems"]}</b>problem targets</div>')
    out.append(f'<div class="card"><b>{s["pages_with_problems"]}</b>pages to edit</div>')
    for cat in Category:
        if s[cat.value]:
            out.append(f'<div class="card"><b class="{cat.value}">{s[cat.value]}</b>{escape(label(cat))}</div>')
    out.append("</div>")

    fixes = build_fix_list(result)
    out.append("<h2>Fix list</h2>")
    if not fixes:
        out.append("<p>No problems found.</p>")
    else:
        out.append('<p class="muted">Most severe first, then by the number of pages linking to the target.</p>')
        out.append(
            "<table><thead><tr><th>#</th><th>Target</th><th>Problem</th><th>Pages</th><th>Suggestion</th></tr>"
            "</thead><tbody>"
        )
        for rank, item in enumerate(fixes[:max_fix], 1):
            suggestion = escape(item.suggestion)
            if item.archived_url:
                suggestion += f"<br>{_a(item.archived_url)}"
            out.append(
                f'<tr><td class="n">{rank}</td><td class="url">{_a(item.target)}</td>'
                f'<td>{_tag(item.category)} {escape(item.reason)}</td><td class="n">{item.impact}</td>'
                f"<td>{suggestion}</td></tr>"
            )
        out.append("</tbody></table>")
        if len(fixes) > max_fix:
            out.append(f'<p class="muted">... and {len(fixes) - max_fix} more in the CSV/JSON report.</p>')

    groups = page_groups(result)
    if groups:
        out.append("<h2>Problems by page</h2>")
        for page, items in groups[:max_pages]:
            out.append(
                f"<details open><summary><h3>{escape(page)}</h3> "
                f'<span class="muted">({len(items)} link{"s" if len(items) != 1 else ""})</span></summary>'
            )
            out.append(f'<p class="muted">{_a(page)}</p>')
            out.append(
                "<table><thead><tr><th>Link</th><th>Anchor</th><th>Problem</th><th>Suggestion</th></tr></thead><tbody>"
            )
            for link, check in items:
                chain = hops_text(check)
                chain_html = f'<div class="chain">{escape(chain)}</div>' if chain else ""
                out.append(
                    f'<tr><td class="url">{_a(link.target)}{chain_html}</td>'
                    f"<td>{escape(link.anchor) or '<span class=muted>-</span>'}"
                    f' <span class="muted">&lt;{escape(link.tag)}&gt;</span></td>'
                    f"<td>{_tag(check.category)} {escape(check.reason)}</td><td>{escape(check.suggestion)}</td></tr>"
                )
            out.append("</tbody></table></details>")
    out.append(f"<footer>link-rot-checker {escape(__version__)}</footer></main></body></html>")
    return "\n".join(out) + "\n"
