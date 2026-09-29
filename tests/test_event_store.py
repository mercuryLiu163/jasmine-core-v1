"""P0-T02/T04/T05: Event Store and the three projections, at the storage layer.

These run against real SQLite with real transactions; nothing is mocked. The
fault-injection tests monkeypatch one method to raise *after* the Event insert,
which is the exact shape P0-T05 requires.
"""

from __future__ import annotations

import sqlite3
import unittest

from support import DbTestCase  # noqa: E402

from jasmine_core import SCHEMA_VERSION, db, errors, objects, registry  # noqa: E402
from jasmine_core.models import NewEvent, NewObject  # noqa: E402

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
HAPPENED = "2026-09-29T12:00:00.000000Z"


class StoreTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = registry.Registry(self.conn)
        with db.transaction(self.conn):
            self.registry.upsert_host(HOST, display_name="test host")
            self.registry.upsert_actor(ACTOR, kind="human", display_name="tester",
                                       home_host_id=HOST)
        self.objects = objects.ObjectStore(self.conn, schema_version=SCHEMA_VERSION)

    def new_event(self, text: str = "hello", **overrides) -> NewEvent:
        fields = {
            "event_type": "user.prompt",
            "source_system": "test",
            "actor_id": ACTOR,
            "actor_kind": "human",
            "host_id": HOST,
            "payload": {"text": text},
            "occurred_at": overrides.pop("occurred_at", HAPPENED),
        }
        from jasmine_core.clock import parse_rfc3339

        fields.update(overrides)
        if isinstance(fields["occurred_at"], str):
            fields["occurred_at"] = parse_rfc3339(fields["occurred_at"])
        # Mirrors what NewEvent.from_request does: the client-stated time is what
        # the idempotency hash sees.
        fields["client_occurred_at"] = fields["occurred_at"]
        return NewEvent(**fields)

    def new_event_with_payload(self, payload: dict) -> NewEvent:
        from jasmine_core.clock import parse_rfc3339

        return NewEvent(
            event_type="user.prompt", source_system="test", actor_id=ACTOR, actor_kind="human",
            host_id=HOST, payload=payload, occurred_at=parse_rfc3339(HAPPENED),
            client_occurred_at=parse_rfc3339(HAPPENED),
        )

    def append(self, spec: NewEvent, **kwargs) -> dict:
        with db.transaction(self.conn):
            event, _ = self.objects.events.append(spec, **kwargs)
            return event

    def create_project(self, name: str = "demo", **kwargs) -> dict:
        body = {"name": name, "host_id": HOST}
        body.update(kwargs)
        return self.objects.create(NewObject.project(body), actor_id=ACTOR)

    def create_task(self, project_id: str, title: str = "do it", **kwargs) -> dict:
        body = {"title": title, "project_id": project_id, "host_id": HOST}
        body.update(kwargs)
        return self.objects.create(NewObject.task(body), actor_id=ACTOR)

    def create_session(self, **kwargs) -> dict:
        body = {"host_id": HOST}
        body.update(kwargs)
        return self.objects.create(NewObject.session(body), actor_id=ACTOR)

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


class EventAppend(StoreTestCase):
    def test_an_event_is_stored_with_its_provenance(self) -> None:
        event = self.append(self.new_event("你好，世界"))
        self.assertEqual(event["event_type"], "user.prompt")
        self.assertEqual(event["actor_id"], ACTOR)
        self.assertEqual(event["host_id"], HOST)
        self.assertEqual(event["payload"]["text"], "你好，世界")
        self.assertEqual(len(event["body_sha256"]), 64)
        self.assertEqual(event["seq"], 1)
        self.assertTrue(event["event_id"].startswith("evt_"))

    def test_original_text_is_stored_byte_for_byte(self) -> None:
        text = "  leading and trailing  \t tabs\nnewlines  and  mixed   spacing  "
        event = self.append(self.new_event(text))
        self.assertEqual(event["payload"]["text"], text)

    def test_seq_increases_monotonically(self) -> None:
        for index in range(5):
            self.append(self.new_event(f"turn {index}"))
        seqs = [e["seq"] for e in self.objects.events.list()]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_an_event_needs_no_task_or_session(self) -> None:
        event = self.append(self.new_event("orphan"))
        self.assertIsNone(event["task_id"])
        self.assertIsNone(event["project_id"])
        self.assertIsNone(event["session_id"])
        self.assertEqual(self.count("events"), 1)

    def test_an_explicit_nonexistent_task_is_refused_with_a_typed_error(self) -> None:
        # ADR 0002 §2.4: an explicit dangling reference is an error, not a silent
        # drop -- and ADR 0003 §1.4 freezes the code as 404 task_not_found, so it
        # must not reach the caller as a raw sqlite3.IntegrityError at COMMIT.
        with self.assertRaises(errors.NotFound) as ctx:
            self.append(self.new_event("dangling", task_id="tsk_01K742SG00YPWF74TSXEKA3254"))
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "task_not_found")
        self.assertEqual(self.count("events"), 0)


class EventIdempotency(StoreTestCase):
    def test_same_event_id_and_same_body_is_a_replay(self) -> None:
        spec = self.new_event("retry me", event_id="evt_01K742SG00KSNSRN81BXC5ZAZC")
        first = self.append(spec)
        with db.transaction(self.conn):
            second, replayed = self.objects.events.append(spec)
        self.assertTrue(replayed)
        self.assertEqual(second["event_id"], first["event_id"])
        self.assertEqual(second["body_sha256"], first["body_sha256"])
        self.assertEqual(self.count("events"), 1)

    def test_same_event_id_with_different_body_is_a_conflict(self) -> None:
        first = self.append(self.new_event("original", event_id="evt_01K742SG00KSNSRN81BXC5ZAZC"))
        with self.assertRaises(errors.EventIdConflict) as ctx:
            self.append(self.new_event("changed", event_id="evt_01K742SG00KSNSRN81BXC5ZAZC"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "event_id_conflict")
        self.assertEqual(ctx.exception.details["existing_event_id"], first["event_id"])
        self.assertNotEqual(
            ctx.exception.details["existing_body_sha256"],
            ctx.exception.details["request_body_sha256"],
        )
        self.assertEqual(self.count("events"), 1)
        stored = self.objects.events.get(first["event_id"])
        self.assertEqual(stored["payload"]["text"], "original")

    def test_a_replay_from_inside_a_failed_transaction_leaves_nothing(self) -> None:
        spec = self.new_event("only once", event_id="evt_01K742SG00KSNSRN81BXC5ZAZC")
        self.append(spec)
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                self.objects.events.append(spec)
                raise RuntimeError("caller aborted after the replay")
        self.assertEqual(self.count("events"), 1)

    def test_same_source_event_id_under_a_new_event_id_is_refused(self) -> None:
        self.append(self.new_event("once", source_event_id="codex-turn-1"))
        with self.assertRaises(errors.SourceEventDuplicate) as ctx:
            self.append(self.new_event("once", source_event_id="codex-turn-1",
                                       event_id="evt_01K742SG00KSNSRN81BXC5ZAZD"))
        self.assertEqual(ctx.exception.code, "source_event_duplicate")
        self.assertEqual(self.count("events"), 1)

    def test_the_same_source_event_id_from_a_different_system_is_fine(self) -> None:
        self.append(self.new_event("a", source_event_id="turn-1", source_system="codex"))
        self.append(self.new_event("b", source_event_id="turn-1", source_system="claude"))
        self.assertEqual(self.count("events"), 2)

    def test_a_null_source_event_id_is_never_deduplicated(self) -> None:
        self.append(self.new_event("a"))
        self.append(self.new_event("a"))
        self.assertEqual(self.count("events"), 2)


class ObjectCreation(StoreTestCase):
    def test_a_project_is_created_with_its_source_event(self) -> None:
        result = self.create_project("demo")
        project = result["object"]
        self.assertFalse(result["replayed"])
        self.assertEqual(project["name"], "demo")
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["revision"], 1)
        event = self.objects.events.get(project["source_event_id"])
        self.assertEqual(event["event_type"], "project.created")
        self.assertEqual(event["payload"]["object_id"], project["project_id"])
        self.assertEqual(event["host_id"], HOST)
        self.assertEqual(event["actor_id"], ACTOR)

    def test_a_task_is_created_within_its_project(self) -> None:
        project = self.create_project("demo")["object"]
        result = self.create_task(project["project_id"])
        task = result["object"]
        self.assertEqual(task["project_id"], project["project_id"])
        self.assertEqual(task["status"], "ACTIVE")
        self.assertEqual(task["revision"], 1)
        self.assertEqual(self.objects.events.get(task["source_event_id"])["event_type"],
                         "task.created")

    def test_a_session_records_its_host_and_actor(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"])["object"]
        result = self.create_session(project_id=project["project_id"], task_id=task["task_id"])
        session = result["object"]
        self.assertEqual(session["host_id"], HOST)
        self.assertEqual(session["actor_id"], ACTOR)
        self.assertEqual(session["task_id"], task["task_id"])
        self.assertEqual(session["status"], "open")
        self.assertEqual(session["revision"], 1)
        self.assertIsNone(session["ended_at"])
        self.assertEqual(self.objects.events.get(session["source_event_id"])["event_type"],
                         "session.started")

    def test_a_session_may_have_no_project_or_task(self) -> None:
        result = self.create_session()
        self.assertIsNone(result["object"]["project_id"])
        self.assertIsNone(result["object"]["task_id"])

    def test_a_task_requires_an_existing_project(self) -> None:
        with self.assertRaises(errors.NotFound) as ctx:
            self.create_task("prj_01K742SG000Z61XPMPFJBYH7RZ")
        self.assertEqual(ctx.exception.code, "project_not_found")
        self.assertEqual(self.count("events"), 0)
        self.assertEqual(self.count("tasks"), 0)

    def test_a_session_task_must_belong_to_the_given_project(self) -> None:
        first = self.create_project("one")["object"]
        second = self.create_project("two")["object"]
        task = self.create_task(first["project_id"])["object"]
        with self.assertRaises(errors.InvalidRequest):
            self.create_session(project_id=second["project_id"], task_id=task["task_id"])
        self.assertEqual(self.count("sessions"), 0)

    def test_retrying_a_create_replays_instead_of_duplicating(self) -> None:
        body = {"name": "demo", "host_id": HOST, "event_id": "evt_01K742SG00KSNSRN81BXC5ZAZC"}
        first = self.objects.create(NewObject.project(body), actor_id=ACTOR)
        second = self.objects.create(NewObject.project(body), actor_id=ACTOR)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["object"]["project_id"], second["object"]["project_id"])
        self.assertEqual(self.count("projects"), 1)
        self.assertEqual(self.count("events"), 1)

    def test_reusing_an_event_id_for_different_content_is_a_conflict(self) -> None:
        body = {"name": "demo", "host_id": HOST, "event_id": "evt_01K742SG00KSNSRN81BXC5ZAZC"}
        self.objects.create(NewObject.project(body), actor_id=ACTOR)
        body["name"] = "renamed"
        with self.assertRaises(errors.EventIdConflict):
            self.objects.create(NewObject.project(body), actor_id=ACTOR)
        self.assertEqual(self.count("projects"), 1)
        self.assertEqual(self.objects.get("project", self.objects.list("project")[0]["project_id"])["name"],
                         "demo")

    def test_the_object_id_is_excluded_from_the_idempotency_hash(self) -> None:
        # Otherwise a retry would hash differently (new object_id) and be reported
        # as a conflict instead of a replay.
        body = {"name": "demo", "host_id": HOST, "event_id": "evt_01K742SG00KSNSRN81BXC5ZAZC"}
        self.objects.create(NewObject.project(body), actor_id=ACTOR)
        result = self.objects.create(NewObject.project(dict(body)), actor_id=ACTOR)
        self.assertTrue(result["replayed"])


class AtomicityAndFaultInjection(StoreTestCase):
    """P0-T05: an injected failure must not leave a half-written pair."""

    def _explode(self, *args, **kwargs):
        raise RuntimeError("injected failure after the Event insert")

    def test_a_failure_after_the_event_leaves_no_event_and_no_object(self) -> None:
        original = self.objects._insert_projection
        self.objects._insert_projection = self._explode  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                self.create_project("doomed")
        finally:
            del self.objects._insert_projection
        self.assertEqual(self.count("events"), 0)
        self.assertEqual(self.count("projects"), 0)
        self.assertEqual(self.objects.events.list(), [])
        self.assertFalse(self.conn.in_transaction)

    def test_a_failure_before_the_projection_leaves_nothing_for_a_task_either(self) -> None:
        project = self.create_project("demo")["object"]
        self.objects._insert_projection = self._explode  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                self.create_task(project["project_id"])
        finally:
            del self.objects._insert_projection
        self.assertEqual(self.count("events"), 1)  # only the project event
        self.assertEqual(self.count("tasks"), 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_connection_is_usable_and_consistent_after_a_failure(self) -> None:
        self.objects._insert_projection = self._explode  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                self.create_project("doomed")
        finally:
            del self.objects._insert_projection
        result = self.create_project("recovered")
        self.assertEqual(result["object"]["name"], "recovered")
        self.assertEqual(self.count("projects"), 1)
        self.assertEqual(self.count("events"), 1)
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_a_projection_without_an_event_is_rejected_by_the_schema(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO projects (project_id, name, description, status, revision,"
                    " source_event_id, created_at, updated_at) VALUES"
                    " ('prj_01K742SG000Z61XPMPFJBYH7RZ', 'x', '', 'active', 1,"
                    " 'evt_01K742SG00KSNSRN81BXC5ZAZC', '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')"
                )


class RestartRecovery(StoreTestCase):
    def test_everything_is_readable_after_reopening_the_database(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"], "write the ADR")["object"]
        session = self.create_session(project_id=project["project_id"],
                                      task_id=task["task_id"])["object"]
        event = self.append(self.new_event("原话 preserved"))["event_id"]

        self.conn.close()
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.objects = objects.ObjectStore(self.conn, schema_version=SCHEMA_VERSION)

        self.assertEqual(self.objects.get("project", project["project_id"])["name"], "demo")
        self.assertEqual(self.objects.get("task", task["task_id"])["title"], "write the ADR")
        self.assertEqual(self.objects.get("session", session["session_id"])["host_id"], HOST)
        stored = self.objects.events.get(event)
        self.assertEqual(stored["payload"]["text"], "原话 preserved")
        self.assertEqual(stored["actor_id"], ACTOR)
        self.assertEqual(stored["host_id"], HOST)

    def test_ids_and_relations_are_identical_after_restart(self) -> None:
        project = self.create_project("demo")["object"]
        before = self.objects.list("project"), self.objects.events.list()
        self.conn.close()
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.objects = objects.ObjectStore(self.conn, schema_version=SCHEMA_VERSION)
        after = self.objects.list("project"), self.objects.events.list()
        self.assertEqual(before, after)
        self.assertEqual(before[0][0]["source_event_id"], before[1][0]["event_id"])
        self.assertEqual(before[0][0]["project_id"], project["project_id"])


class Queries(StoreTestCase):
    def test_events_can_be_filtered_by_session_and_task(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"])["object"]
        session = self.create_session(project_id=project["project_id"],
                                      task_id=task["task_id"])["object"]
        self.append(self.new_event("one", session_id=session["session_id"],
                                   task_id=task["task_id"], project_id=project["project_id"]))
        self.append(self.new_event("two", session_id=session["session_id"],
                                   task_id=task["task_id"], project_id=project["project_id"]))
        self.append(self.new_event("elsewhere"))
        self.assertEqual(len(self.objects.events.list(session_id=session["session_id"])), 2)
        # Three, not two: the task.created event now carries the task_id column, so
        # the creating event is findable by the object it created.
        self.assertEqual(len(self.objects.events.list(task_id=task["task_id"])), 3)
        # Four: project.created, task.created, session.started and one prompt all
        # name the project, so the chain is walkable from the project downwards.
        self.assertEqual(len(self.objects.events.list(project_id=project["project_id"])), 4)
        self.assertEqual(len(self.objects.events.list(event_type="user.prompt")), 3)
        self.assertEqual(len(self.objects.events.list(source_system="test")), 3)

    def test_pagination_is_stable_and_bounded(self) -> None:
        for index in range(7):
            self.append(self.new_event(f"turn {index}"))
        page = self.objects.events.list(limit=3)
        self.assertEqual([e["payload"]["text"] for e in page], ["turn 0", "turn 1", "turn 2"])
        rest = self.objects.events.list(limit=10, after_seq=page[-1]["seq"])
        self.assertEqual(len(rest), 4)
    def test_page_size_is_clamped_to_a_sane_range(self) -> None:
        for index in range(4):
            self.append(self.new_event(f"turn {index}"))
        # A nonsense limit must not return nothing and must not return everything:
        # it is clamped, so the page is usable rather than empty or unbounded.
        self.assertEqual(len(self.objects.events.list(limit=0)), 1)
        self.assertEqual(len(self.objects.events.list(limit=10_000)), 4)
        self.assertEqual(len(self.objects.events.list(limit=-5)), 1)

    def test_tasks_can_be_listed_per_project(self) -> None:
        first = self.create_project("one")["object"]
        second = self.create_project("two")["object"]
        self.create_task(first["project_id"], "in first")
        self.create_task(second["project_id"], "in second")
        self.assertEqual(len(self.objects.list("task", project_id=first["project_id"])), 1)
        self.assertEqual(len(self.objects.list("task")), 2)

    def test_require_raises_the_frozen_not_found_error(self) -> None:
        with self.assertRaises(errors.NotFound) as ctx:
            self.objects.require("task", "tsk_01K742SG00YPWF74TSXEKA3254")
        self.assertEqual(ctx.exception.code, "task_not_found")


class RequestValidation(StoreTestCase):
    def test_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(errors.InvalidRequest):
            NewObject.project({"name": "x", "host_id": HOST, "surprise": 1})

    def test_expected_revision_is_refused_on_create(self) -> None:
        with self.assertRaises(errors.UnexpectedExpectedRevision):
            NewObject.task({"title": "t", "project_id": "prj_01K742SG000Z61XPMPFJBYH7RY",
                            "host_id": HOST, "expected_revision": 1})

    def test_ids_must_carry_the_right_prefix(self) -> None:
        for body in ({"name": "x", "host_id": "prj_01K742SG000Z61XPMPFJBYH7RY"},
                     {"title": "t", "project_id": "ses_01K742SG00CN4E98TXMDE6TBEP", "host_id": HOST}):
            with self.assertRaises(errors.InvalidRequest):
                (NewObject.project if "name" in body else NewObject.task)(body)

    def test_event_text_must_be_present_and_a_string(self) -> None:
        with self.assertRaises(errors.InvalidRequest):
            self.append(self.new_event_with_payload({}))
        with self.assertRaises(errors.InvalidRequest):
            self.append(self.new_event_with_payload({"text": 7}))
        self.assertEqual(self.count("events"), 0)

    def test_oversized_text_is_refused(self) -> None:
        from jasmine_core import models

        with self.assertRaises(errors.PayloadTooLarge):
            self.append(self.new_event_with_payload({"text": "x" * (models.MAX_TEXT_BYTES + 1)}))
        self.assertEqual(self.count("events"), 0)

    def test_an_unknown_event_type_is_refused_at_the_store_too(self) -> None:
        from jasmine_core.clock import parse_rfc3339

        spec = NewEvent(
            event_type="made.up", source_system="test", actor_id=ACTOR, actor_kind="human",
            host_id=HOST, payload={"text": "x"}, occurred_at=parse_rfc3339(HAPPENED),
        )
        with self.assertRaises(errors.InvalidRequest):
            self.append(spec)

    def test_unknown_event_type_is_refused(self) -> None:
        with self.assertRaises(errors.InvalidRequest):
            NewEvent.from_request(
                {
                    "event_type": "not.a.type", "source_system": "test", "host_id": HOST,
                    "payload": {"text": "x"}, "occurred_at": HAPPENED,
                },
                actor_id=ACTOR, actor_kind="human",
            )

    def test_from_request_builds_a_validated_event(self) -> None:
        spec = NewEvent.from_request(
            {
                "event_type": "user.prompt", "source_system": "codex", "host_id": HOST,
                "source_event_id": "turn-1", "payload": {"text": "hi"}, "occurred_at": HAPPENED,
            },
            actor_id=ACTOR, actor_kind="human",
        )
        self.assertEqual(spec.source_system, "codex")
        self.assertEqual(spec.payload["text"], "hi")
        self.assertIsNone(spec.session_id)


class TimestampsAreSampledPerWrite(StoreTestCase):
    def test_recorded_at_advances_instead_of_being_frozen_at_store_creation(self) -> None:
        first = self.create_project("one")["object"]["created_at"]
        self.conn.execute("UPDATE core_meta SET value = value")  # no-op, keeps the write above
        second = self.create_project("two")["object"]["created_at"]
        self.assertNotEqual(first, second)

    def test_created_at_and_updated_at_come_from_the_accepting_event(self) -> None:
        result = self.create_project("demo")
        record, event = result["object"], result["event"]
        self.assertEqual(record["created_at"], event["recorded_at"])
        self.assertEqual(record["updated_at"], event["recorded_at"])
        self.assertEqual(record["source_event_id"], event["event_id"])

    def test_a_session_started_at_comes_from_its_event(self) -> None:
        result = self.create_session()
        self.assertEqual(result["object"]["started_at"], result["event"]["occurred_at"])


class IdentityIsNotTakenOnTrust(StoreTestCase):
    OTHER_ACTOR = "act_01K742SG00YPWF74TSXEKA3254"

    def test_actor_kind_is_read_from_the_registry_not_from_the_caller(self) -> None:
        with db.transaction(self.conn):
            self.registry.upsert_actor(self.OTHER_ACTOR, kind="system")
        result = self.objects.create(
            NewObject.project({"name": "demo", "host_id": HOST}), actor_id=self.OTHER_ACTOR
        )
        self.assertEqual(result["event"]["actor_kind"], "system")

    def test_a_body_naming_another_actor_is_refused(self) -> None:
        with db.transaction(self.conn):
            self.registry.upsert_actor(self.OTHER_ACTOR, kind="system")
        with self.assertRaises(errors.ActorMismatch) as ctx:
            self.create_project("demo", actor_id=self.OTHER_ACTOR)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "actor_mismatch")
        self.assertEqual(self.count("events"), 0)
        self.assertEqual(self.count("projects"), 0)

    def test_a_body_naming_the_same_actor_is_accepted(self) -> None:
        result = self.create_project("demo", actor_id=ACTOR)
        self.assertEqual(result["event"]["actor_id"], ACTOR)

    def test_an_unregistered_actor_is_refused(self) -> None:
        with self.assertRaises(errors.NotFound) as ctx:
            self.objects.create(
                NewObject.project({"name": "demo", "host_id": HOST}), actor_id=self.OTHER_ACTOR
            )
        self.assertEqual(ctx.exception.code, "actor_not_found")

    def test_an_unregistered_host_is_refused(self) -> None:
        with self.assertRaises(errors.NotFound) as ctx:
            self.objects.create(
                NewObject.project({"name": "demo", "host_id": "hst_01K742SG00YPWF74TSXEKA3254"}),
                actor_id=ACTOR,
            )
        self.assertEqual(ctx.exception.code, "host_not_found")
        self.assertEqual(self.count("events"), 0)

    def test_an_unregistered_host_on_a_raw_event_is_refused(self) -> None:
        with self.assertRaises(errors.NotFound) as ctx:
            self.append(self.new_event("x", host_id="hst_01K742SG00YPWF74TSXEKA3254"))
        self.assertEqual(ctx.exception.code, "host_not_found")


class OneEventCreatesAtMostOneObject(StoreTestCase):
    def test_a_projection_cannot_claim_another_kind_of_creating_event(self) -> None:
        project_event = self.create_project("demo")["event"]["event_id"]
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO tasks (task_id, project_id, title, description, status, revision,"
                    " source_event_id, created_at, updated_at)"
                    " VALUES ('tsk_01K742SG00YPWF74TSXEKA3254',"
                    " 'prj_01K742SG000Z61XPMPFJBYH7RZ', 'x', '', 'ACTIVE', 1, ?,"
                    " '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')",
                    (project_event,),
                )
        self.assertIn("task.created", str(ctx.exception))
        self.assertEqual(self.count("tasks"), 0)

    def test_a_project_cannot_claim_a_session_event(self) -> None:
        session_event = self.create_session()["event"]["event_id"]
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO projects (project_id, name, description, status, revision,"
                    " source_event_id, created_at, updated_at) VALUES"
                    " ('prj_01K742SG000Z61XPMPFJBYH7RZ', 'x', '', 'active', 1, ?,"
                    " '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')",
                    (session_event,),
                )
        self.assertIn("project.created", str(ctx.exception))

    def test_a_session_cannot_claim_a_task_event(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"])["object"]
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO sessions (session_id, project_id, task_id, host_id, actor_id,"
                    " status, revision, source_event_id, started_at, created_at, updated_at)"
                    " VALUES ('ses_01K742SG00YPWF74TSXEKA3254', NULL, NULL, ?, ?, 'open', 1, ?,"
                    " '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')",
                    (HOST, ACTOR, task["source_event_id"]),
                )
        self.assertIn("session.started", str(ctx.exception))


class EventsCarryTheirObjectAssociation(StoreTestCase):
    def test_a_task_created_event_carries_the_project_and_task_columns(self) -> None:
        project = self.create_project("demo")["object"]
        result = self.create_task(project["project_id"], "write the ADR")
        event = result["event"]
        self.assertEqual(event["project_id"], project["project_id"])
        self.assertIsNone(event["task_id"])  # the task does not exist yet when its Event is written
        self.assertEqual(result["object"]["project_id"], project["project_id"])

    def test_a_session_event_carries_the_task_and_the_project_derived_from_it(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"])["object"]
        result = self.create_session(task_id=task["task_id"])  # no project_id given
        event = result["event"]
        self.assertEqual(event["task_id"], task["task_id"])
        self.assertEqual(event["project_id"], project["project_id"])
        self.assertEqual(result["object"]["project_id"], project["project_id"])

    def test_the_creating_event_is_findable_through_its_project(self) -> None:
        project = self.create_project("demo")["object"]
        task = self.create_task(project["project_id"])["object"]
        by_project = self.objects.events.list(project_id=project["project_id"])
        # A project.created event cannot name its own project: the row does not
        # exist until after the Event is written, and the Event is append-only.
        # Every later event in the project can, which is what makes the chain
        # walkable from a project downwards.
        self.assertEqual([e["event_type"] for e in by_project], ["task.created"])
        self.assertIsNone(self.objects.events.get(
            self.objects.list("project")[0]["source_event_id"])["project_id"])


class DeferralIsLoadBearing(DbTestCase):
    """The deferral is a design decision, so a test must depend on it."""

    def test_a_reference_to_a_session_created_later_succeeds_at_statement_time(self) -> None:
        reg = registry.Registry(self.conn)
        with db.transaction(self.conn):
            reg.upsert_host(HOST)
            reg.upsert_actor(ACTOR, kind="human")
        conn = self.conn
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(conn):
                # With a non-deferred foreign key this INSERT itself would fail.
                # It is the deferral that lets the Event land and lets the object
                # that creates the session be written in the same transaction.
                conn.execute(
                    "INSERT INTO events (event_id, seq, schema_version, event_type, source_system,"
                    " source_event_id, occurred_at, recorded_at, actor_id, actor_kind, host_id,"
                    " session_id, project_id, task_id, payload_json, body_sha256)"
                    " VALUES (?, 1, 1, 'user.prompt', 'test', NULL, '2026-09-29T00:00:00Z',"
                    " '2026-09-29T00:00:00Z', ?, 'human', ?, ?, NULL, NULL, '{}', ?)",
                    ("evt_01K742SG00KSNSRN81BXC5ZAZC", ACTOR, HOST,
                     "ses_01K742SG00CN4E98TXMDE6TBEP", "e" * 64),
                )
                self.assertIsNotNone(
                    conn.execute("SELECT 1 FROM events WHERE event_id = ?",
                                 ("evt_01K742SG00KSNSRN81BXC5ZAZC",)).fetchone()
                )
        self.assertIsNone(
            conn.execute("SELECT 1 FROM events WHERE event_id = ?",
                         ("evt_01K742SG00KSNSRN81BXC5ZAZC",)).fetchone()
        )


class NonFinitePayloadsAreRefused(StoreTestCase):
    def test_nan_and_infinity_are_refused_with_a_typed_error(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(errors.InvalidRequest):
                self.append(self.new_event_with_payload({"text": "x", "weird": value}))
        self.assertEqual(self.count("events"), 0)


if __name__ == "__main__":
    unittest.main()
