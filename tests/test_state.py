"""P1-02 State API, migration, replay and fail-closed evidence checks."""
from __future__ import annotations

import json
import unittest

from test_api import ACTOR, HOST, ApiTestCase
from jasmine_core import auth, db, ids, registry
from jasmine_core.state import EvidenceValidationResult, StateStore

AGENT = ids.new_id("act")
REQ = {"requirements": [{"key": "build", "kind": "BUILD", "required_result": "PASS"}]}


class StateHttp(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            registry.Registry(conn).upsert_actor(AGENT, kind="agent", home_host_id=HOST)
        self.agent_token = auth.Auth(conn).issue_key(
            actor_id=AGENT, label="agent", scopes=["objects:write", "state:write", "state:read"])["token"]
        self.human_token = self.key(["objects:write", "objects:read", "state:write", "state:read", "state:accept"])
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST}, token=self.human_token)
        self.project = project["object"]["project_id"]
        _, task = self.call("POST", "/v1/tasks", {"title": "t", "project_id": self.project,
                 "host_id": HOST, "acceptance_criteria": REQ}, token=self.human_token)
        self.task = task["object"]["task_id"]

    def create_step(self, *, criteria=REQ, token=None, revision=1, event_id=None):
        return self.call("POST", f"/v1/tasks/{self.task}/steps", {
            "title": "build", "host_id": HOST, "expected_revision": revision,
            "event_id": event_id or ids.new_id("evt"), "acceptance_criteria": criteria},
            token=token or self.human_token)

    def test_step_revision_task_revision_replay_and_history(self) -> None:
        event_id = ids.new_id("evt")
        status, created = self.create_step(event_id=event_id)
        self.assertEqual(status, 201, created)
        self.assertEqual((created["step"]["status"], created["step"]["revision"],
                          created["task_revision"]), ("PLANNED", 1, 2))
        sid = created["step"]["step_id"]
        body = {"status": "IN_PROGRESS", "expected_revision": 1, "host_id": HOST,
                "event_id": ids.new_id("evt")}
        status, changed = self.call("POST", f"/v1/steps/{sid}/transition", body, token=self.agent_token)
        self.assertEqual(status, 200, changed)
        self.assertEqual((changed["step"]["revision"], changed["task_revision"]), (2, 3))
        status, replay = self.call("POST", f"/v1/steps/{sid}/transition", body, token=self.agent_token)
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay["step"], changed["step"])
        self.assertEqual(replay["task_revision"], changed["task_revision"])
        self.assertEqual(replay["event"], changed["event"])
        self.assertEqual(replay["evidence_ids"], changed["evidence_ids"])
        status, first_replay = self.create_step(event_id=event_id)
        self.assertEqual(status, 200, first_replay)
        self.assertEqual(first_replay["step"], created["step"])
        status, history = self.call("GET", f"/v1/steps/{sid}/history", token=self.human_token)
        self.assertEqual([e["event_type"] for e in history["events"]], ["step.created", "step.transitioned"])
        status, task = self.call("GET", f"/v1/tasks/{self.task}", token=self.human_token)
        self.assertEqual(task["task"]["revision"], 3)

    def test_task_create_replay_returns_original_snapshot(self) -> None:
        event_id = ids.new_id("evt")
        body = {"title": "later", "project_id": self.project, "host_id": HOST,
                "acceptance_criteria": REQ, "event_id": event_id}
        status, first = self.call("POST", "/v1/tasks", body, token=self.human_token)
        self.assertEqual(status, 201)
        tid = first["object"]["task_id"]
        self.call("POST", f"/v1/tasks/{tid}/steps", {"title": "s", "host_id": HOST,
            "expected_revision": 1}, token=self.human_token)
        status, replay = self.call("POST", "/v1/tasks", body, token=self.human_token)
        self.assertEqual((status, replay["replayed"]), (200, True))
        self.assertEqual(replay["object"], first["object"])
        self.assertEqual(replay["event"], first["event"])

    def test_old_revision_and_invalid_jump_leave_truth_unchanged(self) -> None:
        _, created = self.create_step()
        sid = created["step"]["step_id"]
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        status, missing = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "IN_PROGRESS", "host_id": HOST}, token=self.agent_token)
        self.assertEqual((status, missing["error"]["code"]), (400, "missing_expected_revision"))
        status, jump = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "EXECUTED", "expected_revision": 1, "host_id": HOST}, token=self.agent_token)
        self.assertEqual((status, jump["error"]["code"]), (409, "invalid_state_transition"))
        status, changed = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "IN_PROGRESS", "expected_revision": 1, "host_id": HOST}, token=self.agent_token)
        self.assertEqual(status, 200, changed)
        status, stale = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "IN_PROGRESS", "expected_revision": 1, "host_id": HOST}, token=self.agent_token)
        self.assertEqual((status, stale["error"]["code"]), (409, "revision_conflict"))
        self.assertEqual(stale["error"]["details"]["current_revision"], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before + 1)

    def test_evidence_fail_closed_and_agent_criteria_denied(self) -> None:
        status, denied = self.call("POST", "/v1/tasks", {"title": "weak", "project_id": self.project,
            "host_id": HOST, "acceptance_criteria": REQ}, token=self.agent_token)
        self.assertEqual((status, denied["error"]["code"]), (403, "forbidden_scope"))
        status, denied = self.create_step(token=self.agent_token)
        self.assertEqual((status, denied["error"]["code"]), (403, "forbidden_scope"))
        _, created = self.create_step()
        sid = created["step"]["step_id"]
        self.call("POST", f"/v1/steps/{sid}/transition", {"status": "IN_PROGRESS",
            "expected_revision": 1, "host_id": HOST}, token=self.agent_token)
        self.call("POST", f"/v1/steps/{sid}/transition", {"status": "EXECUTED",
            "expected_revision": 2, "host_id": HOST}, token=self.agent_token)
        status, refusal = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "VERIFIED", "expected_revision": 3, "host_id": HOST}, token=self.agent_token)
        self.assertEqual((status, refusal["error"]["code"]), (422, "missing_evidence"))
        status, step = self.call("GET", f"/v1/steps/{sid}", token=self.human_token)
        self.assertEqual(step["step"]["status"], "EXECUTED")
        self.assertEqual(step["step"]["revision"], 3)

    def test_malformed_enums_and_hashes_are_400_without_truth_write(self) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        before_revision = conn.execute("SELECT revision FROM tasks WHERE task_id=?", (self.task,)).fetchone()[0]
        for bad_requirement in (
            {"key": "x", "kind": [], "required_result": "PASS"},
            {"key": "x", "kind": "BUILD", "required_result": []},
            {"key": "x", "kind": "BUILD", "required_result": "PASS", "command_sha256": "abc"},
            {"key": "x", "kind": "BUILD", "required_result": "PASS", "artifact_sha256": ["a"]},
        ):
            status, refusal = self.create_step(criteria={"requirements": [bad_requirement]})
            self.assertEqual((status, refusal["error"]["code"]), (400, "invalid_request"), refusal)
        _, created = self.create_step()
        sid = created["step"]["step_id"]
        after_create_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        after_create_revision = conn.execute("SELECT revision FROM tasks WHERE task_id=?", (self.task,)).fetchone()[0]
        for bad_status in ([], {}):
            status, refusal = self.call("POST", f"/v1/steps/{sid}/transition", {
                "status": bad_status, "expected_revision": 1, "host_id": HOST}, token=self.agent_token)
            self.assertEqual((status, refusal["error"]["code"]), (400, "invalid_request"), refusal)
            status, refusal = self.call("POST", f"/v1/tasks/{self.task}/transition", {
                "status": bad_status, "expected_revision": after_create_revision,
                "host_id": HOST}, token=self.human_token)
            self.assertEqual((status, refusal["error"]["code"]), (400, "invalid_request"), refusal)
        self.assertEqual(after_create_events, before_events + 1)
        self.assertEqual(after_create_revision, before_revision + 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], after_create_events)
        self.assertEqual(conn.execute("SELECT revision FROM tasks WHERE task_id=?", (self.task,)).fetchone()[0], after_create_revision)
        self.assertEqual(conn.execute("SELECT revision FROM steps WHERE step_id=?", (sid,)).fetchone()[0], 1)

    def test_accept_requires_real_step_and_real_evidence(self) -> None:
        status, refusal = self.call("POST", f"/v1/tasks/{self.task}/accept", {
            "expected_revision": 1, "host_id": HOST}, token=self.human_token)
        self.assertEqual((status, refusal["error"]["code"]), (409, "invalid_state_transition"))
        _, created = self.create_step()
        sid = created["step"]["step_id"]
        status, refusal = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "SKIPPED", "reason": "not required", "expected_revision": 1,
            "host_id": HOST}, token=self.agent_token)
        self.assertEqual((status, refusal["error"]["code"]), (403, "forbidden_scope"))
        status, skip = self.call("POST", f"/v1/steps/{sid}/transition", {
            "status": "SKIPPED", "reason": "not required", "expected_revision": 1,
            "host_id": HOST}, token=self.human_token)
        self.assertEqual(status, 200, skip)
        status, refusal = self.call("POST", f"/v1/tasks/{self.task}/accept", {
            "expected_revision": 3, "host_id": HOST}, token=self.human_token)
        self.assertEqual((status, refusal["error"]["code"]), (409, "invalid_state_transition"))


class EvidenceTrace(ApiTestCase):
    def test_proof_ids_are_in_event_and_replay(self) -> None:
        # A test double exercises the store-to-Event wiring only. Production has
        # no permissive default and the HTTP test above checks its refusal.
        rule_ref = {"rule_id": ids.new_id("rul"), "version": 2}
        class FixtureValidator:
            def validate(self, request):
                return EvidenceValidationResult([ids.new_id("evd")], [rule_ref])
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        token = self.key(["objects:write", "state:write", "state:accept"])
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST}, token=token)
        _, task = self.call("POST", "/v1/tasks", {"title": "t", "project_id": project["object"]["project_id"],
            "host_id": HOST}, token=token)
        store = StateStore(conn, schema_version=4, evidence_validator=FixtureValidator())
        step = store.create_step(task["object"]["task_id"], {"title": "s", "host_id": HOST,
            "expected_revision": 1, "acceptance_criteria": REQ}, actor_id=ACTOR,
            actor_kind="human", can_accept=True)["step"]
        store.transition_step(step["step_id"], {"status": "IN_PROGRESS", "host_id": HOST,
            "expected_revision": 1}, actor_id=ACTOR, actor_kind="human", can_accept=True)
        store.transition_step(step["step_id"], {"status": "EXECUTED", "host_id": HOST,
            "expected_revision": 2}, actor_id=ACTOR, actor_kind="human", can_accept=True)
        body = {"status": "VERIFIED", "host_id": HOST, "expected_revision": 3,
                "event_id": ids.new_id("evt")}
        first = store.transition_step(step["step_id"], body, actor_id=ACTOR,
                                      actor_kind="human", can_accept=True)
        replay = store.transition_step(step["step_id"], body, actor_id=ACTOR,
                                       actor_kind="human", can_accept=True)
        self.assertEqual(replay["step"], first["step"])
        self.assertEqual(replay["evidence_ids"], first["evidence_ids"])
        self.assertEqual(first["event"]["payload"]["evidence_ids"], first["evidence_ids"])
        self.assertEqual(first["event"]["payload"]["rule_versions"], [rule_ref])
        self.assertEqual(replay["rule_versions"], [rule_ref])
        self.assertEqual(store.history(task["object"]["task_id"], step_id=step["step_id"])[-1]
                         ["payload"]["evidence_ids"], first["evidence_ids"])


class MigrationAndAtomicity(ApiTestCase):
    def test_projection_failure_rolls_back_event_and_task_revision(self) -> None:
        token = self.key(["objects:write", "state:write"])
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST}, token=token)
        _, task = self.call("POST", "/v1/tasks", {"title": "t", "project_id": project["object"]["project_id"],
            "host_id": HOST}, token=token)
        tid = task["object"]["task_id"]
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("CREATE TRIGGER test_fail_step BEFORE INSERT ON steps BEGIN SELECT RAISE(ABORT, 'injected'); END")
        before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        status, _ = self.call("POST", f"/v1/tasks/{tid}/steps", {"title": "s", "host_id": HOST,
            "expected_revision": 1}, token=token)
        self.assertEqual(status, 500)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
        self.assertEqual(conn.execute("SELECT revision FROM tasks WHERE task_id=?", (tid,)).fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0], 0)

    def test_concurrent_clients_with_same_revision_only_one_succeeds(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        token = self.key(["objects:write", "state:write"])
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST}, token=token)
        _, task = self.call("POST", "/v1/tasks", {"title": "t", "project_id": project["object"]["project_id"],
            "host_id": HOST}, token=token)
        tid = task["object"]["task_id"]
        bodies = [{"title": f"s{i}", "host_id": HOST, "expected_revision": 1,
                   "event_id": ids.new_id("evt")} for i in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda body: self.call("POST", f"/v1/tasks/{tid}/steps", body, token=token), bodies))
        self.assertEqual(sorted(status for status, _ in replies), [201, 409], replies)
        self.assertEqual(next(body for status, body in replies if status == 409)["error"]["code"],
                         "revision_conflict")


class UpgradeFromP0(unittest.TestCase):
    def test_open_task_becomes_active_without_losing_revision(self) -> None:
        import tempfile
        from pathlib import Path
        from jasmine_core import clock
        from jasmine_core.db import set_meta
        from jasmine_core.events import EventStore
        from jasmine_core.migrations import discover, migrate
        from jasmine_core.models import NewEvent
        temp = tempfile.TemporaryDirectory(prefix="p1-old-db-")
        self.addCleanup(temp.cleanup)
        conn = db.connect(Path(temp.name) / "old.db")
        self.addCleanup(conn.close)
        with db.transaction(conn):
            for migration in discover()[:3]:
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                             (migration.name, migration.version, migration.checksum, clock.now_rfc3339()))
                set_meta(conn, "schema_version", migration.version)
            reg = registry.Registry(conn)
            reg.upsert_host(HOST)
            reg.upsert_actor(ACTOR, kind="human", home_host_id=HOST)
            project_id, task_id = ids.new_id("prj"), ids.new_id("tsk")
            events = EventStore(conn, schema_version=3)
            project_event, _ = events.append(NewEvent(event_type="project.created", source_system="test",
                actor_id=ACTOR, actor_kind="human", host_id=HOST, payload={"text": ""},
                occurred_at=clock.now()))
            conn.execute("INSERT INTO projects (project_id,name,source_event_id,created_at,updated_at) VALUES (?,?,?,?,?)",
                         (project_id, "p", project_event["event_id"], clock.now_rfc3339(), clock.now_rfc3339()))
            task_event, _ = events.append(NewEvent(event_type="task.created", source_system="test",
                actor_id=ACTOR, actor_kind="human", host_id=HOST, project_id=project_id,
                payload={"text": ""}, occurred_at=clock.now()))
            conn.execute("INSERT INTO tasks (task_id,project_id,title,status,revision,source_event_id,created_at,updated_at) VALUES (?,?,?,'open',7,?,?,?)",
                         (task_id, project_id, "t", task_event["event_id"], clock.now_rfc3339(), clock.now_rfc3339()))
        self.assertEqual([migration for migration in migrate(conn)], ["m0004_task_step_state", "m0005_evidence_fingerprint", "m0006_codex_exec_observations", "m0007_interpretations"])
        task = conn.execute("SELECT status,revision,acceptance_criteria_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        self.assertEqual((task["status"], task["revision"], json.loads(task["acceptance_criteria_json"])),
                         ("ACTIVE", 7, {"requirements": []}))
