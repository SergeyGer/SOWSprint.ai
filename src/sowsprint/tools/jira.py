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


class JiraRequestError(RuntimeError):
    """A Jira call that failed, described in Jira's own words.

    ``raise_for_status`` produces ``Client error '400 Bad Request' for url ...``, which
    names the status and nothing else. Jira puts the reason in the response body, and that
    body is the difference between "the integration is broken" and "this project key is
    held by a project in the trash — pick another or purge it".

    The first live run of this integration failed 22 calls in a row and every log line said
    only ``400 Bad Request``. The information needed to fix it in a minute was sitting in
    the response the whole time.
    """

    def __init__(self, method: str, path: str, response: Any) -> None:
        self.status = response.status_code
        self.detail = _explain_response(response)
        super().__init__(f"{method} {path} -> HTTP {self.status}: {self.detail}")


def _explain_response(response: Any) -> str:
    """Pull the human-readable reason out of a Jira error body."""
    try:
        body = response.json()
    except Exception:
        return (response.text or "").strip()[:400] or "(empty body)"

    parts = [str(m) for m in (body.get("errorMessages") or []) if m]
    parts += [f"{field}: {message}" for field, message in (body.get("errors") or {}).items()]
    if not parts:
        # Some endpoints report a bare message and some report nothing at all.
        single = body.get("message")
        parts = [str(single)] if single else [str(body)[:400]]
    return "; ".join(parts)


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
        #: Set once the start-date lookup has failed, so the retry runs once per connector
        #: rather than once per issue.
        self._start_field_absent = False
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
        # `path` may carry a query string; joining keeps it intact.
        url = f"{base}{path}"
        with httpx.Client(timeout=self.settings.request_timeout_s) as client:
            response = client.request(
                method, url, headers=self._auth_header(), json=payload
            )
            if response.status_code >= 400:
                raise JiraRequestError(method, path, response)
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
                # The start-date field is a custom field whose id varies by tenant, and
                # it is what makes a Jira timeline show a bar rather than a point. Absent
                # on many tenants, hence the discovery rather than a hard-coded id.
                start_date=self.start_date_field() or "absent",
            )
        except Exception as exc:
            log.warning("jira.field_discovery_failed", error=str(exc))
            self._fields = {}
        return self._fields

    def start_date_field(self) -> str:
        """The tenant's Start date field id, or an empty string if it has none.

        Re-discovers once when the field is absent from a cached map. Field discovery has
        been observed returning a partial list — 55 fields where the same tenant reports
        86 moments later, immediately after a project is created — and the consequence is
        silent: the start date is dropped and an undated issue on a Jira timeline reads as
        a deliberate choice rather than a lookup that came back short.

        A tenant genuinely without the field pays one extra request per session, which is
        the right trade against undated backlogs.
        """
        names = ("Start date", "Start Date", "Target start", "Planned start")
        for name in names:
            if name in self.field_map():
                return self.field_map()[name]

        # A negative result is cached. Without that, every issue in a backlog repeats the
        # retry, and the sleeps compound into minutes — the test suite went from twenty
        # seconds to seven and a half before this line existed.
        if self._start_field_absent:
            return ""

        # Nothing to retry in dry-run: no field is ever written, and the retry would add
        # its sleeps to every test that exercises issue creation.
        if self.dry_run:
            self._start_field_absent = True
            return ""

        # Retry with a pause. Jira has been observed reporting 55 fields where it reports
        # 87 seconds later — same tenant, same credentials, same request — so the
        # truncation outlasts an immediate retry. Three attempts over three seconds ride
        # it out.
        import time

        for attempt in range(3):
            time.sleep(0.5 * (attempt + 1))
            self._fields = None
            fields = self.field_map()
            for name in names:
                if name in fields:
                    log.info(
                        "jira.fields_rediscovered",
                        attempt=attempt + 1,
                        count=len(fields),
                        field=fields[name],
                    )
                    return fields[name]
        self._start_field_absent = True
        log.warning("jira.start_date_field_unavailable", attempts=3)
        return ""

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
        # Discover the field catalogue BEFORE creating the project. Jira scopes
        # `/rest/api/3/field` to a project context, and immediately after a project is
        # created it reports 55 fields where it otherwise reports 87 — dropping "Target
        # start", which is exactly the field the schedule needs. The result is cached, so
        # reading it first costs one request and makes every later call see the full list.
        #
        # Without this the run produced thirty issues with due dates and no start dates:
        # a timeline showing when work is wanted but not when it begins.
        self.field_map()

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
        if not self.dry_run:
            # Every run provisions its own workspace. A key already held — by a live
            # project or one in the trash, which reserves it just the same — is stepped
            # over with a numeric suffix. The executor rewrites the project key in every
            # later call from the key Jira actually returned, so the issues follow.
            requested = payload["key"]
            requested_name = payload["name"]
            payload["key"] = self.free_project_key(requested)
            payload["name"] = self.free_project_name(
                requested_name, self.settings.resolved_project_start.strftime("%d %b %Y")
            )
            if payload["key"] != requested or payload["name"] != requested_name:
                log.info(
                    "jira.project_identity_adjusted",
                    requested_key=requested,
                    allocated_key=payload["key"],
                    requested_name=requested_name,
                    allocated_name=payload["name"],
                )

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
                error=f"{type(exc).__name__}: {exc}{self._key_held_by_trash_hint(payload['key'])}",
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
        start_date: str = "",
        due_date: str = "",
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
        # `duedate` is a standard field on every tenant. The start date is not, so it is
        # set only when the tenant has one — a missing field would otherwise turn the
        # whole issue creation into a 400 and lose the backlog over a decoration.
        if due_date:
            fields["duedate"] = due_date
        start_field = self.start_date_field()
        if start_date and start_field:
            fields[start_field] = start_date
        elif start_date:
            # Field discovery has been observed returning a partial list — 55 fields where
            # a moment later the same tenant reports 86 — immediately after a project is
            # created. The start date is then dropped, and an undated issue on a timeline
            # looks like a deliberate choice rather than a lookup that came back short.
            log.warning(
                "jira.start_date_field_missing",
                summary=summary[:60],
                detail="the tenant reported no Start date field; the issue is undated",
            )

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
    def free_project_key(self, preferred: str) -> str:
        """The preferred key if it is free, otherwise the first free variant.

        Every run provisions a NEW project. That is what a reader of the transcript
        expects — "this engagement produced this workspace" — and it is the only behaviour
        that makes two runs comparable, since a shared project accumulates issues from
        both and answers neither question.

        A Jira key is reserved for the lifetime of the project, including while it sits in
        the trash, so the check has to include deleted projects. The key must also stay
        within Jira's rules: two to ten characters, starting with a letter.
        """
        import re

        base = re.sub(r"[^A-Z0-9]", "", preferred.upper()) or "SOW"
        if not base[0].isalpha():
            base = f"S{base}"
        base = base[:8]

        taken = self._taken_keys()
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base[: 10 - len(str(suffix))]}{suffix}"
            if candidate not in taken:
                return candidate
        return base[:10]

    def free_project_name(self, preferred: str, start: str = "") -> str:
        """A project name Jira will accept.

        The key being free is not enough: Jira requires the NAME to be unique across every
        live project as well, and the two are checked independently. A second run for the
        same client therefore failed with `projectName: a project with this name already
        exists` while its key was perfectly available — the error named the name, and the
        uniqueness logic only handled the key.

        The start date is the natural disambiguator: two engagements for one client are
        distinguished by when they begin, and a reader sees that in the project list.
        """
        taken = self._taken_names()
        if preferred not in taken:
            return preferred

        suffix = f" — {start}" if start else ""
        candidate = f"{preferred[: 120 - len(suffix)]}{suffix}"
        if candidate not in taken:
            return candidate

        for index in range(2, 100):
            tag = f" ({index})"
            candidate = f"{preferred[: 120 - len(tag)]}{tag}"
            if candidate not in taken:
                return candidate
        return preferred[:120]

    def _taken_names(self) -> set[str]:
        """Every project name in use, including archived and trashed ones."""
        taken: set[str] = set()
        for status in ("live,archived", "deleted"):
            try:
                found = self._request(
                    "GET", f"/rest/api/3/project/search?status={status}&maxResults=100"
                )
            except Exception:
                continue
            for project in found.get("values", []) if isinstance(found, dict) else []:
                if project.get("name"):
                    taken.add(str(project["name"]))
        return taken

    def _taken_keys(self) -> set[str]:
        """Every key this tenant has used, including projects in the trash."""
        taken: set[str] = set()
        for status in ("live,archived", "deleted"):
            try:
                found = self._request(
                    "GET", f"/rest/api/3/project/search?status={status}&maxResults=100"
                )
            except Exception:
                continue
            for project in found.get("values", []) if isinstance(found, dict) else []:
                if project.get("key"):
                    taken.add(str(project["key"]).upper())
        return taken

    def find_project(self, project_key: str) -> dict[str, Any] | None:
        """The live project with this key, or None.

        Only live projects count. A key held by a project in the trash cannot be reused —
        Jira keeps the key reserved and refuses to create over it — so returning the
        trashed project here would turn a clear failure into a confusing one.
        """
        if self.dry_run:
            return None
        try:
            found = self._request(
                "GET",
                "/rest/api/3/project/search",
                None,
            )
        except Exception:
            return None
        for project in found.get("values", []) if isinstance(found, dict) else []:
            if str(project.get("key", "")).upper() == project_key.upper():
                return project
        return None

    def _key_held_by_trash_hint(self, project_key: str) -> str:
        """Explain a taken key that no visible project owns.

        This is worth spelling out because the state is invisible in the Jira UI's project
        list: deleting a project moves it to the trash, the key stays reserved, and every
        later create fails with 'this key is already in use' while the list shows nothing
        with that key.
        """
        try:
            trashed = self._request(
                "GET", "/rest/api/3/project/search?status=deleted&maxResults=100"
            )
        except Exception:
            return ""
        for project in trashed.get("values", []) if isinstance(trashed, dict) else []:
            if str(project.get("key", "")).upper() == project_key.upper():
                return (
                    f" — the key is held by a deleted project ({project.get('name')!r}) in the"
                    " Jira trash. Purge it in Jira (Project settings → Delete → permanently)"
                    " or set SOWSPRINT_JIRA_PROJECT_KEY to a free key."
                )
        return ""

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
            start_date=str(arguments.get("start_date") or ""),
            due_date=str(arguments.get("due_date") or ""),
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
