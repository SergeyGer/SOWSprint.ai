"""Financial token telemetry.

Every LLM invocation in SOWSprint routes through :func:`record_call`, which prices
the call, appends it to the session ledger and updates the aggregate dashboard.

The ledger is intentionally *process-local and thread-safe*: a Chainlit session maps
1:1 to a ledger, and the sticky dashboard widget renders :meth:`CostLedger.report`
after every graph step.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

from ..config import get_settings
from ..models import CostReport, TokenUsage
from .pricing import SHADOW_PRICE, lookup, transcription_cost

_LEDGERS: dict[str, CostLedger] = {}
_REGISTRY_LOCK = threading.Lock()


class CostLedger:
    """Append-only, thread-safe token and cost accumulator for one session."""

    def __init__(self, session_id: str, budget_usd: float | None = None) -> None:
        self.session_id = session_id
        self.budget_usd = (
            budget_usd if budget_usd is not None else get_settings().session_budget_usd
        )
        self._lock = threading.Lock()
        self._entries: list[TokenUsage] = []
        self._by_node: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0.0, "tokens": 0.0, "cost_usd": 0.0}
        )
        self._by_model: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0.0, "tokens": 0.0, "cost_usd": 0.0}
        )
        self._started_at = datetime.utcnow()
        self._last_call_at: datetime | None = None
        self._totals = {
            "calls": 0, "prompt": 0, "completion": 0, "cost": 0.0, "latency": 0.0,
            "cached": 0, "savings": 0.0,
        }

    # ------------------------------------------------------------------ mutation
    def record(self, usage: TokenUsage) -> TokenUsage:
        """Attach a priced call to the ledger and return the enriched record."""
        with self._lock:
            self._entries.append(usage)
            self._totals["calls"] += 1
            self._totals["prompt"] += usage.prompt_tokens
            self._totals["completion"] += usage.completion_tokens
            self._totals["cost"] += usage.cost_usd
            self._totals["latency"] += usage.latency_ms
            self._totals["cached"] += usage.cached_tokens
            self._totals["savings"] += usage.cache_savings_usd

            node_bucket = self._by_node[usage.node]
            node_bucket["calls"] += 1
            node_bucket["tokens"] += usage.total_tokens
            node_bucket["cost_usd"] += usage.cost_usd

            model_bucket = self._by_model[usage.model]
            model_bucket["calls"] += 1
            model_bucket["tokens"] += usage.total_tokens
            model_bucket["cost_usd"] += usage.cost_usd

            self._last_call_at = datetime.utcnow()
        return usage

    def record_transcription(self, model: str, duration_seconds: float, node: str = "voice") -> TokenUsage:
        """Account for speech-to-text spend, which is priced per minute of audio."""
        usage = TokenUsage(
            model=model,
            provider="whisper",
            node=node,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=transcription_cost(model, duration_seconds),
            success=True,
        )
        return self.record(usage)

    # ------------------------------------------------------------------ accessors
    @property
    def entries(self) -> list[TokenUsage]:
        with self._lock:
            return list(self._entries)

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return self._totals["cost"]

    @property
    def is_over_budget(self) -> bool:
        return self.total_cost_usd >= self.budget_usd

    def report(self) -> CostReport:
        """Snapshot the ledger as a UI-ready aggregate."""
        with self._lock:
            prompt = self._totals["prompt"]
            completion = self._totals["completion"]
            return CostReport(
                session_id=self.session_id,
                calls=int(self._totals["calls"]),
                prompt_tokens=int(prompt),
                completion_tokens=int(completion),
                total_tokens=int(prompt + completion),
                cost_usd=round(self._totals["cost"], 6),
                cached_tokens=int(self._totals["cached"]),
                cache_savings_usd=round(self._totals["savings"], 6),
                budget_usd=self.budget_usd,
                by_node={k: dict(v) for k, v in self._by_node.items()},
                by_model={k: dict(v) for k, v in self._by_model.items()},
                latency_ms_total=self._totals["latency"],
                started_at=self._started_at,
                last_call_at=self._last_call_at,
            )

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._by_node.clear()
            self._by_model.clear()
            self._totals = {
            "calls": 0, "prompt": 0, "completion": 0, "cost": 0.0, "latency": 0.0,
            "cached": 0, "savings": 0.0,
        }
            self._last_call_at = None


def get_ledger(session_id: str) -> CostLedger:
    """Return (creating if needed) the ledger bound to ``session_id``."""
    with _REGISTRY_LOCK:
        ledger = _LEDGERS.get(session_id)
        if ledger is None:
            ledger = CostLedger(session_id)
            _LEDGERS[session_id] = ledger
        return ledger


def drop_ledger(session_id: str) -> None:
    """Release a finished session's ledger."""
    with _REGISTRY_LOCK:
        _LEDGERS.pop(session_id, None)


def price_call(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    *,
    simulate_offline: bool = True,
) -> tuple[float, bool]:
    """Price one call.

    Returns ``(cost_usd, simulated)``. Offline engines genuinely cost nothing; when
    ``simulate_offline`` is set they are priced against a shadow tier so the
    observability dashboard remains demonstrable without credentials.
    """
    entry = lookup(model)
    if entry.provider == "offline" and simulate_offline:
        return (
            SHADOW_PRICE.cost(prompt_tokens, completion_tokens, cached_tokens),
            True,
        )
    return entry.cost(prompt_tokens, completion_tokens, cached_tokens), False


def record_call(
    *,
    session_id: str,
    model: str,
    provider: str,
    node: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    latency_ms: float = 0.0,
    success: bool = True,
    error: str | None = None,
    simulate_offline: bool = True,
    cost_override_usd: float | None = None,
    extra: dict[str, Any] | None = None,
) -> TokenUsage:
    """Price and persist a single LLM invocation. Never raises.

    ``cost_override_usd`` exists for modalities the chat price book cannot express —
    embeddings and speech-to-text are billed per input token or per minute of audio,
    not per prompt/completion pair.
    """
    savings = 0.0
    if cost_override_usd is not None:
        cost = float(cost_override_usd)
    else:
        try:
            cost, _simulated = price_call(
                model,
                prompt_tokens,
                completion_tokens,
                cached_tokens,
                simulate_offline=simulate_offline,
            )
            savings = lookup(model).cache_savings(cached_tokens)
        except Exception:  # pragma: no cover - telemetry must never break a run
            cost = 0.0

    usage = TokenUsage(
        model=model,
        provider=provider,
        node=node,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cache_savings_usd=round(savings, 6),
        latency_ms=latency_ms,
        cost_usd=round(cost, 6),
        success=success,
        error=error,
        **(extra or {}),
    )
    return get_ledger(session_id).record(usage)


def estimate_tokens(text: str) -> int:
    """Cheap provider-agnostic token estimate (~4 chars/token, floor of 1).

    Used for pre-flight budget checks and for offline engines that do not report
    usage. Deliberately conservative: it rounds up.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
