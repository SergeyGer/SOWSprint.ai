"""Shared pytest fixtures.

Two design rules keep this suite fast and deterministic:

* **No network, ever.** Every fixture resolves to the offline adapters and the
  in-process vector store, so the suite runs identically on a laptop, in CI and inside
  the container.
* **Small by default.** Tests that do not exercise corpus breadth use a compact
  hand-built corpus (``mini_pipeline``); only the tests that genuinely depend on corpus
  scale use the full bundled dataset, and that pipeline is session-scoped so ingestion
  happens once.
"""

from __future__ import annotations

import pytest

from sowsprint.config import Settings, VectorBackend
from sowsprint.rag.embeddings import HashEmbedder
from sowsprint.rag.ingest import ingest_corpus
from sowsprint.rag.pipeline import RagPipeline
from sowsprint.rag.store import build_store

# --------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Offline, in-memory settings with a small embedding dimension for speed."""
    return Settings(
        vector_backend=VectorBackend.MEMORY,
        embedding_provider="hash",
        rerank_provider="heuristic",
        llm_provider="mock",
        embedding_dim=128,
        dry_run_integrations=True,
        session_budget_usd=2.50,
        chunk_size=600,
        chunk_overlap=80,
        retrieval_top_k=12,
        retrieval_final_k=4,
        log_json=False,
    )


@pytest.fixture(scope="session")
def full_settings() -> Settings:
    """Session-scoped settings used for corpus-scale retrieval tests."""
    return Settings(
        vector_backend=VectorBackend.MEMORY,
        embedding_provider="hash",
        rerank_provider="heuristic",
        llm_provider="mock",
        embedding_dim=256,
        dry_run_integrations=True,
        log_json=False,
    )


# --------------------------------------------------------------------------------------
# Corpora
# --------------------------------------------------------------------------------------

#: Four entries chosen so every retrieval assertion is unambiguous: exactly one EU and
#: one US entry per topic, with deliberately non-overlapping vocabulary.
MINI_CORPUS: list[dict] = [
    {
        "id": "eu-gdpr-breach-notification",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Data Processing Agreement, cl. 4.2 (adapted)",
        "doc_type": "regulation",
        "title": "Personal data breach notification duty",
        "text": (
            "The processor shall notify the controller without undue delay and no later "
            "than 48 hours after becoming aware of a personal data breach affecting data "
            "subjects in the European Union. The notification shall describe the nature of "
            "the breach, the categories and approximate number of data subjects concerned, "
            "the likely consequences, and the measures taken or proposed to address it. "
            "Where notification to the supervisory authority is required, the controller "
            "shall provide the reasoning for any delay beyond seventy-two hours."
        ),
        "tags": ["gdpr", "breach", "notification", "supervisory authority"],
        "risk_level": "high",
    },
    {
        "id": "eu-aiact-high-risk-obligations",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Article 6 (adapted)",
        "doc_type": "regulation",
        "title": "High-risk AI system provider obligations",
        "text": (
            "Providers of high-risk artificial intelligence systems shall establish and "
            "maintain a risk management system throughout the lifecycle, draw up technical "
            "documentation before placing the system on the market, implement human "
            "oversight measures, and keep automatically generated logs for traceability. "
            "The classification of a system as high-risk under the EU AI Act depends on its "
            "intended purpose and on whether it operates as a safety component of a "
            "regulated product."
        ),
        "tags": ["ai act", "high-risk", "oversight", "transparency"],
        "risk_level": "high",
    },
    {
        "id": "us-ccpa-opt-out-rights",
        "jurisdiction": "US",
        "source": "Pile of Law — Cal. Civ. Code § 1798.120 (adapted)",
        "doc_type": "statute",
        "title": "California consumer opt-out and sale restrictions",
        "text": (
            "A business shall not sell or share the personal information of a California "
            "consumer who has opted out, and shall provide a clear and conspicuous notice of "
            "the right to opt out. The business shall honour opt-out preference signals sent "
            "by a platform, browser or extension. A service provider receiving personal "
            "information under the CCPA as amended by the CPRA shall not retain, use or "
            "disclose it for any purpose other than the business purposes specified in the "
            "contract."
        ),
        "tags": ["ccpa", "cpra", "privacy", "opt-out"],
        "risk_level": "high",
    },
    {
        "id": "us-delaware-board-authority",
        "jurisdiction": "US",
        "source": "Pile of Law — 8 Del. C. § 141(a) (adapted)",
        "doc_type": "statute",
        "title": "Delaware board management authority and fiduciary duties",
        "text": (
            "The business and affairs of every corporation organised under the Delaware "
            "General Corporation Law shall be managed by or under the direction of a board "
            "of directors. Directors owe fiduciary duties of care and loyalty to the "
            "corporation and its stockholders, and a transaction with an interested director "
            "requires either disinterested director approval or approval by a majority of "
            "the disinterested shares outstanding to fall within the safe harbour."
        ),
        "tags": ["delaware", "dgcl", "fiduciary", "board"],
        "risk_level": "medium",
    },
]


@pytest.fixture(scope="session")
def mini_corpus() -> list[dict]:
    return [dict(entry) for entry in MINI_CORPUS]


@pytest.fixture
def mini_pipeline(settings: Settings, mini_corpus: list[dict]) -> RagPipeline:
    """A pipeline indexed over the compact corpus, ready for retrieval."""
    store = build_store(settings, backend=VectorBackend.MEMORY)
    stats, encoder, store, embedder = ingest_corpus(
        store=store, settings=settings, entries=mini_corpus
    )
    pipeline = RagPipeline(settings, store=store, auto_ingest=False)
    pipeline.bm25 = encoder
    pipeline.embedder = embedder
    pipeline.retriever.store = store
    pipeline.retriever.bm25 = encoder
    pipeline.retriever.embedder = embedder
    pipeline._ingest_stats = stats
    return pipeline


@pytest.fixture(scope="session")
def full_pipeline(full_settings: Settings) -> RagPipeline:
    """Session-scoped pipeline over the real bundled compliance corpus."""
    pipeline = RagPipeline(full_settings)
    pipeline.ensure_indexed()
    return pipeline


# --------------------------------------------------------------------------------------
# Requirement samples
# --------------------------------------------------------------------------------------

DETAILED_BRIEF = """# Project Aurora Data Platform
Nordwind Logistics GmbH (Berlin) requires a customer analytics platform for our
operations organisation.

- Build a React dashboard used by 200 warehouse staff across 6 distribution centres
- Develop a Python FastAPI backend with PostgreSQL as the system of record
- Integrate with Salesforce for account data and SAP for shipment events
- Deliver GDPR-compliant audit logging that satisfies our Data Protection Officer

Budget: EUR 120k. Delivery within 12 weeks. Success means 40% faster monthly close.
The platform processes personal data of EU employees."""


@pytest.fixture
def detailed_brief() -> str:
    return DETAILED_BRIEF


@pytest.fixture
def vague_brief() -> str:
    return "We need some kind of AI thing for our sales team. Not sure about the details yet."


@pytest.fixture
def sample_evidence() -> str:
    """An evidence block in exactly the format ``RetrievedChunk.to_context_block`` emits."""
    return "\n\n".join(
        [
            "[1] Personal data breach notification duty (jurisdiction=EU) "
            "id=eu-gdpr-breach-notification\n"
            "The processor shall notify the controller without undue delay and no later "
            "than 48 hours after becoming aware of a personal data breach.",
            "[2] High-risk AI system provider obligations (jurisdiction=EU) "
            "id=eu-aiact-high-risk-obligations\n"
            "Providers of high-risk AI systems shall maintain technical documentation and "
            "implement human oversight.",
            "[3] Governing law and exclusive jurisdiction (jurisdiction=EU) "
            "id=eu-governing-law-ireland\n"
            "This agreement is governed by the laws of Ireland and the courts of Dublin "
            "have exclusive jurisdiction.",
        ]
    )


# --------------------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------------------


@pytest.fixture
def hash_embedder(settings: Settings) -> HashEmbedder:
    return HashEmbedder(settings)


@pytest.fixture
def session_id() -> str:
    return "pytest-session"
