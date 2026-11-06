"""Google Gemini, and the engine line that says which engine actually ran.

Two things were learned the hard way while preparing the release demo, and both are tests now.

**A provider that returns 401 degrades silently.** Every node retried four times, fell back to the
deterministic engine and completed; the documents looked the same and the audit still returned a
verdict. Nothing on the result said a model had never been consulted, so two briefs "failing the
audit" were in fact the offline checker.

**Changing the provider leaves the model names behind.** `llm_provider` moved to OpenAI while
`reasoning_model` still read `claude-haiku-5-5`, and the API answered 404 for a model that does
not exist there. Gemini now carries `model_family_markers` so the same mistake substitutes a
Gemini default instead of producing an opaque 404.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sowsprint.config import LLMProvider, Settings  # noqa: E402
from sowsprint.llm.gemini_client import GeminiClient, _clean_schema  # noqa: E402
from sowsprint.models import CostReport  # noqa: E402
from sowsprint.telemetry import record_call, tracker  # noqa: E402


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "gemini_api_key": "test-key"}
    base.update(overrides)
    return Settings(**base)


class TestTheProviderIsSelectable:
    def test_gemini_resolves_when_it_is_the_only_credential(self) -> None:
        settings = _settings()
        assert settings.has_gemini
        # Anthropic and OpenAI are absent from this Settings instance, so auto lands on Gemini.
        assert settings.resolved_llm_provider is LLMProvider.GEMINI

    def test_it_can_be_selected_explicitly(self) -> None:
        settings = _settings(llm_provider="gemini")
        assert settings.resolved_llm_provider is LLMProvider.GEMINI

    def test_a_deployment_without_its_key_does_not_choose_it(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.resolved_llm_provider is not LLMProvider.GEMINI


class TestTheClientTranslatesTheWireFormat:
    """Gemini is the one provider here that is not OpenAI-compatible."""

    def test_roles_and_turns_are_converted(self) -> None:
        from sowsprint.llm.base import Message

        messages = [
            Message(role="system", content="You are a lawyer."),
            Message(role="user", content="Draft a clause."),
            Message(role="assistant", content="Here it is."),
        ]
        contents = GeminiClient._to_contents(messages)

        # Gemini has no `system` role: it becomes a user turn rather than being dropped.
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][0]["text"] == "Here it is."

    def test_consecutive_turns_of_the_same_role_are_merged(self) -> None:
        """Gemini rejects two user turns in a row; the parts are combined instead."""
        from sowsprint.llm.base import Message

        contents = GeminiClient._to_contents(
            [Message(role="user", content="one"), Message(role="user", content="two")]
        )
        assert len(contents) == 1
        assert len(contents[0]["parts"]) == 2

    def test_the_schema_is_stripped_of_what_gemini_rejects(self) -> None:
        """Pydantic emits `$defs` and `$ref`; Gemini accepts neither."""
        pydantic_style = {
            "title": "Plan",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "milestones": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Milestone"},
                }
            },
            "$defs": {
                "Milestone": {
                    "title": "Milestone",
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            },
        }

        cleaned = _clean_schema(pydantic_style)

        assert "title" not in cleaned
        assert "$defs" not in cleaned
        assert "additionalProperties" not in cleaned
        # The reference is inlined, not left dangling.
        assert cleaned["properties"]["milestones"]["items"]["type"] == "object"
        assert "name" in cleaned["properties"]["milestones"]["items"]["properties"]


class TestTheModelNameFollowsTheProvider:
    def test_a_foreign_model_id_falls_back_to_a_gemini_default(self) -> None:
        """Switching provider alone leaves the model names behind; that produced a 404."""
        client = GeminiClient("s", _settings())
        resolved = client.default_model("reasoning")

        assert "gemini" in resolved
        assert "claude" not in resolved

    def test_a_gemini_model_id_is_kept(self) -> None:
        client = GeminiClient("s", _settings(reasoning_model="gemini-2.5-flash"))
        assert client.default_model("reasoning") == "gemini-2.5-flash"


class TestTheEngineLine:
    """The card has to say which engine ran, because it is invisible otherwise."""

    @staticmethod
    def _engine_line(report: CostReport) -> str:
        # Mirrors app._engine_line without importing chainlit.
        providers = dict(getattr(report, "by_provider", {}) or {})
        if not providers:
            return "no model calls recorded"
        live = {n: c for n, c in providers.items() if n not in ("offline", "mock", "unknown")}
        offline = providers.get("offline", 0) + providers.get("mock", 0)
        if not live:
            return "offline"
        if offline:
            return "mixed"
        return "live"

    def test_the_ledger_counts_calls_per_provider(self) -> None:
        tracker.drop_ledger("engine-test")
        record_call(
            session_id="engine-test", model="gpt-4.1", provider="openai", node="legal",
            prompt_tokens=10, completion_tokens=5,
        )
        record_call(
            session_id="engine-test", model="offline", provider="offline", node="critic",
            prompt_tokens=5, completion_tokens=2, cost_override_usd=0.0,
        )

        report = tracker.get_ledger("engine-test").report()

        assert report.by_provider == {"openai": 1, "offline": 1}
        assert self._engine_line(report) == "mixed"

    def test_a_fully_offline_run_reads_as_offline(self) -> None:
        report = CostReport(session_id="x", by_provider={"offline": 14})
        assert self._engine_line(report) == "offline"

    def test_a_fully_live_run_reads_as_live(self) -> None:
        report = CostReport(session_id="x", by_provider={"anthropic": 12})
        assert self._engine_line(report) == "live"

    def test_two_live_providers_still_read_as_live(self) -> None:
        report = CostReport(session_id="x", by_provider={"anthropic": 9, "gemini": 3})
        assert self._engine_line(report) == "live"

    def test_no_calls_is_its_own_answer(self) -> None:
        """Not "live" and not "offline" — nothing ran."""
        assert self._engine_line(CostReport(session_id="x")) == "no model calls recorded"


@pytest.mark.parametrize("tier", ["reasoning", "critic", "fast"])
def test_every_tier_has_a_gemini_default(tier: str) -> None:
    """A tier without a default would send an empty model name to the API."""
    assert GeminiClient.provider_defaults.get(tier)
