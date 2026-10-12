#!/usr/bin/env python
"""Generate docs/badges.json, the data behind the README's technology badges.

Why this indirection
--------------------
A badge with a version typed into it is a claim that decays. `LangGraph 0.2` stays on the
README long after the dependency moved, and nothing fails — the badge just becomes wrong.
Readers have no way to tell a current badge from a stale one, which is the same problem the
rest of this repository's documentation checks exist to prevent.

So the badges are read from the files that actually govern the versions: ``requirements.txt``
for the libraries, the CI matrix for Python, the Dockerfile for the base image. Shields.io
fetches the generated JSON over `dynamic/json`, which means the README shows what the
repository declares, and a dependency bump updates the badge with no edit to the README.

Regenerate after changing a dependency::

    python scripts/gen_badges.py

``--check`` fails when the file is out of date, which is what runs in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "badges.json"

#: Badge label -> distribution name in requirements.txt.
#:
#: Only the core stack: a badge row that lists every transitive dependency communicates
#: nothing. These are the choices a reader evaluating the project would want to see.
TRACKED: dict[str, str] = {
    "LangGraph": "langgraph",
    "Chainlit": "chainlit",
    "Qdrant": "qdrant-client",
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "Pydantic": "pydantic",
    "ReportLab": "reportlab",
    "Structlog": "structlog",
}

REQUIREMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<spec>[<>=!~][^;#\s]*)?\s*$")


def parse_requirements(path: Path) -> dict[str, str]:
    """Map distribution name -> version specifier, ignoring comments and blanks."""
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = REQUIREMENT_RE.match(line)
        if match:
            found[match.group("name").lower()] = (match.group("spec") or "").strip()
    return found


def candidate_bounds(spec: str) -> str:
    """Render a specifier set as the range a reader cares about.

    ``>=0.2.60,<1.3`` becomes ``0.2.60 – 1.3``: the floor that is supported and the ceiling
    that is not. The comparison operators are noise in a badge, and the two numbers are the
    whole message.
    """
    floor = re.search(r">=\s*([0-9][0-9A-Za-z.\-]*)", spec)
    ceiling = re.search(r"<\s*([0-9][0-9A-Za-z.\-]*)", spec)
    match_floor = re.search(r"==\s*([0-9][0-9A-Za-z.\-]*)", spec)

    if match_floor:
        return match_floor.group(1)
    if floor and ceiling:
        return f"{floor.group(1)}\u2013{ceiling.group(1)}"
    if floor:
        return f"\u2265{floor.group(1)}"
    if ceiling:
        return f"<{ceiling.group(1)}"
    return "any"


def python_versions() -> str:
    """Read the CI matrix rather than restating it.

    The versions a project *claims* to support and the versions it *tests* diverge quietly.
    A badge that states the tested set is the more useful claim, and it is also the one
    that cannot drift, because it is read from the workflow that runs.
    """
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        return "unknown"
    match = re.search(r'python-version:\s*\[([^\]]+)\]', workflow.read_text(encoding="utf-8"))
    if not match:
        return "unknown"
    versions = [v.strip().strip('"\'') for v in match.group(1).split(",") if v.strip()]
    return " | ".join(versions)


def docker_version() -> str:
    """The Python version inside the shipped image, which is not the CI matrix."""
    dockerfile = ROOT / "Dockerfile"
    if not dockerfile.is_file():
        return "unknown"
    match = re.search(r"^FROM\s+python:([0-9.]+)-slim", dockerfile.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "unknown"


def build() -> dict[str, str]:
    requirements = parse_requirements(ROOT / "requirements.txt")
    data = {"Python": python_versions(), "Docker": docker_version()}
    for label, distribution in TRACKED.items():
        spec = requirements.get(distribution)
        if spec is None:
            print(f"  ! {distribution} is not in requirements.txt; skipping {label}", file=sys.stderr)
            continue
        data[label] = candidate_bounds(spec)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail if the file is out of date.")
    args = parser.parse_args(argv)

    data = build()
    rendered = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if args.check:
        if not OUT.is_file():
            print(f"✗ {OUT.relative_to(ROOT)} does not exist; run scripts/gen_badges.py", file=sys.stderr)
            return 1
        if OUT.read_text(encoding="utf-8") != rendered:
            print(f"✗ {OUT.relative_to(ROOT)} is out of date", file=sys.stderr)
            current = json.loads(OUT.read_text(encoding="utf-8"))
            for key in sorted(set(current) | set(data)):
                if current.get(key) != data.get(key):
                    print(f"    {key}: {current.get(key)!r} -> {data.get(key)!r}", file=sys.stderr)
            print("  Run: python scripts/gen_badges.py", file=sys.stderr)
            return 1
        print(f"  ✅ {len(data)} badge value(s) match the dependency files")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    for key, value in sorted(data.items()):
        print(f"    {key:12} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
