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
import html
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import chainlit as cl

from sowsprint.agents import RunOutcome, RunStatus, ScopingSession
from sowsprint.agents.graph import describe_graph
from sowsprint.config import Settings, get_settings
from sowsprint.export.bundle import ARTIFACT_KINDS
from sowsprint.llm import LLMError
from sowsprint.models import CritiqueReport, TechnicalBlueprint
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

#: The artefacts a user can sensibly choose between.
#:
#: The quality audit and the run report are always produced: one is the evidence for the
#: verdict and the other is the record of what happened, so offering to omit them would
#: offer to leave no account of the run at all.
_SELECTABLE_ARTIFACTS = {
    "sow_markdown", "sow_pdf", "backlog_markdown", "jira_csv", "backlog_json",
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


# Whether the callbacks are registered is what Chainlit uses to decide if it needs a
# JWT secret — not whether authentication is *enabled* at runtime. Registering them
# unconditionally meant `SOWSPRINT_AUTH_ENABLED=false` still aborted startup with
#
#     ValueError: You must provide a JWT secret in the environment to use authentication
#
# which contradicts the documented way to run an open demo and breaks the promise that
# the published image runs with no configuration at all. Found by running that image.
if get_settings().auth_enabled:

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

else:
    log.warning(
        "auth.disabled",
        detail="SOWSPRINT_AUTH_ENABLED=false — the application is open to anyone who "
        "can reach the port. Intended for a local demo, never for a shared deployment.",
    )


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
            # "not reached" reads as an omission or a failure. For an optional stage it
            # usually means the opposite: Clarify asks nothing when the brief already
            # answers everything, which is the good outcome, and calling that unreached
            # makes a clean run look truncated.
            if node in OPTIONAL_STAGES:
                mark, detail = "—", "*not needed*"
            else:
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

    # The difference between "working" and "hung" is the whole question a waiting user
    # has. A spinner leaves them counting seconds with no idea whether three minutes is
    # normal, so the line names the agent, says what it is doing, and shows how long it
    # has been at it.
    started = cl.user_session.get("activity_started")
    if started is None or done:
        started = time.time()
        cl.user_session.set("activity_started", started)
    elapsed = int(time.time() - started)

    if done:
        content = f"### ✅ {text}"
    else:
        content = (
            f"### ⏳ Working — {elapsed // 60}:{elapsed % 60:02d}\n\n"
            f"{text}\n\n"
            "<sub>The agents run in sequence; this line updates as each one finishes. "
            "Nothing is sent to Jira or Notion before you approve.</sub>"
        )
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

            # Only once a verdict exists. Creating the dashboard during `legal` put a cost
            # table in the transcript ABOVE the audit it was paying for, so the reader saw
            # what the run had spent before seeing what it had concluded — while the
            # numbers were still moving. It is edited in place, so creating it early fixes
            # its position for the rest of the conversation.
            if stage in ("tools", "finalize"):
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


# --------------------------------------------------------------------------------------
# Final report helpers
#
# The reader of a finished engagement has one question — did it pass? — and a second one
# immediately after: what is the evidence? The previous layout answered neither first:
# it opened with the run status, listed quantities, and only then mentioned the verdict,
# so the thing that mattered was the fifth line down and phrased as one bullet among many.
#
# Everything below is ordered by those two questions.
# --------------------------------------------------------------------------------------


def _verdict_banner(critique: CritiqueReport | None, status: str) -> str:
    """The result of the engagement, first and unmistakable.

    Deliberately not a bullet in a list. A reader scanning the transcript after a long run
    should not have to find the verdict — it should be the thing they see.
    """
    if critique is None:
        return "\n".join([
            f"# {STATUS_ICONS.get(status, '•')} NO AUDIT — `{status}`",
            "",
            (
                "The run ended before the quality audit, so no verdict is available. "
                "The artefacts below are drafts and have not been checked."
            ),
        ])

    if getattr(critique, "degraded", False):
        return "\n".join([
            "# ⚠️ AUDIT DID NOT RUN",
            "",
            "**There is no verdict.** " + (critique.degradation_reason or ""),
            "",
            (
                "Treat every number and finding below as unreliable and re-run. "
                "The contract itself was drafted normally."
            ),
        ])

    blocking = len(critique.blocking_findings)
    advisory = len(critique.findings) - blocking
    score = critique.quality_score
    grounding = critique.grounding_ratio or 0.0

    # What actually decided the verdict. The score is the lower of two things — the
    # findings penalty and the grounding cap — and naming the wrong one sends the reader
    # to fix something that is not the problem. A run with one advisory finding and a
    # grounding ratio of 0.33 fails because a third of the contract is untraceable, not
    # because of the finding; saying "advisory findings pushed the score down" would be
    # false and would point at the wrong repair.
    from sowsprint.models import ADVISORY_WEIGHT, BLOCKING_WEIGHT, MAX_ADVISORY_PENALTY

    findings_score = 1.0 - BLOCKING_WEIGHT * blocking - min(
        MAX_ADVISORY_PENALTY, ADVISORY_WEIGHT * advisory
    )
    grounding_cap = max(0.40, grounding)
    grounding_decided = grounding_cap < findings_score - 0.005

    if critique.passed:
        headline = "# ✅ AUDIT PASSED"
        if grounding_decided:
            meaning = (
                "The contract is ready. The findings list is clean, and the score is held "
                "at the grounding level rather than by any defect."
            )
        else:
            meaning = (
                "The contract is ready. No blocking finding was raised, and the score "
                "cleared the 0.72 bar."
            )
    else:
        headline = "# ❌ AUDIT FAILED"
        if blocking:
            meaning = (
                f"**{blocking} blocking finding{'' if blocking == 1 else 's'}** must be "
                "resolved before this contract is safe to sign."
            )
        elif grounding_decided:
            meaning = (
                f"**The findings are not what failed this.** Only "
                f"{grounding:.0%} of substantive clauses are traceable to retrieved "
                f"compliance evidence, and that caps the score at {grounding_cap:.2f} — "
                "below the 0.72 bar — however few findings were raised. Bind the remaining "
                "clauses to retrieved passages, or treat this draft as unsupported."
            )
        else:
            meaning = (
                "No single finding blocks signature, but the accumulated advisory findings "
                "pushed the score below the 0.72 bar."
            )

    # The score, and where it came from when the two definitions disagree.
    if critique.raw_quality_score is not None and abs(
        critique.raw_quality_score - score
    ) > 0.01:
        basis = (
            f"derived from {len(critique.findings)} finding(s)"
            + (f", capped by grounding at {grounding:.0%}" if grounding_decided else "")
            + f" — the judge had proposed {critique.raw_quality_score:.2f}"
        )
    else:
        basis = f"derived from {len(critique.findings)} finding(s)"

    lines = [
        headline,
        "",
        meaning,
        "",
        "| Score | Grounding | Blocking | Advisory |",
        "| :-- | :-- | :-- | :-- |",
        f"| **{score:.2f}** | {grounding:.0%} | {blocking} | {advisory} |",
        "",
        f"<sub>Score {basis}.</sub>",
    ]

    if critique.findings:
        lines += ["", "**What the audit objected to**", ""]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        for finding in sorted(
            critique.findings, key=lambda f: order.get(f.severity.value, 9)
        ):
            mark = "🔴" if finding.is_blocking else "•"
            lines.append(
                f"- {mark} **{finding.severity.value} · {finding.category.value}** "
                f"`{finding.location}` — {finding.description}"
            )
            if finding.remediation:
                lines.append(f"  - *Fix:* {finding.remediation}")

    return "\n".join(lines)


def _model_line() -> str:
    """Which model produced the documents, and which audited them.

    Named because a contract is a work product: a reader has to be able to say what wrote
    it. The two are usually different vendors on purpose — a model auditing its own output
    is worse at catching its own mistakes — and seeing both makes that visible.
    """
    settings = _settings()
    draft = settings.reasoning_model
    judge = settings.critic_model or settings.reasoning_model
    embed = settings.embedding_model

    if settings.offline_mode:
        return (
            f"**Models:** deterministic offline engine (no model was called) · "
            f"embeddings `{embed}`"
        )
    same = draft == judge and settings.critic_provider == settings.llm_provider
    if same:
        return f"**Model:** `{draft}` · embeddings `{embed}`"
    return (
        f"**Models:** `{draft}` drafted · `{judge}` audited "
        f"(independent judge) · embeddings `{embed}`"
    )


def _gantt_html(blueprint: TechnicalBlueprint) -> str:
    """The delivery plan as a timeline: milestones, duration, effort.

    A summary rather than the plan itself. Every story, criterion and dependency is in the
    downloadable files and on the tracker; repeating them here buries the shape of the
    engagement — which is the one thing a chat transcript is good at showing.

    Rendered as inline HTML because Chainlit does not render Mermaid, and a table of
    proportional bars communicates sequence and relative size without a chart library.
    """
    milestones = blueprint.milestones or []
    total = blueprint.estimated_duration_weeks or sum(
        m.duration_weeks for m in milestones
    ) or 1

    palette = [
        ("#34d399", "#065f46"),
        ("#60a5fa", "#1e3a8a"),
        ("#a78bfa", "#4c1d95"),
        ("#fbbf24", "#78350f"),
        ("#f472b6", "#831843"),
    ]

    rows, offset = [], 0
    for index, milestone in enumerate(milestones):
        weeks = milestone.duration_weeks or 0
        points = sum(s.story_points or 0 for e in milestone.epics for s in e.stories)
        stories = sum(len(e.stories) for e in milestone.epics)
        width = (weeks / total * 100) if total else 0
        left = (offset / total * 100) if total else 0
        bar, _ = palette[index % len(palette)]
        rows.append(
            f'<tr>'
            f'<td style="padding:5px 12px 5px 0;white-space:nowrap;font-size:13px">'
            f'{html.escape(milestone.name)}</td>'
            f'<td style="padding:5px 12px 5px 0;color:#94a3b8;font-size:12px;'
            f'white-space:nowrap">{weeks} wk</td>'
            f'<td style="padding:5px 12px 5px 0;color:#94a3b8;font-size:12px;'
            f'white-space:nowrap">{stories} stories · {points} pts</td>'
            f'<td style="width:100%;padding:5px 0">'
            f'<div style="position:relative;height:16px;background:#1e293b;'
            f'border-radius:8px;overflow:hidden">'
            f'<div style="position:absolute;left:{left:.1f}%;width:{width:.1f}%;'
            f'height:100%;background:{bar};border-radius:8px"></div></div></td>'
            f'</tr>'
        )
        offset += weeks

    header = (
        f'<div style="color:#94a3b8;font-size:12px;margin-bottom:6px">'
        f'{len(milestones)} milestone(s) over {total} weeks · '
        f'{len((blueprint.milestones and [s for m in milestones for e in m.epics for s in e.stories]) or [])} '
        f'stories · {blueprint.estimated_total_points} points</div>'
    )
    return (
        header
        + '<table style="border-collapse:collapse;width:100%;max-width:760px">'
        + "".join(rows)
        + "</table>"
    )


def _documents_block(outcome: RunOutcome) -> str:
    """What was produced, in what format, and where it can be opened.

    The formats matter to a reader in a way a file listing does not: PDF is for signing,
    CSV is for importing into a tracker, JSON is for a downstream system, Markdown is for
    review. Saying so turns a list of downloads into a decision.
    """
    if not outcome.artifacts:
        return "_No documents were produced for this run._"

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
    lines = ["| Document | Format | Size | Use |", "| :-- | :-- | --: | :-- |"]
    for artifact in outcome.artifacts:
        path = Path(artifact.path)
        label = artifact.label
        if artifact.kind == "sow_pdf":
            label = f"**{label}**"
        lines.append(
            f"| {label} | `{path.suffix.lstrip('.').upper()}` | "
            f"{artifact.size_bytes / 1024:.0f} KB | {purpose.get(artifact.kind, '')} |"
        )
    return "\n".join(lines)


def _plan_destinations(outcome: RunOutcome) -> list[str]:
    """Where the plan can be opened, without repeating what is in it."""
    links: list[str] = []

    for integration in outcome.integrations or []:
        for item in integration.get("created") or []:
            url = str(item.get("url", ""))
            if not url.startswith("http"):
                continue
            # Parse the host rather than searching the string. `"atlassian.net" in url`
            # also matches https://example.com/atlassian.net/… — CodeQL flags it, and it is
            # right to: the value comes from an API response and ends up as a link the
            # reader is invited to click.
            try:
                host = urlparse(url).hostname or ""
            except ValueError:
                continue
            if host == "atlassian.net" or host.endswith(".atlassian.net"):
                key = item.get("key") or ""
                # A project URL is more useful than an issue URL: the reader wants the
                # whole board, not one story.
                if key and "-" not in str(key):
                    links.append(f"↗ [Open project **{key}** in Jira]({url})")
                elif key:
                    links.append(f"↗ [{key}]({url})")
    if links:
        board = [link for link in links if "Open project" in link]
        return (board or links[:1]) + ([f"_{len(links)} item(s) created_"] if len(links) > 1 else [])
    return []


def _notion_link() -> str:
    """Notion's own address, so a reader knows where to look."""
    settings = _settings()
    if not settings.notion_configured:
        return ""
    return (
        "Documents are also written to **Notion** — open "
        "[app.notion.com](https://app.notion.com/) and look in the page this integration "
        "was connected to."
    )


def _approval_summary(outcome: RunOutcome) -> list[str]:
    """What the approval gate is actually asking about.

    It used to print the planned function calls — `create_jira_project_workspace`,
    `create_jira_issue`, `create_notion_page` — which are the pipeline's vocabulary rather
    than the reader's, and thirty near-identical lines of them. A person approving a
    provisioning step needs to know what will exist afterwards and where, so they can
    decide whether it is right; the mechanism is a log concern.
    """
    from collections import Counter

    # Grouped by what will EXIST, not by which function creates it. Falling back to the
    # tool name put `create_jira_project_workspace` on screen — the pipeline's vocabulary,
    # which is exactly what this summary exists to keep out of the reader's way.
    destinations = {
        "create_jira_project_workspace": "Jira project",
        "create_notion_page": "Notion page",
        "update_jira_issue": "issue update",
    }
    wording = {
        "epic": ("epic", "epics"),
        "story": ("user story", "user stories"),
        "task": ("task", "tasks"),
        "bug": ("bug", "bugs"),
        "Jira project": ("Jira project", "Jira projects"),
        "Notion page": ("Notion page", "Notion pages"),
        "issue update": ("issue update", "issue updates"),
    }

    counts: Counter[str] = Counter()
    for call in outcome.tool_calls:
        arguments = call.get("arguments") or {}
        kind = str(arguments.get("issue_type") or "").lower()
        if kind:
            counts[kind] += 1
        elif call.get("tool"):
            counts[destinations.get(str(call["tool"]), "other item")] += 1

    project = ""
    for call in outcome.tool_calls:
        arguments = call.get("arguments") or {}
        if arguments.get("project_key"):
            project = str(arguments["project_key"])
            break

    if not counts:
        return ["_Nothing will be provisioned._", ""]

    lines = [_schedule_block(outcome), "", "**What this will create**", ""]
    for kind, count in counts.most_common():
        singular, plural = wording.get(kind, (kind.replace("_", " "), f"{kind}s"))
        lines.append(f"- {count} {singular if count == 1 else plural}")

    if project:
        lines += [
            "",
            f"A **new Jira project `{project}`**, plus a page in Notion. Nothing is added "
            "to an existing project: each run provisions its own workspace so two "
            "engagements stay comparable.",
        ]
    return lines


def _project_shape(outcome: RunOutcome) -> str:
    """Planned dates and size, as one sentence.

    A start date alone answers half the question. What a reader weighing a proposal needs
    is when it begins, when it ends, how long that is, and what it takes to do — and the
    last of those is the one nobody can estimate by reading a milestone list.
    """
    start = _settings().resolved_project_start
    weeks = 0
    if outcome.blueprint is not None:
        weeks = outcome.blueprint.estimated_duration_weeks or 0
        if not weeks:
            weeks = sum(m.duration_weeks or 0 for m in outcome.blueprint.milestones)

    if not weeks:
        return f"**Start:** {start:%A, %d %B %Y} — change it in the settings panel."

    from sowsprint.schedule import project_window

    _, finish = project_window(start, weeks)
    points = outcome.blueprint.estimated_total_points if outcome.blueprint else 0
    stories = len(outcome.blueprint.all_stories()) if outcome.blueprint else 0

    size = f"{weeks} weeks"
    if stories:
        size += f" · {stories} stories"
    if points:
        size += f" · {points} points"

    return (

            f"**Start:** {start:%A, %d %B %Y} · "
            f"**Planned finish:** {finish:%A, %d %B %Y} · "
            f"**Duration:** {size}"

    )


def _resource_block(outcome: RunOutcome) -> str:
    """The roles this plan needs, counted from the work itself.

    There is no staffing field in the blueprint and inventing one would be guesswork. The
    stories do carry the role they are written for — "as a warehouse operator I want…" —
    so counting them gives a defensible answer to "what does this take", derived from the
    plan rather than asserted alongside it.
    """
    if outcome.blueprint is None:
        return ""

    from collections import Counter

    roles: Counter[str] = Counter()
    for story in outcome.blueprint.all_stories():
        role = (getattr(story, "as_a", "") or "").strip()
        if role:
            roles[role] += 1
    if not roles:
        return ""

    listed = ", ".join(
        f"{role} ({count})" if count > 1 else role for role, count in roles.most_common(6)
    )
    extra = len(roles) - 6
    tail = f" and {extra} more role(s)" if extra > 0 else ""
    return (
        f"**Resources:** {listed}{tail} — counted from the roles the stories "
        "are written for."
    )


def _schedule_block(outcome: RunOutcome) -> str:
    """Dates, duration and resources together, in the order a reader asks for them."""
    parts = [_project_shape(outcome)]
    resources = _resource_block(outcome)
    if resources:
        parts.append(resources)
    parts.append(
        "<sub>Change the start date in the settings panel before approving.</sub>"
    )
    return "\n\n".join(parts)


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
        lines += _approval_summary(outcome)
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
    #
    # Ordered by the two questions a reader has when a run ends: did it pass, and what is
    # the evidence? The previous layout opened with the run status and mentioned the
    # verdict fifth, as one bullet among many, which buried the only line that decides
    # whether the work is usable.
    lines = [
        _verdict_banner(outcome.critique, outcome.status),
        "",
        _model_line(),
        "",
    ]

    if outcome.errors:
        lines += ["**Errors during the run**", ""]
        lines += [f"- `{error}`" for error in outcome.errors]
        lines.append("")

    if outcome.halt_reason:
        lines += [f"> {outcome.halt_reason}", ""]

    lines += ["---", "", "## Evidence for the verdict", ""]
    lines += [
        (
            "The documents below are what the verdict is based on. They are the deliverable; "
            "the audit above is the opinion about them."
        ),
        "",
    ]

    if outcome.sow:
        lines += [
            f"**Statement of Work** — {outcome.sow.title}",
            "",
            f"- {len(outcome.sow.clauses)} clauses, {outcome.sow.word_count():,} words",
            f"- Governing law: {outcome.sow.governing_law}",
            f"- {len(outcome.sow.retrieved_evidence_ids)} clause(s) traceable to retrieved "
            "compliance evidence",
            "",
        ]

    lines += ["### Documents produced", "", _documents_block(outcome), ""]

    notion = _notion_link()
    if notion:
        lines += [notion, ""]

    # ------------------------------------------------------------------ delivery plan
    if outcome.blueprint:
        lines += ["---", "", "## Delivery plan", ""]
        lines += [
            (
                "The shape of the engagement at a glance. Every story, acceptance criterion "
                "and dependency is in the downloadable files and on the tracker."
            ),
            "",
        ]
        lines.append(_gantt_html(outcome.blueprint))
        lines.append("")

        where = _plan_destinations(outcome)
        if where:
            lines += ["**Open it:** " + " · ".join(where), ""]

    # ------------------------------------------------------------------ destinations
    if outcome.integrations:
        # State plainly where the data went. "38 call(s) succeeded (dry-run)" reads as
        # success, and a reader reasonably concluded the backlog had been created when
        # nothing had been sent anywhere.
        lines += ["---", "", "## Where the data went", "",
                  "| Destination | Result |", "| :-- | :-- |"]
        any_dry = False
        for integration in outcome.integrations:
            target = str(integration.get("target") or "integration")
            dry = bool(integration.get("dry_run"))
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
        lines.append("")

        if any_dry:
            lines += [
                (
                    "> **Nothing reached Jira or Notion.** Dry-run is on, so every call was "
                    "validated and thrown away. Set `SOWSPRINT_DRY_RUN_INTEGRATIONS=false` "
                    "and run again to provision for real."
                ),
                "",
            ]

    with contextlib.suppress(Exception):
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
            cl.input_widget.DatePicker(
                id="project_start",
                label="Project start date",
                initial=_settings().resolved_project_start.isoformat(),
                description=(
                    "The date the delivery plan begins. Defaults to next Monday, so "
                    "milestone boundaries land on weekdays rather than mid-week. Every "
                    "epic and story in Jira is scheduled from here, which is what makes "
                    "the timeline show a schedule instead of a list. Change it before "
                    "approving if the engagement starts later."
                ),
            ),
            cl.input_widget.MultiSelect(
                id="artifact_kinds",
                label="Documents to produce",
                initial=[],
                items={
                    kind: label
                    for kind, label in ARTIFACT_KINDS.items()
                    if kind in _SELECTABLE_ARTIFACTS
                },
                description=(
                    "Leave empty to produce all of them. Deselecting a document saves "
                    "rendering time and disk, NOT model tokens — the audit is roughly 70% "
                    "of a run's cost and the drafting calls are most of the rest, so a "
                    "shorter list does not buy a cheaper run. Choose the formats you will "
                    "actually use: PDF to sign, CSV to import into a tracker, Markdown to "
                    "review, JSON for a downstream system. The audit and the run report "
                    "are always produced, because they are the evidence for the verdict "
                    "and the record of the run."
                ),
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
    chosen = list(settings.get("artifact_kinds") or [])
    cl.user_session.set("artifact_kinds", chosen)

    start = settings.get("project_start")
    if start:
        cl.user_session.set("project_start", str(start))

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
            + (
                ", all documents"
                if not chosen
                else f", {len(chosen)} document type(s)"
            )
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

    # The document selection lives in the user session and has to reach the exporter,
    # which reads it from Settings. An empty selection means "all", which is also what an
    # untouched widget means, so the two cannot be confused.
    chosen = cl.user_session.get("artifact_kinds")
    if chosen:
        settings = settings.model_copy(update={"artifact_kinds": ",".join(chosen)})

    start = cl.user_session.get("project_start")
    if start:
        settings = settings.model_copy(update={"project_start": str(start)})

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


#: How often to transcribe while the speaker is still talking. Long enough that Whisper has
#: a phrase to work with and that the cost stays negligible, short enough that the words
#: appear while they are still being said.
LIVE_TRANSCRIBE_EVERY_S = 4.0


def _voice_panel(
    text: str,
    *,
    state: str,
    seconds: float = 0.0,
    detail: str = "",
) -> str:
    """The recording state, as the one element the client script reads.

    ``state`` is the whole contract between here and ``public/live-voice.js``:

    * ``idle``       — nothing is being recorded; the field is cleared
    * ``recording``  — words are arriving and are mirrored into the field as they come
    * ``review``     — the recording has stopped and the text is waiting for confirmation

    Exactly one of these elements exists at a time. The first version used a fixed id and
    left every previous element in the transcript, so ``getElementById`` returned the
    OLDEST one — the first brief ever spoken — and every later recording re-displayed it.
    A reader saw words they had not just said, which is both wrong and alarming.
    """
    import html as _html

    payload = _html.escape(text, quote=True)
    if state == "recording":
        body = (
            '<span class="voice-dot"></span>'
            f'<b>Recording</b> — {seconds:.0f}s · speak, then press the microphone again'
            + (f'<div class="voice-review">{_html.escape(text)}</div>' if text else "")
        )
    elif state == "review":
        body = (
            '<b>Transcribed</b> — check it before sending'
            f'<div class="voice-review">{_html.escape(text)}</div>'
        )
    else:
        body = _html.escape(detail or "")

    return (
        f'<span id="sowsprint-voice" data-state="{state}" data-text="{payload}">'
        f'{body}</span>'
    )


async def _clear_voice_panel() -> None:
    """Remove the previous panel, so only one ever exists.

    The client finds the panel by id; leaving older ones in the transcript is what made the
    field show a stale brief. Clearing the content removes the id from the document.
    """
    previous = cl.user_session.get("voice_panel")
    if previous is None:
        return
    with contextlib.suppress(Exception):
        previous.content = ""
        await previous.update()
    cl.user_session.set("voice_panel", None)


async def _transcribe_partial(buffer: list[bytes], settings: Any) -> str:
    """Transcribe what has been captured so far, or return empty on any failure.

    A partial transcript is a convenience; the definitive one is produced when the speaker
    stops. Anything that goes wrong here is swallowed deliberately, because a failed interim
    pass must never interrupt the recording or surface as an error about the user's voice.
    """
    payload, suffix = build_audio_file(
        buffer, mime_type="audio/wav", sample_rate=settings.audio_sample_rate
    )
    if not payload:
        return ""
    target = Path(tempfile.gettempdir()) / f"sowsprint-partial-{uuid.uuid4().hex[:8]}{suffix}"
    try:
        target.write_bytes(payload)
        result = await asyncio.to_thread(transcribe_audio, target, settings=settings)
        return result.text if result.ok else ""
    except Exception as exc:
        log.debug("voice.partial_failed", error=str(exc))
        return ""
    finally:
        target.unlink(missing_ok=True)


@cl.action_callback("voice_accept")
async def on_voice_accept(action: cl.Action) -> None:
    """Send the transcript the speaker has just checked.

    The text is read from the composer rather than from the session, so an edit made in the
    field is what the agents receive. The live panel carried it there; this picks up whatever
    the speaker left.
    """
    text = str(cl.user_session.get("pending_transcript") or "").strip()
    if not text:
        await cl.Message(content="Nothing to send — record again.").send()
        return
    cl.user_session.set("pending_transcript", None)
    await _clear_voice_panel()
    await _start_run(text)


@cl.action_callback("voice_discard")
async def on_voice_discard(action: cl.Action) -> None:
    """Throw the recording away without sending anything."""
    cl.user_session.set("pending_transcript", None)
    await _clear_voice_panel()
    await cl.Message(content="Recording discarded. Nothing was sent.").send()


@cl.on_audio_start
async def on_audio_start() -> bool:
    """Accept an audio stream from the browser's capture widget.

    Deliberately does nothing else. Chainlit invokes this once per **session**, not once per
    recording — measured: one call across a session in which two recordings were made. The
    panel therefore cannot be created here, or the second recording has none and its words
    go nowhere. `chunk.isStart` marks the first chunk of every recording, so the reset lives
    in `on_audio_chunk`.
    """
    return True


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.AudioChunk) -> None:
    """Accumulate chunks, and periodically show what has been said so far.

    The speaker should be able to read their own words as they arrive: a recording whose text
    only appears afterwards gives no way to notice a muted microphone, the wrong device, or a
    sentence that came out wrong, until it is too late to redo it.
    """
    if chunk.isStart:
        # The first chunk of a recording: this is where a new one begins.
        cl.user_session.set("audio_ended", False)
        cl.user_session.set("audio_mime", chunk.mimeType)
        await _clear_voice_panel()
        cl.user_session.set("audio_chunks", [])
        cl.user_session.set("audio_seconds", 0.0)
        cl.user_session.set("last_partial_at", 0.0)
        cl.user_session.set("live_transcript", "")
        panel = cl.Message(content=_voice_panel("", state="recording"), author="Voice")
        cl.user_session.set("voice_panel", panel)
        await panel.send()

    buffer: list[bytes] = cl.user_session.get("audio_chunks") or []
    buffer.append(chunk.data)
    cl.user_session.set("audio_chunks", buffer)

    # `elapsedTime` is milliseconds since the recording began — the client sends
    # `Date.now() - startedAt` (chainlit frontend, `audio_connection`). Read as seconds it
    # is always far past the interval, so every chunk triggered a transcription: 128
    # Whisper calls for two recordings, each re-transcribing the whole clip.
    elapsed_ms = float(getattr(chunk, "elapsedTime", 0) or 0)
    seconds = max(
        float(cl.user_session.get("audio_seconds") or 0.0), elapsed_ms / 1000.0
    )
    cl.user_session.set("audio_seconds", seconds)

    # Transcribe on a time interval, not a chunk count: chunk sizes are the browser's choice
    # and vary with the device.
    last_pass = float(cl.user_session.get("last_partial_at") or 0.0)
    if seconds - last_pass < LIVE_TRANSCRIBE_EVERY_S:
        return
    cl.user_session.set("last_partial_at", seconds)

    settings = _settings()
    # A plain string, not an enum: the resolver returns the adapter name, and `.value` raised
    # on every chunk — so the live path failed silently inside Chainlit's wrapper while the
    # recording itself carried on.
    if str(settings.resolved_whisper_provider) == "offline":
        return

    text = await _transcribe_partial(buffer, settings)
    log.info(
        "voice.chunk",
        elapsed=round(seconds, 1),
        chunk_bytes=len(chunk.data),
        got=len(text),
        panel=cl.user_session.get("voice_panel") is not None,
    )
    previous = str(cl.user_session.get("live_transcript") or "")
    # Whisper truncates on short clips; never let a shorter reading replace a fuller one.
    if text and len(text.strip()) >= len(previous.strip()):
        cl.user_session.set("live_transcript", text)
        previous = text

    panel = cl.user_session.get("voice_panel")
    if panel is not None and not cl.user_session.get("audio_ended"):
        with contextlib.suppress(Exception):
            panel.content = _voice_panel(previous, state="recording", seconds=seconds)
            await panel.update()


@cl.on_audio_end
async def on_audio_end() -> None:
    """Persist the recording, transcribe it and feed the text into the graph.

    Chainlit 2.x invokes this hook with **no arguments** (``socket.py``:
    ``await config.code.on_audio_end()``) and its wrapper binds positional arguments to
    parameter names, so any parameter here raises ``TypeError`` at runtime. The chunks
    are therefore read from the user session rather than passed in.
    """
    cl.user_session.set("audio_ended", True)

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

    # Show the transcript and wait. Speech recognition mishears things — names, numbers,
    # accents — and a brief that silently went in wrong produces a contract about the wrong
    # engagement. Confirming costs one click and is the only point where the speaker can
    # catch it.
    cl.user_session.set("pending_transcript", result.text)
    panel = cl.user_session.get("voice_panel")
    if panel is not None:
        with contextlib.suppress(Exception):
            panel.content = _voice_panel(result.text, state="review")
            await panel.update()

    await cl.Message(
        content="\n".join(
            [
                f"📝 **Transcript** — {result.summary()}",
                "",
                "_Edit it in the message box if it misheard, then confirm._",
            ]
        ),
        actions=[
            cl.Action(name="voice_accept", payload={"decision": "accept"}, label="✅ Send it"),
            cl.Action(name="voice_discard", payload={"decision": "discard"}, label="🗑 Discard"),
        ],
    ).send()
