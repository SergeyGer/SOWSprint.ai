"""Release 2.1, R4 — two roles, and the author cannot approve their own contract.

The documentation was honest that roles existed in the account model and were not checked on
operations. That is worse than having no roles: it looks like a control and is not one.

The rule that does most of the work is not about trust. A reviewer who did not write the document
reads it differently, and the value of the gate is that second reading.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.security.roles import (  # noqa: E402
    AppRole,
    DenialCode,
    Permission,
    can,
    can_approve,
    can_view,
    role_from_metadata,
)


class TestTheAuthorCannotApprove:
    def test_the_author_is_refused_in_every_role(self) -> None:
        """Including administrator. Writing the contract does not become a licence to sign it."""
        for role in (AppRole.AUTHOR, AppRole.APPROVER, AppRole.ADMIN, AppRole.SERVICE):
            verdict = can_approve(
                actor_id="alice",
                role=role,
                run_owner="alice",
                run_reviewer="bob",
            )
            assert not verdict.allowed, role
            assert verdict.code is DenialCode.AUTHOR_CANNOT_APPROVE

    def test_the_refusal_explains_what_to_do_instead(self) -> None:
        verdict = can_approve(actor_id="alice", role="admin", run_owner="alice")
        assert "second person" in verdict.reason


class TestTheReviewerIsSetOnTheRun:
    def test_the_appointed_reviewer_may_approve(self) -> None:
        verdict = can_approve(
            actor_id="bob", role=AppRole.APPROVER, run_owner="alice", run_reviewer="bob"
        )
        assert verdict.allowed

    def test_a_stranger_may_not(self) -> None:
        verdict = can_approve(
            actor_id="eve", role=AppRole.APPROVER, run_owner="alice", run_reviewer="bob"
        )
        assert not verdict.allowed
        assert verdict.code is DenialCode.NOT_THE_REVIEWER

    def test_an_administrator_must_be_appointed(self) -> None:
        """Being an administrator says how you got an account, not what you agreed to."""
        verdict = can_approve(
            actor_id="root", role=AppRole.ADMIN, run_owner="alice", run_reviewer="bob"
        )
        assert not verdict.allowed
        assert verdict.code is DenialCode.ADMIN_NOT_APPOINTED

    def test_an_appointed_administrator_may_approve(self) -> None:
        verdict = can_approve(
            actor_id="root", role=AppRole.ADMIN, run_owner="alice", run_reviewer="root"
        )
        assert verdict.allowed

    def test_an_unappointed_run_cannot_be_approved_by_anyone_but_its_author_is_still_refused(self) -> None:
        """A run with no reviewer is a run nobody may approve — which is the honest state."""
        stranger = can_approve(actor_id="bob", role=AppRole.APPROVER, run_owner="alice")
        author = can_approve(actor_id="alice", role=AppRole.AUTHOR, run_owner="alice")

        assert not stranger.allowed
        assert not author.allowed
        assert author.code is DenialCode.AUTHOR_CANNOT_APPROVE


class TestUnknownRolesFallDownwards:
    """An unrecognised role must never become the most privileged one."""

    @pytest.mark.parametrize("value", ["", "superuser", "ROOT!", None, "aprover"])
    def test_an_unknown_role_cannot_approve(self, value) -> None:
        verdict = can_approve(
            actor_id="bob", role=value, run_owner="alice", run_reviewer="bob"
        )
        assert not verdict.allowed
        assert verdict.code is DenialCode.ROLE_MAY_NOT_APPROVE

    def test_metadata_defaults_to_the_least_privileged(self) -> None:
        assert role_from_metadata({}) is AppRole.SERVICE
        assert role_from_metadata({"role": "nonsense"}) is AppRole.SERVICE
        assert role_from_metadata({"role": "approver"}) is AppRole.APPROVER


class TestTheRoleDecidesTheAction:
    def test_an_author_may_draft_but_not_approve(self) -> None:
        assert can(role="author", permission=Permission.DRAFT).allowed
        assert not can(role="author", permission=Permission.APPROVE).allowed

    def test_an_approver_may_approve_but_not_draft(self) -> None:
        assert can(role="approver", permission=Permission.APPROVE).allowed
        assert not can(role="approver", permission=Permission.DRAFT).allowed

    def test_a_service_account_may_not_approve(self) -> None:
        """R4.2: a service key runs authorised actions; authorising is not one of them."""
        assert not can(role="service", permission=Permission.APPROVE).allowed


class TestVisibility:
    """R4.3: a run is visible to its participants."""

    def test_a_participant_may_view(self) -> None:
        assert can_view(actor_id="alice", run_owner="alice", run_reviewer="bob").allowed
        assert can_view(actor_id="bob", run_owner="alice", run_reviewer="bob").allowed

    def test_a_stranger_may_not(self) -> None:
        verdict = can_view(actor_id="eve", run_owner="alice", run_reviewer="bob")
        assert not verdict.allowed
        assert verdict.code is DenialCode.NOT_A_PARTICIPANT

    def test_an_administrator_may_view(self) -> None:
        """An operator has to be able to look at a stuck run — explicitly, not by implication."""
        assert can_view(
            actor_id="root", run_owner="alice", run_reviewer="bob", role="admin"
        ).allowed


class TestTheServerEnforcesIt:
    """The check has to be in the call path, not in the button."""

    @pytest.fixture
    def session(self, tmp_path: Path):
        from sowsprint.agents import ScopingSession
        from sowsprint.config import Settings
        from sowsprint.rag.pipeline import get_pipeline
        from sowsprint.telemetry import drop_ledger

        settings = Settings(
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
        pipeline = get_pipeline(settings)
        pipeline.ensure_indexed()
        drop_ledger("r4-test")
        return ScopingSession("alice", settings=settings, pipeline=pipeline)

    def test_an_author_approving_through_the_api_is_refused(self, session) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        import conftest

        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")

        result = session.resume_approval(True, actor_id="alice", actor_role="approver")

        assert result.publication["allowed"] is False
        assert result.publication["code"] == DenialCode.AUTHOR_CANNOT_APPROVE.value
        # The run is still waiting. A refused decision is not a decision.
        assert result.awaiting_approval

    def test_the_appointed_reviewer_gets_through(self, session) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        import conftest

        session.start(conftest.DETAILED_BRIEF, jurisdiction="EU")
        session.appoint_reviewer("bob")

        result = session.resume_approval(True, actor_id="bob", actor_role="approver")

        assert result.status == "completed"

    def test_an_author_cannot_appoint_themselves(self, session) -> None:
        with pytest.raises(ValueError):
            session.appoint_reviewer("alice")
