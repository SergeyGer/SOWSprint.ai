"""Legal-aware chunking and sparse (BM25) encoding.

Two concerns live here because they share the same tokenizer:

* **Chunking** — legal corpora punish naive fixed-width splitting. A liability cap
  severed from its proviso is worse than useless, because the Legal agent will cite
  a half-sentence. The chunker therefore prefers structural boundaries (numbered
  clauses, lettered sub-clauses, paragraph breaks) and only falls back to sentence
  packing when a passage has no structure.
* **Sparse encoding** — BM25 with a hashed vocabulary, so term indices are stable
  across processes without shipping a vocabulary file. Python's built-in ``hash()``
  is salted per process and therefore unusable here; ``zlib.crc32`` is stable.
"""

from __future__ import annotations

import json
import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..models import stable_id

# --------------------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------------------

#: Sparse vocabulary size (2^20 buckets). Collisions are rare and, with signed
#: hashing, approximately self-cancelling.
HASH_SPACE = 1 << 20

BM25_K1 = 1.5
BM25_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9§]+(?:[.\-][a-z0-9]+)*")

_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "of", "in", "on", "at", "to", "for", "from", "by", "with", "without", "within", "into", "onto", "over", "under", "about", "as", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "doing", "have", "has", "had", "having", "will", "would", "shall", "should", "may", "might", "must", "can", "could", "not", "no", "nor", "so", "such", "it", "its", "it's", "their", "there", "here", "they", "them", "we", "our", "us", "you", "your", "he", "she", "his", "her", "any", "all", "each", "both", "few", "more", "most", "other", "some", "only", "own", "same", "too", "very", "s", "t", "just", "don", "now"]
)


def tokenize(text: str) -> list[str]:
    """Lowercase, split, drop stopwords and light-stem plural/suffix noise.

    Deliberately conservative: legal terms of art ("indemnification",
    "sub-processor") must survive intact, so no aggressive Porter stemming.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.casefold()):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        if len(raw) > 4 and raw.endswith("ies"):
            raw = raw[:-3] + "y"
        elif len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        tokens.append(raw)
    return tokens


def hash_index(token: str) -> int:
    """Stable, process-independent term index."""
    return zlib.crc32(token.encode("utf-8")) % HASH_SPACE


def hash_sign(token: str) -> float:
    """Sign used by the feature-hashing embedder to cancel collision bias."""
    return 1.0 if (zlib.crc32(b"s:" + token.encode("utf-8")) & 1) else -1.0


# --------------------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------------------


@dataclass
class SparseVector:
    """A sparse vector in the (index, weight) form Qdrant expects."""

    indices: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.indices)

    def to_dict(self) -> dict[str, list[float]]:
        return {"indices": self.indices, "values": self.values}

    @classmethod
    def from_dict(cls, payload: dict[str, list[float]]) -> SparseVector:
        return cls(indices=list(payload.get("indices", [])), values=list(payload.get("values", [])))

    def dot(self, other: SparseVector) -> float:
        """Dot product against another sparse vector."""
        if not self.indices or not other.indices:
            return 0.0
        if len(self.indices) > len(other.indices):
            return other.dot(self)
        lookup = dict(zip(other.indices, other.values, strict=False))
        return sum(
            value * lookup.get(index, 0.0)
            for index, value in zip(self.indices, self.values, strict=False)
        )


class BM25Encoder:
    """Okapi BM25 with a hashed vocabulary.

    Documents and queries are encoded into the same hashed space, so the dot product
    of the two sparse vectors is exactly the BM25 score (up to collision noise).
    Corpus statistics (document frequency, average length) are fitted at ingestion
    time and persisted so query encoding in a fresh process reproduces the same
    weights.
    """

    def __init__(self, k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1 = k1
        self.b = b
        self.doc_freq: Counter[int] = Counter()
        self.doc_count: int = 0
        self.avg_length: float = 1.0
        self._fitted = False

    # ------------------------------------------------------------------ fitting
    def fit(self, documents: list[str]) -> None:
        """Compute document frequency and average length over the corpus."""
        self.doc_freq = Counter()
        total_length = 0
        for document in documents:
            tokens = tokenize(document)
            total_length += len(tokens)
            for index in {hash_index(token) for token in tokens}:
                self.doc_freq[index] += 1
        self.doc_count = len(documents)
        self.avg_length = (total_length / self.doc_count) if self.doc_count else 1.0
        self._fitted = True

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def idf(self, index: int) -> float:
        """Robertson-Sparck-Jones IDF with the standard +0.5 smoothing."""
        freq = self.doc_freq.get(index, 0)
        numerator = self.doc_count - freq + 0.5
        denominator = freq + 0.5
        if denominator <= 0 or numerator <= 0:
            return 0.0
        return math.log(1.0 + numerator / denominator)

    # ------------------------------------------------------------------ encoding
    def encode_document(self, text: str) -> SparseVector:
        """BM25 term weights for one document (the ``tf`` component of the score)."""
        tokens = tokenize(text)
        if not tokens:
            return SparseVector()
        length = len(tokens)
        counts = Counter(hash_index(token) for token in tokens)
        norm = self.k1 * (1.0 - self.b + self.b * (length / (self.avg_length or 1.0)))

        indices: list[int] = []
        values: list[float] = []
        for index, tf in sorted(counts.items()):
            weight = (tf * (self.k1 + 1.0)) / (tf + norm)
            indices.append(index)
            values.append(round(weight, 6))
        return SparseVector(indices, values)

    def encode_query(self, text: str) -> SparseVector:
        """IDF-weighted query terms (the ``idf`` component of the score)."""
        tokens = tokenize(text)
        if not tokens:
            return SparseVector()
        counts = Counter(hash_index(token) for token in tokens)
        indices: list[int] = []
        values: list[float] = []
        for index, tf in sorted(counts.items()):
            weight = self.idf(index) * tf
            if weight <= 0:
                continue
            indices.append(index)
            values.append(round(weight, 6))
        return SparseVector(indices, values)

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, object]:
        return {
            "k1": self.k1,
            "b": self.b,
            "doc_count": self.doc_count,
            "avg_length": self.avg_length,
            "doc_freq": {str(k): v for k, v in self.doc_freq.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> BM25Encoder:
        encoder = cls(k1=float(payload.get("k1", BM25_K1)), b=float(payload.get("b", BM25_B)))
        encoder.doc_count = int(payload.get("doc_count", 0))
        encoder.avg_length = float(payload.get("avg_length", 1.0))
        encoder.doc_freq = Counter(
            {int(k): int(v) for k, v in dict(payload.get("doc_freq", {})).items()}
        )
        encoder._fitted = encoder.doc_count > 0
        return encoder

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BM25Encoder | None:
        if not path.is_file():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


# --------------------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------------------

#: Structural boundaries, most significant first.
_CLAUSE_START_RE = re.compile(
    r"(?m)^\s*(?:"
    r"(?:\d+\.\d+(?:\.\d+)*)"        # 4.2 / 4.2.1
    r"|(?:\d+\.)"                     # 4.
    r"|(?:\([a-z]\))"                 # (a)
    r"|(?:\([ivx]+\))"                # (iv)
    r"|(?:ARTICLE\s+[IVXLC\d]+)"      # ARTICLE IV
    r"|(?:Section\s+\d+)"             # Section 3
    r")\s+",
    re.IGNORECASE,
)

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENTENCE_RE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])")


@dataclass
class TextChunk:
    """One retrievable unit produced by the chunker."""

    chunk_id: str
    parent_id: str
    text: str
    chunk_index: int
    section: str = ""

    @property
    def token_count(self) -> int:
        return len(tokenize(self.text))


def _split_structural(text: str) -> list[str]:
    """Split on numbered/lettered clause boundaries."""
    boundaries = [match.start() for match in _CLAUSE_START_RE.finditer(text)]
    if len(boundaries) < 2:
        return [text]
    pieces: list[str] = []
    for position, start in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(text)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
    return pieces


def _pack_sentences(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Fallback packer: greedily fill windows on sentence boundaries."""
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if not sentences:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence_len = len(sentence)
        if current and current_len + sentence_len > chunk_size:
            chunks.append(" ".join(current))
            # Carry a tail of the previous window forward to preserve context.
            carry: list[str] = []
            carry_len = 0
            for previous in reversed(current):
                if carry_len + len(previous) > overlap:
                    break
                carry.insert(0, previous)
                carry_len += len(previous)
            current = carry
            current_len = carry_len
        current.append(sentence)
        current_len += sentence_len + 1
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if c.strip()]


def chunk_document(
    text: str,
    *,
    parent_id: str,
    source: str = "",
    chunk_size: int = 1100,
    overlap: int = 150,
    min_chunk_chars: int = 120,
) -> list[TextChunk]:
    """Split one document into retrievable chunks.

    Structural clause boundaries are honoured first; oversized clauses are then
    packed on sentence boundaries, and undersized fragments are merged forward so no
    chunk is a dangling proviso.
    """
    text = (text or "").strip()
    if not text:
        return []

    # 1. Prefer structure: paragraphs, then numbered clauses inside each paragraph.
    units: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        units.extend(_split_structural(paragraph) if len(paragraph) > chunk_size else [paragraph])

    # 2. Enforce the size budget on each unit.
    sized: list[str] = []
    for unit in units:
        if len(unit) <= chunk_size:
            sized.append(unit)
        else:
            sized.extend(_pack_sentences(unit, chunk_size, overlap))

    # 3. Merge undersized fragments with their successor.
    merged: list[str] = []
    buffer = ""
    for unit in sized:
        candidate = f"{buffer}\n{unit}".strip() if buffer else unit
        if len(candidate) < min_chunk_chars and len(sized) > 1:
            buffer = candidate
            continue
        merged.append(candidate)
        buffer = ""
    if buffer:
        if merged:
            merged[-1] = f"{merged[-1]}\n{buffer}".strip()
        else:
            merged.append(buffer)

    chunks: list[TextChunk] = []
    for index, body in enumerate(merged):
        chunk_id = stable_id(parent_id, str(index), body[:64])
        section = ""
        heading = _CLAUSE_START_RE.match(body)
        if heading:
            section = body[:60].split("\n", 1)[0].strip()
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                parent_id=parent_id,
                text=body,
                chunk_index=index,
                section=section,
            )
        )
    return chunks
