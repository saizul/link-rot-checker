"""Find broken and decaying links on a website."""

from link_rot_checker._version import __version__
from link_rot_checker.config import Settings
from link_rot_checker.fixlist import build_fix_list
from link_rot_checker.model import Category, CheckResult, FixItem, Link, ScanResult
from link_rot_checker.scan import ScanError, run_scan

__all__ = [
    "Category",
    "CheckResult",
    "FixItem",
    "Link",
    "ScanError",
    "ScanResult",
    "Settings",
    "__version__",
    "build_fix_list",
    "run_scan",
]
