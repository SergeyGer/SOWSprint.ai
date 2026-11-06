"""Google Gemini.

Gemini is the one provider here that is not OpenAI-compatible, which is why it gets its own
adapter rather than a subclass like Groq and Ollama do. Three differences matter:

* the model name travels in the **path**, not the body;
* the key travels in a **header** (`x-goog-api-key`), not a bearer token;
* the conversation is a list of `contents` with `parts`, not `messages` with a `content`
  string, and the assistant role is called `model`.

Everything above this adapter — tier routing, schema validation, budget accounting, the offline
fallback — is shared, so the differences stop here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar

import httpx

from ..config import Settings
from ..observability.logging import get_logger
from .base import BaseLLMClient, LLMError, Message

log = get_logger(__name__)

#: Gemini's REST surface. `v1beta` is where the current models are served; `v1` lags.
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient(BaseLLMClient):
    """Chat client for Google Gemini."""

    provider = "gemini"

    #: Anything without `gemini` in the id is a leftover from another provider's configuration —
    #: switching `llm_provider` alone leaves the model names behind, and Google answers with a
    #: 404 that reads like a broken deployment rather than a mismatched setting.
    model_family_markers: ClassVar[tuple[str, ...]] = ("gemini",)

    provider_defaults: ClassVar[dict[str, str]] = {
        "reasoning": "gemini-2.5-pro",
        "critic": "gemini-2.5-pro",
        "fast": "gemini-2.5-flash",
    }

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        super().__init__(session_id, settings)
        if not self.settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        self._base_url = (
            getattr(self.settings, "gemini_base_url", None) or DEFAULT_BASE_URL
        ).rstrip("/")

    # ------------------------------------------------------------------ translation
    @staticmethod
    def _to_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Translate the shared message shape into Gemini's.

        Gemini has no `system` role: instructions go in `systemInstruction`, which the caller
        assembles separately. A system message that reaches here is folded into the first user
        turn rather than dropped — losing it would silently change what the model was asked.
        """
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = "model" if message.role == "assistant" else "user"
            text = message.content
            if message.role == "system":
                role = "user"
            if contents and contents[-1]["role"] == role:
                # Gemini expects alternating turns; consecutive same-role parts are merged.
                contents[-1]["parts"].append({"text": text})
            else:
                contents.append({"role": role, "parts": [{"text": text}]})
        return contents

    # ------------------------------------------------------------------ provider
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
        contents = self._to_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if schema is not None:
            # Gemini accepts a JSON schema directly. Passing it means the provider enforces the
            # shape, which is the same contract the other adapters get from their structured
            # output modes — and it fails at the API rather than in our validator.
            payload["generationConfig"]["responseMimeType"] = "application/json"
            with_schema = schema.model_json_schema() if hasattr(schema, "model_json_schema") else None
            if with_schema:
                payload["generationConfig"]["responseSchema"] = _clean_schema(with_schema)

        url = f"{self._base_url}/models/{model}:generateContent"
        try:
            response = httpx.post(
                url,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key or "",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=getattr(self.settings, "request_timeout_s", 120),
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"gemini request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"gemini returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            # A blocked prompt returns no candidates and a `promptFeedback` block reason; saying
            # which is far more useful than "empty response".
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "no candidates returned"
            raise LLMError(f"gemini produced no output: {reason}")

        candidate = candidates[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            finish = candidate.get("finishReason") or "unknown"
            raise LLMError(f"gemini returned no text (finishReason={finish})")

        usage = data.get("usageMetadata") or {}
        return text, {
            "prompt_tokens": usage.get("promptTokenCount", 0) or 0,
            "completion_tokens": usage.get("candidatesTokenCount", 0) or 0,
            "finish_reason": candidate.get("finishReason"),
        }


#: Keys Gemini's `responseSchema` rejects. Pydantic emits all of them; the schema is a request
#: for constrained decoding, not a document, so dropping the annotations loses nothing.
_DROPPED_SCHEMA_KEYS = frozenset(
    {"title", "default", "examples", "$defs", "additionalProperties", "definitions"}
)


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip the JSON-Schema vocabulary Gemini does not accept.

    `$defs` is the awkward one: Pydantic hoists nested models there and references them with
    `$ref`, which Gemini does not resolve. The reference is inlined instead, so a schema with a
    nested list of stories is sent as a schema rather than rejected as malformed.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                name = node["$ref"].rsplit("/", 1)[-1]
                target = defs.get(name)
                if target is not None:
                    return walk(target)
            return {
                key: walk(value)
                for key, value in node.items()
                if key not in _DROPPED_SCHEMA_KEYS
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    cleaned = walk(schema)
    # `type` must be present at the root or Gemini rejects the request.
    if isinstance(cleaned, dict) and "type" not in cleaned and "properties" in cleaned:
        cleaned["type"] = "object"
    return cleaned


def describe(data: Any) -> str:  # pragma: no cover - diagnostics helper
    """Compact representation for a log line."""
    return json.dumps(data, default=str)[:400]
