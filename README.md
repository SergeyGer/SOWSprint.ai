<div align="center">

# SOWSprint.ai

**Autonomous multi-agent AI platform that turns chaotic B2B requirements into
enterprise-grade Statements of Work and instantly deployable project workflows.**

`LangGraph` · `Chainlit` · `Qdrant` · `Docker` · `Python 3.11`

*Scoping-to-contract lifecycle: **3 days → 5 minutes.***

</div>

---

## What this is

A client sends a messy brief — a paragraph, a bullet list, a voice note recorded on a
phone. Five minutes later they have a jurisdiction-aware Statement of Work, a
milestone/epic/story backlog with testable acceptance criteria, a quality-audit report,
and a provisioned Jira workspace — with a live meter of what the whole thing cost.

Four LLM personas run as a **cyclic state graph**, not a chain. When the Critic rejects a
contract it goes back to the drafting agent. When the brief is missing something
critical, the graph *stops* and asks exactly three questions instead of inventing an
answer.

```
Triage ──(INCOMPLETE)──► Clarify ⟲  (3 questions, human-in-the-loop)
   │
   └──(COMPLETE)──► Architect ──► Legal/SOW ◄──┐
                                     │         │
                                     ▼         │ (failed audit)
                                  Critic ──────┘
                                     │ (passed)
                                     ▼
                              Approval gate ⟲  (human-in-the-loop)
                                     │
                                     ▼
                          Jira/Notion ──► SOW PDF + backlog + cost report
```

---

## Quick start

### Zero credentials required

The default configuration runs the **entire pipeline on deterministic local engines** —
heuristic reasoning, hashing embeddings, in-process vector store, dry-run integrations.
No API keys, no network calls. Add credentials later to upgrade quality; nothing is
gated behind them.

```bash
git clone <repo> && cd SOWSprint
make install
make demo          # full pipeline, headless, ~15 seconds
```

```bash
make dev           # Chainlit UI at http://localhost:8000
```

### With Docker (app + Qdrant)

```bash
cp .env.example .env
make up            # builds and starts both containers
make ps            # health status
```

### Mobile testing over Wi-Fi

The brief requires testing on a real iPhone. The application binds `0.0.0.0` inside the
container and the published port is bound on all host interfaces:

```bash
make lan-ip        # prints http://<your-lan-ip>:8000
```

Open that URL in Safari on a phone on the same Wi-Fi. The microphone button in the
composer records audio, which is transcribed and fed straight into Triage.

> **If port 8000 is already in use**, set `SOWSPRINT_HOST_PORT` in `.env`. The
> container-internal port stays 8000; only the host mapping changes.

---

## What you get

| Deliverable | Format | Produced by |
| :-- | :-- | :-- |
| Statement of Work | PDF (signature-ready) + Markdown | `export/sow_pdf.py`, `sow_markdown.py` |
| Agile backlog | Markdown + JSON | `export/sow_markdown.py` |
| Jira import file | CSV matching Jira's import mapper | `export/jira_csv.py` |
| Quality audit report | Markdown | `export/sow_markdown.py` |
| Run report | JSON (scope, blueprint, audit, integrations, cost) | `export/bundle.py` |
| Jira / Notion workspace | Live REST or validated dry-run payloads | `tools/` |

---

## Feature deep-dives

### 1. Agentic orchestration with bounded cycles

The graph (`src/sowsprint/agents/graph.py`) is compiled by LangGraph with a checkpointer
and two `interrupt()` points. Three properties make it production-safe rather than a
demo:

**Cycles are bounded structurally.** The quality-repair loop consults
`critique_attempts` against `max_critic_retries`; the clarification loop consults
`triage_rounds` against `MAX_CLARIFICATION_ROUNDS`. Neither can spin.

**Analysis is separated from interruption.** LangGraph re-executes a node from the top
when it resumes after an interrupt — so any LLM call placed *before* an `interrupt()`
would be billed twice. Triage (analyse) and Clarify (ask) are therefore separate nodes,
which is what makes the human-in-the-loop cycle cost-correct rather than merely
functional. There is a regression test for exactly this
(`test_clarification_does_not_rebill_the_triage_node`).

**Failure is declarative.** A budget overrun or provider error sets a terminal status;
every routing function checks it and diverts to `END`, instead of exception handlers
scattered through the graph.

### 2. Compliance-aware Advanced RAG (EU vs US)

The jurisdiction toggle is not a prompt hint. It is a **metadata filter pushed into the
vector engine on both retrieval arms**, so a clause from the wrong regime can never
reach the candidate pool:

```python
# src/sowsprint/rag/schema.py
Filter(must=[FieldCondition(key="jurisdiction", match=MatchValue(value="EU"))])
```

Pipeline: `dense (cosine) ∥ sparse (BM25) → RRF fusion → cross-encoder rerank → top-k`

| Stage | Implementation | Why |
| :-- | :-- | :-- |
| Chunking | Clause-boundary aware, sentence packing fallback | A liability cap severed from its proviso is worse than useless |
| Dense | Qdrant named vectors, cosine | Semantic recall across paraphrased legal language |
| Sparse | **BM25 implemented in-house** with a hashed vocabulary (`zlib.crc32`, not `hash()`) | Stable across processes without shipping a vocabulary file |
| Fusion | Reciprocal Rank Fusion, k=60 | Cosine ∈ [-1,1] and BM25 is unbounded; RRF fuses *ranks*, so no score calibration is needed |
| Rerank | Heuristic cross-encoder surrogate offline; BGE via TEI or Cohere when configured | Fusion optimises recall; reranking optimises the finite context budget |
| Multi-topic | One query per clause topic, excluding already-seen chunks | A single blended query lets GDPR passages crowd out the liability cap |

Corpus: **69 entries → 141 chunks (70 EU / 71 US)**, adapted from the Atticus Project
CUAD categories and the Pile of Law, covering GDPR, the EU AI Act, CCPA/CPRA, HIPAA,
PCI DSS, SOX, SEC disclosure, Delaware DGCL, and the commercial clauses in between.

### 3. Mobile-first UI with native voice

`Chainlit` renders the whole experience; `public/custom.css` tightens it for iOS Safari
(44 px touch targets, 16 px inputs to prevent zoom-on-focus, scrollable tables).
Recordings are uploaded as-is — `.m4a` from iOS Safari, `.webm` from Chrome — because
the Whisper API accepts them directly, which removes ffmpeg from the image.

`Groq whisper-large-v3-turbo` is preferred when a Groq key exists: it is the difference
between voice scoping feeling instant and feeling broken. With no key the pipeline
degrades **honestly** — it reports that transcription is unavailable instead of
pretending to have heard something.

### 4. AI financial observability

Every LLM call routes through `telemetry/tracker.py`, which prices it against a
per-million-token book and updates a per-session ledger. The UI renders a **sticky
custom element** (`public/elements/CostDashboard.jsx`) showing live spend, budget
consumption, token split, call count and a per-agent cost breakdown.

The graph enforces a hard session budget and halts rather than overshooting it. When
running on the offline engine, calls consume no billable tokens; the dashboard applies a
**shadow price** and labels the figure `simulated` so it can never be mistaken for an
invoice.

### 5. External tool automation via function calling

The approved blueprint and the registry's JSON function schemas are handed to the model,
which returns a validated `ToolCallPlan`. Two safety properties: the plan is schema-
validated *before* anything executes, and hallucinated tool names are dropped before
they reach the executor. Payloads are built identically in both modes, so **dry-run** is
a genuine review tool rather than a stub.

---

## Engineering decisions worth flagging

**Offline-first is a design constraint, not a fallback.** The deterministic engine
(`llm/offline.py`) is a real rule-based implementation — lexicon extraction, phase
planning, jurisdiction-parameterised clause assembly, and a rule-based auditor. It makes
the whole system reproducible in CI with no network, and it is the CPU-only instance of
the "local guardrail" tier the brief calls for. Both backends read the *same* tagged
prompt blocks, so switching providers changes answer quality but never control flow.

**Telemetry never breaks a run.** Pricing failures, unknown models and provider errors
inside the accounting layer are swallowed; a missing price yields zero cost, not an
exception.

**The Critic's most important check is citation existence.** Inventing a citation is the
single most dangerous failure mode in the pipeline, so the Legal agent may cite only ids
literally present in the evidence block, and the Critic independently verifies every one
against the corpus.

**`unsafe_allow_html` stays off.** The UI renders model-generated contract text; allowing
raw HTML would turn a prompt-injection attempt in a customer brief into stored XSS. All
chat rendering is therefore pure Markdown.

---

## Project structure

```
SOWSprint/
├── app.py                      Chainlit entry point (presentation only)
├── Dockerfile                  Multi-stage, python:3.11-slim, non-root
├── docker-compose.yml          sowsprint-app + sowsprint-qdrant
├── public/
│   ├── custom.css              Mobile-first presentation layer
│   └── elements/CostDashboard.jsx   Sticky cost widget
└── src/sowsprint/
    ├── config.py               Pydantic settings; every adapter is optional
    ├── models.py               Domain contract (graph state + LLM schemas + UI model)
    ├── agents/
    │   ├── graph.py            LangGraph topology and routing table
    │   ├── nodes.py            Node implementations (halt-aware, telemetry-emitting)
    │   ├── prompts.py          Persona prompts + tagged context blocks
    │   ├── runtime.py          ScopingSession façade (sync + streaming)
    │   └── state.py            GraphState TypedDict with reducers
    ├── rag/
    │   ├── chunking.py         Legal-aware chunker + in-house BM25
    │   ├── schema.py           Payload contract + metadata filter
    │   ├── store.py            Qdrant + in-memory backends (identical semantics)
    │   ├── fusion.py           Reciprocal Rank Fusion
    │   ├── rerank.py           Heuristic / TEI / Cohere cross-encoders
    │   ├── retriever.py        Orchestrator (multi-topic, filtered, reranked)
    │   ├── pipeline.py         Process-wide singleton
    │   └── corpus_data.py      69-entry compliance corpus
    ├── llm/
    │   ├── base.py             Client contract + telemetry + retry + validation
    │   ├── offline.py          Deterministic engine (local guardrail)
    │   ├── nlp.py              Rule-based requirement analysis
    │   ├── openai_client.py    OpenAI + Groq + Ollama (one contract)
    │   ├── anthropic_client.py Claude
    │   └── factory.py          Tier routing + graceful degradation
    ├── telemetry/              Price book, ledger, dashboard rendering
    ├── tools/                  Registry, Jira, Notion, planner, executor
    ├── voice/                  Whisper pipeline with honest offline degradation
    ├── export/                 SOW PDF/Markdown, Jira CSV, bundle assembly
    └── observability/          Structured JSON logging with run correlation
```

---

## Verification

Everything below was executed against this codebase, not asserted.

| Suite | Result |
| :-- | :-- |
| `tests/test_nlp.py` | 51 passed |
| `tests/test_offline_engine.py` | 42 passed |
| `tests/test_rag_chunking.py` | 31 passed |
| `tests/test_rag_retrieval.py` | 48 passed |
| `tests/test_agents_graph.py` | 39 passed |
| **Live UI harness** (`scripts/verify_ui.py`) | **12/12 checks passed** |

The UI harness drives the *running Chainlit server over its real socket.io protocol* —
the same wire format the browser uses — and asserts the full conversation: welcome →
requirement → clarification → approval → provisioning → deliverables.

Measured end-to-end on the EU sample brief:

```
69 corpus entries → 141 chunks (70 EU / 71 US) indexed in 187 ms
Triage    COMPLETE, confidence 98%, 3 compliance triggers
Architect 4 milestones · 19 user stories · 62 story points
Legal     14 clauses · 977 words · 8 evidence links
Retrieval 140 fused → 12 passages, jurisdiction=EU filter enforced
Critic    PASSED · score 1.00 · grounding 73% · 0 findings
Tools     27 function calls, 0 failures (Jira + Notion, dry-run)
Export    7 artefacts incl. a 12 KB PDF
Cost      $0.3057 simulated · 68,987 tokens · 5 calls · 11% of budget
```

```bash
make test          # unit + integration + e2e
make verify-ui     # drive the live UI over socket.io
make health        # container liveness + readiness (incl. jurisdiction filter probe)
```

---

## Configuration

Everything is a `SOWSPRINT_*` environment variable; see [`.env.example`](.env.example)
for the annotated list. The ones that matter most:

| Variable | Default | Effect |
| :-- | :-- | :-- |
| `SOWSPRINT_LLM_PROVIDER` | `auto` | `auto` picks the best credentialed provider, else offline |
| `SOWSPRINT_VECTOR_BACKEND` | `qdrant` | `auto` degrades to in-process memory if Qdrant is down |
| `SOWSPRINT_CRITIC_MODEL` | `gpt-4o` | Point at a **different vendor** for genuine judge independence |
| `SOWSPRINT_SESSION_BUDGET_USD` | `2.50` | Hard ceiling; the graph halts rather than exceed it |
| `SOWSPRINT_MAX_CRITIC_RETRIES` | `1` | Bounds the quality-repair cycle |
| `SOWSPRINT_DRY_RUN_INTEGRATIONS` | `true` | Validate Jira/Notion payloads without sending them |
| `SOWSPRINT_HOST_PORT` | `8000` | Host-side port for Docker; container port stays 8000 |

---

## Limitations and roadmap

Honest about what this is not:

* **The offline engine is rule-based, not semantic.** It produces genuinely useful,
  internally consistent contracts, but a cloud model drafts materially better prose.
  The offline path exists for reproducibility and as a guardrail tier.
* **The bundled corpus is an adaptation** of CUAD/Pile-of-Law subject matter, written for
  demonstration. It is not legal advice and does not replace counsel review.
* **No authentication.** Chainlit ships without an auth callback here; put it behind a
  reverse proxy or add `@cl.password_auth_callback` before exposing it beyond a LAN.
* **`MemorySaver` checkpoints are per-process.** Swap in `SqliteSaver`/`PostgresSaver`
  for multi-replica deployments — the graph topology does not change.
* **The heuristic reranker is a surrogate**, not a trained cross-encoder. Point
  `SOWSPRINT_TEI_RERANK_URL` at a BGE reranker for the real thing.

Roadmap: an evaluation harness scoring generated SOWs against a golden set; SSE streaming
of token-level output; a Postgres checkpointer; multi-tenant corpus isolation.

---

## License

MIT. See [LICENSE](LICENSE).

<div align="center">
<sub>Built to demonstrate production-grade agentic orchestration, compliance-aware
retrieval, containerised infrastructure and AI financial observability.</sub>
</div>
