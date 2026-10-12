#!/usr/bin/env python
"""Generate docs/badges.json and the README's technology badge block.

Why this indirection
--------------------
A badge with a version typed into it is a claim that decays. `LangGraph 0.2` stays on the
README long after the dependency moved, and nothing fails — the badge simply becomes wrong,
and a reader has no way to tell a current badge from a stale one.

So the values are read from the files that actually govern the versions, and the README
block is generated from the same table that maps each technology to its wiki page. One
table, so a new badge cannot be added to the README without a destination, and a renamed
wiki page breaks the build rather than leaving a dead link.

Sources:

* ``requirements.txt``      — runtime libraries
* ``requirements-dev.txt``  — development tooling
* the CI matrix             — the Python versions actually tested
* the Dockerfile            — the Python version actually shipped

Regenerate after changing any of them::

    python scripts/gen_badges.py

``--check`` fails when either output is stale, which is what runs in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "badges.json"
README = ROOT / "README.md"

WIKI_BASE = "https://github.com/SergeyGer/SOWSprint.ai/wiki"
RAW_BADGES = "https://raw.githubusercontent.com/SergeyGer/SOWSprint.ai/main/docs/badges.json"

#: Fence markers around the generated block, so the README can carry hand-written prose
#: on either side without the generator guessing where its output ends.
BEGIN = "<!-- BEGIN GENERATED BADGES — edit scripts/gen_badges.py, not this block -->"
END = "<!-- END GENERATED BADGES -->"


@dataclass(frozen=True)
class Tech:
    """One technology badge.

    The wiki page is not decoration. A version tells a reader what is used; the link tells
    them what it is used *for* here, which is the question a badge usually provokes and
    almost never answers.
    """

    label: str
    distribution: str
    color: str
    wiki: str
    logo: str | None = None
    source: str = "requirements.txt"


TECH: tuple[Tech, ...] = (
    # Language and runtime first, then orchestration, interface, storage, providers, then
    # the tooling that keeps it honest. Left to right is roughly the shape of the system.
    Tech("Python", "python", "3776AB", "Testing", logo="python", source="ci-matrix"),
    Tech("Docker", "python", "2496ED", "Operations", logo="docker", source="dockerfile"),
    Tech("LangGraph", "langgraph", "1C3C3C", "Agent-Pipeline"),
    Tech("Chainlit", "chainlit", "F80061", "Architecture"),
    Tech("Qdrant", "qdrant-client", "DC244C", "Compliance-RAG"),
    Tech("OpenAI", "openai", "412991", "Cost-Observability", logo="openai"),
    Tech("Anthropic", "anthropic", "D97757", "Cost-Observability", logo="anthropic"),
    Tech("Pydantic", "pydantic", "E92063", "Architecture", logo="pydantic"),
    Tech("Ruff", "ruff", "D7FF64", "Testing", source="requirements-dev.txt"),
)

REQUIREMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<spec>[<>=!~][^;#\s]*)?\s*$")


def parse_requirements(path: Path) -> dict[str, str]:
    """Map distribution name -> version specifier, ignoring comments and blanks."""
    if not path.is_file():
        return {}
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

    ``>=0.2.60,<1.3`` becomes ``0.2.60–1.3``: the floor that is supported and the ceiling
    that is not. The comparison operators are noise in a badge, and those two numbers are
    the whole message.
    """
    exact = re.search(r"==\s*([0-9][0-9A-Za-z.\-]*)", spec)
    if exact:
        return exact.group(1)
    floor = re.search(r">=\s*([0-9][0-9A-Za-z.\-]*)", spec)
    ceiling = re.search(r"<\s*([0-9][0-9A-Za-z.\-]*)", spec)
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
    The tested set is the more useful claim, and it cannot drift, because it is read from
    the workflow that runs it.
    """
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        return "unknown"
    match = re.search(r'python-version:\s*\[([^\]]+)\]', workflow.read_text(encoding="utf-8"))
    if not match:
        return "unknown"
    return " | ".join(v.strip().strip("\"'") for v in match.group(1).split(",") if v.strip())


def docker_version() -> str:
    """The Python version inside the shipped image, which is not the CI matrix."""
    dockerfile = ROOT / "Dockerfile"
    if not dockerfile.is_file():
        return "unknown"
    match = re.search(
        r"^FROM\s+python:([0-9.]+)-slim", dockerfile.read_text(encoding="utf-8"), re.M
    )
    return match.group(1) if match else "unknown"


def value_for(tech: Tech, sources: dict[str, dict[str, str]]) -> str:
    if tech.source == "ci-matrix":
        return python_versions()
    if tech.source == "dockerfile":
        return docker_version()
    spec = sources.get(tech.source, {}).get(tech.distribution)
    if spec is None:
        return "unknown"
    return candidate_bounds(spec)


def build() -> dict[str, str]:
    sources = {
        "requirements.txt": parse_requirements(ROOT / "requirements.txt"),
        "requirements-dev.txt": parse_requirements(ROOT / "requirements-dev.txt"),
    }
    data: dict[str, str] = {}
    for tech in TECH:
        value = value_for(tech, sources)
        if value == "unknown":
            print(f"  ! {tech.distribution} not found in {tech.source}; skipping {tech.label}",
                  file=sys.stderr)
            continue
        data[tech.label] = value
    return data


def badge_url(label: str, color: str, logo: str | None) -> str:
    """A shields.io dynamic badge reading its value from docs/badges.json.

    ``cacheSeconds`` is set explicitly. Without it shields.io caches for an unspecified
    period, so a dependency bump would surface at a time nobody could predict. Thirty
    minutes is long enough that the badge is not refetched on every page view and short
    enough that a merge shows up the same working day.

    The value doubles as a cache key, which matters when ADDING a badge: shields.io
    remembers the "no result" it returned while the new key was still missing from
    badges.json, and serves it for the remainder of the window even after the key exists.
    Changing this number is the fix, and it is worth knowing before spending an hour
    debugging a badge whose data is already correct.
    """
    url = (
        "https://img.shields.io/badge/dynamic/json"
        f"?url={urllib.parse.quote(RAW_BADGES, safe='')}"
        f"&query={urllib.parse.quote(f'$.{label}', safe='')}"
        f"&label={urllib.parse.quote(label)}&color={color}"
        f"&style=flat-square&cacheSeconds=1800"
    )
    if logo:
        url += f"&logo={logo}&logoColor=white"
    return url


def render_block(data: dict[str, str]) -> str:
    badges = " ".join(
        f"[![{tech.label}]({badge_url(tech.label, tech.color, tech.logo)})]"
        f"({WIKI_BASE}/{tech.wiki})"
        for tech in TECH
        if tech.label in data
    )
    return (
        f"{BEGIN}\n"
        "**Core stack** — each version is read from the file that governs it "
        "(`requirements.txt`, `requirements-dev.txt`, the CI matrix, the Dockerfile), so the "
        "badges cannot drift from what the repository declares. Follow one to see what the "
        "technology is used for here.\n\n"
        f"{badges}\n"
        f"{END}"
    )


def check_json(data: dict[str, str]) -> int:
    rendered = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if not OUT.is_file():
        print(f"✗ {OUT.relative_to(ROOT)} does not exist; run scripts/gen_badges.py", file=sys.stderr)
        return 1
    if OUT.read_text(encoding="utf-8") != rendered:
        print(f"✗ {OUT.relative_to(ROOT)} is out of date", file=sys.stderr)
        current = json.loads(OUT.read_text(encoding="utf-8"))
        for key in sorted(set(current) | set(data)):
            if current.get(key) != data.get(key):
                print(f"    {key}: {current.get(key)!r} -> {data.get(key)!r}", file=sys.stderr)
        return 1
    return 0


def check_readme(data: dict[str, str]) -> int:
    text = README.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print("✗ README.md is missing the generated badge block markers", file=sys.stderr)
        return 1
    actual = text.split(BEGIN, 1)[1].split(END, 1)[0]
    expected = render_block(data).split(BEGIN, 1)[1].split(END, 1)[0]
    if actual != expected:
        print("✗ the README badge block is out of date", file=sys.stderr)
        print("  Run: python scripts/gen_badges.py", file=sys.stderr)
        for line in expected.splitlines():
            if line not in actual.splitlines():
                print(f"    missing: {line[:110]}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail if either output is stale.")
    args = parser.parse_args(argv)

    data = build()
    block = render_block(data)

    if args.check:
        failed = check_json(data) | check_readme(data)
        if failed:
            return 1
        print(f"  ✅ {len(data)} badge value(s) and the README block match their sources")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    text = README.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        before = text.split(BEGIN, 1)[0]
        after = text.split(END, 1)[1]
        README.write_text(before + block + after, encoding="utf-8")
        readme_note = "README block replaced"
    else:
        readme_note = "README block not found — markers missing, left untouched"

    print(f"  wrote {OUT.relative_to(ROOT)} ({readme_note})")
    for key, value in sorted(data.items()):
        print(f"    {key:12} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
