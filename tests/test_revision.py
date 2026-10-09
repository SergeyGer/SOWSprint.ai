"""Tests for clause-level revision.

The property under test is a guarantee, not a preference: a clause the parties locked
comes back byte-identical, whatever the model returns. Asking a model to leave agreed
text alone works most of the time; restoring it in code works every time, and a
silently altered liability clause is not a defect anyone forgives.
"""

from __future__ import annotations

from sowsprint.llm.offline import enforce_lock, legal_revision_offline
from sowsprint.models import SOWClause, SOWDocument, diff_sow


def make_document(*, revision: int = 0) -> SOWDocument:
    return SOWDocument(
        title="Statement of Work — Test",
        client_name="Client",
        vendor_name="Vendor",
        revision=revision,
        clauses=[
            SOWClause(number="1", heading="Definitions", body="Defined terms are as follows."),
            SOWClause(
                number="2",
                heading="Limitation of Liability",
                body="Liability is capped at the fees paid in the preceding twelve months.",
                locked=True,
            ),
            SOWClause(number="3", heading="Termination", body="Termination on sixty days notice."),
        ],
    )


class TestLockEnforcement:
    def test_a_tampered_locked_clause_is_restored_verbatim(self) -> None:
        previous = make_document()
        tampered = previous.model_copy(deep=True)
        tampered.clauses[1].body = "Liability is unlimited."

        fixed, restored = enforce_lock(previous, tampered)

        assert restored == ["2"]
        assert fixed.clauses[1].body == previous.clauses[1].body
        assert fixed.clauses[1].locked is True

    def test_an_unlocked_clause_is_left_alone(self) -> None:
        previous = make_document()
        edited = previous.model_copy(deep=True)
        edited.clauses[2].body = "Termination on thirty days notice, mutual."

        fixed, restored = enforce_lock(previous, edited)

        assert restored == []
        assert fixed.clauses[2].body == "Termination on thirty days notice, mutual."

    def test_a_deleted_locked_clause_comes_back(self) -> None:
        previous = make_document()
        dropped = previous.model_copy(deep=True)
        dropped.clauses = [c for c in dropped.clauses if c.number != "2"]

        fixed, restored = enforce_lock(previous, dropped)

        assert restored == ["2"]
        assert any(c.number == "2" for c in fixed.clauses)

    def test_a_renamed_locked_heading_is_restored(self) -> None:
        previous = make_document()
        renamed = previous.model_copy(deep=True)
        renamed.clauses[1].heading = "Liability (unlimited)"

        fixed, restored = enforce_lock(previous, renamed)

        assert restored == ["2"]
        assert fixed.clauses[1].heading == "Limitation of Liability"

    def test_no_locks_is_a_no_op(self) -> None:
        previous = make_document()
        for clause in previous.clauses:
            clause.locked = False
        edited = previous.model_copy(deep=True)
        edited.clauses[1].body = "Anything."

        fixed, restored = enforce_lock(previous, edited)

        assert restored == []
        assert fixed.clauses[1].body == "Anything."


class TestDiff:
    def test_classifies_every_kind_of_change(self) -> None:
        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].body = "Termination on thirty days notice, mutual."
        current.clauses.append(SOWClause(number="4", heading="Force Majeure", body="Neither party is liable for events beyond its control."))

        diff = diff_sow(previous, current)
        by_number = {c.number: c.change for c in diff.changes}

        assert by_number["1"] == "unchanged"
        assert by_number["2"] == "locked"
        assert by_number["3"] == "modified"
        assert by_number["4"] == "added"

    def test_detects_removals(self) -> None:
        previous = make_document()
        current = make_document(revision=1)
        current.clauses = [c for c in current.clauses if c.number != "3"]

        diff = diff_sow(previous, current)
        assert any(c.change == "removed" and c.number == "3" for c in diff.changes)

    def test_matches_by_heading_when_numbering_shifts(self) -> None:
        """A renumbered clause is an edit, not a delete plus an add."""
        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].number = "4"
        current.clauses[2].body = "Changed text."

        diff = diff_sow(previous, current)
        changed = [c for c in diff.changes if c.heading == "Termination"]
        assert len(changed) == 1
        assert changed[0].change == "modified"

    def test_summary_counts_are_reported(self) -> None:
        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].body = "Changed."
        summary = diff_sow(previous, current).summary()
        assert "modified" in summary
        assert "locked" in summary

    def test_edited_excludes_unchanged_and_locked(self) -> None:
        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].body = "Changed."
        diff = diff_sow(previous, current)
        assert {c.number for c in diff.edited} == {"3"}
        assert {c.number for c in diff.preserved} == {"2"}


class TestOfflineRevision:
    def test_records_the_request_without_fabricating_edits(self) -> None:
        """The offline engine cannot reason about an instruction, so it must not pretend to."""
        previous = make_document()
        revised = legal_revision_offline(
            previous.model_dump(mode="json"),
            "Make the liability cap mutual",
            {},
            "",
            "EU",
        )
        diff = diff_sow(previous, revised)

        assert revised.revision == previous.revision + 1
        assert {c.number for c in diff.preserved} == {"2"}
        # Nothing that existed was altered or dropped.
        assert not any(c.change in ("modified", "removed") for c in diff.changes)
        annex = revised.clauses[-1]
        assert "Make the liability cap mutual" in annex.body

    def test_appends_rather_than_renumbering(self) -> None:
        previous = make_document()
        revised = legal_revision_offline(previous.model_dump(mode="json"), "x", {}, "", "EU")
        assert revised.clauses[-1].number == "4"
        assert [c.number for c in revised.clauses[:3]] == ["1", "2", "3"]

    def test_handles_decimal_numbering(self) -> None:
        previous = make_document()
        previous.clauses.append(SOWClause(number="3.2", heading="Notice", body="Notice text."))
        revised = legal_revision_offline(previous.model_dump(mode="json"), "x", {}, "", "EU")
        assert revised.clauses[-1].number == "4"

    def test_records_the_revision_in_the_notes(self) -> None:
        previous = make_document()
        revised = legal_revision_offline(
            previous.model_dump(mode="json"), "Reduce payment milestones to three", {}, "", "EU"
        )
        assert any("Reduce payment milestones" in note for note in revised.compliance_notes)


class TestRevisionRendering:
    def test_diff_markdown_shows_before_and_after(self) -> None:
        from sowsprint.export.sow_markdown import render_sow_diff

        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].body = "Mutual termination on thirty days notice."
        rendered = render_sow_diff(diff_sow(previous, current))

        assert "Revision 0 → 1" in rendered
        assert "**Before**" in rendered
        assert "**After**" in rendered
        assert "Mutual termination" in rendered
        assert "Locked clauses preserved" in rendered

    def test_diff_artefact_is_written(self) -> None:
        import shutil

        from sowsprint.export.bundle import render_deliverables

        previous = make_document()
        current = make_document(revision=1)
        current.clauses[2].body = "Changed body text for the revision artefact."
        run_id = "pytest-revision-artefact"
        try:
            refs = render_deliverables(
                session_id="pytest",
                run_id=run_id,
                scope=None,
                blueprint=None,
                sow=current.model_dump(mode="json"),
                critique=None,
                integrations=None,
                cost=None,
                diff=diff_sow(previous, current),
            )
            kinds = {ref["kind"] for ref in refs}
            assert "revision_markdown" in kinds
        finally:
            from sowsprint.config import ARTIFACT_DIR

            shutil.rmtree(ARTIFACT_DIR / run_id, ignore_errors=True)


class TestRevisionThroughTheSession:
    def test_revise_enforces_locks_end_to_end(
        self, settings, mini_pipeline, tmp_path
    ) -> None:
        """The whole path: a locked clause survives an instruction aimed at it."""
        from sowsprint.agents import ScopingSession
        from sowsprint.telemetry import tracker

        tracker.drop_ledger("pytest-revision")
        offline = settings.model_copy(update={"llm_provider": "mock"})
        session = ScopingSession("pytest-revision", settings=offline, pipeline=mini_pipeline)

        session.start(
            """# Test Platform
Build a React dashboard for 200 warehouse staff.
Our goal is faster monthly reporting for operations.
Delivery in 8 weeks. Integrate with SAP for shipment events.
Success means 20 percent faster reporting. Budget: EUR 50k.""",
            jurisdiction="EU",
        )
        assert session.last_outcome and session.last_outcome.sow is not None
        first = session.last_outcome.sow

        # Lock the first clause, then ask for a change that would touch everything.
        locked_number = first.clauses[0].number
        assert session.set_locks([locked_number], locked=True) == [locked_number]
        original_body = first.clauses[0].body

        for _event in session.revise("Rewrite the entire contract from scratch."):
            pass

        revised = session.last_outcome.sow
        assert revised is not None
        assert revised.revision == first.revision + 1
        restored = next(c for c in revised.clauses if c.number == locked_number)
        assert restored.body == original_body
        assert restored.locked is True

    def test_set_locks_reports_only_real_changes(
        self, settings, mini_pipeline
    ) -> None:
        from sowsprint.agents import ScopingSession
        from sowsprint.telemetry import tracker

        tracker.drop_ledger("pytest-locks")
        offline = settings.model_copy(update={"llm_provider": "mock"})
        session = ScopingSession("pytest-locks", settings=offline, pipeline=mini_pipeline)
        session.start(
            """# Test Platform
Build a React dashboard for 200 warehouse staff.
Our goal is faster monthly reporting for operations.
Delivery in 8 weeks. Integrate with SAP for shipment events.
Success means 20 percent faster reporting. Budget: EUR 50k.""",
            jurisdiction="EU",
        )
        number = session.last_outcome.sow.clauses[0].number

        assert session.set_locks([number], locked=True) == [number]
        # Locking an already-locked clause is not a change.
        assert session.set_locks([number], locked=True) == []
        assert session.set_locks(["999"], locked=True) == []
        assert session.set_locks([number], locked=False) == [number]
