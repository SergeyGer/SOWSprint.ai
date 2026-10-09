"""LangGraph node implementations.

Each node is a thin, side-effect-scoped function ``GraphState -> partial update``. All
the interesting behaviour lives in the agent modules; the nodes own *orchestration*
only: they call an agent, validate the result, record a telemetry event, and decide
what the state should look like afterwards.

Two design decisions are worth calling out:

**Analysis is separated from interruption.** ``clarify`` and ``approval`` are the only
nodes that call :func:`~langgraph.types.interrupt`, and neither performs any work
before interrupting. LangGraph re-executes a node from the top when it resumes, so any
LLM call placed before an ``interrupt`` would be billed twice. Splitting ``triage``
(analyse) from ``clarify`` (ask) is what makes the human-in-the-loop cycle
cost-correct rather than merely functional.

**Every node is halt-aware.** A budget overrun or provider failure sets a terminal
status; every routing function checks it and diverts to ``END``. That keeps the failure
path declarative instead of scattering exception handlers through the graph.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..config import Settings, get_settings
from ..llm.base import BaseLLMClient, BudgetExceededError, LLMError
from ..llm.factory import ModelRouter, build_llm_client, get_session_client
from ..models import (
    CritiqueReport,
    Jurisdiction,
    NodeName,
    RequirementScope,
    ScopeStatus,
    SOWDocument,
    TechnicalBlueprint,
    calibrate_severity,
    score_from_findings,
    verify_missing_clause_findings,
)
from ..observability.logging import get_logger
from ..rag.pipeline import RagPipeline, get_pipeline
from ..telemetry import tracker
from ..tools.executor import ToolExecutor
from ..tools.planner import ToolCallPlan, plan_tool_calls
from ..tools.registry import ToolRegistry, build_default_registry
from .prompts import (
    build_architect_messages,
    build_critic_messages,
    build_legal_messages,
    build_triage_messages,
)
from .state import (
    TERMINAL_STATUSES,
    WAITING_STATUSES,
    GraphState,
    RunStatus,
    Stage,
    StepEvent,
)

log = get_logger(__name__)

#: Clarification rounds before the scope is accepted with documented assumptions.
MAX_CLARIFICATION_ROUNDS = 3

#: Maps a node name onto the UI stage it belongs to (some nodes share a stage).
_STAGE_BY_NODE: dict[str, Stage] = {
    "triage": Stage.TRIAGE,
    "clarify": Stage.CLARIFY,
    "architect": Stage.ARCHITECT,
    "legal": Stage.LEGAL,
    "critic": Stage.CRITIC,
    "plan_tools": Stage.APPROVAL,
    "approval": Stage.APPROVAL,
    "tools": Stage.TOOLS,
    "finalize": Stage.FINALIZE,
}


def _event(
    node: str,
    stage: Stage,
    title: str,
    *,
    detail: str = "",
    status: str = "completed",
    duration_ms: float = 0.0,
    response: Any = None,
    payload: dict[str, Any] | None = None,
) -> StepEvent:
    """Build a step-telemetry record for the UI trace."""
    event: StepEvent = {
        "node": node,
        "stage": stage.value,
        "title": title,
        "detail": detail,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        "payload": payload or {},
    }
    if response is not None:
        event["provider"] = getattr(response, "provider", "")
        event["model"] = getattr(response, "model", "")
        event["tokens"] = getattr(response, "total_tokens", 0)
        event["cost_usd"] = getattr(response, "cost_usd", 0.0)
    return event


def _cost_snapshot(session_id: str) -> dict[str, Any]:
    return tracker.get_ledger(session_id).report().model_dump(mode="json")


class AgentNodes:
    """Binds one session's clients and services to the graph's node callables."""

    def __init__(
        self,
        session_id: str,
        *,
        client: BaseLLMClient | None = None,
        pipeline: RagPipeline | None = None,
        registry: ToolRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session_id = session_id
        self.settings = settings or get_settings()
        self.client = client or get_session_client(session_id, self.settings)
        self.critic_client = self._resolve_critic_client(client)
        self.pipeline = pipeline or get_pipeline(self.settings)
        self.registry = registry or build_default_registry()
        self.router = ModelRouter(session_id, self.settings)
        self.executor = ToolExecutor(self.registry)

    def _resolve_critic_client(self, injected: BaseLLMClient | None) -> BaseLLMClient:
        """Give the Critic its own client when it runs on a different provider.

        Judge independence is only real if the request actually reaches another vendor.
        Without this, setting ``critic_model`` to an OpenAI id while the reasoning tier
        resolved to Anthropic would post that id to the Anthropic API and fail.

        The **same** ``session_id`` is passed deliberately: the cost ledger is keyed by
        session, so a separate id would hide the Critic's spend from the dashboard.
        """
        if injected is not None:
            # A caller-injected client (tests, custom wiring) serves every node.
            return injected

        critic_provider = self.settings.resolved_critic_provider
        if critic_provider is self.settings.resolved_llm_provider:
            return self.client

        log.info(
            "nodes.critic_provider_split",
            reasoning=self.settings.resolved_llm_provider.value,
            critic=critic_provider.value,
        )
        return build_llm_client(
            self.session_id, self.settings, provider=critic_provider
        )

    # ------------------------------------------------------------------ helpers
    def _halted(self, state: GraphState) -> bool:
        return str(state.get("status", "")) in TERMINAL_STATUSES

    def _halt(self, state: GraphState, exc: Exception, node: str, stage: Stage | None = None) -> dict[str, Any]:
        """Convert an exception into a terminal state update."""
        resolved_stage = stage or _STAGE_BY_NODE.get(node, Stage.START)
        if isinstance(exc, BudgetExceededError):
            log.warning("node.budget_exceeded", node=node, spent=exc.spent, budget=exc.budget)
            return {
                "status": RunStatus.BUDGET_EXCEEDED.value,
                "halt_reason": str(exc),
                "errors": [f"[{node}] {exc}"],
                "events": [
                    _event(node, resolved_stage, "Budget exhausted", detail=str(exc), status="failed")
                ],
                "cost": _cost_snapshot(self.session_id),
            }
        log.error("node.failed", node=node, error=str(exc))
        return {
            "status": RunStatus.FAILED.value,
            "halt_reason": f"{type(exc).__name__}: {exc}",
            "errors": [f"[{node}] {type(exc).__name__}: {exc}"],
            "events": [
                _event(node, resolved_stage, "Agent failed", detail=str(exc), status="failed")
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ triage
    def triage(self, state: GraphState) -> dict[str, Any]:
        """Parse the raw requirement into a normalised, validated scope."""
        if self._halted(state):
            return {}
        started = time.perf_counter()
        try:
            prior = state.get("scope")
            messages = build_triage_messages(
                state.get("raw_requirement", ""),
                prior_scope=prior,
                clarification_answers=state.get("clarification_answers", ""),
                round_number=int(state.get("triage_rounds", 0)),
            )
            response = self.client.complete(
                messages,
                schema=RequirementScope,
                node="triage",
                tier=self.router.tier_for("triage"),
                max_tokens=self.router.max_tokens_for("triage"),
            )
        except (BudgetExceededError, LLMError) as exc:
            return self._halt(state, exc, "triage")

        scope = response.parsed
        if not isinstance(scope, RequirementScope):
            return self._halt(state, LLMError("Triage returned no valid RequirementScope"), "triage")

        # Trust the UI toggle over model inference: the operator chose the regime.
        requested = state.get("jurisdiction")
        if requested:
            # Tolerates UI variants ("EU+US", "dual", lowercase). An unrecognised value
            # must not discard a valid inferred regime, so coerce falls back quietly.
            scope.jurisdiction = Jurisdiction.coerce(requested, default=scope.jurisdiction)

        duration = (time.perf_counter() - started) * 1000.0
        update: dict[str, Any] = {
            "scope": scope.model_dump(mode="json"),
            "clarification_answers": "",
            "stage": Stage.TRIAGE.value,
            "pending_questions": [q.model_dump(mode="json") for q in scope.clarifying_questions],
            "triage_rounds": int(state.get("triage_rounds", 0)) + 1,
            "events": [
                _event(
                    "triage",
                    Stage.TRIAGE,
                    f"Scope parsed — {scope.status.value}",
                    detail=(
                        f"{len(scope.deliverables)} deliverables · "
                        f"{len(scope.compliance_flags)} compliance trigger(s) · "
                        f"confidence {scope.confidence:.0%}"
                    ),
                    duration_ms=duration,
                    response=response,
                    payload={
                        "title": scope.title,
                        "jurisdiction": scope.jurisdiction.value,
                        "missing": scope.missing_variables,
                    },
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }
        if scope.status is ScopeStatus.INCOMPLETE:
            update["status"] = RunStatus.AWAITING_CLARIFICATION.value
            update["route"] = "clarify"
        else:
            update["status"] = RunStatus.RUNNING.value
            update["route"] = "architect"
        return update

    # ------------------------------------------------------------------ clarify
    def clarify(self, state: GraphState) -> dict[str, Any]:
        """Ask the human exactly three questions and fold the answers back in.

        Nothing happens before :func:`interrupt`, so re-execution on resume is free.
        """
        if self._halted(state):
            return {}

        from langgraph.types import interrupt

        questions = state.get("pending_questions") or []
        answers = interrupt(
            {
                "type": "clarification",
                "round": int(state.get("triage_rounds", 0)),
                "questions": questions,
            }
        )
        if isinstance(answers, dict):
            answers = answers.get("answers", "")
        answers_text = str(answers or "").strip()

        # Re-run triage with the answers folded in. This is a genuine second analysis,
        # not a replay, so the cost is legitimate.
        started = time.perf_counter()
        try:
            messages = build_triage_messages(
                state.get("raw_requirement", ""),
                prior_scope=state.get("scope"),
                clarification_answers=answers_text,
                round_number=int(state.get("triage_rounds", 0)),
            )
            response = self.client.complete(
                messages,
                schema=RequirementScope,
                node="triage",
                tier=self.router.tier_for("triage"),
                max_tokens=self.router.max_tokens_for("triage"),
            )
        except (BudgetExceededError, LLMError) as exc:
            return self._halt(state, exc, "clarify")

        scope = response.parsed
        if not isinstance(scope, RequirementScope):
            return self._halt(state, LLMError("Clarify returned no valid RequirementScope"), "clarify")

        requested = state.get("jurisdiction")
        if requested:
            # Tolerates UI variants ("EU+US", "dual", lowercase). An unrecognised value
            # must not discard a valid inferred regime, so coerce falls back quietly.
            scope.jurisdiction = Jurisdiction.coerce(requested, default=scope.jurisdiction)

        rounds = int(state.get("triage_rounds", 0)) + 1
        exhausted = rounds >= MAX_CLARIFICATION_ROUNDS

        if scope.status is ScopeStatus.INCOMPLETE and exhausted:
            # Stop blocking: convert the residual gaps into explicit assumptions so the
            # contract can still be drafted and reviewed by a human.
            scope.assumptions = list(
                dict.fromkeys(
                    [
                        *scope.assumptions,
                        *[
                            f"To be confirmed at kick-off: {var.replace('_', ' ')}."
                            for var in scope.missing_variables
                        ],
                    ]
                )
            )
            scope.status = ScopeStatus.COMPLETE
            scope.clarifying_questions = []

        duration = (time.perf_counter() - started) * 1000.0
        update: dict[str, Any] = {
            "scope": scope.model_dump(mode="json"),
            "clarification_answers": "",
            "pending_questions": [q.model_dump(mode="json") for q in scope.clarifying_questions],
            "triage_rounds": rounds,
            "stage": Stage.CLARIFY.value,
            "events": [
                _event(
                    "clarify",
                    Stage.CLARIFY,
                    f"Clarification round {rounds} merged — {scope.status.value}",
                    detail=(
                        f"consumer answers folded in; "
                        f"{len(scope.missing_variables)} variable(s) still open"
                        if scope.missing_variables
                        else "all critical variables resolved"
                    ),
                    duration_ms=duration,
                    response=response,
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }
        if scope.status is ScopeStatus.INCOMPLETE and not exhausted:
            update["status"] = RunStatus.AWAITING_CLARIFICATION.value
            update["route"] = "clarify"
        else:
            update["status"] = RunStatus.RUNNING.value
            update["route"] = "architect"
        return update

    # ------------------------------------------------------------------ architect
    def architect(self, state: GraphState) -> dict[str, Any]:
        """Decompose the validated scope into milestones, epics and user stories."""
        if self._halted(state):
            return {}
        started = time.perf_counter()

        repair: list[str] = []
        critique = state.get("critique")
        if isinstance(critique, dict) and str(state.get("repair_target")) == NodeName.ARCHITECT.value:
            repair = [str(item) for item in critique.get("repair_instructions", [])]

        try:
            messages = build_architect_messages(state.get("scope") or {}, repair_instructions=repair)
            response = self.client.complete(
                messages,
                schema=TechnicalBlueprint,
                node="architect",
                tier=self.router.tier_for("architect"),
                max_tokens=self.router.max_tokens_for("architect"),
            )
        except (BudgetExceededError, LLMError) as exc:
            return self._halt(state, exc, "architect")

        blueprint = response.parsed
        if not isinstance(blueprint, TechnicalBlueprint):
            return self._halt(state, LLMError("Architect returned no valid TechnicalBlueprint"), "architect")

        blueprint.recompute_totals()
        duration = (time.perf_counter() - started) * 1000.0
        return {
            "blueprint": blueprint.model_dump(mode="json"),
            "stage": Stage.ARCHITECT.value,
            "status": RunStatus.RUNNING.value,
            "repair_target": "",
            "events": [
                _event(
                    "architect",
                    Stage.ARCHITECT,
                    f"Blueprint ready — {len(blueprint.milestones)} milestones",
                    detail=(
                        f"{len(blueprint.all_stories())} user stories · "
                        f"{blueprint.estimated_total_points} story points · "
                        f"{blueprint.estimated_duration_weeks} weeks"
                    ),
                    duration_ms=duration,
                    response=response,
                    payload={
                        "milestones": [m.name for m in blueprint.milestones],
                        "stories": len(blueprint.all_stories()),
                    },
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ legal
    def legal(self, state: GraphState) -> dict[str, Any]:
        """Retrieve jurisdiction-filtered compliance evidence and draft the SOW."""
        if self._halted(state):
            return {}
        started = time.perf_counter()

        scope_payload = state.get("scope") or {}
        blueprint_payload = state.get("blueprint")
        critique = state.get("critique")
        is_repair = (
            isinstance(critique, dict)
            and not critique.get("passed", True)
            and str(state.get("repair_target")) == NodeName.LEGAL.value
        )

        # --- retrieval ---------------------------------------------------------
        evidence = state.get("evidence", "")
        retrieval_diag: dict[str, Any] = state.get("retrieval_diagnostics", {})
        try:
            scope = RequirementScope.model_validate(scope_payload)
            blueprint = (
                TechnicalBlueprint.model_validate(blueprint_payload) if blueprint_payload else None
            )
            jurisdiction = Jurisdiction.coerce(
                state.get("jurisdiction") or scope.jurisdiction, default=scope.jurisdiction
            )
            result = self.pipeline.retrieve_for_scope(scope, blueprint, jurisdiction=jurisdiction)
            evidence = result.render_evidence()
            retrieval_diag = result.diagnostics.as_dict() if result.diagnostics else {}
        except Exception as exc:
            log.warning("legal.retrieval_failed", error=str(exc))
            evidence = evidence or "(retrieval unavailable)"

        # --- drafting ----------------------------------------------------------
        try:
            messages = build_legal_messages(
                scope_payload,
                blueprint_payload,
                evidence,
                str(state.get("jurisdiction", "EU")),
                critique=critique if is_repair else None,
                previous_draft=state.get("draft_sow") if is_repair else None,
            )
            response = self.client.complete(
                messages,
                schema=SOWDocument,
                node="legal",
                tier=self.router.tier_for("legal"),
                max_tokens=self.router.max_tokens_for("legal"),
            )
        except (BudgetExceededError, LLMError) as exc:
            return self._halt(state, exc, "legal")

        sow = response.parsed
        if not isinstance(sow, SOWDocument):
            return self._halt(state, LLMError("Legal returned no valid SOWDocument"), "legal", Stage.LEGAL)

        sow.jurisdiction = Jurisdiction.coerce(
            state.get("jurisdiction") or sow.jurisdiction, default=sow.jurisdiction
        )
        duration = (time.perf_counter() - started) * 1000.0
        return {
            "draft_sow": sow.model_dump(mode="json"),
            "evidence": evidence,
            "retrieval_diagnostics": retrieval_diag,
            "stage": Stage.LEGAL.value,
            "status": RunStatus.RUNNING.value,
            "events": [
                _event(
                    "legal",
                    Stage.LEGAL,
                    ("SOW revised" if is_repair else "SOW drafted")
                    + f" — {len(sow.clauses)} clauses",
                    detail=(
                        f"{sow.word_count()} words · {len(sow.retrieved_evidence_ids)} evidence "
                        f"link(s) · {retrieval_diag.get('returned', 0)} passage(s) retrieved under "
                        f"{state.get('jurisdiction', 'EU')} filter"
                    ),
                    duration_ms=duration,
                    response=response,
                    payload={"retrieval": retrieval_diag, "repair": is_repair},
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ critic
    def critic(self, state: GraphState) -> dict[str, Any]:
        """Run the adversarial quality audit."""
        if self._halted(state):
            return {}
        started = time.perf_counter()
        attempt = int(state.get("critique_attempts", 0))

        try:
            messages = build_critic_messages(
                state.get("draft_sow") or {},
                state.get("evidence", ""),
                str(state.get("jurisdiction", "EU")),
                blueprint=state.get("blueprint"),
                attempt=attempt,
            )
            response = self.critic_client.complete(
                messages,
                schema=CritiqueReport,
                node="critic",
                tier=self.router.tier_for("critic"),
                max_tokens=self.router.max_tokens_for("critic"),
            )
        except (BudgetExceededError, LLMError) as exc:
            return self._halt(state, exc, "critic")

        report = response.parsed
        if not isinstance(report, CritiqueReport):
            return self._halt(state, LLMError("Critic returned no valid CritiqueReport"), "critic")

        report.attempt = attempt

        # If the pipeline is running on a cloud provider but the audit fell back to the
        # deterministic engine, the verdict is not a judgement of this contract. The
        # offline critic matches headings against a fixed template: handed a document
        # drafted by a language model, it reports every clause as missing and scores the
        # contract 0.00. That is a failure of the audit, not of the contract, and
        # presenting it as a verdict sends the repair loop chasing defects that do not
        # exist. Record it so the interface can say so.
        configured = str(getattr(self.settings, "resolved_critic_provider", "")).split(".")[-1].lower()
        used = str(getattr(self.critic_client, "provider", "")).lower()
        if used == "offline" and configured not in ("mock", "offline", ""):
            report.degraded = True
            report.degradation_reason = (
                f"the judge ({configured}) was unreachable, so this verdict comes from "
                "the deterministic offline checker, which matches clause headings "
                "against a fixed template. Treat the score and the findings as "
                "unreliable and re-run the audit."
            )
            log.warning(
                "critic.degraded_audit",
                configured=configured,
                findings=len(report.findings),
                score=report.quality_score,
            )

        # Calibrate severity before the verdict is trusted. A judge model is reliable
        # about *what* is wrong and inconsistent about *how much it matters*, so the
        # blocking decision comes from the finding category rather than from the
        # model's own severity label.
        original_blocking = len(report.blocking_findings)
        original_score = report.quality_score
        original_findings = len(report.findings)
        report.findings = [calibrate_severity(f) for f in report.findings]

        # Then cross-check any 'missing clause' claim against the document itself.
        # A judge can report a clause absent when it is present, and a CRITICAL such
        # finding halts the run for nothing.
        draft = _validate_sow(state.get("draft_sow"))
        if draft is not None:
            report.findings, contradicted = verify_missing_clause_findings(
                draft, report.findings
            )
            if contradicted:
                report.dismissed = contradicted
                log.warning("critic.missing_clause_contradicted", notes=contradicted)

        # The verdict AND the score are both recomputed once anything was dismissed.
        # Recomputing only the verdict left the judge's score standing — a score it
        # assigned while believing eleven mandatory clauses were absent — so the run
        # failed on `0.00 >= 0.72` for defects that had just been proven not to exist.
        # The UI could also report "FAILED" beside a findings list containing nothing
        # blocking, which is incoherent on its face.
        if len(report.findings) != original_findings:
            report.raw_quality_score = original_score
            report.quality_score = score_from_findings(report)
            report.passed = not report.blocking_findings and report.quality_score >= 0.72
            log.info(
                "critic.verdict_recomputed",
                node="critic",
                findings_before=original_findings,
                findings_after=len(report.findings),
                blocking_before=original_blocking,
                blocking_after=len(report.blocking_findings),
                score_before=original_score,
                score_after=report.quality_score,
                passed=report.passed,
            )
        elif len(report.blocking_findings) != original_blocking:
            report.passed = not report.blocking_findings and report.quality_score >= 0.72
            log.info(
                "critic.severity_calibrated",
                node="critic",
                before=original_blocking,
                after=len(report.blocking_findings),
                passed=report.passed,
            )

        duration = (time.perf_counter() - started) * 1000.0
        return {
            "critique": report.model_dump(mode="json"),
            "critique_attempts": attempt + 1,
            "stage": Stage.CRITIC.value,
            "status": RunStatus.RUNNING.value,
            "events": [
                _event(
                    "critic",
                    Stage.CRITIC,
                    f"Quality audit {'PASSED' if report.passed else 'FAILED'} — "
                    f"score {report.quality_score:.2f}",
                    detail=(
                        f"grounding {report.grounding_ratio:.0%} · "
                        f"hallucination {report.hallucination_score:.0%} · "
                        f"{len(report.findings)} finding(s), "
                        f"{len(report.blocking_findings)} blocking"
                    ),
                    status="completed" if report.passed else "failed",
                    duration_ms=duration,
                    response=response,
                    payload={
                        "findings": [
                            {
                                "severity": f.severity.value,
                                "category": f.category.value,
                                "location": f.location,
                            }
                            for f in report.findings
                        ]
                    },
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ plan tools
    def plan_tools(self, state: GraphState) -> dict[str, Any]:
        """Ask the model for the function-call batch that provisions the workspace."""
        if self._halted(state):
            return {}
        started = time.perf_counter()

        plan = plan_tool_calls(
            self.client,
            blueprint=state.get("blueprint") or {},
            sow=state.get("draft_sow"),
            registry=self.registry,
            session_id=self.session_id,
        )
        duration = (time.perf_counter() - started) * 1000.0
        preview = self.executor.preview(plan)
        return {
            "tool_calls": [call.model_dump(mode="json") for call in plan.calls],
            "stage": Stage.APPROVAL.value,
            "events": [
                _event(
                    "plan_tools",
                    Stage.APPROVAL,
                    f"Integration plan — {len(plan.calls)} function call(s)",
                    detail=plan.summary or "no calls planned",
                    duration_ms=duration,
                    payload={"preview": preview[:12], "histogram": plan.tool_histogram()},
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ approval
    def approval(self, state: GraphState) -> dict[str, Any]:
        """Human sign-off gate before any external system is touched."""
        if self._halted(state):
            return {}

        from langgraph.types import interrupt

        decision = interrupt(
            {
                "type": "approval",
                "tool_calls": state.get("tool_calls", []),
                "critique": state.get("critique"),
            }
        )
        approved = bool(decision)

        return {
            "approved": approved,
            "stage": Stage.APPROVAL.value,
            "status": RunStatus.RUNNING.value if approved else RunStatus.FAILED.value,
            "halt_reason": "" if approved else "Contract rejected by the reviewer at the approval gate.",
            "events": [
                _event(
                    "approval",
                    Stage.APPROVAL,
                    "Contract approved" if approved else "Contract rejected",
                    detail=(
                        "proceeding to workspace provisioning"
                        if approved
                        else "no external system was modified"
                    ),
                    status="completed" if approved else "failed",
                )
            ],
        }

    # ------------------------------------------------------------------ tools
    def tools(self, state: GraphState) -> dict[str, Any]:
        """Execute the approved function-call batch against Jira/Notion."""
        if self._halted(state):
            return {}
        started = time.perf_counter()

        calls = state.get("tool_calls") or []
        plan = ToolCallPlan.model_validate({"summary": "", "calls": calls})
        report = self.executor.execute(plan)
        duration = (time.perf_counter() - started) * 1000.0

        return {
            "integrations": [report.as_dict()],
            "stage": Stage.TOOLS.value,
            "events": [
                _event(
                    "tools",
                    Stage.TOOLS,
                    f"Workspace provisioning — {report.created_count} call(s) succeeded",
                    detail=report.summary(),
                    status="completed" if report.failed_count == 0 else "failed",
                    duration_ms=duration,
                    payload={
                        "created": [
                            {"tool": r.tool, "key": r.resource_key, "url": r.resource_url}
                            for r in report.created[:20]
                        ],
                        "failed": [
                            {"tool": r.tool, "error": r.error} for r in report.failed[:10]
                        ],
                        "dry_run": report.dry_run,
                    },
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }

    # ------------------------------------------------------------------ finalize
    def finalize(self, state: GraphState) -> dict[str, Any]:
        """Render the deliverables and close the run."""
        if self._halted(state) and str(state.get("status")) != RunStatus.FAILED.value:
            return {}

        started = time.perf_counter()
        artifacts: list[dict[str, Any]] = []
        errors: list[str] = []

        try:
            from ..export.bundle import render_deliverables

            artifacts = render_deliverables(
                session_id=self.session_id,
                run_id=str(state.get("run_id", "run")),
                scope=state.get("scope"),
                blueprint=state.get("blueprint"),
                sow=state.get("draft_sow"),
                critique=state.get("critique"),
                integrations=state.get("integrations"),
                cost=_cost_snapshot(self.session_id),
            )
        except Exception as exc:
            log.error("finalize.export_failed", error=str(exc))
            errors.append(f"[finalize] export failed: {type(exc).__name__}: {exc}")

        duration = (time.perf_counter() - started) * 1000.0
        rejected = str(state.get("status")) == RunStatus.FAILED.value
        return {
            "artifacts": artifacts,
            "stage": Stage.FINALIZE.value,
            "status": RunStatus.FAILED.value if rejected else RunStatus.COMPLETED.value,
            "errors": errors,
            "events": [
                _event(
                    "finalize",
                    Stage.FINALIZE,
                    f"Deliverables rendered — {len(artifacts)} artefact(s)",
                    detail=", ".join(a.get("label", "") for a in artifacts) or "no artefacts",
                    status="completed" if not errors else "failed",
                    duration_ms=duration,
                    payload={"artifacts": artifacts},
                )
            ],
            "cost": _cost_snapshot(self.session_id),
        }


# --------------------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------------------


def _terminal(state: GraphState) -> bool:
    return str(state.get("status", "")) in TERMINAL_STATUSES


def make_routers() -> dict[str, Callable[[GraphState], str]]:
    """Build the conditional-edge functions.

    Kept as a factory so the graph builder and the tests share one definition of the
    transition table rather than duplicating the logic.
    """

    def after_triage(state: GraphState) -> str:
        if _terminal(state):
            return "end"
        if str(state.get("status")) == RunStatus.AWAITING_CLARIFICATION.value:
            return "clarify"
        return "architect"

    def after_clarify(state: GraphState) -> str:
        if _terminal(state):
            return "end"
        if str(state.get("status")) == RunStatus.AWAITING_CLARIFICATION.value:
            return "clarify"
        return "architect"

    def after_architect(state: GraphState) -> str:
        return "end" if _terminal(state) else "legal"

    def after_legal(state: GraphState) -> str:
        return "end" if _terminal(state) else "critic"

    def after_critic(state: GraphState) -> str:
        """Self-correction loop: failed audits bounce back to the responsible agent."""
        if _terminal(state):
            return "end"

        critique = state.get("critique") or {}
        passed = bool(critique.get("passed"))
        attempts = int(state.get("critique_attempts", 0))

        if passed:
            return "plan_tools"

        if attempts <= int(get_settings().max_critic_retries):
            # Route to the agent that owns the most severe blocking finding.
            target = NodeName.LEGAL.value
            for finding in critique.get("findings", []):
                if finding.get("severity") in ("high", "critical"):
                    target = finding.get("target_node", NodeName.LEGAL.value)
                    break
            return "architect" if target == NodeName.ARCHITECT.value else "legal"

        # Repair budget exhausted: surface the residual risk instead of looping forever.
        return "plan_tools"

    def after_plan_tools(state: GraphState) -> str:
        if _terminal(state):
            return "end"
        return "tools" if state.get("auto_deploy") else "approval"

    def after_approval(state: GraphState) -> str:
        if str(state.get("status")) == RunStatus.FAILED.value:
            return "finalize"
        return "tools" if state.get("approved") else "finalize"

    def after_tools(state: GraphState) -> str:
        return "finalize"

    def after_finalize(state: GraphState) -> str:
        return "end"

    return {
        "after_triage": after_triage,
        "after_clarify": after_clarify,
        "after_architect": after_architect,
        "after_legal": after_legal,
        "after_critic": after_critic,
        "after_plan_tools": after_plan_tools,
        "after_approval": after_approval,
        "after_tools": after_tools,
        "after_finalize": after_finalize,
    }


def is_waiting(state: GraphState) -> bool:
    """True when the graph has handed control back to the human."""
    return str(state.get("status", "")) in WAITING_STATUSES

def _validate_sow(payload: Any) -> SOWDocument | None:
    """Re-validate the drafted contract from graph state, tolerating absence."""
    if not payload:
        return None
    try:
        return SOWDocument.model_validate(payload)
    except Exception:
        return None
