"""Anthropic Claude adapter.

The Messages API differs from the OpenAI contract in three ways this adapter
normalises: the system prompt is a top-level argument rather than a message role,
``max_tokens`` is mandatory, and usage is reported as ``input_tokens`` /
``output_tokens`` with a separate prompt-cache read counter.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, ClassVar

import httpx
from anthropic import Anthropic

from ..config import Settings
from .base import BaseLLMClient, Message, get_logger

log = get_logger(__name__)

#: Models known to reject the ``temperature`` parameter. Newer Claude releases
#: deprecate it in favour of adaptive thinking. This is a *cache*, not the source of
#: truth: a model that rejects it at runtime is added automatically and the request is
#: retried without it, so an unlisted future release still works with no code change.
_TEMPERATURE_REJECTING: set[str] = {
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
}
_TEMPERATURE_LOCK = threading.Lock()


def rejects_temperature(model: str) -> bool:
    """True when ``temperature`` must be omitted for this model.

    Matches on prefix so dated snapshots (``claude-haiku-5-5-20260101``) are caught
    without enumerating every release.
    """
    needle = model.strip().lower()
    with _TEMPERATURE_LOCK:
        return any(needle.startswith(known) for known in _TEMPERATURE_REJECTING)


def remember_temperature_rejection(model: str) -> None:
    """Record that this model rejects ``temperature``, for the rest of the process."""
    needle = model.strip().lower()
    with _TEMPERATURE_LOCK:
        _TEMPERATURE_REJECTING.add(needle)


class AnthropicClient(BaseLLMClient):
    """Chat client for Anthropic Claude models."""

    provider = "anthropic"

    #: Claude ids always contain "claude". Anything else reaching this client is a
    #: configuration error that would otherwise surface as an opaque API 404.
    model_family_markers = ("claude",)

    provider_defaults: ClassVar[dict[str, str]] = {
        "reasoning": "claude-sonnet-5-5",
        "critic": "claude-sonnet-5-5",
        "fast": "claude-haiku-5-5",
    }

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        super().__init__(session_id, settings)
        kwargs: dict[str, Any] = {
            "api_key": self.settings.anthropic_api_key,
            # Split budgets: fail fast on an unreachable endpoint, wait patiently for
            # a long structured completion.
            "timeout": httpx.Timeout(
                self.settings.request_timeout_s, connect=self.settings.connect_timeout_s
            ),
            "max_retries": 0,
        }
        if self.settings.anthropic_base_url:
            kwargs["base_url"] = self.settings.anthropic_base_url
        self._client = Anthropic(**kwargs)

    @staticmethod
    def _cacheable_system(system_prompt: str, model: str) -> Any:
        """Mark the persona + schema block as an ephemeral prompt-cache breakpoint.

        Every node re-sends a large static prefix on every call — the persona plus the
        JSON schema, which runs to roughly 900 tokens for ``TechnicalBlueprint``. Across
        retries, clarification rounds, repair loops and successive engagements that
        prefix is byte-identical, so Anthropic can serve it from cache at 10% of the
        input rate instead of re-billing it.

        Anthropic caches the prefix *up to and including* the marked block, which is why
        the breakpoint goes on the system block: everything before it is stable, and
        everything after it (the evidence, the draft) is call-specific.

        The minimum cacheable prefix is 1024 tokens for Sonnet/Opus and 2048 for Haiku;
        below that the API simply ignores the marker, so marking unconditionally is
        safe.
        """
        if not system_prompt:
            return None
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

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

        request: dict[str, Any] = {
            "model": model,
            "system": self._cacheable_system(system_prompt, model),
            "messages": turns,
            "max_tokens": max_tokens,
        }
        if not rejects_temperature(model):
            request["temperature"] = temperature

        try:
            response = self._client.messages.create(**request)
        except Exception as exc:
            # Newer Claude releases deprecate `temperature` in favour of adaptive
            # thinking, and the set of such models grows with every release. Rather
            # than maintain a hard-coded list that rots, learn from the API once and
            # retry immediately — the caller never sees the failure.
            if "temperature" in request and "temperature" in str(exc).lower():
                log.info("anthropic.temperature_unsupported", model=model, node=node)
                remember_temperature_rejection(model)
                request.pop("temperature")
                response = self._client.messages.create(**request)
            else:
                raise

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read or cache_write:
            log.info(
                "anthropic.prompt_cache",
                model=model,
                node=node,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )
        return text, {
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cached_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "finish_reason": getattr(response, "stop_reason", None),
        }
