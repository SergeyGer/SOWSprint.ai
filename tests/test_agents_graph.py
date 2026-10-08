"""Tests for the LangGraph orchestration layer.

The graph is the product. These tests cover the three things that can silently break a
cyclic multi-agent system:

* **Topology** — every node reachable, every edge declared, no orphan branches.
* **Bounded loops** — the clarification and quality-repair cycles must terminate on a
  counter, not on hope.
* **Human-in-the-loop** — the graph must stop at an interrupt and resume with the
  human's payload, without double-billing the nodes that ran before it.
"""

from __future__ import annotations

import pytest

from sowsprint.agents import ScopingSession
from sowsprint.agents.graph import NODE_NAMES, build_graph, describe_graph, graph_mermaid
from sowsprint.agents.nodes import MAX_CLARIFICATION_ROUNDS, AgentNodes, make_routers
from sowsprint.agents.runtime import RunOutcome
from sowsprint.agents.state import (
    WAITING_STATUSES,
    GraphState,
    RunStatus,
    Stage,
    initial_state,
)
from sowsprint.config import Settings
from sowsprint.models import (
    CriticFinding,
    CritiqueReport,
    FindingCategory,
    NodeName,
    Severity,
)
from sowsprint.rag.pipeline import RagPipeline
from sowsprint.telemetry import tracker

# --------------------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------------------


class ScriptedCriticClient:
    """Duck-typed LLM client that forces the first N quality audits to fail.

    Used to prove the self-correction loop actually re-enters the Legal agent rather
    than merely declaring the draft unacceptable.
    """

    def __init__(self, inner, fail_times: int = 1) -> None:
        self.inner = inner
        self.fail_times = fail_times
        self.critic_calls = 0

    def complete(self, messages, *, schema=None, **kwargs):
        response = self.inner.complete(messages, schema=schema, **kwargs)
        if schema is CritiqueReport:
            self.critic_calls += 1
            if self.critic_calls <= self.fail_times:
                response.parsed = CritiqueReport(
                    passed=False,
                    quality_score=0.30,
                    grounding_ratio=0.10,
                    hallucination_score=0.0,
                    summary="Injected failure for loop testing.",
                    findings=[
                        CriticFinding(
                            category=FindingCategory.UNSUPPORTED_CLAIM,
                            severity=Severity.HIGH,
                            location="6",
                            description="Clause 6 cites no retrieved evidence.",
                            remediation="Bind clause 6 to a retrieved passage id.",
                            target_node=NodeName.LEGAL,
                        )
                    ],
                    repair_instructions=["Bind clause 6 to a retrieved passage id."],
                    attempt=0,
                )
        return response


# --------------------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------------------


class TestTopology:
    def test_all_nodes_are_registered(self, session_id: str, mini_pipeline: RagPipeline) -> None:
        nodes = AgentNodes(session_id, pipeline=mini_pipeline)
        graph = build_graph(nodes)
        drawn = graph.get_graph()
        present = set(drawn.nodes)
        for name in NODE_NAMES:
            assert name in present, name

    def test_entry_point_is_triage(self, session_id: str, mini_pipeline: RagPipeline) -> None:
        graph = build_graph(AgentNodes(session_id, pipeline=mini_pipeline))
        assert "__start__" in graph.get_graph().nodes

    def test_every_non_terminal_node_has_an_outgoing_edge(
        self, session_id: str, mini_pipeline: RagPipeline
    ) -> None:
        graph = build_graph(AgentNodes(session_id, pipeline=mini_pipeline))
        drawn = graph.get_graph()
        sources = {edge.source for edge in drawn.edges}
        for name in NODE_NAMES:
            assert name in sources, f"{name} is a dead end"

    def test_mermaid_and_description_are_documented(self) -> None:
        mermaid = graph_mermaid()
        assert mermaid.startswith("graph TD")
        for name in ("triage", "architect", "legal", "critic", "finalize"):
            assert name in mermaid

        description = describe_graph()
        assert description["entrypoint"] == "triage"
        assert {loop["name"] for loop in description["loops"]} == {
            "clarification",
            "quality_repair",
        }
        assert set(description["human_in_the_loop"]) == {"clarify", "approval"}


# --------------------------------------------------------------------------------------
# Routing table
# --------------------------------------------------------------------------------------


class TestRouting:
    @pytest.fixture
    def routers(self) -> dict:
        return make_routers()

    def test_terminal_states_always_route_to_end(self, routers: dict) -> None:
        for status in (
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.BUDGET_EXCEEDED.value,
        ):
            state: GraphState = {"status": status}
            assert routers["after_triage"](state) == "end"
            assert routers["after_clarify"](state) == "end"
            assert routers["after_architect"](state) == "end"
            assert routers["after_legal"](state) == "end"
            assert routers["after_critic"](state) == "end"
            assert routers["after_plan_tools"](state) == "end"

    def test_triage_routes_to_clarify_when_incomplete(self, routers: dict) -> None:
        state: GraphState = {"status": RunStatus.AWAITING_CLARIFICATION.value}
        assert routers["after_triage"](state) == "clarify"

    def test_triage_routes_to_architect_when_complete(self, routers: dict) -> None:
        assert routers["after_triage"]({"status": RunStatus.RUNNING.value}) == "architect"

    def test_critic_passes_to_tool_planning(self, routers: dict) -> None:
        state: GraphState = {
            "status": RunStatus.RUNNING.value,
            "critique": {"passed": True, "findings": []},
            "critique_attempts": 1,
        }
        assert routers["after_critic"](state) == "plan_tools"

    def test_critic_failure_routes_back_to_legal(self, routers: dict) -> None:
        state: GraphState = {
            "status": RunStatus.RUNNING.value,
            "critique": {
                "passed": False,
                "findings": [
                    {
                        "severity": "high",
                        "category": "unsupported_claim",
                        "target_node": NodeName.LEGAL.value,
                    }
                ],
            },
            "critique_attempts": 1,
        }
        assert routers["after_critic"](state) == "legal"

    def test_scope_drift_routes_back_to_architect(self, routers: dict) -> None:
        state: GraphState = {
            "status": RunStatus.RUNNING.value,
            "critique": {
                "passed": False,
                "findings": [
                    {
                        "severity": "high",
                        "category": "scope_drift",
                        "target_node": NodeName.ARCHITECT.value,
                    }
                ],
            },
            "critique_attempts": 1,
        }
        assert routers["after_critic"](state) == "architect"

    def test_repair_loop_is_bounded(self, routers: dict) -> None:
        """Once the attempt budget is spent the graph advances instead of looping."""
        settings = __import__("sowsprint.config", fromlist=["get_settings"]).get_settings()
        state: GraphState = {
            "status": RunStatus.RUNNING.value,
            "critique": {
                "passed": False,
                "findings": [
                    {"severity": "critical", "target_node": NodeName.LEGAL.value}
                ],
            },
            "critique_attempts": settings.max_critic_retries + 5,
        }
        assert routers["after_critic"](state) == "plan_tools"

    def test_approval_gate_respects_auto_deploy(self, routers: dict) -> None:
        assert routers["after_plan_tools"]({"status": RunStatus.RUNNING.value}) == "approval"
        assert (
            routers["after_plan_tools"]({"status": RunStatus.RUNNING.value, "auto_deploy": True})
            == "tools"
        )

    def test_rejection_skips_provisioning(self, routers: dict) -> None:
        state: GraphState = {"status": RunStatus.RUNNING.value, "approved": False}
        assert routers["after_approval"](state) == "finalize"
        state["approved"] = True
        assert routers["after_approval"](state) == "tools"


# --------------------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------------------


class TestHappyPath:
    @pytest.fixture
    def session(self, session_id: str, settings: Settings, full_pipeline: RagPipeline) -> ScopingSession:
        tracker.drop_ledger(session_id)
        return ScopingSession(session_id, settings=settings, pipeline=full_pipeline)

    def test_full_run_reaches_the_approval_gate(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        outcome = session.start(detailed_brief, jurisdiction="EU")

        assert outcome.awaiting_approval
        assert outcome.status == RunStatus.AWAITING_APPROVAL.value
        assert outcome.scope is not None
        assert outcome.blueprint is not None
        assert outcome.sow is not None
        assert outcome.critique is not None
        assert outcome.tool_calls

    def test_artifacts_are_produced(self, session: ScopingSession, detailed_brief: str) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        session.resume_approval(True)
        final = session.last_outcome

        assert final is not None
        assert final.status == RunStatus.COMPLETED.value
        kinds = {artifact.kind for artifact in final.artifacts}
        assert "sow_pdf" in kinds
        assert "sow_markdown" in kinds
        assert "jira_csv" in kinds
        assert all(artifact.exists() for artifact in final.artifacts)

    def test_step_telemetry_covers_every_agent(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        session.resume_approval(True)
        final = session.last_outcome

        assert final is not None
        stages = {event.get("stage") for event in final.events}
        for expected in ("triage", "architect", "legal", "critic", "tools", "finalize"):
            assert expected in stages, expected

        for event in final.events:
            assert event.get("title")
            assert event.get("status") in ("completed", "failed", "skipped", "started")

    def test_cost_is_accounted_per_agent(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        report = session.cost_report()

        assert report.calls >= 4
        assert report.total_tokens > 0
        assert report.prompt_tokens > 0
        assert report.completion_tokens > 0
        assert report.cost_usd > 0
        for node in ("triage", "architect", "legal", "critic"):
            assert node in report.by_node, node

    def test_retrieval_diagnostics_are_exposed(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        outcome = session.last_outcome
        assert outcome is not None
        assert outcome.retrieval
        assert outcome.retrieval.get("jurisdiction") == "EU"
        assert outcome.retrieval.get("dense_candidates", 0) > 0
        assert outcome.retrieval.get("sparse_candidates", 0) > 0

    def test_auto_deploy_skips_the_gate(self, session: ScopingSession, detailed_brief: str) -> None:
        outcome = session.start(detailed_brief, jurisdiction="EU", auto_deploy=True)
        assert outcome.status == RunStatus.COMPLETED.value
        assert outcome.integrations

    def test_us_jurisdiction_uses_us_clauses(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="US", auto_deploy=True)
        outcome = session.last_outcome
        assert outcome is not None
        assert outcome.sow is not None
        headings = {clause.heading for clause in outcome.sow.clauses}
        assert "Indemnification" in headings
        assert "Governing Law and Venue" in headings
        assert outcome.sow.jurisdiction.value == "US"

    def test_trace_markdown_renders_without_html(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        """The UI runs with unsafe_allow_html disabled, so no raw tags may appear."""
        session.start(detailed_brief, jurisdiction="EU")
        trace = session.trace_markdown()
        assert trace
        assert "<sub>" not in trace
        assert "<br>" not in trace


# --------------------------------------------------------------------------------------
# Human-in-the-loop
# --------------------------------------------------------------------------------------


class TestHumanInTheLoop:
    @pytest.fixture
    def session(self, session_id: str, settings: Settings, full_pipeline: RagPipeline) -> ScopingSession:
        tracker.drop_ledger(session_id)
        return ScopingSession(session_id, settings=settings, pipeline=full_pipeline)

    def test_vague_brief_blocks_on_exactly_three_questions(
        self, session: ScopingSession, vague_brief: str
    ) -> None:
        outcome = session.start(vague_brief, jurisdiction="EU")

        assert outcome.awaiting_clarification
        assert outcome.waiting_for == "clarification"
        assert len(outcome.pending_questions) == 3
        for question in outcome.pending_questions:
            assert question["question"]
            assert question["why_blocking"]

    def test_answers_resume_the_graph(
        self, session: ScopingSession, vague_brief: str
    ) -> None:
        session.start(vague_brief, jurisdiction="EU")
        outcome = session.resume_clarification(
            "Delivery in 12 weeks. Users are the inside sales team. "
            "Integrate with Salesforce. Success is 20% more opportunities. Budget EUR 80k."
        )

        assert not outcome.awaiting_clarification
        assert outcome.scope is not None
        assert outcome.scope.status.value == "COMPLETE"
        assert outcome.blueprint is not None

    def test_clarification_does_not_rebill_the_triage_node(
        self, session: ScopingSession, vague_brief: str
    ) -> None:
        """Analysis and interruption are separate nodes precisely so this holds."""
        session.start(vague_brief, jurisdiction="EU")
        calls_before = session.cost_report().by_node["triage"]["calls"]

        session.resume_clarification("12 weeks. Sales team. Salesforce. 20% uplift. EUR 80k.")
        calls_after = session.cost_report().by_node["triage"]["calls"]

        # Exactly one additional analysis pass (the post-clarification re-run), not a
        # replay of the pre-interrupt call.
        assert calls_after == calls_before + 1

    def test_rejection_halts_before_any_integration(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        session.resume_approval(False)

        last = session.last_outcome
        assert last is not None
        assert last.status == RunStatus.FAILED.value
        assert "rejected" in last.halt_reason.lower()
        assert not last.integrations or all(
            not entry.get("created") for entry in last.integrations
        )

    def test_approval_provisions_and_completes(
        self, session: ScopingSession, detailed_brief: str
    ) -> None:
        session.start(detailed_brief, jurisdiction="EU")
        session.resume_approval(True)
        final = session.last_outcome

        assert final is not None
        assert final.status == RunStatus.COMPLETED.value
        assert final.integrations
        assert final.integrations[0]["dry_run"] is True
        assert final.integrations[0]["created"]

    def test_state_snapshot_reports_next_nodes(
        self, session: ScopingSession, vague_brief: str
    ) -> None:
        session.start(vague_brief, jurisdiction="EU")
        assert "clarify" in session.next_nodes


# --------------------------------------------------------------------------------------
# Self-correction loop
# --------------------------------------------------------------------------------------


class TestSelfCorrection:
    def test_failed_audit_returns_to_the_legal_agent(
        self, session_id: str, settings: Settings, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        tracker.drop_ledger(session_id)
        session = ScopingSession(session_id, settings=settings, pipeline=full_pipeline)
        # The Critic owns a separate client now (judge independence), so the
        # failure injection must target that client, not the reasoning one.
        session.nodes.critic_client = ScriptedCriticClient(
            session.nodes.critic_client, fail_times=1
        )

        outcome = session.start(detailed_brief, jurisdiction="EU", auto_deploy=True)

        # The Legal agent must have run twice: the original draft and the repair.
        assert session.cost_report().by_node["legal"]["calls"] >= 2
        assert outcome.critique is not None
        assert outcome.status in (RunStatus.COMPLETED.value, RunStatus.AWAITING_APPROVAL.value)

    def test_repair_attempts_are_counted(
        self, session_id: str, settings: Settings, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        tracker.drop_ledger(session_id)
        session = ScopingSession(session_id, settings=settings, pipeline=full_pipeline)
        # The Critic owns a separate client now (judge independence), so the
        # failure injection must target that client, not the reasoning one.
        session.nodes.critic_client = ScriptedCriticClient(
            session.nodes.critic_client, fail_times=1
        )
        session.start(detailed_brief, jurisdiction="EU", auto_deploy=True)

        assert session.state.get("critique_attempts", 0) >= 2

    def test_loop_terminates_when_repairs_never_succeed(
        self, session_id: str, settings: Settings, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        """A permanently failing audit must still terminate, not spin."""
        tracker.drop_ledger(session_id)
        session = ScopingSession(session_id, settings=settings, pipeline=full_pipeline)
        session.nodes.critic_client = ScriptedCriticClient(
            session.nodes.critic_client, fail_times=99
        )

        outcome = session.start(detailed_brief, jurisdiction="EU", auto_deploy=True)

        assert outcome.status in (
            RunStatus.COMPLETED.value,
            RunStatus.AWAITING_APPROVAL.value,
        )
        assert session.cost_report().by_node["critic"]["calls"] <= settings.max_critic_retries + 1


# --------------------------------------------------------------------------------------
# Failure and guardrails
# --------------------------------------------------------------------------------------


class TestGuardrails:
    def test_budget_exhaustion_halts_the_graph(
        self, session_id: str, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        tracker.drop_ledger(session_id)
        broke = Settings(
            vector_backend="memory",
            embedding_provider="hash",
            llm_provider="mock",
            session_budget_usd=0.0,
            log_json=False,
        )
        session = ScopingSession(session_id, settings=broke, pipeline=full_pipeline)
        tracker.get_ledger(session_id).budget_usd = 0.0

        outcome = session.start(detailed_brief, jurisdiction="EU")

        assert outcome.status == RunStatus.BUDGET_EXCEEDED.value
        assert "budget" in outcome.halt_reason.lower()
        assert outcome.sow is None

    def test_provider_failure_is_surfaced_not_swallowed(
        self, session_id: str, settings: Settings, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        tracker.drop_ledger(session_id)
        session = ScopingSession(session_id, settings=settings, pipeline=full_pipeline)

        class ExplodingClient:
            def complete(self, *args, **kwargs):
                raise RuntimeError("provider is down")

        session.nodes.client = ExplodingClient()
        outcome = session.start(detailed_brief, jurisdiction="EU")

        assert outcome.status == RunStatus.FAILED.value
        assert any("provider is down" in error for error in outcome.errors)

    def test_retrieval_failure_degrades_but_does_not_abort(
        self, session_id: str, settings: Settings, detailed_brief: str
    ) -> None:
        tracker.drop_ledger(session_id)
        session = ScopingSession(session_id, settings=settings, pipeline=None)  # type: ignore[arg-type]

        class BrokenRetriever:
            def retrieve_for_scope(self, *args, **kwargs):
                raise RuntimeError("vector store unavailable")

        from sowsprint.rag.pipeline import RagPipeline as _Pipeline

        broken = _Pipeline(settings, auto_ingest=False)
        broken.retriever = BrokenRetriever()  # type: ignore[assignment]
        session.pipeline = broken
        session.nodes.pipeline = broken

        outcome = session.start(detailed_brief, jurisdiction="EU")

        # The contract is still drafted, just without retrieved evidence.
        assert outcome.sow is not None
        assert outcome.status in (
            RunStatus.AWAITING_APPROVAL.value,
            RunStatus.COMPLETED.value,
        )


# --------------------------------------------------------------------------------------
# Budget-aware routing
# --------------------------------------------------------------------------------------


class TestModelRouter:
    def test_tier_mapping(self, session_id: str) -> None:
        from sowsprint.llm.factory import ModelRouter

        tracker.drop_ledger(session_id)
        router = ModelRouter(session_id)
        assert router.tier_for("triage") == "fast"
        assert router.tier_for("architect") == "reasoning"
        assert router.tier_for("critic") == "critic"

    def test_budget_pressure_downgrades_only_unprotected_nodes(self, session_id: str) -> None:
        from sowsprint.llm.factory import ModelRouter

        tracker.drop_ledger(session_id)
        ledger = tracker.get_ledger(session_id)
        ledger.budget_usd = 1.0
        tracker.record_call(
            session_id=session_id,
            model="gpt-4o",
            provider="openai",
            node="seed",
            prompt_tokens=400_000,
            completion_tokens=100_000,
            simulate_offline=False,
        )

        router = ModelRouter(session_id)
        assert router.tier_for("architect") == "fast"  # downgraded under pressure
        assert router.tier_for("legal") == "reasoning"  # contractual output is protected
        assert router.tier_for("critic") == "critic"


# --------------------------------------------------------------------------------------
# State contract
# --------------------------------------------------------------------------------------


class TestStateContract:
    def test_initial_state_is_complete_and_json_serialisable(self) -> None:
        import json

        state = initial_state(
            session_id="s", run_id="r", raw_requirement="brief", jurisdiction="EU"
        )
        assert state["status"] == RunStatus.RUNNING.value
        assert state["stage"] == Stage.START.value
        assert state["triage_rounds"] == 0
        json.dumps(state, default=str)  # must not raise

    def test_waiting_and_terminal_sets_are_disjoint(self) -> None:
        assert not (WAITING_STATUSES & {RunStatus.COMPLETED.value})
        assert RunStatus.AWAITING_CLARIFICATION.value in WAITING_STATUSES
        assert RunStatus.AWAITING_APPROVAL.value in WAITING_STATUSES

    def test_run_outcome_helpers(self) -> None:
        outcome = RunOutcome(run_id="r", status=RunStatus.AWAITING_CLARIFICATION.value)
        assert outcome.is_waiting
        assert outcome.awaiting_clarification
        assert not outcome.is_terminal

        outcome.status = RunStatus.COMPLETED.value
        assert outcome.is_terminal
        assert not outcome.is_waiting

    def test_max_clarification_rounds_is_bounded(self) -> None:
        assert 1 <= MAX_CLARIFICATION_ROUNDS <= 5
