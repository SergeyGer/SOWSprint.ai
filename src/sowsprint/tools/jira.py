"""Jira REST API v3 connector.

Two operating modes, selected by configuration and credential presence:

* **live** — real REST calls against ``jira_base_url`` using basic auth with an API
  token. Required when ``dry_run_integrations`` is false and all credentials are set.
* **dry-run** (default) — payloads are built and validated exactly as in live mode but
  never transmitted. This is what makes the tool-calling path demonstrable and
  testable without a Jira tenant, and the UI surfaces the exact JSON that *would* have
  been sent, which is how integration payloads get reviewed before go-live.

Every call returns a :class:`~sowsprint.tools.registry.ToolExecutionResult`, so a
single failing issue never aborts the batch.
"""

from __future__ import annotations

import base64
from typing import Any

from ..config import Settings, get_settings
from ..observability.logging import get_logger
from .types import ToolExecutionResult

log = get_logger(__name__)


class JiraConnector:
    """Thin, dependency-light Jira Cloud client."""

    #: Fallback id for Story Points. Most tenants use it, but not all — the field is
    #: absent on free plans and some team-managed projects, and writing an unknown
    #: custom field makes Jira reject the whole issue with a 400.
    DEFAULT_STORY_POINTS_FIELD = "customfield_10016"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._counter = 0
        self._fields: dict[str, str] | None = None
        self._account_id: str = ""

    # ------------------------------------------------------------------ mode
    @property
    def dry_run(self) -> bool:
        return self.settings.dry_run_integrations or not self.settings.jira_configured

    @property
    def mode(self) -> str:
        return "dry-run" if self.dry_run else "live"

    def _auth_header(self) -> dict[str, str]:
        raw = f"{self.settings.jira_email}:{self.settings.jira_api_token}".encode()
        return {
            "Authorization": f"Basic {base64.b64encode(raw).decode()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue an authenticated request to the Jira REST API."""
        import httpx

        base = (self.settings.jira_base_url or "").rstrip("/")
        url = f"{base}{path}"
        with httpx.Client(timeout=self.settings.request_timeout_s) as client:
            response = client.request(
                method, url, headers=self._auth_header(), json=payload
            )
            response.raise_for_status()
            if not response.content:
                return {"status": response.status_code}
            return response.json()

    # ------------------------------------------------------------------ field discovery
    def field_map(self) -> dict[str, str]:
        """Map well-known field names to this tenant's ids, discovering once.

        Jira field ids are per-tenant for custom fields, and a custom field may not
        exist at all. Discovering them up front turns a mid-run 400 into an informed
        decision to omit the field.
        """
        if self._fields is not None:
            return self._fields
        if self.dry_run:
            self._fields = {}
            return self._fields
        try:
            fields = self._request("GET", "/rest/api/3/field")
            self._fields = {
                str(field.get("name")): str(field.get("id"))
                for field in fields
                if field.get("name") and field.get("id")
            }
            log.info(
                "jira.fields_discovered",
                count=len(self._fields),
                story_points=self._fields.get("Story Points", "absent"),
            )
        except Exception as exc:
            log.warning("jira.field_discovery_failed", error=str(exc))
            self._fields = {}
        return self._fields

    def account_id(self) -> str:
        """The authenticated user's Atlassian ``accountId``, resolved once and cached.

        Jira Cloud does not accept an email or username as a project lead. It requires
        an opaque account id, and omitting it fails project creation with

            400 {"errors": {"projectLead": "You must select an active project lead."}}

        which names a field the request never contained — so the message alone does not
        lead to the fix. The value is stable for the credential, hence the cache.
        """
        if self._account_id:
            return self._account_id
        if self.dry_run:
            self._account_id = "dry-run-account"
            return self._account_id
        try:
            payload = self._request("GET", "/rest/api/3/myself")
            self._account_id = str(payload.get("accountId") or "")
            log.info("jira.account_id_resolved", account_id=self._account_id[:12] + "…")
        except Exception as exc:
            log.warning("jira.account_id_failed", error=str(exc))
            self._account_id = ""
        return self._account_id

    def story_points_field(self) -> str | None:
        """The tenant's Story Points field id, or ``None`` when it does not exist."""
        return self.field_map().get("Story Points")

    # ------------------------------------------------------------------ operations
    def create_project(self, project_key: str, name: str, description: str) -> ToolExecutionResult:
        """Create a Jira project (or simulate it in dry-run mode)."""
        payload: dict[str, Any] = {
            "key": project_key.upper()[:10],
            "name": name,
            "description": description[:1000],
            "projectTypeKey": "software",
        }
        # A project lead is mandatory, and the API wants an account id rather than the
        # email the older Server API accepted.
        lead = self.account_id()
        if lead:
            payload["leadAccountId"] = lead
        # The Scrum template needs Jira Software and project-create rights. Omitting it
        # still creates a usable project; the epics and stories are created the same
        # way either way, so a tenant without the template degrades rather than failing.
        if self.settings.jira_project_template:
            payload["projectTemplateKey"] = self.settings.jira_project_template
        if self.dry_run:
            return ToolExecutionResult(
                tool="create_jira_project_workspace",
                ok=True,
                dry_run=True,
                request={"method": "POST", "path": "/rest/api/3/project", "body": payload},
                response={"key": payload["key"], "self": f"{self.settings.jira_base_url or 'https://jira.local'}/rest/api/3/project/{payload['key']}"},
                resource_key=payload["key"],
                resource_url=f"{self.settings.jira_base_url or 'https://jira.local'}/projects/{payload['key']}",
            )
        try:
            response = self._request("POST", "/rest/api/3/project", payload)
            key = response.get("key", payload["key"])
            return ToolExecutionResult(
                tool="create_jira_project_workspace",
                ok=True,
                dry_run=False,
                request=payload,
                response=response,
                resource_key=key,
                resource_url=response.get("self", ""),
            )
        except Exception as exc:
            log.warning("jira.create_project_failed", key=payload["key"], error=str(exc))
            return ToolExecutionResult(
                tool="create_jira_project_workspace",
                ok=False,
                dry_run=False,
                request=payload,
                error=f"{type(exc).__name__}: {exc}",
            )

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Story",
        story_points: int | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        parent_key: str | None = None,
    ) -> ToolExecutionResult:
        """Create one Jira issue."""
        fields: dict[str, Any] = {
            "project": {"key": project_key.upper()},
            "summary": summary[:255],
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = _to_adf(description)
        if priority:
            fields["priority"] = {"name": priority}
        if labels:
            fields["labels"] = [label.lower().replace(" ", "-")[:255] for label in labels][:20]
        if story_points is not None:
            # Only write the field if this tenant actually has it. Sending an unknown
            # custom field rejects the entire issue, so an absent Story Points field
            # must cost the estimate, not the ticket.
            points_field = self.story_points_field() or self.DEFAULT_STORY_POINTS_FIELD
            if self.dry_run or points_field in self.field_map().values():
                fields[points_field] = story_points
            else:
                log.info(
                    "jira.story_points_field_absent",
                    project=project_key,
                    detail="this tenant has no 'Story Points' field; the estimate is "
                    "omitted from the issue rather than rejecting it",
                )
        if parent_key:
            fields["parent"] = {"key": parent_key}

        payload = {"fields": fields}

        if self.dry_run:
            self._counter += 1
            synthetic_key = f"{project_key.upper()}-{self._counter}"
            return ToolExecutionResult(
                tool="create_jira_issue",
                ok=True,
                dry_run=True,
                request={"method": "POST", "path": "/rest/api/3/issue", "body": payload},
                response={"id": str(10000 + self._counter), "key": synthetic_key},
                resource_key=synthetic_key,
                resource_url=f"{self.settings.jira_base_url or 'https://jira.local'}/browse/{synthetic_key}",
            )

        try:
            response = self._request("POST", "/rest/api/3/issue", payload)
            key = response.get("key", "")
            return ToolExecutionResult(
                tool="create_jira_issue",
                ok=True,
                dry_run=False,
                request=payload,
                response=response,
                resource_key=key,
                resource_url=f"{(self.settings.jira_base_url or '').rstrip('/')}/browse/{key}",
            )
        except Exception as exc:
            log.warning("jira.create_issue_failed", summary=summary[:60], error=str(exc))
            return ToolExecutionResult(
                tool="create_jira_issue",
                ok=False,
                dry_run=False,
                request=payload,
                error=f"{type(exc).__name__}: {exc}",
            )

    def update_issue(self, issue_key: str, fields: dict[str, Any]) -> ToolExecutionResult:
        """Patch fields on an existing issue."""
        payload = {"fields": fields}
        if self.dry_run:
            return ToolExecutionResult(
                tool="update_jira_issue",
                ok=True,
                dry_run=True,
                request={"method": "PUT", "path": f"/rest/api/3/issue/{issue_key}", "body": payload},
                response={"status": 204},
                resource_key=issue_key,
            )
        try:
            response = self._request("PUT", f"/rest/api/3/issue/{issue_key}", payload)
            return ToolExecutionResult(
                tool="update_jira_issue",
                ok=True,
                dry_run=False,
                request=payload,
                response=response,
                resource_key=issue_key,
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool="update_jira_issue",
                ok=False,
                dry_run=False,
                request=payload,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------ tool handlers
    def create_project_tool(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return self.create_project(
            project_key=str(arguments.get("project_key", "SOW")),
            name=str(arguments.get("name", "Engagement")),
            description=str(arguments.get("description", "")),
        )

    def create_issue_tool(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return self.create_issue(
            project_key=str(arguments.get("project_key", "SOW")),
            summary=str(arguments.get("summary", "")),
            description=str(arguments.get("description", "")),
            issue_type=str(arguments.get("issue_type", "Story")),
            story_points=arguments.get("story_points"),
            priority=arguments.get("priority"),
            labels=list(arguments.get("labels") or []),
            parent_key=arguments.get("parent_key"),
        )

    def update_issue_tool(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return self.update_issue(
            issue_key=str(arguments.get("issue_key", "")),
            fields=dict(arguments.get("fields") or {}),
        )


def _to_adf(text: str) -> dict[str, Any]:
    """Convert plain text (with line breaks) into Atlassian Document Format.

    Jira Cloud's v3 API rejects plain strings for the ``description`` field, so every
    description must be wrapped in ADF. Markdown-ish ``## `` lines become headings and
    ``- `` lines become bullet items, which is enough structure for a story body.
    """
    content: list[dict[str, Any]] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if not bullets:
            return
        content.append(
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": item}]}]}
                    for item in bullets
                ],
            }
        )
        bullets.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush_bullets()
            continue
        if line.startswith("- "):
            bullets.append(line[2:].strip())
            continue
        flush_bullets()
        if line.startswith("## "):
            content.append(
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": line[3:]}]}
            )
        else:
            content.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})

    flush_bullets()
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": text or "-"}]}]
    return {"type": "doc", "version": 1, "content": content}
