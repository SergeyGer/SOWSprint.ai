# Architecture decisions

Short, dated records of the choices that shaped the system — what was chosen, what was
rejected, and what it cost. Full text in
[`docs/adr/`](https://github.com/SergeyGer/SOWSprint.ai/tree/main/docs/adr).

| ADR | Decision | The trade-off accepted |
| :-- | :-- | :-- |
| [0001](https://github.com/SergeyGer/SOWSprint.ai/blob/main/docs/adr/0001-offline-first-reasoning.md) | Offline-first reasoning, cloud as an upgrade | The offline auditor is a template matcher and produces false findings on LLM-drafted text; the platform detects and labels that rather than hiding it |
| [0002](https://github.com/SergeyGer/SOWSprint.ai/blob/main/docs/adr/0002-metadata-filtering-at-query-level.md) | Jurisdiction filtering inside the vector engine | The fallback store must mirror the semantics exactly, and a test enforces it |
| [0003](https://github.com/SergeyGer/SOWSprint.ai/blob/main/docs/adr/0003-reciprocal-rank-fusion.md) | Reciprocal Rank Fusion for hybrid retrieval | Ignores score magnitude; two arms that agree strongly are not weighted higher |
| [0004](https://github.com/SergeyGer/SOWSprint.ai/blob/main/docs/adr/0004-dual-regime-contracts.md) | Dual-regime contracts in one document | Roughly double the retrieval work; 208 s and 30 clauses against 90 s and 16 |

---

## Decisions worth explaining beyond the ADRs

### The cost dashboard left the custom-element path

Chainlit renders custom elements from `public/elements/<Name>.jsx`. That mechanism **did
not work here**: Chainlit 2.12 fetches the file verbatim and evaluates it in the browser,
but nothing in the wheel compiles JSX — no esbuild in the Python package, no Node in the
container. The element never mounted.

The dashboard is now inline HTML with `unsafe_allow_html`, which renders reliably and is
verifiable from the DOM.

> **The diagnostic failure is the more interesting lesson.** The first investigation
> searched the DOM for `"Session spend"` — a string the component never contained — and for
> selectors it never used. The conclusion happened to be correct; the evidence was not.
> It was re-tested against the actual strings before anything was changed.

### The revision workflow is separate from the graph

Contract revision could have been another node. It is a method on the session instead,
because it is a genuinely different workflow: drafting produces a document, revision edits
one a party has already read. The guarantee that matters — that locked clauses come back
byte-identical — is **enforced in code**, not requested of the model.

Measured against a deliberately hostile instruction (*"rewrite the entire agreement and make
the liability unlimited"*) on a 14-clause contract with one clause locked: **1 clause
modified, 12 unchanged, 1 locked and preserved byte-identical.**

### `BOTH` is a selection, not a stored value

No chunk is tagged `BOTH`. The selection expands to its member regimes and the query filter
becomes a set match. Storing a fifth value would double the corpus to represent something no
document is.

The dual template is deliberately **not** a literal union of the two single-regime
templates: the regimes label shared concepts differently, and a contract must state each
concept once. A test asserts no near-duplicate headings survive.

### Nothing is deleted silently

Dead configuration is documented rather than removed (`jira_project_key` is inert). Rejected
datasets are listed with reasons rather than dropped from the survey. Failure modes are in
the README rather than only in the commit log.

This is a deliberate posture: a reader deciding whether to trust the system needs the
negative results as much as the positive ones.

---

## The three documentation improvements worth making next

Written down because they are the difference between good and excellent repository
documentation, and none of them is expensive:

**1. A decision log with dates and authorship.**
The ADRs record *what* and *why*, but not *when* or *by whom*, and several were written
after the fact. A dated entry per significant change — with the evidence that prompted it —
turns the documentation into a history a reviewer can audit. The commit log has the raw
material; it needs extracting into the ADR format.

**2. A golden-set evaluation harness, published with its results.**
The largest credibility gap is that audit quality is asserted, not measured. A published
benchmark — sound contracts with one injected defect each, with precision and recall
reported per release — would let a reviewer see whether the judge improved or merely
changed. Until then, every claim about audit quality is anecdote, however well documented.

**3. Onboarding paths split by role, with a verified quickstart.**
The material exists but is organised by subsystem rather than by reader. A product manager,
a backend engineer and a security reviewer need three different first pages. And the
quickstart should be executed in CI on every release, so "it works" is a tested claim rather
than a maintained hope — the `data/corpus` failure is the argument for this.

→ [Roadmap](Roadmap) · [Testing](Testing)
