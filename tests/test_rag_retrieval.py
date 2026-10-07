"""Tests for the compliance-aware hybrid retrieval pipeline.

The load-bearing claim of this module is the engineering constraint from the brief:
**the jurisdiction filter is pushed down to the vector engine**, so a clause from the
wrong compliance regime can never enter the candidate pool. Several tests below exist
purely to falsify that claim if it ever regresses.
"""

from __future__ import annotations

import pytest

from sowsprint.config import Settings, VectorBackend
from sowsprint.models import Jurisdiction, RequirementScope, TechnicalBlueprint
from sowsprint.rag.fusion import reciprocal_rank_fusion
from sowsprint.rag.pipeline import RagPipeline
from sowsprint.rag.rerank import HeuristicReranker
from sowsprint.rag.schema import (
    build_jurisdiction_filter,
    matches_filter,
    point_id_for,
)
from sowsprint.rag.store import InMemoryStore, ScoredPoint, StorePoint, build_store


class TestMetadataFilterParity:
    """The Qdrant filter and the in-memory predicate must agree exactly."""

    def test_matches_filter_enforces_jurisdiction(self) -> None:
        payload = {"jurisdiction": "EU", "doc_type": "regulation", "chunk_id": "a"}
        assert matches_filter(payload, "EU")
        assert not matches_filter(payload, "US")
        assert matches_filter(payload, None)

    def test_matches_filter_honours_doc_types(self) -> None:
        payload = {"jurisdiction": "EU", "doc_type": "statute", "chunk_id": "a"}
        assert matches_filter(payload, "EU", doc_types=["statute"])
        assert not matches_filter(payload, "EU", doc_types=["playbook"])

    def test_matches_filter_honours_exclusions(self) -> None:
        payload = {"jurisdiction": "EU", "chunk_id": "a"}
        assert not matches_filter(payload, "EU", exclude_chunk_ids=["a"])
        assert matches_filter(payload, "EU", exclude_chunk_ids=["b"])

    def test_qdrant_filter_is_built_with_the_jurisdiction_condition(self) -> None:
        query_filter = build_jurisdiction_filter("EU")
        assert query_filter is not None
        rendered = str(query_filter)
        assert "jurisdiction" in rendered
        assert "EU" in rendered

    def test_qdrant_filter_returns_none_when_unconstrained(self) -> None:
        assert build_jurisdiction_filter(None) is None

    def test_qdrant_filter_includes_must_not_for_exclusions(self) -> None:
        query_filter = build_jurisdiction_filter("EU", exclude_chunk_ids=["x", "y"])
        assert query_filter is not None
        assert query_filter.must_not

    def test_point_ids_are_deterministic_uuids(self) -> None:
        import uuid

        first = point_id_for("eu-gdpr-breach-notification")
        assert first == point_id_for("eu-gdpr-breach-notification")
        uuid.UUID(first)  # raises if malformed
        assert first != point_id_for("us-ccpa-opt-out-rights")


class TestInMemoryStore:
    @pytest.fixture
    def store(self, settings: Settings) -> InMemoryStore:
        store = InMemoryStore(settings)
        store.ensure_collection(2)
        store.upsert(
            [
                StorePoint("eu-a", [1.0, 0.0], _sparse(0, 1.0), {"chunk_id": "eu-a", "jurisdiction": "EU", "doc_type": "regulation"}),
                StorePoint("eu-b", [0.9, 0.1], _sparse(1, 1.0), {"chunk_id": "eu-b", "jurisdiction": "EU", "doc_type": "statute"}),
                StorePoint("us-a", [0.0, 1.0], _sparse(1, 1.0), {"chunk_id": "us-a", "jurisdiction": "US", "doc_type": "statute"}),
            ]
        )
        return store

    def test_count(self, store: InMemoryStore) -> None:
        assert store.count() == 3

    def test_dense_search_is_ordered_by_similarity(self, store: InMemoryStore) -> None:
        hits = store.search_dense([1.0, 0.0], limit=3)
        assert [hit.chunk_id for hit in hits] == ["eu-a", "eu-b", "us-a"]

    def test_dense_search_applies_the_jurisdiction_filter(
        self, store: InMemoryStore
    ) -> None:
        hits = store.search_dense([1.0, 0.0], limit=10, jurisdiction="US")
        assert [hit.chunk_id for hit in hits] == ["us-a"]

    def test_sparse_search_applies_the_jurisdiction_filter(
        self, store: InMemoryStore
    ) -> None:
        hits = store.search_sparse(_sparse(1, 1.0), limit=10, jurisdiction="EU")
        assert {hit.chunk_id for hit in hits} == {"eu-b"}

    def test_sparse_search_ignores_zero_scores(self, store: InMemoryStore) -> None:
        assert store.search_sparse(_sparse(99, 1.0), limit=10) == []

    def test_empty_sparse_query_returns_nothing(self, store: InMemoryStore) -> None:
        assert store.search_sparse(_sparse(0, 0.0).__class__(), limit=10) == []

    def test_upsert_overwrites_by_chunk_id(self, store: InMemoryStore) -> None:
        store.upsert([StorePoint("eu-a", [0.0, 0.0], _sparse(0, 1.0), {"chunk_id": "eu-a", "jurisdiction": "EU"})])
        assert store.count() == 3

    def test_all_payloads(self, store: InMemoryStore) -> None:
        assert len(store.all_payloads()) == 3

    def test_healthy(self, store: InMemoryStore) -> None:
        assert store.healthy()


class TestFusion:
    @staticmethod
    def _hits(prefix: str, count: int, *, start_score: float = 1.0) -> list[ScoredPoint]:
        return [
            ScoredPoint(
                chunk_id=f"{prefix}-{index}",
                score=start_score - index * 0.1,
                payload={"chunk_id": f"{prefix}-{index}", "jurisdiction": "EU"},
            )
            for index in range(count)
        ]

    def test_rrf_is_scale_free(self) -> None:
        """BM25 scores are unbounded; RRF must not let them dominate cosine similarity."""
        dense = self._hits("d", 3, start_score=0.9)
        sparse = self._hits("s", 3, start_score=9_000.0)
        fused = reciprocal_rank_fusion(dense, sparse, k=60.0)
        # Both arms contribute equally at equal rank.
        assert fused[0].fused_score == pytest.approx(fused[1].fused_score)

    def test_documents_in_both_arms_outrank_single_arm_documents(self) -> None:
        dense = [
            ScoredPoint("shared", 0.9, {"chunk_id": "shared"}),
            ScoredPoint("dense-only", 0.8, {"chunk_id": "dense-only"}),
        ]
        sparse = [
            ScoredPoint("shared", 50.0, {"chunk_id": "shared"}),
            ScoredPoint("sparse-only", 40.0, {"chunk_id": "sparse-only"}),
        ]
        fused = reciprocal_rank_fusion(dense, sparse)
        assert fused[0].chunk_id == "shared"
        assert set(fused[0].contributed_arms) == {"dense", "sparse"}

    def test_ranks_are_recorded_per_arm(self) -> None:
        fused = reciprocal_rank_fusion(self._hits("d", 2), self._hits("s", 2))
        by_id = {hit.chunk_id: hit for hit in fused}
        assert by_id["d-0"].dense_rank == 1
        assert by_id["d-0"].sparse_rank is None
        assert by_id["s-1"].sparse_rank == 2

    def test_ordering_is_deterministic_on_ties(self) -> None:
        """Reproducible evaluation runs require a total order."""
        first = reciprocal_rank_fusion(self._hits("d", 3), self._hits("s", 3))
        second = reciprocal_rank_fusion(self._hits("d", 3), self._hits("s", 3))
        assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]

    def test_handles_one_empty_arm(self) -> None:
        fused = reciprocal_rank_fusion(self._hits("d", 3), [])
        assert len(fused) == 3
        assert all(hit.contributed_arms == ["dense"] for hit in fused)

    def test_weights_shift_influence(self) -> None:
        dense = [ScoredPoint("d", 1.0, {"chunk_id": "d"})]
        sparse = [ScoredPoint("s", 1.0, {"chunk_id": "s"})]
        fused = reciprocal_rank_fusion(dense, sparse, dense_weight=5.0, sparse_weight=0.1)
        assert fused[0].chunk_id == "d"


class TestReranker:
    def _candidates(self) -> list:
        from sowsprint.rag.fusion import FusedHit

        return [
            FusedHit(
                chunk_id="relevant",
                payload={
                    "title": "Personal data breach notification duty",
                    "text": "The processor shall notify the controller of a personal data breach within 48 hours.",
                    "tags": ["gdpr", "breach"],
                },
                fused_score=0.03,
            ),
            FusedHit(
                chunk_id="borderline",
                payload={
                    "title": "General record keeping",
                    "text": "Records shall be maintained for the duration of the agreement.",
                    "tags": ["records"],
                },
                fused_score=0.02,
            ),
            FusedHit(
                chunk_id="irrelevant",
                payload={
                    "title": "Delaware board authority",
                    "text": "The business and affairs of the corporation are managed by a board.",
                    "tags": ["delaware", "board"],
                },
                fused_score=0.01,
            ),
        ]

    def test_promotes_the_most_relevant_candidate(self) -> None:
        reranker = HeuristicReranker()
        ranked = reranker.rerank("personal data breach notification", self._candidates(), top_k=3)
        assert ranked[0].chunk_id == "relevant"

    def test_scores_are_bounded_and_ranks_sequential(self) -> None:
        ranked = HeuristicReranker().rerank("breach notification", self._candidates(), top_k=3)
        assert [item.rank for item in ranked] == [1, 2, 3]
        assert all(0.0 <= item.rerank_score <= 1.0 for item in ranked)

    def test_respects_top_k(self) -> None:
        assert len(HeuristicReranker().rerank("breach", self._candidates(), top_k=2)) == 2

    def test_empty_candidates(self) -> None:
        assert HeuristicReranker().rerank("anything", [], top_k=5) == []

    def test_empty_query_falls_back_to_fusion_order(self) -> None:
        ranked = HeuristicReranker().rerank("", self._candidates(), top_k=3)
        assert [item.chunk_id for item in ranked] == ["relevant", "borderline", "irrelevant"]

    def test_tag_and_title_overlap_influence_the_score(self) -> None:
        reranker = HeuristicReranker()
        ranked = reranker.rerank("gdpr breach", self._candidates(), top_k=3)
        by_id = {item.chunk_id: item.rerank_score for item in ranked}
        assert by_id["relevant"] > by_id["irrelevant"]


class TestComplianceRetriever:
    def test_returns_chunks_with_provenance(self, mini_pipeline: RagPipeline) -> None:
        result = mini_pipeline.retriever.retrieve(
            "personal data breach notification duty", jurisdiction="EU", final_k=3
        )
        assert result.chunks
        top = result.chunks[0]
        assert top.chunk_id
        assert top.text
        assert top.jurisdiction == "EU"
        assert top.rerank_score >= 0.0
        assert top.rank >= 1

    def test_jurisdiction_filter_excludes_the_other_regime(
        self, mini_pipeline: RagPipeline
    ) -> None:
        """The central engineering constraint — asserted from both directions."""
        eu = mini_pipeline.retriever.retrieve(
            "privacy opt-out and sale of personal information", jurisdiction="EU", final_k=4
        )
        us = mini_pipeline.retriever.retrieve(
            "privacy opt-out and sale of personal information", jurisdiction="US", final_k=4
        )
        assert eu.chunks and us.chunks
        assert all(chunk.jurisdiction == "EU" for chunk in eu.chunks)
        assert all(chunk.jurisdiction == "US" for chunk in us.chunks)
        assert not ({c.chunk_id for c in eu.chunks} & {c.chunk_id for c in us.chunks})

    def test_same_query_different_regime_returns_different_evidence(
        self, mini_pipeline: RagPipeline
    ) -> None:
        query = "consumer privacy obligations for personal information"
        eu = mini_pipeline.retriever.retrieve(query, jurisdiction="EU", final_k=2)
        us = mini_pipeline.retriever.retrieve(query, jurisdiction="US", final_k=2)
        assert eu.citation_ids() != us.citation_ids()

    def test_diagnostics_describe_the_pipeline(self, mini_pipeline: RagPipeline) -> None:
        result = mini_pipeline.retriever.retrieve("breach notification", jurisdiction="EU")
        diagnostics = result.diagnostics
        assert diagnostics is not None
        assert diagnostics.jurisdiction == "EU"
        assert diagnostics.dense_candidates > 0
        assert diagnostics.store_backend
        assert diagnostics.embedder
        assert diagnostics.reranker
        assert diagnostics.filter_applied is True
        payload = diagnostics.as_dict()
        assert payload["jurisdiction"] == "EU"
        assert "total_ms" in payload

    def test_exclusions_are_honoured(self, mini_pipeline: RagPipeline) -> None:
        first = mini_pipeline.retriever.retrieve("personal data", jurisdiction="EU", final_k=1)
        assert first.chunks
        excluded = first.chunks[0].chunk_id

        second = mini_pipeline.retriever.retrieve(
            "personal data", jurisdiction="EU", final_k=2, exclude_chunk_ids=[excluded]
        )
        assert excluded not in second.citation_ids()

    def test_render_evidence_uses_the_citation_contract(self, mini_pipeline: RagPipeline) -> None:
        """The Legal agent and the Critic agree on citation identity through this format."""
        result = mini_pipeline.retriever.retrieve("breach notification", jurisdiction="EU", final_k=2)
        block = result.render_evidence()
        assert "[1]" in block
        for chunk in result.chunks:
            assert f"id={chunk.chunk_id}" in block
            assert "jurisdiction=" in block

    def test_render_evidence_handles_no_results(self) -> None:
        from sowsprint.rag.retriever import RetrievalResult

        assert "no compliance evidence" in RetrievalResult().render_evidence().lower()

    def test_build_query_includes_scope_and_expansion(self, mini_pipeline: RagPipeline) -> None:
        scope = RequirementScope(
            title="Aurora",
            business_goal="Faster reporting",
            deliverables=["React dashboard"],
            compliance_flags=["GDPR"],
            jurisdiction=Jurisdiction.EU,
            status="COMPLETE",
        )
        query = mini_pipeline.retriever.build_query(scope, None, Jurisdiction.EU)
        assert "Aurora" in query
        assert "GDPR" in query

    def test_topic_queries_are_topic_dominant(self, mini_pipeline: RagPipeline) -> None:
        """A topic query must not be drowned out by the full scope text."""
        scope = RequirementScope(
            title="Aurora",
            business_goal="Faster reporting for the operations organisation",
            deliverables=["React dashboard", "FastAPI backend", "Salesforce integration"],
            jurisdiction=Jurisdiction.EU,
            status="COMPLETE",
        )
        topic_query = mini_pipeline.retriever.build_query(
            scope, None, Jurisdiction.EU, topic="limitation of liability and caps"
        )
        assert topic_query.lower().count("limitation of liability") == 2
        assert "operations organisation" not in topic_query

    def test_multi_topic_retrieval_returns_diverse_evidence(
        self, full_pipeline: RagPipeline, detailed_brief: str
    ) -> None:
        """Each clause topic must contribute new passages, not re-surface the same one."""
        from sowsprint.llm.offline import triage_offline

        scope = triage_offline(detailed_brief, None, "")
        result = full_pipeline.retriever.retrieve_multi_topic(scope, None, jurisdiction=Jurisdiction.EU)

        assert len(result.chunks) >= 8
        assert len({chunk.chunk_id for chunk in result.chunks}) == len(result.chunks)
        assert all(chunk.jurisdiction == "EU" for chunk in result.chunks)

    def test_is_indexed_and_health(self, mini_pipeline: RagPipeline) -> None:
        assert mini_pipeline.is_indexed
        health = mini_pipeline.health()
        assert health["ok"] is True
        assert health["chunks"] == 4
        assert health["bm25_fitted"] is True


class TestFullCorpus:
    def test_bundled_corpus_loads_and_is_balanced(self) -> None:
        from sowsprint.rag.ingest import load_corpus

        corpus = load_corpus()
        assert len(corpus) >= 48
        eu = [entry for entry in corpus if entry["jurisdiction"] == "EU"]
        us = [entry for entry in corpus if entry["jurisdiction"] == "US"]
        assert len(eu) >= 24
        assert len(us) >= 24

    def test_corpus_entries_are_well_formed(self) -> None:
        from sowsprint.rag.ingest import load_corpus

        for entry in load_corpus():
            assert entry["id"]
            assert entry["jurisdiction"] in ("EU", "US")
            assert entry["source"]
            assert entry["title"]
            assert len(entry["text"].split()) >= 100
            assert isinstance(entry["tags"], list) and entry["tags"]

    def test_ids_are_unique(self) -> None:
        from sowsprint.rag.ingest import load_corpus

        ids = [entry["id"] for entry in load_corpus()]
        assert len(ids) == len(set(ids))

    def test_full_corpus_is_indexed_and_balanced(self, full_pipeline: RagPipeline) -> None:
        stats = full_pipeline.stats()
        assert stats.indexed is True
        assert stats.indexed_chunks > 100
        assert stats.eu_chunks > 50
        assert stats.us_chunks > 50

    def test_both_regimes_return_evidence_for_the_same_query(
        self, full_pipeline: RagPipeline
    ) -> None:
        """Neither regime may be a second-class citizen in the corpus."""
        query = "governing law jurisdiction and dispute resolution"
        for jurisdiction in ("EU", "US"):
            result = full_pipeline.retriever.retrieve(
                query, jurisdiction=jurisdiction, final_k=4
            )
            assert result.chunks, jurisdiction
            assert all(chunk.jurisdiction == jurisdiction for chunk in result.chunks)


class TestPipelineFallback:
    def test_build_store_falls_back_to_memory_when_qdrant_is_unreachable(self) -> None:
        settings = Settings(
            vector_backend=VectorBackend.AUTO,
            qdrant_url="http://127.0.0.1:1",  # nothing listens here
        )
        store = build_store(settings)
        assert store.name == "memory"

    def test_ensure_indexed_is_idempotent(self, full_pipeline: RagPipeline) -> None:
        """A second call must reuse the existing index, not rebuild it."""
        first = full_pipeline.ensure_indexed()
        second = full_pipeline.ensure_indexed()
        assert second is first
        assert second.reused_existing or second.chunks > 0

    def test_pipeline_without_auto_ingest_reports_not_indexed(
        self, settings: Settings
    ) -> None:
        settings = settings.model_copy(update={"vector_backend": VectorBackend.MEMORY})
        pipeline = RagPipeline(settings, auto_ingest=False)
        assert not pipeline.is_indexed
        assert pipeline.retriever.ensure_indexed(auto_ingest=False) is False


class TestBlueprintIntegration:
    def test_blueprint_influences_the_query(self, mini_pipeline: RagPipeline) -> None:
        scope = RequirementScope(
            title="Aurora", business_goal="Analytics", jurisdiction=Jurisdiction.EU
        )
        blueprint = TechnicalBlueprint(
            solution_overview="A platform",
            tech_stack=[],
            milestones=[],
        )
        query = mini_pipeline.retriever.build_query(scope, blueprint, Jurisdiction.EU)
        assert "Aurora" in query


def _sparse(index: int, value: float):
    from sowsprint.rag.chunking import SparseVector

    return SparseVector([index], [value])
