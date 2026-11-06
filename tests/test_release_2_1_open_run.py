"""Release 2.1, R2.1 — reopening a run by its id.

The identifier has to do two opposite things at once: be enough to resume the work, and not be
enough to see somebody else's. A link that opens for anyone holding it is not a shared run, it is
a public one.
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
from sowsprint.rag.pipeline import get_pipeline  # noqa: E402
from sowsprint.security.roles import DenialCode, can_view  # noqa: E402
from sowsprint.storage.store import RunRecord, RunStore  # noqa: E402
from sowsprint.telemetry import drop_ledger  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="mock",
        critic_provider="mock",
        vector_backend="memory",
        embedding_provider="hash",
        rerank_provider="heuristic",
        whisper_provider="offline",
        dry_run_integrations=True,
        auth_enabled=False,
        enable_cloud_fallback=False,
        state_store_url=f"sqlite:///{tmp_path / 'runs.db'}",
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.db'}",
    )


@pytest.fixture
def pipeline(settings: Settings):
    pipe = get_pipeline(settings)
    pipe.ensure_indexed()
    drop_ledger("open-run")
    return pipe


class TestTheIdIsEnoughToResume:
    """R2.1: after a reload the team returns by id, not by repeating the brief."""

    def test_a_saved_run_reopens_at_the_same_stage(self, settings, pipeline) -> None:
        session = ScopingSession(
            "alice", settings=settings, pipeline=pipeline, owner_id="alice"
        )
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        run_id = session.run_id
        session.appoint_reviewer("bob")

        # A second process: new session object, same id, nothing carried in memory.
        restored = ScopingSession.restore(
            run_id, settings=settings, session_id="alice-2", pipeline=pipeline
        )
        outcome = restored.snapshot()

        assert restored.run_id == run_id
        assert outcome.awaiting_approval
        assert outcome.stage == "approval"
        assert outcome.sow is not None

    def test_the_restored_run_can_be_decided(self, settings, pipeline) -> None:
        session = ScopingSession(
            "alice", settings=settings, pipeline=pipeline, owner_id="alice"
        )
        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")

        restored = ScopingSession.restore(
            session.run_id, settings=settings, session_id="reviewer-1", pipeline=pipeline
        )
        outcome = restored.resume_approval(True, actor_id="bob", actor_role="approver")

        assert outcome.status == "completed"

    def test_an_unknown_id_reads_as_empty_rather_than_raising(self, settings) -> None:
        """A link to a run that does not exist is a normal thing for a link to be."""
        restored = ScopingSession.restore(
            "run-does-not-exist", settings=settings, session_id="alice"
        )
        outcome = restored.snapshot()
        assert outcome.sow is None


class TestTheIdIsNotEnoughToBrowse:
    """R4.3: a run is visible to its participants."""

    def test_the_owner_and_the_reviewer_may_open_it(self) -> None:
        assert can_view(actor_id="alice", run_owner="alice", run_reviewer="bob").allowed
        assert can_view(actor_id="bob", run_owner="alice", run_reviewer="bob").allowed

    def test_a_stranger_may_not(self) -> None:
        verdict = can_view(actor_id="eve", run_owner="alice", run_reviewer="bob")
        assert not verdict.allowed
        assert verdict.code is DenialCode.NOT_A_PARTICIPANT

    def test_the_refusal_says_whom_to_ask(self) -> None:
        verdict = can_view(actor_id="eve", run_owner="alice", run_reviewer="bob")
        assert "reviewer" in verdict.reason

    def test_an_empty_actor_may_not(self) -> None:
        """An unauthenticated deployment must not open runs by id either."""
        assert not can_view(actor_id="", run_owner="alice", run_reviewer="bob").allowed

    def test_a_run_with_no_reviewer_is_still_its_owners(self) -> None:
        assert can_view(actor_id="alice", run_owner="alice").allowed
        assert not can_view(actor_id="eve", run_owner="alice").allowed


class TestTheListIsScopedToTheAccount:
    def test_only_participating_runs_are_listed(self, tmp_path: Path) -> None:
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        store.save_run(RunRecord(run_id="run-a", title="A", owner_id="alice", reviewer_id="bob"))
        store.save_run(RunRecord(run_id="run-b", title="B", owner_id="carol", reviewer_id="dave"))

        assert [r.run_id for r in store.list_runs(owner_id="alice")] == ["run-a"]
        assert [r.run_id for r in store.list_runs(owner_id="bob")] == ["run-a"]
        assert store.list_runs(owner_id="eve") == []

    def test_a_saved_run_carries_what_the_card_shows(self, tmp_path: Path) -> None:
        """The card states version, mode and time — so the record has to carry them."""
        store = RunStore.sqlite(str(tmp_path / "runs.db"))
        store.save_run(
            RunRecord(
                run_id="run-a",
                title="EU store",
                owner_id="alice",
                reviewer_id="bob",
                current_version=3,
                publication_mode="live",
            )
        )
        record = store.get_run("run-a")

        assert record is not None
        assert record.current_version == 3
        assert record.publication_mode == "live"
        assert record.updated_at
        assert record.reviewer_id == "bob"


class TestTheAccountModelMapsOntoTheRoles:
    """`passwd --role member` is what every existing deployment has written.

    Left unmapped, it fell through to the least privileged role: a normal account could draft a
    contract and then be told its role did not include approving one. Accurate, and not what
    "member" means.
    """

    def test_member_is_an_author(self) -> None:
        from sowsprint.security.roles import AppRole, role_from_metadata

        assert role_from_metadata({"role": "member"}) is AppRole.AUTHOR

    def test_reviewer_is_an_approver(self) -> None:
        from sowsprint.security.roles import AppRole, role_from_metadata

        assert role_from_metadata({"role": "reviewer"}) is AppRole.APPROVER

    def test_a_member_still_cannot_approve(self) -> None:
        from sowsprint.security.roles import DenialCode, can_approve

        verdict = can_approve(
            actor_id="bob", role="member", run_owner="alice", run_reviewer="bob"
        )
        assert not verdict.allowed
        assert verdict.code is DenialCode.ROLE_MAY_NOT_APPROVE


class TestTheTwoPersonFlow:
    """The whole point of the release, as one test.

    An author drafts; a reviewer — a different account, in a different session — finds the run
    by id, opens it, and approves it. Neither step is possible for the wrong person.
    """

    def test_an_author_drafts_and_a_reviewer_decides(self, settings, pipeline) -> None:
        author = ScopingSession("sergey-1", settings=settings, pipeline=pipeline, owner_id="sergey")
        author.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        run_id = author.run_id
        author.appoint_reviewer("reviewer")

        # The reviewer opens the run from their own session, by id alone.
        reviewer = ScopingSession.restore(
            run_id, settings=settings, session_id="reviewer-1", pipeline=pipeline
        )
        reopened = reviewer.snapshot()
        assert reopened.awaiting_approval

        decided = reviewer.resume_approval(
            True, actor_id="reviewer", actor_role="approver"
        )
        assert decided.status == "completed"
        assert decided.publication.get("allowed") is True

    def test_the_author_is_refused_on_the_reopened_run(self, settings, pipeline) -> None:
        author = ScopingSession("sergey-1", settings=settings, pipeline=pipeline, owner_id="sergey")
        author.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        author.appoint_reviewer("reviewer")

        # Same person, new tab: the rule still applies.
        again = ScopingSession.restore(
            author.run_id, settings=settings, session_id="sergey-2", pipeline=pipeline
        )
        result = again.resume_approval(True, actor_id="sergey", actor_role="admin")

        assert result.publication["allowed"] is False
        assert result.publication["code"] == DenialCode.AUTHOR_CANNOT_APPROVE.value
        assert result.awaiting_approval
