"""Shared fixture for Host Hook Protocol tests (ADR 0017).

Component level: a real HTTP Core on a scratch database in a temp dir, with a
fake tokenizer and fake workspace scanner for Context/Checkpoint (the pinned
tiktoken asset is not a dependency of the test environment). These tests never
establish native agent behaviour.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_api import ACTOR, HOST, ApiTestCase

from jasmine_core import auth, db, ids, registry
from jasmine_core.hosthook.protocol import PROTOCOL_VERSION

SYSTEM = ids.new_id("act")
AGENT = ids.new_id("act")
CAPS = "a" * 64


class FakeTokenizer:
    identity = {"name": "fake", "version": "0", "encoding": "fake", "qualification": "TEST_ONLY",
                "counting_scope": "TEST_ONLY", "maximum_tokens": 2500}

    def count(self, text: str) -> int:
        return len(text) // 4 + 1


def fake_scan(budget, *, conn):
    budget.remaining()
    return {"snapshot": {"complete": False, "partial_reasons": ["component"]},
            "sample_window": {"started_at_unix": 1, "finished_at_unix": 2}}


class HostHookHttp(ApiTestCase):
    """Project, Task, Step, an ACTIVE DENY Rule, a system key and a human key."""

    with_workspace = True

    def setUp(self) -> None:
        super().setUp()
        from jasmine_core.api import handlers
        from jasmine_core.context.builder import ContextBuilder
        from jasmine_core.continuity import ContinuityStore

        def builder(conn, *, schema_version, memory=None, **kwargs):
            return ContextBuilder(conn, schema_version=schema_version, tokenizer=FakeTokenizer(),
                                  memory=memory, scanner=fake_scan)

        def continuity(conn, *, schema_version, **kwargs):
            return ContinuityStore(conn, schema_version=schema_version, scanner=fake_scan)

        for target, replacement in (("ContextBuilder", builder), ("ContinuityStore", continuity)):
            patcher = patch.object(handlers, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.workspace = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-workspace-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        (self.root / "source.txt").write_text("initial\n")
        env = {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["source.txt"]'}
        if self.with_workspace:
            env["JASMINE_CORE_WORKSPACE_ROOT"] = str(self.root)
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        if not self.with_workspace:
            os.environ.pop("JASMINE_CORE_WORKSPACE_ROOT", None)

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_actor(SYSTEM, kind="system", home_host_id=HOST)
            reg.upsert_actor(AGENT, kind="agent", home_host_id=HOST)
        issuer = auth.Auth(conn)
        self.system_token = issuer.issue_key(actor_id=SYSTEM, label="hook-system",
                                             scopes=["hosthook:report"])["token"]
        self.human_hook_token = self.key(["hosthook:report"], label="hook-human")
        self.manage = self.key(["objects:write", "objects:read", "events:write", "events:read",
                                "authority:manage", "authority:propose", "authority:read",
                                "state:write", "state:read", "evidence:read", "admin"])
        code, project = self.call("POST", "/v1/projects", {"name": "hosthook", "host_id": HOST}, token=self.manage)
        self.assertEqual(code, 201, project)
        self.project = project["object"]["project_id"]
        code, task = self.call("POST", "/v1/tasks", {"project_id": self.project, "title": "Hook goal",
                                                     "host_id": HOST}, token=self.manage)
        self.assertEqual(code, 201, task)
        self.task = task["object"]["task_id"]
        code, step = self.call("POST", f"/v1/tasks/{self.task}/steps",
                               {"title": "step", "host_id": HOST, "expected_revision": 1}, token=self.manage)
        self.assertEqual(code, 201, step)
        self.step = step["step"]["step_id"]
        code, origin = self.call("POST", "/v1/events", self.event_body(
            project_id=self.project, task_id=self.task, event_id=ids.new_id("evt")), token=self.manage)
        self.assertEqual(code, 201, origin)
        self.origin = origin["event"]["event_id"]

    # -- builders ---------------------------------------------------------------

    def rule(self, key: str, enforcement: str, matcher: dict) -> str:
        body = {"rule_key": key, "kind": "RULE", "severity": "HARD", "enforcement": enforcement,
                "content": key, "matcher": matcher,
                "scope": {"kind": "task", "project_id": self.project, "task_id": self.task},
                "origin_event_id": self.origin, "host_id": HOST, "event_id": ids.new_id("evt")}
        code, proposed = self.call("POST", "/v1/rules/proposals", body, token=self.manage)
        self.assertEqual(code, 201, proposed)
        rule_id = proposed["rule"]["rule_id"]
        code, approved = self.call("POST", f"/v1/rules/{rule_id}/approve",
                                   {"host_id": HOST, "expected_revision": 1}, token=self.manage)
        self.assertEqual(code, 200, approved)
        return rule_id

    def request(self, event: str, payload: dict, *, delivery: str | None = None, task: bool = True,
                step: bool = True, native: dict | None = None, **overrides) -> dict:
        binding = {"project_id": self.project}
        if task:
            binding["task_id"] = self.task
            if step:
                binding["step_id"] = self.step
        body = {"protocol_version": PROTOCOL_VERSION, "event": event,
                "delivery_id": delivery or f"test:{event}:{ids.new_id('evt')}",
                "agent": {"name": "fixture-agent", "version": "0"},
                "adapter": {"name": "fixture", "version": "1", "capabilities_sha256": CAPS},
                "host_id": HOST, "binding": binding,
                "native": native or {"session_id": "native-session-1", "turn_id": "turn-1", "call_id": "call-1"},
                "trust": "NATIVE_HOOK", "payload": payload}
        body.update(overrides)
        return body

    def send(self, body: dict, token: str | None = None):
        return self.call("POST", "/v1/host-hooks/events", body, token=token or self.system_token)

    def tool_before(self, command_action: str | None, paths: list[str] | None, **kw):
        payload = {"tool": "shell", "native_tool": "Bash", "input": {"command": "x"}}
        if command_action:
            payload["action"] = command_action
        if paths:
            payload["paths"] = paths
        return self.send(self.request("tool.before", payload, **kw))

    def db_rows(self, sql: str, params=()):
        conn = db.connect(self.db_path)
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()
