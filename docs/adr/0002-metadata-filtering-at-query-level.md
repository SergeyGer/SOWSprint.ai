# ADR-0002 — Jurisdiction filtering pushed into the vector engine

- **Status:** Accepted
- **Date:** 2026-10
- **Deciders:** Platform engineering

## Context

The compliance regime is a hard constraint, not a ranking preference. A Statement of
Work drafted under EU law that cites a Delaware fiduciary-duty case, or a US contract
citing GDPR Art. 28, is not "slightly worse" — it is wrong, and in a contractual
document that is a liability.

The brief states the requirement precisely:

> Must implement **Metadata Filtering** at the database query level
> (`metadata={"jurisdiction": "EU"}`)

Three implementation strategies were available:

1. **Post-filter** — retrieve top-k globally, then discard wrong-jurisdiction results.
2. **Prompt-only** — retrieve globally and instruct the model to ignore the other regime.
3. **Query-level filter** — pass the constraint into the vector engine so it narrows the
   candidate set before scoring.

## Decision

Push the filter into **both retrieval arms** at query time, using Qdrant's payload
filter with an indexed `jurisdiction` keyword field:

```python
Filter(must=[FieldCondition(key="jurisdiction", match=MatchValue(value="EU"))])
```

`InMemoryStore` mirrors the semantics exactly through `schema.matches_filter`, and a
test asserts the equivalence from both directions so the fallback backend cannot
silently diverge.

## Consequences

**Positive**

- **Recall is preserved.** With 70 EU and 71 US chunks, a post-filter over top-20 would
  routinely return mostly the wrong regime and silently starve the drafting agent.
- The constraint is enforced by the storage engine, so it holds regardless of prompt
  injection in a customer brief.
- A payload index makes the filter an index lookup rather than a collection scan.
- Auditable: the payload index and per-regime point counts are directly inspectable
  (`GET /collections/sowsprint_legal` → 141 points; filtered counts 70 / 71).

**Negative**

- The jurisdiction becomes part of the collection's data model. Re-tagging a document
  requires re-ingesting it.
- Two filter implementations (Qdrant and in-memory) must stay in step. Mitigated by the
  parity test rather than by convention.
- Corpus coverage must be balanced per regime, or one jurisdiction becomes
  second-class. A test asserts both regimes return evidence for the same query.

## Alternatives considered

- **Post-filtering.** Rejected: silently destroys recall and is invisible when it does.
- **Two collections, one per regime.** Rejected: doubles operational surface, and
  cross-regime documents (a US company processing EU personal data) are common enough
  that a single collection with richer metadata is more honest.
- **Prompt-only instruction.** Rejected: unenforceable, and it wastes context budget on
  passages the agent must then discard.

## Verification

```bash
curl -sS -X POST http://localhost:6333/collections/sowsprint_legal/points/count \
  -H 'Content-Type: application/json' \
  -d '{"filter":{"must":[{"key":"jurisdiction","match":{"value":"EU"}}]},"exact":true}'
# → 70   (unfiltered: 141)

python scripts/healthcheck.py --deep
# → [OK] readiness: {"store": "qdrant", "chunks": 141,
#                    "eu_probe": "6a0638b72bca", "us_probe": "defea8be8215"}
```

The readiness probe fails deliberately if both regimes return the *same* chunk, because
that would prove the filter had stopped discriminating.
