"""Cross-encoder reranking layer.

Fusion optimises *recall*; reranking optimises *context density*. The Legal agent has
a finite context budget, so the five passages that survive reranking determine the
quality of the contract far more than the twenty that entered fusion.

Three implementations share one interface:

* :class:`HeuristicReranker` — offline. A genuine cross-encoder *surrogate*: unlike
  BM25 it scores the query and document jointly, using IDF computed **over the
  candidate set**, phrase-adjacency, title overlap and tag overlap. It is
  deterministic and needs no GPU.
* :class:`TEIReranker` — a self-hosted BGE reranker served by HuggingFace
  text-embeddings-inference.
* :class:`CohereReranker` — Cohere's hosted rerank endpoint.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from ..config import RerankProvider, Settings, get_settings
from ..observability.logging import get_logger
from .chunking import tokenize
from .fusion import FusedHit

log = get_logger(__name__)


@dataclass
class RerankedHit:
    """A fused hit with its cross-encoder score."""

    chunk_id: str
    payload: dict
    rerank_score: float
    fused_score: float
    dense_score: float
    sparse_score: float
    dense_rank: int | None
    sparse_rank: int | None
    rank: int = 0


class Reranker(ABC):
    """Joint query-document scorer."""

    name: str = "base"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    def rerank(
        self, query: str, candidates: Sequence[FusedHit], top_k: int
    ) -> list[RerankedHit]:
        """Return the ``top_k`` candidates reordered by joint relevance."""


class HeuristicReranker(Reranker):
    """Deterministic cross-encoder surrogate.

    Score components (weights are explicit so the behaviour is auditable):

    ====================  ======  ==================================================
    Component             Weight  Rationale
    ====================  ======  ==================================================
    IDF-weighted coverage  0.42   Rare query terms matter more than common ones.
    Phrase adjacency       0.18   "data subject request" beats the same words apart.
    Title overlap          0.16   Statute/clause titles are highly informative.
    Tag overlap            0.12   Curated keywords encode the drafter's intent.
    Fusion prior           0.12   Retains the recall signal from both arms.
    ====================  ======  ==================================================
    """

    name = "heuristic-cross-encoder"

    W_COVERAGE = 0.42
    W_PHRASE = 0.18
    W_TITLE = 0.16
    W_TAG = 0.12
    W_PRIOR = 0.12

    def rerank(
        self, query: str, candidates: Sequence[FusedHit], top_k: int
    ) -> list[RerankedHit]:
        if not candidates:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return [
                RerankedHit(
                    chunk_id=hit.chunk_id,
                    payload=hit.payload,
                    rerank_score=hit.fused_score,
                    fused_score=hit.fused_score,
                    dense_score=hit.dense_score,
                    sparse_score=hit.sparse_score,
                    dense_rank=hit.dense_rank,
                    sparse_rank=hit.sparse_rank,
                    rank=index,
                )
                for index, hit in enumerate(candidates[:top_k], start=1)
            ]

        unique_query_terms = list(dict.fromkeys(query_tokens))

        # Document frequency over the candidate set — the reranker's own IDF.
        df: Counter[str] = Counter()
        doc_tokens: dict[str, list[str]] = {}
        for hit in candidates:
            tokens = tokenize(f"{hit.payload.get('title', '')} {hit.payload.get('text', '')}")
            doc_tokens[hit.chunk_id] = tokens
            for term in set(tokens):
                df[term] += 1
        total_docs = len(candidates)

        def idf(term: str) -> float:
            freq = df.get(term, 0)
            return math.log(1.0 + (total_docs - freq + 0.5) / (freq + 0.5))

        query_terms = set(unique_query_terms)
        query_bigrams = list(pairwise(query_tokens))
        max_fused = max((hit.fused_score for hit in candidates), default=1.0) or 1.0

        scored: list[RerankedHit] = []
        for hit in candidates:
            tokens = doc_tokens[hit.chunk_id]
            token_counts = Counter(tokens)

            # 1. IDF-weighted term coverage, with saturating term frequency and a
            #    gentle length penalty so long passages do not win by sheer size.
            matched_weight = 0.0
            total_weight = 0.0
            for term in unique_query_terms:
                weight = idf(term)
                total_weight += weight
                tf = token_counts.get(term, 0)
                if tf:
                    matched_weight += weight * (tf / (tf + 1.2))
            raw_coverage = (matched_weight / total_weight) if total_weight else 0.0
            length_norm = 1.0 + math.log(1.0 + len(tokens) / 400.0)
            coverage = min(1.0, raw_coverage / length_norm)

            # 2. Phrase adjacency: query bigrams appearing contiguously.
            if query_bigrams:
                doc_bigrams = set(pairwise(tokens))
                phrase_hits = sum(1 for bigram in query_bigrams if bigram in doc_bigrams)
                phrase = phrase_hits / len(query_bigrams)
            else:
                phrase = 0.0

            # 3. Title overlap: share of query terms present in the document title.
            title_tokens = set(tokenize(str(hit.payload.get("title", ""))))
            title_matches = title_tokens & query_terms
            title_overlap = len(title_matches) / max(1, len(query_terms)) if title_matches else 0.0

            # 4. Tag overlap.
            tags = {str(tag).casefold() for tag in hit.payload.get("tags", []) or []}
            tag_matches = tags & query_terms
            tag_overlap = len(tag_matches) / max(1, len(query_terms)) if tag_matches else 0.0

            # 5. Fusion prior, normalised to [0, 1].
            prior = hit.fused_score / max_fused if max_fused else 0.0

            score = (
                self.W_COVERAGE * coverage
                + self.W_PHRASE * phrase
                + self.W_TITLE * title_overlap
                + self.W_TAG * tag_overlap
                + self.W_PRIOR * prior
            )

            scored.append(
                RerankedHit(
                    chunk_id=hit.chunk_id,
                    payload=hit.payload,
                    rerank_score=round(min(1.0, max(0.0, score)), 6),
                    fused_score=hit.fused_score,
                    dense_score=hit.dense_score,
                    sparse_score=hit.sparse_score,
                    dense_rank=hit.dense_rank,
                    sparse_rank=hit.sparse_rank,
                )
            )

        scored.sort(key=lambda item: (-item.rerank_score, item.chunk_id))
        for index, item in enumerate(scored, start=1):
            item.rank = index
        return scored[:top_k]


class TEIReranker(Reranker):
    """Self-hosted BGE reranker via HuggingFace text-embeddings-inference."""

    name = "bge-reranker-tei"

    def rerank(
        self, query: str, candidates: Sequence[FusedHit], top_k: int
    ) -> list[RerankedHit]:
        import httpx

        url = self.settings.tei_rerank_url
        if not url or not candidates:
            return HeuristicReranker(self.settings).rerank(query, candidates, top_k)

        documents = [
            f"{hit.payload.get('title', '')}\n{hit.payload.get('text', '')}".strip()
            for hit in candidates
        ]
        try:
            with httpx.Client(timeout=self.settings.request_timeout_s) as client:
                response = client.post(
                    url,
                    json={
                        "query": query,
                        "texts": documents,
                        "raw_scores": False,
                        "return_text": False,
                    },
                )
                response.raise_for_status()
                results = response.json()
        except Exception as exc:
            log.warning("rerank.tei_failed", url=url, error=str(exc))
            return HeuristicReranker(self.settings).rerank(query, candidates, top_k)

        scored: list[RerankedHit] = []
        for item in results:
            index = int(item.get("index", -1))
            if not (0 <= index < len(candidates)):
                continue
            hit = candidates[index]
            scored.append(
                RerankedHit(
                    chunk_id=hit.chunk_id,
                    payload=hit.payload,
                    rerank_score=float(item.get("score", 0.0)),
                    fused_score=hit.fused_score,
                    dense_score=hit.dense_score,
                    sparse_score=hit.sparse_score,
                    dense_rank=hit.dense_rank,
                    sparse_rank=hit.sparse_rank,
                )
            )
        scored.sort(key=lambda entry: (-entry.rerank_score, entry.chunk_id))
        for rank, entry in enumerate(scored, start=1):
            entry.rank = rank
        return scored[:top_k]


class CohereReranker(Reranker):
    """Cohere hosted rerank endpoint."""

    name = "cohere-rerank"
    ENDPOINT = "https://api.cohere.com/v2/rerank"

    def rerank(
        self, query: str, candidates: Sequence[FusedHit], top_k: int
    ) -> list[RerankedHit]:
        import httpx

        if not self.settings.has_cohere or not candidates:
            return HeuristicReranker(self.settings).rerank(query, candidates, top_k)

        documents = [
            f"{hit.payload.get('title', '')}\n{hit.payload.get('text', '')}".strip()
            for hit in candidates
        ]
        try:
            with httpx.Client(timeout=self.settings.request_timeout_s) as client:
                response = client.post(
                    self.ENDPOINT,
                    headers={"Authorization": f"Bearer {self.settings.cohere_api_key}"},
                    json={
                        "model": self.settings.rerank_model,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_k, len(documents)),
                    },
                )
                response.raise_for_status()
                results = response.json().get("results", [])
        except Exception as exc:
            log.warning("rerank.cohere_failed", error=str(exc))
            return HeuristicReranker(self.settings).rerank(query, candidates, top_k)

        scored: list[RerankedHit] = []
        for item in results:
            index = int(item.get("index", -1))
            if not (0 <= index < len(candidates)):
                continue
            hit = candidates[index]
            scored.append(
                RerankedHit(
                    chunk_id=hit.chunk_id,
                    payload=hit.payload,
                    rerank_score=float(item.get("relevance_score", 0.0)),
                    fused_score=hit.fused_score,
                    dense_score=hit.dense_score,
                    sparse_score=hit.sparse_score,
                    dense_rank=hit.dense_rank,
                    sparse_rank=hit.sparse_rank,
                )
            )
        scored.sort(key=lambda entry: (-entry.rerank_score, entry.chunk_id))
        for rank, entry in enumerate(scored, start=1):
            entry.rank = rank
        return scored[:top_k]


def build_reranker(settings: Settings | None = None) -> Reranker:
    """Resolve the configured reranking provider."""
    resolved = settings or get_settings()
    provider = resolved.resolved_rerank_provider
    if provider is RerankProvider.TEI and resolved.tei_rerank_url:
        return TEIReranker(resolved)
    if provider is RerankProvider.COHERE and resolved.has_cohere:
        return CohereReranker(resolved)
    return HeuristicReranker(resolved)
