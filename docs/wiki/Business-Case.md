# Business case

## The cost that does not appear on any invoice

A B2B services engagement begins long before a contract exists. Someone senior — a
solution architect, a delivery lead, a founder — reads a client's brief, works out what
is actually being asked for, and writes it up as a Statement of Work. Then a delivery
manager turns that into a plan.

That work is real, skilled, and almost never billed. It sits in pre-sales.

```mermaid
flowchart TB
    subgraph today["How it works today"]
        direction TB
        T1["Brief arrives"] --> T2["Solution architect<br/>interprets it<br/><b>4–8 h</b>"]
        T2 --> T3["Legal reviews<br/>the draft<br/><b>4–16 h</b>"]
        T3 --> T4["Delivery manager<br/>builds the backlog<br/><b>8–16 h</b>"]
        T4 --> T5["Revisions after<br/>client feedback<br/><b>4–12 h</b>"]
    end

    subgraph cost["What it costs"]
        direction TB
        C1["<b>20–50 person-hours</b><br/>per engagement"]
        C2["<b>2–4 weeks</b><br/>before work starts"]
        C3["Ambiguity that survives<br/>into the signed contract"]
    end

    today --> cost

    style T2 fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
    style T3 fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
    style T4 fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
    style T5 fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
    style C1 fill:#78350f,stroke:#b45309,color:#fef3c7
    style C2 fill:#78350f,stroke:#b45309,color:#fef3c7
    style C3 fill:#78350f,stroke:#b45309,color:#fef3c7
```

The third cost is the expensive one. A Statement of Work that is silent on data
protection, or that states a liability cap without defining its carve-outs, does not fail
at signature. It fails eight months later, in a dispute, when the person who wrote it has
moved to another project.

## What the platform changes

```mermaid
flowchart LR
    B["Brief"] --> S["SOWSprint.ai"]
    S --> W["Statement of Work"]
    S --> P["Backlog"]
    S --> A["Audit report"]

    W --> G{"Human<br/>approval"}
    P --> G
    A --> G
    G -->|approved| J["Jira workspace<br/>created"]
    G -->|approved| N["Notion page<br/>published"]
    G -->|changes requested| S

    style S fill:#065f46,stroke:#10b981,color:#d1fae5
    style G fill:#78350f,stroke:#f59e0b,color:#fef3c7
    style J fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style N fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
```

| | Manual | SOWSprint.ai |
| :-- | :-- | :-- |
| Time to first draft | 1–2 weeks | **90–210 seconds** |
| Time to signed SOW | 3–6 weeks | **Same day** (review, not authorship) |
| Cost per engagement | 20–50 person-hours | **$0.05–0.20** of model spend |
| Compliance coverage | Depends who reviewed it | Checked against a tagged corpus, every time |
| Consistency | Varies by author | Reproducible and auditable |
| Backlog | Written separately, drifts from the SOW | Derived from the same scope object |

## The economics, stated honestly

The saving is **not** "the platform writes your contracts". It is that a human moves from
*authorship* to *review*. That is the whole of the value, and it is enough:

- 20–50 hours of senior time becomes 1–3 hours of review
- The review is better, because the reviewer has a compliance audit in front of them
  rather than a blank page
- The plan and the contract cannot drift apart, because both are generated from one
  scope object

At a blended rate of €80/hour, one engagement recovers roughly **€1,600–4,000** of
unbilled effort. A 12-person consultancy running three engagements a month is looking at
a six-figure annual figure that never appeared on a timesheet.

**What it does not do**, stated because overclaiming here would be dishonest: it does not
replace legal counsel, it does not know your commercial position, and it will not tell
you whether the deal is a good idea. It removes the blank-page problem and the
copy-paste-from-the-last-contract problem. Those are the two that consume the time.

## Where the risk actually moves

The platform does not remove legal risk; it moves it earlier, where it is cheap to fix.

```mermaid
flowchart LR
    subgraph before["Before: risk accumulates silently"]
        direction LR
        B1["Draft"] --> B2["Review"] --> B3["Sign"] --> B4["Delivery"] --> B5["Dispute"]
    end
    subgraph after["After: risk surfaces at the gate"]
        direction LR
        A1["Draft"] --> A2["<b>Audit</b><br/>findings shown"] --> A3["Human decides"] --> A4["Sign"] --> A5["Delivery"]
    end

    style B5 fill:#7f1d1d,stroke:#b91c1c,color:#fee2e2
    style A2 fill:#065f46,stroke:#10b981,color:#d1fae5
    style A3 fill:#78350f,stroke:#f59e0b,color:#fef3c7
```

Findings are named by clause, severity and reasoning, and the human sees them **before**
approving. The audit is not a rubber stamp: on this build it caught a breach-notification
window defined by reference to runbook clause numbers that were not part of the contract
— an unenforceable obligation that would have looked fine to a reader in a hurry.

→ [Who it is for](Who-It-Is-For.md) · [Quality assurance](Quality-Assurance.md) · [Roadmap](Roadmap.md)
