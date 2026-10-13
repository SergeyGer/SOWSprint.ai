"""SOWSprint.ai — zero-install demo for Streamlit Community Cloud.

Why this exists
---------------
The application itself is Chainlit, and Streamlit Community Cloud runs only Streamlit
apps: its entry point must import ``streamlit``. That is not a setting to adjust, so the
product cannot be hosted there as-is.

This is a separate, genuine demo rather than a screenshot gallery. It runs the **real
pipeline** — the same graph, the same retrieval, the same drafting and audit code — on the
deterministic offline engine, which needs no API keys, makes no network calls, and costs
nothing. What a visitor sees is actual output, reproducible byte for byte.

Deploying
---------
Streamlit Community Cloud → New app → point it at this repository and set:

    Main file path:  demo/streamlit_app.py

No secrets are required. The application reads its configuration from the environment and
falls back to local adapters when nothing is configured; the sidebar states which adapters
resolved.

Running locally::

    pip install -r requirements.txt streamlit
    SOWSPRINT_LLM_PROVIDER=mock SOWSPRINT_VECTOR_BACKEND=memory \\
        streamlit run demo/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# The engine is a src-layout package. Streamlit Cloud installs the repository but does
# not necessarily install the package itself, so put src/ on the path before importing.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# --------------------------------------------------------------------------------------
# Lock the demo to the offline path.
#
# Deliberately NOT done with environment variables. A public demo must be incapable of
# touching anyone's systems, and environment variables are the wrong tool for a
# guarantee: they interact with the .env file, with init arguments and with whatever the
# hosting platform injects, so the effective configuration is a precedence question
# rather than a fact.
#
# That is not hypothetical here. A test run of this file omitted one variable, .env
# supplied SOWSPRINT_DRY_RUN_INTEGRATIONS=false, and the run created nine real Jira
# issues and a Notion page. The cleanup took longer than the fix.
#
# The values are therefore passed as constructor arguments, which pydantic-settings
# ranks ABOVE environment and .env alike, and build_offline_settings() is the single
# place they are declared.
# --------------------------------------------------------------------------------------
os.environ.setdefault("SOWSPRINT_LOG_LEVEL", "WARNING")

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="SOWSprint.ai — offline demo",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

SAMPLE = """# Project Aurora Data Platform
Nordwind Logistics GmbH (Berlin) requires a customer analytics platform for our
operations organisation.

- Build a React dashboard used by 200 warehouse staff across 6 distribution centres
- Develop a Python FastAPI backend with PostgreSQL as the system of record
- Integrate with Salesforce for account data and SAP for shipment events
- Deliver GDPR-compliant audit logging that satisfies our Data Protection Officer

Budget: EUR 120k. Delivery within 12 weeks. Success means 40% faster monthly close.
The platform processes personal data of EU employees."""


def build_offline_settings():
    """A Settings object that cannot reach a cloud provider or write anywhere.

    ``dry_run_integrations=True`` is the important one. The demo shows what provisioning
    *would* do; it must never do it. A visitor clicking a button in a public app cannot be
    allowed to create issues in the maintainer's Jira.
    """
    from sowsprint.config import Settings

    return Settings(
        llm_provider="mock",             # deterministic engine, no network, no cost
        vector_backend="memory",         # in-process, no Qdrant container
        embedding_provider="hash",       # local feature hashing, no API
        rerank_provider="heuristic",     # no cross-encoder service
        dry_run_integrations=True,       # validate every call, send none
        auth_enabled=False,              # the demo is public by design
        enable_cloud_fallback=False,     # never escalate to a provider mid-run
    )


@st.cache_resource(show_spinner=False)
def _pipeline():
    """Build the retrieval pipeline once per server process.

    Cached because ingestion and BM25 fitting are the slow part, and because rebuilding
    per click would make the demo feel broken rather than fast.
    """
    from sowsprint.rag.pipeline import get_pipeline

    settings = build_offline_settings()
    pipeline = get_pipeline(settings)
    pipeline.ensure_indexed()
    return settings, pipeline


def _run(brief: str, jurisdiction: str):
    """Drive one engagement through the real graph and return the outcome."""
    from sowsprint.agents import ScopingSession
    from sowsprint.telemetry import drop_ledger

    settings, pipeline = _pipeline()
    session_id = f"demo-{int(time.time())}"
    drop_ledger(session_id)
    session = ScopingSession(session_id, settings=settings, pipeline=pipeline)
    return session.start(brief, jurisdiction=jurisdiction, auto_deploy=True)


# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### SOWSprint.ai")
    st.caption("Autonomous multi-agent scoping for B2B engagements")

    st.divider()
    jurisdiction = st.radio(
        "Compliance regime",
        options=["US", "EU", "BOTH"],
        index=0,
        help=(
            "EU — GDPR and the EU AI Act. US — SEC, Delaware and CCPA. "
            "BOTH — one contract bound by both regimes, with an explicit rule for "
            "conflicts between them."
        ),
    )

    st.divider()
    st.markdown("**This demo runs offline**")
    st.caption(
        "The deterministic engine drives the whole pipeline: no API keys, no network "
        "calls, no cost. The same graph, retrieval and audit code runs in production "
        "with cloud models attached."
    )
    try:
        settings, pipeline = _pipeline()
        health = pipeline.health()
        st.caption(f"Indexed passages: **{health.get('chunks', 0)}**")
    except Exception as exc:
        st.caption(f"Index unavailable: `{type(exc).__name__}`")

    st.divider()
    st.caption(
        "The offline auditor is a rule-based checker, not a language model. Its findings "
        "on a contract it drafts itself are sound; on cloud-drafted text it produces "
        "false positives, which the production system detects and labels."
    )

# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
st.title("Turn a client brief into a Statement of Work")
st.markdown(
    "Paste a messy requirement. You get an enforceable contract, a delivery backlog "
    "with testable acceptance criteria, and a compliance audit — in seconds, with "
    "nothing configured."
)

brief = st.text_area("Client brief", value=SAMPLE, height=240)

left, right = st.columns([1, 3])
with left:
    generate = st.button("Generate", type="primary", use_container_width=True)
with right:
    st.caption(
        "Runs the full pipeline: triage → architect → legal → critic → plan. "
        "Around 10–30 seconds."
    )

if generate:
    if not brief.strip():
        st.warning("Paste a brief first.")
        st.stop()

    with st.spinner("Running the agent pipeline…"):
        try:
            outcome = _run(brief, jurisdiction)
        except Exception as exc:
            st.error(f"The pipeline failed: `{type(exc).__name__}: {exc}`")
            st.stop()

    # ---------------------------------------------------------------- status
    status_cols = st.columns(4)
    status_cols[0].metric("Status", str(outcome.status).title())
    if outcome.sow:
        status_cols[1].metric("Clauses", len(outcome.sow.clauses))
        status_cols[2].metric("Words", f"{outcome.sow.word_count():,}")
    if outcome.blueprint:
        status_cols[3].metric(
            "Stories", len(outcome.blueprint.all_stories())
        )

    # ---------------------------------------------------------------- clarification
    if outcome.awaiting_clarification:
        st.warning(
            "**The scope is incomplete.** The Triage agent will not draft a contract over "
            "an unresolved gap — a Statement of Work without enforceable subject matter "
            "is worse than none. It asked the questions below."
        )
        for index, question in enumerate(outcome.pending_questions, start=1):
            with st.expander(f"{index}. {question.get('question', '')}", expanded=True):
                st.markdown(f"*Why it blocks:* {question.get('why_blocking', '')}")
                suggestions = question.get("suggested_answers") or []
                if suggestions:
                    st.markdown("Suggested: " + " · ".join(f"`{s}`" for s in suggestions))
        st.info(
            "In the full application you answer and the run continues from exactly this "
            "point — the graph is checkpointed."
        )
        st.stop()

    # ---------------------------------------------------------------- contract
    tab_sow, tab_plan, tab_audit = st.tabs(
        ["📋 Statement of Work", "📐 Delivery plan", "🛡️ Compliance audit"]
    )

    with tab_sow:
        if outcome.sow:
            st.caption(
                f"Governing law: {outcome.sow.governing_law} · "
                f"jurisdiction: {outcome.sow.jurisdiction.value} · "
                f"evidence links: {len(outcome.sow.retrieved_evidence_ids)}"
            )
            for clause in outcome.sow.clauses:
                with st.expander(f"{clause.number}. {clause.heading}", expanded=False):
                    st.markdown(clause.body)
                    if clause.citations:
                        st.caption("Evidence: " + ", ".join(f"`{c}`" for c in clause.citations))
                    if clause.jurisdiction_tags:
                        st.caption("Regimes: " + ", ".join(clause.jurisdiction_tags))
            if outcome.sow.compliance_notes:
                st.markdown("**Assumptions recorded**")
                for note in outcome.sow.compliance_notes:
                    st.markdown(f"- {note}")
        else:
            st.info("No contract was produced.")

    with tab_plan:
        if outcome.blueprint:
            for milestone in outcome.blueprint.milestones:
                st.markdown(f"#### {milestone.name} · {milestone.duration_weeks} weeks")
                for epic in milestone.epics:
                    st.markdown(f"**{epic.name}**")
                    for story in epic.stories:
                        points = f" · {story.story_points} pts" if story.story_points else ""
                        st.markdown(f"- {story.title}{points}")
                        for criterion in story.acceptance_criteria:
                            st.caption(f"    {criterion}")
        else:
            st.info("No plan was produced.")

    with tab_audit:
        if outcome.critique:
            critique = outcome.critique
            verdict = "PASSED" if critique.passed else "FAILED"
            (st.success if critique.passed else st.error)(
                f"**{verdict}** — score {critique.quality_score:.2f}, "
                f"grounding {critique.grounding_ratio:.0%}, "
                f"{len(critique.findings)} finding(s)"
            )
            if critique.findings:
                for finding in critique.findings:
                    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
                        finding.severity.value, "🔵"
                    )
                    st.markdown(
                        f"{icon} **{finding.severity.value.upper()} · "
                        f"{finding.category.value}** — {finding.location}"
                    )
                    st.markdown(f"&nbsp;&nbsp;&nbsp;{finding.description}")
                    if finding.remediation:
                        st.caption(f"    Remediation: {finding.remediation}")
            else:
                st.markdown("No findings.")
        else:
            st.info("No audit was produced.")

    # ---------------------------------------------------------------- cost
    st.divider()
    if outcome.cost:
        cost = outcome.cost
        st.markdown("##### Session cost")
        st.caption(
            f"${cost.cost_usd:.6f} · {cost.calls} model call(s) · "
            f"{cost.total_tokens:,} tokens — **simulated**, because the offline engine "
            "consumes no billable tokens. A live engagement with cloud models costs "
            "roughly $0.05–0.20."
        )

    with st.expander("Retrieval diagnostics"):
        st.json(outcome.retrieval or {})

st.divider()
st.caption(
    "The full application adds voice capture, live Jira and Notion provisioning behind a "
    "human approval gate, contract revision with locked clauses, and per-agent cost "
    "telemetry. "
    "[Repository](https://github.com/SergeyGer/SOWSprint.ai) · "
    "[Wiki](https://github.com/SergeyGer/SOWSprint.ai/wiki)"
)
