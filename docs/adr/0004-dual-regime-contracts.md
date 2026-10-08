# ADR-0004 — Dual-regime contracts (EU + US in one document)

- **Status:** Accepted
- **Date:** 2026-10
- **Deciders:** Platform engineering

## Context

The original model treated jurisdiction as a single choice: EU *or* US. Real B2B work is
rarely that clean. The most common transatlantic engagement is a US parent processing EU
personal data — subject to GDPR **and** SEC disclosure **and** CCPA at the same time. A
contract satisfying only one regime is not a partial success; it is unenforceable in the
other.

Three questions had to be answered:

1. **How is "both" represented in the vector store?** No chunk is tagged `BOTH`.
2. **How is evidence balance guaranteed?** A single blended query lets the larger corpus
   dominate the candidate pool.
3. **What goes in the contract when the two regimes conflict?**

## Decision

**`BOTH` is a selection, not a stored value.** `Jurisdiction.BOTH` expands via
`.regimes` into `(EU, US)`, and the query-level filter becomes
`MatchAny(["EU", "US"])`. The fallback store mirrors this in `matches_filter`, with a
parity test.

**Retrieval runs every clause topic against each regime and interleaves the results**
rather than issuing one blended query. Measured on the bundled corpus: a dual run
returns 6 EU + 6 US passages, where a blended query returned a regime-skewed mix.

**Conflicts are resolved by an explicit stricter-standard rule**, not by silently
choosing a side. The dual template adds three clauses that only exist when the regimes
meet:

| Clause | Purpose |
| :-- | :-- |
| Dual-Regime Compliance and Order of Precedence | How the two obligation sets coexist, and who owns compliance for each |
| Cross-Border Data Transfers and Transfer Mechanisms | Adequacy, SCCs, the EU-U.S. Data Privacy Framework, transfer impact assessments |
| Conflicting Obligations and Stricter-Standard Rule | The resolution rule, with a worked example (breach-notification timing) |

The dual template is **not** a literal union of the two single-regime lists. The regimes
label shared concepts differently (`Intellectual Property` vs `Intellectual Property and
Work Product`), and a contract must state each concept once. The template picks one
label per concept — a test asserts no near-duplicate headings survive.

## Consequences

**Positive**

- The single most common real engagement shape is now expressible.
- Evidence balance is structural, not hoped for: the drafting agent cannot receive
  twelve GDPR passages and nothing on Delaware.
- The conflict rule is stated and testable rather than left to the model's judgement.
- `Jurisdiction.coerce` accepts `"EU+US"`, `"dual"` and `"BOTH"`, so the UI and API can
  evolve independently.

**Negative**

- **Roughly double the retrieval work** per engagement (8 topics × 2 regimes), and more
  clauses to draft. Observed: 208 s wall-clock and 30 clauses versus ~90 s and 16 for a
  single regime.
- The stricter-standard rule is conservative. It is the defensible default, but a
  counterparty may negotiate a different allocation, which the model cannot anticipate.
- Governing law becomes an explicit party election rather than a platform decision,
  because no single forum is neutral for both regimes.
- Template drift is a real risk: the dual list is maintained by hand and a test now
  guards its coverage rather than deriving it.

## Alternatives considered

- **A fifth stored jurisdiction value `"BOTH"` on every chunk.** Rejected: it would
  double the corpus and make the filter match a value no document actually has.
- **One blended query filtered to both regimes.** Rejected: measured regime skew, and
  the skew is invisible in the output — the contract simply omits the quieter regime.
- **Two separate contracts, one per regime.** Rejected: operationally worse for the
  customer and it is not what the parties sign.
- **Let the model decide the conflict rule.** Rejected: an unstated resolution rule is
  precisely the gap that becomes a dispute.

## Verification

```bash
.venv/bin/python -m pytest tests/test_jurisdiction.py -q     # 27 tests
.venv/bin/python scripts/verify_ui.py --jurisdiction BOTH
```

Live dual-regime engagement: audit PASSED (0.90), 30 clauses, evidence drawn from both
regimes, $0.0698.
