"""Value types shared between the registry and the connectors.

Extracted so the connectors can describe their results without importing the registry,
which imports them. That cycle was real — deferred to a function-local import to work at
runtime — and it made the dependency direction between orchestration and adapters
unreadable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolExecutionResult(BaseModel):
    """Outcome of one tool invocation."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    dry_run: bool = True
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    #: R3.2: a short, stable category for the failure — `timeout`, `skipped`, `auth`, `error`.
    #:
    #: Separate from `error` on purpose: the message is for a person and may quote a provider,
    #: while the code is for the journal, the summary and any retry decision. Storing only the
    #: message forces every caller to pattern-match prose.
    error_code: str = ""
    resource_key: str = ""
    resource_url: str = ""

    def summary_line(self) -> str:
        mark = "✅" if self.ok else "❌"
        mode = " (dry-run)" if self.dry_run else ""
        target = self.resource_key or self.tool
        return f"{mark} `{self.tool}` → {target}{mode}"
