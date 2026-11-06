"""Release 2.1 addendum 01 — the two blockers, as regression tests.

The addendum's acceptance for both is the same sentence: *tesты на пути от пользовательского
решения до исполнителя* — from the person's decision to the executor, not around it.

Each test therefore asserts what actually reached the connector, because "the run reported an
error" and "nothing was written" are different claims and only the second one matters.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.agents import ScopingSession  # noqa: E402
from sowsprint.config import Settings  # noqa: E402
from sowsprint.policy import evaluate_publication  # noqa: E402
from sowsprint.rag.pipeline import get_pipeline  # noqa: E402
from sowsprint.storage.store import RunStore  # noqa: E402
from sowsprint.telemetry import drop_ledger  # noqa: E402
from sowsprint.tools.executor import ToolExecutor  # noqa: E402
from sowsprint.tools.journal import Journal, operation_key  # noqa: E402
from sowsprint.tools.planner import ToolCallPlan  # noqa: E402
from sowsprint.tools.registry import ToolRegistry  # noqa: E402
from sowsprint.tools.types import ToolExecutionResult  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402


class CountingRegistry(ToolRegistry):
    """Counts what reached the connector. The only number that settles T05."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def execute(self, name: str, arguments: dict) -> ToolExecutionResult:  # type: ignore[override]
        self.calls.append(name)
        key = "EUSTORE" if "project" in name else "EUS-1"
        return ToolExecutionResult(
            tool=name,
            ok=True,
            dry_run=False,
            request=arguments,
            resource_key=key,
            resource_url=f"https://jira/{key}",
        )


class FailingNotionRegistry(CountingRegistry):
    """Jira succeeds; Notion answers 403 the first time and succeeds afterwards."""

    def __init__(self) -> None:
        super().__init__()
        self.notion_attempts = 0

    def execute(self, name: str, arguments: dict) -> ToolExecutionResult:  # type: ignore[override]
        if name == "create_notion_page":
            self.notion_attempts += 1
            if self.notion_attempts == 1:
                self.calls.append(name)
                return ToolExecutionResult(
                    tool=name,
                    ok=False,
                    dry_run=False,
                    request=arguments,
                    error="403 Forbidden",
                    error_code="403",
                )
        return super().execute(name, arguments)


def _plan() -> ToolCallPlan:
    return ToolCallPlan.model_validate(
        {
            "summary": "project then page",
            "calls": [
                {
                    "tool": "create_jira_project_workspace",
                    "arguments": {"project_key": "EUSTORE"},
                },
                {"tool": "create_notion_page", "arguments": {"title": "Summary"}},
            ],
        }
    )


class TestBlockerR3TheReplaySkipsCompletedWork:
    """Addendum 01: "исполнитель повторяет успешную операцию" — the released defect."""

    def test_a_replay_calls_only_the_failed_destination(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        allow = evaluate_publication(mode="live", audit_passed=True, approved=True)

        first = FailingNotionRegistry()
        ToolExecutor(first, dry_run=False, run_id="run-1", store=store).execute(
            _plan(), decision=allow
        )
        assert "create_jira_project_workspace" in first.calls
        assert "create_notion_page" in first.calls

        # A new executor stands in for the restarted process. The Jira project is already in the
        # journal and must not be created again.
        second = CountingRegistry()
        report = ToolExecutor(second, dry_run=False, run_id="run-1", store=store).execute(
            _plan(), decision=allow
        )

        assert "create_jira_project_workspace" not in second.calls
        assert "create_notion_page" in second.calls
        assert sum(1 for r in report.results if r.error_code == "already_done") == 1

    def test_the_saved_identifier_is_reused_for_dependents(self, tmp_path: Path) -> None:
        """Skipping is not enough: later calls must resolve against the saved key."""
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        allow = evaluate_publication(mode="live", audit_passed=True, approved=True)
        plan = ToolCallPlan.model_validate(
            {
                "summary": "epic then story",
                "calls": [
                    {
                        "tool": "create_jira_issue",
                        "arguments": {"project_key": "EUSTORE", "summary": "Epic", "issue_type": "Epic"},
                    },
                    {
                        "tool": "create_jira_issue",
                        "arguments": {
                            "project_key": "EUSTORE",
                            "summary": "Story",
                            "issue_type": "Story",
                            "parent_key": "Epic",
                        },
                    },
                ],
            }
        )
        first = CountingRegistry()
        ToolExecutor(first, dry_run=False, run_id="run-1", store=store).execute(
            plan, decision=allow
        )

        second = CountingRegistry()
        report = ToolExecutor(second, dry_run=False, run_id="run-1", store=store).execute(
            plan, decision=allow
        )

        assert second.calls == []  # both already done
        assert all(r.ok for r in report.results)

    def test_the_journal_answers_a_restarted_process(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        key = operation_key("run-1", "create_jira_project_workspace", {}, 1)
        first = Journal("run-1", store=store)
        first.succeeded(
            first.claim(key, "create_jira_project_workspace", arguments={}, ordinal=1),
            external_id="EUSTORE",
            external_url="https://jira/EUSTORE",
        )

        # Empty in-memory state, populated table — the situation after a restart.
        restored = Journal("run-1", store=store).stored_step(key)
        assert restored is not None
        assert restored.external_id == "EUSTORE"


class TestBlockerR1TheDecisionIsRecordedBeforeWriting:
    """Addendum 01: "В прикладной БД решение не появилось" and the mode drift."""

    @pytest.fixture
    def settings(self, tmp_path: Path) -> Settings:
        return Settings(
            llm_provider="mock",
            critic_provider="mock",
            vector_backend="memory",
            embedding_provider="hash",
            rerank_provider="heuristic",
            whisper_provider="offline",
            dry_run_integrations=True,
            session_budget_usd=2.50,
            auth_enabled=False,
            enable_cloud_fallback=False,
            state_store_url=f"sqlite:///{tmp_path / 'runs.db'}",
            checkpoint_url=f"sqlite:///{tmp_path / 'cp.db'}",
        )

    @pytest.fixture
    def pipeline(self, settings: Settings):
        pipe = get_pipeline(settings)
        pipe.ensure_indexed()
        drop_ledger("blockers")
        drop_ledger("alice")
        return pipe

    def test_the_decision_exists_before_the_graph_moves(self, settings, pipeline) -> None:
        """The record has to exist when the first external call is made, not afterwards."""
        session = ScopingSession("alice", settings=settings, pipeline=pipeline, owner_id="alice")
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")

        result = session.resume_approval(True, actor_id="bob", actor_role="approver")

        # The version is data. A repair loop can leave the audit passing on its second pass, so
        # the decision belongs to version 2 — reading version 1 finds nothing and says nothing
        # useful about why.
        version = int((result.publication or {}).get("version") or 1)
        decision = settings.run_store.get_decision(session.run_id, version)
        assert decision is not None, (
            f"no decision recorded; publication={result.publication!r}, status={result.status!r}"
        )
        assert decision.decision == "approve"
        assert decision.actor_id == "bob"
        assert decision.snapshot_hash, "the decision must name the snapshot it covered"
        assert decision.snapshot_hash == result.publication["snapshot"]

    def test_a_mode_change_between_opening_and_approving_blocks_the_write(
        self, settings, pipeline
    ) -> None:
        """Opened as dry-run, approved after the deployment switched to live."""
        session = ScopingSession("alice", settings=settings, pipeline=pipeline, owner_id="alice")
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")

        # The deployment flips to live while the run is still waiting.
        object.__setattr__(session.settings, "dry_run_integrations", False)

        result = session.resume_approval(True, actor_id="bob", actor_role="approver")

        assert result.publication["allowed"] is False
        assert result.publication["code"] == "mode_changed"
        assert result.status == "awaiting_approval"

    def test_the_blocked_decision_records_nothing(self, settings, pipeline) -> None:
        """A refused approval must not leave a decision that authorises anything."""
        session = ScopingSession("alice", settings=settings, pipeline=pipeline, owner_id="alice")
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")
        object.__setattr__(session.settings, "dry_run_integrations", False)

        session.resume_approval(True, actor_id="bob", actor_role="approver")

        # Nothing was approved, so nothing may be recorded — at any version.
        assert settings.run_store.get_decision(session.run_id, 1) is None
        assert settings.run_store.get_decision(session.run_id, 2) is None

    def test_a_stale_button_cannot_approve_a_changed_version(self, settings, pipeline) -> None:
        """After an edit the snapshot moves, so the earlier approval no longer describes it."""
        from sowsprint.policy import Snapshot

        session = ScopingSession("alice", settings=settings, pipeline=pipeline, owner_id="alice")
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")

        before = Snapshot.build(
            sow={"title": "A"}, backlog={}, plan={}, destinations=["jira"], version=1
        )
        after = Snapshot.build(
            sow={"title": "B"}, backlog={}, plan={}, destinations=["jira"], version=1
        )
        decision = evaluate_publication(
            mode="live",
            audit_passed=True,
            approved=True,
            approved_snapshot=before.digest,
            current_snapshot=after,
        )

        assert decision.blocked
        assert decision.code.value == "version_stale"


@pytest.mark.parametrize("dry_run", [True, False])
def test_the_executor_still_refuses_without_a_decision(dry_run: bool) -> None:
    """Defence in depth: the policy check in the executor is unchanged by either fix."""
    registry = CountingRegistry()
    executor = ToolExecutor(registry, dry_run=dry_run)
    if dry_run:
        executor.execute(_plan())  # dry-run validates and sends nothing
        return

    report = executor.execute(_plan())
    assert registry.calls == []
    assert report.refused


class TestTheResultIsSplitByDestination:
    """R5 / addendum 01: "Показывать результаты отдельно для Jira и Notion"."""

    def test_a_notion_failure_does_not_hide_behind_a_combined_row(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        allow = evaluate_publication(mode="live", audit_passed=True, approved=True)

        registry = FailingNotionRegistry()
        report = ToolExecutor(registry, dry_run=False, run_id="run-1", store=store).execute(
            _plan(), decision=allow
        )
        breakdown = report.journal.per_destination()

        assert breakdown["jira"]["created"] == 1
        assert breakdown["jira"]["failed"] == 0
        assert breakdown["notion"]["created"] == 0
        assert breakdown["notion"]["failed"] == 1

    def test_the_next_action_names_the_retry(self, tmp_path: Path) -> None:
        """A failed run has to tell the reader what to do, not only what happened."""
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        allow = evaluate_publication(mode="live", audit_passed=True, approved=True)
        registry = FailingNotionRegistry()
        report = ToolExecutor(registry, dry_run=False, run_id="run-1", store=store).execute(
            _plan(), decision=allow
        )

        action = report.journal.next_action(dry_run=False)
        assert "retry" in action.lower()
        assert "skipped" in action.lower()

    def test_an_uncertain_operation_is_not_offered_for_retry(self, tmp_path: Path) -> None:
        """A timeout may or may not have taken effect; retrying it could duplicate."""
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        journal = Journal("run-1", store=store)
        step = journal.claim("k1", "create_jira_project", arguments={}, ordinal=1)
        journal.uncertain(step, error_code="timeout")

        action = journal.next_action(dry_run=False)
        assert "reconcile" in action.lower()
        assert "could duplicate" in action.lower()

    def test_a_dry_run_says_nothing_was_sent(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        journal = Journal("run-1", store=store)
        journal.succeeded(
            journal.claim("k1", "create_jira_project", arguments={}, ordinal=1),
            external_id="EUSTORE",
        )
        assert "Nothing was sent" in journal.next_action(dry_run=True)
