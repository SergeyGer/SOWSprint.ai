"""Tests for the deterministic requirement-analysis rule set.

These rules are the analytical core of the offline engine, and they also influence
Triage output when a cloud model is in use (detected signals are attached to the scope
for audit). A regression here silently degrades contract quality, so each extractor is
tested against realistic prose rather than synthetic strings.
"""

from __future__ import annotations

import pytest

from sowsprint.llm import nlp
from sowsprint.models import Jurisdiction


class TestTokenisation:
    def test_split_sentences_keeps_bullets_intact(self) -> None:
        text = "First point here.\n- A bullet item\n- Another bullet\nSecond paragraph."
        units = nlp.split_sentences(text)
        assert "A bullet item" in units
        assert "Another bullet" in units
        assert "First point here." in units

    def test_bullet_lines_only_returns_list_items(self) -> None:
        text = "Intro line.\n- one\n* two\n1. three\nplain"
        assert nlp.bullet_lines(text) == ["one", "two", "three"]

    def test_normalise_collapses_horizontal_whitespace(self) -> None:
        assert nlp.normalise("a   b\t\tc") == "a b c"

    def test_dedupe_is_order_preserving_and_case_insensitive(self) -> None:
        assert nlp.dedupe(["Alpha", "alpha", "Beta", "ALPHA"]) == ["Alpha", "Beta"]


class TestJurisdictionInference:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Our Berlin office needs this for German staff.", Jurisdiction.EU),
            ("GDPR compliance is mandatory for this platform.", Jurisdiction.EU),
            ("The EU AI Act applies to the scoring component.", Jurisdiction.EU),
            ("We are a Delaware corporation.", Jurisdiction.US),
            ("SEC reporting obligations apply to the parent company.", Jurisdiction.US),
            ("California consumers must be able to opt out.", Jurisdiction.US),
        ],
    )
    def test_cues_map_to_the_right_regime(self, text: str, expected: Jurisdiction) -> None:
        jurisdiction, _signals = nlp.infer_jurisdiction(text)
        assert jurisdiction is expected

    def test_conflicting_cues_resolve_by_weight(self) -> None:
        # GDPR (+3) and the AI Act (+3) outweigh a single "United States" (-2).
        text = "GDPR and the EU AI Act apply, even though the parent is in the United States."
        jurisdiction, _ = nlp.infer_jurisdiction(text)
        assert jurisdiction is Jurisdiction.EU

    def test_neutral_text_defaults_to_eu(self) -> None:
        jurisdiction, _ = nlp.infer_jurisdiction("We need a new intranet.")
        assert jurisdiction is Jurisdiction.EU

    def test_signals_are_reported_for_audit(self) -> None:
        _, signals = nlp.infer_jurisdiction("GDPR applies and we are in Germany.")
        assert "GDPR" in signals["EU"]
        assert "Germany" in signals["EU"]


class TestComplianceDetection:
    def test_detects_gdpr_from_personal_data_phrasing(self) -> None:
        flags, _ = nlp.detect_compliance_flags("The system processes personal data.")
        assert any("GDPR" in flag for flag in flags)

    @pytest.mark.parametrize(
        "text,needle",
        [
            ("We handle credit card payments.", "PCI DSS"),
            ("The platform stores protected health information.", "HIPAA"),
            ("Biometric identification is required.", "Biometric"),
            ("Automated decision making will rank applicants.", "AI Act"),
            ("SOX controls are required for reporting.", "SOX"),
            ("We must comply with AML and KYC rules.", "AML"),
        ],
    )
    def test_known_regulatory_triggers(self, text: str, needle: str) -> None:
        flags, _ = nlp.detect_compliance_flags(text)
        assert any(needle.lower() in flag.lower() for flag in flags), flags

    def test_no_false_positive_on_clean_text(self) -> None:
        flags, _ = nlp.detect_compliance_flags("Build a public marketing website.")
        assert flags == []


class TestNumericExtraction:
    @pytest.mark.parametrize(
        "text,expected_weeks",
        [
            ("Delivery within 12 weeks.", 12),
            ("We need this in 3 months.", 13),
            ("Completed over 4 sprints.", 8),
            ("Go live in 90 days.", 13),
        ],
    )
    def test_timeline_conversion(self, text: str, expected_weeks: int) -> None:
        weeks, _signal = nlp.extract_timeline_weeks(text)
        assert weeks == expected_weeks

    def test_timeline_absent_returns_none(self) -> None:
        assert nlp.extract_timeline_weeks("No schedule has been agreed.")[0] is None

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Budget: EUR 120k", "EUR 120k"),
            ("Our budget is $250,000.", "$250,000"),
            ("Approved spend of £80k", "£80k"),
            ("Total cost of €1.2m", "€1.2m"),
        ],
    )
    def test_budget_formats(self, text: str, expected: str) -> None:
        value, _signal = nlp.extract_budget(text)
        assert value is not None
        assert expected.replace(",", "") in value.replace(",", "")

    def test_bare_number_is_not_a_budget(self) -> None:
        """A currency signal is mandatory — a naked figure must never reach a contract."""
        assert nlp.extract_budget("We reviewed 120 items.")[0] is None

    def test_team_size(self) -> None:
        assert nlp.extract_team_size("Delivered by a team of 8 engineers.") == 8


class TestTechnologyDetection:
    def test_groups_by_layer(self) -> None:
        grouped = nlp.detect_technologies("A React frontend with a Python FastAPI backend on AWS.")
        assert "react" in grouped.get("frontend", [])
        assert "python" in grouped.get("backend", [])
        assert "aws" in grouped.get("infrastructure", [])

    def test_word_boundaries_prevent_false_positives(self) -> None:
        """'go' must not match inside 'going'; 'rust' not inside 'trust'."""
        grouped = nlp.detect_technologies("We are going to build trust with users.")
        assert "go" not in grouped.get("backend", [])
        assert "rust" not in grouped.get("backend", [])

    def test_display_names_preserve_brand_casing(self) -> None:
        assert nlp.display_tech("postgresql") == "PostgreSQL"
        assert nlp.display_tech("fastapi") == "FastAPI"
        assert nlp.display_tech("aws") == "AWS"
        assert nlp.display_tech("unknownthing") == "Unknownthing"


class TestDeliverableExtraction:
    def test_extracts_bulleted_deliverables(self, detailed_brief: str) -> None:
        deliverables = nlp.extract_deliverables(detailed_brief)
        joined = " ".join(deliverables).lower()
        assert "react dashboard" in joined
        assert "fastapi" in joined
        assert len(deliverables) >= 3

    def test_excludes_out_of_scope_items(self) -> None:
        text = (
            "Deliver a customer portal.\n"
            "Out of scope: mobile app and phase 2 reporting.\n"
            "Build an admin console."
        )
        deliverables = nlp.extract_deliverables(text)
        joined = " ".join(deliverables).lower()
        assert "admin console" in joined
        assert "mobile app" not in joined or "out of scope" not in joined

    def test_out_of_scope_is_captured_separately(self) -> None:
        text = "Build a portal. Out of scope: mobile app and phase 2 reporting."
        excluded = nlp.extract_out_of_scope(text)
        assert excluded
        assert any("mobile app" in item.lower() for item in excluded)

    def test_never_returns_empty_for_substantive_prose(self) -> None:
        text = "We want a system that handles invoices automatically for our finance team."
        assert nlp.extract_deliverables(text)


class TestQualitativeExtraction:
    def test_business_goal_prefers_intent_markers(self) -> None:
        text = (
            "We need a new portal.\n"
            "Our goal is to reduce manual processing time by 40%.\n"
            "It must be secure."
        )
        goal = nlp.extract_business_goal(text)
        assert "reduce manual processing" in goal.lower()

    def test_constraints_require_a_binding_marker(self) -> None:
        text = "The system must run on-premise. We like blue buttons."
        constraints = nlp.extract_constraints(text)
        assert any("on-premise" in item for item in constraints)
        assert not any("blue buttons" in item for item in constraints)

    def test_success_metrics_need_a_measurable_signal(self) -> None:
        text = "Success means 30% fewer errors. The team is friendly."
        metrics = nlp.extract_success_metrics(text)
        assert any("30%" in item for item in metrics)
        assert not any("friendly" in item for item in metrics)

    def test_risks(self) -> None:
        text = "The legacy system is a dependency risk. We like the design."
        risks = nlp.extract_risks(text)
        assert any("legacy" in item.lower() for item in risks)


class TestTitleExtraction:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("# Project Aurora Data Platform\nBuild a dashboard.", "Project Aurora Data Platform"),
            (
                "Nordwind Logistics GmbH needs a customer analytics platform in Germany.",
                "Customer analytics platform in Germany",
            ),
            (
                "We require a GDPR-compliant claims portal for our European brokers.",
                "GDPR-compliant claims portal for our European brokers",
            ),
            (
                "Acme Corp wants a vendor onboarding system for 300 suppliers.",
                "Vendor onboarding system for 300 suppliers",
            ),
            ("Build a real-time fraud detection service.", "Real-time fraud detection service"),
        ],
    )
    def test_titles_are_compact_and_free_of_scaffolding(self, text: str, expected: str) -> None:
        assert nlp.extract_title(text) == expected

    def test_title_is_bounded(self) -> None:
        long_text = "We need " + " ".join(f"word{i}" for i in range(60)) + " platform."
        assert len(nlp.extract_title(long_text)) <= 82


class TestFullAnalysis:
    def test_analyse_returns_a_coherent_signal_bundle(self, detailed_brief: str) -> None:
        signals = nlp.analyse(detailed_brief)
        assert signals.title == "Project Aurora Data Platform"
        assert signals.jurisdiction is Jurisdiction.EU
        assert signals.timeline_weeks == 12
        assert signals.budget_range is not None
        assert signals.compliance_flags
        assert signals.deliverables
        assert "technology" in signals.signals

    def test_analysis_is_deterministic(self, detailed_brief: str) -> None:
        """Byte-identical output is what makes the offline engine CI-reproducible."""
        first = nlp.analyse(detailed_brief)
        second = nlp.analyse(detailed_brief)
        assert first.deliverables == second.deliverables
        assert first.compliance_flags == second.compliance_flags
        assert first.signals == second.signals
