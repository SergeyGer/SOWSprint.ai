"""Notion API connector.

Mirrors :mod:`sowsprint.tools.jira`: live REST calls when the workspace is configured
and ``dry_run_integrations`` is disabled, otherwise payload-validating simulations.

Notion's API is unusually strict — ``Notion-Version`` is mandatory and every page is a
tree of typed blocks. The Markdown-to-blocks converter below covers the subset the
engagement summary actually uses (headings, bullets, paragraphs, quotes).
"""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from ..observability.logging import get_logger
from .registry import ToolExecutionResult

log = get_logger(__name__)

NOTION_VERSION = "2022-06-28"


class NotionConnector:
    """Minimal Notion page-creation client."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._counter = 0

    @property
    def dry_run(self) -> bool:
        return self.settings.dry_run_integrations or not self.settings.notion_configured

    @property
    def mode(self) -> str:
        return "dry-run" if self.dry_run else "live"

    def create_page(
        self,
        title: str,
        content_markdown: str,
        parent_page_id: str | None = None,
    ) -> ToolExecutionResult:
        """Create a Notion page under the configured parent."""
        resolved_parent = parent_page_id or self.settings.notion_parent_page_id or "workspace-root"
        payload = {
            "parent": {"type": "page_id", "page_id": resolved_parent},
            "properties": {
                "title": [{"type": "text", "text": {"content": title[:2000]}}]
            },
            "children": markdown_to_notion_blocks(content_markdown)[:100],
        }

        if self.dry_run:
            self._counter += 1
            synthetic_id = f"notion-page-{self._counter:04d}"
            return ToolExecutionResult(
                tool="create_notion_page",
                ok=True,
                dry_run=True,
                request={"method": "POST", "path": "/v1/pages", "body": payload},
                response={"id": synthetic_id, "url": f"https://notion.so/{synthetic_id}"},
                resource_key=synthetic_id,
                resource_url=f"https://notion.so/{synthetic_id}",
            )

        try:
            import httpx

            with httpx.Client(timeout=self.settings.request_timeout_s) as client:
                response = client.post(
                    "https://api.notion.com/v1/pages",
                    headers={
                        "Authorization": f"Bearer {self.settings.notion_api_key}",
                        "Notion-Version": NOTION_VERSION,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            page_id = body.get("id", "")
            return ToolExecutionResult(
                tool="create_notion_page",
                ok=True,
                dry_run=False,
                request=payload,
                response={"id": page_id, "url": body.get("url", "")},
                resource_key=page_id,
                resource_url=body.get("url", ""),
            )
        except Exception as exc:
            log.warning("notion.create_page_failed", title=title[:60], error=str(exc))
            return ToolExecutionResult(
                tool="create_notion_page",
                ok=False,
                dry_run=False,
                request=payload,
                error=f"{type(exc).__name__}: {exc}",
            )

    def create_page_tool(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return self.create_page(
            title=str(arguments.get("title", "Engagement")),
            content_markdown=str(arguments.get("content_markdown", "")),
            parent_page_id=arguments.get("parent_page_id"),
        )


# --------------------------------------------------------------------------------------
# Markdown → Notion blocks
# --------------------------------------------------------------------------------------


def _rich_text(content: str) -> list[dict[str, Any]]:
    """Build a rich_text array, honouring inline ``**bold**`` spans."""
    if not content:
        return []
    parts: list[dict[str, Any]] = []
    buffer = ""
    index = 0
    while index < len(content):
        if content.startswith("**", index):
            closing = content.find("**", index + 2)
            if closing != -1:
                if buffer:
                    parts.append({"type": "text", "text": {"content": buffer}})
                    buffer = ""
                parts.append(
                    {
                        "type": "text",
                        "text": {"content": content[index + 2 : closing]},
                        "annotations": {"bold": True},
                    }
                )
                index = closing + 2
                continue
        buffer += content[index]
        index += 1
    if buffer:
        parts.append({"type": "text", "text": {"content": buffer}})
    return parts or [{"type": "text", "text": {"content": content}}]


def markdown_to_notion_blocks(markdown: str, *, max_blocks: int = 100) -> list[dict[str, Any]]:
    """Convert a Markdown subset into Notion block objects.

    Supported: ``#``/``##``/``###`` headings, ``- ``/``* `` bullets, ``> `` quotes,
    ``---`` dividers and plain paragraphs. Anything else becomes a paragraph, which
    Notion renders acceptably.
    """
    blocks: list[dict[str, Any]] = []

    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()

        if stripped.startswith("### "):
            blocks.append(
                {"object": "block", "type": "heading_3",
                 "heading_3": {"rich_text": _rich_text(stripped[4:])}}
            )
        elif stripped.startswith("## "):
            blocks.append(
                {"object": "block", "type": "heading_2",
                 "heading_2": {"rich_text": _rich_text(stripped[3:])}}
            )
        elif stripped.startswith("# "):
            blocks.append(
                {"object": "block", "type": "heading_1",
                 "heading_1": {"rich_text": _rich_text(stripped[2:])}}
            )
        elif stripped.startswith(("- ", "* ", "• ")):
            blocks.append(
                {"object": "block", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": _rich_text(stripped[2:])}}
            )
        elif stripped.startswith("> "):
            blocks.append(
                {"object": "block", "type": "quote",
                 "quote": {"rich_text": _rich_text(stripped[2:])}}
            )
        elif stripped in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            blocks.append(
                {"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": _rich_text(stripped)}}
            )

        if len(blocks) >= max_blocks:
            break

    return blocks
