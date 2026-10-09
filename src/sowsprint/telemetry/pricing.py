"""Model price book used by the financial token telemetry layer.

Prices are expressed in **USD per 1,000,000 tokens** and mirror public list pricing.
Keeping the book in one place means a price change never requires touching the
accounting logic.

Offline (deterministic) engines consume no billable tokens. To keep the cost
dashboard meaningful in credential-free environments the ledger can apply a
*shadow price*: the offline call is priced at a configurable reference tier and the
report is labelled ``simulated`` so the number is never mistaken for a real invoice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MILLION = 1_000_000.0

#: Shortest price-book key allowed to match as an embedded, delimited segment. Keys
#: below this length (e.g. "o3") would otherwise match inside unrelated model names.
MIN_SEGMENT_KEY_LENGTH = 5


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Per-million-token pricing for a single model."""

    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    provider: str = "unknown"

    def cache_savings(self, cached_tokens: int) -> float:
        """USD avoided by serving ``cached_tokens`` from the prompt cache.

        A cache read is billed at a fraction of the input rate (10% at Anthropic,
        typically 25-50% at OpenAI), so the saving is the difference against what
        those tokens would otherwise have cost.
        """
        if cached_tokens <= 0:
            return 0.0
        cached_rate = (
            self.cached_input_per_mtok
            if self.cached_input_per_mtok is not None
            else self.input_per_mtok
        )
        return max(0.0, cached_tokens * (self.input_per_mtok - cached_rate)) / MILLION

    def cost(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
        """Return the USD cost of one call, honouring discounted cached reads."""
        billable_prompt = max(0, prompt_tokens - cached_tokens)
        cached_rate = (
            self.cached_input_per_mtok
            if self.cached_input_per_mtok is not None
            else self.input_per_mtok
        )
        return (
            billable_prompt * self.input_per_mtok
            + cached_tokens * cached_rate
            + completion_tokens * self.output_per_mtok
        ) / MILLION


#: Reference price book. Keys are matched case-insensitively, longest-prefix first,
#: so dated snapshots such as ``gpt-4o-2024-11-20`` resolve to the ``gpt-4o`` entry.
#:
#: Verified against the vendors' published pricing pages. An unlisted model that
#: prefix-matches a listed family bills at the family rate; a model matching nothing
#: prices at zero — which is exactly why new model ids must be added here. A silently
#: free model makes the cost dashboard lie.
PRICE_BOOK: dict[str, ModelPrice] = {
    # ---- Anthropic (current) ------------------------------------------------
    "claude-haiku-5-5": ModelPrice(0.10, 0.50, 0.01, "anthropic"),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00, 0.10, "anthropic"),
    "claude-opus-5-5": ModelPrice(4.00, 20.00, 0.20, "anthropic"),
    "claude-fable-5-1": ModelPrice(10.00, 50.00, 0.25, "anthropic"),
    # ---- Anthropic (legacy, still callable) ---------------------------------
    "claude-3-5-sonnet": ModelPrice(3.00, 15.00, 0.30, "anthropic"),
    "claude-3-5-haiku": ModelPrice(0.80, 4.00, 0.08, "anthropic"),
    "claude-3-7-sonnet": ModelPrice(3.00, 15.00, 0.30, "anthropic"),
    "claude-sonnet-4": ModelPrice(3.00, 15.00, 0.30, "anthropic"),
    "claude-opus-4": ModelPrice(15.00, 75.00, 1.50, "anthropic"),
    "claude-3-opus": ModelPrice(15.00, 75.00, 1.50, "anthropic"),
    "claude-3-haiku": ModelPrice(0.25, 1.25, 0.03, "anthropic"),
    # ---- OpenAI (current) ---------------------------------------------------
    "gpt-6-luna": ModelPrice(0.10, 0.50, 0.01, "openai"),
    "gpt-6-sol": ModelPrice(2.00, 10.00, 0.20, "openai"),
    "gpt-6-astra": ModelPrice(10.00, 50.00, 1.00, "openai"),
    "gpt-5.6-luna": ModelPrice(0.20, 1.20, 0.02, "openai"),
    "gpt-5.6-terra": ModelPrice(2.00, 12.00, 0.20, "openai"),
    "gpt-5.6-sol": ModelPrice(4.00, 20.00, 0.40, "openai"),
    "gpt-5.4-nano": ModelPrice(0.20, 1.25, 0.02, "openai"),
    "gpt-5.4-mini": ModelPrice(0.75, 4.50, 0.075, "openai"),
    "gpt-5.4": ModelPrice(2.50, 15.00, 0.25, "openai"),
    "gpt-5-nano": ModelPrice(0.05, 0.40, 0.005, "openai"),
    "gpt-5-mini": ModelPrice(0.25, 2.00, 0.025, "openai"),
    "gpt-5.2": ModelPrice(1.75, 14.00, 0.175, "openai"),
    "gpt-5.1": ModelPrice(1.25, 10.00, 0.125, "openai"),
    "gpt-5": ModelPrice(1.25, 10.00, 0.125, "openai"),
    # ---- OpenAI (widely deployed) -------------------------------------------
    "gpt-4o": ModelPrice(2.50, 10.00, 1.25, "openai"),
    "gpt-4o-mini": ModelPrice(0.15, 0.60, 0.075, "openai"),
    "gpt-4.1": ModelPrice(2.00, 8.00, 0.50, "openai"),
    "gpt-4.1-mini": ModelPrice(0.40, 1.60, 0.10, "openai"),
    "gpt-4.1-nano": ModelPrice(0.10, 0.40, 0.025, "openai"),
    "o4-mini": ModelPrice(1.10, 4.40, 0.275, "openai"),
    "o3-mini": ModelPrice(1.10, 4.40, 0.55, "openai"),
    "o3": ModelPrice(2.00, 8.00, 0.50, "openai"),
    # ---- Groq (open-weights, very high throughput) --------------------------
    "llama-3.3-70b-versatile": ModelPrice(0.59, 0.79, None, "groq"),
    "llama-3.1-8b-instant": ModelPrice(0.05, 0.08, None, "groq"),
    "mixtral-8x7b": ModelPrice(0.24, 0.24, None, "groq"),
    # ---- Self-hosted (marginal compute only) --------------------------------
    "llama3.1": ModelPrice(0.0, 0.0, None, "ollama"),
    "qwen2.5": ModelPrice(0.0, 0.0, None, "ollama"),
    "mistral": ModelPrice(0.0, 0.0, None, "ollama"),
    # ---- Offline deterministic engine (no billable usage) -------------------
    "offline-heuristic": ModelPrice(0.0, 0.0, None, "offline"),
}

#: Embedding price book (USD per 1M input tokens).
EMBEDDING_PRICE_BOOK: dict[str, ModelPrice] = {
    "text-embedding-3-small": ModelPrice(0.02, 0.0, None, "openai"),
    "text-embedding-3-large": ModelPrice(0.13, 0.0, None, "openai"),
    "text-embedding-ada-002": ModelPrice(0.10, 0.0, None, "openai"),
    "hash-embedding": ModelPrice(0.0, 0.0, None, "offline"),
}

#: Speech-to-text price book (USD per minute of audio).
TRANSCRIPTION_PRICE_BOOK: dict[str, float] = {
    "whisper-1": 0.006,
    "whisper-large-v3-turbo": 0.0007,
    "whisper-large-v3": 0.00185,
    "offline": 0.0,
}

#: Shadow price applied to offline calls when simulated costing is enabled.
SHADOW_PRICE = ModelPrice(2.50, 10.00, 1.25, "offline-simulated")


def lookup(model: str, *, book: dict[str, ModelPrice] | None = None) -> ModelPrice:
    """Resolve a model identifier to its price entry.

    Matching is case-insensitive and uses longest-match-wins across two rules:

    1. **Prefix** — a dated snapshot such as ``gpt-4o-2024-11-20`` resolves to its
       family entry ``gpt-4o``. Longest prefix wins, so ``gpt-4o-mini-2024-07-18``
       resolves to ``gpt-4o-mini`` rather than the pricier ``gpt-4o``.
    2. **Delimited segment** — a vendor-namespaced id such as ``openai/gpt-4o`` or
       ``anthropic.claude-3-5-sonnet`` resolves to the embedded family entry. The key
       must be at least :data:`MIN_SEGMENT_KEY_LENGTH` characters to qualify, which
       stops short keys like ``o3`` from matching inside unrelated model names
       (``some-o3-ish-model`` must stay unpriced, not silently bill at o3 rates).

    Unknown models fall back to a zero-cost entry rather than raising, because
    telemetry must never break the execution path.
    """
    book = PRICE_BOOK if book is None else book
    if not model:
        return ModelPrice(0.0, 0.0, None, "unknown")

    needle = model.strip().lower()
    if needle in book:
        return book[needle]

    best: tuple[int, ModelPrice] | None = None
    for key, price in book.items():
        score = 0
        if needle.startswith(key) or (len(key) >= MIN_SEGMENT_KEY_LENGTH and re.search(
            rf"(?:^|[/\-.:_]){re.escape(key)}(?:$|[/\-.:_@])", needle
        )):
            score = len(key)
        if score and (best is None or score > best[0]):
            best = (score, price)

    return best[1] if best else ModelPrice(0.0, 0.0, None, "unknown")


def transcription_cost(model: str, duration_seconds: float) -> float:
    """USD cost of transcribing ``duration_seconds`` of audio."""
    rate = TRANSCRIPTION_PRICE_BOOK.get(model, 0.0)
    return rate * (max(0.0, duration_seconds) / 60.0)
