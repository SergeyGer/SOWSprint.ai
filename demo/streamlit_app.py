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

    **Every** provider is pinned, not just the reasoning one. `critic_provider` is separate
    and was left at its default, so with keys present in the environment — which is the case
    on any machine configured to run the real application — the demo silently made billable
    OpenAI calls while the sidebar told the visitor it made none. `whisper_provider` and the
    rest are pinned for the same reason: a demo that behaves differently depending on whose
    machine it runs on is not a demo of anything in particular.
    """
    from sowsprint.config import Settings

    return Settings(
        llm_provider="mock",             # deterministic engine, no network, no cost
        critic_provider="mock",          # the auditor is separate; it must be offline too
        vector_backend="memory",         # in-process, no Qdrant container
        embedding_provider="hash",       # local feature hashing, no API
        rerank_provider="heuristic",     # no cross-encoder service
        whisper_provider="offline",      # no transcription service
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
# Presentation, mirroring the application
# --------------------------------------------------------------------------------------
# The demo used to bury the verdict in the last tab and show four bare metrics above it. The
# application leads with the verdict, states the schedule and the roles, draws the milestones
# and lists the documents — because those are the questions a reader actually has. A demo
# that answers different questions than the product is a demo of a different product.

STATUS_ICONS = {
    "completed": "✅",
    "awaiting_clarification": "❓",
    "awaiting_approval": "⏸",
    "budget_exceeded": "💸",
    "halted": "⛔",
    "failed": "❌",
}


def _verdict(outcome) -> tuple[str, str]:
    """(headline, why) — what the audit decided and what decided it."""
    critique = outcome.critique
    if critique is None:
        return (
            f"{STATUS_ICONS.get(str(outcome.status).split('.')[-1], '•')} NO AUDIT",
            "The run ended before the quality audit, so the artefacts below are drafts and "
            "have not been checked.",
        )

    blocking = len(critique.blocking_findings)
    advisory = len(critique.findings) - blocking
    grounding = critique.grounding_ratio or 0.0

    # Name what actually decided it. The score is the lower of the findings penalty and the
    # grounding cap, and pointing at the wrong one sends the reader to fix the wrong thing.
    from sowsprint.models import ADVISORY_WEIGHT, BLOCKING_WEIGHT, MAX_ADVISORY_PENALTY

    findings_score = 1.0 - BLOCKING_WEIGHT * blocking - min(
        MAX_ADVISORY_PENALTY, ADVISORY_WEIGHT * advisory
    )
    cap = max(0.40, grounding)
    grounding_decided = cap < findings_score - 0.005

    if critique.passed:
        return (
            "✅ AUDIT PASSED",
            "The contract is ready. No blocking finding was raised, and the score cleared "
            "the 0.72 bar.",
        )
    if blocking:
        return (
            "❌ AUDIT FAILED",
            f"**{blocking} blocking finding{'' if blocking == 1 else 's'}** must be resolved "
            "before this contract is safe to sign.",
        )
    if grounding_decided:
        return (
            "❌ AUDIT FAILED",
            f"**The findings are not what failed this.** Only {grounding:.0%} of substantive "
            f"clauses are traceable to retrieved compliance evidence, and that caps the score "
            f"at {cap:.2f} — below the 0.72 bar — however few findings were raised.",
        )
    return (
        "❌ AUDIT FAILED",
        "No single finding blocks signature, but the accumulated advisory findings pushed "
        "the score below the 0.72 bar.",
    )


def _verdict_table(critique) -> None:
    blocking = len(critique.blocking_findings)
    advisory = len(critique.findings) - blocking
    cols = st.columns(4)
    cols[0].metric("Score", f"{critique.quality_score:.2f}", help="Derived from the findings.")
    cols[1].metric(
        "Grounding",
        f"{(critique.grounding_ratio or 0):.0%}",
        help="Substantive clauses whose citations all resolve to retrieved evidence. Counted, "
        "not estimated.",
    )
    cols[2].metric("Blocking", blocking)
    cols[3].metric("Advisory", advisory)


def _project_shape(outcome) -> str:
    """Start, finish, duration and size — a start alone answers half the question."""
    from datetime import date, timedelta

    today = date.today()
    start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    weeks = 0
    stories = points = 0
    if outcome.blueprint is not None:
        weeks = outcome.blueprint.estimated_duration_weeks or sum(
            m.duration_weeks or 0 for m in outcome.blueprint.milestones
        )
        stories = len(outcome.blueprint.all_stories())
        points = outcome.blueprint.estimated_total_points or 0

    if not weeks:
        return f"**Start:** {start:%A, %d %B %Y}"

    from sowsprint.schedule import project_window

    _, finish = project_window(start, weeks)
    size = f"{weeks} weeks"
    if stories:
        size += f" · {stories} stories"
    if points:
        size += f" · {points} points"
    return (
        f"**Start:** {start:%A, %d %B %Y} · **Planned finish:** {finish:%A, %d %B %Y} · "
        f"**Duration:** {size}"
    )


def _resources(outcome) -> str:
    """The roles the plan needs, counted from the stories rather than invented."""
    from collections import Counter

    if outcome.blueprint is None:
        return ""
    roles = Counter(
        (story.as_a or "").strip()
        for story in outcome.blueprint.all_stories()
        if (story.as_a or "").strip()
    )
    if not roles:
        return ""
    listed = ", ".join(f"{role} ({count})" for role, count in roles.most_common())
    return f"**Resources:** {listed} — counted from the roles the stories are written for."


def _gantt(outcome) -> None:
    """The milestones drawn against the calendar, as bars."""
    if outcome.blueprint is None or not outcome.blueprint.milestones:
        return
    from datetime import date, timedelta

    from sowsprint.schedule import milestone_windows

    today = date.today()
    start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    milestones = outcome.blueprint.milestones
    windows = milestone_windows(start, [m.duration_weeks or 1 for m in milestones])
    span = (windows[-1][1] - start).days or 1

    rows = []
    for milestone, (opens, closes) in zip(milestones, windows, strict=False):
        left = 100 * (opens - start).days / span
        width = max(2.0, 100 * ((closes - opens).days + 1) / span)
        rows.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
            f'<div style="width:240px;font-size:0.83rem;flex:0 0 240px">{milestone.name}</div>'
            f'<div style="flex:1;position:relative;height:22px;background:rgba(127,127,127,0.13);'
            f'border-radius:3px">'
            f'<div style="position:absolute;left:{left:.2f}%;width:{width:.2f}%;height:100%;'
            f'background:#2563eb;border-radius:3px"></div></div>'
            f'<div style="width:130px;font-size:0.76rem;opacity:0.7;flex:0 0 130px">'
            f'{milestone.duration_weeks}w · {closes:%d %b}</div></div>'
        )
    st.markdown(
        f'<div style="margin:6px 0 2px">{start:%d %b %Y} → {windows[-1][1]:%d %b %Y}</div>'
        + "".join(rows),
        unsafe_allow_html=True,
    )


def _documents(outcome) -> None:
    """What was produced, in what format, and what each format is for."""
    if not outcome.artifacts:
        st.caption("No documents were produced for this run.")
        return
    purpose = {
        "sow_markdown": "review and diff",
        "sow_pdf": "signing and circulation",
        "backlog_markdown": "review",
        "jira_csv": "import into any tracker",
        "backlog_json": "downstream systems",
        "audit_markdown": "evidence for the verdict above",
        "report_json": "full run record",
        "revision_markdown": "what changed between revisions",
    }
    rows = []
    for artifact in outcome.artifacts:
        path = Path(artifact.path)
        size = f"{path.stat().st_size / 1024:.0f} KB" if path.is_file() else "—"
        rows.append(
            {
                "Document": artifact.kind.replace("_", " ").title(),
                "Format": path.suffix.lstrip(".").upper() or "—",
                "Size": size,
                "Use": purpose.get(artifact.kind, "—"),
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)


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

    # ---------------------------------------------------------------- verdict first
    # Before anything else, because a reader scanning a long run should not have to find it.
    if outcome.critique is not None:
        headline, why = _verdict(outcome)
        st.markdown(f"## {headline}")
        st.markdown(why)
        _verdict_table(outcome.critique)
        st.divider()
    else:
        headline, why = _verdict(outcome)
        st.markdown(f"## {headline}")
        st.caption(why)
        st.divider()

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

    # ---------------------------------------------------------------- shape of the work
    if outcome.blueprint is not None:
        st.markdown(_project_shape(outcome))
        roles = _resources(outcome)
        if roles:
            st.markdown(roles)
        st.markdown("##### Milestones")
        _gantt(outcome)
        st.divider()

    # ---------------------------------------------------------------- documents
    st.markdown("##### Documents produced")
    _documents(outcome)
    st.divider()

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
            st.caption(
                "The verdict is at the top of the page. This is the evidence behind it."
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
