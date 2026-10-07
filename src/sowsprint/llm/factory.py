"""Model routing and client construction.

Two responsibilities live here:

* **Tier routing** — each graph node declares the quality tier it needs, so the
  expensive reasoning tier is spent only where it changes the outcome.
* **Degradation** — when a cloud provider fails or the session budget is exhausted,
  the resilient wrapper drops to the deterministic offline engine instead of
  failing the run. The contract is produced either way; the UI reports which
  engine served each step.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import LLMProvider, Settings, get_settings
from ..observability.logging import get_logger
from ..telemetry import tracker
from .anthropic_client import AnthropicClient
from .base import BaseLLMClient, BudgetExceededError, LLMError, LLMResponse, Message
from .offline import OfflineLLMClient
from .openai_client import GroqClient, OllamaClient, OpenAIClient

log = get_logger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)

#: Quality tier requested by each graph node.
NODE_TIERS: dict[str, str] = {
    "triage": "fast",
    "query_rewrite": "fast",
    "architect": "reasoning",
    "legal": "reasoning",
    "critic": "critic",
    "summarise": "fast",
}

#: Nodes whose output is contractual and therefore never downgraded for cost.
PROTECTED_NODES = frozenset({"legal", "critic"})

#: Budget fraction above which non-protected nodes drop to the cheap tier.
DEGRADE_THRESHOLD = 0.7


class ModelRouter:
    """Chooses the quality tier for a node, taking budget pressure into account."""

    def __init__(self, session_id: str, settings: Settings | None = None) -> None:
        self.session_id = session_id
        self.settings = settings or get_settings()

    def tier_for(self, node: str) -> str:
        tier = NODE_TIERS.get(node, "reasoning")
        if tier == "reasoning" and node not in PROTECTED_NODES:
            ledger = tracker.get_ledger(self.session_id)
            report = ledger.report()
            if report.budget_usd > 0 and report.cost_usd / report.budget_usd >= DEGRADE_THRESHOLD:
                log.info(
                    "router.budget_downgrade",
                    node=node,
                    spent_usd=round(report.cost_usd, 4),
                    budget_usd=report.budget_usd,
                )
                return "fast"
        return tier


class FallbackLLMClient(BaseLLMClient):
    """Try the primary provider; on failure degrade to the offline engine.

    Budget exhaustion is *not* a failure and is propagated to the caller so the
    graph can halt deliberately rather than silently producing an ungrounded
    contract.
    """

    def __init__(
        self, primary: BaseLLMClient, fallback: BaseLLMClient, session_id: str
    ) -> None:
        super().__init__(session_id, primary.settings)
        self.primary = primary
        self.fallback = fallback
        self.provider = primary.provider
        self.degraded_calls = 0

    def default_model(self, tier: str = "reasoning") -> str:
        return self.primary.default_model(tier)

    def _invoke(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("FallbackLLMClient delegates complete()")

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[ModelT] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Attempt the primary provider, degrading on any provider failure."""
        try:
            return self.primary.complete(messages, schema=schema, **kwargs)
        except BudgetExceededError:
            raise
        except LLMError as exc:
            self.degraded_calls += 1
            log.warning(
                "llm.degraded_to_offline",
                provider=self.primary.provider,
                node=kwargs.get("node", "unknown"),
                error=str(exc),
            )
            response = self.fallback.complete(messages, schema=schema, **kwargs)
            response.raw = {**response.raw, "degraded_from": self.primary.provider}
            return response

    @property
    def was_degraded(self) -> bool:
        return self.degraded_calls > 0


def build_llm_client(
    session_id: str,
    settings: Settings | None = None,
    *,
    provider: LLMProvider | None = None,
    enable_fallback: bool = True,
) -> BaseLLMClient:
    """Construct the client selected by configuration, wrapped for resilience."""
    resolved_settings = settings or get_settings()
    chosen = provider or resolved_settings.resolved_llm_provider

    primary: BaseLLMClient
    try:
        primary = _construct(chosen, session_id, resolved_settings)
    except Exception as exc:
        log.warning("llm.provider_init_failed", provider=chosen.value, error=str(exc))
        primary = OfflineLLMClient(session_id, resolved_settings)

    if not enable_fallback or primary.provider == "offline":
        return primary
    return FallbackLLMClient(primary, OfflineLLMClient(session_id, resolved_settings), session_id)


def _construct(provider: LLMProvider, session_id: str, settings: Settings) -> BaseLLMClient:
    if provider is LLMProvider.ANTHROPIC:
        if not settings.has_anthropic:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        return AnthropicClient(session_id, settings)
    if provider is LLMProvider.OPENAI:
        if not settings.has_openai:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        return OpenAIClient(session_id, settings)
    if provider is LLMProvider.GROQ:
        if not settings.has_groq:
            raise RuntimeError("GROQ_API_KEY is not configured")
        return GroqClient(session_id, settings)
    if provider is LLMProvider.OLLAMA:
        return OllamaClient(session_id, settings)
    return OfflineLLMClient(session_id, settings)


_CLIENTS: dict[str, BaseLLMClient] = {}
_CLIENTS_LOCK = threading.Lock()


def get_session_client(session_id: str, settings: Settings | None = None) -> BaseLLMClient:
    """Return the process-wide client bound to a Chainlit session."""
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(session_id)
        if client is None:
            client = build_llm_client(session_id, settings)
            _CLIENTS[session_id] = client
        return client


def drop_session_client(session_id: str) -> None:
    with _CLIENTS_LOCK:
        _CLIENTS.pop(session_id, None)
