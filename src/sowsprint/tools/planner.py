"""LLM-driven tool planning.

The PRD calls for "automated LLM function parsing": rather than hard-coding the REST
sequence, the approved blueprint is handed to the model together with the registry's
JSON function schemas, and the model returns a typed list of calls.

Two properties make this safe in production:

* the plan is validated against :class:`ToolCallPlan` before anything executes, so a
  malformed completion cannot reach a customer's Jira tenant;
* tool availability is injected from the registry at call time, so adding a connector
  requires no prompt change.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..llm.base import BaseLLMClient
from ..llm.factory import NODE_MAX_TOKENS
from ..observability.logging import get_logger
from .registry import ToolRegistry, ToolSpec

log = get_logger(__name__)


class PlannedToolCall(BaseModel):
    """One function invocation requested by the model."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Exact tool name from the available_tools list.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments matching the tool's parameter schema."
    )
    rationale: str = Field(default="", description="Why this call is needed.")


class ToolCallPlan(BaseModel):
    """An ordered batch of function calls."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    calls: list[PlannedToolCall] = Field(default_factory=list)

    def tool_histogram(self) -> dict[str, int]:
        histogram: dict[str, int] = {}
        for call in self.calls:
            histogram[call.tool] = histogram.get(call.tool, 0) + 1
        return histogram


def plan_tool_calls(
    client: BaseLLMClient,
    *,
    blueprint: dict[str, Any],
    sow: dict[str, Any] | None,
    registry: ToolRegistry,
    session_id: str,
) -> ToolCallPlan:
    """Ask the model to emit the tool calls that stand up the delivery workspace."""
    from ..agents.prompts import build_tool_planner_messages

    available: list[ToolSpec] = registry.all()
    messages = build_tool_planner_messages(blueprint, sow, registry.to_prompt_dicts())

    try:
        response = client.complete(
            messages,
            schema=ToolCallPlan,
            node="tools",
            tier="reasoning",
            # Tool planning emits one call per epic and per story, so it needs the same
            # generous output budget as the Architect node that produced the plan.
            max_tokens=NODE_MAX_TOKENS["tools"],
        )
    except Exception as exc:
        log.warning("tools.planning_failed", error=str(exc))
        return ToolCallPlan(summary=f"Tool planning failed: {exc}", calls=[])

    plan = response.parsed
    if not isinstance(plan, ToolCallPlan):
        return ToolCallPlan(summary="Tool planning returned no usable plan.", calls=[])

    # Drop any hallucinated tool name before it reaches the executor.
    known = {spec.name for spec in available}
    valid_calls = [call for call in plan.calls if call.tool in known]
    dropped = len(plan.calls) - len(valid_calls)
    if dropped:
        log.warning("tools.unknown_tools_dropped", dropped=dropped, known=sorted(known))
        plan.summary = f"{plan.summary} ({dropped} unknown tool call(s) discarded)".strip()

    plan.calls = valid_calls
    return plan
