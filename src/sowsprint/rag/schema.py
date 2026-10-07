"""Vector-store payload contract and metadata-filter construction.

The engineering constraint driving this module is *query-level* jurisdiction
filtering: the compliance regime selected in the UI must be enforced by the database
engine, not by post-filtering a global result set. Post-filtering silently degrades
recall (the top-k may contain mostly the other jurisdiction) and can leak
wrong-jurisdiction clauses into a contract.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Deterministic namespace so a chunk id always maps to the same Qdrant point id.
POINT_NAMESPACE = uuid.UUID("6f1a4e2c-9b3d-4c7e-8a51-2d6f0b9c4e11")

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


class DocType(str, Enum):
    """Corpus document classes; usable as a secondary metadata filter."""

    CONTRACT_CLAUSE = "contract_clause"
    STATUTE = "statute"
    REGULATION = "regulation"
    CASE_NOTE = "case_note"
    PLAYBOOK = "playbook"
    GUIDANCE = "guidance"


class ChunkPayload(BaseModel):
    """Everything stored alongside a vector in Qdrant.

    Payload fields are intentionally flat and low-cardinality: Qdrant builds payload
    indexes over them, and the ``jurisdiction`` keyword index is what makes the
    metadata filter an index scan rather than a collection scan.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    parent_id: str
    jurisdiction: str
    doc_type: str = DocType.CONTRACT_CLAUSE.value
    source: str
    title: str
    text: str
    citation: str = ""
    tags: list[str] = Field(default_factory=list)
    risk_level: str = "medium"
    chunk_index: int = 0
    token_count: int = 0

    def filterable(self) -> dict[str, Any]:
        """Payload subset used by the filter builder."""
        return {
            "jurisdiction": self.jurisdiction,
            "doc_type": self.doc_type,
            "risk_level": self.risk_level,
            "tags": self.tags,
        }


def point_id_for(chunk_id: str) -> str:
    """Map a human-readable chunk id to a deterministic UUIDv5 point id."""
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


def build_jurisdiction_filter(
    jurisdiction: str | None,
    *,
    doc_types: list[str] | None = None,
    exclude_chunk_ids: list[str] | None = None,
    tags_any: list[str] | None = None,
) -> Any:
    """Build a Qdrant ``Filter`` enforcing the jurisdiction constraint.

    Returns ``None`` when no constraint applies, which Qdrant interprets as
    "search the whole collection".

    The filter is expressed as ``must`` conditions so the vector engine narrows the
    candidate set *before* scoring. ``exclude_chunk_ids`` uses ``must_not`` with a
    payload match on ``chunk_id``.
    """
    try:
        from qdrant_client import models as qmodels
    except ImportError:  # pragma: no cover - qdrant-client is a hard dependency
        return None

    must: list[Any] = []
    must_not: list[Any] = []

    if jurisdiction:
        must.append(
            qmodels.FieldCondition(
                key="jurisdiction",
                match=qmodels.MatchValue(value=jurisdiction),
            )
        )

    if doc_types:
        must.append(
            qmodels.FieldCondition(key="doc_type", match=qmodels.MatchAny(any=doc_types))
        )

    if tags_any:
        must.append(
            qmodels.FieldCondition(key="tags", match=qmodels.MatchAny(any=tags_any))
        )

    if exclude_chunk_ids:
        must_not.append(
            qmodels.FieldCondition(
                key="chunk_id", match=qmodels.MatchAny(any=list(exclude_chunk_ids))
            )
        )

    if not must and not must_not:
        return None
    return qmodels.Filter(must=must or None, must_not=must_not or None)


def matches_filter(
    payload: dict[str, Any],
    jurisdiction: str | None,
    doc_types: list[str] | None = None,
    exclude_chunk_ids: list[str] | None = None,
) -> bool:
    """Python mirror of :func:`build_jurisdiction_filter` for the in-memory store.

    Keeping the two implementations side by side guarantees the fallback backend
    applies *identical* filtering semantics to the production vector engine.
    """
    if jurisdiction and payload.get("jurisdiction") != jurisdiction:
        return False
    if doc_types and payload.get("doc_type") not in doc_types:
        return False
    return not (exclude_chunk_ids and payload.get("chunk_id") in set(exclude_chunk_ids))
