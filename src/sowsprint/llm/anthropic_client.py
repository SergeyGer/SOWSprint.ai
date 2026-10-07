"""Anthropic Claude adapter.

The Messages API differs from the OpenAI contract in three ways this adapter
normalises: the system prompt is a top-level argument rather than a message role,
``max_tokens`` is mandatory, and usage is reported as ``input_tokens`` /
``output_tokens`` with a separate prompt-cache read counter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anthropic import Anthropic

from ..config import Settings
from .base import BaseLLMClient, Message


class AnthropicClient(BaseLLMClient):
    """Chat client for Anthropic Claude models."""

    provider = "anthropic"

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        super().__init__(session_id, settings)
        kwargs: dict[str, Any] = {
            "api_key": self.settings.anthropic_api_key,
            "timeout": self.settings.request_timeout_s,
            "max_retries": 0,
        }
        if self.settings.anthropic_base_url:
            kwargs["base_url"] = self.settings.anthropic_base_url
        self._client = Anthropic(**kwargs)

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
        system_prompt = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        if not turns:
            turns = [{"role": "user", "content": system_prompt or "Proceed."}]

        response = self._client.messages.create(
            model=model,
            system=system_prompt or None,
            messages=turns,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        return text, {
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cached_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "finish_reason": getattr(response, "stop_reason", None),
        }
