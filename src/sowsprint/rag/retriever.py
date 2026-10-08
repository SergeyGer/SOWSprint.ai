"""Compliance-aware retrieval orchestrator.

This is the module that satisfies the engineering constraint in the brief: retrieval
behaviour changes with the jurisdiction toggle, and the constraint is enforced **at
the database query level** rather than by filtering results afterwards.

Per query the pipeline runs four stages:

1. **Query construction** — the validated scope, the technical blueprint and the
   jurisdiction are compiled into one retrieval query, with jurisdiction-specific
   terms appended so the sparse arm has something to match on.
2. **Hybrid recall** — dense (cosine) and sparse (BM25) arms each retrieve ``top_k``
   candidates, *both* carrying the metadata filter, so a wrong-jurisdiction clause can
   never enter the candidate pool.
3. **Fusion** — Reciprocal Rank Fusion merges the two ranked lists.
4. **Reranking** — a cross-encoder (or its offline surrogate) selects the ``final_k``
   passages that fit the context budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..models import Jurisdiction, RequirementScope, RetrievedChunk, TechnicalBlueprint
from ..observability.logging import get_logger
from .chunking import BM25Encoder
from .embeddings import Embedder, build_embedder
from .fusion import FusedHit, reciprocal_rank_fusion
from .rerank import RerankedHit, Reranker, build_reranker
from .store import VectorStore, build_store

log = get_logger(__name__)

#: Terms appended to every query for a jurisdiction, giving the sparse arm
#: jurisdiction-specific vocabulary to latch onto.
JURISDICTION_QUERY_EXPANSION: dict[Jurisdiction, list[str]] = {
    Jurisdiction.EU: [
        "GDPR", "personal data", "controller", "processor", "data subject",
        "EU AI Act", "high-risk AI", "transparency", "human oversight",
        "data protection officer", "supervisory authority", "standard contractual clauses",
        "governing law", "limitation of liability", "termination",
    ],
    Jurisdiction.US: [
        "CCPA", "CPRA", "personal information", "SEC disclosure", "materiality",
        "Delaware", "DGCL", "fiduciary duty", "indemnification", "work product",
        "governing law", "venue", "limitation of liability", "termination",
    ],
    # Dual-regime: the sparse arm needs vocabulary from both sides, plus the transfer
    # and conflict concepts that only exist when the two regimes meet.
    Jurisdiction.BOTH: [
        "GDPR", "personal data", "controller", "processor", "data subject",
        "EU AI Act", "high-risk AI", "human oversight",
        "CCPA", "CPRA", "personal information", "SEC disclosure", "Delaware",
        "standard contractual clauses", "international transfer", "adequacy",
        "data privacy framework", "cross-border", "conflict of laws",
        "governing law", "limitation of liability", "indemnification",
    ],
}

#: Focused clause topics the Legal agent retrieves for, one query per topic.
CLAUSE_FOCUS_TOPICS: tuple[str, ...] = (
    "data protection and privacy obligations",
    "limitation of liability and caps",
    "intellectual property ownership and assignment",
    "termination rights and cure periods",
    "confidentiality and trade secrets",
    "payment terms invoicing and late interest",
    "warranties service levels and remedies",
    "governing law jurisdiction and dispute resolution",
)


@dataclass
class RetrievalDiagnostics:
    """Everything the UI needs to explain *why* these passages were selected."""

    query: str
    jurisdiction: str
    dense_candidates: int = 0
    sparse_candidates: int = 0
    fused_candidates: int = 0
    returned: int = 0
    dense_ms: float = 0.0
    sparse_ms: float = 0.0
    fusion_ms: float = 0.0
    rerank_ms: float = 0.0
    embed_ms: float = 0.0
    store_backend: str = ""
    embedder: str = ""
    reranker: str = ""
    filter_applied: bool = True
    excluded_by_filter: int = 0
    topic: str = ""
    regimes: list[str] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return (
            self.embed_ms
            + self.dense_ms
            + self.sparse_ms
            + self.fusion_ms
            + self.rerank_ms
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "jurisdiction": self.jurisdiction,
            "dense_candidates": self.dense_candidates,
            "sparse_candidates": self.sparse_candidates,
            "fused_candidates": self.fused_candidates,
            "returned": self.returned,
            "total_ms": round(self.total_ms, 1),
            "store": self.store_backend,
            "embedder": self.embedder,
            "reranker": self.reranker,
            "filter_applied": self.filter_applied,
            "topic": self.topic,
            "regimes": self.regimes,
        }


@dataclass
class RetrievalResult:
    """Retrieved passages plus provenance."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    diagnostics: RetrievalDiagnostics | None = None
    fused: list[FusedHit] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.chunks)

    def __iter__(self):
        return iter(self.chunks)

    def citation_ids(self) -> list[str]:
        return [chunk.chunk_id for chunk in self.chunks]

    def render_evidence(self, limit: int | None = None) -> str:
        """Render the numbered evidence block injected into agent prompts."""
        selected = self.chunks[:limit] if limit else self.chunks
        if not selected:
            return "(no compliance evidence retrieved)"
        return "\n\n".join(chunk.to_context_block(index) for index, chunk in enumerate(selected, 1))


class ComplianceRetriever:
    """Hybrid, metadata-filtered, reranked retrieval over the legal corpus."""

    def __init__(
        self,
        *,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        bm25: BM25Encoder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or build_store(self.settings)
        self.embedder = embedder or build_embedder(self.settings)
        self.reranker = reranker or build_reranker(self.settings)
        self.bm25 = bm25 or BM25Encoder()

    # ------------------------------------------------------------------ index state
    @property
    def is_indexed(self) -> bool:
        try:
            return self.store.count() > 0 and self.bm25.is_fitted
        except Exception:
            return False

    def ensure_indexed(self, *, auto_ingest: bool = True) -> bool:
        """Make sure the corpus is available; ingests on first use when allowed."""
        if self.is_indexed:
            return True
        if not auto_ingest:
            return False
        from .ingest import ensure_corpus_indexed

        ensure_corpus_indexed(retriever=self)
        return self.is_indexed

    # ------------------------------------------------------------------ query build
    def build_query(
        self,
        scope: RequirementScope | None = None,
        blueprint: TechnicalBlueprint | None = None,
        jurisdiction: Jurisdiction | None = None,
        *,
        topic: str = "",
        geography: str = "",
    ) -> str:
        """Compile the scoping context into one retrieval query.

        When ``topic`` is supplied the query becomes *topic-dominant*: only a short
        scope fingerprint and a few jurisdiction terms are appended. Dumping the whole
        scope into every topic query drowns the topic's lexical signature and makes all
        eight clause queries converge on the same passages — which is exactly the
        failure mode ``retrieve_multi_topic`` exists to avoid.
        """
        resolved_jurisdiction = (
            jurisdiction or (scope.jurisdiction if scope else None) or Jurisdiction.EU
        )
        parts: list[str] = []

        if topic:
            # Repeat the topic so it dominates both the hashed dense features and the
            # BM25 term counts, then add a compact discriminator from the scope.
            parts.extend([topic, topic])
            if scope:
                parts.append(scope.title)
                parts.extend(scope.deliverables[:2])
                parts.extend(scope.compliance_flags[:2])
            if blueprint and blueprint.tech_stack:
                parts.extend(tech.technology for tech in blueprint.tech_stack[:3])
            parts.extend(JURISDICTION_QUERY_EXPANSION[resolved_jurisdiction][:4])
        else:
            if scope:
                parts.append(scope.title)
                parts.append(scope.business_goal)
                parts.extend(scope.deliverables[:4])
                parts.extend(scope.constraints[:3])
                parts.extend(scope.compliance_flags)
                parts.extend(scope.risks[:2])
            if blueprint:
                parts.extend(tech.technology for tech in blueprint.tech_stack[:6])
                if blueprint.milestones:
                    parts.append(" ".join(milestone.name for milestone in blueprint.milestones))
            parts.extend(JURISDICTION_QUERY_EXPANSION[resolved_jurisdiction][:10])

        query = " ".join(part for part in parts if part).strip()
        return query[:2000]

    # ------------------------------------------------------------------ retrieval
    def retrieve(
        self,
        query: str,
        *,
        jurisdiction: Jurisdiction | str = Jurisdiction.EU,
        top_k: int | None = None,
        final_k: int | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
        topic: str = "",
    ) -> RetrievalResult:
        """Run the full hybrid pipeline under a mandatory jurisdiction filter."""
        resolved = Jurisdiction.coerce(jurisdiction)
        # `BOTH` is a selection, not a stored value: expand it so the query-level
        # filter matches either member regime.
        filter_values: str | list[str] = (
            [r.value for r in resolved.regimes]
            if resolved is Jurisdiction.BOTH
            else resolved.value
        )
        jurisdiction_value = resolved.value
        resolved_top_k = top_k or self.settings.retrieval_top_k
        resolved_final_k = final_k or self.settings.retrieval_final_k

        diagnostics = RetrievalDiagnostics(
            query=query,
            jurisdiction=jurisdiction_value,
            store_backend=self.store.name,
            embedder=getattr(self.embedder, "name", "unknown"),
            reranker=getattr(self.reranker, "name", "unknown"),
            topic=topic,
        )

        # --- stage 2a: dense arm -------------------------------------------------
        started = time.perf_counter()
        query_vector = self.embedder.embed_query(query)
        diagnostics.embed_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        dense_hits = self.store.search_dense(
            query_vector,
            resolved_top_k,
            jurisdiction=filter_values,
            doc_types=doc_types,
            exclude_chunk_ids=exclude_chunk_ids,
        )
        diagnostics.dense_ms = (time.perf_counter() - started) * 1000.0
        diagnostics.dense_candidates = len(dense_hits)

        # --- stage 2b: sparse (BM25) arm ----------------------------------------
        started = time.perf_counter()
        sparse_query = self.bm25.encode_query(query)
        sparse_hits = self.store.search_sparse(
            sparse_query,
            resolved_top_k,
            jurisdiction=filter_values,
            doc_types=doc_types,
            exclude_chunk_ids=exclude_chunk_ids,
        )
        diagnostics.sparse_ms = (time.perf_counter() - started) * 1000.0
        diagnostics.sparse_candidates = len(sparse_hits)

        # --- stage 3: fusion -----------------------------------------------------
        started = time.perf_counter()
        fused = reciprocal_rank_fusion(
            dense_hits, sparse_hits, k=self.settings.rrf_k
        )
        diagnostics.fusion_ms = (time.perf_counter() - started) * 1000.0
        diagnostics.fused_candidates = len(fused)

        # --- stage 4: reranking --------------------------------------------------
        started = time.perf_counter()
        reranked = self.reranker.rerank(query, fused, resolved_final_k)
        diagnostics.rerank_ms = (time.perf_counter() - started) * 1000.0
        diagnostics.returned = len(reranked)

        chunks = [self._to_chunk(hit) for hit in reranked]
        log.info(
            "retrieval.completed",
            jurisdiction=jurisdiction_value,
            dense=diagnostics.dense_candidates,
            sparse=diagnostics.sparse_candidates,
            fused=diagnostics.fused_candidates,
            returned=diagnostics.returned,
            total_ms=round(diagnostics.total_ms, 1),
        )
        return RetrievalResult(chunks=chunks, diagnostics=diagnostics, fused=fused)

    @staticmethod
    def _to_chunk(hit: RerankedHit) -> RetrievedChunk:
        payload = hit.payload
        return RetrievedChunk(
            chunk_id=hit.chunk_id,
            text=str(payload.get("text", "")),
            source=str(payload.get("source", "")),
            jurisdiction=str(payload.get("jurisdiction", "")),
            doc_type=str(payload.get("doc_type", "contract_clause")),
            citation=str(payload.get("citation", "")),
            dense_score=hit.dense_score,
            sparse_score=hit.sparse_score,
            fused_score=hit.fused_score,
            rerank_score=hit.rerank_score,
            rank=hit.rank,
        )

    # ------------------------------------------------------------------ convenience
    def retrieve_for_scope(
        self,
        scope: RequirementScope,
        blueprint: TechnicalBlueprint | None = None,
        *,
        jurisdiction: Jurisdiction | None = None,
        final_k: int | None = None,
    ) -> RetrievalResult:
        """Single-query retrieval used when one evidence set must cover everything."""
        resolved = jurisdiction or scope.jurisdiction
        query = self.build_query(scope, blueprint, resolved)
        return self.retrieve(query, jurisdiction=resolved, final_k=final_k)

    def retrieve_multi_topic(
        self,
        scope: RequirementScope,
        blueprint: TechnicalBlueprint | None = None,
        *,
        jurisdiction: Jurisdiction | None = None,
        topics: tuple[str, ...] = CLAUSE_FOCUS_TOPICS,
        per_topic_k: int = 2,
        max_chunks: int = 12,
    ) -> RetrievalResult:
        """Fan out over clause topics, then merge.

        A single blended query under-retrieves narrow clauses: a liability-cap passage
        is easily crowded out by the far more numerous data-protection passages. One
        query per clause topic guarantees each contract section has evidence available.
        """
        resolved = jurisdiction or scope.jurisdiction
        collected: list[RetrievedChunk] = []
        seen: set[str] = set()
        aggregate = RetrievalDiagnostics(
            query="",
            jurisdiction=resolved.value,
            store_backend=self.store.name,
            embedder=getattr(self.embedder, "name", "unknown"),
            reranker=getattr(self.reranker, "name", "unknown"),
            topic="multi-topic",
        )

        # A dual-regime engagement runs every topic against both regimes and
        # interleaves the results. A single blended query would let whichever corpus is
        # larger dominate the candidate pool, and the drafting agent would receive, say,
        # twelve GDPR passages and nothing on Delaware — silently producing a contract
        # that satisfies one regime.
        regimes = resolved.regimes

        for topic in topics:
            if len(collected) >= max_chunks:
                break
            per_regime: list[list[RetrievedChunk]] = []
            for regime in regimes:
                if len(collected) + sum(len(g) for g in per_regime) >= max_chunks:
                    break
                query = self.build_query(scope, blueprint, regime, topic=topic)
                result = self.retrieve(
                    query,
                    jurisdiction=regime,
                    final_k=per_topic_k,
                    # Excluding what we already hold forces each topic to contribute
                    # *new* passages instead of re-surfacing the same dominant chunk.
                    exclude_chunk_ids=sorted(seen) or None,
                    topic=f"{topic} [{regime.value}]" if len(regimes) > 1 else topic,
                )
                if result.diagnostics:
                    aggregate.dense_candidates += result.diagnostics.dense_candidates
                    aggregate.sparse_candidates += result.diagnostics.sparse_candidates
                    aggregate.fused_candidates += result.diagnostics.fused_candidates
                    aggregate.dense_ms += result.diagnostics.dense_ms
                    aggregate.sparse_ms += result.diagnostics.sparse_ms
                    aggregate.fusion_ms += result.diagnostics.fusion_ms
                    aggregate.rerank_ms += result.diagnostics.rerank_ms
                    aggregate.embed_ms += result.diagnostics.embed_ms
                per_regime.append(result.chunks)

            # Round-robin across regimes so the evidence block alternates.
            for position in range(max((len(g) for g in per_regime), default=0)):
                for group in per_regime:
                    if position < len(group):
                        chunk = group[position]
                        if chunk.chunk_id in seen:
                            continue
                        seen.add(chunk.chunk_id)
                        chunk.citation = chunk.citation or topic
                        collected.append(chunk)

        aggregate.regimes = [r.value for r in regimes]
        # Re-rank the merged set once more so the final ordering reflects a single
        # comparable score rather than per-topic score scales.
        for rank, chunk in enumerate(collected, start=1):
            chunk.rank = rank
        collected.sort(key=lambda c: (-c.rerank_score, c.chunk_id))
        for rank, chunk in enumerate(collected, start=1):
            chunk.rank = rank
        aggregate.returned = len(collected)
        return RetrievalResult(chunks=collected[:max_chunks], diagnostics=aggregate)
