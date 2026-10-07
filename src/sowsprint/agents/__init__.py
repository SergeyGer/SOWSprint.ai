"""Multi-agent orchestration: LangGraph state machine, nodes and session runtime."""

from __future__ import annotations

from .graph import NODE_NAMES, build_graph, describe_graph, graph_mermaid
from .nodes import MAX_CLARIFICATION_ROUNDS, AgentNodes, is_waiting, make_routers
from .runtime import RunOutcome, ScopingSession
from .state import (
    TERMINAL_STATUSES,
    WAITING_STATUSES,
    GraphState,
    RunStatus,
    Stage,
    StepEvent,
    initial_state,
)

__all__ = [
    "MAX_CLARIFICATION_ROUNDS",
    "NODE_NAMES",
    "TERMINAL_STATUSES",
    "WAITING_STATUSES",
    "AgentNodes",
    "GraphState",
    "RunOutcome",
    "RunStatus",
    "ScopingSession",
    "Stage",
    "StepEvent",
    "build_graph",
    "describe_graph",
    "graph_mermaid",
    "initial_state",
    "is_waiting",
    "make_routers",
]
