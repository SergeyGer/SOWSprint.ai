"""Process-wide RAG pipeline singleton.

Building a store, an embedder, a BM25 encoder and a reranker on every retrieval would
be wasteful and would re-run corpus ingestion repeatedly. :class:`RagPipeline` owns one
instance of each, indexes the corpus on first use, and exposes the operations the agent
graph and the UI actually need.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..config import Settings, get_settings
from ..models import Jurisdiction, RequirementScope, TechnicalBlueprint
from ..observability.logging import get_logger
from .chunking import BM25Encoder
from .embeddings import Embedder, build_embedder
from .ingest import IngestStats, ensure_corpus_indexed, load_or_refit_bm25
from .rerank import Reranker, build_reranker
from .retriever import ComplianceRetriever, RetrievalResult
from .store import VectorStore, build_store

log = get_logger(__name__)


@dataclass
class PipelineStats:
    """Snapshot rendered in the UI's diagnostics panel."""

    backend: str = ""
    embedder: str = ""
    reranker: str = ""
    indexed_chunks: int = 0
    eu_chunks: int = 0
    us_chunks: int = 0
    embedding_dim: int = 0
    indexed: bool = False
    ingest_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "vector_backend": self.backend,
            "embedder": self.embedder,
            "reranker": self.reranker,
            "indexed_chunks": self.indexed_chunks,
            "eu_chunks": self.eu_chunks,
            "us_chunks": self.us_chunks,
            "embedding_dim": self.embedding_dim,
            "indexed": self.indexed,
        }


class RagPipeline:
    """Owns the retrieval stack and lazily guarantees a populated index."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: VectorStore | None = None,
        auto_ingest: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.auto_ingest = auto_ingest
        self._lock = threading.Lock()
        self._ingest_stats: IngestStats | None = None

        self.store: VectorStore = store or build_store(self.settings)
        self.embedder: Embedder = build_embedder(self.settings)
        self.reranker: Reranker = build_reranker(self.settings)
        self.bm25: BM25Encoder = load_or_refit_bm25(self.store, settings=self.settings)
        self.retriever = ComplianceRetriever(
            store=self.store,
            embedder=self.embedder,
            reranker=self.reranker,
            bm25=self.bm25,
            settings=self.settings,
        )

    # ------------------------------------------------------------------ indexing
    def ensure_indexed(self, *, recreate: bool = False) -> IngestStats:
        """Index the corpus once per process (thread-safe)."""
        with self._lock:
            if self._ingest_stats is not None and not recreate:
                return self._ingest_stats

            started = time.perf_counter()
            stats = ensure_corpus_indexed(
                retriever=self.retriever, store=self.store, recreate=recreate, settings=self.settings
            )
            # Adopt whatever the ingestion pass actually used.
            self.store = self.retriever.store
            self.embedder = self.retriever.embedder
            self.bm25 = self.retriever.bm25
            stats.duration_ms = stats.duration_ms or (time.perf_counter() - started) * 1000.0
            self._ingest_stats = stats
            return stats

    @property
    def is_indexed(self) -> bool:
        return self.retriever.is_indexed

    # ------------------------------------------------------------------ retrieval
    def retrieve(
        self,
        query: str,
        *,
        jurisdiction: Jurisdiction | str = Jurisdiction.EU,
        final_k: int | None = None,
    ) -> RetrievalResult:
        if self.auto_ingest:
            self.ensure_indexed()
        return self.retriever.retrieve(query, jurisdiction=jurisdiction, final_k=final_k)

    def retrieve_for_scope(
        self,
        scope: RequirementScope,
        blueprint: TechnicalBlueprint | None = None,
        *,
        jurisdiction: Jurisdiction | None = None,
        multi_topic: bool = True,
    ) -> RetrievalResult:
        """Primary entry point used by the Legal agent."""
        if self.auto_ingest:
            self.ensure_indexed()
        if multi_topic:
            return self.retriever.retrieve_multi_topic(scope, blueprint, jurisdiction=jurisdiction)
        return self.retriever.retrieve_for_scope(scope, blueprint, jurisdiction=jurisdiction)

    # ------------------------------------------------------------------ diagnostics
    def stats(self) -> PipelineStats:
        stats = PipelineStats(
            backend=self.store.name,
            embedder=getattr(self.embedder, "name", "unknown"),
            reranker=getattr(self.reranker, "name", "unknown"),
            embedding_dim=getattr(self.embedder, "dim", 0),
            indexed=self.retriever.is_indexed,
        )
        try:
            payloads = self.store.all_payloads()
            stats.indexed_chunks = len(payloads)
            stats.eu_chunks = sum(1 for p in payloads if p.get("jurisdiction") == "EU")
            stats.us_chunks = sum(1 for p in payloads if p.get("jurisdiction") == "US")
        except Exception as exc:
            log.warning("pipeline.stats_failed", error=str(exc))
        if self._ingest_stats:
            stats.ingest_ms = self._ingest_stats.duration_ms
        return stats

    def health(self) -> dict[str, object]:
        """Readiness probe payload."""
        try:
            chunks = self.store.count()
        except Exception as exc:
            return {"ok": False, "backend": self.store.name, "error": str(exc)}
        return {
            "ok": chunks > 0,
            "backend": self.store.name,
            "chunks": chunks,
            "bm25_fitted": self.bm25.is_fitted,
            "embedder": getattr(self.embedder, "name", "unknown"),
            "reranker": getattr(self.reranker, "name", "unknown"),
        }


_PIPELINE: RagPipeline | None = None
_PIPELINE_LOCK = threading.Lock()


def get_pipeline(settings: Settings | None = None, *, refresh: bool = False) -> RagPipeline:
    """Return the process-wide retrieval pipeline, constructing it on first use."""
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None or refresh:
            _PIPELINE = RagPipeline(settings)
        return _PIPELINE


def reset_pipeline() -> None:
    """Drop the singleton (used by tests that swap the backend)."""
    global _PIPELINE
    with _PIPELINE_LOCK:
        _PIPELINE = None
