"""Vector storage backends.

Two implementations satisfy one protocol:

* :class:`QdrantStore` — the production engine. Uses **named vectors** so a single
  collection serves both retrieval arms, plus a payload index on ``jurisdiction`` so
  the compliance filter is an indexed lookup rather than a collection scan.
* :class:`InMemoryStore` — a dependency-free fallback that applies *identical*
  filtering semantics (see :func:`~sowsprint.rag.schema.matches_filter`). It exists so
  the agent graph, the API contract and the whole test suite run without the Qdrant
  container, which keeps CI fast and the developer loop instant.

Both are selected transparently by :func:`build_store`.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, VectorBackend, get_settings
from ..observability.logging import get_logger
from .chunking import SparseVector
from .schema import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    build_jurisdiction_filter,
    matches_filter,
    point_id_for,
)

log = get_logger(__name__)


@dataclass
class StorePoint:
    """A chunk ready for indexing."""

    chunk_id: str
    dense: list[float]
    sparse: SparseVector
    payload: dict[str, Any]


@dataclass
class ScoredPoint:
    """A search hit."""

    chunk_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """Storage-engine contract shared by Qdrant and the in-memory fallback."""

    name: str = "base"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    def ensure_collection(self, dim: int, *, recreate: bool = False) -> None:
        """Create the collection and payload indexes if absent."""

    @abstractmethod
    def upsert(self, points: Sequence[StorePoint]) -> int:
        """Insert or overwrite points; returns the number written."""

    @abstractmethod
    def search_dense(
        self,
        vector: list[float],
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        """Nearest neighbours by cosine similarity, filtered at query level."""

    @abstractmethod
    def search_sparse(
        self,
        sparse: SparseVector,
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        """BM25-scored matches, filtered at query level."""

    @abstractmethod
    def count(self) -> int:
        """Number of indexed chunks. Returns 0 when the collection does not exist."""

    @abstractmethod
    def all_payloads(self) -> list[dict[str, Any]]:
        """Every stored payload (used to fit the BM25 encoder and for diagnostics)."""

    def ping(self) -> bool:
        """Connectivity probe: is the storage engine reachable?

        Deliberately distinct from :meth:`healthy`. A cold deployment legitimately has
        no collection yet, and that must select the durable backend so ingestion can
        create it — not silently downgrade to the in-process fallback.
        """
        try:
            self.count()
            return True
        except Exception:
            return False

    def healthy(self) -> bool:
        """Operational probe: reachable and queryable."""
        return self.ping()

    def close(self) -> None:  # pragma: no cover - symmetry hook
        return None


class InMemoryStore(VectorStore):
    """Pure-Python vector store used when Qdrant is unreachable."""

    name = "memory"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._points: dict[str, StorePoint] = {}
        self._dim: int = 0

    def ensure_collection(self, dim: int, *, recreate: bool = False) -> None:
        self._dim = dim
        if recreate:
            self._points.clear()

    def upsert(self, points: Sequence[StorePoint]) -> int:
        for point in points:
            self._points[point.chunk_id] = point
        return len(points)

    def _candidates(
        self,
        jurisdiction: str | None,
        doc_types: list[str] | None,
        exclude_chunk_ids: list[str] | None,
    ) -> list[StorePoint]:
        if not (jurisdiction or doc_types or exclude_chunk_ids):
            return list(self._points.values())
        return [
            point
            for point in self._points.values()
            if matches_filter(
                point.payload, jurisdiction, doc_types, exclude_chunk_ids
            )
        ]

    def search_dense(
        self,
        vector: list[float],
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        scored: list[ScoredPoint] = []
        for point in self._candidates(jurisdiction, doc_types, exclude_chunk_ids):
            scored.append(
                ScoredPoint(
                    chunk_id=point.chunk_id,
                    score=_cosine(vector, point.dense),
                    payload=point.payload,
                )
            )
        scored.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return scored[:limit]

    def search_sparse(
        self,
        sparse: SparseVector,
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        if not sparse:
            return []
        scored: list[ScoredPoint] = []
        for point in self._candidates(jurisdiction, doc_types, exclude_chunk_ids):
            score = sparse.dot(point.sparse)
            if score > 0:
                scored.append(
                    ScoredPoint(chunk_id=point.chunk_id, score=score, payload=point.payload)
                )
        scored.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return scored[:limit]

    def count(self) -> int:
        return len(self._points)

    def all_payloads(self) -> list[dict[str, Any]]:
        return [dict(point.payload) for point in self._points.values()]


def _cosine(first: list[float], second: list[float]) -> float:
    """Cosine similarity; assumes at least one side may be unnormalised."""
    if not first or not second:
        return 0.0
    length = min(len(first), len(second))
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for index in range(length):
        a = first[index]
        b = second[index]
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class QdrantStore(VectorStore):
    """Production backend: Qdrant with named dense + sparse vectors."""

    name = "qdrant"

    def __init__(self, settings: Settings | None = None, *, url: str | None = None) -> None:
        super().__init__(settings)
        from qdrant_client import QdrantClient

        self.collection = self.settings.qdrant_collection
        self._client = QdrantClient(
            url=url or self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key or None,
            timeout=30.0,
            prefer_grpc=False,
        )

    # ------------------------------------------------------------------ schema
    def ensure_collection(self, dim: int, *, recreate: bool = False) -> None:
        from qdrant_client import models as qmodels

        exists = self._collection_exists()
        if exists and recreate:
            self._client.delete_collection(self.collection)
            exists = False

        if not exists:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    DENSE_VECTOR_NAME: qmodels.VectorParams(
                        size=dim, distance=qmodels.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                        index=qmodels.SparseIndexParams(on_disk=False)
                    )
                },
            )
            log.info("qdrant.collection_created", collection=self.collection, dim=dim)

        # Payload indexes make the jurisdiction filter an index lookup.
        for field_name, schema in (
            ("jurisdiction", qmodels.PayloadSchemaType.KEYWORD),
            ("doc_type", qmodels.PayloadSchemaType.KEYWORD),
            ("chunk_id", qmodels.PayloadSchemaType.KEYWORD),
            ("risk_level", qmodels.PayloadSchemaType.KEYWORD),
            ("tags", qmodels.PayloadSchemaType.KEYWORD),
        ):
            try:
                self._client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=schema,
                    wait=True,
                )
            except Exception as exc:
                log.debug("qdrant.payload_index_skipped", field=field_name, error=str(exc))

    def _collection_exists(self) -> bool:
        try:
            return bool(self._client.collection_exists(self.collection))
        except AttributeError:  # pragma: no cover - older qdrant-client
            try:
                self._client.get_collection(self.collection)
                return True
            except Exception:
                return False

    # ------------------------------------------------------------------ writes
    def upsert(self, points: Sequence[StorePoint]) -> int:
        from qdrant_client import models as qmodels

        if not points:
            return 0
        batch = [
            qmodels.PointStruct(
                id=point_id_for(point.chunk_id),
                vector={
                    DENSE_VECTOR_NAME: point.dense,
                    SPARSE_VECTOR_NAME: qmodels.SparseVector(
                        indices=point.sparse.indices, values=point.sparse.values
                    ),
                },
                payload=point.payload,
            )
            for point in points
        ]
        self._client.upsert(collection_name=self.collection, points=batch, wait=True)
        return len(batch)

    # ------------------------------------------------------------------ reads
    def _query(
        self,
        using: str,
        query: Any,
        limit: int,
        jurisdiction: str | None,
        doc_types: list[str] | None,
        exclude_chunk_ids: list[str] | None,
    ) -> list[ScoredPoint]:
        query_filter = build_jurisdiction_filter(
            jurisdiction, doc_types=doc_types, exclude_chunk_ids=exclude_chunk_ids
        )
        response = self._client.query_points(
            collection_name=self.collection,
            query=query,
            using=using,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        results = getattr(response, "points", response) or []
        hits: list[ScoredPoint] = []
        for item in results:
            payload = dict(getattr(item, "payload", {}) or {})
            hits.append(
                ScoredPoint(
                    chunk_id=payload.get("chunk_id", str(getattr(item, "id", ""))),
                    score=float(getattr(item, "score", 0.0) or 0.0),
                    payload=payload,
                )
            )
        return hits

    def search_dense(
        self,
        vector: list[float],
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        return self._query(
            DENSE_VECTOR_NAME, vector, limit, jurisdiction, doc_types, exclude_chunk_ids
        )

    def search_sparse(
        self,
        sparse: SparseVector,
        limit: int,
        *,
        jurisdiction: str | None = None,
        doc_types: list[str] | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[ScoredPoint]:
        from qdrant_client import models as qmodels

        if not sparse:
            return []
        return self._query(
            SPARSE_VECTOR_NAME,
            qmodels.SparseVector(indices=sparse.indices, values=sparse.values),
            limit,
            jurisdiction,
            doc_types,
            exclude_chunk_ids,
        )

    def count(self) -> int:
        # A missing collection is an empty collection, not an error: a cold deployment
        # has no collection until ingestion creates it, and every caller treats that as
        # "no data yet".
        if not self._collection_exists():
            return 0
        return int(self._client.count(self.collection, exact=True).count)

    def ping(self) -> bool:
        """Connectivity probe against the Qdrant server itself, not the collection."""
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    def all_payloads(self) -> list[dict[str, Any]]:
        if not self._collection_exists():
            return []
        payloads: list[dict[str, Any]] = []
        offset: Any = None
        while True:
            records, offset = self._client.scroll(
                collection_name=self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            payloads.extend(dict(record.payload or {}) for record in records)
            if offset is None:
                break
        return payloads


def build_store(
    settings: Settings | None = None,
    *,
    backend: VectorBackend | None = None,
    verify: bool = True,
) -> VectorStore:
    """Resolve the configured backend, falling back to memory when Qdrant is down.

    ``verify`` performs a *connectivity* probe against the server, not a data check:
    a freshly started container has no collection yet and must still select Qdrant so
    ingestion can create it. Only an unreachable server triggers the fallback.
    """
    resolved = settings or get_settings()
    chosen = backend or resolved.vector_backend

    if chosen in (VectorBackend.AUTO, VectorBackend.QDRANT):
        try:
            store = QdrantStore(resolved)
            if not verify or store.ping():
                return store
            log.warning("store.qdrant_unreachable_fallback", url=resolved.qdrant_url)
        except Exception as exc:
            log.warning("store.qdrant_unavailable", url=resolved.qdrant_url, error=str(exc))
            if chosen is VectorBackend.QDRANT:
                raise
    return InMemoryStore(resolved)
