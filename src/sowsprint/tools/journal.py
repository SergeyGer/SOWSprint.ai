"""The operation journal: what was attempted, what succeeded, what needs reconciling.

Release 2.1, R3. Two failures motivate every line here.

**A retry that duplicates.** A call times out after the destination has already accepted it. The
executor sees an error, the operator retries, and the second request creates a second epic. The
fix is not "be careful" — it is a key that is stable across attempts, claimed before the call and
checked on every retry.

**A result that cannot be read.** The run says "25 of 25" and nobody can tell whether those are
the objects this run created or a count of everything in the project. The fix is a summary that
separates *created by this run*, *skipped because it already existed*, and *unknown because the
call did not come back*.

The journal is deliberately boring: it records and it refuses to redo. It does not retry on its
own — an automatic retry of an operation whose outcome is unknown is precisely how duplicates get
made.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..storage.store import OperationRecord, OperationStatus, RunStore


class Outcome(str, Enum):
    """What one journal step concluded."""

    CREATED = "created"
    EXISTING = "existing"
    SKIPPED = "skipped"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


#: The marker written into every object this run creates.
#:
#: Duplicates are prevented at the destination, not only in our tables: a rerun that reaches Jira
#: must be able to find the object without trusting a local row that may not have been written.
#: The marker is derived from the run id and the operation key, so the same operation in the same
#: run always produces the same tag and a different run never collides with it.
MARKER_PREFIX = "sowsprint"


def operation_key(run_id: str, tool: str, arguments: dict[str, Any], ordinal: int) -> str:
    """A stable identity for one planned call.

    Built from what the call *does*, not from its position alone, so re-planning the same batch
    in the same order yields the same keys and a retry is recognised. `ordinal` disambiguates
    legitimate repeats — ten stories with identical arguments are ten operations, not one.
    """
    material = json.dumps(
        {"run": run_id, "tool": tool, "args": arguments, "n": ordinal},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def marker_for(run_id: str, key: str) -> str:
    """The tag that identifies this operation's object at the destination."""
    return f"{MARKER_PREFIX}-{run_id[-8:]}-{key[:8]}"


@dataclass
class Step:
    """One planned call and what became of it."""

    key: str
    tool: str
    ordinal: int
    outcome: Outcome = Outcome.SKIPPED
    external_id: str = ""
    external_url: str = ""
    error_code: str = ""
    detail: str = ""
    attempts: int = 0
    #: Set when this call could not run because something it depends on did not succeed.
    blocker: str = ""


@dataclass
class JournalSummary:
    """R3.4: the counts a reader can act on, per destination."""

    destination: str = ""
    created: list[Step] = field(default_factory=list)
    existing: list[Step] = field(default_factory=list)
    skipped: list[Step] = field(default_factory=list)
    uncertain: list[Step] = field(default_factory=list)
    failed: list[Step] = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def needs_reconciliation(self) -> bool:
        """True when at least one call may or may not have taken effect."""
        return bool(self.uncertain)

    def as_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "created": len(self.created),
            "existing": len(self.existing),
            "skipped": len(self.skipped),
            "uncertain": len(self.uncertain),
            "failed": len(self.failed),
            "needs_reconciliation": self.needs_reconciliation,
            "links": [s.external_url for s in self.created if s.external_url],
        }

    def summary_line(self) -> str:
        parts = [f"{self.created_count} created"]
        if self.existing:
            parts.append(f"{len(self.existing)} already present")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.uncertain:
            parts.append(f"{len(self.uncertain)} need reconciliation")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


class Journal:
    """Records each operation and refuses to repeat a completed one.

    Backed by the run store when one is supplied, and by memory otherwise, so the executor can be
    used in a test without a database — the same shape as the rest of the application, where the
    durable path is the default and the in-memory one is chosen explicitly.
    """

    def __init__(self, run_id: str, *, store: RunStore | None = None) -> None:
        self.run_id = run_id
        self.store = store
        self.steps: dict[str, Step] = {}
        self._pending: dict[str, OperationRecord] = {}

    # ------------------------------------------------------------------ lookup
    def outcome_of(self, key: str) -> Outcome | None:
        """What this key concluded on a previous attempt, if anything."""
        if key in self.steps:
            return self.steps[key].outcome
        if self.store is not None:
            for record in self.store.list_operations(self.run_id):
                if record.op_key == key and record.status == OperationStatus.SUCCEEDED.value:
                    return Outcome.EXISTING
        return None

    def stored_step(self, key: str) -> Step | None:
        """The recorded operation for `key`, reconstructed from the store.

        A process that restarted has an empty `steps` dictionary but a populated table. Without
        this, the executor could see that an operation had succeeded and still have no saved
        identifier to substitute, so the dependent calls would resolve against nothing.
        """
        if key in self.steps:
            return self.steps[key]
        if self.store is None:
            return None
        for record in self.store.list_operations(self.run_id):
            if record.op_key != key:
                continue
            if record.status != OperationStatus.SUCCEEDED.value:
                return None
            return Step(
                key=key,
                tool=record.tool,
                ordinal=0,
                outcome=Outcome.EXISTING,
                external_id=record.external_id,
                external_url=record.external_url,
            )
        return None

    def claim(self, key: str, tool: str, *, arguments: dict[str, Any], ordinal: int) -> Step:
        """Register an intent before anything is sent.

        Ordering is the point. If the process dies between the claim and the call, the journal
        says a call was attempted and its result is unknown — which is recoverable. Claiming
        afterwards would say nothing was attempted, and the retry would duplicate.
        """
        step = Step(key=key, tool=tool, ordinal=ordinal, outcome=Outcome.SKIPPED, attempts=1)
        self.steps[key] = step
        self._record(step, OperationStatus.PENDING, arguments)
        return step

    def succeeded(self, step: Step, *, external_id: str = "", external_url: str = "") -> Step:
        step.outcome = Outcome.CREATED
        step.external_id = external_id
        step.external_url = external_url
        self._record(step, OperationStatus.SUCCEEDED)
        return step

    def already_present(self, step: Step, *, external_id: str = "", external_url: str = "") -> Step:
        """The destination already had this object — a previous attempt, or a rerun.

        Recorded as success with the identifier found, not as a failure. The object exists and
        the dependent calls can proceed; calling it a failure would strand everything downstream.
        """
        step.outcome = Outcome.EXISTING
        step.external_id = external_id
        step.external_url = external_url
        self._record(step, OperationStatus.SUCCEEDED)
        return step

    def uncertain(self, step: Step, *, error_code: str = "timeout") -> Step:
        """The call did not come back. It may or may not have taken effect.

        This is the state the earlier behaviour could not express: it recorded a plain failure,
        the operator retried, and the destination ended up with two epics. Reconciliation has to
        look before it acts, so the state has to say "unknown" rather than "failed".
        """
        step.outcome = Outcome.UNCERTAIN
        step.error_code = error_code
        self._record(step, OperationStatus.UNCERTAIN)
        return step

    def failed(self, step: Step, *, error_code: str = "", detail: str = "") -> Step:
        step.outcome = Outcome.FAILED
        step.error_code = error_code
        step.detail = detail
        self._record(step, OperationStatus.FAILED)
        return step

    def blocked(self, step: Step, *, blocker: str) -> Step:
        """R3.3: a dependent call whose predecessor did not succeed is not attempted.

        Attempting it would either fail at the destination — a story against a project that was
        never created — or, worse, succeed against the wrong parent.
        """
        step.outcome = Outcome.SKIPPED
        step.blocker = blocker
        self._record(step, OperationStatus.SKIPPED)
        return step

    # ------------------------------------------------------------------ report
    def summary(self, destination: str = "") -> JournalSummary:
        summary = JournalSummary(destination=destination)
        buckets = {
            Outcome.CREATED: summary.created,
            Outcome.EXISTING: summary.existing,
            Outcome.SKIPPED: summary.skipped,
            Outcome.UNCERTAIN: summary.uncertain,
            Outcome.FAILED: summary.failed,
        }
        for step in sorted(self.steps.values(), key=lambda s: s.ordinal):
            buckets[step.outcome].append(step)
        return summary

    # -------------------------------------------------------------- persistence
    def _record(
        self,
        step: Step,
        status: OperationStatus,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        if self.store is None:
            return
        self.store.record_operation(
            OperationRecord(
                run_id=self.run_id,
                op_key=step.key,
                tool=step.tool,
                status=status.value,
                attempts=step.attempts,
                external_id=step.external_id,
                external_url=step.external_url,
                error_code=step.error_code,
            )
        )
