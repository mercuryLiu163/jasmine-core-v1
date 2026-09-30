"""P1 Evidence and State tests against the real HTTP server and temp workspace."""
from __future__ import annotations

import os
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_api import ACTOR, HOST, ApiTestCase
from jasmine_core import auth, db, ids, registry


SYSTEM = ids.new_id("act")


class EvidenceHttp(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = tempfile.TemporaryDirectory(prefix="jasmine-evidence-workspace-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        (self.root / "source.txt").write_text("initial\n")
        self.root_patch = patch.dict(os.environ, {
            "JASMINE_CORE_WORKSPACE_ROOT": str(self.root),
            "JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["source.txt"]'})
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            registry.Registry(conn).upsert_actor(SYSTEM, kind="system", home_host_id=HOST)
        self.system_token = auth.Auth(conn).issue_key(actor_id=SYSTEM, label="hook",
            scopes=["evidence:write", "evidence:read", "fingerprint:scan", "fingerprint:read",
                    "guard:check", "state:write"]) ["token"]
        self.human_token = self.key(["objects:write", "objects:read", "events:write",
                                      "authority:manage", "authority:propose", "authority:read",
                                      "state:write", "state:accept", "state:read", "evidence:confirm", "evidence:read"])
        code, project = self.call("POST", "/v1/projects", {"name": "evidence-project", "host_id": HOST},
                                  token=self.human_token)
        self.assertEqual(code, 201, project)
        self.project = project["object"]["project_id"]
        criteria = {"requirements": [{"key": "run", "kind": "COMMAND_RESULT", "required_result": "PASS"}]}
        code, task = self.call("POST", "/v1/tasks", {"project_id": self.project,
            "title": "task", "host_id": HOST, "acceptance_criteria": criteria}, token=self.human_token)
        self.assertEqual(code, 201, task)
        self.task = task["object"]["task_id"]
        code, step = self.call("POST", f"/v1/tasks/{self.task}/steps", {
            "title": "step", "host_id": HOST, "expected_revision": 1,
            "acceptance_criteria": criteria}, token=self.human_token)
        self.assertEqual(code, 201, step)
        self.step = step["step"]["step_id"]

    def transition(self, status: str, revision: int, token: str | None = None):
        return self.call("POST", f"/v1/steps/{self.step}/transition", {
            "status": status, "expected_revision": revision, "host_id": HOST},
            token=token or self.system_token)

    def tool(self, use: str, *, exit_code: int = 0, kind: str = "COMMAND_RESULT"):
        return self.call("POST", "/v1/tool-results", {
            "task_id": self.task, "step_id": self.step, "host_id": HOST,
            "codex_session_id": "codex-fixture", "turn_id": "turn-1", "tool_use_id": use,
            "tool_name": "Bash", "tool_input": {"command": "true"},
            "tool_response": {"exit_code": exit_code, "output": ""}, "kind": kind},
            token=self.system_token)

    def test_tool_result_then_verified_and_workspace_stale(self) -> None:
        self.assertEqual(self.transition("IN_PROGRESS", 1)[0], 200)
        code, captured = self.tool("use-1")
        self.assertEqual(code, 201, captured)
        self.assertEqual(captured["evidence"]["status"], "PASS")
        self.assertEqual(self.tool("use-1"), (200, {**captured, "replayed": True}))
        self.assertEqual(self.transition("EXECUTED", 2)[0], 200)
        code, verified = self.transition("VERIFIED", 3)
        self.assertEqual(code, 200, verified)
        self.assertEqual(verified["step"]["status"], "VERIFIED")
        self.assertEqual(verified["evidence_ids"], [captured["evidence"]["evidence_id"]])
        (self.root / "source.txt").write_text("changed\n")
        code, refreshed = self.call("POST", "/v1/workspaces/fingerprint", {
            "project_id": self.project, "host_id": HOST}, token=self.system_token)
        self.assertEqual(code, 200, refreshed)
        self.assertEqual(refreshed["staled_steps"], [self.step])
        code, read = self.call("GET", f"/v1/steps/{self.step}", token=self.human_token)
        self.assertEqual(read["step"]["status"], "STALE")
        code, history = self.call("GET", f"/v1/steps/{self.step}/history", token=self.human_token)
        self.assertEqual(history["events"][-1]["event_type"], "step.staled")

    def test_failed_result_cannot_verify_or_forge_specialized_kind(self) -> None:
        self.transition("IN_PROGRESS", 1)
        code, refused = self.tool("echo-as-test", kind="TEST")
        self.assertEqual((code, refused["error"]["code"]), (400, "invalid_request"))
        mapping = Path(self._tmp.name).resolve() / "producer.json"
        mapping.write_text(json.dumps([{"kind": "BUILD", "tool_name": "Bash",
            "command_sha256": hashlib.sha256(b"true").hexdigest()}]))
        mapping.chmod(0o600)
        with patch.dict(os.environ, {"JASMINE_CORE_EVIDENCE_PRODUCERS": str(mapping)}):
            code, built = self.tool("real-build", kind="BUILD")
            self.assertEqual((code, built["evidence"]["kind"]), (201, "BUILD"))
            code, refused = self.tool("build-as-device", kind="DEVICE_TEST")
            self.assertEqual((code, refused["error"]["code"]), (400, "invalid_request"))
        code, failure = self.tool("use-fail", exit_code=7)
        self.assertEqual((code, failure["evidence"]["status"]), (201, "FAIL"))
        self.transition("EXECUTED", 2)
        code, refused = self.transition("VERIFIED", 3)
        self.assertEqual((code, refused["error"]["code"]), (422, "missing_evidence"))

    def test_old_cycle_evidence_cannot_verify_retry(self) -> None:
        self.transition("IN_PROGRESS", 1)
        self.tool("first-cycle")
        self.transition("FAILED", 2)
        self.transition("IN_PROGRESS", 3)
        self.transition("EXECUTED", 4)
        code, refused = self.transition("VERIFIED", 5)
        self.assertEqual((code, refused["error"]["code"]), (422, "missing_evidence"))

    def test_active_rule_requirements_bind_exact_version_and_are_audited(self) -> None:
        code, origin = self.call("POST", "/v1/events", {
            "event_type": "assistant.message", "source_system": "test", "host_id": HOST,
            "project_id": self.project, "task_id": self.task,
            "payload": {"text": "propose a command gate"}}, token=self.human_token)
        self.assertEqual(code, 201, origin)
        rule_body = {"rule_key": "verify.command", "kind": "ACCEPTANCE", "severity": "NORMAL",
            "enforcement": "VERIFY", "content": "Require a real command result",
            "matcher": {}, "scope": {"kind": "task", "project_id": self.project,
                                         "task_id": self.task},
            "origin_event_id": origin["event"]["event_id"], "host_id": HOST,
            "verification_requirements": {"requirements": [{
                "key": "rule-command", "kind": "COMMAND_RESULT", "required_result": "PASS"}]}}
        code, proposed = self.call("POST", "/v1/rules/proposals", rule_body,
                                   token=self.human_token)
        self.assertEqual(code, 201, proposed)
        rule_id = proposed["rule"]["rule_id"]
        code, approved = self.call("POST", f"/v1/rules/{rule_id}/approve", {
            "expected_revision": 1, "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, approved)
        self.transition("IN_PROGRESS", 1)
        self.tool("rule-source")
        self.transition("EXECUTED", 2)
        code, verified = self.transition("VERIFIED", 3)
        self.assertEqual(code, 200, verified)
        self.assertEqual(verified["rule_versions"], [{"rule_id": rule_id, "version": 1}])
        code, history = self.call("GET", f"/v1/rules/{rule_id}/history", token=self.human_token)
        self.assertEqual(code, 200, history)
        self.assertEqual(history["history"][0]["rule"]["verification_requirements"],
                         rule_body["verification_requirements"])

    def test_human_confirmation_after_current_revision_accepts_step_and_task(self) -> None:
        self.transition("IN_PROGRESS", 1)
        self.tool("accept-source")
        self.transition("EXECUTED", 2)
        self.transition("VERIFIED", 3)
        code, old_origin = self.call("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "codex-p1-bound",
            "source_event_id": "s:old", "host_id": HOST, "project_id": self.project,
            "task_id": self.task, "payload": {"text": "I confirm this step", "step_id": self.step}},
            token=self.human_token)
        self.assertEqual(code, 201, old_origin)
        body = {"task_id": self.task, "step_id": self.step, "host_id": HOST,
                "origin_event_id": old_origin["event"]["event_id"],
                "expected_revision": 4}
        code, confirmed = self.call("POST", "/v1/evidence/confirm", body,
                                    token=self.human_token)
        self.assertEqual(code, 201, confirmed)
        self.assertEqual(confirmed["evidence"]["kind"], "USER_CONFIRMATION")
        code, accepted = self.transition("ACCEPTED", 4, self.human_token)
        self.assertEqual(code, 200, accepted)
        self.assertIn(confirmed["evidence"]["evidence_id"], accepted["evidence_ids"])
        code, refused = self.call("POST", "/v1/evidence/confirm", {
            **body, "expected_revision": 7}, token=self.human_token)
        self.assertEqual(code, 400, refused)
        code, task_view = self.call("GET", f"/v1/tasks/{self.task}", token=self.human_token)
        revision = task_view["task"]["revision"]
        code, task_origin = self.call("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "codex-p1-bound",
            "source_event_id": "s:new", "host_id": HOST, "project_id": self.project,
            "task_id": self.task, "payload": {"text": "I accept this task"}},
            token=self.human_token)
        self.assertEqual(code, 201, task_origin)
        code, task_confirmation = self.call("POST", "/v1/evidence/confirm", {
            "task_id": self.task, "host_id": HOST,
            "origin_event_id": task_origin["event"]["event_id"],
            "expected_revision": revision}, token=self.human_token)
        self.assertEqual(code, 201, task_confirmation)
        code, result = self.call("POST", f"/v1/tasks/{self.task}/accept", {
            "expected_revision": revision, "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["task"]["status"], "ACCEPTED")
