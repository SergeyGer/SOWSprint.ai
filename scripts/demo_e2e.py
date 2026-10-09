#!/usr/bin/env python
"""Headless end-to-end demonstration of the SOWSprint pipeline.

Runs the complete scoping engagement without Chainlit, exercising every stage:
Triage → (clarification loop) → Architect → Legal/RAG → Critic → (repair loop) →
tool planning → approval gate → Jira/Notion provisioning → deliverable rendering.

This is the script CI uses as a smoke test, and the one to reach for when you want to
see the whole pipeline's behaviour in a terminal.

Usage::

    python scripts/demo_e2e.py                      # offline, in-memory, no credentials
    python scripts/demo_e2e.py --jurisdiction US
    python scripts/demo_e2e.py --vague              # forces the clarification loop
    python scripts/demo_e2e.py --qdrant             # uses a running Qdrant container
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# --- rich sample requirements -------------------------------------------------------

DETAILED_EU = """# Project Aurora Data Platform
Nordwind Logistics GmbH (Berlin) requires a customer analytics platform for our
operations organisation.

- Build a React dashboard used by 200 warehouse staff across 6 distribution centres
- Develop a Python FastAPI backend with PostgreSQL as the system of record
- Integrate with Salesforce for account data and SAP for shipment events
- Deliver GDPR-compliant audit logging that satisfies our Data Protection Officer
- Provide demand forecasting (the EU AI Act applies to this component)

Budget: EUR 120k. Delivery within 12 weeks. Success means 40% faster monthly close.
The platform processes personal data of EU employees.
Out of scope: mobile application and phase 2 reporting."""

DETAILED_US = """# Helios Claims Automation
Meridian Mutual Insurance (Hartford, Connecticut) needs an automated claims triage system.

- Build a React intake portal for 500 claims adjusters
- Develop a Python FastAPI service with PostgreSQL
- Integrate with Salesforce and Guidewire
- Deliver CCPA-compliant consumer data handling and opt-out support
- Provide automated severity scoring for adjusters

Budget: $480,000. Delivery within 20 weeks. Success means 25% faster claim cycle time.
Processes protected health information. SEC reporting obligations apply to the parent
company. Out of scope: subrogation module."""

VAGUE = """We need some kind of AI thing for our sales team. Something that helps them
work faster. Not sure about the details yet — can you figure it out?"""


def banner(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def show_events(outcome, since: int = 0) -> int:
    """Print new step events and return the new cursor."""
    events = outcome.events
    icons = {"completed": "✅", "failed": "⚠️", "skipped": "⏭️"}
    for index, event in enumerate(events[since:], start=since + 1):
        icon = icons.get(str(event.get("status")), "•")
        meta = []
        if event.get("duration_ms"):
            meta.append(f"{event['duration_ms']:.0f}ms")
        if event.get("tokens"):
            meta.append(f"{event['tokens']:,}tok")
        if event.get("cost_usd"):
            meta.append(f"${event['cost_usd']:.5f}")
        suffix = f"  [{' · '.join(meta)}]" if meta else ""
        print(f"  {icon} {index}. {event.get('title')}{suffix}")
        if event.get("detail"):
            print(f"       {event['detail']}")
    return len(events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SOWSprint.ai end-to-end demonstration")
    parser.add_argument("--jurisdiction", choices=["EU", "US", "BOTH"], default="EU")
    parser.add_argument("--vague", action="store_true", help="Use a deliberately vague brief.")
    parser.add_argument("--us-scenario", action="store_true", help="Use the US sample brief.")
    parser.add_argument("--qdrant", action="store_true", help="Use Qdrant instead of in-memory.")
    parser.add_argument("--auto-deploy", action="store_true", help="Skip the approval gate.")
    parser.add_argument("--reject", action="store_true", help="Reject at the approval gate.")
    args = parser.parse_args(argv)

    from sowsprint.agents import ScopingSession
    from sowsprint.config import Settings, VectorBackend
    from sowsprint.llm import OFFLINE_MODEL
    from sowsprint.rag.pipeline import RagPipeline
    from sowsprint.telemetry import render_plain

    settings = Settings(
        vector_backend=VectorBackend.QDRANT if args.qdrant else VectorBackend.MEMORY,
        embedding_dim=768,
        dry_run_integrations=True,
        log_json=False,
    )

    banner("SOWSprint.ai — autonomous B2B scoping-to-contract pipeline")
    print(f"  reasoning engine : {settings.resolved_llm_provider.value}")
    print(f"  embeddings       : {settings.resolved_embedding_provider.value}")
    print(f"  reranker         : {settings.resolved_rerank_provider.value}")
    print(f"  vector backend   : {settings.vector_backend.value}")

    banner("Stage 0 — compliance corpus ingestion")
    pipeline = RagPipeline(settings)
    stats = pipeline.ensure_indexed()
    print(f"  {stats.summary()}")
    health = pipeline.health()
    print(f"  index health: {health}")

    requirement = VAGUE if args.vague else (DETAILED_US if args.us_scenario else DETAILED_EU)
    jurisdiction = args.jurisdiction

    session = ScopingSession("demo-session", settings=settings, pipeline=pipeline)
    banner(f"Stage 1 — Triage ({jurisdiction})")
    outcome = session.start(requirement, jurisdiction=jurisdiction, auto_deploy=args.auto_deploy)
    cursor = show_events(outcome)

    # ---------------------------------------------------------------- clarify loop
    rounds = 0
    while outcome.awaiting_clarification and rounds < 3:
        rounds += 1
        banner(f"Human-in-the-loop — clarification round {rounds}")
        for question in outcome.pending_questions:
            print(f"  ❓ {question['question']}")
            print(f"     why blocking: {question['why_blocking']}")
            if question.get("suggested_answers"):
                print(f"     options: {', '.join(question['suggested_answers'])}")

        answers = (
            "1. Delivery window is 12 weeks.\n"
            "2. Primary users are the 60-person inside sales team and sales managers.\n"
            "3. Must integrate with Salesforce and HubSpot; no other systems.\n"
            "4. Success is measured as 20% more qualified opportunities per rep.\n"
            "5. Budget envelope is EUR 80k."
        )
        print("\n  ▶ simulated customer answer:")
        for line in answers.splitlines():
            print(f"      {line}")
        outcome = session.resume_clarification(answers)
        cursor = show_events(outcome, cursor)

    # ---------------------------------------------------------------- approval gate
    if outcome.awaiting_approval:
        banner("Human-in-the-loop — approval gate")
        print(f"  planned function calls: {len(outcome.tool_calls)}")
        for call in outcome.tool_calls[:8]:
            name = call.get("tool")
            payload = call.get("arguments", {})
            label = payload.get("summary") or payload.get("title") or payload.get("project_key", "")
            print(f"    → {name}: {label}")
        if len(outcome.tool_calls) > 8:
            print(f"    … and {len(outcome.tool_calls) - 8} more")

        approved = not args.reject
        print(f"\n  ▶ reviewer decision: {'APPROVE' if approved else 'REJECT'}")
        outcome = session.resume_approval(approved)
        # No assignment: this is the last call, so the returned cursor would be unused.
        show_events(outcome, cursor)

    # ---------------------------------------------------------------- results
    banner("Final deliverables")
    if outcome.sow:
        print(f"  SOW        : {outcome.sow.title}")
        print(f"  clauses    : {len(outcome.sow.clauses)} ({outcome.sow.word_count():,} words)")
        print(f"  payments   : {len(outcome.sow.payment_schedule)} milestone(s)")
        print(f"  evidence   : {len(outcome.sow.retrieved_evidence_ids)} retrieved link(s)")
    if outcome.blueprint:
        print(
            f"  backlog    : {len(outcome.blueprint.milestones)} milestones · "
            f"{len(outcome.blueprint.all_stories())} stories · "
            f"{outcome.blueprint.estimated_total_points} points"
        )
    if outcome.critique:
        verdict = "PASSED" if outcome.critique.passed else "FAILED"
        print(
            f"  audit      : {verdict} (score {outcome.critique.quality_score:.2f}, "
            f"grounding {outcome.critique.grounding_ratio:.0%})"
        )
    if outcome.retrieval:
        print(
            f"  retrieval  : {outcome.retrieval.get('returned')} passages under "
            f"jurisdiction={outcome.retrieval.get('jurisdiction')} "
            f"filter (dense {outcome.retrieval.get('dense_candidates')} / "
            f"sparse {outcome.retrieval.get('sparse_candidates')} candidates)"
        )
    if outcome.integrations:
        for integration in outcome.integrations:
            print(f"  integrations: {integration.get('summary')}")

    print("\n  artefacts:")
    for artifact in outcome.artifacts:
        print(f"    📄 {artifact.label}: {artifact.path} ({artifact.size_bytes:,} bytes)")

    if outcome.errors:
        print("\n  errors:")
        for error in outcome.errors:
            print(f"    ⚠️ {error}")

    banner("AI financial observability")
    report = session.cost_report()
    print(render_plain(report))
    print("\n  cost by agent:")
    for node, node_stats in sorted(
        report.by_node.items(), key=lambda kv: -kv[1].get("cost_usd", 0.0)
    ):
        print(
            f"    {node:<12} {int(node_stats.get('calls', 0)):>3} call(s)  "
            f"{int(node_stats.get('tokens', 0)):>7,} tok  "
            f"${node_stats.get('cost_usd', 0.0):.5f}"
        )

    banner(f"Final status: {outcome.status.upper()}")
    if outcome.halt_reason:
        print(f"  {outcome.halt_reason}")

    ok = outcome.status in ("completed", "failed") and outcome.sow is not None
    print(f"\n{'✅ pipeline completed successfully' if ok else '❌ pipeline did not complete'}")
    _ = OFFLINE_MODEL  # referenced for documentation completeness
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
