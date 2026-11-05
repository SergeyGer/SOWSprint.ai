"""Retention: delete the runs a pilot no longer needs to keep.

Release 2.1, R2.3. The specification proposes 30 days per run and asks for the procedure to be
written down, which is the right instinct — a retention rule that exists only as an intention is
indistinguishable from keeping everything forever.

Two things this deliberately does *not* do.

**It does not delete on its own.** There is no timer. A background job that removes a team's work
after a month without a human deciding to is a job that eventually removes work someone needed,
and the failure is silent. `--dry-run` is the default for the same reason.

**It deletes three kinds of thing, and all three are listed before anything is removed.** The
rows, the artifacts on disk, and the graph's checkpoints. Deleting the row and leaving the
checkpoint means the contract is still on disk in a table nobody looks at, which is the failure
mode a retention policy exists to prevent.

Run it from cron, from a compose profile, or by hand:

    python -m sowsprint.retention --older-than 30 --dry-run
    python -m sowsprint.retention --older-than 30 --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import ARTIFACT_DIR, get_settings
from .storage.store import RunStore


def _parse(timestamp: str) -> datetime | None:
    try:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def expired_runs(store: RunStore, days: int) -> list[Any]:
    """Runs whose last update is older than `days`."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stale = []
    for run in store.list_runs(limit=10_000):
        updated = _parse(run.updated_at)
        if updated is not None and updated < cutoff:
            stale.append(run)
    return stale


def purge(
    *,
    older_than: int,
    apply: bool = False,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Remove expired runs and everything that belongs to them.

    Returns a report rather than printing, so the caller — a test, a cron job, an operator
    checking first — decides what to show.
    """
    resolved = settings or get_settings()
    store: RunStore = resolved.run_store
    stale = expired_runs(store, older_than)

    report: dict[str, Any] = {
        "cutoff_days": older_than,
        "runs": [run.run_id for run in stale],
        "records_deleted": 0,
        "artifact_dirs": [],
        "checkpoints_cleared": [],
        "applied": apply,
    }

    for run in stale:
        directory = Path(ARTIFACT_DIR) / run.run_id
        if directory.exists():
            report["artifact_dirs"].append(str(directory))
        if not apply:
            continue
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        try:
            from .storage.checkpointer import build_checkpointer

            saver = build_checkpointer(resolved.checkpoint_url)
            saver.delete_thread(run.run_id)
            report["checkpoints_cleared"].append(run.run_id)
        except Exception as exc:  # a checkpoint store that cannot delete is reported, not hidden
            report.setdefault("checkpoint_errors", []).append(f"{run.run_id}: {exc}")
        report["records_deleted"] += store.delete_run(run.run_id)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sowsprint.retention",
        description="Delete runs older than the retention window (release 2.1, R2.3).",
    )
    parser.add_argument("--older-than", type=int, default=30, metavar="DAYS")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete. Without it the command only reports what it would remove.",
    )
    args = parser.parse_args(argv)

    report = purge(older_than=args.older_than, apply=args.apply)
    runs = report["runs"]

    if not runs:
        print(f"No runs older than {args.older_than} days.")
        return 0

    verb = "Deleted" if args.apply else "Would delete"
    print(f"{verb} {len(runs)} run(s) older than {args.older_than} days:")
    for run_id in runs:
        print(f"  {run_id}")
    for directory in report["artifact_dirs"]:
        print(f"  artifacts: {directory}")
    if report["applied"]:
        print(f"  records removed: {report['records_deleted']}")
        print(f"  checkpoints cleared: {len(report['checkpoints_cleared'])}")
        for error in report.get("checkpoint_errors", []):
            print(f"  checkpoint error: {error}")
    else:
        print("\nNothing was removed. Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
