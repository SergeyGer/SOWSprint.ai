"""Persistence for a run that has to survive the browser tab.

Release 2.1, R2.2 names five entities — Run, Revision, Decision, Operation, Event — and the
reason for each is visible in what goes wrong without it:

* **Run** is the handle a team returns to. Without it, closing the tab loses the work.
* **Revision** is what the contract *was* at a moment. Without it, "approved" is a boolean that
  cannot be checked against anything, because the document it referred to has moved on.
* **Decision** is who agreed to which revision, in which mode, at what time. Without it there is
  no answer to "who let this go out".
* **Operation** is each external write, keyed so a retry cannot duplicate it.
* **Event** is the narrative: who did what, when, and how it turned out.

Two design notes that matter more than the tables themselves.

**SQLite by default, Postgres for the pilot.** The specification says the offline example and CI
keep a mode without Postgres, and it is right to: a test suite that needs a database server is a
test suite people run less often. The same SQL is used for both, with a placeholder dialect, so
the pilot does not run code that CI never exercised.

**Timestamps are ISO-8601 text in UTC.** SQLite has no native timestamp type and Postgres
returns `datetime` objects for one, which means two code paths for every read. Storing text
removes the difference without losing anything: every comparison here is equality or ordering on
an ISO string, which sorts lexicographically because the format is fixed-width.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------

#: Statements are dialect-neutral on purpose. `AUTOINCREMENT`/`SERIAL` is avoided by using a
#: UUID primary key for events, and every column is TEXT, INTEGER or REAL, all of which mean the
#: same thing in both engines.
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id            TEXT PRIMARY KEY,
        title             TEXT NOT NULL DEFAULT '',
        owner_id          TEXT NOT NULL DEFAULT '',
        reviewer_id       TEXT NOT NULL DEFAULT '',
        status            TEXT NOT NULL DEFAULT 'running',
        stage             TEXT NOT NULL DEFAULT '',
        current_version   INTEGER NOT NULL DEFAULT 1,
        publication_mode  TEXT NOT NULL DEFAULT 'dry_run',
        jurisdiction      TEXT NOT NULL DEFAULT 'EU',
        brief             TEXT NOT NULL DEFAULT '',
        halt_reason       TEXT NOT NULL DEFAULT '',
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS revisions (
        run_id        TEXT NOT NULL,
        number        INTEGER NOT NULL,
        snapshot_hash TEXT NOT NULL,
        audit_passed  INTEGER,
        audit_report  TEXT NOT NULL DEFAULT '{}',
        plan_hash     TEXT NOT NULL DEFAULT '',
        mode          TEXT NOT NULL DEFAULT 'dry_run',
        destinations  TEXT NOT NULL DEFAULT '[]',
        artifacts     TEXT NOT NULL DEFAULT '[]',
        created_at    TEXT NOT NULL,
        PRIMARY KEY (run_id, number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        run_id        TEXT NOT NULL,
        revision      INTEGER NOT NULL,
        audit_id      TEXT NOT NULL DEFAULT '',
        snapshot_hash TEXT NOT NULL,
        plan_hash     TEXT NOT NULL DEFAULT '',
        mode          TEXT NOT NULL,
        actor_id      TEXT NOT NULL,
        decision      TEXT NOT NULL,
        note          TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL,
        PRIMARY KEY (run_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        run_id       TEXT NOT NULL,
        op_key       TEXT NOT NULL,
        tool         TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'pending',
        attempts     INTEGER NOT NULL DEFAULT 0,
        external_id  TEXT NOT NULL DEFAULT '',
        external_url TEXT NOT NULL DEFAULT '',
        error_code   TEXT NOT NULL DEFAULT '',
        updated_at   TEXT NOT NULL,
        PRIMARY KEY (run_id, op_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id   TEXT PRIMARY KEY,
        run_id     TEXT NOT NULL,
        actor_id   TEXT NOT NULL DEFAULT '',
        action     TEXT NOT NULL,
        version    INTEGER NOT NULL DEFAULT 0,
        result     TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_run ON events (run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_operations_run ON operations (run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_updated ON runs (updated_at)",
)


#: Tables in the order they must be deleted. Nothing references anything by foreign key — the
#: schema is portable and a foreign-key cascade behaves differently across the two engines — so
#: the order is explicit rather than implied.
DELETE_ORDER: tuple[str, ...] = ("events", "operations", "decisions", "revisions", "runs")


# --------------------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------------------


class OperationStatus(str, Enum):
    """Where one external write stands.

    `uncertain` is the important one: a call that timed out may or may not have created
    something. Recording it as failed invites a retry that duplicates, and recording it as
    succeeded hides an object that may not exist. R3 reconciles these; R2 only has to be able
    to store the state honestly.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    SKIPPED = "skipped"


class DecisionKind(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


def utcnow() -> str:
    """A fixed-width UTC timestamp. Sorts lexicographically, compares equal across engines."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class RunRecord:
    run_id: str
    title: str = ""
    owner_id: str = ""
    reviewer_id: str = ""
    status: str = "running"
    stage: str = ""
    current_version: int = 1
    publication_mode: str = "dry_run"
    jurisdiction: str = "EU"
    brief: str = ""
    halt_reason: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RevisionRecord:
    run_id: str
    number: int
    snapshot_hash: str
    audit_passed: bool | None = None
    audit_report: dict[str, Any] = field(default_factory=dict)
    plan_hash: str = ""
    mode: str = "dry_run"
    destinations: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)


@dataclass
class DecisionRecord:
    run_id: str
    revision: int
    snapshot_hash: str
    mode: str
    actor_id: str
    decision: str
    audit_id: str = ""
    plan_hash: str = ""
    note: str = ""
    created_at: str = field(default_factory=utcnow)


@dataclass
class OperationRecord:
    run_id: str
    op_key: str
    tool: str = ""
    status: str = OperationStatus.PENDING.value
    attempts: int = 0
    external_id: str = ""
    external_url: str = ""
    error_code: str = ""
    updated_at: str = field(default_factory=utcnow)


@dataclass
class EventRecord:
    run_id: str
    action: str
    actor_id: str = ""
    version: int = 0
    result: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utcnow)


# --------------------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------------------


class _Dialect(Protocol):
    """The part of SQL that differs between the two engines."""

    placeholder: str

    def connect(self) -> Any: ...


class _SQLiteDialect:
    placeholder = "?"

    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> Any:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        # WAL lets a reader work while a run is writing. The default rollback journal blocks
        # readers for the duration of every write, which in a UI that polls run state means
        # the dashboard stalls whenever the pipeline saves.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection


class _PostgresDialect:
    placeholder = "%s"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row)


class RunStore:
    """The application database: what happened, who agreed, what was written.

    Deliberately small. It stores rows and returns dataclasses; it does not decide anything.
    The publication policy lives in `policy.py` and reads these records rather than being
    reimplemented against them.
    """

    def __init__(self, dialect: _SQLiteDialect | _PostgresDialect) -> None:
        self._dialect = dialect
        self._is_postgres = isinstance(dialect, _PostgresDialect)
        self._init_schema()

    @classmethod
    def sqlite(cls, path: str) -> RunStore:
        return cls(_SQLiteDialect(path))

    @classmethod
    def postgres(cls, dsn: str) -> RunStore:
        return cls(_PostgresDialect(dsn))

    @classmethod
    def from_url(cls, url: str) -> RunStore:
        """`postgresql://…` or `sqlite:///path`. The pilot sets the former, CI the latter."""
        if url.startswith(("postgres://", "postgresql://")):
            return cls.postgres(url)
        return cls.sqlite(url.removeprefix("sqlite:///").removeprefix("sqlite://") or ":memory:")

    @property
    def backend(self) -> str:
        return "postgres" if self._is_postgres else "sqlite"

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        connection = self._dialect.connect()
        try:
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _sql(self, statement: str) -> str:
        if not self._is_postgres:
            return statement
        return statement.replace("?", "%s")

    def _init_schema(self) -> None:
        with self._cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)

    # ------------------------------------------------------------------ runs
    def save_run(self, run: RunRecord, *, touch: bool = True) -> None:
        """Insert or update a run.

        `touch` refreshes `updated_at` to now, which is what every save during a run wants. It
        is turned off when the timestamp is itself the data — importing a run, or restoring one
        whose age determines whether retention may delete it. Overwriting it there would make an
        old run look new and quietly exempt it from the retention policy.
        """
        if touch:
            run.updated_at = utcnow()
        columns = list(run.as_dict())
        values = [getattr(run, c) for c in columns]
        placeholders = ", ".join(self._dialect.placeholder for _ in columns)
        assignments = ", ".join(f"{c} = {self._dialect.placeholder}" for c in columns if c != "run_id")
        sql = self._sql(
            f"INSERT INTO runs ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT (run_id) DO UPDATE SET {assignments}"
        )
        with self._cursor() as cursor:
            cursor.execute(sql, [*values, *[getattr(run, c) for c in columns if c != "run_id"]])

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._cursor() as cursor:
            cursor.execute(self._sql("SELECT * FROM runs WHERE run_id = ?"), (run_id,))
            row = cursor.fetchone()
        return RunRecord(**dict(row)) if row else None

    def list_runs(self, *, owner_id: str = "", limit: int = 50) -> list[RunRecord]:
        sql = "SELECT * FROM runs"
        params: list[Any] = []
        if owner_id:
            sql += " WHERE owner_id = ? OR reviewer_id = ?"
            params += [owner_id, owner_id]
        sql += f" ORDER BY updated_at DESC LIMIT {int(limit)}"
        with self._cursor() as cursor:
            cursor.execute(self._sql(sql), tuple(params))
            rows = cursor.fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    # -------------------------------------------------------------- revisions
    def save_revision(self, revision: RevisionRecord) -> None:
        columns = [
            "run_id", "number", "snapshot_hash", "audit_passed", "audit_report",
            "plan_hash", "mode", "destinations", "artifacts", "created_at",
        ]
        values = [
            revision.run_id,
            revision.number,
            revision.snapshot_hash,
            None if revision.audit_passed is None else int(revision.audit_passed),
            json.dumps(revision.audit_report, sort_keys=True),
            revision.plan_hash,
            revision.mode,
            json.dumps(revision.destinations),
            json.dumps(revision.artifacts),
            revision.created_at,
        ]
        placeholders = ", ".join(self._dialect.placeholder for _ in columns)
        sql = self._sql(
            f"INSERT INTO revisions ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (run_id, number) DO UPDATE SET snapshot_hash = EXCLUDED.snapshot_hash"
        )
        with self._cursor() as cursor:
            cursor.execute(sql, values)

    def get_revision(self, run_id: str, number: int) -> RevisionRecord | None:
        with self._cursor() as cursor:
            cursor.execute(
                self._sql("SELECT * FROM revisions WHERE run_id = ? AND number = ?"),
                (run_id, number),
            )
            row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        data["audit_passed"] = None if data["audit_passed"] is None else bool(data["audit_passed"])
        data["audit_report"] = json.loads(data["audit_report"])
        data["destinations"] = json.loads(data["destinations"])
        data["artifacts"] = json.loads(data["artifacts"])
        return RevisionRecord(**data)

    # -------------------------------------------------------------- decisions
    def save_decision(self, decision: DecisionRecord) -> None:
        """One current decision per revision — the primary key enforces it.

        A second approval of the same revision replaces the first rather than adding to it,
        because two live decisions about one document is precisely the ambiguity R1.2 exists to
        remove. The earlier one remains in `events`.
        """
        columns = [
            "run_id", "revision", "audit_id", "snapshot_hash", "plan_hash",
            "mode", "actor_id", "decision", "note", "created_at",
        ]
        values = [getattr(decision, c) for c in columns]
        placeholders = ", ".join(self._dialect.placeholder for _ in columns)
        sql = self._sql(
            f"INSERT INTO decisions ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (run_id, revision) DO UPDATE SET "
            "actor_id = EXCLUDED.actor_id, decision = EXCLUDED.decision, "
            "snapshot_hash = EXCLUDED.snapshot_hash, created_at = EXCLUDED.created_at"
        )
        with self._cursor() as cursor:
            cursor.execute(sql, values)

    def get_decision(self, run_id: str, revision: int) -> DecisionRecord | None:
        with self._cursor() as cursor:
            cursor.execute(
                self._sql("SELECT * FROM decisions WHERE run_id = ? AND revision = ?"),
                (run_id, revision),
            )
            row = cursor.fetchone()
        return DecisionRecord(**dict(row)) if row else None

    # ------------------------------------------------------------- operations
    def record_operation(self, operation: OperationRecord) -> None:
        columns = [
            "run_id", "op_key", "tool", "status", "attempts",
            "external_id", "external_url", "error_code", "updated_at",
        ]
        values = [getattr(operation, c) for c in columns]
        placeholders = ", ".join(self._dialect.placeholder for _ in columns)
        sql = self._sql(
            f"INSERT INTO operations ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (run_id, op_key) DO UPDATE SET "
            "status = EXCLUDED.status, attempts = EXCLUDED.attempts, "
            "external_id = EXCLUDED.external_id, external_url = EXCLUDED.external_url, "
            "error_code = EXCLUDED.error_code, updated_at = EXCLUDED.updated_at"
        )
        with self._cursor() as cursor:
            cursor.execute(sql, values)

    def list_operations(self, run_id: str) -> list[OperationRecord]:
        with self._cursor() as cursor:
            cursor.execute(
                self._sql("SELECT * FROM operations WHERE run_id = ? ORDER BY updated_at"),
                (run_id,),
            )
            rows = cursor.fetchall()
        return [OperationRecord(**dict(row)) for row in rows]

    # ----------------------------------------------------------------- events
    def add_event(self, event: EventRecord) -> None:
        columns = ["event_id", "run_id", "actor_id", "action", "version", "result", "created_at"]
        values = [getattr(event, c) for c in columns]
        placeholders = ", ".join(self._dialect.placeholder for _ in columns)
        with self._cursor() as cursor:
            cursor.execute(
                self._sql(f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})"),
                values,
            )

    def list_events(self, run_id: str, limit: int = 200) -> list[EventRecord]:
        with self._cursor() as cursor:
            cursor.execute(
                self._sql(
                    "SELECT * FROM events WHERE run_id = ? ORDER BY created_at DESC "
                    f"LIMIT {int(limit)}"
                ),
                (run_id,),
            )
            rows = cursor.fetchall()
        return [EventRecord(**dict(row)) for row in rows]

    # ---------------------------------------------------------------- deletion
    def delete_run(self, run_id: str) -> int:
        """R2.3: deletion covers the records. The caller removes the files and checkpoints.

        Deliberately not a cascade at the database level — the same call has to work on both
        engines, and an operator deleting a run wants to know exactly which tables were touched
        before they are.
        """
        removed = 0
        with self._cursor() as cursor:
            for table in DELETE_ORDER:
                cursor.execute(self._sql(f"DELETE FROM {table} WHERE run_id = ?"), (run_id,))
                removed += cursor.rowcount or 0
        return removed
