"""Tests for regime selection: EU, US, and the dual-regime case.

``BOTH`` is not a label — it changes three things that must stay consistent:
the retrieval filter (no chunk is tagged "BOTH"), the required-clause template, and
the conflict-resolution rule the drafting agent must state.
"""

from __future__ import annotations

import pytest

from sowsprint.llm.offline import _GOVERNING_LAW, _REQUIRED_CLAUSES, legal_offline, triage_offline
from sowsprint.models import Jurisdiction
from sowsprint.rag.pipeline import RagPipeline
from sowsprint.rag.schema import build_jurisdiction_filter, matches_filter


class TestJurisdictionEnum:
    def test_both_expands_into_its_members(self) -> None:
        assert Jurisdiction.BOTH.regimes == (Jurisdiction.EU, Jurisdiction.US)

    def test_single_regimes_return_themselves(self) -> None:
        assert Jurisdiction.EU.regimes == (Jurisdiction.EU,)
        assert Jurisdiction.US.regimes == (Jurisdiction.US,)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("EU", Jurisdiction.EU),
            ("eu", Jurisdiction.EU),
            ("Germany", Jurisdiction.EU),
            ("US", Jurisdiction.US),
            ("usa", Jurisdiction.US),
            ("BOTH", Jurisdiction.BOTH),
            ("EU+US", Jurisdiction.BOTH),
            ("dual", Jurisdiction.BOTH),
            ("EU + US", Jurisdiction.BOTH),
        ],
    )
    def test_coerce_accepts_ui_variants(self, value: str, expected: Jurisdiction) -> None:
        assert Jurisdiction.coerce(value) is expected

    def test_coerce_falls_back_rather_than_raising(self) -> None:
        assert Jurisdiction.coerce("klingon") is Jurisdiction.EU
        assert Jurisdiction.coerce(None, default=Jurisdiction.US) is Jurisdiction.US

    def test_labels_are_distinct(self) -> None:
        labels = {j.label for j in Jurisdiction}
        assert len(labels) == 3
        assert "Dual regime" in Jurisdiction.BOTH.label


class TestDualRegimeFiltering:
    def test_no_chunk_is_ever_tagged_both(self) -> None:
        """`BOTH` is a selection, so the filter must match either member regime."""
        eu = {"jurisdiction": "EU", "chunk_id": "a"}
        us = {"jurisdiction": "US", "chunk_id": "b"}
        values = [r.value for r in Jurisdiction.BOTH.regimes]
        assert matches_filter(eu, values) is True
        assert matches_filter(us, values) is True

    def test_single_regime_still_excludes_the_other(self) -> None:
        assert matches_filter({"jurisdiction": "US"}, ["EU"]) is False

    def test_qdrant_filter_uses_match_any_for_several_values(self) -> None:
        query_filter = build_jurisdiction_filter(["EU", "US"])
        assert query_filter is not None
        assert "MatchAny" in type(query_filter.must[0].match).__name__

    def test_qdrant_filter_uses_match_value_for_one(self) -> None:
        query_filter = build_jurisdiction_filter(["EU"])
        assert "MatchValue" in type(query_filter.must[0].match).__name__


class TestBalancedRetrieval:
    """A dual engagement must draw from both corpora in equal measure."""

    def test_both_returns_evidence_from_each_regime(self, full_pipeline: RagPipeline) -> None:
        scope = triage_offline(
            "A US insurer with a Berlin subsidiary needs a claims platform. "
            "Budget: $480,000, 20 weeks, 500 adjusters, integrate with Salesforce.",
            None,
            "",
        )
        result = full_pipeline.retriever.retrieve_multi_topic(
            scope, None, jurisdiction=Jurisdiction.BOTH
        )
        by_regime: dict[str, int] = {}
        for chunk in result.chunks:
            by_regime[chunk.jurisdiction] = by_regime.get(chunk.jurisdiction, 0) + 1

        assert result.chunks
        assert set(by_regime) == {"EU", "US"}, by_regime
        # Neither regime may be a token presence.
        assert min(by_regime.values()) >= 3, by_regime
        assert result.diagnostics is not None
        assert result.diagnostics.regimes == ["EU", "US"]

    def test_single_regime_returns_only_that_regime(self, full_pipeline: RagPipeline) -> None:
        scope = triage_offline(
            "A Berlin logistics firm needs a GDPR-compliant analytics dashboard. "
            "Budget: EUR 120k, 12 weeks, 200 warehouse staff.",
            None,
            "",
        )
        for jurisdiction in (Jurisdiction.EU, Jurisdiction.US):
            result = full_pipeline.retriever.retrieve_multi_topic(
                scope, None, jurisdiction=jurisdiction
            )
            assert result.chunks
            assert {c.jurisdiction for c in result.chunks} == {jurisdiction.value}


class TestDualRegimeContract:
    @pytest.fixture
    def scope(self) -> object:
        return triage_offline(
            "# Transatlantic Platform\n"
            "Meridian Mutual (Hartford, CT) with a Berlin subsidiary needs a claims "
            "triage platform for 500 adjusters.\n"
            "- Build a React portal\n- Develop a Python FastAPI backend\n"
            "Budget: $480,000, 20 weeks. Success means 25 percent faster cycle time.\n"
            "Processes personal data of EU employees and protected health information.",
            None,
            "",
        )

    def test_template_covers_every_concept_from_both_regimes(self) -> None:
        """No gaps — but also no duplication.

        The two templates label shared concepts differently ("Intellectual Property"
        vs "Intellectual Property and Work Product"). A contract must state each
        concept once, so the dual template picks one label per concept rather than
        being a literal union of the two lists. What must hold is that no *topic* is
        lost.
        """
        both = set(_REQUIRED_CLAUSES[Jurisdiction.BOTH])
        # Distinctive topics from each regime must survive.
        for heading in (
            "Data Protection and GDPR Compliance",
            "Artificial Intelligence Act Compliance",
            "Data Protection and Privacy",
            "Representations and Warranties",
            "Indemnification",
            "Warranties and Service Levels",
        ):
            assert heading in both, heading

        # One clause per concept: no near-duplicate headings.
        import re

        def canonical(heading: str) -> str:
            return re.sub(r"[^a-z]", "", heading.lower())

        canon = [canonical(h) for h in both]
        assert len(canon) == len(set(canon))

        # The dual template must be at least as complete as either single regime.
        assert len(both) >= len(_REQUIRED_CLAUSES[Jurisdiction.EU])
        assert len(both) >= len(_REQUIRED_CLAUSES[Jurisdiction.US])

    def test_template_adds_the_three_dual_only_clauses(self) -> None:
        both = set(_REQUIRED_CLAUSES[Jurisdiction.BOTH])
        for heading in (
            "Dual-Regime Compliance and Order of Precedence",
            "Cross-Border Data Transfers and Transfer Mechanisms",
            "Conflicting Obligations and Stricter-Standard Rule",
        ):
            assert heading in both
            # ...and that they are genuinely new, not inherited from one regime.
            assert heading not in _REQUIRED_CLAUSES[Jurisdiction.EU]
            assert heading not in _REQUIRED_CLAUSES[Jurisdiction.US]

    def test_governing_law_preserves_both_regimes_mandatory_rules(self) -> None:
        text = _GOVERNING_LAW[Jurisdiction.BOTH].lower()
        assert "elected by the parties" in text
        assert "mandatory provisions of both" in text

    def test_document_contains_every_dual_clause(self, scope) -> None:
        sow = legal_offline(scope.model_dump(mode="json"), None, "", "BOTH")
        headings = {clause.heading for clause in sow.clauses}
        for required in _REQUIRED_CLAUSES[Jurisdiction.BOTH]:
            assert required in headings, required

    def test_stricter_standard_rule_states_a_resolution(self, scope) -> None:
        """A contract silent on what happens when the regimes disagree is the trap."""
        sow = legal_offline(scope.model_dump(mode="json"), None, "", "BOTH")
        clause = next(
            c for c in sow.clauses if c.heading == "Conflicting Obligations and Stricter-Standard Rule"
        )
        body = clause.body.lower()
        assert "stricter standard" in body
        assert "breach" in body  # a worked example, not just an abstract rule

    def test_transfer_clause_names_real_mechanisms(self, scope) -> None:
        sow = legal_offline(scope.model_dump(mode="json"), None, "", "BOTH")
        clause = next(
            c for c in sow.clauses if c.heading == "Cross-Border Data Transfers and Transfer Mechanisms"
        )
        body = clause.body.lower()
        assert "standard contractual clauses" in body
        assert "adequacy" in body

    def test_no_dual_clause_leaks_into_single_regime_templates(self, scope) -> None:
        for jurisdiction in ("EU", "US"):
            sow = legal_offline(scope.model_dump(mode="json"), None, "", jurisdiction)
            headings = {clause.heading for clause in sow.clauses}
            assert "Conflicting Obligations and Stricter-Standard Rule" not in headings

    def test_every_dual_clause_has_a_substantive_body(self, scope) -> None:
        sow = legal_offline(scope.model_dump(mode="json"), None, "", "BOTH")
        assert len(sow.clauses) >= 20
        for clause in sow.clauses:
            assert len(clause.body.strip()) >= 40, f"{clause.number} {clause.heading}"
