import csv
import json

from conftest import fast_settings, scan
from fakeweb import FakeWeb, html
from link_rot_checker.fixlist import build_fix_list, impact_score
from link_rot_checker.model import Category
from link_rot_checker.report import write_reports
from link_rot_checker.report.common import csv_safe, page_groups, summary
from link_rot_checker.report.html_report import render_html
from link_rot_checker.report.json_report import render_json
from link_rot_checker.report.markdown import md_escape, render_markdown


def test_fix_list_sorted_by_severity_then_impact(demo_result):
    fixes = build_fix_list(demo_result)
    assert fixes[0].category is Category.BROKEN
    broken = [f for f in fixes if f.category is Category.BROKEN]
    assert [f.target for f in broken[:2]] == ["https://example.com/missing", "https://ext.test/gone"]
    assert broken[0].impact == 3  # home, about, post-1
    assert broken[0].occurrences == 3
    order = [f.category for f in fixes]
    assert order.index(Category.REDIRECT_LOOP) < order.index(Category.PARKED) < order.index(Category.REDIRECT)
    assert Category.OK not in order and Category.SKIPPED not in order
    assert impact_score(broken[0]) == 30.0


def test_fix_list_category_filter(demo_result):
    only = build_fix_list(demo_result, {Category.PARKED})
    assert [f.target for f in only] == ["https://parked.test/"]


def test_page_groups(demo_result):
    groups = dict(page_groups(demo_result))
    assert "https://example.com/blog/" not in groups  # no problems there
    assert [c.category for _, c in groups["https://example.com/blog/post-1"]] == [Category.BROKEN, Category.BROKEN]
    everything = dict(page_groups(demo_result, problems_only=False))
    assert "https://example.com/blog/" in everything


def test_summary(demo_result):
    s = summary(demo_result)
    assert s["pages_crawled"] == len(demo_result.pages)
    assert s["broken"] == 4
    assert s["problems"] == sum(
        s[c] for c in ("broken", "redirect_loop", "parked", "soft_404", "redirect_chain", "http_to_https", "redirect")
    )


def test_write_all_formats(demo_result, tmp_path):
    paths = write_reports(demo_result, ["csv", "json", "markdown", "html"], tmp_path, "site")
    names = sorted(p.name for p in paths)
    assert names == ["site-fix-list.csv", "site-links.csv", "site.html", "site.json", "site.md"]
    with (tmp_path / "site-links.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(demo_result.links)
    gone = [r for r in rows if r["target"] == "https://ext.test/gone"]
    assert gone[0]["archived_url"].startswith("https://web.archive.org/")
    chain = next(r for r in rows if r["target"] == "https://example.com/chain-1")
    assert chain["redirects"] == "2" and "302" in chain["redirect_chain"]
    with (tmp_path / "site-fix-list.csv").open() as fh:
        fixes = list(csv.DictReader(fh))
    assert fixes[0]["rank"] == "1" and fixes[0]["impact_pages"] == "3"
    data = json.loads((tmp_path / "site.json").read_text())
    assert data["schema"] == 1 and data["tool"] == "link-rot-checker"
    assert data["fix_list"][0]["pages"] == sorted(data["fix_list"][0]["pages"])
    assert {t["url"] for t in data["targets"]} == set(demo_result.results)
    md = (tmp_path / "site.md").read_text()
    assert "## Fix list" in md and "<https://ext.test/gone>" in md
    page = (tmp_path / "site.html").read_text()
    assert page.startswith("<!DOCTYPE html>") and "<script" not in page and "http" in page


def evil_result():
    web = FakeWeb()
    web.add(
        "https://e.test/",
        html(
            '<a href="/x?q=<script>">=HYPERLINK("http://evil")</a>'
            '<a href="/y">| pipe *bold* <img src=x onerror=alert(1)></a>',
            title="<b>t</b>",
        ),
    )
    return scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False))


def test_reports_escape_untrusted_text(tmp_path):
    result = evil_result()
    page = render_html(result)
    assert "<script>" not in page
    assert "<img" not in page
    md = render_markdown(result)
    assert "<script>" not in md
    assert "\\| pipe \\*bold\\*" in md
    write_reports(result, ["csv"], tmp_path)
    content = (tmp_path / "link-rot-links.csv").read_text()
    assert "'=HYPERLINK" in content
    json.loads(render_json(result))


def test_csv_safe_and_md_escape():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("-x") == "'-x"
    assert csv_safe("https://x") == "https://x"
    assert csv_safe(None) == ""
    assert md_escape("a|b\nc<d>") == "a\\|b c&lt;d&gt;"


def test_clean_site_report():
    web = FakeWeb().add("https://e.test/", html('<a href="/a">a</a>')).add("https://e.test/a", html("a"))
    result = scan(web, fast_settings(start_urls=["https://e.test/"], wayback=False))
    assert build_fix_list(result) == []
    assert "No problems found." in render_markdown(result)
    assert "No problems found." in render_html(result)
