"""SOWSprint.ai — Chainlit entry point.

This module is the presentation layer only. It owns no business logic: it renders
graph telemetry, forwards human input back into the LangGraph interrupt points, and
keeps the sticky cost dashboard current.

Responsiveness note: the graph is synchronous and CPU/IO-bound, so it is driven on a
worker thread and its step events are marshalled back onto the event loop through an
``asyncio.Queue``. That keeps the UI painting progress live instead of freezing for the
duration of a multi-agent run.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chainlit as cl

from sowsprint.agents import RunOutcome, RunStatus, ScopingSession
from sowsprint.agents.graph import describe_graph
from sowsprint.config import Settings, get_settings
from sowsprint.llm import LLMError
from sowsprint.observability import configure_logging, get_logger
from sowsprint.rag.pipeline import get_pipeline
from sowsprint.security import Authenticator
from sowsprint.voice import build_audio_file, transcribe_audio

log = get_logger(__name__)
_bootstrap_settings = get_settings()
configure_logging(_bootstrap_settings.log_level, _bootstrap_settings.log_json)

# --------------------------------------------------------------------------------------
# Sample briefs
# --------------------------------------------------------------------------------------

SAMPLE_EU = """# Project Aurora Data Platform
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

SAMPLE_US = """# Helios Claims Automation
Meridian Mutual Insurance (Hartford, Connecticut) needs an automated claims triage system.

- Build a React intake portal for 500 claims adjusters
- Develop a Python FastAPI service with PostgreSQL
- Integrate with Salesforce and Guidewire
- Deliver CCPA-compliant consumer data handling and opt-out support
- Provide automated severity scoring for adjusters

Budget: $480,000. Delivery within 20 weeks. Success means 25% faster claim cycle time.
Processes protected health information. SEC reporting obligations apply to the parent
company. Out of scope: subrogation module."""

SAMPLE_VAGUE = (
    "We need some kind of AI thing for our sales team. Something that helps them work "
    "faster. Not sure about the details yet — can you figure it out?"
)

#: Audio containers accepted for voice scoping (superset of the voice module's list,
#: so an attached recording is always routed through one place).
AUDIO_SUFFIXES = frozenset(
    {".m4a", ".mp3", ".mp4", ".wav", ".webm", ".ogg", ".oga", ".flac", ".mpeg", ".mpga"}
)

#: Maps a graph stage onto a Chainlit step type so the trace is colour-coded.
STEP_TYPES: dict[str, str] = {
    "triage": "llm",
    "clarify": "llm",
    "architect": "llm",
    "legal": "retrieval",
    "critic": "llm",
    "approval": "tool",
    "tools": "tool",
    "finalize": "run",
    "start": "run",
    "done": "run",
}

STATUS_ICONS = {
    RunStatus.AWAITING_CLARIFICATION.value: "❓",
    RunStatus.AWAITING_APPROVAL.value: "🖊️",
    RunStatus.COMPLETED.value: "✅",
    RunStatus.FAILED.value: "🛑",
    RunStatus.BUDGET_EXCEEDED.value: "💸",
    RunStatus.RUNNING.value: "⏳",
}


# --------------------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------------------
# Built once at import so a misconfiguration fails at startup rather than at the first
# login attempt, by which point a half-configured deployment is already serving.
_AUTHENTICATOR: Authenticator | None = None


def _authenticator() -> Authenticator:
    """Process-wide authenticator.

    Deliberately built from :func:`get_settings` rather than the session-cached
    ``_settings()``: the authentication callbacks run on the HTTP routes ``/login``
    and ``/auth/header``, where no Chainlit user session exists yet. Reading the
    session there raised, every credential was rejected, and the failure looked
    exactly like a wrong password.
    """
    global _AUTHENTICATOR
    if _AUTHENTICATOR is None:
        _AUTHENTICATOR = Authenticator.from_settings(get_settings())
    return _AUTHENTICATOR


def _current_account() -> str:
    """Identifier of the signed-in principal, for session naming and log correlation.

    Read from the authenticated user rather than from anything the callbacks wrote:
    ``password_auth_callback`` executes *before* a session exists, so a value stored
    there would be lost.
    """
    try:
        user = cl.user_session.get("user")
        if user is not None and getattr(user, "identifier", None):
            return str(user.identifier)
    except Exception:
        pass
    return "anonymous"


# Chainlit exposes two credential paths and both are needed here:
#   * POST /login       -> password_auth_callback, for people using the interface
#   * POST /auth/header -> header_auth_callback,  for the harnesses and CI
# A machine account authenticates with a key rather than a password, so unattended
# scripts never carry a human's credentials.


@cl.password_auth_callback
async def _password_auth(username: str, password: str) -> cl.User | None:
    account = _authenticator().authenticate_password(username, password)
    if account is None:
        # Never echoed into the transcript: a failed login must not confirm whether
        # the username exists.
        return None
    return _authenticator().to_chainlit_user(account)


@cl.header_auth_callback
async def _header_auth(headers) -> cl.User | None:
    account = _authenticator().authenticate_headers(headers)
    if account is None:
        return None
    return _authenticator().to_chainlit_user(account)


# --------------------------------------------------------------------------------------
# Session helpers
# --------------------------------------------------------------------------------------


def _settings() -> Settings:
    """Settings for the current session, falling back to process settings.

    ``on_app_startup`` and the authentication callbacks run on HTTP routes where no
    Chainlit session exists, and ``cl.user_session`` raises ``LookupError: ContextVar
    'chainlit'`` there. Caching per session is an optimisation, not a requirement, so
    the absence of a session degrades to the process-wide instance rather than
    aborting startup — index warming must not depend on a session that does not exist
    yet.
    """
    try:
        cached = cl.user_session.get("settings")
        if cached is None:
            cached = get_settings()
            cl.user_session.set("settings", cached)
        return cached
    except Exception:
        return get_settings()


def _jurisdiction() -> str:
    """Active compliance regime for this session."""
    return cl.user_session.get("jurisdiction") or _settings().default_jurisdiction


def _session() -> ScopingSession | None:
    return cl.user_session.get("scoping_session")


def _chip(jurisdiction: str) -> str:
    """Render the active compliance regime as a readable label."""
    from sowsprint.models import Jurisdiction

    return {
        Jurisdiction.EU: "🇪🇺 **European Union (Germany)** — GDPR & EU AI Act",
        Jurisdiction.US: "🇺🇸 **United States** — SEC, Delaware & CCPA",
        Jurisdiction.BOTH: (
            "🇪🇺🇺🇸 **Dual regime** — satisfies EU (GDPR & AI Act) *and* "
            "US (SEC, Delaware, CCPA) simultaneously"
        ),
    }[Jurisdiction.coerce(jurisdiction)]


def _capability_block() -> str:
    """Describe which adapter each subsystem resolved to."""
    settings = _settings()
    matrix = settings.capability_matrix()
    pipeline = get_pipeline(settings)
    health = pipeline.health()

    reasoning = matrix["reasoning"]
    reasoning_note = (
        "deterministic offline engine (no credentials required)"
        if reasoning == "mock"
        else f"cloud provider `{reasoning}`"
    )
    return "\n".join(
        [
            "| Subsystem | Resolved adapter |",
            "| :-- | :-- |",
            f"| Reasoning | `{reasoning}` — {reasoning_note} |",
            f"| Embeddings | `{matrix['embeddings']}` |",
            f"| Reranker | `{matrix['reranker']}` |",
            f"| Vector store | `{matrix['vector_store']}` ({health.get('chunks', 0)} chunks indexed) |",
            f"| Speech-to-text | `{matrix['transcription']}` |",
            f"| Jira | {matrix['jira']} |",
            f"| Notion | {matrix['notion']} |",
        ]
    )


def _corpus_block() -> str:
    """Describe the knowledge base the next engagement will draw on.

    Shown at the start of every run, because the retrieved evidence is only as good as
    the corpus behind it and nothing else in the transcript lets a reader judge that.
    """
    from sowsprint.rag.ingest import corpus_composition

    try:
        stats = corpus_composition()
    except Exception as exc:
        log.warning("corpus.composition_unavailable", error=str(exc))
        return ""

    jurisdictions = stats.get("jurisdictions") or {}
    total = stats.get("entries") or 0
    if not total:
        return ""

    def share(count: int) -> str:
        return f"{100 * count / total:.0f}%"

    split = " · ".join(f"**{k}** {v} ({share(v)})" for k, v in jurisdictions.items())
    sources = " · ".join(
        f"{name} {count}" for name, count in (stats.get("sources") or {}).items()
    )
    types = " · ".join(
        f"{name} {count}" for name, count in list((stats.get("doc_types") or {}).items())[:5]
    )
    tags = ", ".join(f"`{t}`" for t in list(stats.get("top_tags") or {})[:8])

    lines = [
        "**Knowledge base** — the evidence every clause is grounded in",
        "",
        "| | |",
        "| :-- | :-- |",
        f"| Passages indexed | **{stats['approx_tokens']:,} tokens** "
        f"({stats['chars']:,} chars) |",
        f"| Entries | {total} "
        f"({stats.get('bundled_entries', total)} bundled · "
        f"{stats.get('drop_in_entries', 0)} from `data/corpus/`) |",
        f"| Jurisdiction | {split} |",
        f"| Sources | {sources} |",
        f"| Document types | {types} |",
    ]
    if tags:
        lines.append(f"| Common topics | {tags} |")
    if jurisdictions.get("US", 0) > 3 * max(1, jurisdictions.get("EU", 1)):
        lines += [
            "",
            "> The corpus is heavily skewed to US precedent: CUAD supplies US commercial "
            "clauses, while EU material is largely regulatory text. EU clauses are "
            "drafted from obligations rather than from precedent wording.",
        ]
    return "\n".join(lines)


async def _update_dashboard(session: ScopingSession | None) -> None:
    """Refresh the sticky cost dashboard in the transcript.

    Rendered as raw HTML rather than a Chainlit ``CustomElement``. The element route
    looked correct from the socket payload — which is what the verification harness
    checked — but never mounted in a real browser: Chainlit 2.12 fetches the ``.jsx``
    verbatim and nothing in the wheel or the runtime image compiles JSX. Raw HTML is
    dependent on ``unsafe_allow_html`` and is verifiable from the DOM.
    """
    report = session.cost_report() if session else None
    if report is None:
        return

    from sowsprint.telemetry.report import render_dashboard_html

    html = render_dashboard_html(report, simulated=_settings().offline_mode)

    dashboard = cl.user_session.get("dashboard_msg")
    if dashboard is None:
        dashboard = cl.Message(content=html)
        cl.user_session.set("dashboard_msg", dashboard)
        await dashboard.send()
    else:
        # Editing in place keeps the card pinned near the top of the transcript instead
        # of appending a new one on every step.
        dashboard.content = html
        await dashboard.update()


#: Agents in the order they typically run, with what each hands to the next.
#: (node, icon, name, what it does). The icon carries the agent's role at a glance —
#: a magnifier for reading a brief, scales for drafting, a shield for auditing.
AGENT_FLOW: list[tuple[str, str, str, str]] = [
    ("triage", "🔍", "Triage", "reads the brief, extracts scope, flags compliance triggers"),
    ("clarify", "❓", "Clarify", "asks for the variables it could not infer"),
    ("architect", "📐", "Architect", "turns scope into milestones, epics, stories"),
    ("legal", "⚖️", "Legal", "drafts the contract from scope and retrieved evidence"),
    ("critic", "🛡️", "Critic", "audits the draft against the compliance corpus"),
    ("approval", "✋", "Approval", "waits for a human before anything is provisioned"),
    ("tools", "🔧", "Tools", "creates the workspace in Jira and Notion"),
    ("finalize", "📦", "Finalize", "renders the deliverables"),
]

#: Stages that may be skipped on a given run, so a progress denominator stays honest.
OPTIONAL_STAGES = {"clarify", "approval"}


def _flow_diagram(visited: dict[str, str]) -> str:
    """Render the agent pipeline, marking what has run and what each stage produced.

    Called again on every completed stage: the diagram is the answer to "what is
    happening right now", and a table frozen at "not reached" answers nothing.
    """
    done = [node for node, _, _, _ in AGENT_FLOW if visited.get(node)]
    required = [node for node, _, _, _ in AGENT_FLOW if node not in OPTIONAL_STAGES]
    finished = sum(1 for node in required if visited.get(node))

    filled = round(12 * finished / max(1, len(required)))
    bar = "█" * filled + "░" * (12 - filled)

    rows = [
        f"**Agent pipeline** — `{bar}` {finished}/{len(required)} stages",
        "",
        "| | Agent | What it does | Outcome |",
        "| :--: | :-- | :-- | :-- |",
    ]
    for node, icon, label, purpose in AGENT_FLOW:
        outcome = visited.get(node)
        if outcome is None:
            mark, detail = "·", "*not reached*"
        elif outcome.startswith("⚠"):
            mark, detail = "⚠️", outcome
        else:
            mark, detail = "✅", outcome
        rows.append(f"| {icon} | **{label}** | {purpose} | {mark} {detail} |")
    if done:
        rows += ["", f"<sub>{len(done)} stage(s) reported · updates as the run proceeds</sub>"]
    return "\n".join(rows)


async def _set_activity(text: str, *, done: bool = False) -> None:
    """Show, and keep updating, what the pipeline is doing right now.

    A run takes one to three minutes. Without a live indicator the interface is
    indistinguishable from a hung one, and the temptation is to send the brief again —
    which starts a second engagement and doubles the cost.
    """
    message = cl.user_session.get("activity_msg")
    content = f"{'✅' if done else '⏳'} {text}"
    if message is None:
        message = cl.Message(content=content, author="SOWSprint")
        cl.user_session.set("activity_msg", message)
        await message.send()
    else:
        message.content = content
        await message.update()


async def _render_step(event: dict[str, Any]) -> None:
    """Render one graph step as a Chainlit step."""
    stage = str(event.get("stage", "start"))
    status = str(event.get("status", "completed"))
    icon = {"completed": "✅", "failed": "⚠️", "skipped": "⏭️", "started": "⏳"}.get(status, "•")

    meta: list[str] = []
    if event.get("duration_ms"):
        meta.append(f"{float(event['duration_ms']):,.0f} ms")
    if event.get("tokens"):
        meta.append(f"{int(event['tokens']):,} tokens")
    if event.get("cost_usd"):
        meta.append(f"${float(event['cost_usd']):.5f}")
    if event.get("model"):
        meta.append(str(event["model"]))

    step = cl.Step(
        name=f"{icon} {event.get('title', event.get('node', 'step'))}",
        type=STEP_TYPES.get(stage, "run"),
        show_input="json" if event.get("payload") else False,
    )
    step.input = event.get("payload") or ""
    step.output = "\n\n".join(
        part
        for part in [
            str(event.get("detail", "") or ""),
            f"*{' · '.join(meta)}*" if meta else "",
        ]
        if part
    )
    async with step:
        pass


async def _drive(
    session: ScopingSession,
    event_iterator: Iterator[dict[str, Any]],
) -> RunOutcome:
    """Consume a synchronous graph stream, rendering steps as they arrive.

    The generator runs on a worker thread; events cross back to the event loop through
    a queue. Without this the Chainlit event loop would block for the whole run and the
    user would see nothing until it finished.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def produce() -> None:
        try:
            for event in event_iterator:
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    producer = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                log.error("ui.stream_error", error=str(item))
                await cl.Message(
                    content=f"⚠️ The agent pipeline failed: `{type(item).__name__}: {item}`"
                ).send()
                break
            await _render_step(item)

            # Keep a running account of what has run, for the flow diagram and the
            # live activity line.
            stage = str(item.get("stage") or "")
            title = str(item.get("title") or "")
            detail = str(item.get("detail") or "")
            status = str(item.get("status") or "completed")
            if stage:
                visited = cl.user_session.get("flow_visited") or {}
                marker = "⚠️ " if status == "failed" else ""
                visited[stage] = f"{marker}{title}" + (f" — {detail}" if detail else "")
                cl.user_session.set("flow_visited", visited)

                # Redraw the pipeline in place. It was rendered once at the start and
                # never updated, so every row read "not reached" for the whole run —
                # the table answered the one question it existed to answer with "no".
                diagram = cl.user_session.get("flow_msg")
                if diagram is not None:
                    diagram.content = _flow_diagram(visited)
                    with contextlib.suppress(Exception):
                        await diagram.update()
                label = {node: name for node, _, name, _ in AGENT_FLOW}.get(stage, stage)
                await _set_activity(
                    f"**{label}** — {title or 'working'}"
                    + (f" · *{detail}*" if detail else ""),
                    done=stage in ("tools", "finalize"),
                )

            if stage in ("legal", "critic", "tools"):
                await _update_dashboard(session)
    finally:
        # Drain the worker before reading the outcome. The result is deliberately
        # discarded — a failure inside it has already been reported through the queue —
        # but the await itself is what guarantees the thread has finished.
        with contextlib.suppress(Exception):
            _ = await producer

    return session.last_outcome or session.snapshot()


# --------------------------------------------------------------------------------------
# Outcome rendering
# --------------------------------------------------------------------------------------


async def _present_outcome(outcome: RunOutcome, *, prefix: str = "") -> None:
    """Render a run outcome: questions, approval prompt, or final deliverables."""
    icon = STATUS_ICONS.get(outcome.status, "•")

    if outcome.awaiting_clarification:
        lines = [
            f"{prefix}### {icon} Three questions before I can draft the contract",
            "",
            "The scope is **INCOMPLETE**. A Statement of Work drafted on these gaps "
            "would have no enforceable subject matter, so I need three answers first:",
            "",
        ]
        for index, question in enumerate(outcome.pending_questions, start=1):
            lines.append(f"**{index}. {question.get('question', '')}**")
            lines.append(f"*Why it blocks: {question.get('why_blocking', '')}*")
            suggestions = question.get("suggested_answers") or []
            if suggestions:
                lines.append("Options: " + " · ".join(f"`{s}`" for s in suggestions))
            lines.append("")

        if outcome.scope:
            lines += [
                "---",
                "",
                f"**What I extracted so far:** {len(outcome.scope.deliverables)} deliverable(s) · "
                f"{len(outcome.scope.compliance_flags)} compliance trigger(s) · "
                f"confidence {outcome.scope.confidence:.0%}",
                "",
            ]
            for deliverable in outcome.scope.deliverables[:6]:
                lines.append(f"- {deliverable}")
            if outcome.scope.compliance_flags:
                lines.append("")
                lines.append("**Regulatory triggers detected:**")
                for flag in outcome.scope.compliance_flags:
                    lines.append(f"- {flag}")

        lines += ["", "_Reply with your answers in one message and I will continue._"]
        await cl.Message(content="\n".join(lines)).send()
        await _update_dashboard(_session())
        return

    if outcome.awaiting_approval:
        lines = [
            f"{prefix}### {icon} Contract ready — approval required",
            "",
            "No external system will be touched until you approve. Here is what I intend "
            "to create:",
            "",
        ]
        if outcome.critique:
            verdict = "PASSED" if outcome.critique.passed else "FAILED"
            lines += [
                f"**Quality audit: {verdict}** — score {outcome.critique.quality_score:.2f}, "
                f"grounding {outcome.critique.grounding_ratio:.0%}, "
                f"{len(outcome.critique.findings)} finding(s).",
                "",
            ]
        lines.append(f"**Planned function calls: {len(outcome.tool_calls)}**")
        lines.append("")
        for call in outcome.tool_calls[:12]:
            arguments = call.get("arguments", {})
            label = (
                arguments.get("summary")
                or arguments.get("title")
                or arguments.get("project_key", "")
            )
            kind = arguments.get("issue_type", "")
            lines.append(f"- `{call.get('tool')}` {f'({kind})' if kind else ''} {label}")
        if len(outcome.tool_calls) > 12:
            lines.append(f"- … and {len(outcome.tool_calls) - 12} more")

        lines += ["", "_Use the buttons below, or reply `approve` / `reject`._"]
        await cl.Message(
            content="\n".join(lines),
            actions=[
                cl.Action(
                    name="approve_contract",
                    payload={"decision": "approve"},
                    label="✅ Approve & provision",
                ),
                cl.Action(
                    name="reject_contract",
                    payload={"decision": "reject"},
                    label="🛑 Reject",
                ),
            ],
        ).send()
        await _update_dashboard(_session())
        return

    # ------------------------------------------------------------------ terminal
    lines = [f"{prefix}### {icon} Run finished — `{outcome.status}`", ""]

    if outcome.errors:
        lines.append("**Errors**")
        lines += [f"- `{error}`" for error in outcome.errors]
        lines.append("")

    if outcome.halt_reason:
        lines += [f"> {outcome.halt_reason}", ""]

    if outcome.sow:
        lines += [
            "**Statement of Work**",
            "",
            f"- Title: {outcome.sow.title}",
            f"- Clauses: {len(outcome.sow.clauses)} ({outcome.sow.word_count():,} words)",
            f"- Governing law: {outcome.sow.governing_law}",
            f"- Payment milestones: {len(outcome.sow.payment_schedule)}",
            f"- Retrieved-evidence links: {len(outcome.sow.retrieved_evidence_ids)}",
            "",
        ]

    if outcome.blueprint:
        lines += [
            "**Delivery plan**",
            "",
            f"- Milestones: {len(outcome.blueprint.milestones)}",
            f"- User stories: {len(outcome.blueprint.all_stories())}",
            f"- Story points: {outcome.blueprint.estimated_total_points}",
            f"- Duration: {outcome.blueprint.estimated_duration_weeks} weeks",
            "",
        ]

    if outcome.critique and getattr(outcome.critique, "degraded", False):
        # Say it before the numbers, not after: a reader who sees "Score 0.00" first
        # concludes the contract is bad, when in fact the audit never ran.
        lines += [
            "> ⚠️ **This audit did not run properly.** "
            + (outcome.critique.degradation_reason or ""),
            "",
            "> Treat the score and every finding below as unreliable, and re-run the "
            "engagement. The contract itself was drafted normally.",
            "",
        ]

    if outcome.critique:
        lines += [
            "**Quality audit**"
            + (" *(degraded — see the warning above)*" if getattr(outcome.critique, "degraded", False) else ""),
            "",
            f"- Verdict: {'PASSED ✅' if outcome.critique.passed else 'FAILED ❌'}",
            f"- Score: {outcome.critique.quality_score:.2f}",
            f"- Grounding: {outcome.critique.grounding_ratio:.0%}",
            f"- Findings: {len(outcome.critique.findings)}",
            "",
        ]
        for finding in outcome.critique.findings[:5]:
            lines.append(
                f"  - `{finding.severity.value}` **{finding.category.value}** "
                f"({finding.location}) — {finding.description}"
            )
        if outcome.critique.findings:
            lines.append("")

    if outcome.retrieval:
        lines += [
            "**Compliance retrieval**",
            "",
            f"- Jurisdiction filter: `{outcome.retrieval.get('jurisdiction')}` "
            "(enforced at the vector-engine query level)",
            f"- Dense candidates: {outcome.retrieval.get('dense_candidates')}",
            f"- Sparse (BM25) candidates: {outcome.retrieval.get('sparse_candidates')}",
            f"- Fused → returned: {outcome.retrieval.get('fused_candidates')} → "
            f"{outcome.retrieval.get('returned')}",
            f"- Retrieval time: {outcome.retrieval.get('total_ms')} ms",
            "",
        ]

    if outcome.integrations:
        # Say plainly where the data went. The previous wording — "38 call(s) succeeded
        # against jira+notion (dry-run)" — reads as success, and a reader reasonably
        # concluded the backlog had been created when nothing had been sent anywhere.
        lines += ["**Where the data went**", "", "| Destination | Result |", "| :-- | :-- |"]
        any_dry = False
        for integration in outcome.integrations:
            target = str(integration.get("target") or "integration")
            dry = bool(integration.get("dry_run"))
            summary = str(integration.get("summary") or "")
            created = integration.get("created") or []
            failed = integration.get("failed") or []
            if dry:
                any_dry = True
                lines.append(
                    f"| {target} | ⚠️ **DRY RUN — nothing was sent.** "
                    f"{len(created)} call(s) validated, {len(failed)} failed validation |"
                )
            elif failed:
                lines.append(f"| {target} | ❌ {len(created)} created, **{len(failed)} failed** |")
            else:
                lines.append(f"| {target} | ✅ **{len(created)} item(s) created** |")
            if summary:
                lines.append(f"| | <sub>{summary}</sub> |")
        lines.append("")

        if any_dry:
            lines += [
                "> **Nothing reached Jira or Notion.** Dry-run is on, so every call was "
                "validated and thrown away. To provision the workspace for real, set "
                "`SOWSPRINT_DRY_RUN_INTEGRATIONS=false` and run the engagement again.",
                "",
            ]

        remote = [
            item
            for integration in outcome.integrations
            for item in (integration.get("created") or [])
            if str(item.get("url", "")).startswith("http")
        ]
        if remote and not any_dry:
            lines += ["**Created in the cloud**", ""]
            for item in remote[:12]:
                label = item.get("key") or item.get("id") or item.get("tool")
                lines.append(f"- [{label}]({item['url']})")
            if len(remote) > 12:
                lines.append(f"- … and {len(remote) - 12} more")
            lines.append("")

    if outcome.artifacts:
        lines.append("**Deliverables** — attached below.")
        lines.append("")

    await cl.Message(content="\n".join(lines)).send()

    for artifact in outcome.artifacts:
        path = Path(artifact.path)
        if not path.is_file():
            continue
        try:
            await cl.Message(
                content=f"📄 **{artifact.label}** · {artifact.size_bytes:,} bytes",
                elements=[
                    cl.File(
                        name=path.name,
                        path=str(path),
                        display="inline",
                        mime=_mime_for(path),
                    )
                ],
            ).send()
        except Exception as exc:
            log.warning("ui.artifact_send_failed", path=str(path), error=str(exc))

    # The sticky dashboard is edited in place near the top of the transcript. That is
    # right while work is in progress — it stays visible — but it means the reader has
    # to scroll back up to learn what the engagement cost. A final card at the end
    # states the totals where the conversation actually finishes.
    session = _session()
    if session is not None:
        from sowsprint.telemetry.report import render_dashboard_html

        report = session.cost_report()
        await cl.Message(
            content="\n".join(
                [
                    "### Session totals",
                    "",
                    render_dashboard_html(report, simulated=_settings().offline_mode),
                ]
            )
        ).send()

    await _update_dashboard(session)


def _mime_for(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".csv": "text/csv",
        ".json": "application/json",
    }.get(path.suffix.lower(), "text/markdown")


# --------------------------------------------------------------------------------------
# Chainlit callbacks
# --------------------------------------------------------------------------------------


@cl.on_app_startup
async def on_app_startup() -> None:
    """Warm the retrieval index, and validate authentication before serving.

    The authenticator is built eagerly here. Constructed lazily — on the first login
    attempt — a deployment with authentication enabled and no accounts starts happily
    and then rejects every credential: the application looks healthy and is unusable,
    which is the worst of both. Failing at boot instead surfaces the exact command
    needed to fix it, in the logs, before anyone tries to sign in.

    Running ingestion here rather than lazily on the first message means the
    container's readiness probe reflects a service that can actually draft a
    contract, instead of one that merely answers HTTP. Ingestion is idempotent, so a
    restart against a populated Qdrant volume is a cheap no-op.
    """
    from sowsprint.security import AuthConfigError

    try:
        _authenticator()
    except AuthConfigError as exc:
        log.error("auth.configuration_invalid", error=str(exc))
        raise
    settings = _settings()
    pipeline = get_pipeline(settings)
    try:
        stats = await asyncio.to_thread(pipeline.ensure_indexed)
        log.info(
            "app.index_ready",
            backend=stats.backend,
            chunks=stats.chunks,
            reused=stats.reused_existing,
        )
    except Exception as exc:
        log.error("app.index_warmup_failed", error=str(exc))


@cl.set_starters
async def set_starters() -> list[cl.Starter]:
    """Offer one-click briefs covering the three interesting pipeline paths."""
    return [
        cl.Starter(
            label="🇪🇺 EU logistics platform",
            message=SAMPLE_EU,
            icon="/public/starter-eu.svg",
        ),
        cl.Starter(
            label="🇺🇸 US insurance claims",
            message=SAMPLE_US,
            icon="/public/starter-us.svg",
        ),
        cl.Starter(
            label="❓ Vague brief (clarification loop)",
            message=SAMPLE_VAGUE,
            icon="/public/starter-vague.svg",
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    """Open a session, publish capabilities and install the cost dashboard."""
    settings = _settings()
    # Namespaced by account: two people on one deployment must not share a transcript,
    # a cost ledger, or a budget.
    session_id = f"{_current_account()}-{uuid.uuid4().hex[:8]}"
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("jurisdiction", settings.default_jurisdiction)

    # Warm the index in the background so the first requirement is not delayed.
    pipeline = get_pipeline(settings)
    try:
        stats = await asyncio.to_thread(pipeline.ensure_indexed)
        index_note = stats.summary()
    except Exception as exc:
        log.error("ui.indexing_failed", error=str(exc))
        index_note = f"indexing failed: {exc}"

    topology = describe_graph()
    await cl.Message(
        content="\n".join(
            [
                "# SOWSprint.ai",
                "",
                "*Autonomous multi-agent scoping: raw B2B requirements → enforceable "
                "Statement of Work + deployable agile backlog.*",
                "",
                f"**Active compliance regime — {_chip(settings.default_jurisdiction)}**",
                "",
                _capability_block(),
                "",
                f"**Compliance corpus:** {index_note}",
                "",
                "---",
                "",
                "**How it works**",
                "",
                f"`{' → '.join(topology['nodes'])}`",
                "",
                "The graph is cyclic: a failed quality audit bounces the contract back to "
                "the drafting agent, and an incomplete scope blocks on three targeted "
                "questions. Both loops are hard-bounded.",
                "",
                "---",
                "",
                "**Send a requirement** as text, or tap the microphone to dictate it. "
                "Switch jurisdiction any time from the settings panel next to the composer.",
                "",
                "Once a contract exists you can negotiate it: `/revise <instruction>` to "
                "change specific clauses, `/lock 6` to freeze the ones already agreed, "
                "`/diff` to see what moved. Send `/help` for the full list.",
            ]
        )
    ).send()

    await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="jurisdiction",
                label="Compliance regime",
                values=["EU", "US", "BOTH"],
                initial_value=settings.default_jurisdiction,
                description=(
                    "EU — GDPR & EU AI Act (German/EU entities). "
                    "US — SEC, Delaware & CCPA. "
                    "BOTH — one contract bound by both regimes, with a "
                    "stricter-standard rule for conflicts. Enforced as a metadata "
                    "filter inside the vector engine, and evidence is drawn from "
                    "each selected regime in equal measure."
                ),
            ),
            cl.input_widget.Slider(
                id="session_budget_usd",
                label="Session budget (USD)",
                initial=settings.session_budget_usd,
                min=0.1,
                max=50.0,
                step=0.1,
                description="The graph halts before a call that would exceed this ceiling.",
            ),
            cl.input_widget.Switch(
                id="dry_run_integrations",
                label="Dry-run Jira / Notion",
                initial=settings.dry_run_integrations,
                description="When on, payloads are validated and shown but never sent.",
            ),
            cl.input_widget.Switch(
                id="auto_deploy",
                label="Skip the approval gate",
                initial=False,
                description="Provision the tracker as soon as the audit passes.",
            ),
        ]
    ).send()

    await _update_dashboard(None)


@cl.on_settings_update
async def on_settings_update(settings: dict[str, Any]) -> None:
    """Apply UI settings changes to the live session configuration."""
    from sowsprint.models import Jurisdiction

    jurisdiction = Jurisdiction.coerce(settings.get("jurisdiction", "EU")).value
    cl.user_session.set("jurisdiction", jurisdiction)
    cl.user_session.set("auto_deploy", bool(settings.get("auto_deploy", False)))
    cl.user_session.set("dry_run_integrations", bool(settings.get("dry_run_integrations", True)))

    budget = float(settings.get("session_budget_usd", _settings().session_budget_usd))
    existing = _session()
    if existing is not None:
        from sowsprint.telemetry import get_ledger

        get_ledger(existing.session_id).budget_usd = budget

    await cl.Message(
        content=(
            f"⚙️ Settings applied — {_chip(jurisdiction)}, "
            f"budget **${budget:.2f}**"
            + (", integrations **dry-run**" if settings.get("dry_run_integrations") else ", integrations **live**")
            + ("." if not settings.get("auto_deploy") else ", approval gate **skipped**.")
        )
    ).send()


async def _transcribe(path: Path) -> str:
    """Transcribe an audio file attached to a chat message.

    Returns the transcript, or an empty string when transcription is unavailable, so
    the caller falls back to any typed text instead of failing the whole message.
    """
    result = await asyncio.to_thread(transcribe_audio, path, settings=_settings())

    if not result.ok:
        await cl.Message(
            content="\n".join(
                [
                    "⚠️ **Could not transcribe the attached audio**",
                    "",
                    f"`{result.error}`",
                    "",
                    "_Any text you typed in the same message was still processed._",
                ]
            )
        ).send()
        return ""

    await cl.Message(
        content="\n".join(
            [
                f"📝 **Transcript** — {result.summary()}",
                "",
                "> " + result.text.replace("\n", "\n> "),
            ]
        )
    ).send()
    return result.text


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Route user input: new requirement, clarification answers, or approval decision."""
    text = (message.content or "").strip()

    # A file upload may carry an audio requirement.
    for element in message.elements or []:
        if isinstance(element, cl.File) and Path(str(element.path)).suffix.lower() in AUDIO_SUFFIXES:
            transcript = await _transcribe(Path(str(element.path)))
            if transcript:
                text = f"{text}\n{transcript}".strip()

    if not text:
        await cl.Message(content="Please describe the engagement, or attach an audio brief.").send()
        return

    session = _session()

    # ---------------------------------------------------------- commands
    if session is not None and session.last_outcome is not None:
        outcome = session.last_outcome
        has_contract = outcome.sow is not None

        if text.strip().casefold() in ("/help", "/commands"):
            await cl.Message(content=REVISION_HELP).send()
            return

        lock_command = _parse_lock_command(text)
        if lock_command and has_contract:
            verb, targets = lock_command
            if not targets:
                await cl.Message(content=REVISION_HELP).send()
                return
            changed = session.set_locks(targets, locked=(verb == "lock"))
            if changed:
                await cl.Message(
                    content=(
                        f"{'🔒 Locked' if verb == 'lock' else '🔓 Unlocked'}: "
                        + ", ".join(f"`{n}`" for n in changed)
                    )
                ).send()
            else:
                await cl.Message(
                    content=f"Nothing to change — clauses {targets} were not found "
                    "or already had that state. Try `/clauses`."
                ).send()
            await _update_dashboard(session)
            return

        if text.strip().casefold().startswith("/clauses") and has_contract:
            rows = ["| # | Clause | State |", "| --: | :-- | :-- |"]
            for clause in outcome.sow.clauses:
                rows.append(
                    f"| {clause.number} | {clause.heading} | "
                    f"{'🔒 locked' if clause.locked else 'editable'} |"
                )
            await cl.Message(content="\n".join(rows)).send()
            return

        if text.strip().casefold().startswith("/revise") and has_contract:
            instruction = text.strip()[len("/revise") :].strip()
            if not instruction:
                await cl.Message(content=REVISION_HELP).send()
                return
            await _run_revision(session, instruction)
            return

        if text.strip().casefold().startswith("/diff") and has_contract:
            last = cl.user_session.get("last_diff")
            await cl.Message(
                content=last
                or "No revision yet in this session. Use `/revise <instruction>` first."
            ).send()
            return

        if outcome.awaiting_clarification:
            await cl.Message(
                content="📥 Answers received — folding them into the scope."
            ).send()
            result = await _drive(session, session.resume_clarification_stream(text))
            await _present_outcome(result)
            return

        if outcome.awaiting_approval:
            decision = _parse_decision(text)
            if decision is None:
                # Anything that is not a verdict is read as a change request. This is
                # the natural place to negotiate: the reviewer is looking at the
                # contract and wants something different before signing.
                await _run_revision(session, text.strip())
                return
            await _provision(session, decision)
            return

    # ---------------------------------------------------------- new engagement
    await _start_run(text)


#: Slash commands understood after a contract exists.
REVISION_HELP = """**Contract commands**

| Command | Effect |
| :-- | :-- |
| `/revise <instruction>` | Change the contract, e.g. `/revise make the liability cap mutual` |
| `/lock 6 7` | Freeze clauses 6 and 7 — revision returns them byte-identical |
| `/unlock 6` | Release a clause |
| `/diff` | Show what changed in the last revision |
| `/clauses` | List clauses with their lock state |
"""


def _parse_lock_command(text: str) -> tuple[str, list[str]] | None:
    """Parse ``/lock 6 7`` or ``/lock Liability`` into (verb, targets)."""
    stripped = text.strip()
    for verb in ("lock", "unlock"):
        prefix = f"/{verb}"
        if stripped.casefold().startswith(prefix):
            targets = stripped[len(prefix) :].replace(",", " ").split()
            return verb, targets
    return None


def _parse_decision(text: str) -> bool | None:
    """Interpret a free-text approval decision."""
    lowered = text.strip().casefold()
    affirmative = {"approve", "approved", "yes", "y", "ok", "go", "proceed", "confirm", "✅"}
    negative = {"reject", "rejected", "no", "n", "stop", "cancel", "🛑"}
    if lowered in affirmative or lowered.startswith(("approve", "yes", "proceed")):
        return True
    if lowered in negative or lowered.startswith(("reject", "no ", "cancel", "stop")):
        return False
    return None


async def _start_run(requirement: str) -> None:
    """Kick off a fresh engagement."""
    settings = _settings()
    jurisdiction = _jurisdiction()
    session_id = cl.user_session.get("session_id") or f"{_current_account()}-{uuid.uuid4().hex[:8]}"
    auto_deploy = bool(cl.user_session.get("auto_deploy", False))

    if cl.user_session.get("dry_run_integrations") is not None:
        settings = settings.model_copy(
            update={"dry_run_integrations": bool(cl.user_session.get("dry_run_integrations"))}
        )

    from sowsprint.telemetry import drop_ledger, get_ledger

    drop_ledger(session_id)
    get_ledger(session_id).budget_usd = settings.session_budget_usd

    pipeline = get_pipeline(settings)
    session = ScopingSession(session_id, settings=settings, pipeline=pipeline)
    cl.user_session.set("scoping_session", session)
    cl.user_session.set("dashboard_msg", None)
    cl.user_session.set("activity_msg", None)
    cl.user_session.set("flow_visited", {})
    cl.user_session.set("flow_msg", None)

    opening = cl.Message(
        content="\n".join(
            [
                f"## 🚀 New engagement — {_chip(jurisdiction)}",
                "",
                f"*{len(requirement):,} characters received. Starting the agent graph…*",
                "",
                f"`run_id: {session.run_id}`",
                "",
                _corpus_block(),
                "",
                "**Agent pipeline** — this is what will run:",
                "",
                _flow_diagram({}),
            ]
        )
    )
    await opening.send()
    # Held so the pipeline table can be redrawn in place; without this it renders once
    # with every row "not reached" and never changes.
    cl.user_session.set("flow_msg", opening)

    try:
        result = await _drive(
            session,
            session.start_stream(requirement, jurisdiction=jurisdiction, auto_deploy=auto_deploy),
        )
    except LLMError as exc:
        await cl.Message(content=f"⚠️ Provider error: `{exc}`").send()
        return

    await _present_outcome(result)


async def _run_revision(session: ScopingSession, instruction: str) -> None:
    """Apply a targeted change and report what moved."""
    await cl.Message(
        content=f"✏️ **Revising** — *{instruction}*\n\nOnly the affected clauses will change."
    ).send()

    try:
        result = await _drive(session, session.revise(instruction))
    except Exception as exc:
        await cl.Message(content=f"⚠️ Revision failed: `{type(exc).__name__}: {exc}`").send()
        return

    # A diff is the point of a targeted edit: show what moved and what was held.
    from sowsprint.export.sow_markdown import render_sow_diff
    from sowsprint.models import SOWDocument, diff_sow

    previous = cl.user_session.get("previous_sow")
    if isinstance(previous, SOWDocument) and result.sow is not None:
        diff = diff_sow(previous, result.sow)
        rendered = render_sow_diff(diff)
        cl.user_session.set("last_diff", rendered)
        await cl.Message(content=rendered).send()
    if result.sow is not None:
        cl.user_session.set("previous_sow", result.sow)

    if result.sow is not None:
        cl.user_session.set("previous_sow", result.sow)

    await _present_outcome(result)


async def _provision(session: ScopingSession, approved: bool) -> None:
    """Resume the graph past the approval gate."""
    verb = "✅ Approved" if approved else "🛑 Rejected"
    await cl.Message(
        content=f"{verb} — {'provisioning the delivery workspace…' if approved else 'no external system was modified.'}"
    ).send()
    result = await _drive(session, session.resume_approval_stream(approved))
    await _present_outcome(result)


# --------------------------------------------------------------------------------------
# Action callbacks
# --------------------------------------------------------------------------------------


@cl.action_callback("approve_contract")
async def on_approve(action: cl.Action) -> None:
    session = _session()
    if session is None:
        return
    await action.remove()
    await _provision(session, True)


@cl.action_callback("reject_contract")
async def on_reject(action: cl.Action) -> None:
    session = _session()
    if session is None:
        return
    await action.remove()
    await _provision(session, False)


# --------------------------------------------------------------------------------------
# Voice scoping
# --------------------------------------------------------------------------------------


@cl.on_audio_start
async def on_audio_start() -> bool:
    """Accept an audio stream from the browser's native capture widget."""
    cl.user_session.set("audio_chunks", [])
    await cl.Message(
        content="🎙️ *Listening… describe the engagement, then stop the recording.*"
    ).send()
    return True


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.AudioChunk) -> None:
    """Accumulate raw PCM/encoded chunks for the duration of the recording."""
    if chunk.isStart:
        cl.user_session.set("audio_mime", chunk.mimeType)
    buffer = cl.user_session.get("audio_chunks") or []
    buffer.append(chunk.data)
    cl.user_session.set("audio_chunks", buffer)


@cl.on_audio_end
async def on_audio_end() -> None:
    """Persist the recording, transcribe it and feed the text into the graph.

    Chainlit 2.x invokes this hook with **no arguments** (``socket.py``:
    ``await config.code.on_audio_end()``) and its wrapper binds positional arguments to
    parameter names, so any parameter here raises ``TypeError`` at runtime. The chunks
    are therefore read from the user session rather than passed in.
    """
    buffer: list[bytes] = cl.user_session.get("audio_chunks") or []
    if not buffer:
        await cl.Message(content="No audio was captured — please try again.").send()
        return

    settings = _settings()
    mime_type = str(cl.user_session.get("audio_mime") or "audio/wav")

    # The capture widget streams headerless PCM; wrap it before anything tries to
    # parse it as a media file.
    payload, suffix = build_audio_file(
        buffer, mime_type=mime_type, sample_rate=settings.audio_sample_rate
    )
    if not payload:
        await cl.Message(content="No audio was captured — please try again.").send()
        return

    target = Path(tempfile.gettempdir()) / f"sowsprint-voice-{uuid.uuid4().hex[:8]}{suffix}"
    target.write_bytes(payload)
    cl.user_session.set("audio_chunks", [])

    provider = settings.resolved_whisper_provider
    await cl.Message(
        content=(
            f"🎧 **{len(payload) / 1024:.0f} KB** of audio captured — transcribing via "
            f"`{provider}`…"
        )
    ).send()

    result = await asyncio.to_thread(transcribe_audio, target, settings=settings)

    if not result.ok:
        await cl.Message(
            content="\n".join(
                [
                    "⚠️ **Voice transcription unavailable**",
                    "",
                    f"`{result.error}`",
                    "",
                    "The requirement text never reached the agents, so nothing was scoped. "
                    "You can type the brief instead, or configure a Whisper provider and "
                    "retry.",
                ]
            )
        ).send()
        return

    await cl.Message(
        content="\n".join(
            [
                f"📝 **Transcript** — {result.summary()}",
                "",
                "> " + result.text.replace("\n", "\n> "),
                "",
                "_Feeding this into the Triage agent…_",
            ]
        )
    ).send()

    await _start_run(result.text)
