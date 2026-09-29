"""SQLite connection setup and the transaction wrapper (ADR 0002).

There is exactly one place that opens the database and exactly one place that
opens a write transaction, so the WAL / foreign key / durability settings and
the BEGIN IMMEDIATE discipline cannot drift between call sites.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import errors

BUSY_TIMEOUT_MS = 5000

#: P0 favours recoverability over write throughput: FULL means every COMMIT is
#: fsynced, so a crash cannot lose an acknowledged Event. Revisit in P7.
#: `recursive_triggers` is not optional: without it SQLite skips DELETE triggers
#: during REPLACE conflict resolution, which would let `INSERT OR REPLACE` rewrite
#: a Raw Event past the append-only guard.
PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "FULL"),
    ("foreign_keys", "ON"),
    ("recursive_triggers", "ON"),
    ("busy_timeout", str(BUSY_TIMEOUT_MS)),
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open ``core.db`` with the frozen pragma set."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    for name, value in PRAGMAS:
        conn.execute(f"PRAGMA {name}={value}")
    return conn


def is_locked(exc: BaseException) -> bool:
    """True when the failure is write-lock contention, not a data error."""
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


@contextmanager
def translate_lock_errors() -> Iterator[None]:
    """Surface write-lock contention as :class:`errors.DatabaseBusy`.

    ADR 0003 freezes stable machine codes for every client-visible failure;
    SQLite's raw ``OperationalError: database is locked`` is not one of them, so
    the store layer runs its writes under this guard.
    """
    try:
        yield
    except sqlite3.OperationalError as exc:
        if is_locked(exc):
            raise errors.DatabaseBusy(
                "another writer held the database lock past busy_timeout; nothing was written",
                busy_timeout_ms=BUSY_TIMEOUT_MS,
            ) from exc
        raise


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside ``BEGIN IMMEDIATE`` .. ``COMMIT``/``ROLLBACK``.

    IMMEDIATE takes the write lock up front so a mid-transaction busy failure
    cannot turn into a half-applied Event plus projection.

    The rollback covers the COMMIT itself: this schema defers its Event ->
    object foreign keys, so a violated reference is only reported when the
    transaction commits. Leaving that case un-rolled-back would expose a
    half-written Event plus projection and wedge the connection.
    """
    if conn.in_transaction:
        # Nested use would silently widen an outer transaction's blast radius.
        raise RuntimeError("nested transaction: use one transaction per write operation")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM core_meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO core_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
