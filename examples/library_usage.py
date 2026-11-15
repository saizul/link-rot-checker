"""Use link-rot-checker from Python.

python examples/library_usage.py https://www.example.com/
"""

from __future__ import annotations

import asyncio
import sys

from link_rot_checker import Category, Settings, build_fix_list, run_scan


async def main(start: str) -> int:
    settings = Settings(start_urls=[start], max_pages=50, wayback=False)
    result = await run_scan(settings)
    for item in build_fix_list(result, {Category.BROKEN, Category.REDIRECT_LOOP}):
        print(f"{item.impact:>3} pages  {item.category.value:<14} {item.target}  ({item.reason})")
        for page in item.pages[:3]:
            print(f"           linked from {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/")))
