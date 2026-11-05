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
from ..storage.store import OperationStatus
from .journal import Journal, operation_key
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
    #: R3.4: what happened to each operation, bucketed so a reader can tell "created by this
    #: run" from "already existed" from "we do not know". A single created/failed pair cannot
    #: express the third, and the third is the one that needs a human.
    journal: Any | None = None

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

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        dry_run: bool = True,
        run_id: str = "",
        store: Any | None = None,
    ) -> None:
        self.registry = registry
        #: R3: the journal keys every operation by run. A default keeps standalone use possible
        #: without pretending the keys are meaningful across runs.
        self.run_id = run_id or "run-standalone"
        self.store = store
        # The executor must know the mode, not infer it from the adapters. It inferred
        # nothing before — `dry_run` stayed at its default of True while the adapters read the
        # setting themselves, so the layer that decides whether to write had no idea whether
        # writing was allowed.
        self.dry_run = dry_run

    def execute(
        self, plan: ToolCallPlan, *, decision: Any | None = None, run_id: str = ""
    ) -> IntegrationReport:
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
        journal = Journal(run_id or self.run_id, store=self.store)
        report.journal = journal

        for index, call in enumerate(plan.calls, start=1):
            arguments = dict(call.arguments)

            # Resolve any reference pointing at a key generated earlier in this run.
            for argument_name in self.REFERENCE_ARGUMENTS:
                reference = arguments.get(argument_name)
                if isinstance(reference, str) and reference in report.key_map:
                    arguments[argument_name] = report.key_map[reference]

            key = operation_key(self.run_id, call.tool, call.arguments, index)
            step = journal.claim(key, call.tool, arguments=call.arguments, ordinal=index)

            # R3.3: a dependent call whose predecessor did not succeed is not attempted. Running
            # it would either fail at the destination or succeed against the wrong parent.
            unsatisfied = self._unsatisfied_dependency(call, arguments, report)
            if unsatisfied:
                journal.blocked(step, blocker=unsatisfied)
                report.results.append(
                    ToolExecutionResult(
                        tool=call.tool,
                        ok=False,
                        request=arguments,
                        error=f"blocked: {unsatisfied} did not succeed",
                        error_code=OperationStatus.SKIPPED.value,
                    )
                )
                continue

            result = self.registry.execute(call.tool, arguments)
            self._record_outcome(journal, step, result)
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

    def _unsatisfied_dependency(
        self, call: Any, arguments: dict[str, Any], report: IntegrationReport
    ) -> str:
        """Name the object this call needs that was never created.

        Only `parent_key` counts. It is the one argument that refers to something an earlier call
        in *this batch* had to create — R3.3's "epic before issue". `project_key` looks similar
        and is not: on an issue it names a destination that normally already exists, and blocking
        on it would refuse every plan that writes into a project it did not create this time.

        A `parent_key` still holding a planner-side label after resolution refers to an epic that
        was never made. Reporting that as a blocked dependency is more useful than letting the
        destination reject the field — the reader learns which object is missing rather than
        which argument was malformed.
        """
        reference = call.arguments.get("parent_key")
        if not isinstance(reference, str) or not reference:
            return ""
        if arguments.get("parent_key") != reference:
            return ""  # resolved to a real key
        if reference in report.key_map:
            return ""
        return f"parent_key={reference}"

    def _record_outcome(self, journal: Journal, step: Any, result: ToolExecutionResult) -> None:
        """Translate one tool result into a journal verdict.

        The distinction that matters: a timeout is *not* a failure. The destination may have
        accepted the request and lost the response, and recording that as failed is what invites
        the retry that duplicates it.
        """
        if result.ok:
            if result.resource_key:
                journal.succeeded(
                    step, external_id=result.resource_key, external_url=result.resource_url
                )
            else:
                journal.succeeded(step)
            return

        error = (result.error or "").lower()
        if any(token in error for token in ("timeout", "timed out", "read timeout", "connect")):
            journal.uncertain(step, error_code="timeout")
        elif "exists" in error or "duplicate" in error:
            # The destination already has it. That is success for this batch's purposes.
            journal.already_present(step)
        else:
            journal.failed(step, error_code=(result.error or "error")[:80], detail=result.error or "")

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
