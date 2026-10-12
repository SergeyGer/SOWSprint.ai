#!/usr/bin/env python
"""Render Mermaid diagrams to images for the GitHub wiki.

The problem this solves
-----------------------
GitHub renders Mermaid in repository Markdown, in issues and in pull requests — **but
not in wikis**. A wiki page full of ```mermaid blocks therefore shows the diagram source
as literal text, which is what a reader experiences as "the page is just unformatted
Markdown".

The fix keeps one source of truth. The `.md` files in ``docs/wiki/`` keep Mermaid, because
Mermaid is text: it appears in the diff, it can be reviewed, and it renders natively
everywhere except the wiki. This script renders those blocks to PNG and emits a *wikified*
copy of each page with the blocks replaced by image references, which is what the publish
job uploads.

Two outputs, one source:

    docs/wiki/Architecture.md                  <- Mermaid source, reviewed in PRs
    docs/images/diagrams/architecture-1.png    <- rendered, committed
    build/wiki/Architecture.md                 <- wikified copy, uploaded

Images are committed rather than built on the fly so that a reader of the repository can
see them, and so the publish job needs no rendering step of its own.

Usage::

    python scripts/render_diagrams.py             # render + wikify
    python scripts/render_diagrams.py --check     # fail if any image is stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "docs" / "wiki"
IMAGES = ROOT / "docs" / "images" / "diagrams"
BUILD = ROOT / "build" / "wiki"

#: Fenced ```mermaid block, capturing the diagram source.
MERMAID_RE = re.compile(r"^```mermaid\n(.*?)^```$", re.S | re.M)

#: Absolute raw URL, because the wiki is a different repository and a relative path from
#: there would resolve against the wiki, not against docs/images/.
RAW_BASE = "https://raw.githubusercontent.com/SergeyGer/SOWSprint.ai/main/docs/images/diagrams"

#: A neutral theme on white. GitHub wikis respect the reader's colour scheme and a
#: transparent diagram would need per-theme variants; white is legible in both, which is
#: the trade-off chosen here deliberately.
# Plain string, not a format template: the braces below are literal JavaScript and
# doubling them for a .format() call that never happens sends `{{` to the browser, where
# it is a syntax error and the whole helper silently never defines itself.
PAGE = """
<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#ffffff}</style></head><body>
<div id="out"></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' });
  window.renderDiagram = async (src) => {
    const { svg } = await mermaid.render('diagram', src);
    document.getElementById('out').innerHTML = svg;
    const el = document.querySelector('svg');
    if (!el) return null;
    const box = el.getBoundingClientRect();
    return { w: Math.ceil(box.width), h: Math.ceil(box.height) };
  };
</script></body></html>
"""


def digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def render_all(entries: list[tuple[Path, list[str]]]) -> dict[str, str]:
    """Render every diagram, returning {key: digest} for the manifest."""
    from playwright.sync_api import sync_playwright

    IMAGES.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=2)
        page.set_content(PAGE)
        page.wait_for_timeout(4000)

        for path, diagrams in entries:
            slug = path.stem.lower()
            for index, source in enumerate(diagrams, start=1):
                key = f"{slug}-{index}"
                size = page.evaluate("(src) => window.renderDiagram(src)", source)
                if not size:
                    print(f"    ✗ {key}: mermaid produced no SVG", file=sys.stderr)
                    continue
                page.wait_for_timeout(350)

                target = IMAGES / f"{key}.png"
                # Screenshot the SVG element itself, so the image is the diagram and not
                # the surrounding page.
                page.locator("svg").first.screenshot(path=str(target))
                manifest[key] = digest(source)
                print(f"    {key:34} {size['w']:>4}x{size['h']:<4} {target.stat().st_size // 1024:>4} KB")

        browser.close()
    return manifest


def wikify(path: Path, diagrams: list[str]) -> str:
    """Replace each mermaid block with an image reference."""
    slug = path.stem.lower()
    counter = {"n": 0}

    def replace(match: re.Match) -> str:
        counter["n"] += 1
        key = f"{slug}-{counter['n']}"
        return f"![Diagram]({RAW_BASE}/{key}.png)"

    return MERMAID_RE.sub(replace, path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Verify images match the sources; render nothing.")
    parser.add_argument(
        "--wikify-only",
        action="store_true",
        help="Rewrite the pages using the committed images. No browser needed, which is "
        "what lets the publish job run without installing Playwright.",
    )
    args = parser.parse_args(argv)

    if not WIKI.is_dir():
        print(f"✗ {WIKI.relative_to(ROOT)} does not exist", file=sys.stderr)
        return 1

    entries: list[tuple[Path, list[str]]] = []
    for path in sorted(WIKI.glob("*.md")):
        diagrams = MERMAID_RE.findall(path.read_text(encoding="utf-8"))
        if diagrams:
            entries.append((path, diagrams))

    total = sum(len(d) for _, d in entries)
    print(f"  {total} diagram(s) across {len(entries)} page(s)")

    manifest_path = IMAGES / "manifest.json"

    if args.wikify_only:
        BUILD.mkdir(parents=True, exist_ok=True)
        for path in sorted(WIKI.glob("*.md")):
            (BUILD / path.name).write_text(wikify(path, []), encoding="utf-8")
        print(f"  wikified pages written to {BUILD.relative_to(ROOT)}/")
        return 0

    if args.check:
        if not manifest_path.is_file():
            print("  ✗ no manifest; run without --check first", file=sys.stderr)
            return 1
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale = [
            f"{path.stem.lower()}-{i}"
            for path, diagrams in entries
            for i, src in enumerate(diagrams, start=1)
            if recorded.get(f"{path.stem.lower()}-{i}") != digest(src)
        ]
        for path, diagrams in entries:
            for i in range(1, len(diagrams) + 1):
                if not (IMAGES / f"{path.stem.lower()}-{i}.png").is_file():
                    stale.append(f"{path.stem.lower()}-{i}.png")
        if stale:
            print(f"\n  ❌ {len(stale)} diagram(s) out of date: {', '.join(sorted(set(stale)))}")
            print("     Run: python scripts/render_diagrams.py")
            return 1
        print("  ✅ every rendered diagram matches its source")
        return 0

    manifest = render_all(entries)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    BUILD.mkdir(parents=True, exist_ok=True)
    for path in sorted(WIKI.glob("*.md")):
        (BUILD / path.name).write_text(wikify(path, []), encoding="utf-8")
    print(f"  wikified pages written to {BUILD.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
