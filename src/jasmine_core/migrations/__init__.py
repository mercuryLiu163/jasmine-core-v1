"""Versioned, forward-only, checksummed schema migrations (ADR 0001 §2.3).

A migration module exposes ``NAME``, ``VERSION`` and ``STATEMENTS``. The
checksum is the SHA-256 of ``STATEMENTS``, recorded on apply, so an already
released migration cannot be silently edited: a mismatch aborts startup.

The whole run happens inside one ``BEGIN IMMEDIATE`` transaction, so a
failure half way through leaves the database exactly as it was found.
"""

from __future__ import annotations

import hashlib
import importlib
import pkgutil
from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Sequence

from .. import errors
from ..db import get_meta, set_meta, transaction

LOCK_KEY = "migration_lock"
LOCK_OWNER = "jasmine-core"


@dataclass(frozen=True)
class Migration:
    name: str
    version: int
    checksum: str
    statements: Sequence[str]


def _checksum(statements: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def discover() -> list[Migration]:
    """Import every ``mNNNN_*`` module here, ordered by version.

    Versions must be contiguous from 1; a gap means a migration was deleted,
    which the forward-only contract forbids.
    """
    found: list[Migration] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.name.startswith("m"):
            continue
        module: ModuleType = importlib.import_module(f"{__name__}.{info.name}")
        statements = tuple(module.STATEMENTS)
        if not statements:
            raise errors.MigrationConflict(f"migration {info.name} declares no STATEMENTS")
        found.append(
            Migration(
                name=module.NAME,
                version=int(module.VERSION),
                checksum=_checksum(statements),
                statements=statements,
            )
        )
    for index, migration in enumerate(found, start=1):
        if migration.version != index:
            raise errors.MigrationConflict(
                f"migration versions must be contiguous from 1: "
                f"position {index} holds version {migration.version}"
            )
    return found


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None

def applied_migrations(conn) -> list[dict]:
    return [
        {"name": row["name"], "version": row["version"], "checksum": row["checksum"],
         "applied_at": row["applied_at"]}
        for row in conn.execute(
            "SELECT name, version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
    ]


def current_version(conn) -> int:
    if not _table_exists(conn, "core_meta"):
        return 0
    return int(get_meta(conn, "schema_version", 0))


def expected_version() -> int:
    from .. import SCHEMA_VERSION

    return SCHEMA_VERSION


def check_version(conn) -> int:
    """Verify the on-disk schema can be used by this binary, else raise."""
    found = current_version(conn)
    expected = expected_version()
    if found > expected:
        raise errors.SchemaVersionUnsupported(
            f"database schema_version {found} is newer than this build supports ({expected}); "
            "upgrade Jasmine Core before starting",
            found=found,
            expected=expected,
        )
    return found


def verify_checksums(conn) -> list[str]:
    """Return a drift message for every recorded migration the code no longer matches.

    Used by read-only reporting so `jasmine-core schema` cannot present a
    tampered checksum as if it were healthy. Version numbers are compared as
    well as names, so a renamed migration is reported instead of silently
    re-applying and colliding on the UNIQUE version column.
    """
    problems: list[str] = []
    available = {m.name: m for m in discover()}
    recorded: dict[str, str] = {}
    versions: set[int] = set()
    for row in conn.execute("SELECT name, version, checksum FROM schema_migrations ORDER BY version"):
        recorded[row["name"]] = row["checksum"]
        versions.add(row["version"])
        shipped = available.get(row["name"])
        if shipped is None:
            problems.append(f"{row['name']}: recorded in the database but absent from this build")
        elif shipped.checksum != row["checksum"]:
            problems.append(
                f"{row['name']}: applied with checksum {row['checksum']} but the code now ships "
                f"{shipped.checksum}"
            )
    missing = {m.version for m in available.values()} - versions
    for version in sorted(missing):
        problems.append(f"schema version {version} is applied in this build but not recorded in the database")
    return problems


def migrate(conn, *, log: Callable[[str], None] | None = None) -> list[str]:
    """Apply every missing migration; return the names applied.

    Re-running against an up-to-date database is a no-op. A database written by
    a newer build is refused before anything is opened for writing.
    """
    check_version(conn)
    available = discover()
    expected = expected_version()
    if len(available) != expected:
        raise errors.MigrationConflict(
            f"package declares schema version {expected} but ships {len(available)} migrations"
        )

    applied_now: list[str] = []
    # BEGIN IMMEDIATE is the actual cross-process mutex; `migration_lock` is an
    # observable marker row that only exists once the baseline table is there.
    with transaction(conn):
        if _table_exists(conn, "core_meta"):
            owner = get_meta(conn, LOCK_KEY)
            if owner is not None and owner != LOCK_OWNER:
                raise errors.MigrationConflict(f"migration lock is held by {owner!r}")
            set_meta(conn, LOCK_KEY, LOCK_OWNER)

        recorded: dict[str, str] = {}
        if _table_exists(conn, "schema_migrations"):
            recorded = {
                row["name"]: row["checksum"]
                for row in conn.execute("SELECT name, checksum FROM schema_migrations")
            }
        for migration in available:
            if migration.name in recorded:
                if recorded[migration.name] != migration.checksum:
                    raise errors.SchemaVersionUnsupported(
                        f"migration drift: {migration.name} was applied with checksum "
                        f"{recorded[migration.name]} but the code now ships "
                        f"{migration.checksum}; add a new migration instead of editing a released one",
                        migration=migration.name,
                    )
                continue
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (name, version, checksum, applied_at) "
                "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (migration.name, migration.version, migration.checksum),
            )
            set_meta(conn, "schema_version", migration.version)
            applied_now.append(migration.name)
            if log:
                log(f"applied {migration.name} (schema_version={migration.version})")

    if not applied_now and log:
        log(f"schema already at version {current_version(conn)}; no migration needed")
    return applied_now
