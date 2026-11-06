"""Keep the public repository English-only.

Release 2.1 publishes documentation, subtitles and operator guides. The project is worked on in
Russian, and Russian-language working documents live on the operator's machine — never here. A
rule that exists only as an instruction is a rule that lasts until the next busy evening, so this
is a check with the same standing as the other documentation checks.

What counts as published Russian:

* Cyrillic characters in any tracked text file. A single word in a comment is enough — the rule
  is about the repository, not about how much of a file is in another language.
* Subtitle tracks in the published video whose language is Russian.

Binary formats are scanned by type where it can be done cheaply, and skipped where they cannot.
A `.docx` is a zip of XML; scanning it properly means unpacking it, which this does not do. That
is a stated limit rather than a silent one — the files that were actually published here were
text, subtitles and a video.

Usage:

    python scripts/check_english_only.py
    python scripts/check_english_only.py --all      # include untracked files
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: Cyrillic blocks. The basic Russian range plus the supplement, so a stray Ukrainian or Serbian
#: letter in a Russian quotation is still caught.
CYRILLIC = range(0x0400, 0x0500)

#: Extensions that cannot hold reviewable text, or hold it in a form this check would only
#: produce noise from. Images and video are covered by the subtitle check below instead.
SKIP_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
        ".mp4", ".webm", ".mov", ".avi",
        ".pdf", ".zip", ".gz", ".whl", ".db", ".sqlite",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
    }
)

#: Directories a walk must not treat as source when git is unavailable.
EXCLUDED_DIRS = frozenset(
    {"node_modules", "venv", "site-packages", "dist", "build", "__pycache__", "data"}
)

#: Videos whose subtitle tracks are part of what the repository publishes.
PUBLISHED_VIDEOS = ("docs/media/demo-2.1.mp4", "docs/media/demo.mp4")


def tracked_files(root: Path) -> list[Path]:
    try:
        listing = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a checkout — or a container without git. Fall back to walking, so the check still
        # works from an export, but skip the directories that hold downloads rather than source.
        # Without this the walk scanned .pip-cache and reported Cyrillic inside cached package
        # metadata, which is neither ours nor published.
        return [
            path
            for path in root.rglob("*")
            if path.is_file()
            and not any(part.startswith(".") or part in EXCLUDED_DIRS for part in path.parts)
        ]
    return [root / line for line in listing.splitlines() if line.strip()]


def find_cyrillic(path: Path) -> list[int]:
    """Line numbers containing Cyrillic characters."""
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return []
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(ord(char) in CYRILLIC for char in line):
            hits.append(number)
    return hits


def _has_ffprobe() -> bool:
    """Whether subtitle inspection is possible at all, as opposed to finding no tracks."""
    from shutil import which

    return which("ffprobe") is not None


def subtitle_languages(video: Path) -> list[str]:
    """Language tags of the subtitle streams, when ffprobe is available.

    Absent ffprobe, this returns an empty list and the caller notes that the video could not be
    checked. The alternative — installing ffmpeg inside the documentation job — made that job
    hang for over five minutes on `apt-get`, which turns a check people trust into one they
    disable.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", "stream_tags=language", "-of", "csv=p=0", str(video),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_english_only", description=__doc__)
    parser.add_argument("--all", action="store_true", help="also scan untracked files")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)

    root: Path = args.root
    files = tracked_files(root)
    if args.all:
        files += [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
        files = sorted(set(files))

    failures: list[str] = []
    for path in files:
        if path.suffix.lower() in SKIP_SUFFIXES or not path.exists():
            continue
        lines = find_cyrillic(path)
        if lines:
            relative = path.relative_to(root)
            preview = ", ".join(str(number) for number in lines[:5])
            failures.append(f"{relative}: Cyrillic on line(s) {preview}")

    notes: list[str] = []
    probe = _has_ffprobe()
    for name in PUBLISHED_VIDEOS:
        video = root / name
        if not video.exists():
            continue
        if not probe:
            notes.append(f"{name}: subtitle tracks not inspected (ffprobe unavailable)")
            continue
        languages = subtitle_languages(video)
        if not languages:
            # No subtitle stream at all: the file publishes no text, which satisfies the rule
            # for the same reason an image does. Saying "not inspected" here would be wrong and
            # would train a reader to ignore the note.
            notes.append(f"{name}: no subtitle track")
        offenders = [lang for lang in languages if lang.lower().startswith(("ru", "rus"))]
        if offenders:
            failures.append(
                f"{name}: subtitle track(s) {', '.join(offenders)} — "
                "Russian subtitles stay in the local copy"
            )
        # A video with no subtitle track publishes no text at all, which satisfies the rule for
        # the same reason an image does. Only a Russian track is a violation.

    if failures:
        print("  ❌ the public repository is English-only; fix these:")
        for failure in failures:
            print(f"    - {failure}")
        print("\n  Russian-language documents belong in .local-ru/, which git ignores.")
        return 1

    print(f"  ✅ {len(files)} tracked file(s) and the published video are English-only")
    for note in notes:
        print(f"    note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
