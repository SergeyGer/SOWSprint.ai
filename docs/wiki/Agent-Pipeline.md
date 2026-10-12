# Agent pipeline

Eight stages. Each is a method on `AgentNodes`, each takes and returns typed state, and
each emits step events the UI renders as they happen.

```mermaid
flowchart TB
    T["🔍 <b>Triage</b><br/>reads the brief, extracts scope variables,<br/>flags compliance triggers"]
    C["❓ <b>Clarify</b><br/>asks only for the variables it could not infer"]
    A["📐 <b>Architect</b><br/>milestones → epics → stories<br/>with testable acceptance criteria"]
    L["⚖️ <b>Legal</b><br/>drafts the contract from scope<br/>and retrieved compliance evidence"]
    R["🛡️ <b>Critic</b><br/>audits the draft, calibrates severity,<br/>verifies its own claims"]
    P["✋ <b>Approval</b><br/>human gate — nothing provisioned yet"]
    X["🔧 <b>Tools</b><br/>creates the Jira workspace<br/>and the Notion page"]
    F["📦 <b>Finalize</b><br/>renders SOW, backlog, audit<br/>PDF · CSV · JSON"]

    T --> C --> A --> L --> R --> P --> X --> F
    R -.->|"audit failed"| L
    C -.->|"insufficient answers"| T

    style T fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style C fill:#78350f,stroke:#f59e0b,color:#fef3c7
    style A fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style L fill:#065f46,stroke:#10b981,color:#d1fae5
    style R fill:#7c2d12,stroke:#ea580c,color:#fed7aa
    style P fill:#78350f,stroke:#f59e0b,color:#fef3c7
    style X fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style F fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
```

---

## 🔍 Triage — understanding what was actually asked

Extracts a `RequirementScope` from unstructured text: deliverables, business goal,
timeline, budget, user population, integrations, success metrics, and **compliance
triggers** (personal data, health data, financial data, employment data, AI decisions).

The critical design choice: **the model decides what it cannot infer.** Anything missing
from a critical set leaves the scope `INCOMPLETE`, and an incomplete scope does not
proceed to drafting. A Statement of Work written over an unresolved gap has no enforceable
subject matter, and no amount of good drafting downstream repairs that.

Observed live, and worth quoting because it is the behaviour that matters:

> *Regulatory triggers detected: possible employment data (unverified: staff-level
> reporting)*

The agent inferred from "200 warehouse staff" that employee personal data may be in scope,
and said so rather than assuming either way.

## ❓ Clarify — asking instead of guessing

Presents up to three questions, each with **why it blocks**, not just what is missing, plus
suggested answers drawn from similar past scopes. The user answers in one message.

Bounded at three rounds. After that the scope is accepted with **documented assumptions** —
injected into the contract's assumptions clause. The alternative, looping forever, is worse
than proceeding with a stated assumption that a human can challenge.

## 📐 Architect — from scope to plan

Produces a `TechnicalBlueprint`: milestones spanning the timeline, epics per workstream,
stories per epic, each with story points, dependencies and a definition of done.

The constraint that makes it useful: **every story carries acceptance criteria in
GIVEN/WHEN/THEN form**. A story that cannot be tested cannot be accepted, and "the user can
log in" is not an acceptance criterion. This is validated structurally — a story with fewer
than two criteria fails validation and is regenerated.

## ⚖️ Legal — drafting with evidence

Receives the scope, the blueprint and the **retrieved compliance passages**, and produces a
`SOWDocument`: numbered clauses, citations linking each clause back to specific retrieved
evidence, jurisdiction tags, a payment schedule, and assumptions.

The citation discipline is enforced: the prompt requires `id=` values that exist in the
retrieved evidence, and the audit checks that cited identifiers are real.

Clause templates differ by regime, and the dual-regime template adds three clauses that
only exist when EU and US obligations meet.
→ [Compliance-aware RAG](Compliance-RAG)

## 🛡️ Critic — the adversarial audit

A separate agent, and ideally a **separate vendor** from the drafter. A model auditing its
own output is measurably weaker at catching its own hallucinations, so the default
configuration deliberately splits them.

The Critic is where most of the engineering effort went, because a judge that is wrong in
either direction is worse than no judge:

```mermaid
flowchart TB
    RAW["Judge returns verdict<br/>+ findings + score"]
    CAL["<b>1. Calibrate severity</b><br/>category decides blocking,<br/>not the model's own label"]
    VER["<b>2. Verify claims</b><br/>cross-check every 'missing clause'<br/>against the document"]
    RC["<b>3. Recompute</b><br/>if anything was dismissed,<br/>recompute score and verdict"]
    DE{"<b>4. Degraded?</b><br/>did the judge fall back<br/>to the offline engine?"}
    WARN["Mark the verdict unreliable<br/>and say so in the UI"]
    OUT["Verdict stands"]

    RAW --> CAL --> VER --> RC --> DE
    DE -->|yes| WARN
    DE -->|no| OUT

    style CAL fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style VER fill:#065f46,stroke:#10b981,color:#d1fae5
    style RC fill:#065f46,stroke:#10b981,color:#d1fae5
    style WARN fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
```

Each step exists because of a specific observed failure. They are documented in
[Quality assurance](Quality-Assurance), including the one where a judge reported eleven
mandatory clauses absent from a contract containing all eleven.

## ✋ Approval — the human gate

Every planned tool call is listed **before** anything is sent: the workspace key, each epic,
each story. The reviewer sees what approving means, then approves, rejects, or asks for a
change in free text.

This gate is why the platform is deployable in a regulated environment. An autonomous
system with write access to a customer's Jira is not something a delivery director signs
off on; the same system with a review step is.

## 🔧 Tools — provisioning

Executes the approved plan through a tool registry, with **dry-run as the default**.
Dry-run validates every payload and shows it, then discards it. Going live is one setting,
and the report states plainly which mode ran.

## 📦 Finalize — artefacts

| Artefact | Purpose |
| :-- | :-- |
| `statement_of_work.md` / `.pdf` | The contract |
| `backlog.md` | Human-readable delivery plan |
| `jira_backlog.csv` | Importable, with an importer-safe variant |
| `backlog.json` | Machine-readable, for tooling |
| `quality_audit.md` | Clause-referenced findings |
| `run_report.json` | Full structured record of the engagement |

---

## Cost per stage

Measured on a real EU engagement, Claude Haiku drafting and GPT-5.1 auditing:

```mermaid
xychart-beta
    title "Model spend by stage (USD, illustrative run)"
    x-axis ["triage", "architect", "legal", "critic"]
    y-axis "USD" 0 --> 0.09
    bar [0.001, 0.007, 0.008, 0.02]
```

Two things are worth noting from the real numbers rather than the illustration:

- **The audit costs more than the drafting.** That is the right allocation: a false
  positive in the audit costs a full contract rewrite.
- **The judge is the largest single line item** at roughly $0.02 per engagement, against
  $0.05–0.20 total. It is also the component most worth spending on.

Prompt caching reduces repeat-call input cost by about 90% on the static persona and
schema prefix. → [Cost & observability](Cost-Observability)

→ [Architecture](Architecture) · [Quality assurance](Quality-Assurance)
