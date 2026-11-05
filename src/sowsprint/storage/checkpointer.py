"""The graph's checkpoint store, chosen by the same URL as everything else.

R2.2 asks for a saved LangGraph checkpointer bound to a stable `run_id`. The mechanism is
LangGraph's; what this module adds is the decision of *which* saver to build, made in one place
from one setting so the offline example keeps its no-server mode.

The checkpointer matters more than it looks. `interrupt()` is how the application asks a human a
question, and it works by raising out of the node and waiting. Without a durable saver the
question exists only in the process's memory: restart the container while a contract is waiting
for approval and the run is gone — not failed, gone, with no record that anyone was ever asked.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

#: One stack per process, so a saver's connection is opened once rather than per run.
_stack = ExitStack()


def build_checkpointer(url: str) -> Any:
    """Return a durable checkpointer for `url`, or an in-memory one when asked for it.

    `memory://` is a supported value, not a fallback. CI and the offline example use it on
    purpose: a test that needs a file to exist is a test that leaves files behind.
    """
    if not url or url == "memory://":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if url.startswith(("postgres://", "postgresql://")):
        from langgraph.checkpoint.postgres import PostgresSaver

        saver = _stack.enter_context(PostgresSaver.from_conn_string(url))
        # The tables are created idempotently; calling this on every boot is the documented
        # way and costs one round trip.
        saver.setup()
        return saver

    path = url.removeprefix("sqlite:///").removeprefix("sqlite://") or "checkpoints.db"
    if path == ":memory:":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the saver is shared by the request handlers of one process, and
    # SQLite's default would refuse a connection used from a thread other than the one that
    # opened it. WAL mode (set below) makes the concurrent access safe rather than merely
    # permitted.
    import sqlite3

    connection = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
    connection.execute("PRAGMA journal_mode=WAL")
    # SqliteSaver is not itself a context manager; it accepts a connection and closes it through
    # `__exit__` when it is used as one. Wrapping the connection instead keeps the handle open
    # for the life of the process, which is what the shared saver needs.
    saver = SqliteSaver(connection)
    _stack.callback(connection.close)
    return saver


def reset_checkpointer() -> None:
    """Close whatever the process opened. Used by tests to release file handles."""
    global _stack
    _stack.close()
    _stack = ExitStack()
