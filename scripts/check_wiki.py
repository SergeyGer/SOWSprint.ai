#!/usr/bin/env python
"""Keep the wiki and its navigation honest.

Why this exists
---------------
The wiki content lives in ``docs/wiki/`` inside this repository, and CI copies it to the
GitHub wiki on merge. That arrangement exists so wiki edits are **reviewable**: a GitHub
wiki is a separate git repository, so a change to it cannot be seen in a pull request, and
documentation that cannot be reviewed drifts from the code it describes.

Two failure modes follow, and both are silent:

* a page is added but never linked from the sidebar, so nobody finds it;
* a page is renamed or deleted and the sidebar still points at it, so a reader gets a 404.

Neither breaks a build on its own, which is exactly why a check has to exist.

Usage::

    python scripts/check_wiki.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "docs" / "wiki"
SIDEBAR = WIKI / "_Sidebar.md"

#: Pages GitHub treats as metadata rather than content.
SPECIAL = {"_Sidebar.md", "_Footer.md"}

#: `[Label](Page-Name)` inside the sidebar.
SIDEBAR_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

#: Pages that are navigation rather than destinations.
NAVIGATION_ONLY = {"Home"}


def main() -> int:
    if not WIKI.is_dir():
        print(f"✗ {WIKI.relative_to(ROOT)} does not exist", file=sys.stderr)
        return 1

    pages = {p.stem for p in WIKI.glob("*.md")} - {Path(s).stem for s in SPECIAL}
    if not pages:
        print(f"✗ no wiki pages found in {WIKI.relative_to(ROOT)}", file=sys.stderr)
        return 1

    if not SIDEBAR.is_file():
        print("✗ _Sidebar.md is missing; the wiki would have no navigation", file=sys.stderr)
        return 1

    sidebar_text = SIDEBAR.read_text(encoding="utf-8")
    # Only wiki-internal links name pages. The sidebar footer points at the repository
    # and the README, and treating those as page names produced a false "404" on a
    # sidebar that was perfectly correct.
    linked = {
        target.strip()
        for target in SIDEBAR_LINK.findall(sidebar_text)
        if not target.strip().startswith(("http://", "https://", "#", "mailto:"))
    }

    problems: list[str] = []

    unreachable = sorted(pages - linked - NAVIGATION_ONLY)
    if unreachable:
        problems.append(
            "page(s) exist but the sidebar never links them, so a reader cannot find "
            "them: " + ", ".join(unreachable)
        )

    dangling = sorted(linked - pages)
    if dangling:
        problems.append(
            "the sidebar links page(s) that do not exist, so a reader gets a 404: "
            + ", ".join(dangling)
        )

    # A page with no heading is almost always a truncated file.
    for name in sorted(pages):
        body = (WIKI / f"{name}.md").read_text(encoding="utf-8")
        if not any(line.lstrip().startswith("#") for line in body.splitlines()):
            problems.append(f"{name}.md has no heading")
        if len(body.strip()) < 200:
            problems.append(f"{name}.md is suspiciously short ({len(body)} chars)")

    print(f"  {len(pages)} page(s), {len(linked)} sidebar link(s)")
    if problems:
        print(f"\n  ❌ {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"    · {problem}")
        return 1

    print("  ✅ every page is reachable and every link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
