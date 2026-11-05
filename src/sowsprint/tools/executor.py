"""Tool-call execution and reporting.

Execution is strictly sequential and failure-isolated: one rejected issue must not
abort the remaining backlog. Results are collected so the UI can show, per call, both
the request payload and the response — which is what makes dry-run mode a genuine
review tool rather than a stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..observability.logging import get_logger
from .planner import ToolCallPlan
from .registry import ToolExecutionResult, ToolRegistry

log = get_logger(__name__)


@dataclass
class IntegrationReport:
    """Aggregated outcome of one tool-call batch."""

    target: str = "jira"
    dry_run: bool = True
    results: list[ToolExecutionResult] = field(default_factory=list)
    #: Maps a blueprint epic key to the Jira key it was created under, so stories can
    #: be linked even when the planner could not know the generated key in advance.
    key_map: dict[str, str] = field(default_factory=dict)
    #: Set when the batch was refused before anything was sent. A refused report is not a
    #: failed one: nothing was attempted, and the reason is a policy decision rather than an
    #: error from a destination. The interface says "blocked", not "failed", because those
    #: send a reader to different places.
    refused_reason: str = ""
    refused_code: str | None = None

    @property
    def refused(self) -> bool:
        return bool(self.refused_code) and not self.results

    @property
    def created(self) -> list[ToolExecutionResult]:
        return [result for result in self.results if result.ok]

    @property
    def failed(self) -> list[ToolExecutionResult]:
        return [result for result in self.results if not result.ok]

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def summary(self) -> str:
        mode = "dry-run" if self.dry_run else "live"
        if not self.results:
            return "No tool calls were planned."
        return (
            f"{self.created_count} call(s) succeeded, {self.failed_count} failed "
            f"against {self.target} ({mode})."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "dry_run": self.dry_run,
            "summary": self.summary(),
            "created": [
                {
                    "tool": result.tool,
                    "key": result.resource_key,
                    "url": result.resource_url,
                }
                for result in self.created
            ],
            "failed": [
                {"tool": result.tool, "error": result.error} for result in self.failed
            ],
        }


class ToolExecutor:
    """Runs a planned tool-call batch through the registry."""

    #: Argument names whose values may be a caller-side reference that must be
    #: rewritten to a real resource key once the referenced call has executed.
    #: Arguments whose value may be a reference to a key generated earlier in the run.
    #:
    #: `project_key` belongs here because workspace creation may allocate a different key
    #: from the one the planner proposed — a second run for the same client steps over the
    #: taken key rather than writing into the first run's project. Without this, every
    #: issue would still name the proposed key and fail on a project that does not exist.
    REFERENCE_ARGUMENTS = ("parent_key", "project_key")

    def __init__(self, registry: ToolRegistry, *, dry_run: bool = True) -> None:
        self.registry = registry
        # The executor must know the mode, not infer it from the adapters. It inferred
        # nothing before — `dry_run` stayed at its default of True while the adapters read the
        # setting themselves, so the layer that decides whether to write had no idea whether
        # writing was allowed.
        self.dry_run = dry_run

    def execute(self, plan: ToolCallPlan, *, decision: Any | None = None) -> IntegrationReport:
        """Execute every planned call in order, isolating failures.

        Release 2.1: a live batch requires a publication decision that allows it.

        The check is here, in the one place every write passes through, rather than in the
        graph. A rule in the router is a rule the CLI, the API and a resumed checkpoint each
        have to remember; this one cannot be routed around, because routing around it means
        not calling the executor at all.

        Dry-run is unaffected — it validates and sends nothing, so there is nothing to gate.


        Key resolution: a caller cannot know the key Jira will generate for an epic
        that does not exist yet, so the planner tags each issue with a ``client_ref``
        and later calls reference that ref as ``parent_key``. As each call succeeds the
        mapping ``client_ref -> real key`` is recorded and substituted into subsequent
        arguments. Without this, a live run would file every story against a
        non-existent parent.
        """
        if not self.dry_run:
            from ..policy import PublicationMode, evaluate_publication

            resolved = decision
            if resolved is None:
                # No decision supplied means nobody established that publishing is allowed.
                # Defaulting to "go ahead" is exactly the defect this release exists to fix.
                resolved = evaluate_publication(mode=PublicationMode.LIVE, audit_passed=None)
            if not resolved.allowed:
                log.warning(
                    "tools.publication_blocked",
                    code=getattr(resolved.code, "value", None),
                    reason=resolved.reason,
                )
                blocked = IntegrationReport()
                blocked.refused_reason = resolved.reason
                blocked.refused_code = getattr(resolved.code, "value", None)
                return blocked

        report = IntegrationReport()

        for index, call in enumerate(plan.calls, start=1):
            arguments = dict(call.arguments)

            # Resolve any reference pointing at a key generated earlier in this run.
            for argument_name in self.REFERENCE_ARGUMENTS:
                reference = arguments.get(argument_name)
                if isinstance(reference, str) and reference in report.key_map:
                    arguments[argument_name] = report.key_map[reference]

            result = self.registry.execute(call.tool, arguments)
            report.results.append(result)

            if result.ok and result.resource_key:
                # Index the new key by every identifier a later call could use to
                # reference it: the explicit client_ref, the summary, and the project
                # key for workspace creation.
                identifiers = [
                    call.arguments.get("client_ref"),
                    call.arguments.get("summary"),
                    call.arguments.get("title"),
                ]
                if call.tool == "create_jira_project_workspace":
                    identifiers.append(call.arguments.get("project_key"))
                    report.target = "jira"
                if call.tool == "create_notion_page":
                    report.target = "jira+notion"

                for identifier in identifiers:
                    if isinstance(identifier, str) and identifier:
                        report.key_map.setdefault(identifier, result.resource_key)

            log.info(
                "tools.executed",
                index=index,
                tool=call.tool,
                ok=result.ok,
                dry_run=result.dry_run,
                key=result.resource_key,
                client_ref=call.arguments.get("client_ref", ""),
                resolved_parent=arguments.get("parent_key", ""),
            )

        if report.results:
            report.dry_run = all(result.dry_run for result in report.results)
        return report

    def preview(self, plan: ToolCallPlan) -> list[dict[str, Any]]:
        """Describe the batch without executing it, for the approval prompt."""
        return [
            {
                "index": index,
                "tool": call.tool,
                "summary": call.arguments.get("summary")
                or call.arguments.get("title")
                or call.arguments.get("project_key", ""),
                "issue_type": call.arguments.get("issue_type", ""),
                "parent_key": call.arguments.get("parent_key", ""),
                "story_points": call.arguments.get("story_points"),
                "rationale": call.rationale,
            }
            for index, call in enumerate(plan.calls, start=1)
        ]
