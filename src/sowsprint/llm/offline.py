"""Deterministic offline reasoning engine.

Serves as the platform's credential-free reasoning backend **and** as its local
guardrail: the engineering brief explicitly calls for ``Ollama / vLLM (Local
Guardrails)``, and this engine is the CPU-only, zero-dependency instance of that
idea.

It is not a stub. Each agent node has a real rule-based implementation:

===============  ==================================================================
Node             Offline strategy
===============  ==================================================================
Triage           Lexicon + pattern extraction (:mod:`sowsprint.llm.nlp`), critical
                 variable completeness scoring, and generation of exactly three
                 targeted clarification questions.
Architect        Deterministic phase planning: deliverables are grouped into
                 milestones, decomposed into epics and user stories with explicit
                 GIVEN/WHEN/THEN acceptance criteria derived from compliance flags
                 and non-functional constraints.
Legal            Jurisdiction-parameterised clause assembly that binds every clause
                 to retrieved evidence chunk ids, so citations are never invented.
Critic           Rule-based audit: citation existence, evidence coverage, required
                 clause presence, payment arithmetic, ambiguity phrasing.
===============  ==================================================================

Because the engine reads the *same* tagged context blocks that a cloud model would
read, both backends execute an identical node contract; switching providers changes
quality, never control flow.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from ..models import (
    AcceptanceCriterion,
    ClarificationQuestion,
    CriticFinding,
    CritiqueReport,
    Epic,
    FindingCategory,
    Jurisdiction,
    Milestone,
    NodeName,
    PaymentMilestone,
    RequirementScope,
    ScopeStatus,
    Severity,
    SOWClause,
    SOWDocument,
    TechChoice,
    TechnicalBlueprint,
    UserStory,
)
from ..telemetry.tracker import estimate_tokens
from . import nlp
from .base import BaseLLMClient, Message
from .parsing import coerce_list, extract_tagged, extract_tagged_json, loads_lenient

OFFLINE_MODEL = "offline-heuristic-v1"

# --------------------------------------------------------------------------------------
# Critical scoping variables
# --------------------------------------------------------------------------------------

#: Variables the contract cannot be drafted without, in descending priority.
QUESTION_PRIORITY: tuple[str, ...] = (
    "deliverables",
    "business_goal",
    "timeline_weeks",
    "target_users",
    "integrations",
    "success_metrics",
    "budget_range",
    "jurisdiction",
)

_QUESTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "deliverables": {
        "question": "Which concrete deliverables are in scope for the first release?",
        "why_blocking": (
            "Deliverables define the SOW's scope boundary and every milestone's exit "
            "criteria; without them the contract has no enforceable subject matter."
        ),
        "suggested_answers": [
            "Web application + admin console",
            "API and integrations only",
            "Full platform incl. mobile app",
        ],
    },
    "business_goal": {
        "question": "What single business outcome must this engagement deliver?",
        "why_blocking": (
            "The primary outcome drives acceptance criteria and milestone payment "
            "triggers, so it must be stated explicitly rather than inferred."
        ),
        "suggested_answers": [
            "Reduce manual processing time",
            "Increase revenue per customer",
            "Replace a legacy system",
        ],
    },
    "timeline_weeks": {
        "question": "What is the required delivery window or go-live date?",
        "why_blocking": (
            "Duration determines the milestone schedule, the payment plan and the "
            "termination-for-convenience notice period."
        ),
        "suggested_answers": ["8 weeks", "12 weeks", "6 months"],
    },
    "target_users": {
        "question": "Who are the primary end users of the solution?",
        "why_blocking": (
            "User populations determine accessibility obligations and the scope of "
            "any personal-data processing under the chosen jurisdiction."
        ),
        "suggested_answers": ["Internal staff", "External business customers", "Both"],
    },
    "integrations": {
        "question": "Which existing systems must the solution integrate with?",
        "why_blocking": (
            "Third-party dependencies carry their own licensing and data-transfer "
            "terms that must be reflected in the SOW's dependencies schedule."
        ),
        "suggested_answers": ["Salesforce", "SAP", "None — standalone"],
    },
    "success_metrics": {
        "question": "How will success be measured after go-live?",
        "why_blocking": (
            "Measurable outcomes are required for the acceptance-testing clause and "
            "the warranty period."
        ),
        "suggested_answers": [
            "Time saved per transaction",
            "Error rate reduction",
            "Adoption / active users",
        ],
    },
    "budget_range": {
        "question": "What budget envelope has been approved for this engagement?",
        "why_blocking": (
            "The approved envelope caps the total contract value and shapes the "
            "change-request rate card."
        ),
        "suggested_answers": ["< €50k", "€50k – €150k", "> €150k"],
    },
    "jurisdiction": {
        "question": "Which legal jurisdiction and governing law should apply?",
        "why_blocking": (
            "Governing law determines the mandatory data-protection, liability and "
            "termination provisions the contract must contain."
        ),
        "suggested_answers": ["European Union (GDPR / EU AI Act)", "United States (Delaware)"],
    },
}

#: Number of clarification questions the PRD mandates per round.
QUESTIONS_PER_ROUND = 3


# --------------------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------------------


def _scope_from_signals(
    signals: nlp.RequirementSignals, prior: dict[str, Any] | None
) -> RequirementScope:
    prior = prior or {}

    def pick(field: str, value: Any, default: Any = None) -> Any:
        """Prefer a previously confirmed value over a freshly inferred one."""
        existing = prior.get(field)
        if existing not in (None, "", [], {}):
            return existing
        return value if value not in (None, "", [], {}) else default

    jurisdiction_raw = pick("jurisdiction", signals.jurisdiction.value, "EU")
    try:
        jurisdiction = Jurisdiction(jurisdiction_raw)
    except ValueError:
        jurisdiction = signals.jurisdiction

    scope = RequirementScope(
        title=pick("title", signals.title, "B2B Engagement"),
        client_name=pick("client_name", signals.client_name, "Client"),
        vendor_name=prior.get("vendor_name") or "SOWSprint Delivery Team",
        business_goal=pick("business_goal", signals.business_goal, ""),
        problem_statement=pick("problem_statement", signals.problem_statement, ""),
        deliverables=coerce_list(pick("deliverables", signals.deliverables, [])),
        out_of_scope=coerce_list(pick("out_of_scope", signals.out_of_scope, [])),
        constraints=coerce_list(pick("constraints", signals.constraints, [])),
        integrations=coerce_list(pick("integrations", signals.integrations, [])),
        target_users=coerce_list(pick("target_users", signals.target_users, [])),
        success_metrics=coerce_list(pick("success_metrics", signals.success_metrics, [])),
        assumptions=coerce_list(prior.get("assumptions")),
        risks=coerce_list(pick("risks", signals.risks, [])),
        compliance_flags=coerce_list(pick("compliance_flags", signals.compliance_flags, [])),
        jurisdiction=jurisdiction,
        timeline_weeks=pick("timeline_weeks", signals.timeline_weeks),
        budget_range=pick("budget_range", signals.budget_range),
        team_size=pick("team_size", signals.team_size),
        detected_signals={k: v for k, v in signals.signals.items() if v},
        revision=int(prior.get("revision", 0) or 0) + 1,
    )
    return scope


def _missing_variables(scope: RequirementScope) -> list[str]:
    missing: list[str] = []
    if not scope.deliverables:
        missing.append("deliverables")
    if not scope.business_goal or len(scope.business_goal) < 12:
        missing.append("business_goal")
    if not scope.timeline_weeks:
        missing.append("timeline_weeks")
    if not scope.target_users:
        missing.append("target_users")
    if not scope.integrations and not scope.constraints:
        missing.append("integrations")
    if not scope.success_metrics:
        missing.append("success_metrics")
    if not scope.budget_range:
        missing.append("budget_range")
    return [m for m in QUESTION_PRIORITY if m in missing]


#: Asked when fewer than three *critical* variables are missing. Re-asking for
#: information the customer already supplied destroys trust, so the remaining slots
#: are filled with commercially material confirmations instead.
_CONFIRMATION_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "acceptance_owner",
        "question": "Who signs off milestone acceptance on the client side?",
        "why_blocking": (
            "The acceptance clause needs a named role and a decision window; without it "
            "milestone payments have no objective trigger."
        ),
        "variable": "acceptance_owner",
        "suggested_answers": ["Product owner", "Steering committee", "CTO / sponsor"],
    },
    {
        "id": "payment_structure",
        "question": "Should fees be fixed-price per milestone or time-and-materials?",
        "why_blocking": (
            "The commercial model determines the payment schedule, the change-request rate "
            "card and the liability cap."
        ),
        "variable": "payment_structure",
        "suggested_answers": ["Fixed price per milestone", "Time & materials", "Capped T&M"],
    },
    {
        "id": "warranty_period",
        "question": "What warranty period should apply after go-live?",
        "why_blocking": (
            "The warranty window defines the defect-remediation obligation and the "
            "post-launch support cost."
        ),
        "variable": "warranty_period",
        "suggested_answers": ["30 days", "90 days", "6 months"],
    },
]


def _build_questions(missing: list[str]) -> list[ClarificationQuestion]:
    """Always emit exactly ``QUESTIONS_PER_ROUND`` questions, per the PRD."""
    selected = list(missing[:QUESTIONS_PER_ROUND])

    questions: list[ClarificationQuestion] = []
    for variable in selected:
        template = _QUESTION_TEMPLATES[variable]
        questions.append(
            ClarificationQuestion(
                id=variable,
                question=template["question"],
                why_blocking=template["why_blocking"],
                variable=variable,
                suggested_answers=list(template["suggested_answers"]),
            )
        )

    # Pad with confirmation questions, never by re-asking a satisfied variable.
    for template in _CONFIRMATION_QUESTIONS:
        if len(questions) >= QUESTIONS_PER_ROUND:
            break
        if any(q.variable == template["variable"] for q in questions):
            continue
        questions.append(ClarificationQuestion(**template))
    return questions


def _initial_confidence(scope: RequirementScope, missing: list[str]) -> float:
    present = len(QUESTION_PRIORITY) - len(missing)
    base = present / len(QUESTION_PRIORITY)
    if scope.compliance_flags:
        base += 0.05
    if scope.deliverables and len(scope.deliverables) >= 3:
        base += 0.05
    return round(min(0.98, max(0.15, base)), 2)


def triage_offline(raw_requirement: str, prior_scope: dict[str, Any] | None, answers: str) -> RequirementScope:
    """Deterministic Triage implementation."""
    combined = f"{raw_requirement}\n{answers}".strip() if answers else raw_requirement

    # Answers are free text; fold them into the analysis and let prior values win
    # only where the answer did not add information.
    signals = nlp.analyse(combined)
    scope = _scope_from_signals(signals, prior_scope)

    if answers:
        scope.assumptions = nlp.dedupe(
            [*scope.assumptions, f"Clarification round {scope.revision} answered by client."]
        )

    missing = _missing_variables(scope)
    scope.missing_variables = missing
    revisions = scope.revision

    # After two clarification rounds we stop blocking: remaining gaps become explicit
    # documented assumptions so the contract can still be drafted and reviewed.
    if missing and revisions >= 3:
        scope.assumptions = nlp.dedupe(
            [
                *scope.assumptions,
                *[
                    f"Assumed for {var.replace('_', ' ')}: to be confirmed in the kick-off workshop."
                    for var in missing
                ],
            ]
        )
        scope.status = ScopeStatus.COMPLETE
        scope.clarifying_questions = []
        scope.confidence = _initial_confidence(scope, missing)
        return scope

    if missing:
        scope.status = ScopeStatus.INCOMPLETE
        scope.clarifying_questions = _build_questions(missing)
        scope.confidence = _initial_confidence(scope, missing)
    else:
        scope.status = ScopeStatus.COMPLETE
        scope.clarifying_questions = []
        scope.confidence = _initial_confidence(scope, [])

    return scope


# --------------------------------------------------------------------------------------
# Architect
# --------------------------------------------------------------------------------------

_PHASE_BLUEPRINTS: list[tuple[str, str]] = [
    ("Discovery & Foundations", "Lock scope, validate assumptions and stand up the delivery spine."),
    ("Core Build", "Deliver the primary user-facing capabilities end to end."),
    ("Integration & Hardening", "Connect external systems, then harden security, compliance and performance."),
    ("Launch & Handover", "Production rollout, observability, documentation and knowledge transfer."),
]

_LAYER_DEFAULTS: dict[str, tuple[str, str]] = {
    "frontend": ("React 18 + TypeScript", "Type-safe component model with a mature ecosystem and broad hiring pool."),
    "mobile": ("React Native", "Shares the web codebase and releases to iOS from the same pipeline."),
    "backend": ("Python 3.11 + FastAPI", "Async I/O suits agentic workloads and keeps one language across the pipeline."),
    "data": ("PostgreSQL 16 + pgvector", "Transactional integrity plus vector search without a second operational store."),
    "ai": ("LangGraph orchestration + GPT-4o class reasoning", "Explicit state machine keeps multi-agent control flow auditable."),
    "infrastructure": ("Docker Compose on Linux, S3-compatible object storage", "Reproducible environments with a low operational ceiling."),
    "integration": ("REST connectors with idempotent retry", "Third-party systems are reconciled through an outbox pattern."),
}

_STORY_TEMPLATES: list[tuple[str, str, str, str]] = [
    (
        "deliver",
        "Deliver {subject}",
        "product owner",
        "a working, demonstrable {subject_lower}",
        "value can be validated at the end of the milestone",
    ),
    (
        "instrument",
        "Instrument and monitor {subject}",
        "platform engineer",
        "structured logs, metrics and alerts for {subject_lower}",
        "regressions are detected before customers report them",
    ),
    (
        "test",
        "Automate acceptance tests for {subject}",
        "QA engineer",
        "an automated suite covering the acceptance criteria of {subject_lower}",
        "each milestone exit is objectively verifiable",
    ),
    (
        "document",
        "Document and hand over {subject}",
        "delivery lead",
        "runbook and handover material for {subject_lower}",
        "the client team can operate the capability unaided",
    ),
]


def _slug(text: str, limit: int = 38) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -" else " " for ch in text).strip()
    words = cleaned.split()
    slug = "-".join(words[:6]).upper()
    return slug[:limit] or "ITEM"


def _subject_of(deliverable: str) -> str:
    """Turn a deliverable sentence into a compact noun-ish subject.

    Leading casing is preserved so the subject reads correctly both mid-sentence
    ("Deliver a React dashboard …") and as a heading (capitalised by the caller).
    """
    lowered = deliverable.strip().rstrip(".")
    for verb in nlp._DELIVERABLE_VERBS:
        if lowered.casefold().startswith(verb):
            lowered = lowered[len(verb) :].strip()
            break
    words = lowered.split()
    subject = " ".join(words[:8]) if words else deliverable.strip()
    return subject


def _acceptance_criteria(subject: str, scope: RequirementScope, milestone_index: int) -> list[AcceptanceCriterion]:
    criteria = [
        AcceptanceCriterion(
            given=f"the {subject.lower()} is deployed to the staging environment",
            when="the acceptance scenario defined for this deliverable is executed",
            then="the expected observable result is produced and recorded in the test report",
        ),
        AcceptanceCriterion(
            given="a user with the intended role accesses the capability",
            when="they follow the documented primary flow",
            then="the flow completes without manual intervention or error in the logs",
        ),
    ]
    if scope.compliance_flags:
        flag = scope.compliance_flags[0]
        criteria.append(
            AcceptanceCriterion(
                given=f"the solution processes data covered by {flag}",
                when="a data-subject request or audit export is triggered",
                then="the system produces the required evidence within the contractual SLA",
            )
        )
    if milestone_index >= 2 and scope.success_metrics:
        criteria.append(
            AcceptanceCriterion(
                given="the capability has run in production for one full reporting period",
                when=f"the metric '{scope.success_metrics[0][:90]}' is measured",
                then="the agreed threshold is met or a remediation plan is raised",
            )
        )
    return criteria


def _distribute(items: list[str], buckets: int) -> list[list[str]]:
    """Split ``items`` into ``buckets`` balanced groups, preserving order.

    Guarantees both that no bucket is empty while items remain and that the final
    phase is never silently dropped — the failure mode of naive slicing when the
    deliverable count is not divisible by the phase count.
    """
    if buckets <= 0:
        return []
    groups: list[list[str]] = [[] for _ in range(buckets)]
    for index, item in enumerate(items):
        # Round-robin keeps group sizes within one of each other and, crucially,
        # fills the early groups first so a short list still reaches later phases.
        groups[index % buckets].append(item)
    return groups


def _build_milestones(scope: RequirementScope) -> list[Milestone]:
    deliverables = scope.deliverables or [
        "Discovery report and validated scope",
        "Core platform implementation",
        "Production launch and handover",
    ]

    phase_count = min(len(_PHASE_BLUEPRINTS), max(1, len(deliverables)))
    groups = _distribute(deliverables, phase_count)

    milestones: list[Milestone] = []
    for phase_index, (phase_name, phase_objective) in enumerate(_PHASE_BLUEPRINTS[:phase_count]):
        chunk = groups[phase_index]
        if not chunk:
            continue

        epics: list[Epic] = []
        for item_index, deliverable in enumerate(chunk):
            subject = _subject_of(deliverable)
            epic_key = f"{_slug(phase_name)}-{item_index + 1}"
            stories: list[UserStory] = []
            is_final_phase = phase_index == phase_count - 1
            for template_index, (kind, title_tpl, role, want_tpl, so_that) in enumerate(
                _STORY_TEMPLATES[: 4 if is_final_phase else 3]
            ):
                stories.append(
                    UserStory(
                        key=f"{epic_key}-{template_index + 1}",
                        title=title_tpl.format(subject=subject),
                        as_a=role,
                        i_want=want_tpl.format(subject_lower=subject.lower()),
                        so_that=so_that,
                        acceptance_criteria=_acceptance_criteria(subject, scope, phase_index),
                        story_points=(3, 5, 2, 2)[template_index % 4],
                        priority="High" if template_index == 0 else "Medium",
                        labels=dedupe_labels([kind, phase_name.split()[0].lower(), scope.jurisdiction.value.lower()]),
                        depends_on=[f"{epic_key}-1"] if template_index > 0 else [],
                    )
                )

            epics.append(
                Epic(
                    key=epic_key,
                    name=(subject[:1].upper() + subject[1:])[:70],
                    objective=f"Deliver '{subject}' to the standard defined in the SOW acceptance process.",
                    stories=stories,
                )
            )

        duration = max(1, round((scope.timeline_weeks or phase_count * 2) / phase_count))

        milestones.append(
            Milestone(
                name=phase_name,
                objective=phase_objective,
                duration_weeks=duration,
                epics=epics,
            )
        )
    return milestones


def dedupe_labels(labels: list[str]) -> list[str]:
    return nlp.dedupe([label for label in labels if label])


def _build_tech_stack(scope: RequirementScope) -> list[TechChoice]:
    detected = nlp.detect_technologies(" ".join(scope.deliverables + scope.constraints))
    # Fold in the layer summary captured by Triage ("ai: rag, langgraph").
    for entry in scope.detected_signals.get("technology", []):
        if ":" not in entry:
            continue
        layer, _, values = entry.partition(":")
        tokens = [v.strip() for v in values.split(",") if v.strip()]
        if tokens:
            detected.setdefault(layer.strip(), [])
            detected[layer.strip()] = nlp.dedupe([*detected[layer.strip()], *tokens])

    layers = set(detected) | {"backend", "frontend", "infrastructure"}
    if scope.compliance_flags:
        layers.add("data")
    if scope.integrations:
        layers.add("integration")

    stack: list[TechChoice] = []
    for layer in sorted(layers):
        if detected.get(layer):
            technology = ", ".join(nlp.display_tech(t) for t in detected[layer][:3])
            rationale = "Explicitly requested or evidenced in the customer requirement."
            alternatives = ["Re-evaluate during the discovery milestone"]
        else:
            technology, rationale = _LAYER_DEFAULTS.get(
                layer, ("To be selected in discovery", "Deferred until constraints are confirmed.")
            )
            alternatives = ["Alternative assessed during discovery"]
        stack.append(
            TechChoice(
                layer=layer,
                technology=technology,
                rationale=rationale,
                alternatives_considered=alternatives,
            )
        )
    return stack


def architect_offline(scope_payload: dict[str, Any]) -> TechnicalBlueprint:
    """Deterministic Architect implementation."""
    scope = RequirementScope.model_validate(scope_payload)
    milestones = _build_milestones(scope)

    non_functional: list[str] = [
        "Availability target of 99.9% monthly for production services.",
        "p95 API response time below 400 ms at the agreed reference load.",
        "All personally identifiable fields encrypted at rest with AES-256.",
        "CI pipeline blocks merges on failing tests or high-severity security findings.",
    ]
    for flag in scope.compliance_flags:
        non_functional.append(f"Compliance control evidence maintained for: {flag}.")
    non_functional.extend(scope.constraints[:4])

    blueprint = TechnicalBlueprint(
        solution_overview=(
            f"{scope.title} will be delivered as {len(milestones)} sequential milestones. "
            f"{scope.business_goal or 'The engagement delivers the agreed scope.'} "
            "Each milestone ends with a demonstrable increment and an acceptance review."
        ),
        architecture_style=(
            "Modular service boundaries with an event-driven integration layer"
            if scope.integrations
            else "Modular monolith with a documented path to service extraction"
        ),
        tech_stack=_build_tech_stack(scope),
        milestones=milestones,
        non_functional_requirements=nlp.dedupe(non_functional),
        definition_of_done=[
            "All acceptance criteria for the milestone are demonstrably met.",
            "Automated tests cover the new functionality and pass in CI.",
            "Documentation and runbook updates are merged.",
            "Security and compliance checks show no unresolved high findings.",
            "Product owner has signed the milestone acceptance note.",
        ],
    )
    blueprint.recompute_totals()
    if not blueprint.estimated_duration_weeks and scope.timeline_weeks:
        blueprint.estimated_duration_weeks = scope.timeline_weeks
    return blueprint


# --------------------------------------------------------------------------------------
# Legal / SOW
# --------------------------------------------------------------------------------------

_GOVERNING_LAW = {
    Jurisdiction.EU: "the laws of Ireland, with the courts of Dublin having exclusive jurisdiction",
    Jurisdiction.US: "the laws of the State of Delaware, excluding its conflict-of-law rules",
}

_REQUIRED_CLAUSES: dict[Jurisdiction, list[str]] = {
    Jurisdiction.EU: [
        "Definitions",
        "Scope of Services",
        "Deliverables and Milestones",
        "Acceptance Procedure",
        "Change Control",
        "Fees and Payment",
        "Data Protection and GDPR Compliance",
        "Artificial Intelligence Act Compliance",
        "Confidentiality",
        "Intellectual Property",
        "Warranties and Service Levels",
        "Limitation of Liability",
        "Term and Termination",
        "Governing Law and Jurisdiction",
    ],
    Jurisdiction.US: [
        "Definitions",
        "Scope of Services",
        "Deliverables and Milestones",
        "Acceptance Procedure",
        "Change Control",
        "Fees and Payment",
        "Data Protection and Privacy",
        "Confidentiality",
        "Intellectual Property and Work Product",
        "Representations and Warranties",
        "Indemnification",
        "Limitation of Liability",
        "Term and Termination",
        "Governing Law and Venue",
    ],
}


def _evidence_index(evidence_block: str) -> list[dict[str, str]]:
    """Parse the numbered evidence blocks injected by the retriever."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in evidence_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "] " in stripped:
            if current:
                entries.append(current)
            header = stripped[1:].split("] ", 1)
            current = {"id": header[0].strip(), "meta": header[1].strip(), "text": ""}
        elif current is not None:
            current["text"] = f"{current['text']} {stripped}".strip()
    if current:
        entries.append(current)

    for entry in entries:
        meta = entry.get("meta", "")
        for part in meta.split():
            if part.startswith("id="):
                entry["chunk_id"] = part[3:]
        if "chunk_id" not in entry:
            entry["chunk_id"] = entry["id"]
    return entries


def _cite(entries: list[dict[str, str]], keywords: Sequence[str], limit: int = 2) -> list[str]:
    """Return evidence ids whose text best matches ``keywords``."""
    scored: list[tuple[int, str]] = []
    lowered_keywords = [k.casefold() for k in keywords if k]
    for entry in entries:
        haystack = f"{entry.get('meta', '')} {entry.get('text', '')}".casefold()
        score = sum(1 for keyword in lowered_keywords if keyword in haystack)
        if score:
            scored.append((score, entry.get("chunk_id", entry.get("id", ""))))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    ids = []
    for _score, chunk_id in scored:
        if chunk_id and chunk_id not in ids:
            ids.append(chunk_id)
        if len(ids) >= limit:
            break
    return ids


def _clause_body(
    heading: str,
    scope: RequirementScope,
    blueprint: TechnicalBlueprint | None,
    entries: list[dict[str, str]],
    jurisdiction: Jurisdiction,
    payment_schedule: list[PaymentMilestone] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Compose one clause plus its citations and jurisdiction tags."""
    tags = [jurisdiction.value]
    if heading in ("Data Protection and GDPR Compliance",):
        tags += ["GDPR", "EU"]
    if heading == "Artificial Intelligence Act Compliance":
        tags += ["EU AI Act", "EU"]
    if heading == "Data Protection and Privacy" and jurisdiction is Jurisdiction.US:
        tags += ["CCPA", "US"]

    if heading == "Definitions":
        body = (
            '"Deliverables" means the items listed in Section 3 and any written variation agreed '
            'under Section 5. "Acceptance Criteria" means the GIVEN/WHEN/THEN conditions recorded '
            'in the Statement of Work backlog. "Personal Data" has the meaning given in the '
            "applicable data-protection law identified in Section 7."
        )
        return body, [], tags

    if heading == "Scope of Services":
        items = "; ".join(scope.deliverables[:8]) or "the services described in the agreed backlog"
        body = (
            f"The Supplier shall provide the following services for {scope.client_name}: {items}. "
            f"The engagement objective is: {scope.business_goal or 'as recorded in the discovery report'}. "
            "Services outside Section 3 are governed by Section 5 (Change Control)."
        )
        return body, [], tags

    if heading == "Deliverables and Milestones":
        if blueprint and blueprint.milestones:
            parts = []
            for index, milestone in enumerate(blueprint.milestones, start=1):
                parts.append(
                    f"Milestone {index} — {milestone.name} ({milestone.duration_weeks} week(s), "
                    f"{milestone.total_points} points): {milestone.objective}"
                )
            body = "The Supplier shall deliver the following milestones: " + " ".join(parts)
        else:
            body = "The Supplier shall deliver the items listed in Section 2 in the agreed sequence."
        return body, [], tags

    if heading == "Acceptance Procedure":
        body = (
            "Within five (5) business days of each milestone handover the Customer shall either "
            "accept the Deliverable in writing or issue a written notice specifying the respect in "
            "which it fails the Acceptance Criteria. Absent a notice within that period the "
            "Deliverable is deemed accepted. Remediation is performed at no additional charge where "
            "the Deliverable does not conform to the agreed Acceptance Criteria."
        )
        return body, [], tags

    if heading == "Change Control":
        body = (
            "Either party may request a change by written notice. The Supplier shall respond within "
            "three (3) business days with an impact assessment covering scope, schedule, fees and "
            "risk. No change is binding until both parties have signed a variation order; the "
            "Supplier is not obliged to commence changed work before that signature."
        )
        return body, [], tags

    if heading == "Fees and Payment":
        if payment_schedule:
            parts = [
                f"{p.name} — {p.percentage:.0f}% payable on {p.trigger.rstrip('.')}"
                for p in payment_schedule
            ]
            schedule_text = " ".join(parts)
        else:
            schedule_text = "Payment is made against milestones agreed in writing."
        body = (
            "The Customer shall pay the fees in accordance with the following milestone-linked "
            f"schedule: {schedule_text} All amounts are exclusive of VAT and any other applicable "
            "taxes, which the Customer shall pay in addition. Invoices are payable within thirty "
            "(30) days of receipt. Overdue amounts accrue interest at one percent (1%) per month "
            "or the maximum permitted by law, whichever is lower. Amounts are non-refundable except "
            "where this Agreement is terminated for the Supplier's uncured material breach."
        )
        return body, _cite(entries, ["payment", "invoice", "fee", "interest"]), tags

    if heading in ("Data Protection and GDPR Compliance",):
        body = (
            "Where the Supplier processes Personal Data on behalf of the Customer, it acts as "
            "processor and the Customer as controller. The Supplier shall process Personal Data "
            "only on documented instructions, implement appropriate technical and organisational "
            "measures, assist the Customer in responding to data-subject requests within the "
            "statutory period, notify any personal-data breach without undue delay and no later "
            "than 48 hours after becoming aware of it, and delete or return Personal Data on "
            "termination. Transfers outside the EEA require a valid transfer mechanism."
        )
        return body, _cite(entries, ["gdpr", "personal data", "processor", "breach", "transfer"]), tags

    if heading == "Artificial Intelligence Act Compliance":
        body = (
            "The parties shall classify each AI component against the EU AI Act risk tiers before "
            "go-live. The Supplier shall maintain a technical file for any high-risk component, "
            "implement human-oversight controls, log system events for traceability, and provide "
            "the transparency information required for deployers. Where a component is classified "
            "as prohibited the Supplier shall not place it on the market or put it into service."
        )
        return body, _cite(entries, ["ai act", "high-risk", "transparency", "oversight"]), tags

    if heading == "Data Protection and Privacy":
        body = (
            "The Supplier shall comply with applicable federal and state privacy law, including the "
            "California Consumer Privacy Act as amended, shall not sell or share Personal "
            "Information, shall provide reasonable security procedures appropriate to the nature of "
            "the information, and shall notify the Customer of any security incident without "
            "unreasonable delay."
        )
        return body, _cite(entries, ["ccpa", "privacy", "security", "personal information"]), tags

    if heading == "Confidentiality":
        body = (
            "Each party shall keep the other's Confidential Information secret for the term of this "
            "Agreement and for five (5) years thereafter, use it solely to perform this Agreement, "
            "and disclose it only to personnel bound by equivalent obligations. These obligations do "
            "not apply to information that is public, independently developed, or required to be "
            "disclosed by law."
        )
        return body, _cite(entries, ["confidential", "non-disclosure", "trade secret"]), tags

    if heading == "Intellectual Property":
        body = (
            "All Intellectual Property Rights in the Deliverables transfer to the Customer upon "
            "payment in full of the associated milestone fees. The Supplier retains ownership of its "
            "pre-existing materials and grants the Customer a perpetual, worldwide, royalty-free "
            "licence to use them to the extent embedded in the Deliverables. Third-party and "
            "open-source components are supplied under their respective licences."
        )
        return body, _cite(entries, ["intellectual property", "work product", "assignment", "license"]), tags

    if heading == "Intellectual Property and Work Product":
        body = (
            'All right, title and interest in the work product created under this Agreement shall '
            'vest in the Customer upon full payment. The Supplier hereby assigns all such rights and '
            'agrees to execute further documents reasonably requested to perfect that assignment. '
            'The Supplier retains ownership of its pre-existing tools and grants a non-exclusive, '
            'perpetual licence for their embedded use.'
        )
        return body, _cite(entries, ["work product", "assignment", "intellectual property"]), tags

    if heading == "Warranties and Service Levels":
        body = (
            "The Supplier warrants that the Deliverables will conform to the Statement of Work for "
            "ninety (90) days after acceptance and that the services will be performed with "
            "reasonable skill and care. Production services shall meet a 99.9% monthly availability "
            "target and a p95 response time of 400 ms. Service credits are the sole remedy for "
            "availability shortfalls."
        )
        return body, _cite(entries, ["warranty", "service level", "sla", "availability"]), tags

    if heading == "Representations and Warranties":
        body = (
            "Each party represents that it has full power and authority to enter into this "
            "Agreement and that performance will not violate any other agreement. The Supplier "
            "further warrants that the Deliverables are original or properly licensed, that no "
            "third-party right will be infringed by their use, and that services will be performed "
            "in a professional and workmanlike manner."
        )
        return body, _cite(entries, ["warranty", "representations", "authority"]), tags

    if heading == "Indemnification":
        body = (
            "The Supplier shall defend and indemnify the Customer against third-party claims that "
            "the Deliverables infringe a patent, copyright or trade secret, and shall pay resulting "
            "damages and costs finally awarded. The Customer shall indemnify the Supplier against "
            "claims arising from Customer materials and from data supplied in breach of law."
        )
        return body, _cite(entries, ["indemnif", "infringe", "third-party claim"]), tags

    if heading == "Limitation of Liability":
        cap = "the total fees paid or payable in the twelve (12) months preceding the claim"
        body = (
            f"Neither party is liable for indirect, incidental or consequential loss, nor for lost "
            f"profits or anticipated savings. Each party's aggregate liability is capped at {cap}. "
            "These limitations do not apply to death or personal injury caused by negligence, to "
            "breach of confidentiality, or to fraud."
        )
        return body, _cite(entries, ["liability", "cap", "consequential", "limitation"]), tags

    if heading == "Term and Termination":
        term = f"{max(1, scope.timeline_weeks or 12)} weeks"
        body = (
            f"This Agreement begins on the Effective Date and continues for {term}, extendable by "
            "written agreement. Either party may terminate for material breach not cured within "
            "thirty (30) days of written notice, or on insolvency of the other party. The Customer "
            "may terminate for convenience on thirty (30) days' notice, paying for work performed "
            "and non-cancellable commitments incurred to that date."
        )
        return body, _cite(entries, ["termination", "breach", "notice", "convenience"]), tags

    if heading in ("Governing Law and Jurisdiction", "Governing Law and Venue"):
        body = (
            f"This Agreement and any dispute arising out of it are governed by "
            f"{_GOVERNING_LAW[jurisdiction]}. The parties submit to the exclusive jurisdiction of "
            "those courts and exclude the United Nations Convention on Contracts for the "
            "International Sale of Goods."
        )
        return body, _cite(entries, ["governing law", "jurisdiction", "venue", "delaware"]), tags

    return (
        f"{heading}: the parties shall perform their respective obligations in accordance with the "
        "Statement of Work and applicable law.",
        [],
        tags,
    )


def legal_offline(
    scope_payload: dict[str, Any],
    blueprint_payload: dict[str, Any] | None,
    evidence_block: str,
    jurisdiction_value: str,
    repair_instructions: list[str] | None = None,
    previous_draft: dict[str, Any] | None = None,
) -> SOWDocument:
    """Deterministic Legal/SOW implementation."""
    scope = RequirementScope.model_validate(scope_payload)
    blueprint = (
        TechnicalBlueprint.model_validate(blueprint_payload) if blueprint_payload else None
    )
    try:
        jurisdiction = Jurisdiction(jurisdiction_value)
    except ValueError:
        jurisdiction = scope.jurisdiction

    entries = _evidence_index(evidence_block)
    headings = list(_REQUIRED_CLAUSES[jurisdiction])

    # Repair round: keep the previous draft and append remediation annexes so the
    # Critic can observe that its instructions were acted upon.
    repair_notes: list[str] = []
    if repair_instructions:
        headings = list(dict.fromkeys([*headings, "Remediation Annex"]))
        repair_notes = list(repair_instructions)

    # The payment schedule is derived first because the "Fees and Payment" clause
    # must quote it verbatim — the Critic checks the two for consistency.
    milestone_names = (
        [m.name for m in blueprint.milestones]
        if blueprint and blueprint.milestones
        else ["Delivery"]
    )
    if len(milestone_names) == 1:
        weights = [100.0]
    elif len(milestone_names) == 2:
        weights = [40.0, 60.0]
    else:
        base = round(100.0 / len(milestone_names), 2)
        weights = [base] * (len(milestone_names) - 1)
        weights.append(round(100.0 - sum(weights), 2))

    schedule = [
        PaymentMilestone(
            name=f"Payment {i} — {name}",
            trigger=f"Written acceptance of milestone '{name}' and issue of the corresponding invoice.",
            percentage=weight,
        )
        for i, (name, weight) in enumerate(zip(milestone_names, weights, strict=False), start=1)
    ]

    clauses: list[SOWClause] = []
    used_citations: list[str] = []
    for index, heading in enumerate(headings, start=1):
        if heading == "Remediation Annex":
            body = (
                "The following defects identified by the quality audit have been remediated in this "
                "revision: " + " ".join(f"({i}) {note}" for i, note in enumerate(repair_notes, 1))
            )
            citations = _cite(entries, ["compliance", "requirement"], limit=1)
            tags = [jurisdiction.value]
        else:
            body, citations, tags = _clause_body(
                heading, scope, blueprint, entries, jurisdiction, schedule
            )

        clauses.append(
            SOWClause(
                number=str(index),
                heading=heading,
                body=body,
                citations=citations,
                jurisdiction_tags=tags,
            )
        )
        used_citations.extend(citations)

    compliance_notes = list(scope.compliance_flags) or [
        "No specific regulatory trigger was detected in the requirement; a baseline "
        "confidentiality and data-handling regime applies."
    ]

    doc = SOWDocument(
        title=f"Statement of Work — {scope.title}",
        client_name=scope.client_name,
        vendor_name=scope.vendor_name,
        jurisdiction=jurisdiction,
        governing_law=_GOVERNING_LAW[jurisdiction],
        executive_summary=(
            f"{scope.client_name} engages {scope.vendor_name} to deliver {scope.title}. "
            f"{scope.business_goal or 'The engagement objective is recorded in the discovery report.'} "
            f"The work is organised into {len(milestone_names)} milestone(s) with a combined "
            f"estimate of {blueprint.estimated_total_points if blueprint else 0} story points."
        ),
        clauses=clauses,
        payment_schedule=schedule,
        compliance_notes=compliance_notes,
        assumptions_and_dependencies=nlp.dedupe(
            [*scope.assumptions, *scope.constraints[:5]]
        )
        or ["Delivery assumes timely access to client subject-matter experts."],
        acceptance_process=(
            "Acceptance is performed per Section 4 against the GIVEN/WHEN/THEN criteria recorded "
            "in the agreed backlog. Milestone acceptance releases the corresponding payment."
        ),
        change_control=(
            "Changes follow Section 5 and require a signed variation order before work starts."
        ),
        termination=(
            "Either party may terminate for uncured material breach on thirty (30) days' notice; "
            "the Customer may terminate for convenience on thirty (30) days' notice."
        ),
        data_protection=(
            "Processing of Personal Data is governed by Section 7 of this Statement of Work."
        ),
        intellectual_property=(
            "Intellectual Property Rights in the Deliverables transfer to the Customer on payment "
            "in full, as set out in Section 10."
        ),
        retrieved_evidence_ids=nlp.dedupe(used_citations) or [e["chunk_id"] for e in entries][:5],
    )

    if previous_draft:
        doc.compliance_notes.append(
            f"Revision issued in response to quality audit: {len(repair_notes)} finding(s) addressed."
        )
    return doc


# --------------------------------------------------------------------------------------
# Critic
# --------------------------------------------------------------------------------------

_AMBIGUITY_PHRASES = (
    "tbd", "to be determined", "to be decided", "as appropriate", "reasonable efforts",
    "best efforts", "as needed", "and/or", "etc.", "and so on", "various", "some",
    "floating", "reasonable time",
)


def critic_offline(
    sow_payload: dict[str, Any],
    evidence_block: str,
    jurisdiction_value: str,
    attempt: int = 0,
    blueprint_payload: dict[str, Any] | None = None,
) -> CritiqueReport:
    """Deterministic LLM-as-a-Judge implementation."""
    try:
        doc = SOWDocument.model_validate(sow_payload)
    except Exception as exc:
        return CritiqueReport(
            passed=False,
            quality_score=0.0,
            hallucination_score=1.0,
            grounding_ratio=0.0,
            summary=f"The draft could not be validated against the SOW schema: {exc}",
            findings=[
                CriticFinding(
                    category=FindingCategory.TEMPLATE_DEVIATION,
                    severity=Severity.CRITICAL,
                    location="global",
                    description="Draft does not satisfy the required SOW structure.",
                    remediation="Regenerate the document conforming to the SOWDocument schema.",
                )
            ],
            attempt=attempt,
            repair_instructions=["Regenerate the SOW conforming to the schema."],
        )

    entries = _evidence_index(evidence_block)
    known_ids = {e["chunk_id"] for e in entries if e.get("chunk_id")}
    known_ids |= {e.get("id", "") for e in entries}
    findings: list[CriticFinding] = []

    # --- 1. required clause coverage -----------------------------------------
    try:
        jurisdiction = Jurisdiction(jurisdiction_value)
    except ValueError:
        jurisdiction = doc.jurisdiction
    required = _REQUIRED_CLAUSES[jurisdiction]
    present_headings = {c.heading for c in doc.clauses}
    for heading in required:
        if heading not in present_headings:
            findings.append(
                CriticFinding(
                    category=FindingCategory.MISSING_CLAUSE,
                    severity=Severity.HIGH,
                    location="global",
                    description=f"Required {jurisdiction.value} clause '{heading}' is absent.",
                    evidence=f"Template for {jurisdiction.value} mandates {len(required)} clauses.",
                    remediation=f"Add clause '{heading}' to the Statement of Work.",
                )
            )

    # --- 2. citation existence (hallucination check) --------------------------
    unsupported: list[str] = []
    for clause in doc.clauses:
        for citation in clause.citations:
            if known_ids and citation not in known_ids:
                unsupported.append(citation)
                findings.append(
                    CriticFinding(
                        category=FindingCategory.HALLUCINATION,
                        severity=Severity.CRITICAL,
                        location=clause.number,
                        description=(
                            f"Clause '{clause.heading}' cites evidence id '{citation}' which does "
                            "not exist in the retrieved corpus."
                        ),
                        evidence=f"Known ids: {', '.join(sorted(known_ids)[:8])}",
                        remediation="Replace the citation with a retrieved chunk id or remove it.",
                    )
                )

    # --- 3. evidence grounding -------------------------------------------------
    citable = [
        c for c in doc.clauses if c.heading not in ("Definitions", "Scope of Services", "Deliverables and Milestones")
    ]
    grounded = [c for c in citable if c.citations and all(cit in known_ids for cit in c.citations)]
    grounding_ratio = (len(grounded) / len(citable)) if citable else 1.0

    if entries and grounding_ratio < 0.5:
        ungrounded = [c.number for c in citable if c not in grounded][:4]
        findings.append(
            CriticFinding(
                category=FindingCategory.UNSUPPORTED_CLAIM,
                severity=Severity.MEDIUM,
                location=", ".join(ungrounded) or "global",
                description=(
                    f"Only {grounding_ratio:.0%} of substantive clauses cite retrieved compliance "
                    "evidence."
                ),
                evidence=f"{len(entries)} evidence chunk(s) were available to the drafting agent.",
                remediation="Bind the listed clauses to the retrieved passages via `citations`.",
            )
        )

    # --- 4. commercial arithmetic ---------------------------------------------
    if doc.payment_schedule:
        total_pct = round(sum(p.percentage for p in doc.payment_schedule), 2)
        if abs(total_pct - 100.0) > 0.01:
            findings.append(
                CriticFinding(
                    category=FindingCategory.COMMERCIAL_RISK,
                    severity=Severity.HIGH,
                    location="payment_schedule",
                    description=f"Payment milestones sum to {total_pct}% instead of 100%.",
                    remediation="Rebalance the payment schedule so the percentages total 100%.",
                )
            )

    # --- 5. ambiguity scan -----------------------------------------------------
    ambiguous: list[str] = []
    for clause in doc.clauses:
        lowered = clause.body.casefold()
        hits = [phrase for phrase in _AMBIGUITY_PHRASES if phrase in lowered]
        if hits:
            ambiguous.append(clause.number)
            findings.append(
                CriticFinding(
                    category=FindingCategory.AMBIGUITY,
                    severity=Severity.LOW,
                    location=clause.number,
                    description=(
                        f"Clause '{clause.heading}' contains non-deterministic phrasing: "
                        f"{', '.join(hits)}."
                    ),
                    evidence=hits[0],
                    remediation="Replace subjective phrasing with a measurable obligation.",
                )
            )

    # --- 6. empty clause bodies ------------------------------------------------
    for clause in doc.clauses:
        if len(clause.body.strip()) < 40:
            findings.append(
                CriticFinding(
                    category=FindingCategory.TEMPLATE_DEVIATION,
                    severity=Severity.HIGH,
                    location=clause.number,
                    description=f"Clause '{clause.heading}' is empty or insubstantial.",
                    remediation="Draft substantive obligations for this clause.",
                )
            )

    # --- 7. scope drift against the blueprint ----------------------------------
    if blueprint_payload:
        try:
            blueprint = TechnicalBlueprint.model_validate(blueprint_payload)
            doc_blob = " ".join(c.body for c in doc.clauses).casefold()
            for milestone in blueprint.milestones:
                if milestone.name.casefold() not in doc_blob:
                    findings.append(
                        CriticFinding(
                            category=FindingCategory.SCOPE_DRIFT,
                            severity=Severity.MEDIUM,
                            location="Deliverables and Milestones",
                            description=(
                                f"Blueprint milestone '{milestone.name}' is not reflected in the "
                                "contract's milestone schedule."
                            ),
                            remediation="Align the SOW milestone table with the technical blueprint.",
                            target_node=NodeName.ARCHITECT,
                        )
                    )
        except Exception:
            pass

    # --- scoring ---------------------------------------------------------------
    penalty = sum(f.severity.weight for f in findings)
    quality_score = round(max(0.0, min(1.0, 1.0 - penalty)), 2)
    hallucination_score = round(
        min(1.0, len(unsupported) / max(1, sum(len(c.citations) for c in doc.clauses))), 2
    )
    blocking = [f for f in findings if f.is_blocking]
    passed = not blocking and quality_score >= 0.72

    repair_instructions = nlp.dedupe([f.remediation for f in findings if f.severity is not Severity.INFO])

    if passed:
        summary = (
            f"Quality audit passed with a score of {quality_score:.2f}. "
            f"{len(doc.clauses)} clauses reviewed, {len(grounded)} bound to retrieved compliance "
            f"evidence (grounding {grounding_ratio:.0%}). No blocking defects."
        )
    else:
        summary = (
            f"Quality audit FAILED with a score of {quality_score:.2f}. "
            f"{len(blocking)} blocking defect(s) and {len(findings)} finding(s) in total; the draft "
            "is returned to the Legal agent for remediation."
        )

    for finding in findings:
        if finding.category is FindingCategory.SCOPE_DRIFT:
            finding.target_node = NodeName.ARCHITECT

    return CritiqueReport(
        passed=passed,
        quality_score=quality_score,
        hallucination_score=hallucination_score,
        grounding_ratio=round(grounding_ratio, 3),
        summary=summary,
        findings=findings,
        checked_clauses=len(doc.clauses),
        unsupported_citations=nlp.dedupe(unsupported),
        repair_instructions=repair_instructions[:8],
        attempt=attempt,
    )


# --------------------------------------------------------------------------------------
# Tool planning
# --------------------------------------------------------------------------------------


#: Words that carry no project-key value.
_KEY_STOPWORDS = frozenset(
    {
        "project", "statement", "work", "sow", "the", "and", "for", "with", "platform",
        "system", "solution", "engagement", "delivery", "programme", "program",
        "initiative", "phase", "new", "implementation", "build", "service",
        # Filler produced by vague briefs such as "some kind of AI thing".
        "some", "kind", "kinds", "sort", "thing", "things", "stuff", "various",
        "general", "possible", "maybe", "help", "helps", "need", "needs", "want",
        "wants", "like", "using", "use", "based",
    }
)


def project_key_from_title(title: str) -> str:
    """Derive a Jira-safe project key from an engagement title.

    Prefers a distinctive token over generic delivery vocabulary, so
    "Statement of Work — Project Aurora Data Platform" yields ``AURORA`` rather than
    ``STATEM``, and "Some kind of AI thing for our sales team" yields ``SALES``.

    This is the single source of truth for key derivation: both the offline tool
    planner and the CSV exporter call it, so the live REST path and the offline import
    file can never disagree about the key they would create.
    """
    cleaned = "".join(ch if ch.isalnum() or ch == " " else " " for ch in title)
    words = [word for word in cleaned.split() if word]
    distinctive = [word for word in words if word.casefold() not in _KEY_STOPWORDS]

    candidates = distinctive or words
    if not candidates:
        return "SOW"

    # Prefer the first distinctive word long enough to be meaningful.
    primary = next((word for word in candidates if len(word) >= 4), candidates[0])
    key = primary.upper()[:8]

    if len(key) < 4 and len(candidates) > 1:
        secondary = next((w for w in candidates[1:] if w != primary), "")
        key += secondary.upper()[: 8 - len(key)]

    return (key or "SOW").ljust(2, "X")[:10]


#: Backwards-compatible private alias.
_project_key = project_key_from_title


def tool_plan_offline(
    blueprint_payload: dict[str, Any] | None,
    sow_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic function-calling plan for workspace provisioning.

    Emits exactly the call sequence the prompt contract prescribes: project → epics →
    stories (linked by ``parent_key``) → optional Notion summary page.
    """
    blueprint = TechnicalBlueprint.model_validate(blueprint_payload or {})
    sow = SOWDocument.model_validate(sow_payload) if sow_payload else None

    project_name = (sow.title if sow else "Engagement").replace("Statement of Work — ", "")
    project_key = _project_key(project_name)

    calls: list[dict[str, Any]] = [
        {
            "tool": "create_jira_project_workspace",
            "arguments": {
                "project_key": project_key,
                "name": project_name[:120],
                "description": (sow.executive_summary if sow else blueprint.solution_overview)[:1000],
            },
            "rationale": "Create the delivery workspace before any issue can be filed.",
        }
    ]

    # Planned epic keys map onto the epic call's ``client_ref``; the executor rewrites
    # each story's ``parent_key`` from that ref to the key Jira actually generated.
    for milestone in blueprint.milestones:
        for epic in milestone.epics:
            calls.append(
                {
                    "tool": "create_jira_issue",
                    "arguments": {
                        "project_key": project_key,
                        "summary": f"[{milestone.name}] {epic.name}",
                        "description": (
                            f"## Objective\n{epic.objective}\n\n"
                            f"## Milestone\n{milestone.name} ({milestone.duration_weeks} weeks)\n\n"
                            f"## Stories\n- " + "\n- ".join(s.title for s in epic.stories)
                        ),
                        "issue_type": "Epic",
                        "labels": [milestone.name.split()[0].lower()],
                        "client_ref": epic.key,
                    },
                    "rationale": f"Epic for {epic.name}; referenced later as {epic.key}.",
                }
            )
            for story in epic.stories:
                criteria = "\n".join(
                    f"- {criterion.render()}" for criterion in story.acceptance_criteria
                )
                calls.append(
                    {
                        "tool": "create_jira_issue",
                        "arguments": {
                            "project_key": project_key,
                            "summary": story.title,
                            "description": (
                                f"## Story\n{story.render_statement()}\n\n"
                                f"## Acceptance criteria\n{criteria or '- To be defined'}\n\n"
                                f"## Epic\n{epic.key}"
                            ),
                            "issue_type": "Story",
                            "story_points": story.story_points,
                            "priority": story.priority,
                            "labels": story.labels[:6],
                            "parent_key": epic.key,
                        },
                        "rationale": f"Story under epic {epic.key}.",
                    }
                )

    if sow is not None:
        calls.append(
            {
                "tool": "create_notion_page",
                "arguments": {
                    "title": f"{project_name} — Engagement Summary",
                    "content_markdown": (
                        f"# {project_name}\n\n{sow.executive_summary}\n\n"
                        f"## Milestones\n"
                        + "\n".join(f"- **{m.name}** — {m.objective}" for m in blueprint.milestones)
                        + "\n\n## Compliance\n"
                        + "\n".join(f"- {note}" for note in sow.compliance_notes)
                    ),
                },
                "rationale": "Give stakeholders a readable summary alongside the tracker.",
            }
        )

    return {
        "summary": (
            f"Provision {project_key} with {len(blueprint.milestones)} milestone(s), "
            f"{sum(len(m.epics) for m in blueprint.milestones)} epic(s) and "
            f"{len(blueprint.all_stories())} story(ies)."
        ),
        "calls": calls,
    }


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------


class OfflineLLMClient(BaseLLMClient):
    """Deterministic local engine exposed through the standard client interface."""

    provider = "offline"

    def default_model(self, tier: str = "reasoning") -> str:
        return OFFLINE_MODEL

    def _invoke(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        node: str,
        schema: type[BaseModel] | None,
    ) -> tuple[str, dict[str, Any]]:
        blob = "\n\n".join(m.content for m in messages)
        prompt_tokens = estimate_tokens(blob)

        payload = self._dispatch(node, schema, blob)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": estimate_tokens(text),
            "finish_reason": "stop",
        }

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, node: str, schema: type[BaseModel] | None, blob: str) -> dict[str, Any]:
        schema_name = schema.__name__ if schema is not None else ""
        handler = {
            "triage": self._triage,
            "architect": self._architect,
            "legal": self._legal,
            "critic": self._critic,
            "tools": self._tools,
        }.get(node)

        if handler is None:
            handler = {
                "RequirementScope": self._triage,
                "TechnicalBlueprint": self._architect,
                "SOWDocument": self._legal,
                "CritiqueReport": self._critic,
                "ToolCallPlan": self._tools,
            }.get(schema_name)

        if handler is None:
            return self._generic(schema)

        result = handler(blob)
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        return result

    # ------------------------------------------------------------------ nodes
    def _triage(self, blob: str) -> RequirementScope:
        raw = extract_tagged(blob, "raw_requirement") or blob
        answers = extract_tagged(blob, "clarification_answers", default="")
        prior = extract_tagged_json(blob, "prior_scope")
        if isinstance(prior, list) and prior:
            prior = prior[-1]
        if not isinstance(prior, dict):
            prior = None
        return triage_offline(raw, prior, answers)

    def _architect(self, blob: str) -> TechnicalBlueprint:
        scope = extract_tagged_json(blob, "scope") or {}
        return architect_offline(scope if isinstance(scope, dict) else {})

    def _legal(self, blob: str) -> SOWDocument:
        scope = extract_tagged_json(blob, "scope") or {}
        blueprint = extract_tagged_json(blob, "blueprint")
        evidence = extract_tagged(blob, "evidence", default="")
        jurisdiction = extract_tagged(blob, "jurisdiction", default="EU").strip() or "EU"
        critique = extract_tagged_json(blob, "critique") or {}
        previous = extract_tagged_json(blob, "previous_draft")
        repairs: list[str] = []
        if isinstance(critique, dict):
            repairs = coerce_list(critique.get("repair_instructions"))
        return legal_offline(
            scope if isinstance(scope, dict) else {},
            blueprint if isinstance(blueprint, dict) else None,
            evidence,
            jurisdiction,
            repair_instructions=[str(r) for r in repairs],
            previous_draft=previous if isinstance(previous, dict) else None,
        )

    def _critic(self, blob: str) -> CritiqueReport:
        sow = extract_tagged_json(blob, "draft_sow") or {}
        evidence = extract_tagged(blob, "evidence", default="")
        jurisdiction = extract_tagged(blob, "jurisdiction", default="EU").strip() or "EU"
        blueprint = extract_tagged_json(blob, "blueprint")
        attempt_raw = extract_tagged(blob, "attempt", default="0").strip()
        try:
            attempt = int(attempt_raw)
        except ValueError:
            attempt = 0
        return critic_offline(
            sow if isinstance(sow, dict) else {},
            evidence,
            jurisdiction,
            attempt=attempt,
            blueprint_payload=blueprint if isinstance(blueprint, dict) else None,
        )

    def _tools(self, blob: str) -> dict[str, Any]:
        blueprint = extract_tagged_json(blob, "blueprint")
        sow = extract_tagged_json(blob, "sow")
        return tool_plan_offline(
            blueprint if isinstance(blueprint, dict) else None,
            sow if isinstance(sow, dict) else None,
        )

    # ------------------------------------------------------------------ fallback
    @staticmethod
    def _generic(schema: type[BaseModel] | None) -> dict[str, Any]:
        """Emit a minimal schema-valid object for unmodelled requests."""
        if schema is None:
            return {"text": "offline engine: no schema requested"}
        payload: dict[str, Any] = {}
        for name, field in schema.model_fields.items():
            if not field.is_required():
                continue
            payload[name] = _infer_placeholder(field.annotation)
        parsed = loads_lenient(json.dumps(payload))
        return parsed if isinstance(parsed, dict) else {}


def _infer_placeholder(annotation: Any) -> Any:
    import typing

    origin = typing.get_origin(annotation)
    if annotation is str:
        return "offline"
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if origin in (list, set, tuple):
        return []
    if origin is dict:
        return {}
    return None
