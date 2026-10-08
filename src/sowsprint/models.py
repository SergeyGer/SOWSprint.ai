"""Domain model for the SOWSprint pipeline.

These Pydantic models serve three roles simultaneously:

1. **Graph state contract** — the typed payload threaded through the LangGraph nodes.
2. **LLM structured-output schema** — each one is handed to the provider as a JSON
   schema, so agent output is validated before it can reach a downstream node.
3. **UI rendering model** — Chainlit steps and the final report render directly from
   these objects, so the presentation layer never parses free text.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class Jurisdiction(str, Enum):
    """Compliance regime driving retrieval-time metadata filtering."""

    EU = "EU"
    US = "US"

    @property
    def label(self) -> str:
        return {
            Jurisdiction.EU: "European Union — GDPR & EU AI Act",
            Jurisdiction.US: "United States — SEC & Delaware corporate law",
        }[self]


class ScopeStatus(str, Enum):
    """Triage verdict; controls the human-in-the-loop branch of the state graph."""

    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> float:
        return {
            Severity.INFO: 0.0,
            Severity.LOW: 0.05,
            Severity.MEDIUM: 0.12,
            Severity.HIGH: 0.25,
            Severity.CRITICAL: 0.5,
        }[self]


class FindingCategory(str, Enum):
    HALLUCINATION = "hallucination"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    COMPLIANCE_GAP = "compliance_gap"
    SCOPE_DRIFT = "scope_drift"
    COMMERCIAL_RISK = "commercial_risk"
    AMBIGUITY = "ambiguity"
    MISSING_CLAUSE = "missing_clause"
    TEMPLATE_DEVIATION = "template_deviation"


class NodeName(str, Enum):
    TRIAGE = "triage"
    ARCHITECT = "architect"
    LEGAL = "legal"
    CRITIC = "critic"
    TOOLS = "tools"
    FINALIZE = "finalize"


# --------------------------------------------------------------------------------------
# Stage 1 — Triage output
# --------------------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    """A single blocking scoping question posed to the human in the loop."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable slug, e.g. 'integration_targets'.")
    question: str = Field(description="One crisp question, answerable in a sentence.")
    why_blocking: str = Field(description="Why the contract cannot be drafted without it.")
    variable: str = Field(description="Name of the scoping variable this resolves.")
    suggested_answers: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Optional quick-pick answers rendered as UI buttons.",
    )


class RequirementScope(BaseModel):
    """Normalised interpretation of the raw customer requirement."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short engagement title.")
    client_name: str = Field(default="Client", description="Buying organisation.")
    vendor_name: str = Field(default="SOWSprint Delivery Team")
    business_goal: str = Field(description="The outcome the client is buying.")
    problem_statement: str = Field(default="")
    deliverables: list[str] = Field(default_factory=list, min_length=0)
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    target_users: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    compliance_flags: list[str] = Field(
        default_factory=list,
        description="Regulatory triggers detected, e.g. 'personal data', 'PCI DSS'.",
    )
    jurisdiction: Jurisdiction = Jurisdiction.EU
    timeline_weeks: int | None = Field(default=None, ge=1, le=260)
    budget_range: str | None = None
    team_size: int | None = Field(default=None, ge=1, le=500)
    status: ScopeStatus = ScopeStatus.INCOMPLETE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarifying_questions: list[ClarificationQuestion] = Field(default_factory=list)
    missing_variables: list[str] = Field(default_factory=list)
    detected_signals: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Explainability trail: which matched keywords drove which inference. Values are "
            "lists of matched signals, or nested maps (e.g. compliance/jurisdiction) when a "
            "single variable is supported by several distinct signal families."
        ),
    )
    revision: int = Field(default=0, ge=0, description="Incremented on each clarification round.")

    @field_validator("clarifying_questions")
    @classmethod
    def _at_most_three_questions(
        cls, v: list[ClarificationQuestion]
    ) -> list[ClarificationQuestion]:
        """The PRD mandates exactly three targeted questions, never a wall of them."""
        return v[:3]

    @property
    def is_actionable(self) -> bool:
        return self.status is ScopeStatus.COMPLETE and bool(self.deliverables)

    def question_texts(self) -> list[str]:
        return [q.question for q in self.clarifying_questions]

    def short_summary(self) -> str:
        parts = [self.title, f"{len(self.deliverables)} deliverables"]
        if self.timeline_weeks:
            parts.append(f"{self.timeline_weeks}w")
        if self.jurisdiction:
            parts.append(self.jurisdiction.value)
        return " · ".join(parts)


# --------------------------------------------------------------------------------------
# Stage 2 — Architect output
# --------------------------------------------------------------------------------------


class TechChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: str = Field(description="e.g. 'frontend', 'data', 'infrastructure'.")
    technology: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    given: str
    when: str
    then: str

    def render(self) -> str:
        return f"GIVEN {self.given} WHEN {self.when} THEN {self.then}"


class UserStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Jira-style issue key fragment, e.g. 'AUTH-3'.")
    title: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    story_points: int = Field(default=3, ge=1, le=21)
    priority: Literal["Highest", "High", "Medium", "Low", "Lowest"] = "Medium"
    labels: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    def render_statement(self) -> str:
        return f"As a {self.as_a}, I want {self.i_want}, so that {self.so_that}."


class Epic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    objective: str
    stories: list[UserStory] = Field(default_factory=list)

    @property
    def total_points(self) -> int:
        return sum(s.story_points for s in self.stories)


class Milestone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    objective: str
    duration_weeks: int = Field(default=2, ge=1, le=52)
    epics: list[Epic] = Field(default_factory=list)

    @property
    def total_points(self) -> int:
        return sum(e.total_points for e in self.epics)

    @property
    def story_count(self) -> int:
        return sum(len(e.stories) for e in self.epics)


class TechnicalBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_overview: str
    architecture_style: str = Field(default="Modular monolith with event-driven workers")
    tech_stack: list[TechChoice] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    estimated_total_points: int = Field(default=0, ge=0)
    estimated_duration_weeks: int = Field(default=0, ge=0)

    def all_stories(self) -> list[UserStory]:
        return [s for m in self.milestones for e in m.epics for s in e.stories]

    def recompute_totals(self) -> None:
        self.estimated_total_points = sum(m.total_points for m in self.milestones)
        self.estimated_duration_weeks = sum(m.duration_weeks for m in self.milestones)


# --------------------------------------------------------------------------------------
# Stage 3 — Legal / SOW output
# --------------------------------------------------------------------------------------


class PaymentMilestone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    trigger: str = Field(description="Objective event releasing the payment.")
    percentage: float = Field(ge=0.0, le=100.0)
    amount_usd: float | None = Field(default=None, ge=0.0)


class SOWClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: str = Field(description="e.g. '4.2'.")
    heading: str
    body: str
    citations: list[str] = Field(
        default_factory=list,
        description="Chunk IDs retrieved from the compliance corpus supporting this clause.",
    )
    jurisdiction_tags: list[str] = Field(default_factory=list)


class SOWDocument(BaseModel):
    """The generated Statement of Work."""

    model_config = ConfigDict(extra="forbid")

    title: str
    client_name: str
    vendor_name: str
    effective_date: date = Field(default_factory=date.today)
    jurisdiction: Jurisdiction = Jurisdiction.EU
    governing_law: str = ""
    executive_summary: str = ""
    clauses: list[SOWClause] = Field(default_factory=list)
    payment_schedule: list[PaymentMilestone] = Field(default_factory=list)
    total_value_usd: float | None = Field(default=None, ge=0.0)
    compliance_notes: list[str] = Field(default_factory=list)
    assumptions_and_dependencies: list[str] = Field(default_factory=list)
    acceptance_process: str = ""
    change_control: str = ""
    termination: str = ""
    data_protection: str = ""
    intellectual_property: str = ""
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def word_count(self) -> int:
        return len(" ".join(c.body for c in self.clauses).split())

    def clause_index(self) -> dict[str, SOWClause]:
        return {c.number: c for c in self.clauses}


# --------------------------------------------------------------------------------------
# Stage 4 — Critic output
# --------------------------------------------------------------------------------------


class CriticFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FindingCategory
    severity: Severity
    location: str = Field(description="Clause number, milestone key, or 'global'.")
    description: str
    evidence: str = Field(default="", description="Quote or retrieval id proving the finding.")
    remediation: str = Field(description="Concrete instruction for the upstream agent.")
    target_node: NodeName = NodeName.LEGAL

    @property
    def is_blocking(self) -> bool:
        return self.severity in (Severity.HIGH, Severity.CRITICAL)


class CritiqueReport(BaseModel):
    """LLM-as-a-Judge verdict gating progression to the deliverables stage."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    quality_score: float = Field(ge=0.0, le=1.0)
    hallucination_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="0 = fully grounded, 1 = fabricated."
    )
    grounding_ratio: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Share of claims traceable to retrieved evidence."
    )
    summary: str = ""
    findings: list[CriticFinding] = Field(default_factory=list)
    checked_clauses: int = Field(default=0, ge=0)
    unsupported_citations: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(
        default_factory=list, description="Ordered fix list handed back to the Legal node."
    )
    attempt: int = Field(default=0, ge=0)

    @property
    def blocking_findings(self) -> list[CriticFinding]:
        return [f for f in self.findings if f.is_blocking]


# --------------------------------------------------------------------------------------
# Retrieval + telemetry value objects
# --------------------------------------------------------------------------------------


class RetrievedChunk(BaseModel):
    """One passage returned by the compliance-aware retriever."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    source: str
    jurisdiction: str
    doc_type: str = "contract_clause"
    citation: str = ""
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float = 0.0
    rank: int = 0

    def to_context_block(self, index: int) -> str:
        """Render as an evidence block with an inline citation marker.

        The ``id=`` token is what the Legal agent copies into ``SOWClause.citations``
        and what the Critic later verifies against the corpus, so the two agents agree
        on citation identity without sharing any other state.
        """
        head = (
            f"[{index}] {self.citation or self.source} "
            f"(jurisdiction={self.jurisdiction}) id={self.chunk_id}"
        )
        return f"{head}\n{self.text.strip()}"


class TokenUsage(BaseModel):
    """Token + cost accounting for a single LLM call."""

    model_config = ConfigDict(extra="allow")

    model: str
    provider: str = "mock"
    node: str = "unknown"
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    success: bool = True
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CostReport(BaseModel):
    """Aggregated session telemetry rendered by the sticky dashboard widget."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    budget_usd: float = 2.50
    by_node: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_model: dict[str, dict[str, float]] = Field(default_factory=dict)
    latency_ms_total: float = 0.0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_call_at: datetime | None = None

    @property
    def budget_used_pct(self) -> float:
        if self.budget_usd <= 0:
            return 0.0
        return min(100.0, 100.0 * self.cost_usd / self.budget_usd)

    @property
    def budget_remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.cost_usd)

    @property
    def avg_latency_ms(self) -> float:
        return self.latency_ms_total / self.calls if self.calls else 0.0


class JiraSyncResult(BaseModel):
    """Outcome of a downstream tool-calling integration."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["jira", "notion"]
    dry_run: bool = True
    created: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""

    @property
    def created_count(self) -> int:
        return len(self.created)


class ArtifactRef(BaseModel):
    """A file produced for the user at the end of the run."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "sow_pdf",
        "sow_markdown",
        "backlog_markdown",
        "backlog_json",
        "jira_csv",
        "audit_markdown",
        "report_json",
    ]
    path: str
    label: str
    size_bytes: int = 0

    def exists(self) -> bool:
        from pathlib import Path

        return Path(self.path).is_file()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def stable_id(*parts: str, length: int = 12) -> str:
    """Deterministic short identifier derived from the given parts."""
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def default_signing_deadline(days: int = 14) -> date:
    return date.today() + timedelta(days=days)


AnnotatedGoal = Annotated[str, Field(min_length=3)]


# --------------------------------------------------------------------------------------
# Severity calibration policy
# --------------------------------------------------------------------------------------

#: Finding categories that may block a contract.
#:
#: Only defects that make an agreement unenforceable or expose a party to material
#: loss belong here. A judge model reliably identifies *what* is wrong but is
#: inconsistent about *how much it matters* — the same draft drew HIGH and CRITICAL
#: ratings on different runs for comparable issues. Deriving the blocking decision
#: from the category rather than from the model's severity label makes the gate
#: reproducible.
BLOCKING_CATEGORIES: frozenset[FindingCategory] = frozenset(
    {
        FindingCategory.HALLUCINATION,  # a fabricated citation
        FindingCategory.MISSING_CLAUSE,  # a mandatory clause absent
        FindingCategory.COMMERCIAL_RISK,  # payment arithmetic or unbounded liability
        FindingCategory.SCOPE_DRIFT,  # the contract contradicts the plan
    }
)

#: Severity ceiling applied to advisory categories when the model over-escalates.
ADVISORY_SEVERITY_CEILING = Severity.MEDIUM


def calibrate_severity(finding: CriticFinding) -> CriticFinding:
    """Cap the severity of advisory findings so they cannot block a contract.

    Returns the finding unchanged when its category is genuinely blocking.
    """
    if finding.category in BLOCKING_CATEGORIES:
        return finding
    if finding.severity.weight > ADVISORY_SEVERITY_CEILING.weight:
        finding.severity = ADVISORY_SEVERITY_CEILING
    return finding


#: Words too generic to identify a clause topic.
_CLAUSE_TOPIC_STOPWORDS = frozenset(
    {
        "clause", "clauses", "section", "sections", "contract", "agreement", "sow",
        "missing", "absent", "include", "includes", "including", "require", "required",
        "requires", "address", "addresses", "addressing", "provide", "provides",
        "statement", "work", "document", "does", "not", "the", "and", "for", "with",
        "that", "this", "which", "from", "into", "under", "上述", "must", "should",
    }
)


def clause_topic_terms(text: str, *, limit: int = 6) -> list[str]:
    """Extract the significant topic words from a finding description."""
    import re

    words = re.findall(r"[a-z][a-z\-]{3,}", (text or "").casefold())
    return [w for w in dict.fromkeys(words) if w not in _CLAUSE_TOPIC_STOPWORDS][:limit]


def verify_missing_clause_findings(
    document: SOWDocument, findings: list[CriticFinding]
) -> tuple[list[CriticFinding], list[str]]:
    """Check ``missing_clause`` findings against the document before trusting them.

    A judge model occasionally reports a clause as absent when it is present — observed
    live: a CRITICAL "does not include a clause addressing governing law and dispute
    resolution" against a contract whose Clause 25 is titled exactly that, and which
    blocked the run.

    This is a deterministic cross-check, not a second opinion: the document either
    contains a clause about the topic or it does not. Confirmed-absent findings pass
    through untouched; contradicted ones are downgraded to LOW so they inform the
    reviewer without halting a delivery.
    """
    if not findings:
        return findings, []

    haystack = " ".join(
        f"{clause.heading} {clause.body}" for clause in document.clauses
    ).casefold()
    headings = " ".join(clause.heading for clause in document.clauses).casefold()

    contradicted: list[str] = []
    for finding in findings:
        if finding.category is not FindingCategory.MISSING_CLAUSE:
            continue

        terms = clause_topic_terms(f"{finding.description} {finding.remediation}")
        if not terms:
            continue

        # A heading match is decisive; a body match needs a stronger signal.
        heading_hits = [term for term in terms if term in headings]
        body_hits = [term for term in terms if term in haystack]
        confirmed_present = bool(heading_hits) or len(body_hits) >= max(2, len(terms) - 1)
        if not confirmed_present:
            continue

        contradicted.append(
            f"{finding.location}: judge reported '{terms[0]}' missing, but the document "
            f"covers it (matched: {', '.join(heading_hits or body_hits[:3])})"
        )
        finding.severity = Severity.LOW
        finding.description = (
            f"[auto-downgraded: the document appears to cover this] {finding.description}"
        )

    return findings, contradicted
