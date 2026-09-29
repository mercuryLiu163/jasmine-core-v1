"""P0-T01: empty database migration, schema version, replay safety."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from support import DbTestCase  # noqa: E402

from jasmine_core import SCHEMA_VERSION, db, errors  # noqa: E402
from jasmine_core.migrations import (  # noqa: E402
    applied_migrations,
    check_version,
    current_version,
    discover,
    expected_version,
    migrate,
    verify_checksums,
)

EXPECTED_TABLES = {
    "core_meta",
    "schema_migrations",
    "hosts",
    "actors",
    "projects",
    "tasks",
    "sessions",
    "events",
}


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return {row["name"] for row in rows}


def _triggers(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    return {row["name"] for row in rows}


def _indexes(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'ux_%'")
    return {row["name"] for row in rows}


class MigrationFromEmptyDatabase(DbTestCase):
    migrate_db = False

    def test_empty_database_has_no_schema_before_migrating(self) -> None:
        self.assertEqual(_tables(self.conn), set())
        self.assertEqual(current_version(self.conn), 0)

    def test_migrate_creates_the_full_baseline_schema(self) -> None:
        applied = migrate(self.conn)
        self.assertEqual(applied, ["m0001_baseline"])
        self.assertEqual(_tables(self.conn), EXPECTED_TABLES)
        self.assertEqual(current_version(self.conn), expected_version())
        self.assertEqual(current_version(self.conn), SCHEMA_VERSION)

    def test_migrate_does_not_change_the_connection_pragma_set(self) -> None:
        # The pragma set belongs to db.connect(), not to migrate(); this asserts
        # migrate() does not silently reset it.
        migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA recursive_triggers").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_migrate_installs_the_immutability_and_uniqueness_guards(self) -> None:
        migrate(self.conn)
        self.assertLessEqual(
            {"trg_events_immutable_update", "trg_events_immutable_delete"},
            _triggers(self.conn),
        )
        self.assertIn("ux_events_source", _indexes(self.conn))

    def test_migration_record_carries_a_checksum_and_timestamp(self) -> None:
        migrate(self.conn)
        recorded = applied_migrations(self.conn)
        self.assertEqual([row["version"] for row in recorded], [1])
        for row in recorded:
            self.assertEqual(len(row["checksum"]), 64)
            self.assertTrue(row["applied_at"].endswith("Z"), row["applied_at"])


class ConnectionPragmas(DbTestCase):
    def test_connect_applies_the_frozen_pragma_set(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("PRAGMA recursive_triggers").fetchone()[0], 1)
        self.assertEqual(int(self.conn.execute("PRAGMA busy_timeout").fetchone()[0]), 5000)

    def test_connections_are_in_autocommit_mode(self) -> None:
        # db.transaction() issues BEGIN/COMMIT itself; leaving Python to manage
        # transactions would break the IMMEDIATE discipline.
        self.assertIsNone(self.conn.isolation_level)
        self.assertFalse(self.conn.in_transaction)

    def test_a_second_connection_gets_the_same_pragmas(self) -> None:
        other = db.connect(self.db_path)
        self.addCleanup(other.close)
        self.assertEqual(other.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(other.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")


class MigrationIsReplayable(DbTestCase):
    def test_second_run_applies_nothing_and_preserves_data(self) -> None:
        self.conn.execute(
            "INSERT INTO actors (actor_id, kind, display_name, created_at) VALUES (?, 'human', ?, ?)",
            ("act_keepme", "someone", "2026-09-29T00:00:00.000000Z"),
        )
        applied = migrate(self.conn)
        self.assertEqual(applied, [])
        self.assertEqual(current_version(self.conn), SCHEMA_VERSION)
        row = self.conn.execute("SELECT display_name FROM actors WHERE actor_id = 'act_keepme'").fetchone()
        self.assertEqual(row["display_name"], "someone")

    def test_repeated_runs_do_not_duplicate_tables_or_rows(self) -> None:
        for _ in range(3):
            migrate(self.conn)
        self.assertEqual(_tables(self.conn), EXPECTED_TABLES)
        self.assertEqual(len(applied_migrations(self.conn)), SCHEMA_VERSION)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"], SCHEMA_VERSION
        )

    def test_migration_versions_are_contiguous_from_one(self) -> None:
        found = discover()
        self.assertEqual([m.version for m in found], list(range(1, len(found) + 1)))
        self.assertEqual(len(found), SCHEMA_VERSION)

    def test_check_version_accepts_the_current_build(self) -> None:
        self.assertEqual(check_version(self.conn), SCHEMA_VERSION)

    def test_check_version_rejects_a_newer_database(self) -> None:
        db.set_meta(self.conn, "schema_version", SCHEMA_VERSION + 1)
        with self.assertRaises(errors.SchemaVersionUnsupported):
            check_version(self.conn)

    def test_migration_drift_is_refused(self) -> None:
        with self.conn:
            self.conn.execute("UPDATE schema_migrations SET checksum = ? WHERE name = 'm0001_baseline'",
                              ("0" * 64,))
        with self.assertRaises(errors.SchemaVersionUnsupported) as ctx:
            migrate(self.conn)
        self.assertIn("drift", ctx.exception.message)

    def test_migrate_refuses_a_database_from_a_newer_build(self) -> None:
        db.set_meta(self.conn, "schema_version", SCHEMA_VERSION + 1)
        with self.assertRaises(errors.SchemaVersionUnsupported):
            migrate(self.conn)
        self.assertEqual(len(applied_migrations(self.conn)), SCHEMA_VERSION)

    def test_verify_checksums_reports_a_tampered_record(self) -> None:
        self.assertEqual(verify_checksums(self.conn), [])
        with self.conn:
            self.conn.execute("UPDATE schema_migrations SET checksum = ? WHERE name = 'm0001_baseline'",
                              ("0" * 64,))
        problems = verify_checksums(self.conn)
        self.assertEqual(len(problems), 1)
        self.assertIn("m0001_baseline", problems[0])

class TransactionDiscipline(DbTestCase):
    def test_rollback_discards_every_statement_in_the_block(self) -> None:
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO actors (actor_id, kind, display_name, created_at) VALUES (?, 'human', ?, ?)",
                    ("act_temp", "ghost", "2026-09-29T00:00:00.000000Z"),
                )
                raise RuntimeError("injected failure after the write")
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM actors WHERE actor_id = 'act_temp'").fetchone()
        )

    def test_commit_persists_and_nested_transactions_are_refused(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO actors (actor_id, kind, display_name, created_at) VALUES (?, 'human', ?, ?)",
                ("act_committed", "kept", "2026-09-29T00:00:00.000000Z"),
            )
            with self.assertRaises(RuntimeError):
                with db.transaction(self.conn):
                    pass
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM actors WHERE actor_id = 'act_committed'").fetchone()
        )


class LockContentionIsTyped(DbTestCase):
    def test_raw_lock_errors_become_database_busy(self) -> None:
        with self.assertRaises(errors.DatabaseBusy) as ctx:
            with db.translate_lock_errors():
                raise sqlite3.OperationalError("database is locked")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "database_busy")
        self.assertEqual(ctx.exception.details["busy_timeout_ms"], db.BUSY_TIMEOUT_MS)

    def test_other_sqlite_errors_are_not_translated(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            with db.translate_lock_errors():
                raise sqlite3.OperationalError("no such table: events")

    def test_is_locked_distinguishes_contention_from_data_errors(self) -> None:
        self.assertTrue(db.is_locked(sqlite3.OperationalError("database is locked")))
        self.assertFalse(db.is_locked(sqlite3.OperationalError("no such table: events")))
        self.assertFalse(db.is_locked(ValueError("database is locked")))


if __name__ == "__main__":
    unittest.main()
