"""P0-T01 follow-up: the schema guarantees themselves, exercised not inspected.

The P0-01 review found that asserting trigger *names* exist in sqlite_master
passes even when a trigger body is `SELECT 1`, and that the deferred/immediate
foreign key split had no behavioural coverage at all. These tests perform the
attacks instead of checking the catalogue.
"""

from __future__ import annotations

import sqlite3
import unittest

from support import DbTestCase  # noqa: E402

from jasmine_core import db  # noqa: E402

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
EVENT = "evt_01K742SG00KSNSRN81BXC5ZAZC"

_EVENT_INSERT = (
    "INSERT INTO events (event_id, seq, schema_version, event_type, source_system, source_event_id,"
    " occurred_at, recorded_at, actor_id, actor_kind, host_id, session_id, project_id, task_id,"
    " payload_json, body_sha256)"
    " VALUES (?, 1, 1, 'user.prompt', 'test', NULL, '2026-09-29T00:00:00.000000Z',"
    " '2026-09-29T00:00:00.000000Z', ?, 'human', ?, NULL, NULL, NULL, ?, ?)"
)


class GuardedDbTestCase(DbTestCase):
    def seed_identity(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO hosts (host_id, display_name, kind, first_seen_at, last_seen_at)"
                " VALUES (?, 'host', 'workstation', '2026-09-29T00:00:00Z', '2026-09-29T00:00:00Z')",
                (HOST,),
            )
            self.conn.execute(
                "INSERT INTO actors (actor_id, kind, display_name, created_at)"
                " VALUES (?, 'human', 'someone', '2026-09-29T00:00:00Z')",
                (ACTOR,),
            )

    def add_event(self, event_id: str = EVENT, payload: str = '{"text":"original"}',
                  digest: str = "d" * 64) -> None:
        self.conn.execute(_EVENT_INSERT, (event_id, ACTOR, HOST, payload, digest))

    def event_row(self) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT event_id, payload_json, body_sha256 FROM events WHERE event_id = ?", (EVENT,)
        ).fetchone()

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


class AppendOnlyIsEnforcedByTheDatabase(GuardedDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed_identity()
        self.add_event()

    def test_update_is_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.conn.execute("UPDATE events SET payload_json = '{\"text\":\"rewritten\"}'")
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.event_row()["payload_json"], '{"text":"original"}')

    def test_delete_is_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.conn.execute("DELETE FROM events")
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.count("events"), 1)

    def test_insert_or_replace_cannot_rewrite_an_event(self) -> None:
        # Without trg_events_no_replace (or with recursive_triggers off) this
        # silently replaces the stored Raw Event and its hash.
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.conn.execute(_EVENT_INSERT.replace(
                "VALUES (?, 1, 1,", "VALUES (?, 900, 1,"),
                (EVENT, ACTOR, HOST, '{"text":"rewritten"}', "f" * 64),
            )
        self.assertIn("append-only", str(ctx.exception))
        row = self.event_row()
        self.assertEqual(row["payload_json"], '{"text":"original"}')
        self.assertEqual(row["body_sha256"], "d" * 64)

    def test_recursive_triggers_is_actually_enabled(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA recursive_triggers").fetchone()[0], 1)

    def test_replace_is_also_blocked_on_a_connection_with_defaults(self) -> None:
        # A future caller that opens the file without db.connect() must still not
        # be able to rewrite an Event, hence the INSERT trigger rather than a
        # pragma-only guarantee.
        other = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.addCleanup(other.close)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            other.execute(_EVENT_INSERT.replace("VALUES (?, 1, 1,", "VALUES (?, 901, 1,"),
                           (EVENT, ACTOR, HOST, '{"text":"rewritten"}', "f" * 64))
        self.assertIn("append-only", str(ctx.exception))


class DeferredForeignKeysCommitTogetherOrNotAtAll(GuardedDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed_identity()

    def test_a_session_and_the_event_that_created_it_commit_together(self) -> None:
        with db.transaction(self.conn):
            self.add_event(EVENT)
            self.conn.execute(
                "INSERT INTO sessions (session_id, project_id, task_id, host_id, actor_id, status,"
                " revision, source_event_id, started_at, created_at, updated_at)"
                " VALUES (?, NULL, NULL, ?, ?, 'open', 1, ?, '2026-09-29T00:00:00Z',"
                " '2026-09-29T00:00:00Z', '2026-09-29T00:00:00Z')",
                ("ses_01K742SG00CN4E98TXMDE6TBEP", HOST, ACTOR, EVENT),
            )
        self.assertEqual(self.count("sessions"), 1)
        self.assertEqual(self.count("events"), 1)

    def test_a_dangling_deferred_reference_fails_at_commit_and_rolls_back(self) -> None:
        # The event claims a session that never gets created. Because the FK is
        # deferred, the INSERT itself succeeds; COMMIT is where it must fail, and
        # the rollback must remove the half-written Event.
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                self.add_event(EVENT, payload='{"session":"ghost"}')
                self.conn.execute(
                    "INSERT INTO events (event_id, seq, schema_version, event_type, source_system,"
                    " source_event_id, occurred_at, recorded_at, actor_id, actor_kind, host_id,"
                    " session_id, project_id, task_id, payload_json, body_sha256)"
                    " VALUES (?, 2, 1, 'user.prompt', 'test', NULL, '2026-09-29T00:00:00Z',"
                    " '2026-09-29T00:00:00Z', ?, 'human', ?, ?, NULL, NULL, '{}', ?)",
                    ("evt_01K742SG00KSNSRN81BXC5ZAZD", ACTOR, HOST,
                     "ses_01K742SG00CN4E98TXMDE6TBZ", "e" * 64),
                )
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.count("events"), 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_connection_is_reusable_after_a_failed_commit(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                self.add_event(EVENT)
                self.conn.execute(
                    "INSERT INTO events (event_id, seq, schema_version, event_type, source_system,"
                    " source_event_id, occurred_at, recorded_at, actor_id, actor_kind, host_id,"
                    " session_id, project_id, task_id, payload_json, body_sha256)"
                    " VALUES (?, 2, 1, 'user.prompt', 'test', NULL, '2026-09-29T00:00:00Z',"
                    " '2026-09-29T00:00:00Z', ?, 'human', ?, ?, NULL, NULL, '{}', ?)",
                    ("evt_01K742SG00KSNSRN81BXC5ZAZD", ACTOR, HOST,
                     "ses_01K742SG00CN4E98TXMDE6TBZ", "e" * 64),
                )
        with db.transaction(self.conn):
            self.add_event("evt_01K742SG00KSNSRN81BXC5ZAZF", payload="{}", digest="e" * 64)
        self.assertEqual(self.count("events"), 1)

    def test_projection_cannot_be_written_before_its_event(self) -> None:
        # The projection -> Event foreign key is not deferred, so the "Event
        # first, projection second" order is enforced by the schema itself.
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO projects (project_id, name, description, status, revision,"
                    " source_event_id, created_at, updated_at)"
                    " VALUES ('prj_01K742SG000Z61XPMPFJBYH7RZ', 'p', '', 'active', 1, ?,"
                    " '2026-09-29T00:00:00Z', '2026-09-29T00:00:00Z')",
                    (EVENT,),
                )
        self.assertEqual(self.count("projects"), 0)

    def test_revision_must_start_at_one(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                self.add_event(EVENT)
                self.conn.execute(
                    "INSERT INTO projects (project_id, name, description, status, revision,"
                    " source_event_id, created_at, updated_at)"
                    " VALUES ('prj_01K742SG000Z61XPMPFJBYH7RZ', 'p', '', 'active', 0, ?,"
                    " '2026-09-29T00:00:00Z', '2026-09-29T00:00:00Z')",
                    (EVENT,),
                )

    def test_one_source_event_cannot_be_stored_twice(self) -> None:
        def insert(event_id: str, seq: int) -> None:
            self.conn.execute(
                _EVENT_INSERT.replace(
                    "VALUES (?, 1, 1, 'user.prompt', 'test', NULL,",
                    f"VALUES (?, {seq}, 1, 'user.prompt', 'test', 'codex-turn-7',",
                ),
                (event_id, ACTOR, HOST, '{"text":"same turn"}', "e" * 64),
            )

        with db.transaction(self.conn):
            insert("evt_01K742SG00KSNSRN81BXC5ZAZD", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                insert("evt_01K742SG00KSNSRN81BXC5ZAZE", 2)
        self.assertEqual(self.count("events"), 1)


if __name__ == "__main__":
    unittest.main()
