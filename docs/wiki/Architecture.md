# Architecture

## System overview

```mermaid
flowchart TB
    subgraph client["Client"]
        PHONE["📱 Phone<br/>voice capture"]
        DESK["💻 Desktop<br/>browser"]
    end

    subgraph app["Application container"]
        direction TB
        UI["Chainlit UI<br/>socket.io + audio<br/>authentication"]
        GRAPH["LangGraph state machine<br/>Triage → Clarify → Architect<br/>→ Legal → Critic → Approval → Tools"]
        LLM["Provider adapters<br/>Anthropic · OpenAI · Groq<br/>Ollama · deterministic offline"]
        RAG["Retrieval pipeline<br/>hybrid dense + BM25 → RRF → rerank"]
        TOOLS["Tool connectors<br/>Jira · Notion<br/>dry-run capable"]
        TEL["Telemetry<br/>pricing · budgets<br/>prompt-cache accounting"]
    end

    subgraph data["Data"]
        QD[("Qdrant<br/>named vectors<br/>dense + sparse")]
        ART[("Artifacts<br/>SOW · backlog · audit<br/>PDF · CSV · JSON")]
    end

    subgraph cloud["External"]
        ANTH["Anthropic API"]
        OAI["OpenAI API"]
        JR["Jira Cloud"]
        NO["Notion API"]
    end

    PHONE --> UI
    DESK --> UI
    UI --> GRAPH
    GRAPH --> LLM
    GRAPH --> RAG
    GRAPH --> TOOLS
    GRAPH --> TEL
    RAG --> QD
    GRAPH --> ART
    LLM --> ANTH
    LLM --> OAI
    TOOLS --> JR
    TOOLS --> NO

    style GRAPH fill:#065f46,stroke:#10b981,color:#d1fae5
    style RAG fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    style LLM fill:#78350f,stroke:#f59e0b,color:#fef3c7
```

## The single most important design decision

**Everything runs offline, and the cloud is an upgrade rather than a dependency.**

```mermaid
flowchart LR
    START["Resolve provider"] --> Q{"Any cloud<br/>credential?"}
    Q -->|yes| CLOUD["Cloud provider<br/>higher quality audit"]
    Q -->|no| OFFLINE["Deterministic<br/>rule-based engine"]

    CLOUD -.->|"LLMError<br/>after retries"| OFFLINE
    OFFLINE --> OUT["Same graph<br/>same artefacts<br/>same schema"]
    CLOUD --> OUT

    style OFFLINE fill:#065f46,stroke:#10b981,color:#d1fae5
    style CLOUD fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
```

This is not a demo mode bolted on afterwards. It is the reason:

- **483 tests run with no network and no credentials**, so CI needs no secrets and a
  revoked key cannot break the build
- The graph, the schemas, the artefacts and the UI are identical either way, so the
  offline path exercises the real code
- A deployment can be demonstrated, evaluated and regression-tested before anyone signs a
  contract with a model provider
- The offline engine is also a **guardrail tier**: when a cloud call fails after retries,
  the run degrades rather than dying

The consequence, stated honestly: the offline auditor is a rule-based checker. Handed a
contract drafted by a language model it produces false findings, and the platform now
detects and reports that rather than presenting it as a verdict.
→ [Quality assurance](Quality-Assurance)

## The graph

A cyclic `StateGraph`, not a chain. The cycle is the repair loop: a failed audit returns
the draft to the drafting agent.

```mermaid
stateDiagram-v2
    [*] --> triage
    triage --> clarify: scope INCOMPLETE
    clarify --> triage: answers received
    triage --> architect: scope COMPLETE
    architect --> legal
    legal --> critic
    critic --> legal: audit failed<br/>(repair budget remains)
    critic --> plan_tools: audit passed
    plan_tools --> approval
    approval --> tools: approved
    approval --> [*]: rejected
    tools --> finalize
    finalize --> [*]
```

State is a typed dictionary carried through the graph and checkpointed with LangGraph's
`MemorySaver`, which is what makes human-in-the-loop interrupts resumable: the approval
gate can pause for minutes and resume from the exact node.

Every node returns a partial update plus **step events**, which the UI renders as they
arrive. The graph is synchronous; the UI drives it on a worker thread and consumes events
through a queue, so the event loop is never blocked by a two-minute run.

## Layering

```mermaid
flowchart TB
    subgraph presentation["Presentation"]
        APP["app.py<br/>Chainlit callbacks<br/>auth · commands · dashboards"]
    end
    subgraph orchestration["Orchestration"]
        RUNTIME["runtime.py<br/>ScopingSession<br/>streaming · revision"]
        NODES["nodes.py<br/>one method per agent"]
        GRAPH2["graph.py<br/>wiring · conditional edges"]
    end
    subgraph capability["Capability"]
        LLMP["llm/<br/>base · factory · adapters<br/>offline engine"]
        RAGP["rag/<br/>schema · store · retriever<br/>rerank · pipeline"]
        TOOLP["tools/<br/>registry · planner<br/>jira · notion"]
    end
    subgraph foundation["Foundation"]
        MODELS["models.py<br/>Pydantic contracts"]
        CONFIG["config.py<br/>settings · capability matrix"]
        TELE["telemetry/<br/>pricing · tracker"]
        OBS["observability/<br/>structured logging"]
    end

    presentation --> orchestration --> capability --> foundation

    style MODELS fill:#7c2d12,stroke:#ea580c,color:#fed7aa
```

**`models.py` is the contract layer**, and it is where the design pays off. Every agent
input and output is a Pydantic model, so:

- The LLM is asked for structured output against a generated JSON schema
- The offline engine produces the *same* models, so both paths are interchangeable
- A malformed model response is a retry, not a corrupt artefact
- The export layer renders from models and never re-parses text

## Why these technologies

| Choice | Reason | What it costs |
| :-- | :-- | :-- |
| **LangGraph** | Cyclic graph with typed state and interruptible human-in-the-loop. A chain cannot express "audit failed, go back". | Framework coupling around state shapes |
| **Chainlit** | Purpose-built chat UI with native audio capture and custom elements | Its custom-element mechanism turned out not to compile in this container — see [Decisions](Decisions) |
| **Qdrant** | Named vectors allow dense and sparse in one collection, and filters are applied inside the engine | A second container; an in-memory store mirrors the same filter semantics for tests |
| **Pydantic v2** | JSON-schema generation for structured output, plus validation at every boundary | Verbose models; worth it for the guarantees |
| **Docker Compose** | Two containers, one command, identical locally and in CI | Baked images rather than bind mounts, so source edits need a rebuild |

## Deliberate non-goals

- **No database.** Sessions are in-process and checkpointed in memory. Persistence is a
  roadmap item, not an oversight. → [Roadmap](Roadmap)
- **No multi-tenancy.** One deployment serves one organisation. Adding tenancy without
  understanding the compliance boundary would be worse than not having it.
- **No fine-tuning.** Retrieval and structured output solve the problem; fine-tuning would
  add a training pipeline and a drift surface for no measured gain.

→ [Agent pipeline](Agent-Pipeline) · [Compliance-aware RAG](Compliance-RAG) · [Decisions](Decisions)
