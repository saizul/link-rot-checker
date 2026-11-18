import json

import pytest

from link_rot_checker.baseline import Baseline, baseline_from_dict, compare, load_baseline
from link_rot_checker.cli import EXIT_ERROR, EXIT_OK, EXIT_PROBLEMS
from link_rot_checker.config import ConfigError, load_config_file
from link_rot_checker.model import Category, CheckResult, ScanResult
from test_config_cli import run_cli


def result(**categories: str) -> ScanResult:
    results = {
        f"https://example.com/{name}": CheckResult(url=f"https://example.com/{name}", category=Category(cat))
        for name, cat in categories.items()
    }
    return ScanResult(start_urls=[], pages={}, links=[], results=results)


def baseline(**categories: str) -> Baseline:
    return Baseline("old.json", {f"https://example.com/{k}": Category(v) for k, v in categories.items()})


def short(urls):
    return [url.rsplit("/", 1)[1] for url in urls]


def test_compare_classifies_targets():
    diff = compare(
        result(a="broken", b="broken", c="ok", d="redirect", e="broken"),
        baseline(a="broken", b="redirect", c="broken", e="soft_404", gone="parked"),
    )
    assert short(diff.new) == ["d"]
    assert short(diff.worse) == ["b", "e"]
    assert short(diff.unchanged) == ["a"]
    assert short(diff.fixed) == ["c", "gone"]
    assert short(diff.regressions) == ["b", "d", "e"]


def test_compare_limited_to_categories():
    diff = compare(result(a="broken", b="redirect"), baseline(b="ok"), {Category.BROKEN})
    assert short(diff.new) == ["a"]
    assert diff.fixed == [] and diff.unchanged == []


def test_baseline_from_dict_validates():
    data = {"tool": "link-rot-checker", "schema": 1, "finished_at": "2026-11-01T00:00:00Z",
            "targets": [{"url": "https://x.test/", "category": "broken"}, {"url": 5}, {"category": "weird"}]}
    parsed = baseline_from_dict(data, "b.json")
    assert parsed.categories == {"https://x.test/": Category.BROKEN}
    assert parsed.finished_at.startswith("2026-11-01")
    with pytest.raises(ConfigError, match="not a link-rot-checker"):
        baseline_from_dict({"tool": "other"})
    with pytest.raises(ConfigError, match="schema"):
        baseline_from_dict({"tool": "link-rot-checker", "schema": 2})
    with pytest.raises(ConfigError, match="targets"):
        baseline_from_dict({"tool": "link-rot-checker", "schema": 1})


def test_load_baseline_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read baseline"):
        load_baseline(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_baseline(bad)


def first_report(tmp_path):
    code, _out, _web = run_cli(["scan", "https://example.com/", "-f", "json", "-q"], tmp_path)
    assert code == EXIT_PROBLEMS
    saved = tmp_path / "baseline.json"
    saved.write_text((tmp_path / "out" / "link-rot.json").read_text())
    return saved


def test_cli_baseline_with_no_changes_passes(tmp_path):
    saved = first_report(tmp_path)
    code, out, _web = run_cli(["scan", "https://example.com/", "-f", "json", "--baseline", str(saved)], tmp_path)
    assert code == EXIT_OK
    assert f"compared with {saved}: 0 new, 0 worse, 0 fixed," in out
    assert "FAIL:" not in out
    data = json.loads((tmp_path / "out" / "link-rot.json").read_text())
    assert data["baseline"]["new"] == [] and data["baseline"]["unchanged"]


def test_cli_baseline_fails_on_new_problems_only(tmp_path):
    saved = first_report(tmp_path)
    data = json.loads(saved.read_text())
    for target in data["targets"]:
        if target["url"] == "https://example.com/missing":
            target["category"] = "ok"
        if target["url"] == "https://ext.test/gone":
            target["category"] = "redirect"
    data["targets"].append({"url": "https://example.com/was-broken", "category": "broken"})
    saved.write_text(json.dumps(data))
    code, out, _web = run_cli(["scan", "https://example.com/", "--baseline", str(saved)], tmp_path)
    assert code == EXIT_PROBLEMS
    assert "  new: https://example.com/missing" in out
    assert "  fixed: https://example.com/was-broken" in out
    assert "FAIL: 2 target(s) in broken,redirect_loop since the baseline" in out


def test_cli_baseline_errors_exit_2(tmp_path):
    code, _out, web = run_cli(["scan", "https://example.com/", "--baseline", str(tmp_path / "nope.json")], tmp_path)
    assert code == EXIT_ERROR
    assert web.requests == []


def test_baseline_in_config_file(tmp_path):
    path = tmp_path / "lrc.toml"
    path.write_text('[link-rot-checker]\nbaseline = "reports/link-rot.json"\n')
    assert load_config_file(path).baseline == "reports/link-rot.json"
