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

#: P0 favours recoverability over write throughput: FULL means every COMMIT is
#: fsynced, so a crash cannot lose an acknowledged Event. Revisit in P7.
PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "FULL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
)


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open ``core.db`` with the frozen pragma set."""
    db_path = Path(path)
    if read_only:
        if not db_path.exists():
            raise errors.SchemaVersionUnsupported(f"database does not exist: {db_path}")
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    for name, value in PRAGMAS:
        conn.execute(f"PRAGMA {name}={value}")
    return conn


def apply_pragmas(conn: sqlite3.Connection) -> None:
    for name, value in PRAGMAS:
        conn.execute(f"PRAGMA {name}={value}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside ``BEGIN IMMEDIATE`` .. ``COMMIT``/``ROLLBACK``.

    IMMEDIATE takes the write lock up front so a mid-transaction busy failure
    cannot turn into a half-applied Event plus projection.
    """
    if conn.in_transaction:
        # Nested use would silently widen an outer transaction's blast radius.
        raise RuntimeError("nested transaction: use one transaction per write operation")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM core_meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO core_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
