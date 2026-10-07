"""LangGraph state-graph construction.

Topology::

    START → triage ─┬─(INCOMPLETE)─► clarify ─┐
                    │                  ▲      │ (still incomplete)
                    │                  └──────┘
                    └─(COMPLETE)───────────────┐
                                               ▼
                            architect ◄────(scope drift)────┐
                                │                           │
                                ▼                           │
                              legal ◄──(legal findings)─────┤
                                │                           │
                                ▼                           │
                              critic ───────────────────────┘
                                │ (passed / repair budget spent)
                                ▼
                           plan_tools ─(auto_deploy)─► tools ─► finalize ─► END
                                │                                 ▲
                                └─(human gate)─► approval ────────┘

Cycle safety is enforced structurally rather than by hoping: the Critic edge consults
``critique_attempts`` against ``max_critic_retries``, so the ``legal``/``critic`` loop
has a hard bound. The clarification loop is bounded the same way by
``triage_rounds`` against ``MAX_CLARIFICATION_ROUNDS``.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..observability.logging import get_logger
from .nodes import AgentNodes, make_routers
from .state import GraphState

log = get_logger(__name__)

#: Node names as they appear in the compiled graph.
NODE_NAMES = (
    "triage",
    "clarify",
    "architect",
    "legal",
    "critic",
    "plan_tools",
    "approval",
    "tools",
    "finalize",
)


def build_graph(
    nodes: AgentNodes,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the SOWSprint state graph.

    A checkpointer is mandatory because the graph uses ``interrupt()`` for
    human-in-the-loop control; :class:`~langgraph.checkpoint.memory.MemorySaver` is the
    default. Production deployments swap in ``SqliteSaver`` or ``PostgresSaver``
    without touching the topology — see docs/runbook.md.
    """
    routers = make_routers()
    builder: StateGraph = StateGraph(GraphState)

    builder.add_node("triage", nodes.triage)
    builder.add_node("clarify", nodes.clarify)
    builder.add_node("architect", nodes.architect)
    builder.add_node("legal", nodes.legal)
    builder.add_node("critic", nodes.critic)
    builder.add_node("plan_tools", nodes.plan_tools)
    builder.add_node("approval", nodes.approval)
    builder.add_node("tools", nodes.tools)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "triage")

    builder.add_conditional_edges(
        "triage",
        routers["after_triage"],
        {"clarify": "clarify", "architect": "architect", "end": END},
    )
    builder.add_conditional_edges(
        "clarify",
        routers["after_clarify"],
        {"clarify": "clarify", "architect": "architect", "end": END},
    )
    builder.add_conditional_edges(
        "architect",
        routers["after_architect"],
        {"legal": "legal", "end": END},
    )
    builder.add_conditional_edges(
        "legal",
        routers["after_legal"],
        {"critic": "critic", "end": END},
    )
    builder.add_conditional_edges(
        "critic",
        routers["after_critic"],
        {
            "legal": "legal",
            "architect": "architect",
            "plan_tools": "plan_tools",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "plan_tools",
        routers["after_plan_tools"],
        {"approval": "approval", "tools": "tools", "end": END},
    )
    builder.add_conditional_edges(
        "approval",
        routers["after_approval"],
        {"tools": "tools", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "tools",
        routers["after_tools"],
        {"finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "finalize",
        routers["after_finalize"],
        {"end": END},
    )

    compiled = builder.compile(checkpointer=checkpointer or MemorySaver())
    log.info("graph.compiled", nodes=list(NODE_NAMES))
    return compiled


def graph_mermaid() -> str:
    """Return the Mermaid source of the compiled topology for documentation."""
    return """graph TD
    START([START]) --> triage[Triage Agent<br/>requirement parser]
    triage -- INCOMPLETE --> clarify{{Clarification<br/>3 questions}}
    clarify -- still incomplete --> clarify
    clarify -- resolved --> architect
    triage -- COMPLETE --> architect[Architect Agent<br/>technical blueprint]
    architect --> legal[Legal / SOW Agent<br/>compliance-aware RAG]
    legal --> critic{Critic Agent<br/>LLM-as-a-Judge}
    critic -- HIGH/CRITICAL --> legal
    critic -- scope drift --> architect
    critic -- passed --> plan_tools[Tool planner<br/>function calling]
    plan_tools -- auto-deploy --> tools[Jira / Notion<br/>provisioning]
    plan_tools -- human gate --> approval{{Approval gate}}
    approval -- approved --> tools
    approval -- rejected --> finalize
    tools --> finalize[Finalize<br/>SOW PDF + backlog + cost report]
    finalize --> DONE([DONE])
"""


def describe_graph() -> dict[str, Any]:
    """Structured description of the pipeline, used by the UI and the docs build."""
    return {
        "nodes": list(NODE_NAMES),
        "entrypoint": "triage",
        "terminals": ["END"],
        "loops": [
            {
                "name": "clarification",
                "nodes": ["triage", "clarify"],
                "bound": "MAX_CLARIFICATION_ROUNDS",
            },
            {
                "name": "quality_repair",
                "nodes": ["critic", "legal", "architect"],
                "bound": "max_critic_retries",
            },
        ],
        "human_in_the_loop": ["clarify", "approval"],
    }
