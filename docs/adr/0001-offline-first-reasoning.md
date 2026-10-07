# ADR-0001 — Offline-first reasoning instead of cloud-only

- **Status:** Accepted
- **Date:** 2026-10
- **Deciders:** Platform engineering

## Context

The platform's value proposition is agentic orchestration: a Triage agent that decides a
scope is incomplete, an Architect that decomposes it, a Legal agent that drafts against
retrieved compliance evidence, and a Critic that audits the result. Every one of those
steps depends on an LLM.

That creates three problems if cloud providers are the only backend:

1. **The system cannot be tested deterministically.** A contract that differs on every
   run cannot be asserted against in CI, and a stochastic-quality regression is
   invisible.
2. **Nothing runs without credentials.** A reviewer cloning the repository to evaluate
   the architecture sees an error, not a product.
3. **The failure mode is total.** A provider outage or an expired key stops the
   pipeline rather than degrading it.

The brief also explicitly calls for `Ollama / vLLM (Local Guardrails)`, implying a
local tier was expected.

## Decision

Implement a full **deterministic offline reasoning engine** (`llm/offline.py`) as a
first-class backend, not a stub, and make it the default when no credentials are
present.

Both the offline engine and every cloud adapter consume the *same* prompt contract:
persona system prompt plus tagged context blocks (`<raw_requirement>`, `<scope>`,
`<evidence>`, `<draft_sow>`). The offline engine parses those blocks with the same
`extract_tagged` helper the cloud path uses to read its own responses. Switching
providers changes answer quality; it never changes control flow.

## Consequences

**Positive**

- The entire graph — including both human-in-the-loop cycles — runs in CI with no
  network and no credentials. 317 tests execute in ~10 seconds.
- Output is byte-identical across runs, so retrieval and routing changes are attributable.
- Reviewer onboarding is `make install && make demo`.
- A provider outage degrades quality rather than availability:
  `FallbackLLMClient` drops a failed call to the offline engine and the run completes.
- It satisfies the brief's local-guardrail requirement without shipping model weights.

**Negative**

- The offline engine is a substantial second implementation to maintain. Its rule set
  (lexicons, compliance triggers, jurisdiction scoring, clause assembly) needs updating
  when the domain changes.
- Offline output is not comparable in prose quality to a frontier model. The README
  states this plainly rather than overclaiming.
- Two code paths can drift. Mitigated by the shared prompt contract and by tests that
  exercise the graph through both backends.

## Alternatives considered

- **Mock LLM returning canned JSON.** Rejected: it would make the tests pass while
  proving nothing about the pipeline's ability to produce a coherent contract.
- **Record/replay HTTP fixtures (VCR).** Rejected: fixtures rot, they cannot respond to
  new inputs, and they do not help a reviewer exploring the UI.
- **Require an API key and document it.** Rejected: it makes the architecture
  unevaluable by anyone who has not signed up for a provider.
