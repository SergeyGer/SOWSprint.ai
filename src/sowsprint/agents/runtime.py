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

import uuid
from collections.abc import Iterator
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
    SOWDocument,
    TechnicalBlueprint,
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
    ) -> None:
        self.session_id = session_id
        self.settings = settings or get_settings()
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.started_at = datetime.utcnow()

        self.client = client or get_session_client(session_id, self.settings)
        self.pipeline = pipeline or get_pipeline(self.settings)
        self.registry = registry or build_default_registry()

        # `nodes` owns the per-tier clients, including the independent Critic.
        self.nodes = AgentNodes(
            session_id,
            client=self.client,
            pipeline=self.pipeline,
            registry=self.registry,
            settings=self.settings,
        )
        self._checkpointer = checkpointer
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
        )
        log.info(
            "session.start",
            run_id=self.run_id,
            jurisdiction=jurisdiction,
            chars=len(requirement),
            auto_deploy=auto_deploy,
        )
        return self._drive(state)

    def resume_clarification(self, answers: str) -> RunOutcome:
        """Supply the customer's answers and continue the clarification loop."""
        return self._resume(answers)

    def resume_approval(self, approved: bool) -> RunOutcome:
        """Record the reviewer's decision at the approval gate."""
        return self._resume(bool(approved))

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
        )
        log.info(
            "session.start",
            run_id=self.run_id,
            jurisdiction=jurisdiction,
            chars=len(requirement),
            auto_deploy=auto_deploy,
        )
        yield from self._stream(state)

    def resume_clarification_stream(self, answers: str) -> Iterator[StepEvent]:
        """Stream the graph forward after the customer answers the questions."""
        yield from self._stream(self._resume_payload(answers))

    def resume_approval_stream(self, approved: bool) -> Iterator[StepEvent]:
        """Stream the graph forward after the reviewer's decision."""
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
