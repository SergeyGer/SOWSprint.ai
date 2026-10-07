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
from datetime import datetime
from pathlib import Path
from typing import Any

import chainlit as cl

from sowsprint.agents import RunOutcome, RunStatus, ScopingSession
from sowsprint.agents.graph import describe_graph
from sowsprint.config import Settings, get_settings
from sowsprint.llm import LLMError
from sowsprint.observability import configure_logging, get_logger
from sowsprint.rag.pipeline import get_pipeline
from sowsprint.telemetry import render_dashboard
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
# Session helpers
# --------------------------------------------------------------------------------------


def _settings() -> Settings:
    cached = cl.user_session.get("settings")
    if cached is None:
        cached = get_settings()
        cl.user_session.set("settings", cached)
    return cached


def _jurisdiction() -> str:
    return cl.user_session.get("jurisdiction") or _settings().default_jurisdiction


def _session() -> ScopingSession | None:
    return cl.user_session.get("scoping_session")


def _chip(jurisdiction: str) -> str:
    """Render the active compliance regime as a readable label."""
    if jurisdiction == "US":
        return "🇺🇸 **United States** — SEC & Delaware corporate law"
    return "🇪🇺 **European Union** — GDPR & EU AI Act"


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


async def _update_dashboard(session: ScopingSession | None) -> None:
    """Refresh the sticky cost widget, falling back to Markdown if it will not render."""
    report = session.cost_report() if session else None
    if report is None:
        return

    props = {
        "sessionId": report.session_id,
        "costUsd": round(report.cost_usd, 6),
        "budgetUsd": report.budget_usd,
        "promptTokens": report.prompt_tokens,
        "completionTokens": report.completion_tokens,
        "totalTokens": report.total_tokens,
        "calls": report.calls,
        "avgLatencyMs": round(report.avg_latency_ms, 1),
        "budgetUsedPct": round(report.budget_used_pct, 2),
        "byNode": {k: dict(v) for k, v in report.by_node.items()},
        "simulated": _settings().offline_mode,
        "updatedAt": datetime.utcnow().strftime("%H:%M:%S UTC"),
    }

    dashboard = cl.user_session.get("dashboard_msg")

    # Preferred path: the sticky custom element.
    with contextlib.suppress(Exception):
        element = cl.CustomElement(name="CostDashboard", props=props, display="inline")
        if dashboard is None:
            message = cl.Message(content="", elements=[element])
            await message.send()
            cl.user_session.set("dashboard_msg", message)
        else:
            dashboard.elements = [element]
            await dashboard.update()
        return

    # Fallback: a plain Markdown card, updated in place.
    content = render_dashboard(report)
    if dashboard is None:
        message = cl.Message(content=content)
        await message.send()
        cl.user_session.set("dashboard_msg", message)
    else:
        dashboard.content = content
        await dashboard.update()


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
            if str(item.get("stage")) in ("legal", "critic", "tools"):
                await _update_dashboard(session)
    finally:
        with contextlib.suppress(Exception):
            await producer

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

    if outcome.critique:
        lines += [
            "**Quality audit**",
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
        lines.append("**Integrations**")
        lines.append("")
        for integration in outcome.integrations:
            lines.append(f"- {integration.get('summary')}")
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

    await _update_dashboard(_session())


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
    """Warm the retrieval index before the container reports healthy.

    Running ingestion here rather than lazily on the first message means the
    container's readiness probe reflects a service that can actually draft a
    contract, instead of one that merely answers HTTP. Ingestion is idempotent, so a
    restart against a populated Qdrant volume is a cheap no-op.
    """
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
    session_id = f"chainlit-{uuid.uuid4().hex[:10]}"
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
            ]
        )
    ).send()

    await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="jurisdiction",
                label="Compliance regime",
                values=["EU", "US"],
                initial_value=settings.default_jurisdiction,
                description=(
                    "EU: GDPR + EU AI Act · US: SEC + Delaware corporate law. "
                    "Enforced as a metadata filter inside the vector engine."
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
    jurisdiction = str(settings.get("jurisdiction", "EU")).upper()
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
            f"⚙️ Settings applied — compliance regime **{jurisdiction}**, "
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

    # ---------------------------------------------------------- in-flight run
    if session is not None and session.last_outcome is not None:
        outcome = session.last_outcome

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
                await cl.Message(
                    content="Please reply `approve` or `reject`, or use the buttons above."
                ).send()
                return
            await _provision(session, decision)
            return

    # ---------------------------------------------------------- new engagement
    await _start_run(text)


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
    session_id = cl.user_session.get("session_id") or f"chainlit-{uuid.uuid4().hex[:10]}"
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

    await cl.Message(
        content="\n".join(
            [
                f"## 🚀 New engagement — {_chip(jurisdiction)}",
                "",
                f"*{len(requirement):,} characters received. Starting the agent graph…*",
                "",
                f"`run_id: {session.run_id}`",
            ]
        )
    ).send()

    try:
        result = await _drive(
            session,
            session.start_stream(requirement, jurisdiction=jurisdiction, auto_deploy=auto_deploy),
        )
    except LLMError as exc:
        await cl.Message(content=f"⚠️ Provider error: `{exc}`").send()
        return

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
