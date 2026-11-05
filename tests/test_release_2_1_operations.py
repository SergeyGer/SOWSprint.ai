"""Release 2.1, R3 — a retry that cannot duplicate and a result that can be read.

The two failures behind this package, in the words of the acceptance matrix:

*T04* — two requests at once, a timeout after the destination has already created the object,
then a restart and a retry. Expected: one batch, the successful objects kept, the uncertain ones
reconciled, **zero duplicates**.

*T05* — Jira wrote its objects, Notion returned a confirmed refusal, the permissions are fixed and
the operation is repeated. Expected: the Jira success and the Notion error are both visible, the
retry adds only the Notion page.

Both reduce to one property: an operation has a stable identity, and the system knows what it has
already done with that identity.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.storage.store import OperationStatus, RunStore  # noqa: E402
from sowsprint.tools.journal import (  # noqa: E402
    Journal,
    Outcome,
    marker_for,
    operation_key,
)


class TestTheKey:
    """R3.1: schema accepts a unique key, the constraint rejects duplicates."""

    def test_the_same_call_produces_the_same_key(self) -> None:
        first = operation_key("run-1", "create_jira_issue", {"summary": "x"}, 1)
        second = operation_key("run-1", "create_jira_issue", {"summary": "x"}, 1)
        assert first == second

    def test_a_different_position_is_a_different_operation(self) -> None:
        """Ten stories with identical arguments are ten operations, not one."""
        first = operation_key("run-1", "create_jira_issue", {"summary": "x"}, 1)
        second = operation_key("run-1", "create_jira_issue", {"summary": "x"}, 2)
        assert first != second

    def test_a_different_run_never_collides(self) -> None:
        first = operation_key("run-1", "create_jira_issue", {"summary": "x"}, 1)
        second = operation_key("run-2", "create_jira_issue", {"summary": "x"}, 1)
        assert first != second

    def test_the_marker_is_stable_and_run_scoped(self) -> None:
        key = operation_key("run-abc", "t", {}, 1)
        assert marker_for("run-abc", key) == marker_for("run-abc", key)
        assert marker_for("run-abc", key).startswith("sowsprint-")
        # A different run must not reuse the tag, or the second run would find the first
        # run's objects and skip creating its own.
        assert marker_for("run-xyz", key) != marker_for("run-abc", key)


class TestTheJournalRecords:
    def test_a_claim_precedes_the_call(self) -> None:
        """If the process dies between the claim and the call, the journal must say so.

        Claiming afterwards would record that nothing was attempted, and the retry would
        duplicate — which is the exact failure R3 exists to prevent.
        """
        journal = Journal("run-1")
        step = journal.claim("k1", "create_jira_issue", arguments={}, ordinal=1)
        assert step.attempts == 1
        assert journal.outcome_of("k1") == Outcome.SKIPPED

    def test_a_timeout_is_uncertain_not_failed(self) -> None:
        journal = Journal("run-1")
        step = journal.claim("k1", "create_jira_issue", arguments={}, ordinal=1)
        journal.uncertain(step, error_code="timeout")

        assert step.outcome is Outcome.UNCERTAIN
        assert journal.summary().needs_reconciliation is True

    def test_a_confirmed_refusal_is_failed(self) -> None:
        journal = Journal("run-1")
        step = journal.claim("k1", "create_notion_page", arguments={}, ordinal=1)
        journal.failed(step, error_code="403")

        assert step.outcome is Outcome.FAILED
        assert journal.summary().needs_reconciliation is False

    def test_an_object_that_already_existed_is_success(self) -> None:
        """The destination has it. The object exists and dependents can proceed.

        Recording it as a failure would strand everything downstream and send the operator
        looking for an error that did not happen.
        """
        journal = Journal("run-1")
        step = journal.claim("k1", "create_jira_project", arguments={}, ordinal=1)
        journal.already_present(step, external_id="EUSTORE")

        summary = journal.summary()
        assert summary.created_count == 0
        assert len(summary.existing) == 1
        assert summary.existing[0].external_id == "EUSTORE"


class TestDependencies:
    """R3.3: a dependent call whose predecessor did not succeed is not attempted."""

    def test_a_blocked_call_records_which_object_is_missing(self) -> None:
        journal = Journal("run-1")
        step = journal.claim("k2", "create_jira_issue", arguments={}, ordinal=2)
        journal.blocked(step, blocker="parent_key=Onboarding epic")

        assert step.outcome is Outcome.SKIPPED
        assert "Onboarding epic" in step.blocker
        assert journal.summary().created_count == 0

    def test_the_executor_skips_a_story_whose_epic_was_never_created(self) -> None:
        from sowsprint.tools.executor import ToolExecutor
        from sowsprint.tools.planner import ToolCallPlan
        from sowsprint.tools.registry import build_default_registry

        plan = ToolCallPlan.model_validate(
            {
                "summary": "story without an epic",
                "calls": [
                    {
                        "tool": "create_jira_issue",
                        "arguments": {
                            "project_key": "AURORA",
                            "summary": "Orphan story",
                            "issue_type": "Story",
                            "parent_key": "An epic nobody made",
                        },
                    }
                ],
            }
        )
        report = ToolExecutor(build_default_registry(), run_id="run-1").execute(plan)

        assert report.created_count == 0
        assert report.results[0].error_code == OperationStatus.SKIPPED.value
        assert "An epic nobody made" in (report.results[0].error or "")


class TestTheSummary:
    """R3.4: the counts a reader can act on."""

    def test_it_separates_created_from_existing_from_unknown(self) -> None:
        journal = Journal("run-1")
        created = journal.claim("k1", "create_jira_project", arguments={}, ordinal=1)
        journal.succeeded(created, external_url="https://jira/EUSTORE")
        existing = journal.claim("k2", "create_jira_issue", arguments={}, ordinal=2)
        journal.already_present(existing, external_id="EUS-1")
        unknown = journal.claim("k3", "create_notion_page", arguments={}, ordinal=3)
        journal.uncertain(unknown)

        summary = journal.summary("jira+notion")
        payload = summary.as_dict()

        assert payload["created"] == 1
        assert payload["existing"] == 1
        assert payload["uncertain"] == 1
        assert payload["needs_reconciliation"] is True
        assert payload["links"] == ["https://jira/EUSTORE"]

    def test_a_clean_run_needs_no_reconciliation(self) -> None:
        journal = Journal("run-1")
        step = journal.claim("k1", "create_jira_project", arguments={}, ordinal=1)
        journal.succeeded(step, external_id="EUSTORE")

        summary = journal.summary("jira")
        assert summary.needs_reconciliation is False
        assert summary.created_count == 1


class TestTheJournalPersists:
    """A journal that lives only in memory cannot answer "did I already do this" after a restart."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> RunStore:
        return RunStore.sqlite(str(tmp_path / "runs.db"))

    def test_a_completed_operation_is_recognised_on_a_later_attempt(
        self, store: RunStore
    ) -> None:
        first = Journal("run-1", store=store)
        step = first.claim("k1", "create_jira_project", arguments={}, ordinal=1)
        first.succeeded(step, external_id="EUSTORE")

        # A new journal stands in for a restarted process reading the same database.
        second = Journal("run-1", store=store)
        assert second.outcome_of("k1") is Outcome.EXISTING

    def test_the_recorded_status_is_the_real_one(self, store: RunStore) -> None:
        journal = Journal("run-1", store=store)
        step = journal.claim("k1", "create_notion_page", arguments={}, ordinal=1)
        journal.uncertain(step)

        stored = store.list_operations("run-1")
        assert len(stored) == 1
        assert stored[0].status == OperationStatus.UNCERTAIN.value
        assert stored[0].error_code == "timeout"

    def test_operations_from_different_runs_do_not_mix(self, store: RunStore) -> None:
        for run in ("run-1", "run-2"):
            journal = Journal(run, store=store)
            step = journal.claim("k1", "create_jira_project", arguments={}, ordinal=1)
            journal.succeeded(step, external_id=run)

        assert store.list_operations("run-1")[0].external_id == "run-1"
        assert store.list_operations("run-2")[0].external_id == "run-2"


class TestARetryAddsOnlyWhatIsMissing:
    """The T05 scenario, expressed directly.

    Jira succeeded, Notion refused, permissions were fixed and the batch was replayed. The
    replay must not create a second Jira project.
    """

    def test_a_second_pass_recognises_the_first_pass_success(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        key_project = operation_key("run-1", "create_jira_project", {"project_key": "X"}, 1)
        key_page = operation_key("run-1", "create_notion_page", {"title": "T"}, 2)

        first = Journal("run-1", store=store)
        project = first.claim(key_project, "create_jira_project", arguments={}, ordinal=1)
        first.succeeded(project, external_id="EUSTORE")
        page = first.claim(key_page, "create_notion_page", arguments={}, ordinal=2)
        first.failed(page, error_code="403")

        second = Journal("run-1", store=store)
        assert second.outcome_of(key_project) is Outcome.EXISTING
        assert second.outcome_of(key_page) is None  # a refusal is retryable

        assert store.list_operations("run-1")[0].status == OperationStatus.SUCCEEDED.value
        assert store.list_operations("run-1")[1].status == OperationStatus.FAILED.value


class TestInjectedSettingsReachTheConnectors:
    """The defect found while verifying R3, and the reason it was possible.

    `build_default_registry()` read the process-wide settings instead of the ones it was given,
    so a caller that configured dry-run still got live connectors. A verification run written to
    prove that nothing is written to Jira wrote nineteen objects into Jira.

    These assert the wiring, not the credentials: a registry built for dry-run settings must
    report simulated connectors whatever the environment says.
    """

    def test_a_dry_run_registry_is_simulated_even_when_the_environment_is_live(self) -> None:
        from sowsprint.config import Settings
        from sowsprint.tools.registry import build_default_registry

        settings = Settings(
            _env_file=None,
            dry_run_integrations=True,
            jira_api_token="not-a-real-token",
            notion_api_key="not-a-real-key",
            jira_email="nobody@example.invalid",
        )
        registry = build_default_registry(settings)
        result = registry.execute(
            "create_notion_page", {"title": "should be simulated", "content": "x"}
        )

        assert result.dry_run is True

    def test_the_registry_does_not_fall_back_to_the_global_settings(self) -> None:
        """Passing settings must be enough. Falling back silently is how the bug hid."""
        import inspect

        from sowsprint.tools.registry import build_default_registry

        signature = inspect.signature(build_default_registry)
        assert "settings" in signature.parameters
        assert signature.parameters["settings"].default is None

    def test_the_session_registry_honours_its_settings_too(self) -> None:
        """The second half of the same leak.

        `ScopingSession` built a registry before the nodes existed. Fixing `AgentNodes` alone
        left this path open, and the first verification after that fix still wrote a project
        into Jira — which is why this test exists rather than a note.
        """
        from sowsprint.agents import ScopingSession
        from sowsprint.config import Settings

        settings = Settings(
            _env_file=None,
            dry_run_integrations=True,
            jira_api_token="not-a-real-token",
            notion_api_key="not-a-real-key",
            jira_email="nobody@example.invalid",
            state_store_url="sqlite:///:memory:",
            checkpoint_url="memory://",
        )
        session = ScopingSession("probe", settings=settings)
        result = session.registry.execute(
            "create_notion_page", {"title": "simulated", "content": "x"}
        )
        assert result.dry_run is True
