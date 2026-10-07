"""Tests for the deterministic offline reasoning engine.

This engine is both the credential-free backend *and* the local guardrail, so its
output must be contract-grade, not placeholder text. The tests therefore assert on the
substance of what it produces: that acceptance criteria are testable, that citations
can never be invented, and that every score it reports is arithmetically consistent.
"""

from __future__ import annotations

import pytest

from sowsprint.llm.offline import (
    QUESTIONS_PER_ROUND,
    architect_offline,
    critic_offline,
    legal_offline,
    project_key_from_title,
    tool_plan_offline,
    triage_offline,
)
from sowsprint.models import (
    CritiqueReport,
    FindingCategory,
    Jurisdiction,
    RequirementScope,
    ScopeStatus,
    Severity,
    SOWDocument,
    TechnicalBlueprint,
)

US_EVIDENCE = """[1] California consumer opt-out and sale restrictions (jurisdiction=US) id=us-ccpa-opt-out-rights
A business shall not sell or share the personal information of a California consumer who has opted out.
[2] Delaware board management authority (jurisdiction=US) id=us-delaware-board-authority
The business and affairs of every corporation organised under the Delaware General Corporation Law shall be managed by a board of directors.
[3] Limitation of liability and consequential loss (jurisdiction=US) id=us-liability-cap
Each party's aggregate liability is capped at the fees paid in the twelve months preceding the claim."""


class TestTriage:
    def test_detailed_brief_is_complete(self, detailed_brief: str) -> None:
        scope = triage_offline(detailed_brief, None, "")
        assert scope.status is ScopeStatus.COMPLETE
        assert scope.is_actionable
        assert scope.clarifying_questions == []
        assert scope.timeline_weeks == 12
        assert scope.jurisdiction is Jurisdiction.EU
        assert scope.confidence > 0.8

    def test_vague_brief_blocks_and_asks_exactly_three_questions(
        self, vague_brief: str
    ) -> None:
        scope = triage_offline(vague_brief, None, "")
        assert scope.status is ScopeStatus.INCOMPLETE
        assert not scope.is_actionable
        assert len(scope.clarifying_questions) == QUESTIONS_PER_ROUND
        assert scope.missing_variables

    def test_every_question_is_actionable(self, vague_brief: str) -> None:
        scope = triage_offline(vague_brief, None, "")
        for question in scope.clarifying_questions:
            assert question.question.endswith("?")
            assert len(question.why_blocking) > 30
            assert question.variable
            assert question.suggested_answers

    def test_questions_never_reask_a_satisfied_variable(self, detailed_brief: str) -> None:
        """Padding a short question list must not re-ask what the customer already said."""
        partial = (
            "# Portal\nBuild a React dashboard. Delivery within 8 weeks. "
            "Users are warehouse staff. Integrate with SAP. Budget: EUR 50k."
        )
        scope = triage_offline(partial, None, "")
        asked = {question.variable for question in scope.clarifying_questions}
        satisfied = {"deliverables", "timeline_weeks", "target_users", "integrations", "budget_range"}
        # Whatever is asked, well-specified variables must not reappear.
        assert not (asked & satisfied) or scope.status is ScopeStatus.INCOMPLETE

    def test_clarification_answers_promote_the_scope(self, vague_brief: str) -> None:
        first = triage_offline(vague_brief, None, "")
        assert first.status is ScopeStatus.INCOMPLETE

        answers = (
            "Delivery window is 12 weeks. Users are the 60-person inside sales team. "
            "Integrate with Salesforce and HubSpot. Success is 20% more qualified "
            "opportunities. Budget is EUR 80k."
        )
        second = triage_offline(vague_brief, first.model_dump(mode="json"), answers)
        assert second.status is ScopeStatus.COMPLETE
        assert second.revision == first.revision + 1
        assert second.timeline_weeks == 12

    def test_prior_scope_values_win_over_reinference(self, detailed_brief: str) -> None:
        first = triage_offline(detailed_brief, None, "")
        prior = first.model_dump(mode="json")
        prior["timeline_weeks"] = 26  # a human corrected the schedule
        second = triage_offline(detailed_brief, prior, "")
        assert second.timeline_weeks == 26

    def test_loop_terminates_with_documented_assumptions(self, vague_brief: str) -> None:
        """After the round budget is spent the scope must proceed, not block forever."""
        scope = triage_offline(vague_brief, None, "")
        for _ in range(3):
            scope = triage_offline(vague_brief, scope.model_dump(mode="json"), "")
        assert scope.status is ScopeStatus.COMPLETE
        assert scope.assumptions
        assert scope.clarifying_questions == []

    def test_jurisdiction_is_recorded(self) -> None:
        scope = triage_offline("A Delaware corporation needs a claims system.", None, "")
        assert scope.jurisdiction is Jurisdiction.US


class TestArchitect:
    @pytest.fixture
    def blueprint(self, detailed_brief: str) -> TechnicalBlueprint:
        scope = triage_offline(detailed_brief, None, "")
        return architect_offline(scope.model_dump(mode="json"))

    def test_produces_a_full_decomposition(self, blueprint: TechnicalBlueprint) -> None:
        assert blueprint.milestones
        assert blueprint.tech_stack
        assert blueprint.all_stories()

    def test_every_story_has_testable_acceptance_criteria(
        self, blueprint: TechnicalBlueprint
    ) -> None:
        for story in blueprint.all_stories():
            assert len(story.acceptance_criteria) >= 2, story.key
            for criterion in story.acceptance_criteria:
                rendered = criterion.render()
                assert rendered.startswith("GIVEN ")
                assert " WHEN " in rendered
                assert " THEN " in rendered
                assert criterion.given and criterion.when and criterion.then

    def test_story_points_are_within_the_agile_scale(
        self, blueprint: TechnicalBlueprint
    ) -> None:
        for story in blueprint.all_stories():
            assert 1 <= story.story_points <= 13
            assert story.story_points in (1, 2, 3, 5, 8, 13)

    def test_totals_are_arithmetically_consistent(self, blueprint: TechnicalBlueprint) -> None:
        assert blueprint.estimated_total_points == sum(
            milestone.total_points for milestone in blueprint.milestones
        )
        assert blueprint.estimated_duration_weeks == sum(
            milestone.duration_weeks for milestone in blueprint.milestones
        )
        assert blueprint.estimated_total_points > 0

    def test_last_phase_is_never_dropped(self, blueprint: TechnicalBlueprint) -> None:
        """Naive slicing drops the final milestone when the count is not divisible."""
        assert len(blueprint.milestones) == 4
        assert "handover" in blueprint.milestones[-1].name.lower()

    def test_compliance_flags_become_non_functional_requirements(
        self, blueprint: TechnicalBlueprint
    ) -> None:
        joined = " ".join(blueprint.non_functional_requirements).lower()
        assert "gdpr" in joined or "ai act" in joined

    def test_requested_technologies_appear_in_the_stack(
        self, blueprint: TechnicalBlueprint
    ) -> None:
        technologies = " ".join(choice.technology for choice in blueprint.tech_stack)
        assert "React" in technologies
        assert "FastAPI" in technologies
        assert "Salesforce" in technologies

    def test_is_deterministic(self, blueprint: TechnicalBlueprint) -> None:
        scope = triage_offline("# X\nBuild a React dashboard for staff. 8 weeks.", None, "")
        first = architect_offline(scope.model_dump(mode="json"))
        second = architect_offline(scope.model_dump(mode="json"))
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


class TestLegal:
    @pytest.fixture
    def scope(self, detailed_brief: str) -> RequirementScope:
        return triage_offline(detailed_brief, None, "")

    @pytest.fixture
    def blueprint(self, scope: RequirementScope) -> TechnicalBlueprint:
        return architect_offline(scope.model_dump(mode="json"))

    def test_eu_document_contains_every_required_clause(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        headings = {clause.heading for clause in sow.clauses}
        for required in (
            "Definitions",
            "Scope of Services",
            "Deliverables and Milestones",
            "Acceptance Procedure",
            "Change Control",
            "Fees and Payment",
            "Data Protection and GDPR Compliance",
            "Artificial Intelligence Act Compliance",
            "Governing Law and Jurisdiction",
        ):
            assert required in headings, required

    def test_us_document_uses_the_us_template(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"), blueprint.model_dump(mode="json"), US_EVIDENCE, "US"
        )
        headings = {clause.heading for clause in sow.clauses}
        assert "Representations and Warranties" in headings
        assert "Indemnification" in headings
        assert "Governing Law and Venue" in headings
        assert "Data Protection and GDPR Compliance" not in headings
        assert "Delaware" in sow.governing_law

    def test_no_clause_body_is_empty(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        """An empty clause is a contract defect, not a formatting problem."""
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        for clause in sow.clauses:
            assert len(clause.body.strip()) >= 40, f"{clause.number} {clause.heading}"

    def test_citations_only_reference_supplied_evidence(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        """The Legal agent must never invent a citation id."""
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        known = {
            "eu-gdpr-breach-notification",
            "eu-aiact-high-risk-obligations",
            "eu-governing-law-ireland",
        }
        for clause in sow.clauses:
            for citation in clause.citations:
                assert citation in known, f"clause {clause.number} cited {citation}"

    def test_payment_schedule_totals_one_hundred_percent(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        assert sow.payment_schedule
        assert pytest.approx(sum(p.percentage for p in sow.payment_schedule), abs=0.01) == 100.0

    def test_fees_clause_quotes_the_schedule(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        fees = next(c for c in sow.clauses if c.heading == "Fees and Payment")
        assert "%" in fees.body
        assert "invoice" in fees.body.lower()

    def test_milestones_match_the_blueprint(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )
        deliveries = next(c for c in sow.clauses if c.heading == "Deliverables and Milestones")
        for milestone in blueprint.milestones:
            assert milestone.name in deliveries.body

    def test_repair_round_adds_a_remediation_annex(
        self, scope: RequirementScope, blueprint: TechnicalBlueprint, sample_evidence: str
    ) -> None:
        sow = legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
            repair_instructions=["Bind the liability clause to retrieved evidence."],
            previous_draft={},
        )
        headings = {clause.heading for clause in sow.clauses}
        assert "Remediation Annex" in headings
        annex = next(c for c in sow.clauses if c.heading == "Remediation Annex")
        assert "liability clause" in annex.body

    def test_works_without_evidence(self, scope: RequirementScope) -> None:
        """Retrieval failure must degrade the draft, never abort it."""
        sow = legal_offline(scope.model_dump(mode="json"), None, "", "EU")
        assert isinstance(sow, SOWDocument)
        assert sow.clauses
        assert sow.word_count() > 200


class TestCritic:
    @pytest.fixture
    def good_sow(self, detailed_brief: str, sample_evidence: str) -> SOWDocument:
        scope = triage_offline(detailed_brief, None, "")
        blueprint = architect_offline(scope.model_dump(mode="json"))
        return legal_offline(
            scope.model_dump(mode="json"),
            blueprint.model_dump(mode="json"),
            sample_evidence,
            "EU",
        )

    def test_audit_returns_a_machine_checkable_verdict(self, good_sow: SOWDocument) -> None:
        report = critic_offline(good_sow.model_dump(mode="json"), "", "EU")
        assert isinstance(report, CritiqueReport)
        assert 0.0 <= report.quality_score <= 1.0
        assert 0.0 <= report.grounding_ratio <= 1.0
        assert 0.0 <= report.hallucination_score <= 1.0
        assert report.checked_clauses == len(good_sow.clauses)
        assert report.summary

    def test_detects_a_fabricated_citation(self, good_sow: SOWDocument) -> None:
        """The single most dangerous failure mode: citing evidence that does not exist."""
        payload = good_sow.model_dump(mode="json")
        payload["clauses"][6]["citations"] = ["eu-totally-invented-clause-99"]
        report = critic_offline(payload, US_EVIDENCE, "EU")

        hallucinations = [
            finding
            for finding in report.findings
            if finding.category is FindingCategory.HALLUCINATION
        ]
        assert hallucinations
        assert hallucinations[0].severity is Severity.CRITICAL
        assert not report.passed
        assert "eu-totally-invented-clause-99" in report.unsupported_citations

    def test_detects_a_missing_required_clause(self, good_sow: SOWDocument) -> None:
        payload = good_sow.model_dump(mode="json")
        payload["clauses"] = [
            clause for clause in payload["clauses"] if clause["heading"] != "Indemnification"
        ]
        report = critic_offline(payload, US_EVIDENCE, "US")
        missing = [
            finding for finding in report.findings if finding.category is FindingCategory.MISSING_CLAUSE
        ]
        assert missing
        assert any("Indemnification" in finding.description for finding in missing)

    def test_detects_broken_payment_arithmetic(self, good_sow: SOWDocument) -> None:
        payload = good_sow.model_dump(mode="json")
        payload["payment_schedule"][0]["percentage"] = 5.0
        report = critic_offline(payload, "", "EU")
        commercial = [
            finding
            for finding in report.findings
            if finding.category is FindingCategory.COMMERCIAL_RISK
        ]
        assert commercial
        assert not report.passed

    def test_detects_subjective_phrasing(self, good_sow: SOWDocument) -> None:
        payload = good_sow.model_dump(mode="json")
        payload["clauses"][3]["body"] = (
            "The Supplier shall use reasonable efforts to deliver as appropriate, TBD."
        )
        report = critic_offline(payload, "", "EU")
        ambiguity = [
            finding for finding in report.findings if finding.category is FindingCategory.AMBIGUITY
        ]
        assert ambiguity

    def test_malformed_draft_is_a_critical_finding_not_a_crash(self) -> None:
        report = critic_offline({"title": "broken"}, "", "EU")
        assert not report.passed
        assert report.hallucination_score == 1.0
        assert report.findings[0].severity is Severity.CRITICAL

    def test_repair_instructions_are_actionable_and_deduplicated(
        self, good_sow: SOWDocument
    ) -> None:
        payload = good_sow.model_dump(mode="json")
        payload["clauses"][6]["citations"] = ["nope-1"]
        payload["clauses"][7]["citations"] = ["nope-1"]
        report = critic_offline(payload, US_EVIDENCE, "EU")
        assert report.repair_instructions
        assert len(report.repair_instructions) == len(set(report.repair_instructions))
        assert all(len(instruction) > 10 for instruction in report.repair_instructions)

    def test_passing_report_has_no_blocking_findings(self, good_sow: SOWDocument) -> None:
        report = critic_offline(
            good_sow.model_dump(mode="json"), "", "EU", blueprint_payload=None
        )
        if report.passed:
            assert report.blocking_findings == []
            assert report.quality_score >= 0.72


class TestToolPlanning:
    def test_plan_follows_the_required_sequence(self, detailed_brief: str) -> None:
        scope = triage_offline(detailed_brief, None, "")
        blueprint = architect_offline(scope.model_dump(mode="json"))
        plan = tool_plan_offline(blueprint.model_dump(mode="json"), None)

        calls = plan["calls"]
        assert calls[0]["tool"] == "create_jira_project_workspace"
        assert plan["summary"]

        epics = [c for c in calls if c["arguments"].get("issue_type") == "Epic"]
        stories = [c for c in calls if c["arguments"].get("issue_type") == "Story"]
        assert len(epics) == sum(len(m.epics) for m in blueprint.milestones)
        assert len(stories) == len(blueprint.all_stories())

    def test_stories_link_to_their_epic(self, detailed_brief: str) -> None:
        scope = triage_offline(detailed_brief, None, "")
        blueprint = architect_offline(scope.model_dump(mode="json"))
        plan = tool_plan_offline(blueprint.model_dump(mode="json"), None)

        story = next(c for c in plan["calls"] if c["arguments"].get("issue_type") == "Story")
        assert story["arguments"]["parent_key"]
        assert story["arguments"]["story_points"] >= 1
        assert "GIVEN" in story["arguments"]["description"]

    def test_notion_page_added_when_a_sow_is_present(self, detailed_brief: str) -> None:
        scope = triage_offline(detailed_brief, None, "")
        blueprint = architect_offline(scope.model_dump(mode="json"))
        sow = legal_offline(scope.model_dump(mode="json"), blueprint.model_dump(mode="json"), "", "EU")

        without = tool_plan_offline(blueprint.model_dump(mode="json"), None)
        with_sow = tool_plan_offline(blueprint.model_dump(mode="json"), sow.model_dump(mode="json"))

        assert not any(c["tool"] == "create_notion_page" for c in without["calls"])
        assert any(c["tool"] == "create_notion_page" for c in with_sow["calls"])


class TestProjectKey:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Statement of Work — Project Aurora Data Platform", "AURORA"),
            ("Helios Claims Automation", "HELIOS"),
            ("Some kind of AI thing for our sales team", "SALES"),
            ("Vendor onboarding system", "VENDOR"),
        ],
    )
    def test_keys_avoid_generic_delivery_vocabulary(self, title: str, expected: str) -> None:
        assert project_key_from_title(title) == expected

    def test_key_is_jira_safe(self) -> None:
        key = project_key_from_title("Ünïcödé & Symbols!! Platform")
        assert key.isalnum()
        assert 2 <= len(key) <= 10

    def test_empty_title_falls_back(self) -> None:
        assert project_key_from_title("") == "SOW"
