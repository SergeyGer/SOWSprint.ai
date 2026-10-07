"""External tool automation: function-calling registry, planners and connectors."""

from __future__ import annotations

from .executor import IntegrationReport, ToolExecutor
from .jira import JiraConnector
from .notion import NotionConnector, markdown_to_notion_blocks
from .planner import PlannedToolCall, ToolCallPlan, plan_tool_calls
from .registry import (
    CREATE_JIRA_ISSUE_PARAMS,
    CREATE_JIRA_PROJECT_PARAMS,
    CREATE_NOTION_PAGE_PARAMS,
    UPDATE_JIRA_ISSUE_PARAMS,
    ToolExecutionResult,
    ToolRegistry,
    ToolSpec,
    build_default_registry,
)

__all__ = [
    "CREATE_JIRA_ISSUE_PARAMS",
    "CREATE_JIRA_PROJECT_PARAMS",
    "CREATE_NOTION_PAGE_PARAMS",
    "UPDATE_JIRA_ISSUE_PARAMS",
    "IntegrationReport",
    "JiraConnector",
    "NotionConnector",
    "PlannedToolCall",
    "ToolCallPlan",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolRegistry",
    "ToolSpec",
    "build_default_registry",
    "markdown_to_notion_blocks",
    "plan_tool_calls",
]
