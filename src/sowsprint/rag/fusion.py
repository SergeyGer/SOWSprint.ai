"""Reciprocal Rank Fusion for hybrid retrieval.

Dense and sparse retrieval produce scores on incomparable scales — cosine similarity
lives in ``[-1, 1]`` while BM25 is unbounded. Naively summing them lets whichever arm
happens to produce large numbers dominate. Reciprocal Rank Fusion (Cormack et al.,
2009) sidesteps the calibration problem entirely by fusing *ranks*:

    score(d) = Σ_arms  weight_arm / (k + rank_arm(d))

with ``k = 60`` as the standard smoothing constant. The result is scale-free, robust
to one arm returning garbage, and requires no per-corpus tuning.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .store import ScoredPoint


@dataclass
class FusedHit:
    """A chunk with its per-arm ranks and the fused score."""

    chunk_id: str
    payload: dict[str, Any]
    fused_score: float = 0.0
    dense_score: float = 0.0
    sparse_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    contributed_arms: list[str] = field(default_factory=list)


def reciprocal_rank_fusion(
    dense_hits: Sequence[ScoredPoint],
    sparse_hits: Sequence[ScoredPoint],
    *,
    k: float = 60.0,
    dense_weight: float = 1.0,
    sparse_weight: float = 1.0,
) -> list[FusedHit]:
    """Fuse two ranked lists into one, ordered by descending fused score.

    Ties are broken by chunk id so the ordering is fully deterministic — a
    requirement for reproducible evaluation runs.
    """
    merged: dict[str, FusedHit] = {}

    def _ingest(hits: Sequence[ScoredPoint], arm: str, weight: float) -> None:
        for rank, hit in enumerate(hits, start=1):
            entry = merged.get(hit.chunk_id)
            if entry is None:
                entry = FusedHit(chunk_id=hit.chunk_id, payload=dict(hit.payload))
                merged[hit.chunk_id] = entry
            entry.fused_score += weight / (k + rank)
            entry.contributed_arms.append(arm)
            if arm == "dense":
                entry.dense_score = hit.score
                entry.dense_rank = rank
            else:
                entry.sparse_score = hit.score
                entry.sparse_rank = rank

    _ingest(dense_hits, "dense", dense_weight)
    _ingest(sparse_hits, "sparse", sparse_weight)

    ordered = sorted(merged.values(), key=lambda hit: (-hit.fused_score, hit.chunk_id))
    return ordered


def explain_fusion(hits: Sequence[FusedHit], limit: int = 5) -> str:
    """Compact diagnostic table for the UI's retrieval inspector."""
    lines = ["| # | chunk | dense rank | sparse rank | RRF |", "| --: | :-- | --: | --: | --: |"]
    for index, hit in enumerate(hits[:limit], start=1):
        lines.append(
            f"| {index} | `{hit.chunk_id}` | {hit.dense_rank or '—'} | "
            f"{hit.sparse_rank or '—'} | {hit.fused_score:.5f} |"
        )
    return "\n".join(lines)
