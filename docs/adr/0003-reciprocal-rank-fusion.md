# ADR-0003 — Reciprocal Rank Fusion for hybrid retrieval

- **Status:** Accepted
- **Date:** 2026-10
- **Deciders:** Platform engineering

## Context

The brief requires hybrid search: dense embeddings **plus** BM25, followed by a
cross-encoder reranking layer. The two arms produce scores on incomparable scales:

| Arm | Score | Range |
| :-- | :-- | :-- |
| Dense (cosine) | similarity | `[-1, 1]` |
| Sparse (BM25) | term-weight sum | `[0, ∞)`, observed 0–12 in this corpus |

Something must merge them, and the obvious approach — normalise both to `[0,1]` and
take a weighted sum — is where hybrid search usually goes wrong.

## Decision

Use **Reciprocal Rank Fusion** (Cormack et al., 2009):

```
score(d) = Σ_arms  weight_arm / (k + rank_arm(d))      k = 60
```

RRF consumes only *ranks*, so no score calibration is required, and `k` requires no
per-corpus tuning.

## Consequences

**Positive**

- **Scale-free by construction.** A test asserts that a dense arm scoring `0.9` and a
  sparse arm scoring `9000` contribute equally at equal rank — the property a
  normalise-and-sum approach cannot guarantee.
- **Robust to one arm failing.** If BM25 returns noise for a paraphrased query, the
  dense arm still determines the ordering, and vice versa.
- **Documents found by both arms are promoted**, which is the signal worth trusting.
- Deterministic tie-breaking by chunk id makes evaluation runs reproducible.

**Negative**

- RRF discards magnitude information: a document that is *overwhelmingly* the best match
  for one arm ranks no higher than a marginal match at the same rank. For legal
  retrieval this is acceptable because the reranker restores discrimination.
- `k = 60` is a constant inherited from the literature rather than tuned on this corpus.
  It is exposed as `SOWSPRINT_RRF_K` should tuning become worthwhile.

## Alternatives considered

- **Min-max normalisation + weighted sum.** Rejected: normalisation bounds are set by
  the retrieved set, so a single outlier compresses every other score. It also has to be
  recomputed per query, making scores non-comparable across queries.
- **Convex combination with tuned α.** Rejected: α is corpus- and query-distribution-
  specific, and we have no labelled relevance set to tune it against.
- **Score-based fusion with z-score normalisation.** Rejected: assumes roughly Gaussian
  score distributions, which BM25 over a 141-chunk legal corpus does not exhibit.

## Relationship to the reranker

Fusion and reranking solve different problems, and both are needed:

- **Fusion optimises recall** — it decides what enters the candidate set.
- **Reranking optimises context density** — it decides what survives into the drafting
  agent's finite context window.

Dropping fusion and reranking everything in one arm's top-k would lose the
complementary matches; dropping reranking would spend the context budget on passages
that mere term overlap promoted.

## Verification

```bash
.venv/bin/python -m pytest tests/test_rag_retrieval.py::TestFusion -v
# test_rrf_is_scale_free
# test_documents_in_both_arms_outrank_single_arm_documents
# test_ranks_are_recorded_per_arm
# test_ordering_is_deterministic_on_ties
# test_handles_one_empty_arm
# test_weights_shift_influence
```
