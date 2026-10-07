# Architecture Decision Records

Short, dated records of the decisions that shaped this system — what was chosen, what
was rejected, and what it cost.

| ADR | Decision | Why it matters |
| :-- | :-- | :-- |
| [0001](0001-offline-first-reasoning.md) | Offline-first reasoning instead of cloud-only | Makes the whole pipeline deterministic and runnable with zero credentials |
| [0002](0002-metadata-filtering-at-query-level.md) | Jurisdiction filtering pushed into the vector engine | Wrong-regime clauses can never enter the candidate pool |
| [0003](0003-reciprocal-rank-fusion.md) | Reciprocal Rank Fusion for hybrid retrieval | Merges incomparable score scales without calibration |

Format: Context → Decision → Consequences (positive and negative) → Alternatives
considered. Negative consequences are recorded deliberately; an ADR with no downside
usually means the analysis stopped early.
