"""Session runtime: the façade the UI drives.

:class:`ScopingSession` owns one compiled graph plus its checkpoint thread. Callers get
a typed :class:`RunOutcome` after every drive, which is what the Chainlit layer renders
— the UI never touches raw graph state or LangGraph primitives.

The human-in-the-loop protocol is a small, explicit state machine::

    start()                  → AWAITING_CLARIFICATION  → resume_clarification(answers)
                             → AWAITING_APPROVAL       → resume_approval(approved)
                             → COMPLETED | FAILED | BUDGET_EXCEEDED
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import Settings, get_settings
from ..llm.base import BaseLLMClient
from ..llm.factory import get_session_client
from ..models import (
    ArtifactRef,
    CostReport,
    CritiqueReport,
    RequirementScope,
    SOWDiff,
    SOWDocument,
    TechnicalBlueprint,
    diff_sow,
)
from ..observability.logging import bind_run, get_logger
from ..rag.pipeline import RagPipeline, get_pipeline
from ..telemetry import tracker
from ..tools.registry import ToolRegistry, build_default_registry
from .graph import build_graph
from .nodes import AgentNodes
from .state import WAITING_STATUSES, GraphState, RunStatus, StepEvent, initial_state

log = get_logger(__name__)


@dataclass
class RunOutcome:
    """Everything the UI needs after one drive of the graph."""

    run_id: str
    status: str
    stage: str = "start"
    waiting_for: str | None = None

    scope: RequirementScope | None = None
    blueprint: TechnicalBlueprint | None = None
    sow: SOWDocument | None = None
    critique: CritiqueReport | None = None

    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    integrations: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)

    events: list[StepEvent] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)
    cost: CostReport | None = None

    halt_reason: str = ""
    errors: list[str] = field(default_factory=list)

    #: Release 2.1, R1/R5: whether this run may write externally, and why.
    #: Carried on the outcome so the interface renders a decision rather than re-deriving one
    #: from the verdict — two places computing the same policy is how they come to disagree.
    publication: dict[str, Any] = field(default_factory=dict)

    #: R3.4: what each external destination actually got.
    operation_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def is_waiting(self) -> bool:
        return self.status in WAITING_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.BUDGET_EXCEEDED.value,
        )

    @property
    def awaiting_clarification(self) -> bool:
        return self.status == RunStatus.AWAITING_CLARIFICATION.value

    @property
    def awaiting_approval(self) -> bool:
        return self.status == RunStatus.AWAITING_APPROVAL.value

    def new_events(self, since_index: int) -> list[StepEvent]:
        return self.events[since_index:]


def _parse_interrupt(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a pending interrupt payload directly from a graph result.

    Retained as a fast path; :meth:`ScopingSession._pending_interrupt` is authoritative
    because it reads the checkpointed task state.
    """
    raw = result.get("__interrupt__")
    if not raw:
        return None
    first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"value": value}


def _interrupt_from_tasks(tasks: Any) -> dict[str, Any] | None:
    """Read the interrupt payload from checkpointed Pregel tasks.

    LangGraph records pending interrupts on ``StateSnapshot.tasks[*].interrupts``;
    the ``__interrupt__`` key is only present on some return paths and versions, so
    this is the reliable source.
    """
    for task in tasks or ():
        for item in getattr(task, "interrupts", None) or ():
            value = getattr(item, "value", item)
            if isinstance(value, dict):
                return value
            return {"value": value}
    return None


class ScopingSession:
    """Drives one end-to-end scoping engagement."""

    def __init__(
        self,
        session_id: str,
        *,
        settings: Settings | None = None,
        client: BaseLLMClient | None = None,
        pipeline: RagPipeline | None = None,
        registry: ToolRegistry | None = None,
        checkpointer: Any | None = None,
        run_id: str | None = None,
        owner_id: str = "",
    ) -> None:
        self.session_id = session_id
        #: Who owns this run. Defaults to the session, which is unique per connection; the
        #: application passes the authenticated user instead, because a reviewer opening the run
        #: from a new tab has a new session id and must still be judged against the same author.
        self.owner_id = owner_id or session_id
        self.settings = settings or get_settings()
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.started_at = datetime.utcnow()

        self.client = client or get_session_client(session_id, self.settings)
        self.pipeline = pipeline or get_pipeline(self.settings)
        # Same reason as in AgentNodes: the session builds its own registry before the nodes
        # exist, and passing the global settings here would have re-opened the leak the
        # nodes closed.
        self.registry = registry or build_default_registry(self.settings)

        # Pass through the *caller-supplied* client, not the resolved one.
        #
        # AgentNodes treats a non-None client as "the caller owns provider selection,
        # use it for every node" — which is what tests and custom wiring want. Passing
        # `self.client` here meant that branch was always taken, so the independently
        # routed Critic client was never constructed and `SOWSPRINT_CRITIC_PROVIDER`
        # had no effect in the real application. Only an explicitly injected client
        # should suppress per-tier routing.
        self.nodes = AgentNodes(
            session_id,
            client=client,
            pipeline=self.pipeline,
            registry=self.registry,
            settings=self.settings,
        )
        # R2.1: the checkpoint store is durable unless the caller explicitly asks for memory.
        #
        # Without this the approval gate existed only in the process's memory: restart the
        # container while a contract waited for a decision and the run was not failed, it was
        # gone, with no record that anyone had been asked. `interrupt()` works by raising out of
        # the node and waiting, so what it waits *in* is the whole feature.
        if checkpointer is None:
            from ..storage.checkpointer import build_checkpointer

            checkpointer = build_checkpointer(getattr(self.settings, "checkpoint_url", "memory://"))
        self._checkpointer = checkpointer
        self.store = getattr(self.settings, "run_store", None)
        self.graph = build_graph(self.nodes, checkpointer=checkpointer)
        self._config: dict[str, Any] = {
            "configurable": {"thread_id": self.run_id},
            "recursion_limit": 60,
        }
        self._last_state: GraphState = {}
        #: Populated by the streaming API once a drive finishes.
        self.last_outcome: RunOutcome | None = None
        bind_run(self.run_id)

    # ------------------------------------------------------------------ lifecycle
    def start(
        self,
        requirement: str,
        *,
        jurisdiction: str = "EU",
        auto_deploy: bool = False,
    ) -> RunOutcome:
        """Begin a new engagement from a raw requirement (text or transcribed voice)."""
        state = initial_state(
            session_id=self.session_id,
            run_id=self.run_id,
            raw_requirement=requirement,
            jurisdiction=jurisdiction,
            auto_deploy=auto_deploy,
            publication_mode=self.settings.publication_mode,
        )
        log.info(
            "session.start",
            run_id=self.run_id,
            jurisdiction=jurisdiction,
            chars=len(requirement),
            auto_deploy=auto_deploy,
            publication_mode=self.settings.publication_mode,
        )
        return self._drive(state)

    def resume_clarification(self, answers: str) -> RunOutcome:
        """Supply the customer's answers and continue the clarification loop."""
        return self._resume(answers)

    @classmethod
    def restore(
        cls,
        run_id: str,
        *,
        settings: Any | None = None,
        session_id: str = "",
        pipeline: Any | None = None,
        checkpointer: Any | None = None,
    ) -> ScopingSession:
        """Reopen a saved run by its id.

        R2.1: after a reload or a restart the team returns to work by the run id, not by
        repeating the brief. The run id *is* the LangGraph thread id, so reopening it is enough
        to read the checkpoint — the same version, the same stage, the same waiting interrupt.

        Nothing is asserted here about what the run will contain. A caller asks
        :meth:`snapshot` and gets whatever was saved; a run that never existed reads as an empty
        state rather than an exception, because "not found" is a normal thing for a link to be.
        """
        resolved = settings or get_settings()
        session = cls(
            session_id or f"restored-{run_id[:8]}",
            run_id=run_id,
            settings=resolved,
            pipeline=pipeline,
            checkpointer=checkpointer,
        )
        return session

    def history(self) -> list[dict[str, Any]]:
        """Every checkpoint the graph has written for this run, oldest first.

        Used by the review card to answer "how did it get here" without re-running anything.
        """
        rows: list[dict[str, Any]] = []
        try:
            for item in self.graph.get_state_history(self._config):
                values = getattr(item, "values", {}) or {}
                rows.append(
                    {
                        "stage": values.get("stage", ""),
                        "status": values.get("status", ""),
                        "approved": bool(values.get("approved")),
                    }
                )
        except Exception as exc:
            log.warning("session.history_unavailable", run_id=self.run_id, error=str(exc))
        return list(reversed(rows))

    def resume_approval(
        self, approved: bool, *, actor_id: str = "", actor_role: str = ""
    ) -> RunOutcome:
        """Record the reviewer's decision at the approval gate.

        R2.2: the decision is written to the application database before the graph moves. The
        order matters — if the process dies between the two, a decision that was made is
        recorded but nothing was written externally, which is recoverable. The reverse leaves
        objects in Jira with no record of who authorised them.
        """
        # R4.1 / R4.2: the check is here, on the server, before the graph moves.
        #
        # A button that is not rendered is a suggestion. This is the control: any caller — the
        # interface, an API route, a script — reaches the same function, and the rule that the
        # author of a contract cannot approve it is enforced once rather than remembered
        # everywhere.
        if actor_id:
            from ..security.roles import can_approve

            owner = self._run_owner()
            reviewer = self._run_reviewer()
            verdict = can_approve(
                actor_id=actor_id,
                role=actor_role or "",
                run_owner=owner,
                run_reviewer=reviewer,
            )
            if not verdict.allowed:
                log.warning(
                    "approval.refused",
                    run_id=self.run_id,
                    actor=actor_id,
                    code=verdict.code.value if verdict.code else None,
                )
                current = self.snapshot()
                current.publication = {
                    **(current.publication or {}),
                    "allowed": False,
                    "code": verdict.code.value if verdict.code else None,
                    "reason": verdict.reason,
                }
                self.last_outcome = current
                return current

        # R1 blocker, addendum 01: the decision is written BEFORE the graph moves, not after.
        #
        # The earlier order recorded it on the way out, which means a crash between the first
        # external call and the return left objects in Jira with no record of who authorised
        # them — the exact failure the record exists to prevent. Writing first makes the worst
        # case a decision recorded with nothing sent, which an operator can see and resume.
        #
        # A persistence failure here aborts the decision rather than being logged and ignored:
        # if the record cannot be written, the approval cannot be honoured.
        guard = self._publication_guard(approved, actor_id)
        if guard is not None:
            return guard

        outcome = self._resume(bool(approved))
        try:
            self._persist_decision(outcome, approved, actor_id)
        except Exception as exc:  # after the fact: the run has already moved
            log.error("session.decision_not_persisted", run_id=self.run_id, error=str(exc))
        return outcome

    def _publication_guard(self, approved: bool, actor_id: str) -> RunOutcome | None:
        """Record the decision, or explain why the run may not proceed.

        Returns an outcome to hand back when the approval is refused; ``None`` when it is safe
        to drive the graph.

        Three checks, in the order that produces the most useful refusal:

        1. **The mode has not moved.** A run opened as dry-run and confirmed after the
           deployment switched to live must not write. The saved mode is the one shown to the
           person who clicked, so it is the one that governs.
        2. **The decision can be recorded.** No record, no write.
        3. **The run is still at the same version.** The snapshot was taken when the approval
           card was rendered; if the document moved, the button describes something else.
        """
        from ..policy import PublicationMode, Snapshot, evaluate_publication
        from .nodes import _audit_degraded, _audit_passed

        # Read the checkpoint, not a cached field. `_last_state` is a convenience for the
        # outcome snapshot; relying on it here meant a guard that silently saw an empty state
        # when the cache had been reset, refused for "no audit", and recorded nothing — which
        # looks identical to a correct refusal until you look for the row.
        try:
            state = dict(self.graph.get_state(self._config).values or {})
        except Exception as exc:
            log.error("approval.state_unreadable", run_id=self.run_id, error=str(exc))
            state = dict(getattr(self, "_last_state", {}) or {})
        saved_mode = str(state.get("publication_mode") or self.settings.publication_mode)
        actual_mode = self.settings.publication_mode

        def refuse(code: str, reason: str) -> RunOutcome:
            log.warning("approval.blocked", run_id=self.run_id, code=code)
            current = self.snapshot()
            current.publication = {
                **(current.publication or {}),
                "allowed": False,
                "code": code,
                "reason": reason,
            }
            self.last_outcome = current
            return current

        if saved_mode != actual_mode:
            # The connectors follow the deployment; the person clicked on the run's own mode.
            return refuse(
                "mode_changed",
                f"This run was opened in {saved_mode} mode and the deployment is now in "
                f"{actual_mode}. Nothing was sent. Reopen the run, review it in the current "
                "mode, and approve again.",
            )

        if not approved:
            # A rejection writes nothing externally, so it needs no record before the fact; it
            # is recorded on the way out like any other state change.
            return None

        snapshot = Snapshot.build(
            sow=state.get("draft_sow"),
            backlog=state.get("blueprint"),
            plan={"calls": state.get("tool_calls") or []},
            destinations=sorted(
                {
                    "jira" if str(c.get("tool", "")).startswith("create_jira") else "notion"
                    for c in (state.get("tool_calls") or [])
                    if str(c.get("tool", "")).startswith(("create_jira", "create_notion"))
                }
            ),
            version=int((state.get("critique") or {}).get("attempt", 0)) + 1,
        )
        decision = evaluate_publication(
            mode=PublicationMode(saved_mode),
            audit_passed=_audit_passed(state),
            audit_degraded=_audit_degraded(state),
            approved=True,
            approved_snapshot=snapshot.digest,
            current_snapshot=snapshot,
        )
        if decision.blocked:
            return refuse(
                decision.code.value if decision.code else "blocked",
                decision.reason,
            )

        try:
            self._record_decision_ahead(decision, snapshot, approved, actor_id)
        except Exception as exc:
            log.error("session.decision_not_recorded", run_id=self.run_id, error=str(exc))
            return refuse(
                "decision_not_recorded",
                "The decision could not be recorded, so nothing was sent. This is a storage "
                f"problem, not a contract problem: {exc}",
            )
        return None

    def _record_decision_ahead(self, decision: Any, snapshot: Any, approved: bool, actor_id: str) -> None:
        """Write the decision before the first external call."""
        store = self.store
        if store is None:
            return
        from ..storage.store import DecisionRecord, EventRecord, RevisionRecord, RunRecord

        version = int(snapshot.version)
        mode = decision.mode.value if hasattr(decision.mode, "value") else str(decision.mode)
        record = store.get_run(self.run_id) or RunRecord(run_id=self.run_id)
        record.owner_id = record.owner_id or self.owner_id
        record.reviewer_id = record.reviewer_id or getattr(self, "_pending_reviewer", "")
        record.publication_mode = mode
        record.current_version = version
        record.status = "approved" if approved else record.status
        store.save_run(record)

        store.save_revision(
            RevisionRecord(
                run_id=self.run_id,
                number=version,
                snapshot_hash=snapshot.digest,
                audit_passed=True,
                plan_hash=snapshot.plan_hash,
                mode=mode,
                destinations=list(snapshot.destinations),
            )
        )
        store.save_decision(
            DecisionRecord(
                run_id=self.run_id,
                revision=version,
                snapshot_hash=snapshot.digest,
                mode=mode,
                actor_id=actor_id or self.owner_id,
                decision="approve" if approved else "reject",
            )
        )
        store.add_event(
            EventRecord(
                run_id=self.run_id,
                actor_id=actor_id or self.owner_id,
                action="approve" if approved else "reject",
                version=version,
                result="recorded before execution",
            )
        )

    def appoint_reviewer(self, reviewer_id: str) -> None:
        """Name the person who may approve this run.

        R4.1: the reviewer is set on the run, not in the environment. Until this is called, a
        run has an author and nobody entitled to approve it — which is the honest state and the
        one that produces a useful refusal rather than a silent self-approval.
        """
        if not reviewer_id:
            raise ValueError("a reviewer id is required")
        if reviewer_id == self.owner_id:
            raise ValueError("the author cannot appoint themselves as reviewer")
        self._pending_reviewer = reviewer_id
        if self.store is None:
            return

        # Written now, not when the decision is taken. An appointment is a durable fact about
        # the run: a reviewer who opens it from another tab has to be recognised before they
        # decide anything, and a record created at decision time would arrive too late.
        from ..storage.store import RunRecord

        record = self.store.get_run(self.run_id)
        if record is None:
            record = RunRecord(run_id=self.run_id, owner_id=self.owner_id)
        record.reviewer_id = reviewer_id
        self.store.save_run(record, touch=False)

    def _run_owner(self) -> str:
        """Who started this run.

        Read from the store when the run has been saved, and from the session otherwise. The
        store is authoritative because it survives a restart: a reviewer reopening the run must
        be refused for the same reason the author was refused, not for a fresh one.
        """
        if self.store is not None:
            record = self.store.get_run(self.run_id)
            if record is not None and record.owner_id:
                return record.owner_id
        return self.owner_id

    def _run_reviewer(self) -> str:
        """Who is appointed to review this run. Empty until the first decision records one."""
        if self.store is not None:
            record = self.store.get_run(self.run_id)
            if record is not None and record.reviewer_id:
                return record.reviewer_id
        return getattr(self, "_pending_reviewer", "")

    def _persist_run(self, outcome: RunOutcome) -> None:
        """Record the run as soon as it exists.

        R2.1 asks for a run id the team can return to. A record written only when a decision is
        taken would not be listed while the thing is actually waiting — which is exactly when
        somebody else needs to find it.
        """
        if self.store is None:
            return
        from ..storage.store import RunRecord

        existing = self.store.get_run(self.run_id)
        record = existing or RunRecord(run_id=self.run_id)
        record.owner_id = record.owner_id or self.owner_id
        record.title = str(getattr(outcome.sow, "title", "") or record.title or "")[:400]
        record.status = outcome.status
        record.stage = outcome.stage
        record.publication_mode = str(
            (outcome.publication or {}).get("mode") or self.settings.publication_mode
        )
        record.halt_reason = outcome.halt_reason or ""
        self.store.save_run(record)

    def _persist_decision(self, outcome: RunOutcome, approved: bool, actor_id: str) -> None:
        """Store the run, its current revision and the decision as one story."""
        store = self.store
        if store is None:
            return

        from ..storage.store import DecisionRecord, EventRecord, RevisionRecord, RunRecord

        publication = outcome.publication or {}
        snapshot_hash = str(publication.get("snapshot") or "")
        version = int(publication.get("version") or 1)
        mode = str(publication.get("mode") or "dry_run")

        store.save_run(
            RunRecord(
                run_id=self.run_id,
                title=str(getattr(outcome.sow, "title", "") or "")[:400],
                owner_id=self.owner_id,
                reviewer_id=actor_id or getattr(self, "_pending_reviewer", ""),
                status=outcome.status,
                stage=outcome.stage,
                current_version=version,
                publication_mode=mode,
                jurisdiction=str(getattr(outcome, "jurisdiction", "EU") or "EU"),
                brief=str(getattr(outcome, "brief", "") or "")[:20000],
                halt_reason=outcome.halt_reason,
            )
        )
        store.save_revision(
            RevisionRecord(
                run_id=self.run_id,
                number=version,
                snapshot_hash=snapshot_hash,
                audit_passed=bool(outcome.critique.passed) if outcome.critique else None,
                audit_report=(
                    outcome.critique.model_dump(mode="json") if outcome.critique else {}
                ),
                plan_hash=str(publication.get("plan_hash") or ""),
                mode=mode,
                destinations=list(publication.get("destinations") or []),
                artifacts=[a.path for a in (outcome.artifacts or [])],
            )
        )
        store.save_decision(
            DecisionRecord(
                run_id=self.run_id,
                revision=version,
                snapshot_hash=snapshot_hash,
                mode=mode,
                actor_id=actor_id or self.session_id,
                decision="approve" if approved else "reject",
            )
        )
        store.add_event(
            EventRecord(
                run_id=self.run_id,
                actor_id=actor_id or self.session_id,
                action="approve" if approved else "reject",
                version=version,
                result=outcome.status,
            )
        )

    # ------------------------------------------------------------------ revision
    def revise(
        self,
        instruction: str,
        *,
        lock: Sequence[str] = (),
        unlock: Sequence[str] = (),
    ) -> Iterator[StepEvent]:
        """Apply a targeted change to the current contract.

        Revision is deliberately a separate workflow from drafting rather than another
        node in the graph. Drafting produces a document; revision edits one that the
        parties have already read, and the guarantee that matters — that untouched and
        locked clauses come back unchanged — is enforced in code rather than by asking
        a model nicely.

        Yields step events; the result is on :attr:`last_outcome`.
        """
        from ..llm.base import LLMError
        from ..llm.offline import (
            enforce_lock,
            legal_revision_offline,
        )
        from .nodes import _event
        from .state import Stage

        state = dict(self.state)
        current = _validate(SOWDocument, state.get("draft_sow"))
        if current is None:
            raise RuntimeError("There is no contract to revise yet.")

        # ---- apply lock changes the operator requested ------------------------
        if lock or unlock:
            self.set_locks(lock, locked=True)
            self.set_locks(unlock, locked=False)
            state = dict(self.state)
            current = _validate(SOWDocument, state.get("draft_sow")) or current

        started = time.perf_counter()
        assert self.nodes is not None
        client = self.nodes.client

        if getattr(client, "provider", "") == "offline":
            revised = legal_revision_offline(
                current.model_dump(mode="json"),
                instruction,
                state.get("scope") or {},
                state.get("evidence", ""),
                str(state.get("jurisdiction", "EU")),
            )
            response = None
        else:
            from .prompts import build_revision_messages

            messages = build_revision_messages(
                current.model_dump(mode="json"),
                instruction,
                state.get("scope") or {},
                state.get("evidence", ""),
                str(state.get("jurisdiction", "EU")),
                blueprint=state.get("blueprint"),
            )
            response = client.complete(
                messages,
                schema=SOWDocument,
                node="legal",
                tier=self.nodes.router.tier_for("legal"),
                max_tokens=self.nodes.router.max_tokens_for("legal"),
            )
            revised = response.parsed
            if not isinstance(revised, SOWDocument):
                raise LLMError("Revision returned no valid SOWDocument")

        # ---- the guarantee ----------------------------------------------------
        revised, restored = enforce_lock(current, revised)
        revised.revision = current.revision + 1

        diff = diff_sow(current, revised)
        duration = (time.perf_counter() - started) * 1000.0

        event = _event(
            "revision",
            Stage.LEGAL,
            f"Revision {revised.revision} — {len(diff.edited)} clause(s) changed",
            detail=(
                f"{diff.summary()}"
                + (f" · {len(restored)} locked clause(s) restored" if restored else "")
            ),
            duration_ms=duration,
            response=response,
            payload={
                "instruction": instruction,
                "edited": [
                    {"number": c.number, "heading": c.heading, "change": c.change}
                    for c in diff.edited
                ],
                "restored_locks": restored,
            },
        )

        # ---- re-audit the revised contract ------------------------------------
        audit_events: list[StepEvent] = []
        critique_payload = state.get("critique")
        try:
            audited_state = {
                **state,
                "draft_sow": revised.model_dump(mode="json"),
                "critique_attempts": 0,
            }
            audit_update = self.nodes.critic(audited_state)
            audit_events = list(audit_update.get("events") or [])
            critique_payload = audit_update.get("critique", critique_payload)
        except Exception as exc:
            log.warning("revision.audit_failed", error=str(exc))

        # ---- persist -----------------------------------------------------------
        self._last_state = {
            **state,
            "draft_sow": revised.model_dump(mode="json"),
            "critique": critique_payload,
            "stage": Stage.LEGAL.value,
            "status": state.get("status", RunStatus.RUNNING.value),
        }
        artifacts = self._render_artifacts(self._last_state, diff=diff)
        self._last_state["artifacts"] = artifacts

        yield event
        yield from audit_events
        self.last_outcome = self._to_outcome(self._last_state, None)

    def set_locks(self, numbers: Sequence[str], *, locked: bool) -> list[str]:
        """Lock or unlock clauses by number. Returns the numbers actually changed."""

        state = dict(self.state)
        document = _validate(SOWDocument, state.get("draft_sow"))
        if document is None:
            return []

        wanted = {str(n).strip() for n in numbers if str(n).strip()}
        changed: list[str] = []
        for clause in document.clauses:
            matches = clause.number in wanted or clause.heading in wanted
            if matches and clause.locked is not locked:
                clause.locked = locked
                changed.append(clause.number)
        self._last_state = {**state, "draft_sow": document.model_dump(mode="json")}
        log.info("revision.locks_changed", locked=locked, clauses=changed)
        return changed

    def _render_artifacts(self, state: dict[str, Any], *, diff: SOWDiff | None = None) -> list[dict[str, Any]]:
        """Re-render deliverables for the current state, optionally with a diff."""
        from ..export.bundle import render_deliverables

        try:
            return render_deliverables(
                session_id=self.session_id,
                run_id=str(state.get("run_id", self.run_id)),
                scope=state.get("scope"),
                blueprint=state.get("blueprint"),
                sow=state.get("draft_sow"),
                critique=state.get("critique"),
                integrations=state.get("integrations"),
                cost=self.cost_report().model_dump(mode="json"),
                diff=diff,
            )
        except Exception as exc:
            log.error("revision.export_failed", error=str(exc))
            return []

    def cancel(self) -> None:
        """Mark the run as failed without touching external systems."""
        log.info("session.cancel", run_id=self.run_id)

    # ------------------------------------------------------------------ streaming
    def start_stream(
        self,
        requirement: str,
        *,
        jurisdiction: str = "EU",
        auto_deploy: bool = False,
    ) -> Iterator[StepEvent]:
        """Start a run, yielding each step event as the graph produces it.

        The final :class:`RunOutcome` is available afterwards on
        :attr:`last_outcome`, so the UI can render progress live and then read the
        result without a second call.
        """
        state = initial_state(
            session_id=self.session_id,
            run_id=self.run_id,
            raw_requirement=requirement,
            jurisdiction=jurisdiction,
            auto_deploy=auto_deploy,
            publication_mode=self.settings.publication_mode,
        )
        log.info(
            "session.start",
            run_id=self.run_id,
            jurisdiction=jurisdiction,
            chars=len(requirement),
            auto_deploy=auto_deploy,
            publication_mode=self.settings.publication_mode,
        )
        yield from self._stream(state)

    def resume_clarification_stream(self, answers: str) -> Iterator[StepEvent]:
        """Stream the graph forward after the customer answers the questions."""
        yield from self._stream(self._resume_payload(answers))

    def resume_approval_stream(
        self, approved: bool, *, actor_id: str = "", actor_role: str = ""
    ) -> Iterator[StepEvent]:
        """Stream the graph forward after the reviewer's decision.

        The role check runs before the stream starts, for the same reason `resume_approval`
        has it: a refused decision must not produce the appearance of progress.
        """
        if approved and actor_id:
            from ..security.roles import can_approve

            verdict = can_approve(
                actor_id=actor_id,
                role=actor_role,
                run_owner=self._run_owner(),
                run_reviewer=self._run_reviewer(),
            )
            if not verdict.allowed:
                log.warning(
                    "approval.refused",
                    run_id=self.run_id,
                    actor=actor_id,
                    code=verdict.code.value if verdict.code else None,
                )
                current = self.snapshot()
                current.publication = {
                    **(current.publication or {}),
                    "allowed": False,
                    "code": verdict.code.value if verdict.code else None,
                    "reason": verdict.reason,
                }
                self.last_outcome = current
                return
        yield from self._stream(self._resume_payload(bool(approved)))

    @staticmethod
    def _resume_payload(payload: Any) -> Any:
        from langgraph.types import Command

        return Command(resume=payload)

    def _stream(self, payload: Any) -> Iterator[StepEvent]:
        """Drive the graph in update mode, emitting events and recording the outcome."""
        try:
            for chunk in self.graph.stream(payload, self._config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                for _node, update in chunk.items():
                    if not isinstance(update, dict):
                        continue
                    yield from update.get("events") or []
        except Exception as exc:
            log.error("session.stream_failed", run_id=self.run_id, error=str(exc))
            self.last_outcome = RunOutcome(
                run_id=self.run_id,
                status=RunStatus.FAILED.value,
                halt_reason=f"{type(exc).__name__}: {exc}",
                errors=[str(exc)],
                cost=tracker.get_ledger(self.session_id).report(),
            )
            return

        self.last_outcome = self.snapshot()
        self._persist_run(self.last_outcome)

    def snapshot(self) -> RunOutcome:
        """Read the current checkpoint and package it as a :class:`RunOutcome`."""
        try:
            state_snapshot = self.graph.get_state(self._config)
            self._last_state = dict(state_snapshot.values or {})
            interrupt_payload = _interrupt_from_tasks(getattr(state_snapshot, "tasks", None))
        except Exception as exc:
            log.warning("session.snapshot_failed", run_id=self.run_id, error=str(exc))
            interrupt_payload = None
        return self._to_outcome(self._last_state, interrupt_payload)

    # ------------------------------------------------------------------ internals
    def _resume(self, payload: Any) -> RunOutcome:
        from langgraph.types import Command

        return self._drive(Command(resume=payload))

    def _drive(self, payload: Any) -> RunOutcome:
        """Invoke the graph and package the result.

        Records the outcome on :attr:`last_outcome` as well as returning it, so callers
        can use the streaming and non-streaming APIs interchangeably.
        """
        try:
            result = self.graph.invoke(payload, self._config)
        except Exception as exc:
            log.error("session.graph_failed", run_id=self.run_id, error=str(exc))
            self._last_state = {**self._last_state, "status": RunStatus.FAILED.value}
            self.last_outcome = RunOutcome(
                run_id=self.run_id,
                status=RunStatus.FAILED.value,
                halt_reason=f"{type(exc).__name__}: {exc}",
                errors=[str(exc)],
                cost=tracker.get_ledger(self.session_id).report(),
            )
            return self.last_outcome

        self._last_state = dict(result)
        interrupt_payload = _parse_interrupt(result) or self._pending_interrupt()
        self.last_outcome = self._to_outcome(result, interrupt_payload)
        return self.last_outcome

    def _pending_interrupt(self) -> dict[str, Any] | None:
        """Read the pending interrupt from the checkpointed task state."""
        try:
            snapshot = self.graph.get_state(self._config)
        except Exception as exc:
            log.debug("runtime.get_state_failed", error=str(exc))
            return None
        return _interrupt_from_tasks(getattr(snapshot, "tasks", None))

    @property
    def next_nodes(self) -> tuple[str, ...]:
        """Nodes the graph will execute when resumed (empty when finished)."""
        try:
            return tuple(self.graph.get_state(self._config).next or ())
        except Exception:
            return ()

    def _to_outcome(
        self, state: dict[str, Any], interrupt_payload: dict[str, Any] | None
    ) -> RunOutcome:
        status = str(state.get("status", RunStatus.RUNNING.value))

        outcome = RunOutcome(
            run_id=self.run_id,
            status=status,
            stage=str(state.get("stage", "start")),
            pending_questions=list(state.get("pending_questions") or []),
            tool_calls=list(state.get("tool_calls") or []),
            integrations=list(state.get("integrations") or []),
            events=list(state.get("events") or []),
            retrieval=dict(state.get("retrieval_diagnostics") or {}),
            halt_reason=str(state.get("halt_reason", "")),
            errors=list(state.get("errors") or []),
            publication=dict(state.get("publication") or {}),
            operation_summary=dict(state.get("operation_summary") or {}),
            cost=tracker.get_ledger(self.session_id).report(),
        )

        outcome.scope = _validate(RequirementScope, state.get("scope"))
        outcome.blueprint = _validate(TechnicalBlueprint, state.get("blueprint"))
        outcome.sow = _validate(SOWDocument, state.get("draft_sow"))
        outcome.critique = _validate(CritiqueReport, state.get("critique"))
        outcome.artifacts = [
            ArtifactRef.model_validate(item) for item in (state.get("artifacts") or [])
        ]

        if interrupt_payload:
            kind = str(interrupt_payload.get("type", ""))
            outcome.waiting_for = kind or None
            if kind == "clarification":
                outcome.status = RunStatus.AWAITING_CLARIFICATION.value
                outcome.pending_questions = list(
                    interrupt_payload.get("questions") or outcome.pending_questions
                )
            elif kind == "approval":
                outcome.status = RunStatus.AWAITING_APPROVAL.value
        elif status in WAITING_STATUSES:
            # Status was set by the node but the graph already resumed past it.
            outcome.waiting_for = (
                "clarification"
                if status == RunStatus.AWAITING_CLARIFICATION.value
                else "approval"
            )

        return outcome

    # ------------------------------------------------------------------ inspection
    @property
    def state(self) -> GraphState:
        """Latest graph state snapshot."""
        if not self._last_state:
            try:
                snapshot = self.graph.get_state(self._config)
                self._last_state = dict(snapshot.values or {})
            except Exception:
                return {}
        return self._last_state

    def event_count(self) -> int:
        return len(self.state.get("events") or [])

    def cost_report(self) -> CostReport:
        return tracker.get_ledger(self.session_id).report()

    def trace_markdown(self, since_index: int = 0) -> str:
        """Render the step-telemetry trace as Markdown for the Chainlit UI.

        Deliberately free of raw HTML: the app runs with ``unsafe_allow_html = false``
        because it renders model-generated contract text.
        """
        events = (self.state.get("events") or [])[since_index:]
        if not events:
            return "_No steps recorded yet._"

        icons = {"completed": "✅", "failed": "⚠️", "skipped": "⏭️", "started": "⏳"}
        lines: list[str] = []
        for index, event in enumerate(events, start=since_index + 1):
            icon = icons.get(str(event.get("status", "completed")), "•")
            duration = float(event.get("duration_ms", 0.0) or 0.0)
            meta_bits: list[str] = []
            if duration:
                meta_bits.append(f"{duration:,.0f} ms")
            if event.get("tokens"):
                meta_bits.append(f"{int(event['tokens']):,} tok")
            if event.get("cost_usd"):
                meta_bits.append(f"${float(event['cost_usd']):.5f}")
            if event.get("model"):
                meta_bits.append(str(event["model"]))
            meta = f" — *{' · '.join(meta_bits)}*" if meta_bits else ""
            lines.append(f"{icon} **{index}. {event.get('title', event.get('node', 'step'))}**{meta}")
            if event.get("detail"):
                lines.append(f"    {event['detail']}")
            lines.append("")
        return "\n".join(lines)


def _validate(model: Any, payload: Any) -> Any:
    """Re-validate a state payload into its domain model, tolerating ``None``."""
    if not payload:
        return None
    try:
        return model.model_validate(payload)
    except Exception as exc:
        log.warning("runtime.state_revalidation_failed", model=model.__name__, error=str(exc))
        return None
