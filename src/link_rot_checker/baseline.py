"""Compare a scan with an earlier JSON report, so CI can fail on *new* link rot only.

A baseline is the ``link-rot.json`` file of an earlier run. Targets are matched by URL. A target counts as new
when it is a problem now and was not one (or was a less severe one) in the baseline; it counts as fixed when it
was a problem and now is not, or when no page links to it any more.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from link_rot_checker.config import ConfigError
from link_rot_checker.model import PROBLEM_CATEGORIES, SEVERITY, Category, ScanResult


@dataclass
class Baseline:
    """Target categories from an earlier report."""

    source: str
    categories: dict[str, Category]
    finished_at: str = ""


@dataclass
class BaselineDiff:
    """How the problem targets changed since the baseline."""

    source: str
    new: list[str] = field(default_factory=list)
    worse: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[str]:
        """New problems plus problems that became more severe, sorted."""
        return sorted({*self.new, *self.worse})

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "new": self.new,
            "worse": self.worse,
            "fixed": self.fixed,
            "unchanged": self.unchanged,
        }


def baseline_from_dict(data: Any, source: str = "<baseline>") -> Baseline:
    if not isinstance(data, dict) or data.get("tool") != "link-rot-checker":
        raise ConfigError(f"{source} is not a link-rot-checker JSON report")
    if data.get("schema") != 1:
        raise ConfigError(f"{source}: unsupported report schema {data.get('schema')!r} (expected 1)")
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ConfigError(f"{source}: the report has no 'targets' list")
    categories: dict[str, Category] = {}
    valid = {c.value for c in Category}
    for item in targets:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("category") in valid:
            categories[item["url"]] = Category(item["category"])
    return Baseline(source, categories, str(data.get("finished_at") or ""))


def load_baseline(path: str | Path) -> Baseline:
    """Read a ``link-rot.json`` report written by an earlier run."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read baseline {path}: {exc.strerror or exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"baseline {path} is not valid JSON: {exc}") from exc
    return baseline_from_dict(data, str(path))


def compare(
    result: ScanResult, baseline: Baseline, categories: Iterable[Category] = PROBLEM_CATEGORIES
) -> BaselineDiff:
    """Classify the targets of ``result`` against ``baseline`` for the given problem categories."""
    wanted = set(categories)
    diff = BaselineDiff(baseline.source)
    current = {url: check.category for url, check in result.results.items()}
    for url in sorted(current):
        now = current[url]
        if now not in wanted:
            continue
        before = baseline.categories.get(url)
        if before is None or before not in wanted:
            diff.new.append(url)
        elif SEVERITY[now] < SEVERITY[before]:
            diff.worse.append(url)
        else:
            diff.unchanged.append(url)
    for url in sorted(baseline.categories):
        if baseline.categories[url] in wanted and current.get(url) not in wanted:
            diff.fixed.append(url)
    return diff
