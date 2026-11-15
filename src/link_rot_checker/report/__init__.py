"""Report writers."""

from __future__ import annotations

from pathlib import Path

from link_rot_checker.model import ScanResult
from link_rot_checker.report.csv_report import write_csv
from link_rot_checker.report.html_report import render_html
from link_rot_checker.report.json_report import render_json
from link_rot_checker.report.markdown import render_markdown

FORMATS = ("csv", "json", "markdown", "html")


def write_reports(result: ScanResult, formats: list[str], outdir: Path, basename: str = "link-rot") -> list[Path]:
    """Write every requested format into ``outdir`` and return the created files."""
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        if fmt == "csv":
            written.extend(write_csv(result, outdir, basename))
        elif fmt == "json":
            path = outdir / f"{basename}.json"
            path.write_text(render_json(result), encoding="utf-8")
            written.append(path)
        elif fmt == "markdown":
            path = outdir / f"{basename}.md"
            path.write_text(render_markdown(result), encoding="utf-8")
            written.append(path)
        elif fmt == "html":
            path = outdir / f"{basename}.html"
            path.write_text(render_html(result), encoding="utf-8")
            written.append(path)
        else:
            raise ValueError(f"unknown format: {fmt}")
    return written


__all__ = ["FORMATS", "render_html", "render_json", "render_markdown", "write_csv", "write_reports"]
