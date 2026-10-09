"""Tests for the Jira CSV converter.

The raw backlog CSV is written for the API connector. Three of its columns make Jira's
manual importer fail or silently misbehave, and each is a real trap rather than a
theoretical one.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/jira_csv.py")

RAW = [
    {
        "Summary": "Discovery",
        "Issue Key": "AURORA-1",
        "Issue Type": "Epic",
        "Priority": "High",
        "Story Points": "",
        "Epic Link": "",
        "Epic Name": "Discovery",
        "Labels": "discovery",
        "Description": "Epic body",
        "Acceptance Criteria": "",
    },
    {
        "Summary": "Build the dashboard",
        "Issue Key": "AURORA-2",
        "Issue Type": "Story",
        "Priority": "High",
        "Story Points": "5",
        "Epic Link": "AURORA-1",
        "Epic Name": "",
        "Labels": "ui",
        "Description": "Story body",
        "Acceptance Criteria": "- GIVEN x WHEN y THEN z",
    },
]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "jira_backlog.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RAW[0].keys()))
        writer.writeheader()
        writer.writerows(RAW)
    return path


def run_converter(source: Path, out: Path, *flags: str) -> list[dict[str, str]]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "-o", str(out), *flags],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "rows" in result.stdout
    with out.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestImporterCompatibility:
    def test_issue_key_is_dropped(self, source: Path, tmp_path: Path) -> None:
        """A mapped Issue Key means "update this issue", not "create it"."""
        rows = run_converter(source, tmp_path / "out.csv")
        assert all("Issue Key" not in row for row in rows)

    def test_acceptance_criteria_moves_into_the_description(self, source: Path, tmp_path: Path) -> None:
        rows = run_converter(source, tmp_path / "out.csv")
        story = next(r for r in rows if r["Issue Type"] == "Story")
        assert "Acceptance Criteria" not in story
        assert "GIVEN x WHEN y THEN z" in story["Description"]
        assert "Story body" in story["Description"]

    def test_acceptance_criteria_can_be_kept_for_a_tenant_that_has_the_field(
        self, source: Path, tmp_path: Path
    ) -> None:
        rows = run_converter(source, tmp_path / "out.csv", "--keep-acceptance-criteria")
        story = next(r for r in rows if r["Issue Type"] == "Story")
        assert story["Acceptance Criteria"] == "- GIVEN x WHEN y THEN z"

    def test_story_points_are_dropped_by_default(self, source: Path, tmp_path: Path) -> None:
        """This tenant has no Story Points field; an unmapped column is ignored."""
        rows = run_converter(source, tmp_path / "out.csv")
        assert all("Story Points" not in row for row in rows)

    def test_story_points_can_be_kept(self, source: Path, tmp_path: Path) -> None:
        rows = run_converter(source, tmp_path / "out.csv", "--keep-story-points")
        story = next(r for r in rows if r["Issue Type"] == "Story")
        assert story["Story Points"] == "5"

    def test_header_is_the_union_of_all_rows(self, source: Path, tmp_path: Path) -> None:
        """Epics carry no Parent and stories do; the first row alone truncates the header."""
        rows = run_converter(source, tmp_path / "out.csv", "--team-managed")
        assert "Parent" in rows[0]
        assert rows[1]["Parent"] == "" or "Parent" in rows[1]


class TestEpicLinking:
    def test_team_managed_links_by_parent_summary(self, source: Path, tmp_path: Path) -> None:
        """No key exists before import, so the link has to be the epic's summary."""
        rows = run_converter(source, tmp_path / "out.csv", "--team-managed")
        story = next(r for r in rows if r["Issue Type"] == "Story")
        assert story["Parent"] == "Discovery"
        assert "Epic Link" not in story

    def test_company_managed_keeps_epic_link_and_names_the_epic(
        self, source: Path, tmp_path: Path
    ) -> None:
        rows = run_converter(source, tmp_path / "out.csv")
        story = next(r for r in rows if r["Issue Type"] == "Story")
        epic = next(r for r in rows if r["Issue Type"] == "Epic")
        assert story["Epic Link"] == "AURORA-1"
        assert epic["Epic Name"] == "Discovery"
        assert "Parent" not in story


class TestGuards:
    def test_a_missing_file_exits_non_zero(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path / "absent.csv")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "not found" in result.stderr

    def test_a_csv_without_a_summary_column_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "Summary" in result.stderr
