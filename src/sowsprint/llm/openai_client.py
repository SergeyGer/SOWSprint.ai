"""OpenAI and OpenAI-compatible chat adapter.

Because Groq, Ollama, vLLM, LiteLLM and Azure OpenAI all expose the same
``/chat/completions`` contract, this single adapter covers cloud reasoning *and*
self-hosted inference; only ``base_url`` and the model id change.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from openai import OpenAI

from ..config import Settings
from .base import BaseLLMClient, Message

#: Models that reject ``temperature`` and require ``max_completion_tokens``.
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class OpenAIClient(BaseLLMClient):
    """Chat client for OpenAI and every OpenAI-compatible gateway."""

    #: Deliberately permissive: this adapter also fronts Groq, Ollama, vLLM and
    #: LiteLLM, whose model ids share no common prefix. The guard exists to catch the
    #: one mistake that matters here — an Anthropic id sent to an OpenAI endpoint.
    model_family_markers: ClassVar[tuple[str, ...]] = ()

    provider_defaults: ClassVar[dict[str, str]] = {
        "reasoning": "gpt-4o",
        "critic": "gpt-4o-mini",
        "fast": "gpt-4o-mini",
    }

    def __init__(
        self,
        session_id: str,
        settings: Settings | None = None,
        *,
        provider: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        super().__init__(session_id, settings)
        self.provider = provider
        self._default_model = default_model
        self._client = OpenAI(
            api_key=api_key or self.settings.openai_api_key,
            base_url=base_url or self.settings.openai_base_url,
            timeout=self.settings.request_timeout_s,
            max_retries=0,  # the base class owns retry policy and accounting
        )

    def default_model(self, tier: str = "reasoning") -> str:
        if self._default_model:
            return self._default_model
        return super().default_model(tier)

    def _invoke(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        node: str,
        schema: type | None,
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
        }

        if model.lower().startswith(_REASONING_PREFIXES):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = temperature

        response = self._client.chat.completions.create(**payload)
        content = response.choices[0].message.content or ""

        usage = getattr(response, "usage", None)
        usage_dict: dict[str, Any] = {}
        if usage is not None:
            usage_dict["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
            usage_dict["completion_tokens"] = getattr(usage, "completion_tokens", 0) or 0
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                usage_dict["cached_tokens"] = getattr(details, "cached_tokens", 0) or 0
        usage_dict["finish_reason"] = getattr(response.choices[0], "finish_reason", None)
        return content, usage_dict


class GroqClient(OpenAIClient):
    """Groq LPU inference — used for low-latency Whisper and fast model tiers."""

    GROQ_BASE_URL = "https://api.groq.com/openai/v1"
    provider = "groq"

    provider_defaults: ClassVar[dict[str, str]] = {
        "reasoning": "llama-3.3-70b-versatile",
        "critic": "llama-3.3-70b-versatile",
        "fast": "llama-3.1-8b-instant",
    }

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        super().__init__(
            session_id,
            settings,
            provider="groq",
            api_key=(settings or Settings()).groq_api_key,
            base_url=self.GROQ_BASE_URL,
            default_model="llama-3.3-70b-versatile",
        )


class OllamaClient(OpenAIClient):
    """Self-hosted Ollama runtime, used as the on-premise guardrail tier."""

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        resolved = settings or Settings()
        super().__init__(
            session_id,
            resolved,
            provider="ollama",
            api_key="ollama",  # the OpenAI SDK requires a non-empty key
            base_url=f"{resolved.ollama_base_url.rstrip('/')}/v1",
            default_model=resolved.reasoning_model,
        )
