"""Tests for configuration resolution, the domain models and the telemetry layer.

Provider resolution is exercised with fully explicit ``Settings(...)`` construction
(including ``foo_api_key=None``) so an ambient environment variable or ``.env`` entry can
never change the outcome of a resolution test.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

import pytest
from pydantic import ValidationError

from sowsprint.config import (
    EmbeddingProvider,
    LLMProvider,
    RerankProvider,
    Settings,
    VectorBackend,
)
from sowsprint.models import (
    AcceptanceCriterion,
    ClarificationQuestion,
    CostReport,
    CriticFinding,
    CritiqueReport,
    Epic,
    FindingCategory,
    Jurisdiction,
    Milestone,
    PaymentMilestone,
    RequirementScope,
    ScopeStatus,
    Severity,
    SOWClause,
    SOWDocument,
    TechChoice,
    TechnicalBlueprint,
    TokenUsage,
    UserStory,
)
from sowsprint.telemetry import (
    PRICE_BOOK,
    CostLedger,
    budget_bar,
    drop_ledger,
    estimate_tokens,
    get_ledger,
    lookup,
    one_liner,
    price_call,
    record_call,
    render_dashboard,
    render_plain,
    transcription_cost,
)


def make_settings(**overrides) -> Settings:
    """Credential-free settings; every key is cleared unless overridden."""
    base: dict = {
        "llm_provider": LLMProvider.AUTO,
        "embedding_provider": EmbeddingProvider.AUTO,
        "rerank_provider": RerankProvider.AUTO,
        "vector_backend": VectorBackend.MEMORY,
        "whisper_provider": "auto",
        "anthropic_api_key": None,
        "openai_api_key": None,
        "groq_api_key": None,
        "cohere_api_key": None,
        "tei_rerank_url": None,
        "jira_base_url": None,
        "jira_email": None,
        "jira_api_token": None,
        "notion_api_key": None,
        "notion_parent_page_id": None,
        "dry_run_integrations": True,
    }
    base.update(overrides)
    return Settings(**base)


# =================================================================================
# Settings: provider resolution
# =================================================================================


def test_auto_llm_provider_resolves_to_mock_without_keys():
    settings = make_settings()
    assert settings.has_openai is False
    assert settings.has_anthropic is False
    assert settings.has_groq is False
    assert settings.resolved_llm_provider is LLMProvider.MOCK


def test_auto_llm_provider_prefers_the_credential_that_is_present():
    assert make_settings(anthropic_api_key="sk-ant").resolved_llm_provider is LLMProvider.ANTHROPIC
    assert make_settings(openai_api_key="sk-openai").resolved_llm_provider is LLMProvider.OPENAI
    assert make_settings(groq_api_key="gsk").resolved_llm_provider is LLMProvider.GROQ
    # Documented precedence when several keys are configured.
    both = make_settings(anthropic_api_key="sk-ant", openai_api_key="sk-openai")
    assert both.resolved_llm_provider is LLMProvider.ANTHROPIC


def test_explicit_llm_provider_is_never_overridden_by_credentials():
    forced = make_settings(llm_provider=LLMProvider.MOCK, openai_api_key="sk-openai")
    assert forced.resolved_llm_provider is LLMProvider.MOCK
    assert forced.offline_mode is True

    forced_cloud = make_settings(llm_provider=LLMProvider.OPENAI)
    assert forced_cloud.resolved_llm_provider is LLMProvider.OPENAI


def test_auto_embedding_provider_resolves_to_hash_without_keys():
    assert make_settings().resolved_embedding_provider is EmbeddingProvider.HASH
    assert make_settings(openai_api_key="sk-openai").resolved_embedding_provider is (
        EmbeddingProvider.OPENAI
    )
    # An explicit choice wins over an available key.
    explicit = make_settings(embedding_provider=EmbeddingProvider.HASH, openai_api_key="sk-openai")
    assert explicit.resolved_embedding_provider is EmbeddingProvider.HASH


def test_auto_rerank_provider_resolution():
    assert make_settings().resolved_rerank_provider is RerankProvider.HEURISTIC
    assert make_settings(tei_rerank_url="http://reranker:80/rerank").resolved_rerank_provider is (
        RerankProvider.TEI
    )
    assert make_settings(cohere_api_key="co-key").resolved_rerank_provider is RerankProvider.COHERE
    # A self-hosted endpoint outranks the hosted Cohere reranker.
    both = make_settings(tei_rerank_url="http://reranker:80/rerank", cohere_api_key="co-key")
    assert both.resolved_rerank_provider is RerankProvider.TEI

    explicit = make_settings(rerank_provider=RerankProvider.COHERE, tei_rerank_url="http://r:80")
    assert explicit.resolved_rerank_provider is RerankProvider.COHERE


def test_auto_whisper_provider_resolution():
    assert make_settings().resolved_whisper_provider == "offline"
    assert make_settings(openai_api_key="sk-openai").resolved_whisper_provider == "openai"
    assert make_settings(groq_api_key="gsk").resolved_whisper_provider == "groq"
    # Groq is the lowest-latency option and therefore preferred.
    both = make_settings(groq_api_key="gsk", openai_api_key="sk-openai")
    assert both.resolved_whisper_provider == "groq"

    forced = make_settings(whisper_provider="openai", groq_api_key="gsk")
    assert forced.resolved_whisper_provider == "openai"
    forced_offline = make_settings(whisper_provider="offline", openai_api_key="sk-openai")
    assert forced_offline.resolved_whisper_provider == "offline"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (LLMProvider.MOCK, True),
        (LLMProvider.OPENAI, False),
        (LLMProvider.ANTHROPIC, False),
        (LLMProvider.GROQ, False),
        (LLMProvider.OLLAMA, False),
    ],
)
def test_offline_mode_is_true_only_for_mock(provider: LLMProvider, expected: bool):
    assert make_settings(llm_provider=provider).offline_mode is expected


def test_offline_mode_is_false_for_auto_once_any_cloud_key_exists():
    assert make_settings().offline_mode is True
    assert make_settings(openai_api_key="sk-openai").offline_mode is False
    assert make_settings(anthropic_api_key="sk-ant").offline_mode is False
    assert make_settings(groq_api_key="gsk").offline_mode is False


def test_capability_matrix_reports_every_subsystem():
    matrix = make_settings().capability_matrix()

    assert set(matrix) == {
        "reasoning",
        "critic",
        "embeddings",
        "reranker",
        "vector_store",
        "transcription",
        "auth",
        "jira",
        "notion",
    }
    assert matrix == {
        "reasoning": "mock",
        # No critic_provider configured, so the Critic shares the reasoning provider
        # and no independence suffix is shown.
        "critic": "mock",
        "embeddings": "hash",
        "reranker": "heuristic",
        "vector_store": "memory",
        "transcription": "offline",
        # Authentication is on by default, and the matrix must say so: an operator
        # reading the dashboard should never have to guess whether the deployment is
        # open.
        "auth": "enabled",
        # No credentials and dry_run_integrations=True, so the connectors simulate.
        # The suffix matters: it separates "deliberately held in dry-run" from
        # "dry-run because nothing is configured" — a safety setting versus an
        # incomplete deployment.
        "jira": "dry-run (unconfigured)",
        "notion": "dry-run (unconfigured)",
    }


def test_critic_can_run_on_a_different_provider_than_the_drafter():
    """Judge independence is a configuration guarantee, not a documentation claim."""
    from sowsprint.config import LLMProvider

    settings = make_settings(
        anthropic_api_key="sk-ant",
        openai_api_key="sk-oai",
        reasoning_model="claude-haiku-5-5",
        critic_model="gpt-4o-mini",
        critic_provider=LLMProvider.OPENAI,
    )
    assert settings.resolved_llm_provider is LLMProvider.ANTHROPIC
    assert settings.resolved_critic_provider is LLMProvider.OPENAI
    assert settings.judge_is_independent is True
    assert settings.capability_matrix()["critic"] == "openai (independent judge)"


def test_critic_inherits_the_drafter_provider_by_default():

    settings = make_settings(anthropic_api_key="sk-ant", anthropic_base_url=None)
    assert settings.resolved_critic_provider is settings.resolved_llm_provider
    assert settings.judge_is_independent is False
    assert settings.capability_matrix()["critic"] == "anthropic"


def test_critic_and_fast_models_inherit_the_reasoning_model_when_unset():
    """One provider should need one model id, not three."""
    from sowsprint.llm.anthropic_client import AnthropicClient

    settings = make_settings(
        anthropic_api_key="sk-ant",
        reasoning_model="claude-haiku-5-5",
        critic_model=None,
        fast_model=None,
    )
    client = AnthropicClient("tier-inheritance", settings)
    assert client.default_model("reasoning") == "claude-haiku-5-5"
    assert client.default_model("critic") == "claude-haiku-5-5"
    assert client.default_model("fast") == "claude-haiku-5-5"


def test_foreign_model_id_falls_back_instead_of_failing_at_the_api():
    """A cross-vendor model id must not reach the wrong API as an opaque 404."""
    from sowsprint.llm.anthropic_client import AnthropicClient

    settings = make_settings(
        anthropic_api_key="sk-ant",
        reasoning_model="gpt-4o",
        critic_model="gpt-4o-mini",
    )
    client = AnthropicClient("tier-guard", settings)
    assert client.default_model("reasoning").startswith("claude-")
    assert client.default_model("critic").startswith("claude-")
    assert client.default_model("fast").startswith("claude-")


def test_capability_matrix_agrees_with_connector_mode():
    """The UI must never claim 'live' while the connector is simulating, or vice versa."""
    from sowsprint.tools.jira import JiraConnector
    from sowsprint.tools.notion import NotionConnector

    for dry_run in (True, False):
        for credentials in (True, False):
            settings = make_settings(
                dry_run_integrations=dry_run,
                jira_base_url="https://example.atlassian.net" if credentials else None,
                jira_email="ops@example.com" if credentials else None,
                jira_api_token="token" if credentials else None,
                notion_api_key="secret" if credentials else None,
                notion_parent_page_id="page-id" if credentials else None,
            )
            matrix = settings.capability_matrix()
            context = f"dry_run={dry_run} credentials={credentials}"

            assert (matrix["jira"] == "live") is (not JiraConnector(settings).dry_run), context
            assert (matrix["notion"] == "live") is (not NotionConnector(settings).dry_run), context


def test_capability_matrix_switches_to_cloud_adapters():
    matrix = make_settings(
        openai_api_key="sk-openai",
        cohere_api_key="co-key",
        vector_backend=VectorBackend.QDRANT,
    ).capability_matrix()

    assert matrix["reasoning"] == "openai"
    assert matrix["embeddings"] == "openai"
    assert matrix["reranker"] == "cohere"
    assert matrix["vector_store"] == "qdrant"
    assert matrix["transcription"] == "openai"


@pytest.mark.parametrize("port", [0, -1, 70000, 65536])
def test_port_validation_rejects_out_of_range_values(port: int):
    with pytest.raises(ValidationError) as excinfo:
        Settings(port=port)
    assert "port must be within 1..65535" in str(excinfo.value)


@pytest.mark.parametrize("port", [1, 8000, 65535])
def test_port_validation_accepts_in_range_values(port: int):
    assert Settings(port=port).port == port


# =================================================================================
# RequirementScope
# =================================================================================


def make_question(index: int) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=f"question_{index}",
        question=f"Clarifying question number {index}?",
        why_blocking="The contract cannot be drafted without it.",
        variable=f"variable_{index}",
    )


def test_clarifying_questions_are_truncated_to_three():
    scope = RequirementScope(
        title="Aurora",
        business_goal="Ship the portal.",
        clarifying_questions=[make_question(index) for index in range(1, 6)],
    )

    assert len(scope.clarifying_questions) == 3
    assert scope.question_texts() == [
        "Clarifying question number 1?",
        "Clarifying question number 2?",
        "Clarifying question number 3?",
    ]
    assert [q.id for q in scope.clarifying_questions] == ["question_1", "question_2", "question_3"]


def test_question_texts_is_empty_when_nothing_is_blocking():
    scope = RequirementScope(title="Aurora", business_goal="Ship the portal.")
    assert scope.question_texts() == []


def test_is_actionable_requires_complete_status_and_deliverables():
    incomplete = RequirementScope(
        title="Aurora",
        business_goal="Ship the portal.",
        deliverables=["Portal"],
        status=ScopeStatus.INCOMPLETE,
    )
    assert incomplete.is_actionable is False

    no_deliverables = RequirementScope(
        title="Aurora",
        business_goal="Ship the portal.",
        deliverables=[],
        status=ScopeStatus.COMPLETE,
    )
    assert no_deliverables.is_actionable is False

    actionable = RequirementScope(
        title="Aurora",
        business_goal="Ship the portal.",
        deliverables=["Portal"],
        status=ScopeStatus.COMPLETE,
    )
    assert actionable.is_actionable is True


def test_short_summary_mentions_deliverables_and_timeline():
    scope = RequirementScope(
        title="Aurora",
        business_goal="Ship the portal.",
        deliverables=["Portal", "Reports"],
        timeline_weeks=16,
        jurisdiction=Jurisdiction.US,
    )
    summary = scope.short_summary()
    assert summary.startswith("Aurora")
    assert "2 deliverables" in summary
    assert "16w" in summary
    assert "US" in summary


# =================================================================================
# TechnicalBlueprint
# =================================================================================


def make_story(key: str, title: str, points: int = 3) -> UserStory:
    return UserStory(
        key=key,
        title=title,
        as_a="product owner",
        i_want="a working increment",
        so_that="value can be validated",
        story_points=points,
        acceptance_criteria=[
            AcceptanceCriterion(given="a deployment", when="the scenario runs", then="it passes")
        ],
    )


def make_blueprint() -> TechnicalBlueprint:
    return TechnicalBlueprint(
        solution_overview="Two milestones delivered sequentially.",
        tech_stack=[TechChoice(layer="backend", technology="FastAPI", rationale="Async I/O")],
        milestones=[
            Milestone(
                name="Discovery",
                objective="Lock scope.",
                duration_weeks=3,
                epics=[
                    Epic(
                        key="DISC-1",
                        name="Foundations",
                        objective="Stand up the spine.",
                        stories=[make_story("DISC-1-1", "First", 3), make_story("DISC-1-2", "Second", 5)],
                    )
                ],
            ),
            Milestone(
                name="Build",
                objective="Deliver the product.",
                duration_weeks=5,
                epics=[
                    Epic(
                        key="BUILD-1",
                        name="Core",
                        objective="Build the core.",
                        stories=[make_story("BUILD-1-1", "Third", 8)],
                    ),
                    Epic(
                        key="BUILD-2",
                        name="Reporting",
                        objective="Build reporting.",
                        stories=[make_story("BUILD-2-1", "Fourth", 2)],
                    ),
                ],
            ),
        ],
        # Deliberately stale values that recompute_totals() must overwrite.
        estimated_total_points=999,
        estimated_duration_weeks=99,
    )


def test_recompute_totals_sums_story_points_and_durations():
    blueprint = make_blueprint()

    assert blueprint.recompute_totals() is None
    assert blueprint.estimated_total_points == 3 + 5 + 8 + 2
    assert blueprint.estimated_duration_weeks == 3 + 5
    # Per-milestone roll-ups agree with the blueprint total.
    assert [milestone.total_points for milestone in blueprint.milestones] == [8, 10]
    assert blueprint.milestones[0].story_count == 2
    assert blueprint.milestones[1].epics[0].total_points == 8


def test_recompute_totals_on_an_empty_blueprint_is_zero():
    blueprint = TechnicalBlueprint(solution_overview="Nothing yet.")
    blueprint.recompute_totals()
    assert blueprint.estimated_total_points == 0
    assert blueprint.estimated_duration_weeks == 0


def test_all_stories_flattens_in_milestone_then_epic_order():
    blueprint = make_blueprint()
    keys = [story.key for story in blueprint.all_stories()]
    assert keys == ["DISC-1-1", "DISC-1-2", "BUILD-1-1", "BUILD-2-1"]


# =================================================================================
# SOWDocument
# =================================================================================


def make_sow() -> SOWDocument:
    return SOWDocument(
        title="Statement of Work — Aurora",
        client_name="Northwind",
        vendor_name="SOWSprint Delivery Team",
        jurisdiction=Jurisdiction.EU,
        governing_law="the laws of Ireland",
        clauses=[
            SOWClause(number="1", heading="Definitions", body="Alpha beta gamma."),
            SOWClause(number="2", heading="Scope of Services", body="Delta epsilon."),
            SOWClause(number="6", heading="Fees and Payment", body="Zeta eta theta iota."),
        ],
        payment_schedule=[
            PaymentMilestone(name="Payment 1", trigger="Acceptance", percentage=40.0),
            PaymentMilestone(name="Payment 2", trigger="Launch", percentage=60.0),
        ],
    )


def test_sow_word_count_counts_clause_bodies_only():
    sow = make_sow()

    # 3 + 2 + 4 body words; headings and title are excluded.
    assert sow.word_count() == 9
    assert sow.word_count() != len(" ".join(c.heading for c in sow.clauses).split())

    sow.clauses.append(SOWClause(number="7", heading="Extra", body="one"))
    assert sow.word_count() == 10

    assert SOWDocument(
        title="Empty", client_name="C", vendor_name="V"
    ).word_count() == 0


def test_sow_clause_index_keys_by_clause_number():
    sow = make_sow()
    index = sow.clause_index()

    assert set(index) == {"1", "2", "6"}
    assert index["6"] is sow.clauses[2]
    assert index["6"].heading == "Fees and Payment"
    assert index["1"].body == "Alpha beta gamma."


# =================================================================================
# CritiqueReport
# =================================================================================


def test_blocking_findings_returns_only_high_and_critical():
    findings = [
        CriticFinding(
            category=FindingCategory.AMBIGUITY,
            severity=severity,
            location="1",
            description=f"{severity.value} finding",
            remediation="Fix it.",
        )
        for severity in (Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
    ]
    critique = CritiqueReport(passed=False, quality_score=0.4, findings=findings)

    blocking = critique.blocking_findings
    assert [finding.severity for finding in blocking] == [Severity.HIGH, Severity.CRITICAL]
    assert all(finding.is_blocking for finding in blocking)
    assert len(critique.findings) == 5

    assert CritiqueReport(passed=True, quality_score=1.0).blocking_findings == []


# =================================================================================
# CostReport
# =================================================================================


def make_report(**overrides) -> CostReport:
    base: dict = {
        "session_id": "session-1",
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "budget_usd": 2.50,
        "latency_ms_total": 0.0,
    }
    base.update(overrides)
    return CostReport(**base)


def test_budget_used_pct_clamps_at_100():
    assert make_report(cost_usd=1.25, budget_usd=2.50).budget_used_pct == pytest.approx(50.0)
    assert make_report(cost_usd=2.50, budget_usd=2.50).budget_used_pct == pytest.approx(100.0)
    assert make_report(cost_usd=99.0, budget_usd=2.50).budget_used_pct == 100.0
    # A zero budget is reported as no usage rather than dividing by zero.
    assert make_report(cost_usd=1.0, budget_usd=0.0).budget_used_pct == 0.0


def test_budget_remaining_is_never_negative():
    assert make_report(cost_usd=1.00, budget_usd=2.50).budget_remaining_usd == pytest.approx(1.50)
    assert make_report(cost_usd=2.50, budget_usd=2.50).budget_remaining_usd == 0.0
    assert make_report(cost_usd=9.99, budget_usd=2.50).budget_remaining_usd == 0.0


def test_avg_latency_handles_zero_calls():
    assert make_report(calls=0, latency_ms_total=1234.0).avg_latency_ms == 0.0
    assert make_report(calls=4, latency_ms_total=1000.0).avg_latency_ms == pytest.approx(250.0)


# =================================================================================
# pricing
# =================================================================================


def test_lookup_resolves_versioned_snapshots_to_their_family_entry():
    entry = lookup("gpt-4o-2024-11-20")

    assert entry is PRICE_BOOK["gpt-4o"]
    assert entry.input_per_mtok == pytest.approx(2.50)
    assert entry.output_per_mtok == pytest.approx(10.00)
    assert entry.provider == "openai"

    # The longer, more specific key still wins over its own prefix.
    assert lookup("gpt-4o-mini-2024-07-18") is PRICE_BOOK["gpt-4o-mini"]
    assert lookup("claude-3-5-sonnet-20241022") is PRICE_BOOK["claude-3-5-sonnet"]


def test_lookup_is_case_and_whitespace_insensitive():
    assert lookup("GPT-4O") is PRICE_BOOK["gpt-4o"]
    assert lookup("  Claude-3-5-Sonnet  ") is PRICE_BOOK["claude-3-5-sonnet"]


def test_lookup_falls_back_to_a_zero_cost_entry_for_unknown_models():
    for model in ("not-a-real-model-xyz", "", "   "):
        entry = lookup(model)
        assert entry.provider == "unknown"
        assert entry.input_per_mtok == 0.0
        assert entry.output_per_mtok == 0.0
        assert entry.cost(1_000_000, 1_000_000) == 0.0


def test_model_price_applies_the_cached_input_discount():
    entry = PRICE_BOOK["gpt-4o"]

    assert entry.cost(1_000_000, 0) == pytest.approx(2.50)
    assert entry.cost(0, 1_000_000) == pytest.approx(10.00)
    # A fully cached prompt is billed at the cached rate only.
    assert entry.cost(1_000_000, 0, 1_000_000) == pytest.approx(1.25)
    assert entry.cost(1_000_000, 1_000_000, 500_000) == pytest.approx(1.25 + 0.625 + 10.00)


def test_transcription_cost_scales_linearly_with_duration():
    assert transcription_cost("whisper-1", 60.0) == pytest.approx(0.006)
    assert transcription_cost("whisper-1", 30.0) == pytest.approx(0.003)
    assert transcription_cost("whisper-1", 120.0) == pytest.approx(
        2 * transcription_cost("whisper-1", 60.0)
    )
    assert transcription_cost("whisper-large-v3-turbo", 600.0) == pytest.approx(0.007)

    assert transcription_cost("whisper-1", 0.0) == 0.0
    assert transcription_cost("whisper-1", -15.0) == 0.0
    assert transcription_cost("unknown-model", 60.0) == 0.0


# =================================================================================
# telemetry rendering
# =================================================================================


def test_budget_bar_respects_width_and_fills_at_full_usage():
    assert len(budget_bar(make_report(), width=7)) == 7
    assert len(budget_bar(make_report())) == 12
    assert budget_bar(make_report(), width=7) == "░" * 7
    assert budget_bar(make_report(cost_usd=1.25, budget_usd=2.50), width=10) == "█" * 5 + "░" * 5
    assert budget_bar(make_report(cost_usd=2.50, budget_usd=2.50), width=10) == "█" * 10
    assert budget_bar(make_report(cost_usd=99.0, budget_usd=2.50), width=10) == "█" * 10


def test_one_liner_is_a_compact_non_empty_summary():
    line = one_liner(make_report(cost_usd=0.42, total_tokens=4500, calls=2))

    assert line
    assert line == "💸 $0.4200 / $2.50 · 4,500 tok · 2 calls"
    assert one_liner(make_report()) == "💸 $0.0000 / $2.50 · 0 tok · 0 calls"


def test_render_dashboard_contains_session_metrics():
    report = make_report(
        cost_usd=1.05,
        total_tokens=12000,
        prompt_tokens=9000,
        completion_tokens=3000,
        calls=3,
        latency_ms_total=4500.0,
        by_node={"triage": {"calls": 1.0, "tokens": 4000.0, "cost_usd": 0.80}},
        by_model={"gpt-4o": {"calls": 1.0, "tokens": 4000.0, "cost_usd": 0.80}},
    )

    dashboard = render_dashboard(report)

    assert dashboard
    assert "Session compute cost" in dashboard
    assert "**42.0%** of $2.50 budget" in dashboard
    assert "| Total cost | **$1.0500** |" in dashboard
    assert "| Remaining | $1.4500 |" in dashboard
    assert "| Total tokens | **12,000** |" in dashboard
    assert "| LLM calls | 3 |" in dashboard
    assert "| Avg latency | 1,500 ms |" in dashboard
    assert "**Cost by agent**" in dashboard
    assert "| `triage` | 1 | 4,000 | $0.8000 |" in dashboard
    assert "**Cost by model**" in dashboard
    assert "Budget warning" not in dashboard
    assert "Budget exhausted" not in dashboard


def test_render_dashboard_escalates_budget_warnings_at_the_documented_thresholds():
    # 85% consumed: warn, but do not claim the run has halted.
    warning = render_dashboard(make_report(cost_usd=2.125, budget_usd=2.50))
    assert "Budget warning" in warning
    assert "Budget exhausted" not in warning

    # >= 100% consumed: the hard stop message.
    exhausted = render_dashboard(make_report(cost_usd=5.0, budget_usd=2.50))
    assert "Budget exhausted" in exhausted
    assert "Budget warning" not in exhausted

    quiet = render_dashboard(make_report(cost_usd=0.10))
    assert "Budget warning" not in quiet
    assert "Budget exhausted" not in quiet


def test_render_dashboard_respects_top_n():
    report = make_report(
        cost_usd=1.0,
        by_node={
            "triage": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.50},
            "architect": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.30},
            "legal": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.20},
        },
        by_model={
            "gpt-4o": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.50},
            "gpt-4o-mini": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.30},
            "claude-3-5-sonnet": {"calls": 1.0, "tokens": 100.0, "cost_usd": 0.20},
        },
    )

    dashboard = render_dashboard(report, top_n=2)
    assert "`triage`" in dashboard
    assert "`architect`" in dashboard
    assert "`legal`" not in dashboard
    assert "`gpt-4o`" in dashboard
    assert "`gpt-4o-mini`" in dashboard
    assert "`claude-3-5-sonnet`" not in dashboard


def test_render_plain_is_a_non_empty_terminal_report():
    plain = render_plain(make_report(cost_usd=0.5, calls=2))

    assert plain
    assert "SOWSprint session session-1" in plain
    assert "cost (USD)       : $0.5000" in plain
    assert "budget (USD)     : 2.50 (20.0% used)" in plain


# =================================================================================
# pricing + ledger integration
# =================================================================================


def test_estimate_tokens_is_conservative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_price_call_uses_a_shadow_price_for_offline_engines():
    cost, simulated = price_call("offline-heuristic", 1_000_000, 0)
    assert simulated is True
    assert cost == pytest.approx(2.50)

    real_cost, simulated_real = price_call("offline-heuristic", 1_000_000, 0, simulate_offline=False)
    assert simulated_real is False
    assert real_cost == 0.0

    cloud_cost, cloud_simulated = price_call("gpt-4o", 1_000_000, 0)
    assert cloud_simulated is False
    assert cloud_cost == pytest.approx(2.50)


@pytest.fixture
def telemetry_session_id() -> str:
    """A private ledger id, released again once the test finishes."""
    identifier = f"pytest-telemetry-{uuid.uuid4().hex}"
    yield identifier
    drop_ledger(identifier)


def test_record_call_prices_and_persists_to_the_session_ledger(telemetry_session_id: str):
    usage = record_call(
        session_id=telemetry_session_id,
        model="gpt-4o",
        provider="openai",
        node="triage",
        prompt_tokens=1000,
        completion_tokens=0,
    )

    assert usage.cost_usd == pytest.approx(0.0025)
    assert usage.total_tokens == 1000

    ledger = get_ledger(telemetry_session_id)
    assert get_ledger(telemetry_session_id) is ledger
    assert ledger.total_cost_usd == pytest.approx(0.0025)
    assert ledger.report().calls == 1

    drop_ledger(telemetry_session_id)
    # A released session gets a brand new ledger rather than the stale one.
    assert get_ledger(telemetry_session_id) is not ledger


def test_cost_ledger_accumulates_totals_and_buckets():
    ledger = CostLedger("ledger-test", budget_usd=1.0)
    assert ledger.total_cost_usd == 0.0
    assert ledger.entries == []

    first = TokenUsage(
        model="gpt-4o",
        provider="openai",
        node="triage",
        prompt_tokens=1000,
        completion_tokens=500,
        latency_ms=250.0,
        cost_usd=0.02,
    )
    second = TokenUsage(
        model="gpt-4o-mini",
        provider="openai",
        node="architect",
        prompt_tokens=2000,
        completion_tokens=1000,
        latency_ms=750.0,
        cost_usd=0.03,
    )

    assert ledger.record(first) is first
    ledger.record(second)

    report = ledger.report()
    assert report.session_id == "ledger-test"
    assert report.calls == 2
    assert report.prompt_tokens == 3000
    assert report.completion_tokens == 1500
    assert report.total_tokens == 4500
    assert report.cost_usd == pytest.approx(0.05)
    assert report.budget_usd == 1.0
    assert report.latency_ms_total == pytest.approx(1000.0)
    assert report.avg_latency_ms == pytest.approx(500.0)
    assert report.last_call_at is not None
    assert report.by_node == {
        "triage": {"calls": 1.0, "tokens": 1500.0, "cost_usd": 0.02},
        "architect": {"calls": 1.0, "tokens": 3000.0, "cost_usd": 0.03},
    }
    assert report.by_model == {
        "gpt-4o": {"calls": 1.0, "tokens": 1500.0, "cost_usd": 0.02},
        "gpt-4o-mini": {"calls": 1.0, "tokens": 3000.0, "cost_usd": 0.03},
    }
    assert len(ledger.entries) == 2


def test_cost_ledger_is_over_budget_flips_at_the_configured_ceiling():
    ledger = CostLedger("ledger-budget", budget_usd=1.00)
    assert ledger.is_over_budget is False

    ledger.record(TokenUsage(model="gpt-4o", node="legal", cost_usd=0.60))
    assert ledger.is_over_budget is False

    ledger.record(TokenUsage(model="gpt-4o", node="legal", cost_usd=0.40))
    assert ledger.is_over_budget is True

    ledger.record(TokenUsage(model="gpt-4o", node="legal", cost_usd=0.01))
    assert ledger.is_over_budget is True
    assert ledger.report().budget_used_pct == 100.0


def test_cost_ledger_records_transcription_spend_by_minute():
    ledger = CostLedger("ledger-voice", budget_usd=1.0)

    usage = ledger.record_transcription("whisper-1", 120.0, node="voice")

    assert usage.provider == "whisper"
    assert usage.node == "voice"
    assert usage.prompt_tokens == 0
    assert usage.cost_usd == pytest.approx(0.012)
    assert ledger.report().by_model["whisper-1"]["cost_usd"] == pytest.approx(0.012)


def test_cost_ledger_reset_clears_everything():
    ledger = CostLedger("ledger-reset", budget_usd=0.01)
    ledger.record(
        TokenUsage(model="gpt-4o", node="triage", prompt_tokens=10, completion_tokens=5, cost_usd=0.5)
    )
    assert ledger.is_over_budget is True

    ledger.reset()

    assert ledger.entries == []
    assert ledger.total_cost_usd == 0.0
    assert ledger.is_over_budget is False
    report = ledger.report()
    assert report.calls == 0
    assert report.total_tokens == 0
    assert report.cost_usd == 0.0
    assert report.latency_ms_total == 0.0
    assert report.by_node == {}
    assert report.by_model == {}
    assert report.last_call_at is None
    # The ledger stays usable after a reset.
    ledger.record(TokenUsage(model="gpt-4o", node="triage", cost_usd=0.001))
    assert ledger.report().calls == 1


def test_capability_matrix_admits_when_a_cloud_provider_has_no_key():
    """A dashboard that reports a model which is not in use is worse than none."""
    from sowsprint.config import LLMProvider

    # Provider pinned explicitly, but no credential supplied: build_llm_client
    # degrades to the offline engine, so the matrix must say so.
    settings = make_settings(
        llm_provider=LLMProvider.ANTHROPIC,
        anthropic_api_key=None,
        critic_provider=LLMProvider.OPENAI,
        openai_api_key=None,
    )
    matrix = settings.capability_matrix()

    assert matrix["reasoning"] == "anthropic (no key - offline fallback)"
    assert "no key" in matrix["critic"]
    assert settings.provider_is_usable(LLMProvider.ANTHROPIC) is False
    assert settings.provider_is_usable(LLMProvider.MOCK) is True
    # Self-hosted has no credential to check, so it is never flagged.
    assert settings.provider_is_usable(LLMProvider.OLLAMA) is True


def test_capability_matrix_drops_the_caveat_once_the_key_is_present():
    from sowsprint.config import LLMProvider

    settings = make_settings(
        llm_provider=LLMProvider.ANTHROPIC,
        anthropic_api_key="sk-ant-real",
        critic_provider=LLMProvider.OPENAI,
        openai_api_key="sk-oai-real",
    )
    matrix = settings.capability_matrix()
    assert matrix["reasoning"] == "anthropic"
    assert matrix["critic"] == "openai (independent judge)"
    assert "no key" not in matrix["critic"]


def test_partial_configuration_is_reported_per_tier():
    """Supplying only the drafting key must not make the Critic look healthy."""
    from sowsprint.config import LLMProvider

    settings = make_settings(
        llm_provider=LLMProvider.ANTHROPIC,
        anthropic_api_key="sk-ant-real",
        critic_provider=LLMProvider.OPENAI,
        openai_api_key=None,
    )
    matrix = settings.capability_matrix()
    assert matrix["reasoning"] == "anthropic"
    assert "no key" in matrix["critic"]


class TestAnthropicParameterCompatibility:
    """Newer Claude releases reject `temperature`; the adapter must adapt.

    Verified against the live API: `claude-haiku-5-5` returns
    ``400 invalid_request_error: `temperature` is deprecated for this model``.
    Sending it unconditionally broke every Anthropic call in the graph.
    """

    def test_known_modern_models_reject_temperature(self) -> None:
        from sowsprint.llm.anthropic_client import rejects_temperature

        assert rejects_temperature("claude-haiku-5-5") is True
        assert rejects_temperature("claude-sonnet-5-5") is True
        assert rejects_temperature("claude-opus-5-5") is True

    def test_dated_snapshots_are_matched_by_prefix(self) -> None:
        from sowsprint.llm.anthropic_client import rejects_temperature

        assert rejects_temperature("claude-haiku-5-5-20260101") is True

    def test_legacy_models_still_accept_temperature(self) -> None:
        from sowsprint.llm.anthropic_client import rejects_temperature

        assert rejects_temperature("claude-3-5-sonnet") is False
        assert rejects_temperature("claude-3-haiku") is False

    def test_runtime_rejection_is_learned_and_retried(self, tmp_path) -> None:
        """An unlisted future model must self-heal rather than fail the run."""
        from sowsprint.config import Settings
        from sowsprint.llm.anthropic_client import AnthropicClient, rejects_temperature

        model = "claude-future-9-9"
        assert rejects_temperature(model) is False

        calls: list[dict] = []

        class FakeMessages:
            def create(self, **kwargs):
                calls.append(dict(kwargs))
                if "temperature" in kwargs:
                    raise RuntimeError(
                        "Error code: 400 - `temperature` is deprecated for this model."
                    )

                class Block:
                    type = "text"
                    text = "ok"

                class Usage:
                    input_tokens = 5
                    output_tokens = 1
                    cache_read_input_tokens = 0

                class Response:
                    content: ClassVar[list] = [Block()]
                    usage = Usage()
                    stop_reason = "end_turn"

                return Response()

        class FakeClient:
            messages = FakeMessages()

        settings = Settings(anthropic_api_key="sk-test", reasoning_model=model)
        client = AnthropicClient("temp-learn", settings)
        client._client = FakeClient()

        from sowsprint.llm.base import system, user

        response = client.complete(
            [system("hi"), user("ping")], node="probe", max_tokens=8
        )

        assert response.text == "ok"
        # First attempt carried temperature and was rejected; the retry omitted it.
        assert len(calls) == 2
        assert "temperature" in calls[0]
        assert "temperature" not in calls[1]
        # The lesson is retained for the rest of the process.
        assert rejects_temperature(model) is True


class TestEmptyBaseUrlIsOmitted:
    """A blank base_url must be absent, not empty.

    The OpenAI SDK applies its own default only when the argument is missing. Passing
    ``base_url=""`` stores the empty string, and every request URL is then built as
    ``"" + "/embeddings"``, which httpx rejects with ``UnsupportedProtocol``. The
    caller sees a bare ``APIConnectionError: Connection error`` — a network fault that
    is not one.

    This shipped: OpenAI embeddings were entirely non-functional whenever
    SOWSPRINT_OPENAI_BASE_URL was left blank, and the symptom was repeatedly diagnosed
    as intermittent connectivity and worked around by retrying.
    """

    def test_a_blank_setting_produces_no_base_url_argument(self) -> None:
        from sowsprint.config import openai_client_kwargs

        settings = make_settings(openai_base_url="")
        assert openai_client_kwargs(settings) == {}

    def test_whitespace_is_treated_as_unset(self) -> None:
        from sowsprint.config import openai_client_kwargs

        assert openai_client_kwargs(make_settings(openai_base_url="   ")) == {}

    def test_a_configured_url_is_passed_through(self) -> None:
        from sowsprint.config import openai_client_kwargs

        settings = make_settings(openai_base_url="https://gateway.internal/v1")
        assert openai_client_kwargs(settings) == {"base_url": "https://gateway.internal/v1"}

    def test_an_explicit_url_overrides_the_setting(self) -> None:
        from sowsprint.config import openai_client_kwargs

        settings = make_settings(openai_base_url="https://ignored/v1")
        assert openai_client_kwargs(settings, base_url="https://explicit/v1") == {
            "base_url": "https://explicit/v1"
        }

    def test_an_explicit_blank_overrides_a_configured_setting(self) -> None:
        """Groq and Ollama pass their own endpoint; an explicit blank means 'default'."""
        from sowsprint.config import openai_client_kwargs

        settings = make_settings(openai_base_url="https://configured/v1")
        assert openai_client_kwargs(settings, base_url="") == {}

    def test_the_openai_clients_resolve_a_real_default(self) -> None:
        """The regression that mattered: the client must not end up with base_url ''."""
        from sowsprint.llm.openai_client import OpenAIClient
        from sowsprint.rag.embeddings import OpenAIEmbedder

        settings = make_settings(
            openai_api_key="sk-test",
            openai_base_url="",
            embedding_provider="openai",
        )
        assert str(OpenAIEmbedder(settings)._client.base_url).startswith("https://")
        assert str(OpenAIClient("pytest-base-url", settings)._client.base_url).startswith("https://")
