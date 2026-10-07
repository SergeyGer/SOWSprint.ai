"""Jira-importable CSV export.

The column set matches Jira's *External System Import* CSV mapper so a delivery lead can
bulk-load the backlog without writing a single API call. The file is produced alongside
the live REST provisioning because the two serve different situations: the API path
needs credentials and network access, whereas the CSV works offline and behind a
change-approval process.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import TechnicalBlueprint

#: Column order expected by Jira's CSV importer.
JIRA_COLUMNS = [
    "Summary",
    "Issue Key",
    "Issue Type",
    "Status",
    "Priority",
    "Story Points",
    "Epic Link",
    "Epic Name",
    "Labels",
    "Description",
    "Acceptance Criteria",
]


def render_jira_csv(blueprint: TechnicalBlueprint, *, project_key: str = "SOW") -> str:
    """Render the full backlog as a Jira-importable CSV string."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=JIRA_COLUMNS, lineterminator="\n")
    writer.writeheader()

    counter = 0
    for milestone in blueprint.milestones:
        for epic in milestone.epics:
            counter += 1
            writer.writerow(
                {
                    "Summary": f"[{milestone.name}] {epic.name}",
                    "Issue Key": f"{project_key}-{counter}",
                    "Issue Type": "Epic",
                    "Status": "To Do",
                    "Priority": "High",
                    "Story Points": "",
                    "Epic Link": "",
                    "Epic Name": epic.name,
                    "Labels": f"{milestone.name.split()[0].lower()} milestone",
                    "Description": f"{epic.objective}\n\nMilestone: {milestone.name} "
                    f"({milestone.duration_weeks} weeks)",
                    "Acceptance Criteria": "",
                }
            )
            epic_key = f"{project_key}-{counter}"

            for story in epic.stories:
                counter += 1
                writer.writerow(
                    {
                        "Summary": story.title,
                        "Issue Key": f"{project_key}-{counter}",
                        "Issue Type": "Story",
                        "Status": "To Do",
                        "Priority": story.priority,
                        "Story Points": story.story_points,
                        "Epic Link": epic_key,
                        "Epic Name": "",
                        "Labels": " ".join(story.labels[:6]),
                        "Description": story.render_statement(),
                        "Acceptance Criteria": "\n".join(
                            f"- {criterion.render()}" for criterion in story.acceptance_criteria
                        ),
                    }
                )

    return buffer.getvalue()


def write_jira_csv(blueprint: TechnicalBlueprint, path: Path, *, project_key: str = "SOW") -> Path:
    """Write the backlog CSV to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_jira_csv(blueprint, project_key=project_key), encoding="utf-8")
    return path
