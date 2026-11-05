"""LangGraph state contract.

The graph state is a plain ``TypedDict`` so it can be checkpointed and inspected. Two
fields use additive reducers (``events``, ``errors``): nodes append to them and the
runtime merges concurrent writes instead of raising ``InvalidUpdateError``.

Everything crossing a node boundary is a **JSON-serialisable primitive**. Pydantic
models are dumped on write and re-validated on read. This keeps the checkpoint payload
portable across ``MemorySaver``, SQLite and Postgres savers, and makes the state
inspectable from the UI without importing the domain layer.
"""

from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Any, TypedDict


class RunStatus(str, Enum):
    """Coarse lifecycle state surfaced to the UI."""

    IDLE = "idle"
    RUNNING = "running"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


class Stage(str, Enum):
    """Which agent last executed, for progress rendering."""

    START = "start"
    TRIAGE = "triage"
    CLARIFY = "clarify"
    ARCHITECT = "architect"
    LEGAL = "legal"
    CRITIC = "critic"
    APPROVAL = "approval"
    TOOLS = "tools"
    FINALIZE = "finalize"
    DONE = "done"


class StepEvent(TypedDict, total=False):
    """One observable step in the run trace rendered by Chainlit telemetry."""

    index: int
    node: str
    stage: str
    title: str
    detail: str
    status: str  # started | completed | failed | skipped
    duration_ms: float
    tokens: int
    cost_usd: float
    provider: str
    model: str
    payload: dict[str, Any]


class GraphState(TypedDict, total=False):
    """The payload threaded through every node.

    Grouped as: inputs → derived artifacts → control signals → outputs.
    """

    # ---------------------------------------------------------------- inputs
    session_id: str
    run_id: str
    raw_requirement: str
    jurisdiction: str
    clarification_answers: str
    auto_deploy: bool

    # ---------------------------------------------------------------- artifacts
    scope: dict[str, Any] | None
    blueprint: dict[str, Any] | None
    draft_sow: dict[str, Any] | None
    critique: dict[str, Any] | None
    evidence: str
    retrieval_diagnostics: dict[str, Any]

    # ---------------------------------------------------------------- control
    status: str
    stage: str
    route: str
    triage_rounds: int
    critique_attempts: int
    repair_target: str
    pending_questions: list[dict[str, Any]]
    approved: bool
    halt_reason: str
    #: `dry_run` or `live`, resolved once at the start of the run from the settings.
    publication_mode: str
    #: The publication decision as a plain dict, so it survives the checkpointer and the
    #: report. Set by the approval node and re-checked by the executor.
    publication: dict[str, Any]
    #: R3.4: created / existing / skipped / uncertain counts, per destination.
    operation_summary: dict[str, Any]
    #: The snapshot a reviewer approved. A later edit changes the current snapshot and this
    #: one no longer matches, which is what makes "approved version 3" mean version 3.
    approved_snapshot: str
    snapshot: dict[str, Any]

    # ---------------------------------------------------------------- outputs
    artifacts: list[dict[str, Any]]
    integrations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]

    # ---------------------------------------------------------------- telemetry
    events: Annotated[list[StepEvent], operator.add]
    errors: Annotated[list[str], operator.add]
    cost: dict[str, Any]


def initial_state(
    *,
    session_id: str,
    run_id: str,
    raw_requirement: str,
    jurisdiction: str = "EU",
    auto_deploy: bool = False,
    publication_mode: str = "dry_run",
) -> GraphState:
    """Build the entry state for a fresh run.

    `publication_mode` is resolved from the settings once, here, rather than read from the
    environment at each decision point. A run whose mode can change underneath it is a run
    where an approval means something different by the time it is used.
    """
    return GraphState(
        session_id=session_id,
        run_id=run_id,
        raw_requirement=raw_requirement,
        jurisdiction=jurisdiction,
        clarification_answers="",
        auto_deploy=auto_deploy,
        publication_mode=publication_mode,
        publication={},
        approved_snapshot="",
        snapshot={},
        scope=None,
        blueprint=None,
        draft_sow=None,
        critique=None,
        evidence="",
        retrieval_diagnostics={},
        status=RunStatus.RUNNING.value,
        stage=Stage.START.value,
        route="",
        triage_rounds=0,
        critique_attempts=0,
        repair_target="",
        pending_questions=[],
        approved=False,
        halt_reason="",
        artifacts=[],
        integrations=[],
        tool_calls=[],
        events=[],
        errors=[],
        cost={},
    )


#: Terminal statuses — the runtime stops driving the graph once one is reached.
TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BUDGET_EXCEEDED.value,
    }
)

#: Statuses that hand control back to the human.
WAITING_STATUSES = frozenset(
    {
        RunStatus.AWAITING_CLARIFICATION.value,
        RunStatus.AWAITING_APPROVAL.value,
    }
)
