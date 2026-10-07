"""Embedding providers.

The platform must produce a usable dense index with **zero credentials**, so the
default is a deterministic feature-hashing embedder. It is a genuine (if modest)
semantic representation: unigrams, adjacent bigrams and character 4-grams are hashed
into a fixed-width space with signed collisions. Lexically related passages — exactly
what legal retrieval depends on — land close together with no network round-trip and
no per-token cost.

When an API key is present the OpenAI embedder takes over automatically; the
interface is identical, so the ingestion pipeline is unchanged.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from itertools import pairwise

from ..config import EmbeddingProvider as EmbeddingProviderEnum
from ..config import Settings, get_settings
from ..observability.logging import get_logger
from ..telemetry import tracker
from ..telemetry.pricing import EMBEDDING_PRICE_BOOK, lookup
from .chunking import hash_index, hash_sign, tokenize

log = get_logger(__name__)


class Embedder(ABC):
    """Dense-vector encoder."""

    name: str = "base"
    provider: str = "base"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def dim(self) -> int:
        return self.settings.embedding_dim

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of documents."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query. Providers may override for asymmetric models."""
        return self.embed_documents([text])[0]


def _l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


class HashEmbedder(Embedder):
    """Deterministic feature-hashing embedder (offline default).

    Signed hashing keeps the expected inner product of two documents proportional to
    their true lexical overlap even though the vocabulary is never materialised.
    """

    name = "hash-embedding"
    provider = "offline"

    #: Relative contribution of each feature family.
    UNIGRAM_WEIGHT = 1.0
    BIGRAM_WEIGHT = 0.6
    CHARGRAM_WEIGHT = 0.3
    CHARGRAM_SIZE = 4

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        dim = self.dim
        vector = [0.0] * dim
        tokens = tokenize(text)

        for token in tokens:
            vector[hash_index(token) % dim] += self.UNIGRAM_WEIGHT * hash_sign(token)

        for first, second in pairwise(tokens):
            bigram = f"{first}_{second}"
            vector[hash_index(bigram) % dim] += self.BIGRAM_WEIGHT * hash_sign(bigram)

        for token in tokens:
            if len(token) < self.CHARGRAM_SIZE:
                continue
            padded = f"#{token}#"
            for position in range(len(padded) - self.CHARGRAM_SIZE + 1):
                gram = padded[position : position + self.CHARGRAM_SIZE]
                vector[hash_index(gram) % dim] += self.CHARGRAM_WEIGHT * hash_sign(gram)

        return _l2_normalise(vector)


class OpenAIEmbedder(Embedder):
    """OpenAI embedding models."""

    provider = "openai"
    BATCH_SIZE = 96

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        from openai import OpenAI

        self.name = self.settings.embedding_model
        self._client = OpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,
            timeout=self.settings.request_timeout_s,
            max_retries=2,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = list(texts[start : start + self.BATCH_SIZE])
            kwargs: dict[str, object] = {"model": self.name, "input": batch}
            # Only the v3 family supports an explicit output dimension.
            if "text-embedding-3" in self.name:
                kwargs["dimensions"] = self.dim
            response = self._client.embeddings.create(**kwargs)  # type: ignore[arg-type]
            vectors.extend([item.embedding for item in response.data])
            self._record(len(batch), response)
        return vectors

    def _record(self, batch_size: int, response: object) -> None:
        """Price the embedding call; embeddings are input-token-only."""
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        if prompt_tokens <= 0:
            return
        price = lookup(self.name, book=EMBEDDING_PRICE_BOOK)
        try:
            tracker.record_call(
                session_id="ingestion",
                model=self.name,
                provider="openai",
                node="embedding",
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                simulate_offline=False,
                cost_override_usd=price.cost(prompt_tokens, 0),
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            log.debug("embedding.telemetry_failed", model=self.name, batch=batch_size)


class OllamaEmbedder(Embedder):
    """Self-hosted Ollama embedding endpoint (no per-token cost)."""

    provider = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.name = self.settings.embedding_model
        self.dim = 0  # discovered from the first response

    @property
    def dim(self) -> int:  # type: ignore[override]
        return self._dim

    @dim.setter
    def dim(self, value: int) -> None:
        self._dim = value or self.settings.embedding_dim

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        import httpx

        base = self.settings.ollama_base_url.rstrip("/")
        vectors: list[list[float]] = []
        with httpx.Client(timeout=self.settings.request_timeout_s) as client:
            for text in texts:
                response = client.post(
                    f"{base}/api/embeddings",
                    json={"model": self.name, "prompt": text},
                )
                response.raise_for_status()
                vector = response.json().get("embedding", [])
                if vector and self._dim == 0:
                    self.dim = len(vector)
                vectors.append(vector)
        return vectors


def build_embedder(settings: Settings | None = None) -> Embedder:
    """Resolve the configured embedding provider, degrading to the offline one."""
    resolved = settings or get_settings()
    provider = resolved.resolved_embedding_provider

    if provider is EmbeddingProviderEnum.OPENAI and resolved.has_openai:
        try:
            return OpenAIEmbedder(resolved)
        except Exception as exc:
            log.warning("embedder.init_failed", provider="openai", error=str(exc))
    if provider is EmbeddingProviderEnum.OLLAMA:
        try:
            return OllamaEmbedder(resolved)
        except Exception as exc:
            log.warning("embedder.init_failed", provider="ollama", error=str(exc))
    return HashEmbedder(resolved)
