"""Tests for legal-aware chunking and BM25 sparse encoding.

The properties under test are the ones that quietly break retrieval quality: a chunker
that severs a proviso from its clause, a tokenizer that stems terms of art into
nonsense, or a hashed vocabulary that is not stable across processes.
"""

from __future__ import annotations

import subprocess
import sys
from typing import ClassVar

import pytest

from sowsprint.rag.chunking import (
    HASH_SPACE,
    BM25Encoder,
    SparseVector,
    chunk_document,
    hash_index,
    hash_sign,
    tokenize,
)


class TestTokenisation:
    def test_lowercases_and_drops_stopwords(self) -> None:
        tokens = tokenize("The Supplier shall indemnify the Customer")
        assert "the" not in tokens
        assert "supplier" in tokens
        assert "indemnify" in tokens
        assert "customer" in tokens

    def test_preserves_legal_terms_of_art(self) -> None:
        """Aggressive stemming would destroy the vocabulary retrieval depends on."""
        tokens = tokenize("indemnification sub-processor force majeure")
        assert "indemnification" in tokens
        assert "sub-processor" in tokens
        assert "force" in tokens

    def test_light_plural_stemming(self) -> None:
        assert "obligation" in tokenize("obligations")
        assert "partie" not in tokenize("parties")  # -> "party"
        assert "party" in tokenize("parties")

    def test_section_numbers_survive(self) -> None:
        """Legal citations reference section numbers, so they must index — a lone
        '§' is a single-character symbol with no retrieval value and is dropped."""
        tokens = tokenize("See § 141(a) and Art. 28")
        assert "141" in tokens
        assert "28" in tokens
        assert "§" not in tokens

    def test_empty_input(self) -> None:
        assert tokenize("") == []


class TestHashedVocabulary:
    def test_indices_are_in_range(self) -> None:
        for token in ("gdpr", "delaware", "§", "sub-processor", "indemnification"):
            assert 0 <= hash_index(token) < HASH_SPACE

    def test_hashing_is_stable_across_processes(self) -> None:
        """Python's built-in hash() is salted per process and would break persistence."""
        code = (
            "import sys; sys.path.insert(0,'src');"
            "from sowsprint.rag.chunking import hash_index;"
            "print(hash_index('indemnification'), hash_index('gdpr'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        )
        assert result.returncode == 0, result.stderr
        expected = f"{hash_index('indemnification')} {hash_index('gdpr')}"
        assert result.stdout.strip() == expected

    def test_sign_is_deterministic(self) -> None:
        assert hash_sign("gdpr") == hash_sign("gdpr")
        assert hash_sign("gdpr") in (1.0, -1.0)


class TestSparseVector:
    def test_dot_product(self) -> None:
        first = SparseVector([1, 5, 9], [1.0, 2.0, 3.0])
        second = SparseVector([5, 9, 11], [1.0, 1.0, 1.0])
        assert first.dot(second) == pytest.approx(5.0)

    def test_dot_handles_disjoint_and_empty(self) -> None:
        assert SparseVector([1], [1.0]).dot(SparseVector([2], [1.0])) == 0.0
        assert SparseVector().dot(SparseVector([1], [1.0])) == 0.0

    def test_truthiness_reflects_content(self) -> None:
        assert not SparseVector()
        assert SparseVector([1], [0.5])

    def test_round_trips_through_dict(self) -> None:
        original = SparseVector([3, 7], [0.5, 1.5])
        assert SparseVector.from_dict(original.to_dict()) == original


class TestBM25Encoder:
    CORPUS: ClassVar[list[str]] = [
        "The processor shall notify the controller of a personal data breach.",
        "The board of directors manages the business and affairs of the corporation.",
        "Each party's aggregate liability is capped at fees paid in twelve months.",
        "The processor shall implement appropriate technical and organisational measures.",
    ]

    @pytest.fixture
    def encoder(self) -> BM25Encoder:
        encoder = BM25Encoder()
        encoder.fit(self.CORPUS)
        return encoder

    def test_fit_computes_corpus_statistics(self, encoder: BM25Encoder) -> None:
        assert encoder.is_fitted
        assert encoder.doc_count == len(self.CORPUS)
        assert encoder.avg_length > 0

    def test_rare_terms_get_higher_idf(self, encoder: BM25Encoder) -> None:
        """'breach' appears once; 'processor' twice, so it must be down-weighted."""
        rare = encoder.idf(hash_index("breach"))
        common = encoder.idf(hash_index("processor"))
        assert rare > common > 0

    def test_unseen_term_has_maximum_idf(self, encoder: BM25Encoder) -> None:
        assert encoder.idf(hash_index("zzzznotincorpus")) > encoder.idf(hash_index("processor"))

    def test_document_encoding_produces_positive_weights(self, encoder: BM25Encoder) -> None:
        vector = encoder.encode_document(self.CORPUS[0])
        assert vector.indices
        assert all(value > 0 for value in vector.values)
        assert len(vector.indices) == len(vector.values)

    def test_query_encoding_is_idf_weighted(self, encoder: BM25Encoder) -> None:
        vector = encoder.encode_query("personal data breach")
        assert vector.indices
        assert all(value > 0 for value in vector.values)

    def test_relevant_document_scores_highest(self, encoder: BM25Encoder) -> None:
        """The end-to-end property that matters: the right chunk wins."""
        query = encoder.encode_query("personal data breach notification")
        scores = [
            query.dot(encoder.encode_document(document)) for document in self.CORPUS
        ]
        assert scores.index(max(scores)) == 0

    def test_empty_inputs_are_safe(self, encoder: BM25Encoder) -> None:
        assert not encoder.encode_document("")
        assert not encoder.encode_query("")

    def test_persistence_round_trip(self, encoder: BM25Encoder, tmp_path) -> None:
        """Query encoding in a fresh process must reproduce identical weights."""
        path = tmp_path / "bm25.json"
        encoder.save(path)

        restored = BM25Encoder.load(path)
        assert restored is not None
        assert restored.doc_count == encoder.doc_count

        original = encoder.encode_query("personal data breach")
        reloaded = restored.encode_query("personal data breach")
        assert original.indices == reloaded.indices
        assert original.values == pytest.approx(reloaded.values)

    def test_load_missing_file_returns_none(self, tmp_path) -> None:
        assert BM25Encoder.load(tmp_path / "nope.json") is None

    def test_load_corrupt_file_returns_none(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert BM25Encoder.load(path) is None


class TestChunking:
    def test_short_document_stays_whole(self) -> None:
        chunks = chunk_document("A short clause about liability.", parent_id="doc-1")
        assert len(chunks) == 1
        assert chunks[0].text == "A short clause about liability."

    def test_empty_document_yields_nothing(self) -> None:
        assert chunk_document("", parent_id="doc-1") == []
        assert chunk_document("   \n  ", parent_id="doc-1") == []

    def test_respects_clause_boundaries(self) -> None:
        text = (
            "4.1 The Supplier shall deliver the Services in accordance with the SOW and "
            "shall maintain adequate records of all work performed under this Agreement.\n"
            "4.2 The Customer shall provide timely access to subject matter experts and "
            "shall nominate a product owner within five business days of the Effective Date.\n"
            "4.3 Either party may request a change in accordance with the change control "
            "procedure and no change is binding until both parties sign a variation order."
        )
        chunks = chunk_document(text, parent_id="doc-2", chunk_size=200, min_chunk_chars=50)
        assert len(chunks) >= 2
        # Each chunk should begin at a numbered clause boundary, not mid-sentence.
        for chunk in chunks:
            assert chunk.text.strip()[0].isdigit() or chunk.text.strip()[0] == "4"

    def test_oversized_text_is_split_within_budget(self) -> None:
        sentence = "The Supplier shall maintain comprehensive written records of all work. "
        text = sentence * 60
        chunks = chunk_document(text, parent_id="doc-3", chunk_size=500, overlap=60)
        assert len(chunks) > 1
        # Allow the merge step to combine a short tail, hence the modest tolerance.
        assert all(len(chunk.text) <= 900 for chunk in chunks)

    def test_chunk_ids_are_stable_and_unique(self) -> None:
        text = "Clause one is here.\n\nClause two is here.\n\nClause three is here."
        first = chunk_document(text, parent_id="doc-4", chunk_size=30, min_chunk_chars=10)
        second = chunk_document(text, parent_id="doc-4", chunk_size=30, min_chunk_chars=10)
        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
        assert len({c.chunk_id for c in first}) == len(first)

    def test_chunk_index_is_sequential(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four."
        chunks = chunk_document(text, parent_id="doc-5", chunk_size=12, min_chunk_chars=1)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_token_count_is_reported(self) -> None:
        chunks = chunk_document(
            "The processor shall notify the controller of a breach.", parent_id="doc-6"
        )
        assert chunks[0].token_count > 0

    def test_parent_id_is_propagated(self) -> None:
        chunks = chunk_document("Some clause text that is long enough.", parent_id="parent-xyz")
        assert all(chunk.parent_id == "parent-xyz" for chunk in chunks)

    def test_no_information_is_lost(self) -> None:
        """Every distinctive marker in the source must survive into some chunk."""
        markers = [f"MARKER{i}" for i in range(12)]
        text = "\n\n".join(
            f"Clause {index}. " + " ".join(markers) + " padding text to add length." for index in range(3)
        )
        chunks = chunk_document(text, parent_id="doc-7", chunk_size=200, min_chunk_chars=20)
        combined = " ".join(chunk.text for chunk in chunks)
        for marker in markers:
            assert marker in combined
