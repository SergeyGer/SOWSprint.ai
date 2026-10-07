"""Tests for the deliverable renderers.

The fixtures are built with the deterministic offline engines (``architect_offline`` /
``legal_offline``) so the SOW and blueprint under test are the same objects the running
platform produces, not hand-rolled approximations. Only ``render_deliverables`` touches
the filesystem: it writes to ``data/artifacts/<run_id>/`` by design, so every test that
calls it uses a unique run id and removes the directory afterwards.
"""

from __future__ import annotations

import csv
import io
import shutil
import uuid

import pytest

from sowsprint.config import ARTIFACT_DIR
from sowsprint.export import (
    JIRA_COLUMNS,
    render_critique_markdown,
    render_deliverables,
    render_jira_csv,
    render_sow_markdown,
    render_sow_pdf,
)
from sowsprint.llm.offline import architect_offline, legal_offline
from sowsprint.models import (
    ArtifactRef,
    CriticFinding,
    CritiqueReport,
    FindingCategory,
    Jurisdiction,
    NodeName,
    RequirementScope,
    ScopeStatus,
    Severity,
    SOWDocument,
    TechnicalBlueprint,
)

#: Two retrievable compliance passages, in the block format the retriever emits.
EVIDENCE_BLOCK = (
    "[1] GDPR Article 28 — Processor (jurisdiction=EU) id=gdpr-art28\n"
    "The processor shall provide sufficient guarantees to implement appropriate technical "
    "and organisational measures for the processing of personal data.\n"
    "[2] EU AI Act Article 13 — Transparency (jurisdiction=EU) id=ai-act-art13\n"
    "High-risk AI systems shall be designed and developed so that deployers can interpret "
    "the system output and use it appropriately.\n"
)


def make_scope() -> RequirementScope:
    """A fully specified EU engagement with four deliverables."""
    return RequirementScope(
        title="Aurora Customer Onboarding Platform",
        client_name="Northwind Retail Bank",
        vendor_name="SOWSprint Delivery Team",
        business_goal="Cut retail account-opening time from days to minutes.",
        problem_statement="Onboarding is manual and spread across three disconnected systems.",
        deliverables=[
            "Discovery report and validated scope",
            "Customer onboarding web portal",
            "Salesforce and core banking integration",
            "Production launch and handover",
        ],
        constraints=["Delivery must complete within the agreed sixteen week window"],
        compliance_flags=["GDPR"],
        jurisdiction=Jurisdiction.EU,
        timeline_weeks=16,
        team_size=6,
        status=ScopeStatus.COMPLETE,
        confidence=0.82,
    )


@pytest.fixture
def scope() -> RequirementScope:
    return make_scope()


@pytest.fixture
def blueprint(scope: RequirementScope) -> TechnicalBlueprint:
    return architect_offline(scope.model_dump(mode="json"))


@pytest.fixture
def sow(scope: RequirementScope, blueprint: TechnicalBlueprint) -> SOWDocument:
    return legal_offline(
        scope.model_dump(mode="json"),
        blueprint.model_dump(mode="json"),
        EVIDENCE_BLOCK,
        scope.jurisdiction.value,
    )


@pytest.fixture
def artifact_run():
    """A unique ``data/artifacts/<run_id>`` directory that is removed afterwards."""
    run_id = f"pytest-export-{uuid.uuid4().hex}"
    target = ARTIFACT_DIR / run_id
    yield run_id
    shutil.rmtree(target, ignore_errors=True)


# ---------------------------------------------------------------------------------
# SOW markdown
# ---------------------------------------------------------------------------------


def test_render_sow_markdown_contains_title_and_every_clause(sow: SOWDocument):
    markdown = render_sow_markdown(sow)

    assert markdown.startswith(f"# {sow.title}\n")
    assert f"**Client:** {sow.client_name}" in markdown
    assert f"**Supplier:** {sow.vendor_name}" in markdown
    assert sow.executive_summary in markdown

    assert sow.clauses, "the offline Legal engine must produce clauses"
    for clause in sow.clauses:
        assert f"### {clause.number}. {clause.heading}" in markdown
        assert clause.body.strip() in markdown

    # Citations are rendered as an evidence sub-line when present.
    cited = [clause for clause in sow.clauses if clause.citations]
    assert cited, "the evidence block should ground at least one clause"
    assert "<sub>Evidence:" in markdown


def test_render_sow_markdown_payment_table_totals_100_percent(sow: SOWDocument):
    markdown = render_sow_markdown(sow)

    assert "## 2. Payment schedule" in markdown
    assert "| # | Milestone | Trigger | % |" in markdown

    assert sow.payment_schedule
    for index, payment in enumerate(sow.payment_schedule, start=1):
        row = f"| {index} | {payment.name} | {payment.trigger} | {payment.percentage:.1f}% |"
        assert row in markdown

    total = sum(payment.percentage for payment in sow.payment_schedule)
    assert total == pytest.approx(100.0)
    assert "| | **Total** | | **100.0%** |" in markdown


def test_render_sow_markdown_includes_optional_annexes(sow: SOWDocument):
    markdown = render_sow_markdown(sow)

    assert "## 9. Termination" in markdown
    assert "## 10. Signatures" in markdown
    assert sow.termination in markdown
    if sow.compliance_notes:
        assert "## 3. Compliance notes" in markdown
        for note in sow.compliance_notes:
            assert f"- {note}" in markdown
    assert f"{sow.word_count():,} words" in markdown


# ---------------------------------------------------------------------------------
# Jira CSV
# ---------------------------------------------------------------------------------


def test_render_jira_csv_has_jira_header_and_one_row_per_issue(blueprint: TechnicalBlueprint):
    text = render_jira_csv(blueprint, project_key="AURORA")
    reader = csv.DictReader(io.StringIO(text))

    assert reader.fieldnames == JIRA_COLUMNS
    rows = list(reader)

    epics = [epic for milestone in blueprint.milestones for epic in milestone.epics]
    stories = blueprint.all_stories()
    epic_rows = [row for row in rows if row["Issue Type"] == "Epic"]
    story_rows = [row for row in rows if row["Issue Type"] == "Story"]

    assert len(epic_rows) == len(epics)
    assert len(story_rows) == len(stories)
    assert len(rows) == len(epics) + len(stories)

    # Issue keys are allocated sequentially across both issue types.
    assert rows[0]["Issue Key"] == "AURORA-1"
    assert [row["Issue Key"] for row in rows] == [
        f"AURORA-{index}" for index in range(1, len(rows) + 1)
    ]

    first_epic, first_milestone = epics[0], blueprint.milestones[0]
    assert epic_rows[0]["Summary"] == f"[{first_milestone.name}] {first_epic.name}"
    assert epic_rows[0]["Epic Name"] == first_epic.name
    assert epic_rows[0]["Epic Link"] == ""
    assert epic_rows[0]["Status"] == "To Do"


def test_render_jira_csv_stories_link_to_their_epic_with_points(blueprint: TechnicalBlueprint):
    text = render_jira_csv(blueprint, project_key="AURORA")
    rows = list(csv.DictReader(io.StringIO(text)))

    epic_keys = {row["Issue Key"] for row in rows if row["Issue Type"] == "Epic"}
    story_rows = [row for row in rows if row["Issue Type"] == "Story"]
    assert story_rows, "the blueprint must contain stories"

    for row in story_rows:
        assert row["Epic Link"], "every story must carry an Epic Link"
        assert row["Epic Link"] in epic_keys
        assert row["Story Points"].isdigit()
        assert 1 <= int(row["Story Points"]) <= 21
        assert row["Priority"] in {"Highest", "High", "Medium", "Low", "Lowest"}
        assert row["Epic Name"] == ""

    # Story bodies carry the rendered statement and its acceptance criteria.
    first_story = blueprint.all_stories()[0]
    first_story_row = story_rows[0]
    assert first_story_row["Summary"] == first_story.title
    assert first_story_row["Description"] == first_story.render_statement()
    for criterion in first_story.acceptance_criteria:
        assert f"- {criterion.render()}" in first_story_row["Acceptance Criteria"]


# ---------------------------------------------------------------------------------
# SOW PDF
# ---------------------------------------------------------------------------------


def test_render_sow_pdf_writes_a_real_pdf(sow: SOWDocument, tmp_path):
    path = tmp_path / "nested" / "statement_of_work.pdf"

    returned = render_sow_pdf(sow, path)

    assert returned == path
    assert path.is_file()
    assert path.stat().st_size > 1000
    assert path.read_bytes().startswith(b"%PDF-")


# ---------------------------------------------------------------------------------
# deliverable bundle
# ---------------------------------------------------------------------------------


def test_render_deliverables_writes_referenced_artifacts(
    artifact_run, scope: RequirementScope, blueprint: TechnicalBlueprint, sow: SOWDocument
):
    target = ARTIFACT_DIR / artifact_run
    critique = CritiqueReport(
        passed=True,
        quality_score=0.91,
        grounding_ratio=1.0,
        summary="No blocking defects.",
    )

    refs = render_deliverables(
        session_id="session-test-1",
        run_id=artifact_run,
        scope=scope.model_dump(mode="json"),
        blueprint=blueprint.model_dump(mode="json"),
        sow=sow.model_dump(mode="json"),
        critique=critique.model_dump(mode="json"),
        integrations=[{"target": "jira", "created": [], "failed": [], "dry_run": True}],
        cost={"cost_usd": 0.42, "calls": 7},
    )

    assert target.is_dir()
    kinds = {ref["kind"] for ref in refs}
    assert {"sow_pdf", "sow_markdown", "jira_csv"} <= kinds

    for ref in refs:
        parsed = ArtifactRef.model_validate(ref)
        assert parsed.exists() is True, f"{ref['kind']} was referenced but not written"
        assert parsed.size_bytes > 0
        assert parsed.path.startswith(str(target))

    markdown = (target / "statement_of_work.md").read_text(encoding="utf-8")
    assert sow.title in markdown
    assert markdown.startswith("<!-- run_id:")
    assert (target / "jira_backlog.csv").read_text(encoding="utf-8").startswith("Summary,")
    assert (target / "statement_of_work.pdf").read_bytes().startswith(b"%PDF-")


def test_render_deliverables_tolerates_all_none_inputs(artifact_run):
    refs = render_deliverables(
        session_id="session-test-2",
        run_id=artifact_run,
        scope=None,
        blueprint=None,
        sow=None,
        critique=None,
        integrations=None,
        cost=None,
    )

    assert isinstance(refs, list)
    # Nothing can be rendered from nothing, but the run report is always produced.
    assert [ref["kind"] for ref in refs] == ["report_json"]
    assert ArtifactRef.model_validate(refs[0]).exists() is True
    assert [(ARTIFACT_DIR / artifact_run).joinpath("run_report.json").is_file()]


def test_render_deliverables_survives_a_malformed_payload(artifact_run, blueprint: TechnicalBlueprint):
    """A dict that fails schema validation is skipped, not fatal."""
    refs = render_deliverables(
        session_id="session-test-3",
        run_id=artifact_run,
        scope={"title": "Broken scope"},
        blueprint=blueprint.model_dump(mode="json"),
        sow={"client_name": "No title or clauses"},  # missing required `title`
        critique=None,
        integrations=None,
        cost=None,
    )

    kinds = {ref["kind"] for ref in refs}
    assert "sow_pdf" not in kinds
    assert "jira_csv" in kinds
    assert "report_json" in kinds


# ---------------------------------------------------------------------------------
# critique report
# ---------------------------------------------------------------------------------


def test_render_critique_markdown_reports_failed_verdict_and_findings():
    remediation_with_pipe = "Rebind clause 3 to a retrieved passage | then re-run the audit"
    critique = CritiqueReport(
        passed=False,
        quality_score=0.44,
        hallucination_score=0.25,
        grounding_ratio=0.4,
        summary="Two defects block signature.",
        findings=[
            CriticFinding(
                category=FindingCategory.HALLUCINATION,
                severity=Severity.CRITICAL,
                location="3",
                description="Clause cites an id that is absent from the corpus.",
                evidence="Known ids: gdpr-art28",
                remediation=remediation_with_pipe,
                target_node=NodeName.LEGAL,
            ),
            CriticFinding(
                category=FindingCategory.COMMERCIAL_RISK,
                severity=Severity.HIGH,
                location="6",
                description="Payment percentages do not reconcile.",
                remediation="Restore the schedule to exactly 100%.",
            ),
            CriticFinding(
                category=FindingCategory.AMBIGUITY,
                severity=Severity.LOW,
                location="global",
                description="Minor wording issue.",
                remediation="Tighten the wording of the termination clause.",
            ),
        ],
        checked_clauses=14,
        unsupported_citations=["made-up-id"],
        attempt=1,
    )

    markdown = render_critique_markdown(critique)

    assert "FAILED" in markdown
    assert "PASSED" not in markdown
    assert f"**Quality score:** {critique.quality_score:.2f}" in markdown
    assert "**Attempt:** 2" in markdown
    assert critique.summary in markdown

    for finding in critique.findings:
        assert finding.remediation.replace("|", "\\|") in markdown
        assert finding.severity.value in markdown
        assert finding.category.value in markdown
        assert f"`{finding.location}`" in markdown

    # The escaped pipe must not create an extra table column.
    escaped_row = next(
        line for line in markdown.splitlines() if "Rebind clause 3" in line
    )
    assert escaped_row.count("|") - escaped_row.count("\\|") == 6
    assert "\\|" in escaped_row

    assert "## Unsupported citations" in markdown
    assert "- `made-up-id`" in markdown


def test_render_critique_markdown_reports_passed_verdict_without_findings():
    critique = CritiqueReport(
        passed=True,
        quality_score=0.93,
        grounding_ratio=1.0,
        summary="The draft is grounded and internally consistent.",
        checked_clauses=14,
    )

    markdown = render_critique_markdown(critique)

    assert "PASSED" in markdown
    assert "FAILED" not in markdown
    assert "No defects were identified by the audit." in markdown
    assert "## Findings" in markdown
    assert "Unsupported citations" not in markdown


def test_render_critique_markdown_escapes_newlines_inside_cells():
    critique = CritiqueReport(
        passed=False,
        quality_score=0.2,
        summary="Multi-line remediation.",
        findings=[
            CriticFinding(
                category=FindingCategory.MISSING_CLAUSE,
                severity=Severity.HIGH,
                location="global",
                description="First line\nsecond line",
                remediation="Step one\nStep two",
            )
        ],
    )

    markdown = render_critique_markdown(critique)

    assert "First line second line" in markdown
    assert "Step one Step two" in markdown
    assert "First line\nsecond line" not in markdown
