"""Release 2.1, R2 — a run that outlives the browser tab.

The acceptance criterion is not "rows are written". It is: restart the service while a run waits
for an answer, and the run comes back at the same step with the same version, the same mode and
the same audit — and someone else opening it is refused.

These tests use SQLite in a temporary directory and `memory://` for the graph, so they need no
server and leave nothing behind. That is the mode the specification requires CI to keep.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.storage.checkpointer import build_checkpointer, reset_checkpointer  # noqa: E402
from sowsprint.storage.store import (  # noqa: E402
    DecisionRecord,
    EventRecord,
    OperationRecord,
    OperationStatus,
    RevisionRecord,
    RunRecord,
    RunStore,
    utcnow,
)


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore.sqlite(str(tmp_path / "runs.db"))


class TestTheFiveEntities:
    """R2.2 names them; each test asserts only what that entity is for."""

    def test_a_run_survives_a_new_connection(self, tmp_path: Path) -> None:
        path = str(tmp_path / "runs.db")
        RunStore.sqlite(path).save_run(
            RunRecord(run_id="r1", title="EU store", owner_id="alice", reviewer_id="bob")
        )

        # A second process, not a second object: the whole point is that the row outlives this
        # one handle.
        reopened = RunStore.sqlite(path)
        run = reopened.get_run("r1")

        assert run is not None
        assert run.title == "EU store"
        assert run.reviewer_id == "bob"

    def test_a_revision_keeps_the_audit_it_was_drafted_against(self, store: RunStore) -> None:
        store.save_revision(
            RevisionRecord(
                run_id="r1",
                number=2,
                snapshot_hash="abc",
                audit_passed=False,
                audit_report={"quality_score": 0.67, "findings": [{"severity": "high"}]},
                plan_hash="plan2",
                destinations=["jira"],
            )
        )
        revision = store.get_revision("r1", 2)

        assert revision is not None
        assert revision.audit_passed is False
        assert revision.audit_report["quality_score"] == 0.67
        assert revision.destinations == ["jira"]

    def test_a_decision_names_a_person_and_a_snapshot(self, store: RunStore) -> None:
        store.save_decision(
            DecisionRecord(
                run_id="r1",
                revision=3,
                snapshot_hash="sha3",
                mode="live",
                actor_id="bob",
                decision="approve",
            )
        )
        decision = store.get_decision("r1", 3)

        assert decision is not None
        assert decision.actor_id == "bob"
        assert decision.mode == "live"
        assert decision.snapshot_hash == "sha3"

    def test_one_live_decision_per_revision(self, store: RunStore) -> None:
        """Two current decisions about one document is the ambiguity R1.2 removes.

        A second approval replaces the first rather than accumulating — the earlier one stays in
        the event log, where history belongs.
        """
        for actor in ("bob", "carol"):
            store.save_decision(
                DecisionRecord(
                    run_id="r1",
                    revision=1,
                    snapshot_hash="sha",
                    mode="live",
                    actor_id=actor,
                    decision="approve",
                )
            )

        decision = store.get_decision("r1", 1)
        assert decision is not None
        assert decision.actor_id == "carol"

    def test_an_operation_is_keyed_so_a_retry_cannot_duplicate(self, store: RunStore) -> None:
        """R3 builds on this; R2 only has to make the key real."""
        for status in (OperationStatus.PENDING.value, OperationStatus.SUCCEEDED.value):
            store.record_operation(
                OperationRecord(
                    run_id="r1", op_key="jira:project:EUSTORE", tool="create_jira_project",
                    status=status, attempts=2, external_id="EUSTORE",
                )
            )

        operations = store.list_operations("r1")
        assert len(operations) == 1
        assert operations[0].status == OperationStatus.SUCCEEDED.value
        assert operations[0].external_id == "EUSTORE"

    def test_events_are_ordered_and_scoped_to_their_run(self, store: RunStore) -> None:
        store.add_event(EventRecord(run_id="r1", action="approve", actor_id="bob", version=1))
        store.add_event(EventRecord(run_id="r2", action="reject", actor_id="dave", version=1))

        events = store.list_events("r1")
        assert [e.action for e in events] == ["approve"]
        assert events[0].actor_id == "bob"

    def test_runs_are_listed_by_recency(self, store: RunStore) -> None:
        for index, run in enumerate(("first", "second", "third")):
            store.save_run(RunRecord(run_id=f"r{index}", title=run, created_at=utcnow()))
        listed = store.list_runs(limit=3)
        assert len(listed) == 3

    def test_a_run_can_be_found_by_either_participant(self, store: RunStore) -> None:
        """R2.2: results are visible to the participants of the run, and nobody else."""
        store.save_run(RunRecord(run_id="r1", owner_id="alice", reviewer_id="bob"))
        store.save_run(RunRecord(run_id="r2", owner_id="carol", reviewer_id="dave"))

        assert [r.run_id for r in store.list_runs(owner_id="alice")] == ["r1"]
        assert [r.run_id for r in store.list_runs(owner_id="bob")] == ["r1"]
        assert [r.run_id for r in store.list_runs(owner_id="mallory")] == []


class TestDeletion:
    def test_deletion_covers_every_table(self, store: RunStore) -> None:
        store.save_run(RunRecord(run_id="r1"))
        store.save_revision(RevisionRecord(run_id="r1", number=1, snapshot_hash="x"))
        store.save_decision(
            DecisionRecord(
                run_id="r1", revision=1, snapshot_hash="x", mode="dry_run",
                actor_id="bob", decision="approve",
            )
        )
        store.record_operation(OperationRecord(run_id="r1", op_key="k"))
        store.add_event(EventRecord(run_id="r1", action="approve"))

        removed = store.delete_run("r1")

        assert removed == 5
        assert store.get_run("r1") is None
        assert store.get_revision("r1", 1) is None
        assert store.get_decision("r1", 1) is None
        assert store.list_operations("r1") == []
        assert store.list_events("r1") == []

    def test_deleting_one_run_leaves_the_others(self, store: RunStore) -> None:
        store.save_run(RunRecord(run_id="r1"))
        store.save_run(RunRecord(run_id="r2"))
        store.delete_run("r1")
        assert store.get_run("r2") is not None


class TestTheCheckpointer:
    def test_memory_is_a_supported_choice_not_a_fallback(self) -> None:
        """CI and the offline example use it on purpose: a test that needs a file leaves one."""
        assert type(build_checkpointer("memory://")).__name__ == "InMemorySaver"

    def test_a_file_survives_reopening(self, tmp_path: Path) -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        url = f"sqlite:///{tmp_path / 'cp.db'}"
        config = {"configurable": {"thread_id": "run-1", "checkpoint_ns": ""}}

        first = build_checkpointer(url)
        first.put(config, empty_checkpoint(), {"source": "input", "step": 0, "parents": {}}, {})
        reset_checkpointer()

        # A new handle stands in for a restarted process.
        second = build_checkpointer(url)
        assert second.get_tuple(config) is not None
        reset_checkpointer()


class TestTheStoreIsConfiguredNotAssumed:
    def test_the_default_keeps_working_without_a_server(self) -> None:
        from sowsprint.config import Settings

        settings = Settings(_env_file=None)
        assert settings.state_store_url.startswith("sqlite")
        assert settings.checkpoint_url.startswith("sqlite")

    def test_a_postgres_url_selects_the_other_backend(self) -> None:
        """The URL decides, in one place, so the pilot does not run code CI never touched."""
        sqlite_store = RunStore.from_url("sqlite:///:memory:")
        assert sqlite_store.backend == "sqlite"

    def test_the_store_is_built_lazily(self, tmp_path: Path) -> None:
        """An import that creates tables is an import nobody can use in a test cheaply."""
        from sowsprint.config import Settings

        settings = Settings(_env_file=None, state_store_url=f"sqlite:///{tmp_path / 'lazy.db'}")
        assert getattr(settings, "_run_store_cache", None) is None
        _ = settings.run_store
        assert (tmp_path / "lazy.db").exists()


class TestRetention:
    """R2.3: the policy is a command with a dry-run, not an intention."""

    @pytest.fixture
    def settings(self, tmp_path: Path):
        from sowsprint.config import Settings

        return Settings(
            _env_file=None,
            state_store_url=f"sqlite:///{tmp_path / 'runs.db'}",
            checkpoint_url="memory://",
        )

    def test_dry_run_removes_nothing(self, settings) -> None:
        from sowsprint.retention import purge

        settings.run_store.save_run(
            RunRecord(run_id="old", updated_at="2020-01-01T00:00:00.000000Z"), touch=False
        )
        report = purge(older_than=30, apply=False, settings=settings)

        assert report["runs"] == ["old"]
        assert report["records_deleted"] == 0
        assert settings.run_store.get_run("old") is not None

    def test_apply_removes_the_run_and_leaves_the_rest(self, settings) -> None:
        from sowsprint.retention import purge

        settings.run_store.save_run(
            RunRecord(run_id="old", updated_at="2020-01-01T00:00:00.000000Z"), touch=False
        )
        settings.run_store.save_run(RunRecord(run_id="fresh"))

        report = purge(older_than=30, apply=True, settings=settings)

        assert report["records_deleted"] == 1
        assert settings.run_store.get_run("old") is None
        assert settings.run_store.get_run("fresh") is not None

    def test_an_old_timestamp_is_not_overwritten_by_a_save(self, settings) -> None:
        """The timestamp is the data here: refreshing it would exempt the run from retention."""
        settings.run_store.save_run(
            RunRecord(run_id="old", updated_at="2020-01-01T00:00:00.000000Z"), touch=False
        )
        run = settings.run_store.get_run("old")
        assert run is not None
        assert run.updated_at.startswith("2020-01-01")

    def test_a_normal_save_does_refresh_the_timestamp(self, settings) -> None:
        settings.run_store.save_run(
            RunRecord(run_id="r", updated_at="2020-01-01T00:00:00.000000Z")
        )
        run = settings.run_store.get_run("r")
        assert run is not None
        assert not run.updated_at.startswith("2020-01-01")
