"""P4: the schema guarantees `m0011_memory` is supposed to enforce.

The P4 tables are only as trustworthy as the constraints behind them, because
every writer in P4 is a background process and P6 is explicitly forbidden from
writing SQLite directly. A rule that lives only in a docstring is a rule an
out-of-band writer bypasses; these tests go through the database, not through the
store, so a `DELETE` or an identity `UPDATE` is refused by SQLite itself.
"""

from __future__ import annotations

import re

import sqlite3

from memory_support import HOST, HUMAN, MemoryTestCase
from jasmine_core import SCHEMA_VERSION, db, ids
from jasmine_core.migrations import applied_migrations, current_version, migrate


def _triggers(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def _indexes(conn) -> set[str]:
    return {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ux_%'")}


def _checksum(statements) -> str:
    import hashlib

    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def _write(conn, sql: str, params=()) -> None:
    with db.transaction(conn):
        conn.execute(sql, params)


class SchemaShape(MemoryTestCase):
    def test_the_migration_is_recorded_with_a_checksum(self) -> None:
        records = {row["name"]: row for row in applied_migrations(self.conn)}
        self.assertIn("m0011_memory", records)
        self.assertEqual(records["m0011_memory"]["version"], 11)
        self.assertEqual(records["m0012_web_confirmations"]["version"], SCHEMA_VERSION)
        self.assertEqual(len(records["m0011_memory"]["checksum"]), 64)
        self.assertEqual(current_version(self.conn), SCHEMA_VERSION)

    def test_the_expected_triggers_and_unique_indexes_exist(self) -> None:
        self.assertLessEqual({
            "trg_memory_items_no_delete", "trg_memory_items_no_replace",
            "trg_memory_items_identity_immutable",
            "trg_memory_jobs_no_delete", "trg_memory_jobs_no_replace",
            "trg_memory_jobs_identity_immutable",
            "trg_memory_job_attempts_immutable_update",
            "trg_memory_job_attempts_immutable_delete",
            "trg_memory_job_attempts_no_replace",
            "trg_memory_query_traces_immutable_update",
            "trg_memory_query_traces_immutable_delete",
            "trg_memory_query_traces_no_replace",
            "trg_memory_query_trace_items_immutable_update",
            "trg_memory_query_trace_items_immutable_delete",
            "trg_memory_item_sources_immutable_update",
            "trg_memory_item_sources_immutable_delete",
        }, _triggers(self.conn))
        self.assertIn("ux_memory_jobs_source", _indexes(self.conn))

    def test_p4_created_no_table_it_does_not_own(self) -> None:
        """P4's own migration touches Memory tables and nothing else.

        Scoped to `m0011_memory` rather than to the whole schema: P3's
        Checkpoint/Context tables *do* exist on an integrated Core, and it is
        P3 that owns them. Asserting they are absent from the schema would
        assert that P3 was not merged, which is the opposite of what this test
        is for. So the check is that P4's migration text names no table it does
        not own -- which is what would actually make P4 a second source of the
        P3 contract.
        """
        from jasmine_core.migrations import m0011_memory as migration

        created = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)",
                                 "\n".join(migration.STATEMENTS)))
        self.assertTrue(created, "P4's migration creates no table at all")
        for owned in ("memory_items", "memory_item_sources", "memory_jobs",
                      "memory_job_attempts", "memory_query_traces",
                      "memory_query_trace_items", "memory_engine_status",
                      "memory_project_actors"):
            self.assertIn(owned, created)
        for foreign in ("checkpoints", "context_packs", "context_pack_sections",
                        "resume_results", "tasks", "events", "evidence", "rules"):
            self.assertNotIn(foreign, created,
                             f"P4's migration must not create {foreign}: it is not P4's")

        # And the P6 Dashboard table does not exist anywhere yet, because no
        # phase has shipped it. That one stays a whole-schema assertion.
        tables = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("dashboard", tables)


class Constraints(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.store.items()[0]
        self.job = self.job_store().jobs()[0]

    def assert_refused(self, sql: str, params=(), contains: str = "") -> None:
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            _write(self.conn, sql, params)
        if contains:
            self.assertIn(contains, str(caught.exception))

    # -- append-only -------------------------------------------------------

    def test_a_memory_item_cannot_be_deleted(self) -> None:
        self.assert_refused("DELETE FROM memory_items WHERE memory_id=?",
                            (self.item["memory_id"],), "never deleted")

    def test_a_job_cannot_be_deleted(self) -> None:
        self.assert_refused("DELETE FROM memory_jobs WHERE job_id=?",
                            (self.job["job_id"],), "never deleted")

    def test_an_attempt_or_trace_cannot_be_updated_or_deleted(self) -> None:
        self.worker().tick()
        attempt = self.job_store().attempts(self.job["job_id"])[0]
        self.assert_refused("UPDATE memory_job_attempts SET outcome='SUCCESS' WHERE attempt_id=?",
                            (attempt["attempt_id"],), "append-only")
        self.assert_refused("DELETE FROM memory_job_attempts WHERE attempt_id=?",
                            (attempt["attempt_id"],), "append-only")
        trace = self.store.traces()
        self.assertEqual(trace, [])

    def test_a_memory_item_cannot_be_replaced(self) -> None:
        self.assert_refused(
            "INSERT INTO memory_items (memory_id, bank_id, scope_kind, project_id, task_id,"
            " memory_type, document_id, source_event_id, evidence_ids_json, content_sha256,"
            " sanitized_text, status, revision, engine_state, created_at, updated_at)"
            " SELECT memory_id, bank_id, scope_kind, project_id, task_id, memory_type,"
            " document_id, source_event_id, evidence_ids_json, content_sha256, sanitized_text,"
            " status, revision, engine_state, created_at, updated_at FROM memory_items"
            " WHERE memory_id=?", (self.item["memory_id"],), "append-only")

    # -- identity ----------------------------------------------------------

    def test_a_job_identity_cannot_be_repointed(self) -> None:
        for column, value in (("document_id", "other"),
                               ("content_sha256", "0" * 64),
                               ("bank_id", "jasmine-global"),
                               ("source_event_id", ids.new_id("evt")),
                               ("memory_type", "VERIFIED_OUTCOME")):
            self.assert_refused(
                f"UPDATE memory_jobs SET {column}=? WHERE job_id=?",
                (value, self.job["job_id"]), "identity is immutable")
        # Status, lease, retry counter and receipt are all mutable.
        _write(self.conn, "UPDATE memory_jobs SET status='FAILED', retry_count=2,"
                          " lease_owner='w', lease_expires_at='2026-09-29T12:00:00.000000Z'"
                          " WHERE job_id=?", (self.job["job_id"],))
        row = self.job_store().jobs()[0]
        self.assertEqual((row["status"], row["retry_count"], row["lease_owner"]),
                         ("FAILED", 2, "w"))

    def test_a_memory_item_identity_cannot_be_repointed(self) -> None:
        for column, value in (("document_id", "other"),
                              ("content_sha256", "0" * 64),
                              ("bank_id", "jasmine-global"),
                              ("memory_type", "FAILURE_LESSON"),
                              ("sanitized_text", "different content")):
            self.assert_refused(
                f"UPDATE memory_items SET {column}=? WHERE memory_id=?",
                (value, self.item["memory_id"],), "identity is immutable")

    def test_a_global_item_cannot_name_a_project(self) -> None:
        # The CHECK, not the store, is what stops a project-scoped source from
        # being filed into the shared bank.
        self.assert_refused(
            "INSERT INTO memory_items (memory_id, bank_id, scope_kind, project_id, task_id,"
            " memory_type, document_id, source_event_id, evidence_ids_json, content_sha256,"
            " sanitized_text, status, revision, engine_state, created_at, updated_at)"
            " VALUES (?, 'jasmine-global', 'global', ?, NULL, 'FAILURE_LESSON', ?,"
            " 'evt_00000000000000000000000000', '[]', ?, 'x', 'ACTIVE', 1, 'PENDING_RETAIN',"
            " '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')",
            (ids.new_id("mem"), self.project_id, "hindsight:x:" + "0" * 32, "0" * 64))

    def test_an_unknown_memory_type_is_refused(self) -> None:
        self.assert_refused(
            "INSERT INTO memory_items (memory_id, bank_id, scope_kind, project_id, task_id,"
            " memory_type, document_id, source_event_id, evidence_ids_json, content_sha256,"
            " sanitized_text, status, revision, engine_state, created_at, updated_at)"
            " VALUES (?, 'jasmine-global', 'global', NULL, NULL, 'VIBES', ?,"
            " 'evt_00000000000000000000000000', '[]', ?, 'x', 'ACTIVE', 1, 'PENDING_RETAIN',"
            " '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')",
            (ids.new_id("mem"), "hindsight:y:" + "0" * 32, "0" * 64))

    def test_an_unknown_job_status_or_kind_is_refused(self) -> None:
        self.assert_refused("UPDATE memory_jobs SET status='RETRYING' WHERE job_id=?",
                            (self.job["job_id"],))
        self.assert_refused(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, memory_type,"
            " bank_id, document_id, content_sha256, status, max_attempts, next_attempt_at,"
            " created_at, updated_at) VALUES (?, 'purge', ?, ?, 'FAILURE_LESSON', 'jasmine-global',"
            " 'd', ?, 'PENDING', 3, '2026-01-01T00:00:00.000000Z',"
            " '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')",
            (ids.new_id("mjb"), self.item["memory_id"], self.item["source_event_id"], "0" * 64))

    def test_a_lease_without_an_expiry_is_refused(self) -> None:
        self.assert_refused("UPDATE memory_jobs SET lease_owner='w', lease_expires_at=NULL"
                            " WHERE job_id=?", (self.job["job_id"],))

    def test_a_done_job_must_carry_a_completion_time(self) -> None:
        self.assert_refused("UPDATE memory_jobs SET status='DONE', completed_at=NULL"
                            " WHERE job_id=?", (self.job["job_id"],))

    def test_one_job_per_source_and_kind(self) -> None:
        self.assert_refused(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, memory_type,"
            " bank_id, document_id, content_sha256, status, max_attempts, next_attempt_at,"
            " created_at, updated_at) VALUES (?, 'retain', ?, ?, 'FAILURE_LESSON', ?, ?, ?,"
            " 'PENDING', 3, '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z',"
            " '2026-01-01T00:00:00.000000Z')",
            (ids.new_id("mjb"), self.item["memory_id"], self.item["source_event_id"],
             self.item["bank_id"], "other-document", "0" * 64), "UNIQUE")

    def test_a_job_must_name_a_real_memory_item(self) -> None:
        self.assert_refused(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, memory_type,"
            " bank_id, document_id, content_sha256, status, max_attempts, next_attempt_at,"
            " created_at, updated_at) VALUES (?, 'retain', ?, ?, 'FAILURE_LESSON', 'jasmine-global',"
            " 'd', ?, 'PENDING', 3, '2026-01-01T00:00:00.000000Z',"
            " '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')",
            (ids.new_id("mjb"), ids.new_id("mem"), ids.new_id("evt"), "0" * 64), "FOREIGN KEY")


class MigrationFromP1(MemoryTestCase):
    """A P1 database upgrades to P4 without touching its truth."""

    migrate_db = False

    def test_a_p1_database_upgrades_and_keeps_its_rows(self) -> None:
        """An existing P1 database gains the P4 tables and nothing else changes.

        The P1 shape is produced by applying every migration up to and including
        `m0006` directly, which is what a database written by `main@9059625`
        looks like on disk. Then the shipped code migrates it.
        """
        import importlib

        for version in range(1, 8):
            module = importlib.import_module(f"jasmine_core.migrations.m000{version}_"
                                             + {1: "baseline", 2: "api_auth_audit", 3: "authority",
                                                4: "task_step_state", 5: "evidence_fingerprint",
                                                6: "codex_exec_observations",
                                                7: "interpretations"}[version])
            with db.transaction(self.conn):
                for statement in module.STATEMENTS:
                    self.conn.execute(statement)
                # The real checksum, so the upgrade is a genuine forward
                # migration rather than a drift report.
                self.conn.execute(
                    "INSERT INTO schema_migrations (name, version, checksum, applied_at)"
                    " VALUES (?,?,?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (module.NAME, module.VERSION, _checksum(module.STATEMENTS)))
                self.conn.execute("INSERT INTO core_meta (key, value) VALUES ('schema_version', ?)"
                                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                  (str(version),))
        before_tables = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("memory_items", before_tables)
        # A committed P1 truth row, to prove the upgrade is additive.
        with db.transaction(self.conn):
            from jasmine_core import registry
            registry.Registry(self.conn).upsert_host(HOST, display_name="p1 host")
            registry.Registry(self.conn).upsert_actor(HUMAN, kind="human", display_name="p1",
                                                      home_host_id=HOST)

        applied = migrate(self.conn)
        # P2-02 shipped m0008_resolutions and P3 shipped m0009_continuity and
        # m0010_context, so the upgrade from a P1-era database applies those as
        # well as P4's. This is the test that would catch a renumber done
        # without the migrations it renumbers past actually existing.
        self.assertEqual(applied, ["m0008_resolutions", "m0009_continuity",
                                   "m0010_context", "m0011_memory", "m0012_web_confirmations"])
        self.assertEqual(current_version(self.conn), SCHEMA_VERSION)
        self.assertEqual(
            self.conn.execute("SELECT display_name FROM hosts WHERE host_id=?",
                              (HOST,)).fetchone()["display_name"], "p1 host")
        after_tables = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(before_tables.issubset(after_tables))
        self.assertLessEqual({"memory_items", "memory_jobs"}, after_tables - before_tables)
        # And a second run is a no-op with no checksum drift anywhere.
        self.assertEqual(migrate(self.conn), [])
        from jasmine_core.migrations import verify_checksums

        self.assertEqual(verify_checksums(self.conn), [])

    def test_the_memory_tables_are_empty_on_a_fresh_upgrade(self) -> None:
        migrate(self.conn)
        for table in ("memory_items", "memory_jobs", "memory_query_traces",
                      "memory_engine_status", "memory_project_actors"):
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            self.assertEqual(row["n"], 0, table)


class P0ContractStillHolds(MemoryTestCase):
    """P4 must not have loosened anything the earlier phases froze."""

    def test_core_health_is_unaffected_by_an_empty_memory_schema(self) -> None:
        from jasmine_core.api.handlers import Core

        core = Core(self.conn)
        self.assertEqual(core.schema_version, SCHEMA_VERSION)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA recursive_triggers").fetchone()[0], 1)

    def test_an_event_cannot_be_updated_or_deleted_by_a_memory_path(self) -> None:
        self.project_id = self.project()
        event_id = self.event("user.prompt", project_id=self.project_id, text="x")
        with self.assertRaises(sqlite3.IntegrityError):
            _write(self.conn, "UPDATE events SET event_type='rule.approved' WHERE event_id=?",
                   (event_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            _write(self.conn, "DELETE FROM events WHERE event_id=?", (event_id,))
