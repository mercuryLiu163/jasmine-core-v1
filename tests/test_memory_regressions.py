"""Regression tests for the defects an independent review found.

Each class here is a bug that was reproduced, fixed and is now pinned. They are
kept in one file rather than folded into the feature suites so that the reason
each test exists stays legible: a future edit that reintroduces one of these
should fail here first, and the failure should name the failure it is
preventing.
"""

from __future__ import annotations

import sqlite3
import unittest

from memory_support import HOST, HUMAN, MemoryTestCase
from jasmine_core import clock, db, errors, ids
from jasmine_core.memory import eligibility, filtering, jobs as jobs_module
from jasmine_core.memory import scope as scope_module
from jasmine_core.memory.store import MemoryStore
from jasmine_core.memory.transport import TransportError
from jasmine_core.memory.types import RecallRequest


class RecoveryScanCursor(MemoryTestCase):
    """B1: the cursor must advance past a window with no eligible source.

    The original cursor advanced only to the highest candidate, so a window of
    refused Events never moved it and every eligible Event behind that window was
    stranded — silently and permanently, which made the whole commit-to-enqueue
    recovery claim false.
    """

    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)

    def _many_refusals(self, count: int) -> None:
        from jasmine_core.state import StateStore

        state = StateStore(self.conn, schema_version=9)
        for index in range(count):
            step = state.create_step(self.task_id,
                                     {"title": f"s{index}", "host_id": HOST,
                                      "expected_revision": self.task_revision()},
                                     actor_id=HUMAN, actor_kind="human",
                                     can_accept=False)["step"]["step_id"]
            revision = self.step_revision(step)
            # PLANNED -> IN_PROGRESS -> EXECUTED: a source Event type that
            # eligibility always refuses, so it fills a window with nothing.
            state.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": revision,
                                        "host_id": HOST},
                                 actor_id=HUMAN, actor_kind="human", can_accept=False)
            state.transition_step(step, {"status": "EXECUTED", "expected_revision": revision + 1,
                                        "host_id": HOST},
                                 actor_id=HUMAN, actor_kind="human", can_accept=False)

    def task_revision(self) -> int:
        return int(self.conn.execute("SELECT revision FROM tasks WHERE task_id=?",
                                     (self.task_id,)).fetchone()["revision"])

    def step_revision(self, step_id: str) -> int:
        return int(self.conn.execute("SELECT revision FROM steps WHERE step_id=?",
                                     (step_id,)).fetchone()["revision"])

    def test_an_eligible_event_behind_a_full_window_of_refusals_is_still_found(self) -> None:
        self._many_refusals(60)
        self.active_decision(project_id=self.project_id, rule_key="behind.a.window")
        # A small window, so the refusals cannot all be read at once.
        worker = self.worker(scan_limit=8)
        created: list[str] = []
        for _ in range(20):
            created.extend(worker.tick().as_dict()["created"])
            if created:
                break
        self.assertTrue(created,
                        f"the decision behind the refused window was never enqueued; "
                        f"cursor={self.job_store().cursor()}")

    def test_the_cursor_advances_past_a_window_with_no_candidate_at_all(self) -> None:
        self._many_refusals(6)
        before = self.job_store().cursor()
        self.assertEqual(before, 0)
        report = self.job_store().enqueue_from_sources(eligibility.SourceRegistry(), limit=50)
        self.assertEqual(report["candidates"], 0)
        self.assertGreater(report["cursor_after"], 0, "a refused-only window did not move it")
        self.assertGreater(report["events_examined"], 0)
        # And a second pass does not re-read the same window.
        again = self.job_store().enqueue_from_sources(eligibility.SourceRegistry(), limit=50)
        self.assertEqual(again["events_examined"], 0)
        self.assertEqual(again["cursor_after"], report["cursor_after"])

    def test_the_scan_does_not_hold_the_write_lock_while_hashing_artifacts(self) -> None:
        # M8: resolving an Evidence artifact reference reads and hashes a
        # workspace file. Doing that inside BEGIN IMMEDIATE held the SQLite write
        # lock for the whole I/O, which made concurrent Truth writes fail with
        # database_busy. A proxy records the transaction state at each read,
        # because `Connection.execute` is read-only and cannot be wrapped.
        
        self.evidence(task_id=self.task_id, status="PASS",
                      artifact_uri="workspace:/report.txt")

        #: The cursor itself is re-read inside the transaction, which is correct
        #: and is the only read that legitimately happens there. The scan's
        #: Evidence resolution is the expensive part and must not be.
        SLOW_TABLES = ("FROM events", "FROM evidence", "FROM rule_versions")

        class Watched:
            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner
                self.reads_in_transaction: list[str] = []

            def execute(self, sql, *args, **kwargs):
                text = " ".join(sql.split())
                if (sql.lstrip().upper().startswith("SELECT") and self._inner.in_transaction
                        and any(table in text for table in SLOW_TABLES)):
                    self.reads_in_transaction.append(text[:90])
                return self._inner.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        watched = Watched(self.conn)
        store = MemoryStore(self.conn, schema_version=9)
        jobs_module.JobStore(watched, store).enqueue_from_sources(
            eligibility.SourceRegistry(), limit=50)
        self.assertEqual(watched.reads_in_transaction, [],
                         "the scan read rows while holding the write lock")


class StateTransitions(MemoryTestCase):
    """B2/B3: no worker may overwrite a terminal result, and no crash may wedge."""

    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.store.items()[0]
        self.job = self.job_store().jobs()[0]

    def expire_lease(self, job_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'"
                " WHERE job_id=?", (job_id,))

    def make_due(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute("UPDATE memory_jobs SET next_attempt_at='1970-01-01T00:00:00Z'")

    def test_a_late_failure_cannot_overwrite_a_successful_retain(self) -> None:
        # A's lease expires mid-retain; B recovers it and succeeds; A now reports
        # its own failure. The successful result is the truth.
        claimed = self.job_store().claim_due(owner="worker-a")
        self.assertIsNotNone(claimed)
        self.expire_lease(claimed.job["job_id"])
        recovered = self.worker(lease_owner="worker-b").tick(scan=False)
        self.assertEqual(recovered.outcome, "retained")
        self.assertEqual(self.store.item(self.item["memory_id"])["engine_state"], "RETAINED")
        # A's stale failure must be refused, not applied.
        with self.assertRaises(errors.InvalidStateTransition):
            self.job_store().fail(claimed, error_code="engine_timeout",
                                  safe_error_summary="A was slow")
        self.assertEqual(self.store.item(self.item["memory_id"])["engine_state"], "RETAINED")
        self.assertEqual(self.job_store().jobs()[0]["status"], "DONE")

    def test_a_stale_completion_cannot_overwrite_another_workers_receipt(self) -> None:
        claimed = self.job_store().claim_due(owner="worker-a")
        self.assertIsNotNone(claimed)
        with db.transaction(self.conn):
            self.conn.execute("UPDATE memory_jobs SET status='DONE',"
                              " completed_at='2026-09-29T12:00:00.000000Z' WHERE job_id=?",
                              (claimed.job["job_id"],))
        with self.assertRaises(errors.InvalidStateTransition):
            self.job_store().complete(claimed, receipt={"items_count": 1})

    def test_a_retain_failure_cannot_regress_a_deleted_tombstone(self) -> None:
        self.worker().tick()
        revision = self.store.item(self.item["memory_id"])["revision"]
        self.lifecycle().delete(self.item["memory_id"],
                                {"expected_revision": revision, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.worker(lease_owner="w2").tick()
        self.assertEqual(self.store.item(self.item["memory_id"])["engine_state"], "DELETED")
        self.assertIsNotNone(self.store.item(self.item["memory_id"])["remote_deleted_at"])

    def test_deleting_before_the_retain_ran_still_removes_the_remote_document(self) -> None:
        # B3: the retain job fails `document_deleted_locally`, which moved the
        # item to RETAIN_FAILED; `mark_document_deleted` did not accept that
        # predecessor and raised, so the worker crash-looped and the remote
        # document was never removed.
        revision = self.store.item(self.item["memory_id"])["revision"]
        self.lifecycle().delete(self.item["memory_id"],
                                {"expected_revision": revision, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        reports = self.worker().run(max_ticks=6)
        outcomes = [report.outcome for report in reports]
        self.assertNotIn("error", outcomes, f"the worker hit an unhandled error: {outcomes}")
        self.assertIn("deleted", outcomes, f"the remote document was never removed: {outcomes}")
        self.assertEqual(self.transport.deleted,
                         [(self.item["bank_id"], self.item["document_id"])])
        self.assertEqual(self.store.item(self.item["memory_id"])["engine_state"], "DELETED")

    def test_a_retain_for_a_tombstoned_item_fails_without_regressing_the_state(self) -> None:
        # The job was queued while the item was ACTIVE and the operator deleted
        # it before the worker reached it. Writing a retain failure over
        # `DELETE_PENDING` would regress the item, and refusing the write raised
        # an unhandled error on every tick.
        revision = self.store.item(self.item["memory_id"])["revision"]
        self.lifecycle().delete(self.item["memory_id"],
                                {"expected_revision": revision, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        report = self.worker().tick(scan=False)
        self.assertNotEqual(report.outcome, "error")
        self.assertIn(report.outcome, ("failed", "retry"))
        stored = self.store.item(self.item["memory_id"])
        self.assertEqual(stored["status"], "DELETED")
        self.assertIn(stored["engine_state"], ("DELETE_PENDING", "DELETED", "DELETE_FAILED"))

    def test_one_bad_job_cannot_take_the_service_down(self) -> None:
        # B3/M7: an unexpected exception during a tick used to escape `run()`,
        # so a single bad job exited the service with a traceback and, with the
        # lease expiry, crash-looped forever.
        worker = self.worker()
        original = self.transport.retain

        def explode(**_kwargs):
            raise ValueError("a defect in the engine client")

        self.transport.retain = explode  # type: ignore[method-assign]
        try:
            report = worker.tick(scan=False)
        finally:
            self.transport.retain = original  # type: ignore[method-assign]
        self.assertEqual(report.outcome, "error")
        self.assertIn("unhandled_ValueError", report.error_code)

    def test_a_manual_retry_of_a_queued_job_is_refused_not_audited_as_a_change(self) -> None:
        with self.assertRaises(errors.InvalidStateTransition) as caught:
            self.job_store().request_retry(self.job["job_id"], actor_id=HUMAN,
                                           actor_kind="human", host_id=HOST)
        self.assertIn("already queued", caught.exception.message)
        self.assertIsNone(self.conn.execute(
            "SELECT event_id FROM events WHERE event_type='memory.job.retried'").fetchone())

    def test_a_manual_retry_resets_the_attempt_counter(self) -> None:
        self.transport.retain_error = TransportError("engine_rejected", "no", retryable=False)
        self.worker(max_attempts=1).tick()
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["retry_count"], 1)
        self.job_store().request_retry(job["job_id"], actor_id=HUMAN, actor_kind="human",
                                       host_id=HOST)
        self.assertEqual(self.job_store().jobs()[0]["retry_count"], 0,
                         "an exhausted counter would make the first post-retry failure terminal")

    def test_a_busy_database_does_not_stop_the_worker(self) -> None:
        from unittest.mock import patch

        worker = self.worker()
        with patch.object(worker._jobs, "enqueue_from_sources",
                          side_effect=db.errors.DatabaseBusy("busy")):
            report = worker.tick()
        self.assertEqual(report.outcome, "busy")
        self.assertEqual(report.error_code, "database_busy")


class NoConfiguredEngine(MemoryTestCase):
    """B4: no engine must be a TransportError on every path, not an AttributeError."""

    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.store.items()[0]

    def test_the_worker_survives_a_delete_job_with_no_engine(self) -> None:
        worker = self.worker(transport=None)
        revision = self.store.item(self.item["memory_id"])["revision"]
        self.lifecycle().delete(self.item["memory_id"],
                                {"expected_revision": revision, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        reports = worker.run(max_ticks=5)
        outcomes = [report.outcome for report in reports]
        self.assertNotIn("error", outcomes, f"the worker crashed: {outcomes}")
        self.assertIn("engine_not_configured",
                      {report.error_code for report in reports})
        # The item stays excluded locally; the remote copy is honestly unknown.
        stored = self.store.item(self.item["memory_id"])
        self.assertEqual(stored["status"], "DELETED")
        self.assertIsNone(stored["remote_deleted_at"])

    def test_the_adapter_refuses_to_call_the_engine_inside_a_transaction(self) -> None:
        # M7: the invariant belongs on the adapter, not only on the worker's
        # private phases, or an in-process caller bypasses it.
        from jasmine_core.memory.adapter import MemoryAdapter
        from jasmine_core.memory.types import Provenance, RetainRequest

        adapter = MemoryAdapter(self.conn, self.store, self.transport, schema_version=9)
        request = RetainRequest(
            bank=scope_module.project_bank(self.project_id), document_id="d",
            memory_type="FAILURE_LESSON", content="x",
            provenance=Provenance(source_event_ids=("evt_" + "0" * 26,)),
            context="c", metadata={filtering.canonical.sha256_hex("x"): "0" * 64,
                                   "x": "y"},
        )
        with db.transaction(self.conn):
            with self.assertRaises(RuntimeError) as caught:
                adapter.retain(request)
        self.assertIn("write lock", str(caught.exception))
        self.assertEqual(self.transport.retain_calls, [])


class SecretFilterCompleteness(unittest.TestCase):
    """B5: the unquoted assignment form is what real captured text looks like."""

    def test_a_bare_environment_style_secret_is_redacted(self) -> None:
        for text in ("the deploy failed because DB_PASSWORD=s3cr3t-prod-value was wrong",
                     "run it with GITHUB_TOKEN=ghp_x api.example and it works",
                     "curl -d client_secret=abc123def456 https://api.example"):
            result = filtering.sanitize(text)
            self.assertEqual(result.action, "redact", text)
            self.assertNotIn("s3cr3t", result.text)
            self.assertNotIn("abc123def456", result.text)
            self.assertIn("REDACTED", result.text)

    def test_the_name_match_is_case_insensitive(self) -> None:
        # `canonical.redact_text` is not case-insensitive, so `DB_PASSWORD=`
        # passed both. The engine is a different reader from a log file and a
        # different reader from a recall; the stricter behaviour is used here.
        result = filtering.sanitize("ACCESS_TOKEN=notarealvalue")
        self.assertEqual(result.action, "redact")
        self.assertNotIn("notarealvalue", result.text)

    def test_a_placeholder_is_still_allowed(self) -> None:
        for text in ('export token="$TOKEN"', "set api_key=${API_KEY}",
                     "password=<redacted-by-hand> was in the ticket"):
            self.assertEqual(filtering.sanitize(text).action, "allow", text)


class AclIsEnforcedOnBothPaths(MemoryTestCase):
    """M1/M2: a mode enforced on GET and absent on POST is worse than no mode."""

    def setUp(self) -> None:
        super().setUp()
        from jasmine_core.memory.lifecycle import MemoryLifecycle

        self.a = self.project("a")
        self.b = self.project("b")
        self.active_decision(project_id=self.a, rule_key="engine.a")
        self.active_decision(project_id=self.b, rule_key="engine.b")
        self.scan()
        self.grant_project(self.a, actor_id=self.agent_actor)
        self.store_a = MemoryStore(self.conn, schema_version=9)
        self.acl = MemoryLifecycle(self.conn, self.store_a, mode=scope_module.ACL)

    def test_an_ungranted_project_is_refused_on_the_write_path(self) -> None:
        foreign = next(i for i in self.store.items() if i["project_id"] == self.b)
        # The grant is for project A only, so project B's item is invisible to
        # this actor on both paths.
        self.acl.detail(next(i for i in self.store.items()
                             if i["project_id"] == self.a)["memory_id"],
                        actor_id=self.agent_actor)
        with self.assertRaises(errors.NotFound):
            self.acl.obsolete(foreign["memory_id"],
                              {"expected_revision": 1, "host_id": HOST},
                              actor_id=self.agent_actor, actor_kind="human", host_id=HOST)
        self.assertEqual(self.store.item(foreign["memory_id"])["status"], "ACTIVE")

    def test_an_unfiltered_list_is_scoped_to_the_granted_projects(self) -> None:
        from jasmine_core.memory import jobs as jobs_module

        # `single_user`: an omitted filter means every project, and the query is
        # unchanged.
        single = scope_module.ProjectAccess(self.conn, mode=scope_module.SINGLE_USER)
        self.assertEqual(single.mode, scope_module.SINGLE_USER)
        # `acl`: an omitted filter resolves to the actor's grants, so "no filter"
        # is not a way to read the whole database.
        visible = self._visible(self.agent_actor)
        self.assertEqual(visible, (self.a,))
        jobs = jobs_module.JobStore(self.conn, self.store_a).jobs(projects=visible)
        self.assertEqual({job["project_id"] for job in jobs if job["project_id"]}, {self.a},
                         "an ungranted project's job was listed")
        items = self.store_a.items(projects=visible)
        self.assertEqual({item["project_id"] for item in items if item["project_id"]}, {self.a},
                         "an ungranted project's item was listed")
        self.assertEqual(self.store_a.traces(projects=visible), [])
        # Without the scope an acl actor would see both projects' rows.
        self.assertEqual(len(self.store_a.items(projects=(self.a, self.b))), len(items) + 1)
        # And an ungranted project named explicitly is a 404, not a filter.
        access = scope_module.ProjectAccess(self.conn, mode=scope_module.ACL)
        with self.assertRaises(errors.NotFound):
            access.require(project_id=self.b, actor_id=self.agent_actor)

    def _visible(self, actor_id: str) -> tuple[str, ...]:
        return tuple(row["project_id"] for row in self.conn.execute(
            "SELECT project_id FROM memory_project_actors WHERE actor_id=?", (actor_id,)))

    def test_the_bank_filter_is_checked_against_the_visible_scope(self) -> None:
        # `require_bank` exists for this and is not decorative.
        allowed = (scope_module.project_bank(self.a), scope_module.global_bank())
        with self.assertRaises(errors.NotFound):
            scope_module.require_bank(scope_module.project_bank(self.b).bank_id, allowed)
        self.assertEqual(
            scope_module.require_bank(scope_module.project_bank(self.a).bank_id,
                                      allowed).bank_id,
            scope_module.project_bank(self.a).bank_id)
        self.assertIsNotNone(scope_module.scope_of("jasmine-global"))
        for bad in ("jasmine-global/x", "mem0", "jasmine-project-bad", "", None, 7):
            self.assertIsNone(scope_module.scope_of(bad), bad)
            self.assertFalse(scope_module.is_valid_bank_id(bad), bad)


class TraceDoesNotLeakIdentifiers(MemoryTestCase):
    """M3: a refused result's identifier is information too."""

    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.active_decision(project_id=self.project_id)
        self.scan()
        self.item = self.store.items()[0]

    def test_a_foreign_project_result_names_nothing_in_the_trace(self) -> None:
        from jasmine_core.memory.types import EngineResult

        other = self.project("other")
        # The row is *returned* under project A's bank — which is how a shared
        # engine leaks another tenant's content — but it claims project B's bank.
        real = self.transport.answer_for(self.item["bank_id"], self.item["memory_id"],
                                         "project B's private lesson")
        self.transport.answer[self.item["bank_id"]] = [
            EngineResult(engine_result_id=real.engine_result_id, text=real.text,
                         bank_id=f"jasmine-project-{other}", document_id=real.document_id,
                         metadata=real.metadata)
        ]
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="private", max_tokens=400))
        self.assertEqual([f.reason for f in outcome.filtered], ["foreign_project"])
        trace = self.store.trace(outcome.trace_id)
        self.assertNotIn(self.item["memory_id"], str(trace),
                         "a refused result leaked the item's identifier into the trace")
        self.assertEqual(outcome.items, ())

    def test_a_recalled_item_reports_the_origin_event_it_was_written_with(self) -> None:
        from jasmine_core.memory import provenance

        # Retain first, so the engine metadata is the real one a worker writes.
        self.worker().tick()
        self.assertTrue(self.transport.retain_calls)
        self.assertIn(provenance.ORIGIN_EVENT_ID_KEY, self.transport.retain_calls[0]["metadata"])
        self.transport.answer_for(self.item["bank_id"], self.item["memory_id"],
                                  "V1 uses Hindsight", document_id=self.item["document_id"])
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="Hindsight", max_tokens=400))
        self.assertEqual(len(outcome.items), 1, outcome.as_dict())
        # The persisted origin, not `source_event_id`. This fixture is a
        # Decision, and for a Decision those differ: `source_event_id` is the
        # `rule.approved` Event that created the row, while `origin_event_id` is
        # the Event the Rule version's content came from. Asserting the old
        # equality would pin the bug where the approval was reported as the
        # origin and the real one was never recorded.
        self.assertEqual(self.item["origin_event_id"] != self.item["source_event_id"], True,
                         "this fixture is meant to have an origin that differs from its source")
        self.assertEqual(outcome.items[0].provenance.origin_event_id,
                         self.item["origin_event_id"],
                         "the persisted origin event was not carried to the Recall result")


class EngineSuccessIsNotRecordedOnFailure(MemoryTestCase):
    """M4: a probe is not a success, and a failed recall is not a success."""

    def setUp(self) -> None:
        super().setUp()
        self.project_id = self.project()

    def test_a_fully_degraded_recall_does_not_advance_last_success(self) -> None:
        from jasmine_core.memory.worker import record_engine_probe

        record_engine_probe(self.conn, self.store, self.transport)
        with db.transaction(self.conn):
            self.conn.execute("UPDATE memory_engine_status SET last_success_at=NULL")
        for bank in (self.project_id, None):
            self.transport.recall_error[scope_module.project_bank(
                self.project_id).bank_id if bank else "jasmine-global"] = TransportError(
                "engine_unreachable", "down", retryable=True)
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="anything", max_tokens=400))
        self.assertEqual(outcome.status, "degraded")
        status = self.store.engine_status()
        self.assertIsNone(status["last_success_at"],
                          "a recall where every bank failed was recorded as a success")
        self.assertEqual(status["last_error_code"], "engine_unreachable")

    def test_a_partial_degraded_recall_records_both_sides(self) -> None:
        from jasmine_core.memory.worker import record_engine_probe

        record_engine_probe(self.conn, self.store, self.transport)
        self.transport.recall_error["jasmine-global"] = TransportError(
            "engine_unreachable", "down", retryable=True)
        outcome = self.adapter().recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="anything", max_tokens=400))
        self.assertEqual(outcome.status, "degraded")
        status = self.store.engine_status()
        # One bank did answer, so a success is real; and one bank did not, so the
        # failure is recorded too rather than being cleared by that success.
        self.assertIsNotNone(status["last_success_at"])
        self.assertEqual(status["last_error_code"], "engine_unreachable")


class ScanSurvivesOneBadRow(MemoryTestCase):
    """M6: one unreadable Evidence row must not discard the window."""

    def test_an_unverifiable_reference_does_not_void_the_whole_scan(self) -> None:
        from unittest.mock import patch

        project = self.project()
        task = self.task(project)
        self.evidence(task_id=task, status="PASS", artifact_uri="workspace:/report.txt")
        # A Core with no configured workspace root: the reference cannot be
        # checked, which is a per-row refusal and not a scan-wide failure.
        import os

        previous = os.environ.pop("JASMINE_CORE_WORKSPACE_ROOT", None)
        self.addCleanup(lambda: os.environ.__setitem__("JASMINE_CORE_WORKSPACE_ROOT", previous)
                        if previous else None)
        with patch("jasmine_core.evidence.EvidenceStore._reference_status",
                   side_effect=errors.MissingEvidence("no root")):
            report = self.job_store().enqueue_from_sources(eligibility.SourceRegistry())
        self.assertEqual(report["scanned"] if "scanned" in report else report["candidates"], 0)
        reasons = {r["reason"] for r in report["ineligible"]}
        self.assertIn("evidence_reference_unverifiable", reasons)
        self.assertNotIn("source_failed", reasons,
                         "one bad row took the whole source's output with it")
        # A Decision in the same window is still enqueued.
        self.active_decision(project_id=project, rule_key="still.works")
        self.assertTrue(self.job_store().enqueue_from_sources(
            eligibility.SourceRegistry())["created"])


class P2IsNotAnEligibleSource(MemoryTestCase):
    """P2 landed while this branch was in flight; its boundary is now pinned.

    P2-01 is an *extraction* chain: an `EXTRACTED` interpretation is a
    structured reading of a prompt, not a confirmed Decision. Treating one as
    eligible is precisely the "an unreviewed candidate is eligible" failure the
    P4 taskbook forbids, so this test exists to make the refusal explicit.
    """

    def test_an_extracted_interpretation_is_not_a_memory_source(self) -> None:
        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interpretations'"
        ).fetchone() is None:
            self.skipTest("P2 interpretations are not present on this branch")
        project = self.project()
        origin = self.event("user.prompt", project_id=project, text="use hindsight for memory")
        interpretation_id = ids.new_id("int")
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO interpretations (interpretation_id, event_id, project_id,"
                " task_id, session_id, host_id, source_actor_id, source_actor_kind,"
                " source_type, processor_actor_id, extractor_id, extractor_model,"
                " extractor_version, provider, prompt_version, output_schema_version,"
                " schema_digest, config_digest, input_hash, config_json, created_at,"
                " deadline_at) VALUES (?,?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (interpretation_id, origin, project, HOST, HUMAN, "human", "USER_EXPLICIT",
                 self.system_actor, "codex-local-v1", "m", "v", "p", "pv", "s", "0" * 64,
                 "0" * 64, "0" * 64, "{}", clock.now_rfc3339(), clock.now_rfc3339()))
            self.conn.execute(
                "INSERT INTO interpretation_results (interpretation_id, status,"
                " raw_result_json, candidates_json, confidence, error_code, completed_at)"
                " VALUES (?, 'EXTRACTED', ?, ?, 0.9, NULL, ?)",
                (interpretation_id, "{}", '[{"kind":"DECISION","content":"use hindsight"}]',
                 clock.now_rfc3339()))
        # No job is created from it, and no Memory item exists.
        report = self.job_store().enqueue_from_sources(eligibility.SourceRegistry())
        self.assertEqual(report["candidates"], 0)
        self.assertEqual(self.store.count_items(), 0)


class HealthyCoreUnderMemoryFailure(MemoryTestCase):
    """The degradation contract, once more, at the process level."""

    def test_a_core_with_memory_fully_failing_still_serves_truth(self) -> None:
        from jasmine_core.api.handlers import Core

        project = self.project()
        task = self.task(project)
        self.active_decision(project_id=project)
        self.scan()
        self.transport.retain_error = TransportError("engine_unreachable", "down", retryable=True)
        self.worker().run(max_ticks=1)
        core = Core(self.conn)
        from jasmine_core import SCHEMA_VERSION
        self.assertEqual(core.schema_version, SCHEMA_VERSION)
        self.assertEqual(len(core.authority.list_active(project_id=project)), 1)
        self.assertEqual(self.conn.execute("SELECT status FROM tasks WHERE task_id=?",
                                           (task,)).fetchone()["status"], "ACTIVE")
        # The Core's own health endpoint is a different function and still works.
        from jasmine_core.api.request import Request
        from jasmine_core.api.dispatch import dispatch

        response = dispatch(core, Request(method="GET", path="/v1/health"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["status"], "ok")
        self.assertNotIn("memory", str(response.body).lower())


class GateRunnerG02WouldNotHaveFailed(MemoryTestCase):
    """The G02 case must supersede the old rule, not add a second one."""

    def test_supersede_is_what_marks_the_old_decision_historical(self) -> None:
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        old_rule, _ = self.active_decision(project_id=project,
                                           content="V1 uses mem0 for long-term memory",
                                           rule_key="engine")
        authority = AuthorityStore(self.conn, schema_version=9)
        authority.transition(old_rule, "supersede", {
            "expected_revision": 2, "host_id": HOST, "kind": "DECISION",
            "severity": "HARD", "enforcement": "CONTEXT",
            "content": "V1 uses Hindsight for long-term memory", "matcher": {},
            "scope": {"kind": "project", "project_id": project},
            "origin_event_id": self.event("user.prompt", project_id=project,
                                          text="switch to hindsight"),
        }, actor_id=HUMAN)
        # Scan the superseded version as its own source, exactly as the Gate does:
        # there are now two items, one per Rule version, and the assertion is
        # about the *old* one.
        self.scan()
        self.worker().run(max_ticks=6)
        item = next(i for i in self.store.items() if i["origin_rule_version"] == 1)
        self.transport.answer_for(item["bank_id"], item["memory_id"],
                                  "V1 uses mem0 for long-term memory",
                                  document_id=item["document_id"])
        outcome = self.adapter().recall(RecallRequest(
            project_id=project, actor_id=HUMAN, query="memory engine", max_tokens=400))
        self.assertIn("superseded_decision", {f.reason for f in outcome.filtered},
                      "supersede did not mark the old Decision historical")

    def test_each_decision_is_recorded_at_the_version_that_was_approved(self) -> None:
        """The version must come from history, not from the current Rule row.

        Reading `rules.current_version` while scanning made *both* memories claim
        the newest version, so neither was ever filtered as `superseded_decision`
        and the old Decision was rendered as the current answer — the exact VC-14
        failure the mechanism exists to prevent.
        """
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        old_rule, _ = self.active_decision(project_id=project,
                                           content="V1 uses mem0 for long-term memory",
                                           rule_key="engine")
        AuthorityStore(self.conn, schema_version=9).transition(old_rule, "supersede", {
            "expected_revision": 2, "host_id": HOST, "kind": "DECISION",
            "severity": "HARD", "enforcement": "CONTEXT",
            "content": "V1 uses Hindsight for long-term memory", "matcher": {},
            "scope": {"kind": "project", "project_id": project},
            "origin_event_id": self.event("user.prompt", project_id=project,
                                          text="switch to hindsight"),
        }, actor_id=HUMAN)

        self.scan()
        versions = sorted(i["origin_rule_version"] for i in self.store.items())
        self.assertEqual(versions, [1, 2],
                         "each Rule version must produce its own memory, at its own version")

    def test_only_the_current_decision_is_served_as_current(self) -> None:
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        old_rule, _ = self.active_decision(project_id=project,
                                           content="V1 uses mem0 for long-term memory",
                                           rule_key="engine")
        AuthorityStore(self.conn, schema_version=9).transition(old_rule, "supersede", {
            "expected_revision": 2, "host_id": HOST, "kind": "DECISION",
            "severity": "HARD", "enforcement": "CONTEXT",
            "content": "V1 uses Hindsight for long-term memory", "matcher": {},
            "scope": {"kind": "project", "project_id": project},
            "origin_event_id": self.event("user.prompt", project_id=project,
                                          text="switch to hindsight"),
        }, actor_id=HUMAN)
        self.scan()
        self.worker().run(max_ticks=6)
        for item in self.store.items():
            self.transport.answer_for(item["bank_id"], item["memory_id"],
                                      item["sanitized_text"],
                                      document_id=item["document_id"])

        outcome = self.adapter().recall(RecallRequest(
            project_id=project, actor_id=HUMAN, query="memory engine", max_tokens=800))
        self.assertEqual([f.reason for f in outcome.filtered], ["superseded_decision"],
                         "exactly the old Decision is historical")
        self.assertEqual(len(outcome.items), 1, outcome.as_dict())
        self.assertIn("Hindsight", outcome.items[0].text)
        self.assertNotIn("mem0", outcome.items[0].text)

    def test_a_retired_decision_is_kept_but_never_rendered_as_current(self) -> None:
        """A Decision that was approved and later retired is still history.

        Widening the approved source to accept a retired Rule is only safe
        because the recall filter refuses it by status, not only by version. A
        retired Rule has not bumped `current_version`, so the version comparison
        alone would have served it as the current answer.
        """
        from jasmine_core.authority import AuthorityStore

        project = self.project()
        rule, _ = self.active_decision(project_id=project,
                                       content="V1 uses mem0 for long-term memory",
                                       rule_key="engine")
        AuthorityStore(self.conn, schema_version=9).transition(rule, "retire", {
            "expected_revision": 2, "host_id": HOST,
        }, actor_id=HUMAN)

        self.scan()
        self.worker().run(max_ticks=6)
        retired = self.store.items()
        self.assertEqual(len(retired), 1, "the approved Decision is history, not garbage")
        self.assertEqual(retired[0]["origin_rule_version"], 1)
        item = retired[0]
        self.transport.answer_for(item["bank_id"], item["memory_id"], item["sanitized_text"],
                                  document_id=item["document_id"])

        outcome = self.adapter().recall(RecallRequest(
            project_id=project, actor_id=HUMAN, query="memory engine", max_tokens=400))
        self.assertEqual([f.reason for f in outcome.filtered], ["retired_decision"])
        self.assertEqual(outcome.items, ())


class TwoRealConnectionsStillWork(MemoryTestCase):
    """M7/M8: a second connection sees the scan's writes and is not blocked."""

    def test_a_concurrent_truth_write_succeeds_while_the_scan_runs(self) -> None:
        other = db.connect(self.db_path)
        self.addCleanup(other.close)
        from jasmine_core.events import EventStore
        from jasmine_core.models import NewEvent

        with db.transaction(other):
            EventStore(other, schema_version=9).append(NewEvent(
                event_type="user.prompt", source_system="other", source_event_id=None,
                occurred_at=clock.now(), actor_id=HUMAN, actor_kind="human", host_id=HOST,
                payload={"text": "concurrent write"}))
        report = self.job_store().enqueue_from_sources(eligibility.SourceRegistry(), limit=50)
        self.assertIn("events_examined", report)
        self.assertEqual(report["created"], [], "a user.prompt is not a memory source")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM events WHERE event_id LIKE 'evt_%'"
                              ).fetchone()["n"] > 0, True)


class ProvenanceCannotBeReshapedByTheEngine(MemoryTestCase):
    """The engine echoes what Core wrote; it does not get to rewrite it."""

    def test_another_projects_evidence_id_never_reaches_this_trace(self) -> None:
        from jasmine_core.memory import provenance
        from jasmine_core.memory.types import EngineResult

        mine = self.project()
        other = self.project()
        self.active_decision(project_id=mine)
        self.scan()
        self.worker().run(max_ticks=6)
        item = self.store.items()[0]
        foreign_evidence, _ = self.evidence(task_id=self.task(other), status="FAIL",
                                            detail="another project's failure")
        real = self.transport.answer_for(item["bank_id"], item["memory_id"], "lesson",
                                         document_id=item["document_id"])
        self.transport.answer[item["bank_id"]] = [EngineResult(
            engine_result_id=real.engine_result_id, text=real.text, bank_id=real.bank_id,
            document_id=real.document_id,
            metadata={**real.metadata, provenance.EVIDENCE_IDS_KEY: foreign_evidence})]

        outcome = self.adapter().recall(RecallRequest(
            project_id=mine, actor_id=HUMAN, query="lesson", max_tokens=400))
        self.assertEqual([f.reason for f in outcome.filtered], ["foreign_evidence"])
        self.assertEqual(outcome.items, ())
        self.assertNotIn(foreign_evidence, str(outcome.as_dict()),
                         "a foreign Evidence id was written into a readable trace")

    def test_an_unrelated_event_of_the_same_project_cannot_replace_a_source(self) -> None:
        from jasmine_core.memory import provenance
        from jasmine_core.memory.types import EngineResult

        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        self.worker().run(max_ticks=6)
        item = self.store.items()[0]
        unrelated = self.event("user.prompt", project_id=project,
                               text="an unrelated remark")
        real = self.transport.answer_for(item["bank_id"], item["memory_id"], "lesson",
                                         document_id=item["document_id"])
        self.transport.answer[item["bank_id"]] = [EngineResult(
            engine_result_id=real.engine_result_id, text=real.text, bank_id=real.bank_id,
            document_id=real.document_id,
            metadata={**real.metadata, provenance.SOURCE_EVENT_IDS_KEY: unrelated})]

        outcome = self.adapter().recall(RecallRequest(
            project_id=project, actor_id=HUMAN, query="lesson", max_tokens=400))
        self.assertEqual([f.reason for f in outcome.filtered], ["foreign_source"],
                         "an id the item never claimed was believed")
        self.assertNotIn(unrelated, str(outcome.as_dict()))

    def test_the_recalled_provenance_comes_from_the_item_not_the_echo(self) -> None:
        from jasmine_core.memory import provenance
        from jasmine_core.memory.types import EngineResult

        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        self.worker().run(max_ticks=6)
        item = self.store.items()[0]
        real = self.transport.answer_for(item["bank_id"], item["memory_id"], "lesson",
                                         document_id=item["document_id"])
        # An engine that *drops* a source must not shorten what the trace reports.
        self.transport.answer[item["bank_id"]] = [EngineResult(
            engine_result_id=real.engine_result_id, text=real.text, bank_id=real.bank_id,
            document_id=real.document_id, metadata={
                provenance.MEMORY_ID_KEY: item["memory_id"],
                provenance.ORIGIN_EVENT_ID_KEY: item["origin_event_id"]})]

        outcome = self.adapter().recall(RecallRequest(
            project_id=project, actor_id=HUMAN, query="lesson", max_tokens=400))
        self.assertEqual(len(outcome.items), 1, outcome.as_dict())
        self.assertEqual(outcome.items[0].provenance.source_event_ids,
                         (item["source_event_id"],),
                         "the engine's silence changed the recorded provenance")


class ARetainThatOutlivesItsItemTakesTheWriteBack(MemoryTestCase):
    """The window fencing cannot close: the write is already on the wire."""

    def _retain_while_revoked(self, action: str):
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        item = self.store.items()[0]
        real_retain = self.transport.retain

        def in_flight(**kwargs):
            receipt = real_retain(**kwargs)
            # The operator revokes the item while that call is executing.
            if action == "delete":
                self.lifecycle().delete(item["memory_id"],
                                        {"expected_revision": 1, "host_id": HOST},
                                        actor_id=HUMAN, actor_kind="human", host_id=HOST)
            else:
                self.lifecycle().obsolete(item["memory_id"],
                                          {"expected_revision": 1, "host_id": HOST},
                                          actor_id=HUMAN, actor_kind="human", host_id=HOST)
            return receipt

        self.transport.retain = in_flight
        try:
            report = self.worker().tick(scan=False)
        finally:
            self.transport.retain = real_retain
        return item, report

    def test_a_deleted_item_leaves_no_remote_document_behind(self) -> None:
        item, report = self._retain_while_revoked("delete")
        self.assertEqual(report.error_code, "document_deleted_locally", report.as_dict())
        self.assertNotEqual(report.outcome, "retained")
        self.assertIsNone(self.transport.documents.get((item["bank_id"],
                                                        item["document_id"])),
                          "the retain was undone only in the database")
        job = next(j for j in self.job_store().jobs()
                   if j["memory_id"] == item["memory_id"] and j["kind"] == "retain")
        self.assertNotEqual(job["status"], "DONE",
                            "a take-back was recorded as a successful retain")
        self.assertEqual(self.store.item(item["memory_id"])["status"], "DELETED")

    def test_an_obsoleted_item_keeps_its_document_but_its_units_are_retired(self) -> None:
        item, report = self._retain_while_revoked("obsolete")
        self.assertEqual(report.error_code, "document_deleted_locally", report.as_dict())
        self.assertIsNotNone(self.transport.documents.get((item["bank_id"],
                                                           item["document_id"])),
                             "obsolete keeps the document; only its units retire")
        self.assertTrue(self.transport.invalidated,
                        "the units this retain created were never soft-retired")
        self.assertEqual(self.store.item(item["memory_id"])["status"], "OBSOLETE")

    def test_an_active_item_is_left_alone(self) -> None:
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        item = self.store.items()[0]
        report = self.worker().tick(scan=False)
        self.assertEqual(report.outcome, "retained", report.as_dict())
        self.assertIsNotNone(self.transport.documents.get((item["bank_id"],
                                                           item["document_id"])))
        self.assertFalse(self.transport.deleted,
                         "a healthy retain must not undo itself")


class ADeliveryReceiptIsNotBelievedOnItsWord(MemoryTestCase):
    """The worker's own guard, independent of how strict the transport is."""

    def test_a_delete_receipt_that_reports_failure_is_not_recorded_as_done(self) -> None:
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        self.worker().run(max_ticks=6)
        item = self.store.items()[0]
        self.lifecycle().delete(item["memory_id"],
                                {"expected_revision": 1, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)

        honest = self.transport.delete_document

        def reporting_failure(**kwargs):
            receipt = honest(**kwargs)
            receipt["success"] = False
            return receipt

        self.transport.delete_document = reporting_failure
        try:
            report = self.worker().tick(scan=False)
        finally:
            self.transport.delete_document = honest

        self.assertEqual(report.error_code, "engine_delete_failed", report.as_dict())
        self.assertNotEqual(report.outcome, "deleted")
        job = next(j for j in self.job_store().jobs()
                   if j["memory_id"] == item["memory_id"] and j["kind"] == "delete")
        self.assertNotEqual(job["status"], "DONE",
                            "a receipt saying the delete failed was recorded as a delete")
        self.assertEqual(job["last_error_code"], "engine_delete_failed")
        self.assertNotEqual(self.store.item(item["memory_id"])["engine_state"], "DELETED")


class TheTakeBackOutlivesTheClaimThatOwesIt(MemoryTestCase):
    """A transient timeout must not strand a document the queue has forgotten.

    The failure the review found: the compensation for a stale retain times out,
    and the `fail()` that would record it is then refused by fencing because
    the claim has already been reclaimed. Local says `DELETED`, the document is
    in the bank, both jobs are terminal, and nothing will ever retry. One
    transient timeout is enough — no crash required.
    """

    def test_a_failing_compensation_leaves_a_job_that_can_finish_it(self) -> None:
        from jasmine_core import db as db_module

        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        item = self.store.items()[0]
        real_retain = self.transport.retain
        real_delete = self.transport.delete_document
        compensate_broken = {"on": False}

        def doomed_delete(**kwargs):
            if compensate_broken["on"]:
                raise TransportError("engine_unreachable", "the engine timed out",
                                     retryable=True)
            return real_delete(**kwargs)

        def in_flight(**kwargs):
            # The operator tombstones the item while A's engine call is running.
            self.lifecycle().delete(item["memory_id"],
                                    {"expected_revision": 1, "host_id": HOST},
                                    actor_id=HUMAN, actor_kind="human", host_id=HOST)
            # A's lease lapses before its call returns.
            with db_module.transaction(self.conn):
                self.conn.execute(
                    "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'"
                    " WHERE memory_id=? AND kind='retain'", (item["memory_id"],))
            # Worker B takes the job over: the retain early-exits and the delete
            # runs to completion while A is still on the wire.
            takeover = self.worker(lease_owner="worker-B")
            for _ in range(6):
                tick = takeover.tick(scan=False)
                if tick.outcome == "idle":
                    break
            # Only now does A's write land, after B's delete has finished.
            receipt = real_retain(**kwargs)
            compensate_broken["on"] = True
            return receipt

        self.transport.retain = in_flight
        self.transport.delete_document = doomed_delete
        try:
            stale = self.worker(lease_owner="worker-A").tick(scan=False)
        finally:
            self.transport.retain = real_retain
            self.transport.delete_document = real_delete

        jobs = {j["kind"]: j for j in self.job_store().jobs()}
        self.assertEqual(stale.error_code, "document_deleted_locally",
                         stale.as_dict())
        self.assertNotEqual(jobs["retain"]["status"], "DONE")
        # The whole point: the take-back is queued on a row with no owner, so a
        # refused `fail()` cannot drop it.
        self.assertEqual(jobs["delete"]["status"], "PENDING",
                         f"the take-back was not queued: {jobs['delete']}")
        self.assertIsNotNone(self.transport.documents.get((item["bank_id"],
                                                           item["document_id"])),
                             "the stale write is still out there")

        # The engine answers again and the queue finishes the job fencing could.
        report = self.worker(lease_owner="worker-C").run(max_ticks=8)
        jobs = {j["kind"]: j for j in self.job_store().jobs()}
        self.assertIsNone(self.transport.documents.get((item["bank_id"],
                                                        item["document_id"])),
                          "the queued take-back never ran to completion")
        self.assertEqual(jobs["delete"]["status"], "DONE", jobs["delete"])
        self.assertEqual(jobs["retain"]["status"], "FAILED", jobs["retain"])
        self.assertEqual(self.store.item(item["memory_id"])["engine_state"], "DELETED")
        self.assertTrue(any(r.outcome == "deleted" for r in report), report)

    def test_the_compensation_believes_a_failing_receipt(self) -> None:
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        item = self.store.items()[0]
        real_retain = self.transport.retain
        real_delete = self.transport.delete_document

        def in_flight(**kwargs):
            receipt = real_retain(**kwargs)
            self.lifecycle().delete(item["memory_id"],
                                    {"expected_revision": 1, "host_id": HOST},
                                    actor_id=HUMAN, actor_kind="human", host_id=HOST)
            return receipt

        def lying_delete(**kwargs):
            result = real_delete(**kwargs)
            result["success"] = False
            return result

        self.transport.retain = in_flight
        self.transport.delete_document = lying_delete
        try:
            report = self.worker(lease_owner="worker-A").tick(scan=False)
        finally:
            self.transport.retain = real_retain
            self.transport.delete_document = real_delete

        self.assertEqual(report.error_code, "document_deleted_locally",
                         report.as_dict())
        jobs = {j["kind"]: j for j in self.job_store().jobs()}
        self.assertEqual(jobs["delete"]["status"], "PENDING",
                         "a compensation that reported failure was recorded as done")
        # The double has already popped the document — it acts before it lies —
        # so the assertion is on what Core recorded, not on the double's state.
        self.assertNotEqual(self.store.item(item["memory_id"])["engine_state"],
                            "DELETED", "a receipt saying `false` marked the item deleted")

    def test_re_queueing_a_delete_for_a_document_already_gone_terminates(self) -> None:
        # The re-armed job may find the document already absent; that is the
        # objective, so it must end `DONE` rather than a permanent failure.
        project = self.project()
        self.active_decision(project_id=project)
        self.scan()
        item = self.store.items()[0]
        self.worker().run(max_ticks=6)
        self.lifecycle().delete(item["memory_id"],
                                {"expected_revision": 1, "host_id": HOST},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.worker().run(max_ticks=6)
        self.assertEqual(self.job_store().ensure_engine_work(
            memory_id=item["memory_id"], kind="delete",
            source_event_id=item["source_event_id"], reason="re-arm")["status"], "PENDING")
        self.worker().run(max_ticks=6)
        job = next(j for j in self.job_store().jobs()
                   if j["memory_id"] == item["memory_id"] and j["kind"] == "delete")
        self.assertEqual(job["status"], "DONE",
                         f"an already-absent document stranded the job: {job}")
        self.assertEqual(self.store.item(item["memory_id"])["engine_state"], "DELETED")
