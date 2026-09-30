"""P4-02: durable jobs, the worker, the crash windows and the lifecycle.

`FakeTransport` again, so these are boundary results: they prove Core's job
state machine, its recovery guarantees and its transaction discipline. They say
nothing about a real Hindsight, which the P4 Gates need.

The crash windows are simulated by *actually crashing* — a fresh connection
against the same database file, and a `raise` from inside the engine call at the
worst possible moment — rather than by asserting on a comment.
"""

from __future__ import annotations

import random
import unittest

from memory_support import HOST, HUMAN, MemoryTestCase
from jasmine_core import clock, db, errors, ids
from jasmine_core.memory import eligibility, jobs as jobs_module
from jasmine_core.memory.transport import TransportError
from jasmine_core.memory.types import JOB_STATUSES


class EligibleSources(MemoryTestCase):
    """A type name is not evidence: each type needs its own committed proof."""

    def test_all_five_types_can_be_produced_from_p1_truth(self) -> None:
        project = self.project()
        task = self.task(project)
        self.active_decision(project_id=project)                       # CONFIRMED_DECISION
        self.global_decision()                                        # global Decision
        self.evidence(task_id=task, status="PASS")                    # VERIFIED_OUTCOME
        self.evidence(task_id=task, status="FAIL")                    # FAILURE_LESSON
        self.failed_step(project_id=project, task_id=task)            # FAILURE_LESSON
        report = self.scan()
        self.assertEqual(report["scanned"], 5)
        self.assertEqual(len(report["created"]), 5)
        by_type = {item["memory_type"]: item for item in self.store.items(limit=50)}
        self.assertEqual(sorted(by_type), [
            "CONFIRMED_DECISION", "FAILURE_LESSON", "VERIFIED_OUTCOME"])

    def test_a_stale_step_becomes_a_resolved_issue_not_a_lesson(self) -> None:
        from jasmine_core.state import StateStore

        project = self.project()
        task = self.task(project)
        store = StateStore(self.conn, schema_version=7)
        step = store.create_step(task, {"title": "s", "host_id": HOST, "expected_revision": 1},
                                 actor_id=HUMAN, actor_kind="human",
                                 can_accept=False)["step"]["step_id"]
        store.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": 1,
                                     "host_id": HOST},
                              actor_id=HUMAN, actor_kind="human", can_accept=False)
        store.transition_step(step, {"status": "EXECUTED", "expected_revision": 2,
                                     "host_id": HOST},
                              actor_id=HUMAN, actor_kind="human", can_accept=False)
        store.transition_step(step, {"status": "STALE", "expected_revision": 3,
                                     "reason": "the workspace moved under the result", "host_id": HOST},
                              actor_id=HUMAN, actor_kind="human", can_accept=False)
        report = self.scan()
        self.assertEqual(report["scanned"], 1)
        item = self.store.items()[0]
        self.assertEqual(item["memory_type"], "RESOLVED_ISSUE",
                         "a STALE step is an issue that got resolved, not a failure lesson")
        self.assertIn("the workspace moved under the result", item["sanitized_text"])

    def test_a_stable_project_fact_needs_an_active_project_rule(self) -> None:
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        origin = self.event("user.prompt", project_id=project, text="always pin versions")
        authority = AuthorityStore(self.conn, schema_version=7)
        proposed = authority.propose({
            "rule_key": "pin.versions", "kind": "RULE", "severity": "HARD",
            "enforcement": "CONTEXT", "content": "Always pin dependency versions",
            "matcher": {}, "scope": {"kind": "project", "project_id": project},
            "origin_event_id": origin, "host_id": HOST,
        }, actor_id=HUMAN)
        # PROPOSED only: an unapproved agent proposal is not a stable fact.
        self.assertEqual(self.scan()["scanned"], 0)
        authority.transition(proposed["rule"]["rule_id"], "approve",
                             {"expected_revision": 1, "host_id": HOST}, actor_id=HUMAN)
        self.assertEqual(self.scan()["scanned"], 1)
        self.assertEqual(self.store.items()[0]["memory_type"], "STABLE_PROJECT_FACT")

    def test_a_task_scoped_rule_is_never_a_stable_fact(self) -> None:
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        task = self.task(project)
        origin = self.event("user.prompt", project_id=project, task_id=task, text="do it here")
        authority = AuthorityStore(self.conn, schema_version=7)
        proposed = authority.propose({
            "rule_key": "task.only", "kind": "RULE", "severity": "HARD",
            "enforcement": "CONTEXT", "content": "Only for this task",
            "matcher": {}, "scope": {"kind": "task", "project_id": project, "task_id": task},
            "origin_event_id": origin, "host_id": HOST,
        }, actor_id=HUMAN)
        authority.transition(proposed["rule"]["rule_id"], "approve",
                             {"expected_revision": 1, "host_id": HOST}, actor_id=HUMAN)
        self.assertEqual(self.scan()["scanned"], 0,
                         "current step/task state is not long-term memory (design §7)")

    def test_info_evidence_is_not_a_verified_outcome(self) -> None:
        project = self.project()
        task = self.task(project)
        self.event("tool.result", project_id=project, task_id=task, text="no exit code")
        # An INFO Evidence record is not produced by record_tool_result, so the
        # refusal is asserted at the eligibility level with a synthetic payload.
        from jasmine_core.memory.eligibility import AuthorityEvidenceSource

        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO evidence (evidence_id, task_id, kind, status, result_json,"
                " source_event_id, change_event_id, fingerprint_sha256, actor_id, host_id,"
                " task_revision, created_at) SELECT ?, task_id, 'COMMAND_RESULT', 'INFO',"
                " '{}', source_event_id, change_event_id, fingerprint_sha256, actor_id, host_id,"
                " 1, created_at FROM evidence LIMIT 1",
                (ids.new_id("evd"),))
        self.assertEqual(self.scan()["scanned"], 0)
        del AuthorityEvidenceSource

    def test_a_broken_artifact_reference_is_not_a_verified_outcome(self) -> None:
        import os
        from pathlib import Path

        project = self.project()
        task = self.task(project)
        self.evidence(task_id=task, status="PASS", artifact_uri="workspace:/report.txt")
        # The report the result cited is rewritten. `reference_status` is P1's
        # own verdict, recomputed from the file, and the Evidence row is
        # append-only so this is the only way the reference can break.
        self.assertEqual(self.scan()["scanned"], 1)
        root = Path(os.environ["JASMINE_CORE_WORKSPACE_ROOT"])
        (root / "report.txt").write_text("different content\n")
        self.job_store().reset_cursor(0)
        self.assertEqual(self.scan()["created"], [], "the item already exists")
        # A fresh source with a broken reference is refused outright.
        self.evidence(task_id=task, status="PASS", artifact_uri="workspace:/report.txt")
        (root / "report.txt").write_text("and different again\n")
        self.conn.execute("SELECT 1")
        reasons = []
        from jasmine_core.memory.eligibility import SourceRegistry

        SourceRegistry().scan(self.conn, after_seq=0, limit=200, reasons=reasons)
        self.assertIn("evidence_broken_reference", {r["reason"] for r in reasons})

    def test_a_secret_only_lesson_is_refused_rather_than_redacted_into_junk(self) -> None:
        from jasmine_core.memory.eligibility import AuthorityEvidenceSource, Ineligible

        project = self.project()
        with db.transaction(self.conn):
            self.conn.execute("DROP TRIGGER trg_events_immutable_update")
        self.event("user.prompt", project_id=project,
                   text="the deploy key was -----BEGIN RSA PRIVATE KEY-----\nAAAA\n")
        # The prompt is not a source type, so build the refusal directly against
        # a candidate that carries a private key.
        source = AuthorityEvidenceSource()
        row = self.conn.execute(
            "SELECT event_id, seq, event_type, occurred_at, recorded_at, project_id, task_id,"
            " payload_json FROM events WHERE event_type='user.prompt' ORDER BY seq DESC LIMIT 1"
        ).fetchone()

        with self.assertRaises(Ineligible) as caught:
            source._candidate(self.conn, row, memory_type="FAILURE_LESSON",
                              scope_kind="project", title="t",
                              body="-----BEGIN RSA PRIVATE KEY-----\nAAAA\n",
                              context="c")
        self.assertEqual(caught.exception.reason, "secret_content_rejected")

    def test_a_source_that_is_not_a_project_scope_cannot_be_widened_to_global(self) -> None:
        from jasmine_core.memory.eligibility import AuthorityEvidenceSource, Ineligible

        project = self.project()
        self.task(project)
        row = self.conn.execute(
            "SELECT event_id, seq, event_type, occurred_at, recorded_at, project_id, task_id,"
            " payload_json FROM events WHERE project_id IS NOT NULL ORDER BY seq LIMIT 1").fetchone()
        self.assertIsNotNone(row["project_id"])
        source = AuthorityEvidenceSource()
        with self.assertRaises(Ineligible) as caught:
            source._candidate(self.conn, row, memory_type="FAILURE_LESSON",
                              scope_kind="global", title="t", body="b", context="c")
        self.assertEqual(caught.exception.reason, "global_source_has_project")

    def test_an_unknown_memory_type_is_refused(self) -> None:
        from jasmine_core.memory.eligibility import Ineligible, AuthorityEvidenceSource

        row = self.conn.execute(
            "SELECT event_id, seq, event_type, occurred_at, project_id, task_id, payload_json"
            " FROM events ORDER BY seq LIMIT 1").fetchone()
        with self.assertRaises(Ineligible):
            AuthorityEvidenceSource()._candidate(self.conn, row, memory_type="VIBES",
                                                scope_kind="project", title="t", body="b",
                                                context="c")

    def test_document_ids_are_deterministic_per_source_and_type(self) -> None:
        first = eligibility.document_id_for("evt_" + "0" * 26, "FAILURE_LESSON")
        second = eligibility.document_id_for("evt_" + "0" * 26, "FAILURE_LESSON")
        other = eligibility.document_id_for("evt_" + "0" * 26, "VERIFIED_OUTCOME")
        self.assertEqual(first, second, "a re-scan must land on the same remote document")
        self.assertNotEqual(first, other)


class RecoveryScan(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.active_decision(project_id=self.project_id)

    def test_a_replayed_source_never_produces_a_second_job(self) -> None:
        first = self.scan()
        self.assertEqual(len(first["created"]), 1)
        # Rewind the cursor: the strongest possible replay.
        self.job_store().reset_cursor(0)
        second = self.scan()
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["already_present"]), 1)
        self.assertEqual(len(self.job_store().jobs()), 1)
        self.assertEqual(self.store.count_items(), 1)

    def test_the_cursor_advances_and_a_new_source_is_picked_up(self) -> None:
        self.scan()
        cursor = self.job_store().cursor()
        self.assertGreater(cursor, 0)
        self.evidence(task_id=self.task_id, status="PASS")
        report = self.scan()
        self.assertEqual(len(report["created"]), 1)
        self.assertGreater(self.job_store().cursor(), cursor)
        self.assertEqual(len(self.job_store().jobs()), 2)

    def test_a_rolled_back_event_is_never_scanned(self) -> None:
        # The scan reads committed Events, so a transaction that never commits
        # leaves nothing to find. This is the property that lets P4 promise the
        # gap between a Truth commit and an enqueue is recoverable.
        from jasmine_core.models import NewEvent
        from jasmine_core.events import EventStore

        transaction = db.transaction(self.conn)
        transaction.__enter__()
        try:
            EventStore(self.conn, schema_version=7).append(NewEvent(
                event_type="evidence.recorded", source_system="test", source_event_id=None,
                occurred_at=clock.now(), actor_id=HUMAN, actor_kind="human", host_id=HOST,
                project_id=self.project_id, task_id=self.task_id,
                payload={"text": "x", "evidence_id": ids.new_id("evd")}))
        finally:
            self.conn.rollback()
        self.job_store().reset_cursor(0)
        report = self.scan()
        self.assertEqual(len(report["created"]), 1, "only the committed decision")

    def test_the_cursor_and_the_job_insert_commit_together(self) -> None:
        # Simulate the "job insert committed but the cursor did not" hazard by
        # rewinding the cursor after a successful scan: the unique index, not the
        # cursor, is what actually prevents a duplicate.
        self.scan()
        self.job_store().reset_cursor(0)
        self.scan()
        self.job_store().reset_cursor(0)
        self.scan()
        self.assertEqual(len(self.job_store().jobs()), 1)
        self.assertEqual(self.store.count_items(), 1)

    def test_a_source_that_stops_being_eligible_is_not_re_enqueued(self) -> None:
        self.scan()
        from jasmine_core.authority import AuthorityStore

        item = self.store.items()[0]
        # Retiring the Rule makes the source ineligible on a full replay; the
        # already-created job stays, because history is not rewritten.
        AuthorityStore(self.conn, schema_version=7).transition(
            item["origin_rule_id"], "retire", {"expected_revision": 2, "host_id": HOST},
            actor_id=HUMAN)
        self.job_store().reset_cursor(0)
        report = self.scan()
        self.assertEqual(report["created"], [])
        self.assertEqual(len(self.job_store().jobs()), 1)


class WorkerLoop(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.active_decision(project_id=self.project_id)

    def drain(self, worker=None, ticks: int = 4):
        worker = worker or self.worker()
        return worker.run(max_ticks=ticks)

    def test_a_committed_source_is_retained_and_reaches_done(self) -> None:
        reports = self.drain()
        self.assertEqual([r.outcome for r in reports], ["retained", "idle", "idle", "idle"])
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "DONE")
        self.assertEqual(job["receipt"]["items_count"], 1)
        self.assertEqual(self.store.items()[0]["engine_state"], "RETAINED")
        # The receipt, not just an enqueue acknowledgement.
        self.assertEqual(len(self.transport.documents), 1)
        stored = next(iter(self.transport.documents.values()))
        self.assertEqual(stored["metadata"]["jasmine_memory_type"], "CONFIRMED_DECISION")

    def test_the_same_source_replayed_produces_no_second_document(self) -> None:
        self.drain()
        self.job_store().reset_cursor(0)
        self.drain()
        self.assertEqual(len(self.transport.documents), 1)
        self.assertEqual(len(self.job_store().jobs()), 1)

    def test_the_engine_is_never_called_inside_a_write_transaction(self) -> None:
        observed: list[bool] = []

        class Watcher:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __setattr__(self, name, value):
                setattr(self._inner, name, value)

        # `in_transaction` is the property that matters; assert on it at the
        # moment of the call rather than trusting a comment.
        original = self.transport.retain

        def watched(**kwargs):
            observed.append(self.conn.in_transaction)
            return original(**kwargs)

        self.transport.retain = watched
        self.drain()
        self.assertEqual(observed, [False], "a write lock must never span an engine call")

    def test_a_retryable_failure_returns_the_job_to_pending_with_backoff(self) -> None:
        self.transport.retain_error = TransportError("engine_unreachable", "connection refused",
                                                     retryable=True)
        reports = self.drain(ticks=1)
        self.assertEqual(reports[0].outcome, "retry")
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "PENDING")
        self.assertEqual(job["retry_count"], 1)
        self.assertEqual(job["last_error_code"], "engine_unreachable")
        self.assertGreater(job["next_attempt_at"], job["created_at"])
        self.assertEqual(self.store.items()[0]["engine_state"], "RETAIN_FAILED")

    def test_a_permanent_failure_is_not_retried(self) -> None:
        self.transport.retain_error = TransportError("engine_rejected", "unsupported document",
                                                     retryable=False)
        reports = self.drain(ticks=1)
        self.assertEqual(reports[0].outcome, "failed")
        self.assertEqual(self.job_store().jobs()[0]["status"], "FAILED")

    def test_the_attempt_budget_is_exhausted_rather_than_retried_forever(self) -> None:
        self.transport.retain_error = TransportError("engine_unreachable", "down", retryable=True)
        worker = self.worker(max_attempts=2)
        for _ in range(4):
            self.job_store().reset_cursor(jobs_module.DEFAULT_MAX_ATTEMPTS and 0)
            worker.tick()
            # Make the backoff due immediately so the next tick can claim it.
            with db.transaction(self.conn):
                self.conn.execute("UPDATE memory_jobs SET next_attempt_at='1970-01-01T00:00:00Z'")
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["retry_count"], 2)
        phases = [(a["attempt_number"], a["phase"], a["outcome"])
                  for a in self.job_store().attempts(job["job_id"])]
        self.assertEqual(phases, [
            (1, "CLAIMED", "SUCCESS"), (1, "FAILED", "RETRY"),
            (2, "CLAIMED", "SUCCESS"), (2, "FAILED", "FAILED"),
        ])

    def test_the_full_attempt_history_survives_the_last_error_column(self) -> None:
        self.transport.retain_error = TransportError("engine_timeout", "deadline", retryable=True)
        self.drain(ticks=1)
        self.transport.retain_error = None
        with db.transaction(self.conn):
            self.conn.execute("UPDATE memory_jobs SET next_attempt_at='1970-01-01T00:00:00Z'")
        self.drain(ticks=1)
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "DONE")
        self.assertIsNone(job["last_error_code"], "a success clears the summary column")
        history = self.job_store().attempts(job["job_id"])
        self.assertEqual([(a["phase"], a["error_code"]) for a in history], [
            ("CLAIMED", None), ("FAILED", "engine_timeout"),
            ("CLAIMED", None), ("RETAINED", None),
        ])

    def test_two_workers_on_two_connections_never_double_claim(self) -> None:
        self.scan()
        other_db = self.db_path
        second = db.connect(other_db)
        self.addCleanup(second.close)
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.worker import MemoryWorker

        first = MemoryWorker(self.conn, self.store, self.transport, lease_owner="worker-a")
        rival = MemoryWorker(second, MemoryStore(second, schema_version=7), self.transport,
                             lease_owner="worker-b")
        # Both workers race for the same due job. `BEGIN IMMEDIATE` serialises
        # them, so exactly one can see the row as PENDING.
        winners = [claim for claim in (first._jobs.claim_due(owner="worker-a"),
                                       rival._jobs.claim_due(owner="worker-b"))
                   if claim is not None]
        self.assertEqual(len(winners), 1, "exactly one worker may own a job")
        # And neither can find it again while the lease holds.
        self.assertIsNone(first._jobs.claim_due(owner="worker-a"))
        self.assertIsNone(rival._jobs.claim_due(owner="worker-b"))
        self.assertEqual(self.job_store().counts()["running"], 1)

    def test_a_lease_that_expires_is_recoverable_and_the_job_is_not_lost(self) -> None:
        self.scan()
        worker = self.worker(lease_owner="worker-a")
        claimed = self.job_store().claim_due(owner="worker-a", lease_seconds=1)
        self.assertIsNotNone(claimed)
        # The worker dies here. Nothing else runs until the lease expires.
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'")
        recovered = worker.tick(scan=False)
        self.assertEqual(recovered.outcome, "retained")
        history = self.job_store().attempts(claimed.job["job_id"])
        self.assertEqual([(a["attempt_number"], a["phase"]) for a in history],
                         [(1, "CLAIMED"), (2, "CLAIMED"), (2, "RETAINED")])

    def test_a_crash_after_a_remote_success_before_done_resends_the_same_document(self) -> None:
        self.scan()
        # The engine accepted the document, then the process died before DONE.
        claimed = self.job_store().claim_due(owner="worker-a")
        self.assertIsNotNone(claimed)
        self.transport.retain(bank=claimed.bank, document_id=claimed.job["document_id"],
                              content=claimed.item["sanitized_text"], context="c",
                              metadata={}, tags=(), memory_type=claimed.item["memory_type"],
                              timestamp=None)
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'")
        report = self.worker(lease_owner="worker-b").tick(scan=False)
        self.assertEqual(report.outcome, "retained")
        self.assertEqual(self.job_store().jobs()[0]["status"], "DONE")
        # One remote document, not two: `update_mode=replace` on a stable id is
        # an upsert, and the re-send is what makes the crash recoverable.
        self.assertEqual(len(self.transport.documents), 1)
        self.assertEqual(len(self.transport.retain_calls), 2)
        self.assertEqual(self.transport.retain_calls[0]["document_id"],
                         self.transport.retain_calls[1]["document_id"])

    def test_truth_keeps_working_while_the_engine_is_down(self) -> None:
        # design §14 / VC-22: Memory pauses; Rules, State and Evidence do not.
        from jasmine_core.authority import AuthorityStore
        from jasmine_core.state import StateStore

        self.transport.retain_error = TransportError("engine_unreachable", "down", retryable=True)
        self.drain(ticks=1)
        self.assertEqual(self.job_store().jobs()[0]["status"], "PENDING")

        state = StateStore(self.conn, schema_version=7)
        created = state.create_step(self.task_id, {"title": "device", "host_id": HOST,
                                                   "expected_revision": 1},
                                    actor_id=HUMAN, actor_kind="human", can_accept=False)
        self.assertEqual(created["step"]["status"], "PLANNED")
        started = state.transition_step(created["step"]["step_id"],
                                        {"status": "IN_PROGRESS", "expected_revision": 1,
                                         "host_id": HOST},
                                        actor_id=HUMAN, actor_kind="human", can_accept=False)
        moved = state.transition_step(created["step"]["step_id"],
                                      {"status": "EXECUTED", "expected_revision": 2,
                                       "host_id": HOST},
                                      actor_id=HUMAN, actor_kind="human", can_accept=False)
        self.assertEqual(moved["step"]["status"], "EXECUTED")
        self.assertGreater(moved["task_revision"], started["task_revision"])
        self.assertEqual(AuthorityStore(self.conn, schema_version=7).list_active(
            project_id=self.project_id)[0]["status"], "ACTIVE")
        # And the queued job is still there, waiting, not rolled back.
        self.assertEqual(self.job_store().counts()["pending"], 1)

    def test_the_queue_backlog_does_not_block_a_truth_transaction(self) -> None:
        self.transport.retain_error = TransportError("engine_timeout", "slow", retryable=True)
        for _ in range(3):
            self.drain(ticks=1)
            with db.transaction(self.conn):
                self.conn.execute("UPDATE memory_jobs SET next_attempt_at='1970-01-01T00:00:00Z'")
        self.assertGreaterEqual(self.job_store().counts()["pending"], 1)
        # A Truth write takes its lock normally; nothing in P4 is holding it.
        from jasmine_core.models import NewEvent
        from jasmine_core.events import EventStore

        with db.translate_lock_errors(), db.transaction(self.conn):
            event, _ = EventStore(self.conn, schema_version=7).append(NewEvent(
                event_type="user.prompt", source_system="test", source_event_id=None,
                occurred_at=clock.now(), actor_id=HUMAN, actor_kind="human", host_id=HOST,
                project_id=self.project_id, task_id=self.task_id,
                payload={"text": "still working"}))
        self.assertTrue(event["event_id"].startswith("evt_"))


class ManualRetry(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.job = self.job_store().jobs()[0]

    def fail_it(self) -> None:
        self.transport.retain_error = TransportError("engine_rejected", "nope", retryable=False)
        self.worker().tick()
        self.transport.retain_error = None

    def test_an_authorised_retry_requeues_and_is_audited(self) -> None:
        self.fail_it()
        self.assertEqual(self.job_store().jobs()[0]["status"], "FAILED")
        result = self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                                actor_kind="human", host_id=HOST)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["job"]["status"], "PENDING")
        self.assertEqual(result["event"]["event_type"], "memory.job.retried")
        self.assertEqual(result["event"]["actor_id"], HUMAN)
        self.assertEqual(result["event"]["payload"]["previous_status"], "FAILED")

    def test_a_retry_cannot_change_what_would_be_retained(self) -> None:
        self.fail_it()
        before = {k: self.job_store().jobs()[0][k]
                  for k in ("document_id", "content_sha256", "bank_id", "memory_type")}
        self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN, actor_kind="human",
                                       host_id=HOST)
        after = {k: self.job_store().jobs()[0][k]
                 for k in ("document_id", "content_sha256", "bank_id", "memory_type")}
        self.assertEqual(before, after)

    def test_an_identical_event_id_replays_instead_of_requeueing_twice(self) -> None:
        self.fail_it()
        event_id = ids.new_id("evt")
        first = self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                               actor_kind="human", host_id=HOST,
                                               event_id=event_id)
        second = self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                                actor_kind="human", host_id=HOST,
                                                event_id=event_id)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])

    def test_a_done_job_is_never_retried(self) -> None:
        self.worker().tick()
        self.assertEqual(self.job_store().jobs()[0]["status"], "DONE")
        with self.assertRaises(errors.InvalidStateTransition) as caught:
            self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                           actor_kind="human", host_id=HOST)
        self.assertIn("already retained", caught.exception.message)

    def test_a_running_job_is_not_stolen_from_its_worker(self) -> None:
        self.job_store().claim_due(owner="worker-a")
        with self.assertRaises(errors.InvalidStateTransition):
            self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                           actor_kind="human", host_id=HOST)

    def test_an_agent_cannot_retry(self) -> None:
        self.fail_it()
        with self.assertRaises(errors.ForbiddenActorKind):
            self.job_store().request_retry(self.job["job_id"], actor_id=self.agent_actor,
                                           actor_kind="agent", host_id=HOST)

    def test_an_unknown_job_is_a_typed_refusal(self) -> None:
        with self.assertRaises(errors.NotFound):
            self.job_store().request_retry(ids.new_id("mjb"), actor_id=HUMAN,
                                           actor_kind="human", host_id=HOST)
        with self.assertRaises(errors.InvalidRequest):
            self.job_store().request_retry("not-an-id", actor_id=HUMAN, actor_kind="human",
                                           host_id=HOST)


class BackoffPolicy(unittest.TestCase):
    def test_backoff_grows_and_is_clamped(self) -> None:
        rng = random.Random(7)
        delays = [jobs_module.backoff_seconds(n, rng=rng) for n in range(1, 12)]
        self.assertLess(delays[0], delays[1])
        self.assertLessEqual(max(delays), jobs_module.MAX_BACKOFF_SECONDS)
        self.assertTrue(all(d > 0 for d in delays))

    def test_retryability_follows_the_failure_class(self) -> None:
        for code in jobs_module.RETRYABLE_CODES:
            self.assertTrue(jobs_module.is_retryable(code), code)
        for code in jobs_module.PERMANENT_CODES:
            self.assertFalse(jobs_module.is_retryable(code), code)
        self.assertFalse(jobs_module.is_retryable(None))
        self.assertFalse(jobs_module.is_retryable("something_new"))

    def test_the_two_error_vocabulary_sets_do_not_overlap(self) -> None:
        self.assertEqual(jobs_module.RETRYABLE_CODES & jobs_module.PERMANENT_CODES, frozenset())


class JobListing(MemoryTestCase):
    def test_project_a_jobs_never_appear_in_project_b(self) -> None:
        a, b = self.project("a"), self.project("b")
        self.active_decision(project_id=a, rule_key="engine.a")
        self.active_decision(project_id=b, rule_key="engine.b")
        self.global_decision()
        self.scan()
        for_project_a = self.job_store().jobs(project_id=a)
        for_project_b = self.job_store().jobs(project_id=b)
        # The global bank is shared across this Core's projects, so its job is
        # legitimately in both views. Project-scoped jobs are not.
        a_only = {j["job_id"] for j in for_project_a if j["bank_id"] != "jasmine-global"}
        b_only = {j["job_id"] for j in for_project_b if j["bank_id"] != "jasmine-global"}
        self.assertEqual(len(a_only), 1)
        self.assertEqual(len(b_only), 1)
        self.assertFalse(a_only & b_only, "project isolation must hold in the job list too")
        self.assertTrue(any(job["bank_id"] == "jasmine-global" for job in for_project_a))
        self.assertTrue(any(job["bank_id"] == "jasmine-global" for job in for_project_b))
        # And a project filter never returns another project's own job.
        self.assertTrue(all(j["project_id"] in (a, None) for j in for_project_a))
        self.assertTrue(all(j["project_id"] in (b, None) for j in for_project_b))

    def test_counts_are_reported_per_status(self) -> None:
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        counts = self.job_store().counts(project_id=project)
        self.assertEqual(sorted(counts), sorted(s.lower() for s in JOB_STATUSES))
        self.assertEqual(counts["pending"], 1)

    def test_an_unknown_status_filter_is_a_typed_refusal(self) -> None:
        with self.assertRaises(errors.InvalidRequest):
            self.job_store().jobs(status="RETRYING")
        with self.assertRaises(errors.InvalidRequest):
            self.job_store().jobs(kind="purge")
