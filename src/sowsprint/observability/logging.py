"""Structured logging configuration.

Emits JSON lines in production (container-friendly, Loki/Datadog-ingestable) and a
colourised human format in development. A ``run_id`` context variable is bound to
every record so a single scoping session can be traced across nodes and services.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

_run_id: ContextVar[str] = ContextVar("run_id", default="-")
_node: ContextVar[str] = ContextVar("node", default="-")


def bind_run(run_id: str) -> None:
    _run_id.set(run_id)


def bind_node(node: str) -> None:
    _node.set(node)


def current_run_id() -> str:
    return _run_id.get()


def _inject_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("run_id", _run_id.get())
    node = _node.get()
    if node and node != "-":
        event_dict.setdefault("node", node)
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Idempotently configure stdlib + structlog for the whole process."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )
    # Chainlit and httpx are extremely chatty at INFO.
    for noisy in ("httpx", "httpcore", "urllib3", "chainlit", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
