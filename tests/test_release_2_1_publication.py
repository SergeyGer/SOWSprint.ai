"""Release 2.1, R1 — nothing is written without a passing audit and a version-bound approval.

These are the T01 and T02 rows of the acceptance matrix, expressed as tests rather than as
prose, because the defect they cover was not a missing feature: the system published a contract
its own audit had rejected, and did it while the interface showed FAILED beside an enabled
button.

Each test asserts **zero external writes**, not merely a failed status. A run that reports a
failure after creating a Jira project has still created a Jira project.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.agents.state import initial_state  # noqa: E402
from sowsprint.policy import (  # noqa: E402
    BlockCode,
    PublicationMode,
    Snapshot,
    evaluate_publication,
)
from sowsprint.tools.executor import IntegrationReport, ToolExecutor  # noqa: E402
from sowsprint.tools.planner import ToolCallPlan  # noqa: E402
from sowsprint.tools.registry import ToolRegistry, build_default_registry  # noqa: E402


def _snapshot() -> Snapshot:
    return Snapshot.build(
        sow={"title": "SOW", "clauses": [{"number": "1", "heading": "Fees", "body": "x"}]},
        backlog={"milestones": []},
        plan={"calls": [{"tool": "create_jira_project_workspace", "arguments": {}}]},
        destinations=["jira"],
        version=1,
    )


class TestThePolicyDecides:
    """The decision itself, away from the graph, so its rules are readable on their own."""

    def test_dry_run_is_never_blocked(self) -> None:
        """Dry-run sends nothing, so there is nothing to refuse.

        Blocking it would remove the only operation that is safe by construction — an operator
        validating a plan before asking for live.
        """
        decision = evaluate_publication(mode=PublicationMode.DRY_RUN, audit_passed=False)
        assert decision.allowed

    def test_live_requires_a_passed_audit(self) -> None:
        for value, code in (
            (None, BlockCode.AUDIT_MISSING),
            (False, BlockCode.AUDIT_FAILED),
        ):
            decision = evaluate_publication(mode="live", audit_passed=value, approved=True)
            assert decision.blocked
            assert decision.code is code

    def test_live_requires_approval(self) -> None:
        decision = evaluate_publication(mode="live", audit_passed=True, approved=False)
        assert decision.blocked
        assert decision.code is BlockCode.NOT_APPROVED

    def test_a_degraded_audit_blocks_even_when_it_says_passed(self) -> None:
        """A fallback judge answering "passed" is not a verdict."""
        decision = evaluate_publication(
            mode="live", audit_passed=True, audit_degraded=True, approved=True
        )
        assert decision.blocked
        assert decision.code is BlockCode.AUDIT_DEGRADED

    def test_approval_is_bound_to_the_snapshot_it_covered(self) -> None:
        current = _snapshot()
        stale = evaluate_publication(
            mode="live",
            audit_passed=True,
            approved=True,
            approved_snapshot="0" * 64,
            current_snapshot=current,
        )
        assert stale.blocked
        assert stale.code is BlockCode.VERSION_STALE

        fresh = evaluate_publication(
            mode="live",
            audit_passed=True,
            approved=True,
            approved_snapshot=current.digest,
            current_snapshot=current,
        )
        assert fresh.allowed

    def test_an_already_provisioned_run_is_not_provisioned_twice(self) -> None:
        decision = evaluate_publication(
            mode="live", audit_passed=True, approved=True, completed=True
        )
        assert decision.blocked
        assert decision.code is BlockCode.ALREADY_COMPLETED


class TestTheSnapshotIdentifiesContent:
    """R1.2: the hash covers what was written, and only what was written."""

    def test_a_render_does_not_break_an_approval(self) -> None:
        """A timestamp changes on every export; the contract does not.

        Including one would make every approval stale the moment it was granted, which would
        push people to approve blindly — the opposite of what the snapshot is for.
        """
        first = Snapshot.build(
            sow={"title": "SOW", "generated_at": "2026-01-01"},
            backlog={},
            plan={},
            destinations=["jira"],
            version=1,
        )
        second = Snapshot.build(
            sow={"title": "SOW", "generated_at": "2026-06-01"},
            backlog={},
            plan={},
            destinations=["jira"],
            version=1,
        )
        assert first.digest == second.digest

    def test_an_edit_breaks_it(self) -> None:
        base = Snapshot.build(sow={"title": "SOW"}, backlog={}, plan={}, destinations=[], version=1)
        edited = Snapshot.build(
            sow={"title": "SOW v2"}, backlog={}, plan={}, destinations=[], version=2
        )
        assert base.digest != edited.digest

    def test_destinations_are_part_of_the_snapshot(self) -> None:
        """A plan that grew a Notion call was not approved as a Jira-only plan."""
        jira_only = Snapshot.build(sow={}, backlog={}, plan={}, destinations=["jira"], version=1)
        both = Snapshot.build(
            sow={}, backlog={}, plan={}, destinations=["jira", "notion"], version=1
        )
        assert jira_only.digest != both.digest


class _RecordingRegistry(ToolRegistry):
    """A registry that counts writes, so "0 operations" is measured rather than inferred."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def execute(self, name: str, arguments: dict) -> object:  # type: ignore[override]
        self.calls.append(name)
        raise AssertionError(f"a write was attempted through a blocked executor: {name}")


class TestTheExecutorRefuses:
    """The guard sits where every write passes, so it cannot be routed around."""

    def test_a_live_batch_without_a_decision_writes_nothing(self) -> None:
        registry = _RecordingRegistry()
        executor = ToolExecutor(registry, dry_run=False)
        plan = ToolCallPlan.model_validate(
            {"summary": "", "calls": [{"tool": "create_jira_project_workspace", "arguments": {}}]}
        )
        report = executor.execute(plan)  # no decision supplied at all

        assert registry.calls == []
        assert report.refused
        assert report.refused_code == BlockCode.AUDIT_MISSING.value
        assert report.created == []

    def test_a_blocked_decision_writes_nothing(self) -> None:
        registry = _RecordingRegistry()
        executor = ToolExecutor(registry, dry_run=False)
        blocked = evaluate_publication(mode="live", audit_passed=False, approved=True)
        plan = ToolCallPlan.model_validate(
            {"summary": "", "calls": [{"tool": "create_jira_project_workspace", "arguments": {}}]}
        )
        report = executor.execute(plan, decision=blocked)

        assert registry.calls == []
        assert report.refused is True
        assert report.refused_code == BlockCode.AUDIT_FAILED.value

    def test_dry_run_still_validates(self) -> None:
        """The guard must not turn validation off — that is the whole shipping default."""
        registry = build_default_registry()
        executor = ToolExecutor(registry, dry_run=True)
        plan = ToolCallPlan.model_validate(
            {"summary": "", "calls": [{"tool": "create_notion_page", "arguments": {"title": "x"}}]}
        )
        report = executor.execute(plan)
        # Dry-run is allowed to attempt; the adapter simulates it. Nothing is refused.
        assert report.refused is False


class TestTheRunCarriesItsMode:
    def test_the_entry_state_records_the_mode(self) -> None:
        """Resolved once, not re-read at each decision: a run whose mode can change underneath
        it is a run where an approval means something different by the time it is used."""
        state = initial_state(
            session_id="s", run_id="r", raw_requirement="x", publication_mode="live"
        )
        assert state["publication_mode"] == "live"
        assert state["approved_snapshot"] == ""
        assert state["publication"] == {}

    def test_the_default_is_dry_run(self) -> None:
        state = initial_state(session_id="s", run_id="r", raw_requirement="x")
        assert state["publication_mode"] == "dry_run"


@pytest.mark.parametrize(
    "code",
    [
        BlockCode.AUDIT_FAILED,
        BlockCode.AUDIT_MISSING,
        BlockCode.AUDIT_DEGRADED,
        BlockCode.VERSION_STALE,
        BlockCode.NOT_APPROVED,
        BlockCode.LIVE_DISABLED,
        BlockCode.ALREADY_COMPLETED,
    ],
)
def test_every_block_code_explains_itself(code: BlockCode) -> None:
    """The interface prints the reason verbatim, so each one has to be an instruction."""
    from sowsprint.policy import REASONS

    text = REASONS[code]
    assert len(text) > 40, f"{code} has no useful explanation"
    assert text[0].isupper()
    assert text.rstrip().endswith(".")


def test_a_refused_report_is_not_a_failed_one() -> None:
    """A block and a failure send a reader to different places.

    "The Notion call failed" means look at Notion. "Blocked: the audit failed" means look at
    the contract. Conflating them is how the earlier behaviour hid itself.
    """
    report = IntegrationReport()
    assert report.refused is False
    report.refused_code = "audit_failed"
    report.refused_reason = "the audit failed"
    assert report.refused is True
