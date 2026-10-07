"""Compliance-aware Advanced RAG.

Pipeline shape::

    query ──► dense arm  (cosine,  metadata filter)  ─┐
          └─► sparse arm (BM25,    metadata filter)  ─┴─► RRF fusion ──► cross-encoder ──► top-k

The ``jurisdiction`` metadata filter is pushed down to the vector engine on **both**
arms, so a clause from the wrong compliance regime can never reach the candidate pool.
"""

from __future__ import annotations

from .chunking import BM25Encoder, SparseVector, TextChunk, chunk_document, tokenize
from .embeddings import Embedder, HashEmbedder, OllamaEmbedder, OpenAIEmbedder, build_embedder
from .fusion import FusedHit, explain_fusion, reciprocal_rank_fusion
from .ingest import (
    IngestStats,
    build_payloads,
    ensure_corpus_indexed,
    ingest_corpus,
    load_corpus,
)
from .pipeline import PipelineStats, RagPipeline, get_pipeline, reset_pipeline
from .rerank import (
    CohereReranker,
    HeuristicReranker,
    RerankedHit,
    Reranker,
    TEIReranker,
    build_reranker,
)
from .retriever import (
    CLAUSE_FOCUS_TOPICS,
    ComplianceRetriever,
    RetrievalDiagnostics,
    RetrievalResult,
)
from .schema import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    ChunkPayload,
    DocType,
    build_jurisdiction_filter,
    matches_filter,
    point_id_for,
)
from .store import InMemoryStore, QdrantStore, ScoredPoint, StorePoint, VectorStore, build_store

__all__ = [
    "CLAUSE_FOCUS_TOPICS",
    "DENSE_VECTOR_NAME",
    "SPARSE_VECTOR_NAME",
    "BM25Encoder",
    "ChunkPayload",
    "CohereReranker",
    "ComplianceRetriever",
    "DocType",
    "Embedder",
    "FusedHit",
    "HashEmbedder",
    "HeuristicReranker",
    "InMemoryStore",
    "IngestStats",
    "OllamaEmbedder",
    "OpenAIEmbedder",
    "PipelineStats",
    "QdrantStore",
    "RagPipeline",
    "RerankedHit",
    "Reranker",
    "RetrievalDiagnostics",
    "RetrievalResult",
    "ScoredPoint",
    "SparseVector",
    "StorePoint",
    "TEIReranker",
    "TextChunk",
    "VectorStore",
    "build_embedder",
    "build_jurisdiction_filter",
    "build_payloads",
    "build_reranker",
    "build_store",
    "chunk_document",
    "ensure_corpus_indexed",
    "explain_fusion",
    "get_pipeline",
    "ingest_corpus",
    "load_corpus",
    "matches_filter",
    "point_id_for",
    "reciprocal_rank_fusion",
    "reset_pipeline",
    "tokenize",
]
