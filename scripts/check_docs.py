#!/usr/bin/env python
"""Verify that every documentation asset reference resolves.

Why this exists
---------------
A broken image in a README is worse than a missing one: GitHub renders a broken-image
icon, and the reader concludes the project is unmaintained. Worse, it is invisible to the
author — the file exists on their machine, because it is gitignored or untracked.

Link checkers cover URLs. They do not cover this, because a relative path to a file that
exists in your working tree but not in the repository is a perfectly valid path. That
happened here: the README referenced `docs/images/*.png` that were gitignored at the time.

What it checks
--------------
1. every relative link and image reference in Markdown resolves to a file that is
   **tracked by git** — existing on disk is not enough
2. every in-page anchor (``#section``) matches a heading in the target document
3. every wiki page referenced from the README exists in ``docs/wiki/``

Usage::

    python scripts/check_docs.py
    python scripts/check_docs.py --fix-suggestions
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Markdown files that are part of the published documentation.
DOC_GLOBS = ["README.md", "CONTRIBUTING.md", "chainlit.md", "docs/**/*.md"]

#: `[text](target)` and `![alt](target)` — target captured without any title.
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")

#: HTML attributes. The README embeds its screenshots as `<img src="docs/images/...">`
#: rather than Markdown image syntax, because HTML allows the sizing that a screenshot
#: gallery needs. A checker that only understood Markdown reported success while every
#: image reference in the file went unexamined — which is how the first version of this
#: script passed a deliberately broken link.
HTML_RE = re.compile(r"<(?:img|a|source|video)\b[^>]*?\b(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.I)

#: Fenced code blocks are skipped: they contain illustrative paths, not references.
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "tel:")

#: A github.com wiki URL ending in ``.md`` looks like a link to the page and is not one.
#: GitHub treats ``wiki/Page.md`` as a request for the raw file and 302s the reader to
#: unrendered Markdown. Inside the wiki itself the same syntax is fine — GitHub normalises
#: it — so the rule applies only to absolute URLs written outside the wiki. This was a real
#: regression: 15 README links were changed to the .md form for consistency with the
#: sidebar, and every one of them started sending readers to raw text.
WIKI_MD_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/wiki/[^)\s]+\.md")


def tracked_files() -> set[str]:
    """Repository-relative paths git tracks. Untracked files do not ship."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return set(out.splitlines())


def anchor_of(heading: str) -> str:
    """GitHub's heading-to-anchor algorithm, near enough for our own headings."""
    text = heading.strip().lstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def anchors_in(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    body = FENCE_RE.sub("", path.read_text(encoding="utf-8", errors="ignore"))
    return {
        anchor_of(line)
        for line in body.splitlines()
        if line.lstrip().startswith("#")
    }


def check(fix_suggestions: bool) -> int:
    tracked = tracked_files()
    problems: list[tuple[str, int, str, str]] = []
    checked = 0

    files: list[Path] = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    files = [f for f in files if f.is_file()]

    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        body = FENCE_RE.sub("", path.read_text(encoding="utf-8", errors="ignore"))

        for lineno, line in enumerate(body.splitlines(), start=1):
            targets = [m.group(1) for m in LINK_RE.finditer(line)]
            targets += [m.group(1) for m in HTML_RE.finditer(line)]
            for raw in targets:
                target = raw.strip()
                if not target:
                    continue

                # Checked before the scheme skip below, because this IS an http URL and
                # would otherwise pass straight through.
                if WIKI_MD_URL.match(target):
                    problems.append(
                        (rel, lineno, target, "wiki URL with .md sends the reader to raw text")
                    )
                    checked += 1
                    continue

                if target.startswith(SKIP_PREFIXES):
                    continue
                checked += 1

                file_part, _, anchor = target.partition("#")
                if not file_part:                       # same-page anchor
                    if anchor and anchor not in anchors_in(path):
                        problems.append((rel, lineno, target, f"no heading '#{anchor}' here"))
                    continue

                resolved = (path.parent / file_part).resolve()

                # Inside docs/wiki/, a link without an extension is a GitHub wiki page
                # reference: `[Label](Corporate-LLM)` means Corporate-LLM.md. Checking it
                # as a literal path reports every sidebar link as broken.
                if not resolved.exists() and path.parent == (ROOT / "docs" / "wiki"):
                    candidate = resolved.with_suffix(".md")
                    if candidate.exists():
                        resolved = candidate
                try:
                    repo_rel = resolved.relative_to(ROOT).as_posix()
                except ValueError:
                    problems.append((rel, lineno, target, "resolves outside the repository"))
                    continue

                if not resolved.exists():
                    problems.append((rel, lineno, target, "file does not exist"))
                elif resolved.is_dir():
                    # A directory link is valid — `docs/adr/` is a useful destination —
                    # but git tracks files rather than directories, so it needs its own
                    # check: the directory must contain something tracked, or the link
                    # works locally and 404s for everyone else.
                    prefix = repo_rel.rstrip("/") + "/"
                    if not any(t.startswith(prefix) for t in tracked):
                        problems.append(
                            (rel, lineno, target, "directory exists but holds no tracked files")
                        )
                elif repo_rel not in tracked:
                    problems.append(
                        (rel, lineno, target, "exists locally but is NOT tracked by git")
                    )
                elif anchor and anchor not in anchors_in(resolved):
                    problems.append((rel, lineno, target, f"target has no heading '#{anchor}'"))

    print(f"  checked {checked} reference(s) across {len(files)} document(s)")
    if not problems:
        print("  ✅ every reference resolves to a tracked file")
        return 0

    print(f"\n  ❌ {len(problems)} broken reference(s)\n")
    for rel, lineno, target, why in problems:
        print(f"    {rel}:{lineno}  ->  {target}")
        print(f"        {why}")
    if fix_suggestions:
        print("\n  Likely causes, in order of how often they bite here:")
        print("    · the file is gitignored, so it never reaches the repository")
        print("    · the file was renamed and the reference was not")
        print("    · a wiki page was renamed without updating the sidebar and README")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix-suggestions", action="store_true")
    args = parser.parse_args(argv)
    return check(args.fix_suggestions)


if __name__ == "__main__":
    raise SystemExit(main())
