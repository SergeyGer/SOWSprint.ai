"""Provider-agnostic LLM client contract.

All agent nodes talk to :class:`BaseLLMClient` and never to a vendor SDK. A concrete
client is responsible for exactly one thing — turning messages into text — while the
base class owns everything cross-cutting:

* prompt-cache-aware telemetry and USD costing,
* retry with exponential backoff,
* session budget enforcement,
* structured-output schema injection and validation.

Adding a new vendor therefore means implementing a single ``_invoke`` method.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings, get_settings
from ..observability.logging import get_logger
from ..telemetry import tracker
from .parsing import loads_lenient

log = get_logger(__name__)

Role = Literal["system", "user", "assistant"]
ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a provider fails after exhausting retries."""


class StructuredOutputError(LLMError):
    """Raised when a response cannot be validated against the requested schema."""


class BudgetExceededError(RuntimeError):
    """Raised before a call that would push the session past its cost ceiling."""

    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(
            f"Session budget exhausted: ${spent:.4f} spent of ${budget:.2f} allowed."
        )
        self.spent = spent
        self.budget = budget


@dataclass(slots=True)
class Message:
    """A single conversation turn."""

    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def system(content: str) -> Message:
    return Message("system", content)


def user(content: str) -> Message:
    return Message("user", content)


def assistant(content: str) -> Message:
    return Message("assistant", content)


@dataclass(slots=True)
class LLMResponse:
    """Normalised provider response including usage and cost metadata."""

    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    parsed: BaseModel | None = None
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def schema_hint(schema: type[BaseModel] | None) -> str:
    """Render a JSON-schema instruction block appended to the system prompt.

    Used for every provider because it is universally supported, unlike vendor-native
    structured-output modes which vary between OpenAI, Anthropic and OSS gateways.
    """
    if schema is None:
        return ""
    jsonschema = schema.model_json_schema()
    return (
        "\n\n## Output contract\n"
        "Reply with a SINGLE JSON object and nothing else — no prose, no markdown\n"
        "fences. It MUST validate against this JSON Schema:\n\n"
        f"```json\n{json.dumps(jsonschema, ensure_ascii=False, indent=2)}\n```\n"
    )


class BaseLLMClient(ABC):
    """Common behaviour shared by every provider adapter, including the offline engine."""

    provider: str = "base"

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        self.session_id = session_id
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ provider
    @abstractmethod
    def _invoke(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        node: str,
        schema: type[BaseModel] | None,
    ) -> tuple[str, dict[str, Any]]:
        """Perform one provider call.

        Returns ``(text, usage)`` where ``usage`` may contain ``prompt_tokens``,
        ``completion_tokens`` and ``cached_tokens``.
        """

    def default_model(self, tier: str = "reasoning") -> str:
        """Resolve a logical tier to a concrete model id for this provider."""
        if tier == "fast":
            return self.settings.fast_model
        if tier == "critic":
            return self.settings.critic_model
        return self.settings.reasoning_model

    # ------------------------------------------------------------------ public API
    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[ModelT] | None = None,
        model: str | None = None,
        tier: str = "reasoning",
        temperature: float | None = None,
        max_tokens: int | None = None,
        node: str = "unknown",
        check_budget: bool = True,
    ) -> LLMResponse:
        """Run a completion, priced and validated.

        ``schema`` requests structured output; the returned ``LLMResponse.parsed``
        holds the validated Pydantic instance.
        """
        resolved_model = model or self.default_model(tier)
        resolved_temperature = (
            self.settings.temperature if temperature is None else temperature
        )
        resolved_max_tokens = max_tokens or self.settings.max_tokens

        if check_budget:
            ledger = tracker.get_ledger(self.session_id)
            if ledger.is_over_budget:
                report = ledger.report()
                raise BudgetExceededError(report.cost_usd, report.budget_usd)

        prepared = list(messages)
        hint = schema_hint(schema)
        if hint:
            prepared = self._append_to_system(prepared, hint)

        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            try:
                text, usage = self._invoke(
                    prepared,
                    model=resolved_model,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    node=node,
                    schema=schema,
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000.0
                last_error = exc
                tracker.record_call(
                    session_id=self.session_id,
                    model=resolved_model,
                    provider=self.provider,
                    node=node,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=elapsed,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                log.warning(
                    "llm.attempt_failed",
                    provider=self.provider,
                    model=resolved_model,
                    node=node,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < self.settings.max_retries:
                    time.sleep(min(2.0 ** (attempt - 1) * 0.5, 4.0))
                    continue
                raise LLMError(
                    f"{self.provider} call failed after {attempt} attempts: {exc}"
                ) from exc

            elapsed = (time.perf_counter() - started) * 1000.0
            usage = usage or {}
            usage_record = tracker.record_call(
                session_id=self.session_id,
                model=resolved_model,
                provider=self.provider,
                node=node,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                cached_tokens=int(usage.get("cached_tokens", 0) or 0),
                latency_ms=elapsed,
                success=True,
                extra={"finish_reason": usage.get("finish_reason")},
            )

            parsed: BaseModel | None = None
            if schema is not None:
                parsed = self._validate(text, schema)
                if parsed is None:
                    last_error = StructuredOutputError(
                        f"{self.provider} returned output that does not satisfy "
                        f"{schema.__name__}"
                    )
                    log.warning(
                        "llm.schema_invalid",
                        provider=self.provider,
                        node=node,
                        attempt=attempt,
                        preview=text[:400],
                    )
                    if attempt < self.settings.max_retries:
                        prepared = self._append_to_system(
                            prepared,
                            "\n\n## Correction\nYour previous reply was not valid JSON "
                            "matching the schema. Reply with ONLY the JSON object.",
                        )
                        continue
                    raise last_error

            return LLMResponse(
                text=text,
                model=resolved_model,
                provider=self.provider,
                prompt_tokens=usage_record.prompt_tokens,
                completion_tokens=usage_record.completion_tokens,
                cached_tokens=usage_record.cached_tokens,
                latency_ms=elapsed,
                cost_usd=usage_record.cost_usd,
                parsed=parsed,
                finish_reason=usage.get("finish_reason"),
                raw=usage,
            )

        raise LLMError(f"{self.provider} call failed: {last_error}")  # pragma: no cover

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _append_to_system(messages: Sequence[Message], extra: str) -> list[Message]:
        prepared = list(messages)
        for index, message in enumerate(prepared):
            if message.role == "system":
                prepared[index] = system(message.content + extra)
                return prepared
        return [system(extra.strip()), *prepared]

    @staticmethod
    def _validate(text: str, schema: type[ModelT]) -> ModelT | None:
        payload = loads_lenient(text)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            # Some models wrap the object in a single-element array.
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                payload = payload[0]
            else:
                return None
        try:
            return schema.model_validate(payload)
        except ValidationError:
            return None


class EchoClient(BaseLLMClient):
    """Trivial client used by tests to assert routing without invoking a provider."""

    provider = "echo"

    def _invoke(
        self, messages, *, model, temperature, max_tokens, node, schema
    ) -> tuple[str, dict[str, Any]]:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        if schema is not None:
            return json.dumps(self._stub(schema)), {"prompt_tokens": 1, "completion_tokens": 1}
        return last_user, {"prompt_tokens": 1, "completion_tokens": 1}

    @staticmethod
    def _stub(schema: type[BaseModel]) -> dict[str, Any]:
        """Build a minimal valid instance for the schema (used only by tests)."""
        payload: dict[str, Any] = {}
        for name, model_field in schema.model_fields.items():
            if model_field.is_required():
                payload[name] = _placeholder(model_field.annotation)
        return payload


def _placeholder(annotation: Any) -> Any:
    origin = getattr(annotation, "__origin__", None)
    if annotation is str:
        return "stub"
    if annotation is int:
        return 1
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if origin is list:
        return []
    if origin is dict:
        return {}
    return "stub"
