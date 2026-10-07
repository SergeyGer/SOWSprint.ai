"""Tests for the tool-calling layer.

Covers the registry (registration, schema projections, failure isolation), the two
connectors in dry-run mode (which is the default and the mode the platform boots in
without credentials), the plan/execute pipeline and the planner's validation schema.

No network access is performed anywhere: every connector test pins
``dry_run_integrations=True`` and credentials are either absent or paired with an
unroutable base URL, so a live call would fail loudly instead of silently passing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sowsprint.config import Settings, VectorBackend
from sowsprint.tools import (
    CREATE_JIRA_ISSUE_PARAMS,
    CREATE_JIRA_PROJECT_PARAMS,
    CREATE_NOTION_PAGE_PARAMS,
    UPDATE_JIRA_ISSUE_PARAMS,
    JiraConnector,
    NotionConnector,
    PlannedToolCall,
    ToolCallPlan,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    build_default_registry,
    markdown_to_notion_blocks,
)
from sowsprint.tools.jira import _to_adf

#: Hostname reserved by RFC 2606; any attempt to reach it fails immediately.
UNROUTABLE = "https://jira.invalid"


# ---------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------


def make_settings(**overrides) -> Settings:
    """Explicit, credential-free settings so ambient env vars cannot leak in."""
    base: dict = {
        "vector_backend": VectorBackend.MEMORY,
        "dry_run_integrations": True,
        "llm_provider": "mock",
        "anthropic_api_key": None,
        "openai_api_key": None,
        "groq_api_key": None,
        "cohere_api_key": None,
        "jira_base_url": None,
        "jira_email": None,
        "jira_api_token": None,
        "notion_api_key": None,
        "notion_parent_page_id": None,
    }
    base.update(overrides)
    return Settings(**base)


def echo_spec(name: str = "echo") -> ToolSpec:
    def handler(arguments: dict) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool=name,
            ok=True,
            request=arguments,
            response={"echo": arguments.get("value")},
            resource_key=str(arguments.get("value", "")),
        )

    return ToolSpec(
        name=name,
        description="Echo the supplied value back to the caller.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        category="test",
        tags=["test"],
    )


@pytest.fixture
def jira() -> JiraConnector:
    return JiraConnector(make_settings())


@pytest.fixture
def notion() -> NotionConnector:
    return NotionConnector(make_settings())


def dry_run_registry() -> ToolRegistry:
    """A registry wired to dry-run connectors built from explicit settings."""
    connector = JiraConnector(make_settings())
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="create_jira_project_workspace",
            description="Create the Jira project.",
            parameters=CREATE_JIRA_PROJECT_PARAMS,
            handler=connector.create_project_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="create_jira_issue",
            description="Create one Jira issue.",
            parameters=CREATE_JIRA_ISSUE_PARAMS,
            handler=connector.create_issue_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="update_jira_issue",
            description="Patch fields on an existing Jira issue.",
            parameters=UPDATE_JIRA_ISSUE_PARAMS,
            handler=connector.update_issue_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="create_notion_page",
            description="Create a Notion page.",
            parameters=CREATE_NOTION_PAGE_PARAMS,
            handler=NotionConnector(make_settings()).create_page_tool,
        )
    )
    return registry


# ---------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------


def test_registry_register_get_names_and_execute():
    registry = ToolRegistry()
    assert registry.names() == []
    assert registry.get("echo") is None
    assert registry.all() == []

    spec = echo_spec()
    registry.register(spec)

    assert registry.get("echo") is spec
    assert registry.get("missing") is None
    assert registry.names() == ["echo"]
    assert registry.all() == [spec]

    result = registry.execute("echo", {"value": "hello"})
    assert isinstance(result, ToolExecutionResult)
    assert result.ok is True
    assert result.tool == "echo"
    assert result.resource_key == "hello"
    assert result.response == {"echo": "hello"}
    assert "hello" in result.summary_line()


def test_registry_names_are_sorted_and_register_is_last_write_wins():
    registry = ToolRegistry()
    registry.register(echo_spec("zulu"))
    registry.register(echo_spec("alpha"))
    registry.register(echo_spec("mike"))
    assert registry.names() == ["alpha", "mike", "zulu"]

    replacement = echo_spec("alpha")
    registry.register(replacement)
    assert registry.names() == ["alpha", "mike", "zulu"]
    assert registry.get("alpha") is replacement


def test_execute_unknown_tool_returns_error_result_without_raising():
    registry = dry_run_registry()
    result = registry.execute("no_such_tool", {"project_key": "AURORA"})

    assert result.ok is False
    assert result.tool == "no_such_tool"
    assert result.error is not None
    assert "Unknown tool 'no_such_tool'" in result.error
    # The message must be actionable: it lists every name that *is* callable.
    for known in registry.names():
        assert known in result.error
    assert result.resource_key == ""


def test_execute_converts_handler_exception_into_failure_result():
    def exploding_handler(arguments: dict) -> ToolExecutionResult:  # pragma: no cover
        raise ValueError("kaboom in handler")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="boom",
            description="Always fails.",
            parameters={"type": "object", "properties": {}},
            handler=exploding_handler,
        )
    )

    result = registry.execute("boom", {"attempt": 1})

    assert result.ok is False
    assert result.tool == "boom"
    assert result.error == "ValueError: kaboom in handler"
    assert result.request == {"attempt": 1}


def test_execute_isolates_failures_across_calls():
    def exploding_handler(arguments: dict) -> ToolExecutionResult:  # pragma: no cover
        raise RuntimeError("nope")

    registry = ToolRegistry()
    registry.register(echo_spec("fine"))
    registry.register(
        ToolSpec(
            name="broken",
            description="Always fails.",
            parameters={"type": "object", "properties": {}},
            handler=exploding_handler,
        )
    )

    assert registry.execute("fine", {"value": "ok"}).ok is True
    assert registry.execute("broken", {}).ok is False
    # The registry is still usable after a handler blew up.
    assert registry.execute("fine", {"value": "again"}).ok is True


def test_schema_projections_have_provider_specific_shapes():
    registry = ToolRegistry()
    spec = echo_spec()
    registry.register(spec)

    openai_tools = registry.to_openai_tools()
    assert len(openai_tools) == 1
    entry = openai_tools[0]
    assert set(entry) == {"type", "function"}
    assert entry["type"] == "function"
    assert set(entry["function"]) == {"name", "description", "parameters"}
    assert entry["function"]["name"] == "echo"
    assert entry["function"]["parameters"] == spec.parameters

    anthropic_tools = registry.to_anthropic_tools()
    assert len(anthropic_tools) == 1
    entry = anthropic_tools[0]
    assert set(entry) == {"name", "description", "input_schema"}
    assert entry["input_schema"] == spec.parameters

    prompt_dicts = registry.to_prompt_dicts()
    assert prompt_dicts == [
        {
            "name": "echo",
            "description": spec.description,
            "parameters": spec.parameters,
        }
    ]


def test_schema_projections_follow_registry_order():
    registry = dry_run_registry()
    expected = registry.names()

    assert [entry["function"]["name"] for entry in registry.to_openai_tools()] == expected
    assert [entry["name"] for entry in registry.to_anthropic_tools()] == expected
    assert [entry["name"] for entry in registry.to_prompt_dicts()] == expected


def test_build_default_registry_exposes_the_four_documented_tools():
    registry = build_default_registry()
    assert registry.names() == [
        "create_jira_issue",
        "create_jira_project_workspace",
        "create_notion_page",
        "update_jira_issue",
    ]
    for spec in registry.all():
        assert spec.handler is not None
        assert spec.parameters.get("type") == "object"
        assert spec.parameters.get("properties")


# ---------------------------------------------------------------------------------
# Jira connector (dry-run)
# ---------------------------------------------------------------------------------


def test_jira_create_project_dry_run_returns_payload_and_key(jira: JiraConnector):
    result = jira.create_project("aurora", "Aurora Data Platform", "Executive summary")

    assert result.ok is True
    assert result.dry_run is True
    assert result.resource_key == "AURORA"
    assert result.resource_url.endswith("/projects/AURORA")

    assert result.request["method"] == "POST"
    assert result.request["path"] == "/rest/api/3/project"
    body = result.request["body"]
    assert body["key"] == "AURORA"
    assert body["name"] == "Aurora Data Platform"
    assert body["description"] == "Executive summary"
    assert result.response["key"] == "AURORA"


def test_jira_project_key_is_uppercased_and_truncated(jira: JiraConnector):
    result = jira.create_project("lowercase", "Name", "Description")
    assert result.resource_key == "LOWERCASE"

    long_key = jira.create_project("a" * 25, "Name", "Description")
    assert long_key.resource_key == "A" * 10


def test_jira_create_issue_dry_run_key_sequence_parent_and_points(jira: JiraConnector):
    epic = jira.create_issue(
        "aurora",
        "Epic: onboarding",
        description="## Objective\nShip onboarding.",
        issue_type="Epic",
        labels=["Delivery"],
    )
    story = jira.create_issue(
        "aurora",
        "Story: sign-up form",
        description="## Story\nAs a user I want to sign up.",
        issue_type="Story",
        story_points=5,
        priority="High",
        labels=["Compliance Risk", "gdpr"],
        parent_key=epic.resource_key,
    )
    third = jira.create_issue("aurora", "Story: audit log", issue_type="Story")

    assert epic.resource_key == "AURORA-1"
    assert story.resource_key == "AURORA-2"
    assert third.resource_key == "AURORA-3"
    for result in (epic, story, third):
        assert result.ok is True
        assert result.dry_run is True
        assert result.resource_url.endswith(f"/browse/{result.resource_key}")
        assert result.request["method"] == "POST"
        assert result.request["path"] == "/rest/api/3/issue"

    story_fields = story.request["body"]["fields"]
    assert story_fields["project"] == {"key": "AURORA"}
    assert story_fields["summary"] == "Story: sign-up form"
    assert story_fields["issuetype"] == {"name": "Story"}
    assert story_fields["parent"] == {"key": "AURORA-1"}
    assert story_fields["customfield_10016"] == 5
    assert story_fields["priority"] == {"name": "High"}
    # Labels are normalised for the tracker.
    assert story_fields["labels"] == ["compliance-risk", "gdpr"]
    # Descriptions are wrapped in ADF, never sent as plain strings.
    assert story_fields["description"]["type"] == "doc"

    epic_fields = epic.request["body"]["fields"]
    assert epic_fields["issuetype"] == {"name": "Epic"}
    assert "parent" not in epic_fields
    assert "customfield_10016" not in epic_fields


def test_jira_update_issue_dry_run(jira: JiraConnector):
    result = jira.update_issue("AURORA-7", {"labels": ["compliance"], "priority": {"name": "High"}})

    assert result.ok is True
    assert result.dry_run is True
    assert result.resource_key == "AURORA-7"
    assert result.request["method"] == "PUT"
    assert result.request["path"] == "/rest/api/3/issue/AURORA-7"
    assert result.request["body"]["fields"]["labels"] == ["compliance"]
    assert result.response == {"status": 204}


def test_jira_stays_in_dry_run_when_credentials_are_present():
    configured = make_settings(
        dry_run_integrations=True,
        jira_base_url=UNROUTABLE,
        jira_email="delivery@example.com",
        jira_api_token="token",
    )
    assert configured.jira_configured is True

    connector = JiraConnector(configured)
    assert connector.dry_run is True
    assert connector.mode == "dry-run"

    # A real request would fail against the unroutable host; the dry-run payload is
    # returned instead, which proves nothing was transmitted.
    project = connector.create_project("aurora", "Aurora", "Description")
    assert project.ok is True
    assert project.dry_run is True
    assert project.request["body"]["key"] == "AURORA"

    issue = connector.create_issue_tool({"project_key": "AURORA", "summary": "One", "issue_type": "Story"})
    assert issue.ok is True
    assert issue.dry_run is True


def test_jira_goes_live_only_when_dry_run_is_disabled_and_credentials_exist():
    without_credentials = JiraConnector(make_settings(dry_run_integrations=False))
    assert without_credentials.dry_run is True
    assert without_credentials.mode == "dry-run"

    with_credentials = JiraConnector(
        make_settings(
            dry_run_integrations=False,
            jira_base_url=UNROUTABLE,
            jira_email="delivery@example.com",
            jira_api_token="token",
        )
    )
    assert with_credentials.dry_run is False
    assert with_credentials.mode == "live"


def test_to_adf_converts_markdown_description():
    description = (
        "## Objective\n"
        "Deliver the onboarding portal.\n"
        "\n"
        "- first bullet\n"
        "- second bullet\n"
        "\n"
        "## Notes\n"
        "A closing paragraph."
    )
    doc = _to_adf(description)

    assert doc["type"] == "doc"
    assert doc["version"] == 1
    content = doc["content"]
    assert [node["type"] for node in content] == [
        "heading",
        "paragraph",
        "bulletList",
        "heading",
        "paragraph",
    ]

    heading = content[0]
    assert heading["attrs"] == {"level": 2}
    assert heading["content"][0]["text"] == "Objective"
    assert content[1]["content"][0]["text"] == "Deliver the onboarding portal."

    bullets = content[2]
    assert [item["type"] for item in bullets["content"]] == ["listItem", "listItem"]
    assert [
        item["content"][0]["content"][0]["text"] for item in bullets["content"]
    ] == ["first bullet", "second bullet"]

    assert content[3]["content"][0]["text"] == "Notes"
    assert content[4]["content"][0]["text"] == "A closing paragraph."


def test_to_adf_always_returns_at_least_one_paragraph():
    assert _to_adf("")["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": "-"}]}
    ]
    plain = _to_adf("Just one line")
    assert [node["type"] for node in plain["content"]] == ["paragraph"]


# ---------------------------------------------------------------------------------
# Notion connector (dry-run)
# ---------------------------------------------------------------------------------


def test_notion_create_page_dry_run_returns_synthetic_id(notion: NotionConnector):
    result = notion.create_page("Engagement summary", "# Heading\n\nBody text.")

    assert result.ok is True
    assert result.dry_run is True
    assert result.resource_key == "notion-page-0001"
    assert result.resource_url == "https://notion.so/notion-page-0001"
    assert result.request["method"] == "POST"
    assert result.request["path"] == "/v1/pages"

    body = result.request["body"]
    assert body["parent"] == {"type": "page_id", "page_id": "workspace-root"}
    assert body["properties"]["title"][0]["text"]["content"] == "Engagement summary"
    assert [block["type"] for block in body["children"]] == ["heading_1", "paragraph"]

    second = notion.create_page("Another page", "Body")
    assert second.resource_key == "notion-page-0002"


def test_notion_create_page_honours_explicit_parent(notion: NotionConnector):
    result = notion.create_page("Child", "Body", parent_page_id="parent-123")
    assert result.request["body"]["parent"]["page_id"] == "parent-123"


def test_markdown_to_notion_blocks_maps_every_supported_construct():
    markdown = "\n".join(
        [
            "# Heading one",
            "## Heading two",
            "### Heading three",
            "- dash bullet",
            "* star bullet",
            "> a quote",
            "---",
            "A plain paragraph.",
            "",
        ]
    )
    blocks = markdown_to_notion_blocks(markdown)

    assert [block["type"] for block in blocks] == [
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "bulleted_list_item",
        "quote",
        "divider",
        "paragraph",
    ]
    for block in blocks:
        assert block["object"] == "block"

    assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "Heading one"
    assert blocks[1]["heading_2"]["rich_text"][0]["text"]["content"] == "Heading two"
    assert blocks[2]["heading_3"]["rich_text"][0]["text"]["content"] == "Heading three"
    assert blocks[3]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "dash bullet"
    assert blocks[4]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "star bullet"
    assert blocks[5]["quote"]["rich_text"][0]["text"]["content"] == "a quote"
    assert blocks[6]["divider"] == {}
    assert blocks[7]["paragraph"]["rich_text"][0]["text"]["content"] == "A plain paragraph."


def test_markdown_to_notion_blocks_marks_bold_spans_and_skips_blank_lines():
    blocks = markdown_to_notion_blocks("**Bold** and plain\n\n\n   \nSecond")
    assert len(blocks) == 2
    rich_text = blocks[0]["paragraph"]["rich_text"]
    assert rich_text[0]["text"]["content"] == "Bold"
    assert rich_text[0]["annotations"] == {"bold": True}
    assert rich_text[1]["text"]["content"] == " and plain"

    assert markdown_to_notion_blocks("") == []


def test_markdown_to_notion_blocks_stops_at_max_blocks():
    markdown = "\n".join(f"Paragraph number {index}" for index in range(10))

    assert len(markdown_to_notion_blocks(markdown)) == 10
    capped = markdown_to_notion_blocks(markdown, max_blocks=4)
    assert len(capped) == 4
    assert capped[-1]["paragraph"]["rich_text"][0]["text"]["content"] == "Paragraph number 3"


# ---------------------------------------------------------------------------------
# planner + executor
# ---------------------------------------------------------------------------------


def test_tool_call_plan_validates_a_list_of_call_dicts():
    calls = [
        {"tool": "create_jira_project_workspace", "arguments": {"project_key": "AURORA"}},
        {"tool": "create_jira_issue", "arguments": {"project_key": "AURORA"}, "rationale": "epic"},
        {"tool": "create_jira_issue", "arguments": {"project_key": "AURORA"}, "rationale": "story"},
    ]
    # This is exactly how the tools graph node builds its plan.
    plan = ToolCallPlan.model_validate({"summary": "Provision AURORA", "calls": calls})

    assert plan.summary == "Provision AURORA"
    assert len(plan.calls) == 3
    assert all(isinstance(call, PlannedToolCall) for call in plan.calls)
    assert plan.calls[1].rationale == "epic"
    assert plan.calls[0].rationale == ""
    assert plan.tool_histogram() == {"create_jira_project_workspace": 1, "create_jira_issue": 2}


def test_tool_call_plan_rejects_malformed_calls():
    with pytest.raises(ValidationError):
        ToolCallPlan.model_validate({"calls": [{"arguments": {}}]})

    with pytest.raises(ValidationError):
        # extra="forbid" keeps a hallucinated field from being silently dropped.
        ToolCallPlan.model_validate({"calls": [{"tool": "x", "unexpected": 1}]})


def test_executor_reports_created_and_failed_counts_and_summary():
    registry = dry_run_registry()
    plan = ToolCallPlan.model_validate(
        {
            "summary": "Provision the workspace",
            "calls": [
                {
                    "tool": "create_jira_project_workspace",
                    "arguments": {
                        "project_key": "AURORA",
                        "name": "Aurora Data Platform",
                        "description": "Executive summary",
                    },
                },
                {
                    "tool": "create_jira_issue",
                    "arguments": {
                        "project_key": "AURORA",
                        "summary": "Foundation epic",
                        "issue_type": "Epic",
                    },
                },
                {"tool": "totally_unknown_tool", "arguments": {"project_key": "AURORA"}},
            ],
        }
    )

    report = ToolExecutor(registry).execute(plan)

    assert report.failed_count == 1
    assert report.created_count == 2
    assert len(report.results) == 3
    assert report.dry_run is True

    summary = report.summary()
    assert f"{report.created_count} call(s) succeeded" in summary
    assert f"{report.failed_count} failed" in summary
    assert "dry-run" in summary

    failed = report.failed[0]
    assert failed.tool == "totally_unknown_tool"
    assert "Unknown tool 'totally_unknown_tool'" in (failed.error or "")

    payload = report.as_dict()
    assert payload["target"] == "jira"
    assert [entry["tool"] for entry in payload["created"]] == [
        "create_jira_project_workspace",
        "create_jira_issue",
    ]
    assert payload["failed"] == [
        {"tool": "totally_unknown_tool", "error": failed.error},
    ]
    assert payload["dry_run"] is True
    assert payload["summary"] == summary


def test_executor_rewrites_planned_parent_keys_onto_generated_keys():
    registry = dry_run_registry()
    plan = ToolCallPlan.model_validate(
        {
            "summary": "Epic then story",
            "calls": [
                {
                    "tool": "create_jira_issue",
                    "arguments": {
                        "project_key": "AURORA",
                        "summary": "Onboarding epic",
                        "issue_type": "Epic",
                    },
                },
                {
                    "tool": "create_jira_issue",
                    "arguments": {
                        "project_key": "AURORA",
                        "summary": "Sign-up story",
                        "issue_type": "Story",
                        # The planner can only reference the epic by its planned label.
                        "parent_key": "Onboarding epic",
                    },
                },
            ],
        }
    )

    report = ToolExecutor(registry).execute(plan)

    assert report.failed_count == 0
    epic, story = report.results
    assert epic.resource_key == "AURORA-1"
    assert story.resource_key == "AURORA-2"
    assert story.request["body"]["fields"]["parent"] == {"key": "AURORA-1"}
    assert report.key_map["Onboarding epic"] == "AURORA-1"


def test_executor_sets_jira_plus_notion_target_and_previews_calls():
    registry = dry_run_registry()
    executor = ToolExecutor(registry)
    plan = ToolCallPlan.model_validate(
        {
            "summary": "Provision",
            "calls": [
                {
                    "tool": "create_notion_page",
                    "arguments": {"title": "Engagement summary", "content_markdown": "# Hi"},
                    "rationale": "Stakeholder summary",
                }
            ],
        }
    )

    preview = executor.preview(plan)
    assert preview == [
        {
            "index": 1,
            "tool": "create_notion_page",
            "summary": "Engagement summary",
            "issue_type": "",
            "parent_key": "",
            "story_points": None,
            "rationale": "Stakeholder summary",
        }
    ]

    report = executor.execute(plan)
    assert report.created_count == 1
    assert report.target == "jira+notion"
    assert report.dry_run is True


def test_executor_on_empty_plan_returns_a_no_op_report():
    report = ToolExecutor(dry_run_registry()).execute(ToolCallPlan())

    assert report.results == []
    assert report.created_count == 0
    assert report.failed_count == 0
    assert report.summary() == "No tool calls were planned."
