"""Whether this run may write to Jira and Notion, decided on the server.

Release 2.1 exists because of one defect: a contract whose own audit had failed could be
provisioned. The routing made it possible — when the repair budget ran out, `after_critic` went
to `plan_tools` regardless of the verdict, and `auto_deploy` went from there straight to the
tool executor, past the approval gate.

The fix is not another branch in the router. A rule that lives in the graph is a rule every
other entry point has to remember: the CLI, the API, a script, a resumed checkpoint. So the
decision is a function here, the executor refuses to write without it, and the interface only
renders it.

Three things must all be true before anything is created externally:

1. **The audit passed.** Not "ran" — passed. A missing report, a fallback judge or a failed
   re-audit are each a block, with a different reason so the reader knows what to fix.
2. **The decision is bound to this version.** A reviewer who approved version 3 has not
   approved version 4. The snapshot hash is what makes that enforceable rather than aspirational.
3. **The mode allows it.** Live is off unless an operator enabled it; dry-run is always allowed,
   because validating a plan and sending nothing is the default behaviour of the product.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PublicationMode(str, Enum):
    """What the run is allowed to do with its plan."""

    DRY_RUN = "dry_run"
    LIVE = "live"


class BlockCode(str, Enum):
    """Why publication is refused. Stable strings: they appear in logs and in the interface."""

    AUDIT_FAILED = "audit_failed"
    AUDIT_MISSING = "audit_missing"
    AUDIT_DEGRADED = "audit_degraded"
    VERSION_STALE = "version_stale"
    NOT_APPROVED = "not_approved"
    LIVE_DISABLED = "live_disabled"
    ALREADY_COMPLETED = "already_completed"


#: Human-readable text for each refusal. The interface prints these verbatim, so they are
#: written as instructions rather than as error names.
REASONS: dict[BlockCode, str] = {
    BlockCode.AUDIT_FAILED: (
        "The quality audit failed. Fix the blocking findings and run the audit again — "
        "nothing has been sent to Jira or Notion."
    ),
    BlockCode.AUDIT_MISSING: (
        "The run has no audit report, so there is no verdict to act on. Re-run the audit "
        "before publishing."
    ),
    BlockCode.AUDIT_DEGRADED: (
        "The audit did not run — it fell back to a degraded path. Its findings cannot be "
        "trusted, so publishing is blocked until a real audit succeeds."
    ),
    BlockCode.VERSION_STALE: (
        "This contract changed after it was approved. The approval covered an earlier "
        "version; review the differences and approve the current one."
    ),
    BlockCode.NOT_APPROVED: (
        "This version has not been approved. A reviewer has to confirm this exact version "
        "before anything is created externally."
    ),
    BlockCode.LIVE_DISABLED: (
        "Live publishing is disabled on this deployment. Dry-run validates every call and "
        "sends nothing; an operator enables live for specific destinations."
    ),
    BlockCode.ALREADY_COMPLETED: (
        "This run has already been provisioned. Re-provisioning would create duplicates."
    ),
}


def canonical_json(payload: Any) -> str:
    """A stable serialisation, so the same document always hashes to the same value.

    Sorted keys and no whitespace: two dictionaries with the same content must not produce
    different hashes merely because they were assembled in a different order.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


#: Fields that must never enter the hash.
#:
#: A snapshot identifies *content*. A timestamp changes every time the document is rewritten
#: without a word of it changing, and including one would make every approval stale the moment
#: it was granted. Export bytes are excluded for the same reason — a PDF embeds a creation date
#: and a producer string, and hashing the file would compare format noise rather than substance.
VOLATILE_FIELDS: frozenset[str] = frozenset(
    {
        "generated_at",
        "created_at",
        "updated_at",
        "revision",
        "run_id",
        "session_id",
        "attempt",
        "cost",
        "duration_ms",
        "timestamp",
    }
)


def _strip_volatile(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _strip_volatile(v) for k, v in payload.items() if k not in VOLATILE_FIELDS}
    if isinstance(payload, list):
        return [_strip_volatile(item) for item in payload]
    return payload


@dataclass(frozen=True)
class Snapshot:
    """The exact thing a reviewer approved.

    R1.2: the decision binds to run id, version number, the hash of the canonical SOW and
    backlog, the audit identifier, the plan hash and the destinations. The hash covers content
    only, so an approval survives a re-render and does not survive an edit.
    """

    digest: str
    short: str
    version: int
    audit_id: str
    plan_hash: str
    destinations: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def build(
        cls,
        *,
        sow: dict[str, Any] | None,
        backlog: dict[str, Any] | None,
        plan: Any,
        destinations: list[str] | tuple[str, ...],
        version: int,
        audit_id: str = "",
    ) -> Snapshot:
        # Destinations are hashed, not merely stored. R1.2 names them as part of what the
        # decision covers, and a plan that grew a Notion call while the approval was granted
        # for Jira alone is a different commitment — the reviewer agreed to write in one place.
        resolved_destinations = sorted(str(d) for d in (destinations or ()))
        material = canonical_json(
            _strip_volatile(
                {
                    "sow": sow or {},
                    "backlog": backlog or {},
                    "plan": plan or {},
                    "destinations": resolved_destinations,
                }
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        plan_digest = hashlib.sha256(canonical_json(_strip_volatile(plan or {})).encode()).hexdigest()
        return cls(
            digest=digest,
            short=digest[:12],
            version=version,
            audit_id=audit_id,
            plan_hash=plan_digest[:12],
            destinations=tuple(resolved_destinations),
        )


@dataclass
class Decision:
    """The verdict on whether this run may write, and why.

    `allowed` is about *publishing*, not about running: a blocked run still has a contract, a
    backlog and an audit to read. That distinction is the whole point — the earlier behaviour
    withheld nothing, it published.
    """

    allowed: bool
    code: BlockCode | None = None
    reason: str = ""
    snapshot: Snapshot | None = None
    mode: PublicationMode = PublicationMode.DRY_RUN

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code.value if self.code else None,
            "reason": self.reason,
            "mode": self.mode.value,
            "snapshot": self.snapshot.short if self.snapshot else None,
            "version": self.snapshot.version if self.snapshot else None,
        }


def evaluate_publication(
    *,
    mode: PublicationMode | str,
    audit_passed: bool | None,
    audit_degraded: bool = False,
    approved: bool = False,
    approved_snapshot: str | None = None,
    current_snapshot: Snapshot | None = None,
    completed: bool = False,
) -> Decision:
    """Decide whether this run may create objects outside the system.

    Order matters: the reasons are checked from the most fundamental to the most specific, so
    the returned reason names the first thing that has to be true rather than the last thing
    that happened to be checked.
    """
    resolved_mode = mode if isinstance(mode, PublicationMode) else PublicationMode(str(mode))

    if resolved_mode is PublicationMode.DRY_RUN:
        # Dry-run is never blocked. It writes nothing, and refusing to validate a plan would
        # remove the one operation that is safe by construction.
        return Decision(allowed=True, mode=resolved_mode, snapshot=current_snapshot)

    if audit_degraded:
        return _blocked(BlockCode.AUDIT_DEGRADED, resolved_mode, current_snapshot)
    if audit_passed is None:
        return _blocked(BlockCode.AUDIT_MISSING, resolved_mode, current_snapshot)
    if not audit_passed:
        return _blocked(BlockCode.AUDIT_FAILED, resolved_mode, current_snapshot)

    if completed:
        return _blocked(BlockCode.ALREADY_COMPLETED, resolved_mode, current_snapshot)

    if not approved:
        return _blocked(BlockCode.NOT_APPROVED, resolved_mode, current_snapshot)

    # The approval names a snapshot. If the document moved, the approval did not.
    if approved_snapshot and current_snapshot and approved_snapshot != current_snapshot.digest:
        return _blocked(BlockCode.VERSION_STALE, resolved_mode, current_snapshot)

    return Decision(allowed=True, mode=resolved_mode, snapshot=current_snapshot)


def _blocked(code: BlockCode, mode: PublicationMode, snapshot: Snapshot | None) -> Decision:
    return Decision(allowed=False, code=code, reason=REASONS[code], snapshot=snapshot, mode=mode)
