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
    _stamp_schedule(plan, blueprint, client.settings.resolved_project_start)
    return plan


def _stamp_schedule(plan: ToolCallPlan, blueprint: dict[str, Any], start: Any) -> None:
    """Write the milestone dates onto every issue, overriding whatever was planned.

    Dates are arithmetic, and arithmetic is the thing a language model gets subtly wrong
    while sounding entirely certain. Asked to schedule a twelve-week engagement from a
    given Monday, a live model produced `2025-01-01` — wrong by more than a year, and
    entirely plausible on a timeline.

    So the model decides the structure (which epics, which stories, how they nest) and
    this decides when. Milestone windows are computed once from the start date and matched
    to each call by the `[Milestone]` prefix the plan format already uses; a call the model
    mislabels keeps whatever it proposed rather than being given a date that belongs to
    something else.
    """
    from ..schedule import milestone_windows

    milestones = (blueprint or {}).get("milestones") or []
    if not milestones or start is None:
        return

    spans = milestone_windows(
        start, [int(m.get("duration_weeks") or 1) for m in milestones]
    )
    windows: dict[str, tuple[str, str]] = {}
    epic_milestone: dict[str, str] = {}
    for milestone, (begins, ends) in zip(milestones, spans, strict=False):
        name = str(milestone.get("name") or "")
        if not name:
            continue
        windows[name] = (begins.isoformat(), ends.isoformat())
        # The blueprint states which milestone owns which epic, and `client_ref` is
        # required to carry the epic's key — so this link is guaranteed by the plan format.
        # Matching on the "[Milestone] Name" summary prefix is not: it is a convention of
        # the offline planner, and a live model given no such instruction titles its epics
        # however it likes, which left every issue undated.
        for epic in milestone.get("epics") or []:
            key = str((epic or {}).get("key") or "")
            if key:
                epic_milestone[key] = name

    if not windows:
        return

    # Two passes. Epics first, by the key the plan already promises in `client_ref`.
    # Stories then inherit from the epic they name in `parent_key`, so a story can never
    # be scheduled outside its own epic's window.
    epic_window: dict[str, tuple[str, str]] = {}
    stamped = 0
    for call in plan.calls:
        if call.tool != "create_jira_issue":
            continue
        reference = str(call.arguments.get("client_ref") or "")
        milestone_name = epic_milestone.get(reference)
        if milestone_name and milestone_name in windows:
            window = windows[milestone_name]
            call.arguments["start_date"], call.arguments["due_date"] = window
            epic_window[reference] = window
            stamped += 1

    for call in plan.calls:
        if call.tool != "create_jira_issue" or call.arguments.get("start_date"):
            continue
        window = epic_window.get(str(call.arguments.get("parent_key") or ""))
        if window:
            call.arguments["start_date"], call.arguments["due_date"] = window
            stamped += 1

    if stamped:
        log.info("tools.schedule_stamped", issues=stamped, start=str(start))
    else:
        # Worth saying out loud: a silently undated backlog looks like a deliberate
        # choice on a timeline rather than a lookup that matched nothing.
        log.warning(
            "tools.schedule_unstamped",
            issues=len(plan.calls),
            detail="no epic matched a blueprint milestone; the backlog is undated",
        )
