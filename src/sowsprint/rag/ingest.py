"""Corpus ingestion: chunk, embed, index, and fit the sparse encoder.

The ingestion path is idempotent and self-healing:

* chunk ids are content-derived, so re-ingesting produces identical point ids and
  Qdrant overwrites rather than duplicates;
* BM25 statistics are persisted next to the data directory and refitted from the
  vector store when the cache is missing, so a fresh container against a populated
  Qdrant volume still produces correct sparse weights.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import ARTIFACT_DIR, CORPUS_DIR, Settings, get_settings
from ..observability.logging import get_logger
from .chunking import BM25Encoder, TextChunk, chunk_document
from .embeddings import Embedder, build_embedder
from .schema import ChunkPayload
from .store import StorePoint, VectorStore, build_store

if TYPE_CHECKING:  # pragma: no cover
    pass

log = get_logger(__name__)

BM25_STATS_PATH = ARTIFACT_DIR / "bm25_stats.json"


@dataclass
class IngestStats:
    """Outcome of one ingestion pass."""

    entries: int = 0
    chunks: int = 0
    upserted: int = 0
    dim: int = 0
    backend: str = ""
    embedder: str = ""
    eu_chunks: int = 0
    us_chunks: int = 0
    duration_ms: float = 0.0
    reused_existing: bool = False

    def summary(self) -> str:
        return (
            f"{self.entries} corpus entries → {self.chunks} chunks "
            f"({self.eu_chunks} EU / {self.us_chunks} US) indexed into "
            f"{self.backend} via {self.embedder} in {self.duration_ms:.0f} ms"
        )


def load_corpus() -> list[dict[str, Any]]:
    """Load the bundled compliance corpus, tolerating an absent data module."""
    try:
        from .corpus_data import CORPUS
    except ImportError:  # pragma: no cover - only when the data module is stripped
        log.error("ingest.corpus_missing")
        return []
    return list(CORPUS)


def load_corpus_from_directory(directory: Path | None = None) -> list[dict[str, Any]]:
    """Load optional extra corpus entries from ``data/corpus/*.json``.

    Lets an operator extend the compliance knowledge base without touching code.
    """
    import json

    target = directory or CORPUS_DIR
    if not target.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for path in sorted(target.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("ingest.extra_corpus_unreadable", path=str(path), error=str(exc))
            continue
        if isinstance(payload, list):
            entries.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            entries.append(payload)
    return entries


def build_payloads(
    settings: Settings | None = None,
    *,
    entries: list[dict[str, Any]] | None = None,
) -> list[tuple[ChunkPayload, TextChunk]]:
    """Chunk every corpus entry into indexable payloads."""
    resolved = settings or get_settings()
    corpus = entries if entries is not None else [*load_corpus(), *load_corpus_from_directory()]

    built: list[tuple[ChunkPayload, TextChunk]] = []
    for entry in corpus:
        parent_id = str(entry.get("id") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not parent_id or not text:
            continue

        chunks = chunk_document(
            text,
            parent_id=parent_id,
            source=str(entry.get("source", "")),
            chunk_size=resolved.chunk_size,
            overlap=resolved.chunk_overlap,
        )
        source = str(entry.get("source", ""))
        title = str(entry.get("title", parent_id))
        for chunk in chunks:
            citation = f"{title} — {source}" if source else title
            payload = ChunkPayload(
                chunk_id=chunk.chunk_id,
                parent_id=parent_id,
                jurisdiction=str(entry.get("jurisdiction", "EU")).upper(),
                doc_type=str(entry.get("doc_type", "contract_clause")),
                source=source,
                title=title,
                text=chunk.text,
                citation=citation,
                tags=[str(tag).casefold() for tag in entry.get("tags", []) or []][:8],
                risk_level=str(entry.get("risk_level", "medium")),
                chunk_index=chunk.chunk_index,
                token_count=chunk.token_count,
            )
            built.append((payload, chunk))
    return built


def ingest_corpus(
    *,
    store: VectorStore | None = None,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
    recreate: bool = False,
    entries: list[dict[str, Any]] | None = None,
) -> tuple[IngestStats, BM25Encoder, VectorStore, Embedder]:
    """Chunk, embed and index the compliance corpus.

    Returns the statistics plus the fitted BM25 encoder and the store/embedder that
    were used, so callers can adopt them without rebuilding anything.
    """
    resolved = settings or get_settings()
    started = time.perf_counter()

    resolved_store = store or build_store(resolved)
    resolved_embedder = embedder or build_embedder(resolved)

    payloads = build_payloads(resolved, entries=entries)
    if not payloads:
        log.warning("ingest.nothing_to_index")
        return (
            IngestStats(backend=resolved_store.name, embedder=getattr(resolved_embedder, "name", "")),
            BM25Encoder(),
            resolved_store,
            resolved_embedder,
        )

    dim = resolved_embedder.dim
    resolved_store.ensure_collection(dim, recreate=recreate)

    documents = [payload.text for payload, _chunk in payloads]
    vectors = resolved_embedder.embed_documents(documents)

    encoder = BM25Encoder()
    encoder.fit(documents)

    points = [
        StorePoint(
            chunk_id=payload.chunk_id,
            dense=vector,
            sparse=encoder.encode_document(payload.text),
            payload=payload.model_dump(mode="json"),
        )
        for (payload, _chunk), vector in zip(payloads, vectors, strict=False)
    ]
    upserted = resolved_store.upsert(points)

    BM25_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    encoder.save(BM25_STATS_PATH)

    stats = IngestStats(
        entries=len({payload.parent_id for payload, _ in payloads}),
        chunks=len(payloads),
        upserted=upserted,
        dim=dim,
        backend=resolved_store.name,
        embedder=getattr(resolved_embedder, "name", "unknown"),
        eu_chunks=sum(1 for payload, _ in payloads if payload.jurisdiction == "EU"),
        us_chunks=sum(1 for payload, _ in payloads if payload.jurisdiction == "US"),
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )
    log.info("ingest.completed", **dict(stats.__dict__))
    return stats, encoder, resolved_store, resolved_embedder


def load_or_refit_bm25(store: VectorStore, *, settings: Settings | None = None) -> BM25Encoder:
    """Load cached BM25 statistics, refitting from the store when unavailable.

    A container restart against a persisted Qdrant volume has vectors but no local
    statistics file; refitting from the stored payloads keeps sparse retrieval exact
    instead of silently degrading it.
    """
    encoder = BM25Encoder.load(BM25_STATS_PATH)
    if encoder is not None and encoder.is_fitted:
        return encoder

    log.info("ingest.bm25_refit_from_store")
    payloads = store.all_payloads()
    encoder = BM25Encoder()
    if payloads:
        encoder.fit([str(payload.get("text", "")) for payload in payloads])
    return encoder


def ensure_corpus_indexed(
    *,
    retriever: Any = None,
    store: VectorStore | None = None,
    recreate: bool = False,
    settings: Settings | None = None,
) -> IngestStats:
    """Index the corpus only when the target collection is empty.

    Called lazily on first retrieval so a cold container self-seeds without an
    operator-run migration step.
    """
    resolved = settings or get_settings()
    target_store = store or (retriever.store if retriever is not None else None) or build_store(resolved)

    try:
        existing = target_store.count()
    except Exception as exc:
        log.warning("ingest.count_failed", error=str(exc))
        existing = 0

    if existing > 0 and not recreate:
        encoder = load_or_refit_bm25(target_store, settings=resolved)
        if retriever is not None:
            retriever.store = target_store
            retriever.bm25 = encoder
        return IngestStats(
            chunks=existing,
            backend=target_store.name,
            reused_existing=True,
        )

    stats, encoder, resolved_store, resolved_embedder = ingest_corpus(
        store=target_store, settings=resolved, recreate=recreate
    )
    if retriever is not None:
        retriever.store = resolved_store
        retriever.bm25 = encoder
        retriever.embedder = resolved_embedder
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python -m sowsprint.rag.ingest [--recreate]``."""
    import argparse

    from ..observability.logging import configure_logging

    parser = argparse.ArgumentParser(
        description="Ingest the SOWSprint compliance corpus into the vector store."
    )
    parser.add_argument("--recreate", action="store_true", help="Drop and rebuild the collection.")
    parser.add_argument("--stats-only", action="store_true", help="Report corpus size and exit.")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.stats_only:
        payloads = build_payloads(settings)
        print(f"corpus entries : {len({p.parent_id for p, _ in payloads})}")
        print(f"chunks         : {len(payloads)}")
        print(f"  EU           : {sum(1 for p, _ in payloads if p.jurisdiction == 'EU')}")
        print(f"  US           : {sum(1 for p, _ in payloads if p.jurisdiction == 'US')}")
        return 0

    stats, _encoder, _store, _embedder = ingest_corpus(
        settings=settings, recreate=args.recreate
    )
    print(stats.summary())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
