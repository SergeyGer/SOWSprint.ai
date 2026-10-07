"""LLM abstraction layer: provider adapters, deterministic offline engine, routing."""

from __future__ import annotations

from .base import (
    BaseLLMClient,
    BudgetExceededError,
    EchoClient,
    LLMError,
    LLMResponse,
    Message,
    StructuredOutputError,
    assistant,
    system,
    user,
)
from .factory import (
    NODE_TIERS,
    FallbackLLMClient,
    ModelRouter,
    build_llm_client,
    drop_session_client,
    get_session_client,
)
from .offline import OFFLINE_MODEL, OfflineLLMClient
from .parsing import extract_tagged, extract_tagged_json, loads_lenient, render_tagged

__all__ = [
    "NODE_TIERS",
    "OFFLINE_MODEL",
    "BaseLLMClient",
    "BudgetExceededError",
    "EchoClient",
    "FallbackLLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "ModelRouter",
    "OfflineLLMClient",
    "StructuredOutputError",
    "assistant",
    "build_llm_client",
    "drop_session_client",
    "extract_tagged",
    "extract_tagged_json",
    "get_session_client",
    "loads_lenient",
    "render_tagged",
    "system",
    "user",
]
