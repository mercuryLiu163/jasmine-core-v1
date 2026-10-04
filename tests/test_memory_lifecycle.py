"""P4-02 §5.4: obsolete, delete, pin and the tombstone that outlives the document.

Boundary tests against `FakeTransport`. What they establish is Core's side of the
lifecycle: what becomes recallable, what stops being recallable immediately, what
survives locally, what is claimed about the remote copy, and what an unauthorised
actor can do. The remote read-back half of P4-G07 needs a real engine.
"""

from __future__ import annotations

from memory_support import HOST, HUMAN, MemoryTestCase
from jasmine_core import db, errors, ids


class LifecycleCase(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.store.items()[0]
        self.memory_id = self.item["memory_id"]

    def command(self, action: str, **body):
        payload = {"expected_revision": self.store.item(self.memory_id)["revision"],
                   "host_id": HOST, **body}
        return getattr(self.lifecycle(), action)(
            self.memory_id, payload, actor_id=body.pop("_actor_id", HUMAN),
            actor_kind=body.pop("_actor_kind", "human"), host_id=HOST)


class Obsolete(LifecycleCase):
    def test_obsolete_excludes_from_recall_immediately_and_is_reversible_in_shape(self) -> None:
        result = self.command("obsolete", reason="the lesson was wrong")
        self.assertEqual(result["memory"]["status"], "OBSOLETE")
        self.assertEqual(result["memory"]["revision"], 2)
        self.assertEqual(result["event"]["event_type"], "memory.obsoleted")
        self.assertEqual(result["event"]["payload"]["before"]["status"], "ACTIVE")
        self.assertEqual(result["after"]["status"], "OBSOLETE")
        # The row, its provenance and its source Event all survive.
        stored = self.store.item(self.memory_id)
        self.assertIsNotNone(stored)
        self.assertEqual(self.store.sources_of(self.memory_id)[0]["source_event_id"],
                         self.item["source_event_id"])
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM events WHERE event_id=?",
                                              (self.item["source_event_id"],)).fetchone())

    def test_an_obsolete_item_is_excluded_from_a_real_recall(self) -> None:
        from jasmine_core.memory.types import RecallRequest

        self.transport.answer_for(self.item["bank_id"], self.memory_id, "a lesson",
                                  document_id=self.item["document_id"])
        self.assertEqual(self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="lesson",
            max_tokens=400)).status, "ok")
        self.command("obsolete", reason="wrong")
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="lesson", max_tokens=400))
        self.assertEqual(outcome.status, "empty")
        self.assertEqual([f.reason for f in outcome.filtered], ["obsolete"])

    def test_a_failed_remote_invalidate_never_un_obsoletes_the_item(self) -> None:
        from jasmine_core.memory.lifecycle import describe_engine_claim
        from jasmine_core.memory.transport import TransportError

        self.worker().tick()          # retain the document first
        result = self.command("obsolete", reason="wrong")
        self.assertEqual(result["job"]["kind"], "invalidate")
        # The remote soft retire is engine work; the local exclusion stands
        # whatever the engine does, and the Inspector says which half is true.
        self.assertIn("a queued job will soft-retire",
                      describe_engine_claim(self.store.item(self.memory_id)))
        original_list = self.transport.list_all_document_memories
        self.transport.list_all_document_memories = _raise(
            TransportError("engine_unreachable", "down", retryable=True))
        report = self.worker(lease_owner="worker-b").tick()
        self.assertEqual(report.outcome, "retry")
        self.assertEqual(self.store.item(self.memory_id)["status"], "OBSOLETE")
        self.assertIn("a queued job will soft-retire",
                      describe_engine_claim(self.store.item(self.memory_id)))
        # Once the engine accepts it, the claim changes -- and only then.
        self.transport.list_all_document_memories = original_list
        self.assertIn("soft-retired in the engine",
                      describe_engine_claim(self.store.item(self.memory_id), invalidated=True))

    def test_a_fresh_worker_soft_retires_the_remote_units(self) -> None:
        self.worker().tick()
        self.command("obsolete", reason="wrong")
        report = self.worker(lease_owner="worker-b").tick()
        self.assertEqual(report.outcome, "invalidated")
        self.assertEqual(report.receipt["memory_units_invalidated"], 1)
        self.assertEqual(len(self.transport.invalidated), 1)
        self.assertIn("jasmine memory", self.transport.invalidated[0][2])

    def test_obsolete_twice_is_refused(self) -> None:
        self.command("obsolete", reason="first")
        with self.assertRaises(errors.InvalidStateTransition):
            self.command("obsolete", reason="second")

    def test_a_stale_revision_is_refused_and_nothing_is_written(self) -> None:
        with self.assertRaises(errors.RevisionConflict):
            self.lifecycle().obsolete(self.memory_id, {"expected_revision": 99, "host_id": HOST},
                                      actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.assertEqual(self.store.item(self.memory_id)["status"], "ACTIVE")

    def test_a_missing_revision_is_refused(self) -> None:
        with self.assertRaises(errors.MissingExpectedRevision):
            self.lifecycle().obsolete(self.memory_id, {"host_id": HOST}, actor_id=HUMAN,
                                      actor_kind="human", host_id=HOST)

    def test_an_identical_event_id_replays_without_a_second_transition(self) -> None:
        event_id = ids.new_id("evt")
        first = self.lifecycle().obsolete(self.memory_id,
                                          {"expected_revision": 1, "host_id": HOST,
                                           "event_id": event_id},
                                          actor_id=HUMAN, actor_kind="human", host_id=HOST)
        second = self.lifecycle().obsolete(self.memory_id,
                                           {"expected_revision": 1, "host_id": HOST,
                                            "event_id": event_id},
                                           actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.store.item(self.memory_id)["revision"], 2)

    def test_an_unknown_field_is_refused(self) -> None:
        with self.assertRaises(errors.InvalidRequest):
            self.lifecycle().obsolete(self.memory_id,
                                      {"expected_revision": 1, "host_id": HOST,
                                       "force": True},
                                      actor_id=HUMAN, actor_kind="human", host_id=HOST)


class Delete(LifecycleCase):
    def test_delete_tombstones_locally_and_queues_the_remote_removal(self) -> None:
        self.worker().tick()
        self.assertEqual(len(self.transport.documents), 1)
        result = self.command("delete", reason="user asked")
        self.assertEqual(result["memory"]["status"], "DELETED")
        self.assertEqual(result["memory"]["engine_state"], "DELETE_PENDING")
        self.assertIsNotNone(result["job"])
        self.assertEqual(result["job"]["kind"], "delete")
        self.assertEqual(result["job"]["status"], "PENDING")
        # Not yet claimed as removed: the remote copy is still there.
        self.assertIsNone(self.store.item(self.memory_id)["remote_deleted_at"])
        self.assertIn("queued job will remove", self.lifecycle().detail(
            self.memory_id, actor_id=HUMAN)["engine_claim"])

    def test_the_worker_removes_the_remote_document_and_confirms(self) -> None:
        self.worker().tick()
        self.command("delete", reason="user asked")
        report = self.worker(lease_owner="worker-b").tick()
        self.assertEqual(report.outcome, "deleted")
        self.assertEqual(self.transport.deleted,
                         [(self.item["bank_id"], self.item["document_id"])])
        from jasmine_core.memory import scope as scope_module

        self.assertIsNone(
            self.transport.get_document(bank=scope_module.project_bank(self.project_id),
                                        document_id=self.item["document_id"]),
            "the remote document must be gone, not merely hidden")
        stored = self.store.item(self.memory_id)
        self.assertEqual(stored["engine_state"], "DELETED")
        self.assertIsNotNone(stored["remote_deleted_at"])
        self.assertIn("were removed", self.lifecycle().detail(
            self.memory_id, actor_id=HUMAN)["engine_claim"])

    def test_a_failed_remote_delete_still_excludes_the_item(self) -> None:
        from jasmine_core.memory.transport import TransportError

        self.worker().tick()
        self.command("delete", reason="user asked")
        self.transport.delete_document = _raise(TransportError(
            "engine_server_error", "boom", retryable=True))
        report = self.worker(lease_owner="worker-b").tick()
        self.assertEqual(report.outcome, "retry")
        stored = self.store.item(self.memory_id)
        self.assertEqual(stored["status"], "DELETED", "exclusion is local and immediate")
        self.assertEqual(stored["engine_state"], "DELETE_FAILED")
        self.assertIsNone(stored["remote_deleted_at"],
                          "a failed deletion must never be reported as done")
        self.assertIn("may still exist", self.lifecycle().detail(
            self.memory_id, actor_id=HUMAN)["engine_claim"])

    def test_the_source_of_truth_survives_a_delete(self) -> None:
        from jasmine_core.authority import AuthorityStore

        self.worker().tick()
        self.command("delete", reason="user asked")
        rule = AuthorityStore(self.conn, schema_version=7).get(self.item["origin_rule_id"])
        self.assertEqual(rule["status"], "ACTIVE", "deleting a memory is not retiring a Rule")
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM events WHERE event_id=?",
                                               (self.item["source_event_id"],)).fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM tasks WHERE task_id=?",
                                               (self.task_id,)).fetchone())

    def test_a_tombstone_cannot_be_changed_again(self) -> None:
        self.command("delete", reason="user asked")
        for action in ("obsolete", "delete", "pin", "unpin"):
            with self.assertRaises(errors.InvalidStateTransition, msg=action):
                self.command(action)

    def test_the_row_itself_can_never_be_deleted(self) -> None:
        self.command("delete", reason="user asked")
        with self.assertRaises(Exception) as caught:
            with db.transaction(self.conn):
                self.conn.execute("DELETE FROM memory_items WHERE memory_id=?", (self.memory_id,))
        self.assertIn("never deleted", str(caught.exception))


class Pin(LifecycleCase):
    def test_pin_changes_ordering_metadata_only(self) -> None:
        result = self.command("pin")
        self.assertEqual(result["memory"]["pinned"], True)
        self.assertEqual(result["memory"]["status"], "ACTIVE")
        self.assertEqual(result["memory"]["engine_state"], "PENDING_RETAIN")
        summary = self.lifecycle().detail(self.memory_id, actor_id=HUMAN)["lifecycle"]
        self.assertEqual(summary["pin_affects"], "inspector_ordering_only")

    def test_pin_does_not_change_the_budget_or_the_observation_about_a_rule(self) -> None:
        from jasmine_core.memory.types import RecallRequest

        self.command("pin")
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="anything", max_tokens=400))
        self.assertEqual(outcome.items, (), "a pin does not make anything recallable")
        self.assertEqual(outcome.budget_tokens, 384)

    def test_pin_does_not_reactivate_an_obsolete_item(self) -> None:
        self.command("obsolete", reason="wrong")
        with self.assertRaises(errors.InvalidStateTransition) as caught:
            self.command("pin")
        self.assertIn("cannot be pinned", caught.exception.message)
        self.assertEqual(self.store.item(self.memory_id)["pinned"], False)

    def test_obsolete_clears_a_pin(self) -> None:
        self.command("pin")
        self.command("obsolete", reason="wrong")
        self.assertEqual(self.store.item(self.memory_id)["pinned"], False)

    def test_unpin_is_idempotent_in_effect(self) -> None:
        self.command("pin")
        self.assertEqual(self.command("unpin")["memory"]["pinned"], False)
        self.assertEqual(self.command("unpin")["memory"]["pinned"], False)

    def test_a_pinned_item_stays_non_authoritative(self) -> None:
        from jasmine_core.memory.types import RecallRequest

        self.command("pin")
        self.worker().tick()
        self.transport.answer_for(self.item["bank_id"], self.memory_id,
                                  "V1 uses Hindsight as the long-term memory engine",
                                  document_id=self.item["document_id"])
        item = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="memory engine",
            max_tokens=400)).items[0]
        self.assertTrue(item.pinned)
        self.assertTrue(item.historical)
        self.assertEqual(self.store.item(self.memory_id)["revision"], 2,
                         "only the pin command changed the revision; a recall did not")


class Permissions(LifecycleCase):
    def test_an_agent_cannot_change_the_memory_lifecycle(self) -> None:
        for action in ("obsolete", "delete", "pin", "unpin"):
            with self.assertRaises(errors.ForbiddenActorKind, msg=action):
                getattr(self.lifecycle(), action)(
                    self.memory_id, {"expected_revision": 1, "host_id": HOST},
                    actor_id=self.agent_actor, actor_kind="agent", host_id=HOST)

    def test_a_system_actor_may(self) -> None:
        result = self.lifecycle().pin(self.memory_id, {"expected_revision": 1, "host_id": HOST},
                                      actor_id=self.system_actor, actor_kind="system",
                                      host_id=HOST)
        self.assertEqual(result["memory"]["pinned"], True)
        self.assertEqual(result["event"]["actor_kind"], "system")

    def test_acl_mode_gates_both_the_read_and_the_write_path(self) -> None:
        from jasmine_core.memory import scope
        from jasmine_core.memory.lifecycle import MemoryLifecycle
        from jasmine_core.memory.store import MemoryStore

        from jasmine_core.memory.jobs import JobStore
        from jasmine_core.memory.eligibility import SourceRegistry

        store = MemoryStore(self.conn, schema_version=9)
        acl = MemoryLifecycle(self.conn, store, mode=scope.ACL)
        # A second project's Memory, and a grant for the first project only.
        other = self.project("other")
        self.active_decision(project_id=other, rule_key="other.engine")
        JobStore(self.conn, store).enqueue_from_sources(SourceRegistry())
        foreign = next(item for item in store.items() if item["project_id"] == other)
        self.grant_project(self.project_id, actor_id=self.agent_actor)

        # Granted: the read succeeds.
        acl.detail(self.memory_id, actor_id=self.agent_actor)
        # Not granted: the read is a 404, and so is the write. The actor kind is
        # `human` so the refusal cannot be mistaken for the actor-kind check.
        with self.assertRaises(errors.NotFound):
            acl.detail(foreign["memory_id"], actor_id=self.agent_actor)
        with self.assertRaises(errors.NotFound):
            acl.obsolete(foreign["memory_id"], {"expected_revision": 1, "host_id": HOST},
                         actor_id=self.agent_actor, actor_kind="human", host_id=HOST)
        self.assertEqual(store.item(foreign["memory_id"])["status"], "ACTIVE",
                         "an ungranted actor changed the item in acl mode")

    def test_the_command_event_records_before_and_after_without_the_text(self) -> None:
        result = self.command("obsolete", reason="wrong")
        payload = result["event"]["payload"]
        self.assertEqual(set(payload) >= {"before", "after", "action", "memory_id"}, True)
        self.assertNotIn("sanitized_text", json_text(payload))
        self.assertNotIn("V1 uses Hindsight", json_text(payload))


def json_text(value) -> str:
    import json

    return json.dumps(value)


def _raise(error):
    def raiser(**_kwargs):
        raise error

    return raiser
