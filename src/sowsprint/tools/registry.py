"""Tool registry: function-calling schemas and dispatch.

Tools are declared once as :class:`ToolSpec` objects carrying a JSON-Schema parameter
block. From that single declaration the registry can emit:

* an OpenAI ``tools`` array,
* an Anthropic ``tools`` array,
* the prompt block the deterministic offline planner reads.

The optional ``llm`` dependency in the PRD ("automated LLM function parsing") is
therefore satisfied without hand-maintaining three parallel tool descriptions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Re-exported: callers have imported it from here since the beginning, and moving the
# definition is not a reason to break them.
from .types import ToolExecutionResult

__all__ = ["ToolExecutionResult", "ToolRegistry", "ToolSpec", "build_default_registry"]


@dataclass
class ToolSpec:
    """A callable integration exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolExecutionResult]
    category: str = "general"
    tags: list[str] = field(default_factory=list)

    def to_openai_function(self) -> dict[str, Any]:
        """OpenAI ``tools`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict[str, Any]:
        """Anthropic ``tools`` entry."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact form embedded in the offline planner's prompt."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# --------------------------------------------------------------------------------------
# Function-calling schemas
# --------------------------------------------------------------------------------------

CREATE_JIRA_PROJECT_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_key": {
            "type": "string",
            "description": "Uppercase Jira project key, 2-10 characters, e.g. AURORA.",
        },
        "name": {"type": "string", "description": "Human-readable project name."},
        "description": {
            "type": "string",
            "description": "Project description, typically the engagement executive summary.",
        },
    },
    "required": ["project_key", "name", "description"],
    "additionalProperties": False,
}

CREATE_JIRA_ISSUE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_key": {"type": "string", "description": "Target Jira project key."},
        "summary": {"type": "string", "description": "Issue title."},
        "description": {
            "type": "string",
            "description": "Full issue body including acceptance criteria.",
        },
        "issue_type": {
            "type": "string",
            "enum": ["Epic", "Story", "Task", "Bug", "Subtask"],
            "description": "Jira issue type.",
        },
        "story_points": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "Estimate in story points.",
        },
        "priority": {
            "type": "string",
            "enum": ["Highest", "High", "Medium", "Low", "Lowest"],
        },
        "labels": {"type": "array", "items": {"type": "string"}},
        "parent_key": {
            "type": "string",
            "description": (
                "Parent epic key, required when issue_type is Story or Subtask. Jira "
                "generates epic keys during this run, so reference the epic by the "
                "client_ref you assigned it; the executor substitutes the real key."
            ),
        },
        "client_ref": {
            "type": "string",
            "description": (
                "Caller-side correlation id, NOT sent to Jira. Assign a stable value "
                "(e.g. the blueprint epic key) to any issue that later calls must "
                "reference as parent_key."
            ),
        },
    },
    "required": ["project_key", "summary", "issue_type"],
    "additionalProperties": False,
}

CREATE_NOTION_PAGE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Page title."},
        "content_markdown": {
            "type": "string",
            "description": "Page body in Markdown (headings, bullets and paragraphs).",
        },
        "parent_page_id": {
            "type": "string",
            "description": "Parent Notion page id; defaults to the configured workspace page.",
        },
    },
    "required": ["title", "content_markdown"],
    "additionalProperties": False,
}

UPDATE_JIRA_ISSUE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issue_key": {"type": "string", "description": "Issue key such as AURORA-12."},
        "fields": {
            "type": "object",
            "description": "Jira fields to update, e.g. {\"labels\": [\"compliance\"]}.",
        },
    },
    "required": ["issue_key", "fields"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


class ToolRegistry:
    """Name → :class:`ToolSpec` lookup with provider-specific projections."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolSpec]:
        return [self._tools[name] for name in self.names()]

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [spec.to_openai_function() for spec in self.all()]

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        return [spec.to_anthropic_tool() for spec in self.all()]

    def to_prompt_dicts(self) -> list[dict[str, Any]]:
        return [spec.to_prompt_dict() for spec in self.all()]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Invoke a tool, converting any failure into a structured result."""
        spec = self.get(name)
        if spec is None:
            return ToolExecutionResult(
                tool=name,
                ok=False,
                error=f"Unknown tool '{name}'. Known tools: {', '.join(self.names())}",
            )
        try:
            return spec.handler(arguments)
        except Exception as exc:
            return ToolExecutionResult(
                tool=name,
                ok=False,
                request=arguments,
                error=f"{type(exc).__name__}: {exc}",
            )


def build_default_registry() -> ToolRegistry:
    """Registry wired to the configured Jira and Notion connectors."""
    from ..config import get_settings
    from .jira import JiraConnector
    from .notion import NotionConnector

    settings = get_settings()
    jira = JiraConnector(settings)
    notion = NotionConnector(settings)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="create_jira_project_workspace",
            description=(
                "Create the Jira project that will host the engagement backlog. "
                "Call this once before creating any issues."
            ),
            parameters=CREATE_JIRA_PROJECT_PARAMS,
            handler=jira.create_project_tool,
            category="jira",
            tags=["tracker", "workspace"],
        )
    )
    registry.register(
        ToolSpec(
            name="create_jira_issue",
            description=(
                "Create one Jira issue (Epic, Story, Task or Bug). Set parent_key to link "
                "a story to its epic."
            ),
            parameters=CREATE_JIRA_ISSUE_PARAMS,
            handler=jira.create_issue_tool,
            category="jira",
            tags=["tracker", "backlog"],
        )
    )
    registry.register(
        ToolSpec(
            name="update_jira_issue",
            description="Patch fields on an existing Jira issue.",
            parameters=UPDATE_JIRA_ISSUE_PARAMS,
            handler=jira.update_issue_tool,
            category="jira",
            tags=["tracker"],
        )
    )
    registry.register(
        ToolSpec(
            name="create_notion_page",
            description=(
                "Create a Notion page containing the engagement summary and contract "
                "overview for stakeholders."
            ),
            parameters=CREATE_NOTION_PAGE_PARAMS,
            handler=notion.create_page_tool,
            category="notion",
            tags=["documentation"],
        )
    )
    return registry
