#!/usr/bin/env python
"""Convert the generated backlog CSV into one Jira's importer will accept.

The raw ``jira_backlog.csv`` is written for the API connector, not for Jira's manual
CSV importer, and three of its columns break a manual import:

``Issue Key``
    Every row carries a key such as ``AURORA-1``. Jira treats a mapped *Issue Key* as an
    instruction to **update** an existing issue, not to create one, so every row fails
    against a project where those keys do not exist yet. Dropped here so Jira assigns
    its own keys.

``Story Points`` / ``Epic Link``
    Custom fields that may not exist. This account has neither: it is a team-managed
    project, where epics are linked through **Parent** rather than an *Epic Link* field.
    ``--team-managed`` rewrites the link accordingly.

``Acceptance Criteria``
    Almost never a field in a fresh tenant. Merged into the description, where it is at
    least visible, unless you pass ``--keep-acceptance-criteria`` for a tenant that has
    the field.

Usage::

    python scripts/jira_csv.py data/artifacts/<run>/jira_backlog.csv
    python scripts/jira_csv.py ... --team-managed -o import.csv
    python scripts/jira_csv.py ... --keep-story-points --keep-acceptance-criteria
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def convert(
    rows: list[dict[str, str]],
    *,
    team_managed: bool,
    keep_story_points: bool,
    keep_acceptance_criteria: bool,
) -> tuple[list[dict[str, str]], list[str]]:
    """Return (rows for import, notes about what was changed)."""
    notes: list[str] = []

    # The epic each story belongs to, by its original key, so the link can be rewritten
    # as a summary — the only identifier Jira has before it assigns keys.
    epic_key_to_summary: dict[str, str] = {}
    for row in rows:
        if (row.get("Issue Type") or "").strip().lower() == "epic":
            key = (row.get("Issue Key") or "").strip()
            if key:
                epic_key_to_summary[key] = (row.get("Summary") or "").strip()

    out: list[dict[str, str]] = []
    for row in rows:
        issue_type = (row.get("Issue Type") or "").strip()
        summary = (row.get("Summary") or "").strip()
        description = (row.get("Description") or "").strip()
        criteria = (row.get("Acceptance Criteria") or "").strip()

        record: dict[str, str] = {
            "Summary": summary,
            "Issue Type": issue_type,
            "Priority": (row.get("Priority") or "").strip(),
            "Labels": (row.get("Labels") or "").strip(),
        }

        if keep_acceptance_criteria:
            record["Acceptance Criteria"] = criteria
        elif criteria:
            # Keep it, but somewhere Jira will definitely accept it.
            description = (
                f"{description}\n\nh3. Acceptance criteria\n{criteria}"
                if description
                else f"h3. Acceptance criteria\n{criteria}"
            )
        record["Description"] = description

        if keep_story_points:
            record["Story Points"] = (row.get("Story Points") or "").strip()

        if team_managed:
            # Team-managed projects link a story to its epic through Parent, and the
            # importer resolves it by summary because no key exists yet.
            epic_key = (row.get("Epic Link") or "").strip()
            if issue_type.lower() == "story" and epic_key:
                record["Parent"] = epic_key_to_summary.get(epic_key, epic_key)
        else:
            record["Epic Link"] = (row.get("Epic Link") or "").strip()
            if issue_type.lower() == "epic":
                # Required for epics in a company-managed project.
                record["Epic Name"] = (row.get("Epic Name") or summary).strip()

        out.append(record)

    if any("Issue Key" in row for row in rows):
        notes.append("dropped 'Issue Key' so Jira creates issues instead of trying to update them")
    if not keep_acceptance_criteria:
        notes.append("merged 'Acceptance Criteria' into the description")
    if not keep_story_points:
        notes.append("dropped 'Story Points' (this tenant has no such field)")
    if team_managed:
        notes.append("rewrote 'Epic Link' as 'Parent' for a team-managed project")
    return out, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Path to jira_backlog.csv (in the run's artifact directory).")
    parser.add_argument("-o", "--out", help="Output path. Defaults to <source>-import.csv")
    parser.add_argument(
        "--team-managed",
        action="store_true",
        help="Link stories through Parent instead of the Epic Link field.",
    )
    parser.add_argument("--keep-story-points", action="store_true")
    parser.add_argument("--keep-acceptance-criteria", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        print(f"✗ {source} not found.", file=sys.stderr)
        print("  Run an engagement first, then use data/artifacts/<run-id>/jira_backlog.csv", file=sys.stderr)
        return 1

    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print(f"✗ {source} has no rows", file=sys.stderr)
        return 1
    if "Summary" not in rows[0]:
        print(f"✗ {source} has no 'Summary' column — is this a backlog export?", file=sys.stderr)
        return 1

    converted, notes = convert(
        rows,
        team_managed=args.team_managed,
        keep_story_points=args.keep_story_points,
        keep_acceptance_criteria=args.keep_acceptance_criteria,
    )

    destination = Path(args.out) if args.out else source.with_name(f"{source.stem}-import.csv")
    # Union of every row's keys, in first-seen order: epics carry Epic Name and stories
    # carry Parent, so the first row alone would truncate the header.
    fieldnames: list[str] = []
    for record in converted:
        for key in record:
            if key not in fieldnames:
                fieldnames.append(key)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(converted)

    epics = sum(1 for r in converted if r["Issue Type"].lower() == "epic")
    stories = len(converted) - epics

    print(f"  read      {source}")
    print(f"  wrote     {destination}")
    print(f"  rows      {len(converted)}  ({epics} epic(s), {stories} story/stories)")
    print(f"  columns   {', '.join(fieldnames)}")
    if notes:
        print("\n  changes for the importer")
        for note in notes:
            print(f"    · {note}")
    print(
        "\n  Import in Jira:  Settings (⚙) → System → Import and Export → External System\n"
        "  Import → CSV. Choose the project, then map each column to a Jira field. Any\n"
        "  column you leave unmapped is ignored rather than fatal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
