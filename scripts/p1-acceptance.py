#!/usr/bin/env python3
"""P1-T01..T09 independent component acceptance on throwaway resources.

Every taskbook case has its own assertions and fresh database. This runner
never installs hooks or uses the user's Core database. T10 is deliberately
excluded: synthetic HTTP traffic cannot prove a real Codex tool gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jasmine_core import SCHEMA_VERSION, auth, clock, db, ids, registry  # noqa: E402
from jasmine_core.api.server import Application, _Handler  # noqa: E402
from jasmine_core.events import EventStore  # noqa: E402
from jasmine_core.migrations import current_version, discover, migrate  # noqa: E402
from jasmine_core.models import NewEvent  # noqa: E402
from jasmine_core.db import set_meta  # noqa: E402


class CheckFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def expect(reply: tuple[int, dict], status: int, code: str | None = None) -> dict:
    actual, body = reply
    if actual != status or (code is not None and body.get("error", {}).get("code") != code):
        raise CheckFailure(f"expected HTTP {status}/{code}, got {actual}/{body.get('error', {}).get('code')}")
    return body


def sha_command(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class CaseRecord:
    case_id: str
    verdict: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    failure_class: str | None = None
    failure_output: str | None = None
    reproduction: list[str] = field(default_factory=list)


class Fixture:
    def __init__(self, work: Path, case_id: str) -> None:
        self.root = work / case_id
        self.root.mkdir(mode=0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "source.txt").write_text("v1", encoding="utf-8")
        for command in (["git", "init", "-q", str(self.workspace)],
                        ["git", "-C", str(self.workspace), "add", "source.txt"],
                        ["git", "-C", str(self.workspace), "-c", "user.name=P1 Fixture",
                         "-c", "user.email=p1-fixture@local.invalid", "commit", "-qm", "initial"]):
            subprocess.run(command, check=True, capture_output=True, timeout=15)
        self.db_path = self.root / "core.db"
        self.host = ids.new_id("hst")
        self.human = ids.new_id("act")
        self.agent = ids.new_id("act")
        self.system = ids.new_id("act")
        self.observations: list[dict[str, Any]] = []
        self.previous_root = os.environ.get("JASMINE_CORE_WORKSPACE_ROOT")
        self.previous_producers = os.environ.get("JASMINE_CORE_EVIDENCE_PRODUCERS")
        os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(self.workspace)
        producers = [
            {"kind": kind, "tool_name": "exec", "command_sha256": sha_command(command)}
            for kind, command in (("BUILD", "fixture-build"), ("TEST", "fixture-test"),
                                  ("DEVICE_TEST", "fixture-device"))
        ]
        self.producer_file = self.root / "evidence-producers.json"
        self.producer_file.write_text(json.dumps(producers), encoding="utf-8")
        os.chmod(self.producer_file, 0o600)
        os.environ["JASMINE_CORE_EVIDENCE_PRODUCERS"] = str(self.producer_file)
        conn = db.connect(self.db_path)
        migrate(conn)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_host(self.host)
            for ident, kind in ((self.human, "human"), (self.agent, "agent"),
                                (self.system, "system")):
                reg.upsert_actor(ident, kind=kind, home_host_id=self.host)
        issuer = auth.Auth(conn)
        self.tokens = {
            "human": issuer.issue_key(actor_id=self.human, label="fixture-human", scopes=[
                "admin", "objects:read", "objects:write", "events:read", "events:write",
                "authority:read", "authority:propose", "authority:manage", "guard:check",
                "state:read", "state:write", "state:accept", "evidence:read",
                "evidence:confirm", "fingerprint:read"])["token"],
            "agent": issuer.issue_key(actor_id=self.agent, label="fixture-agent", scopes=[
                "objects:read", "objects:write", "events:read", "events:write",
                "authority:read", "authority:propose", "guard:check", "state:read",
                "state:write", "evidence:read"])["token"],
            "system": issuer.issue_key(actor_id=self.system, label="fixture-system", scopes=[
                "evidence:write", "evidence:read", "fingerprint:scan", "fingerprint:read"])["token"],
        }
        conn.close()
        self.port = _port()
        self.app = Application(self.db_path, log=lambda _line: None)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = self.app
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.project_id: str | None = None
        self.task_id: str | None = None

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.close()
        if self.previous_root is None:
            os.environ.pop("JASMINE_CORE_WORKSPACE_ROOT", None)
        else:
            os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = self.previous_root
        if self.previous_producers is None:
            os.environ.pop("JASMINE_CORE_EVIDENCE_PRODUCERS", None)
        else:
            os.environ["JASMINE_CORE_EVIDENCE_PRODUCERS"] = self.previous_producers

    def call(self, method: str, path: str, body: dict | None = None, *,
             role: str = "human", headers: dict[str, str] | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request_headers = {"Content-Type": "application/json",
                           "Authorization": "Bearer " + self.tokens[role]}
        request_headers.update(headers or {})
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                status, result = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status, result = error.code, json.loads(error.read())
        self.observations.append({"method": method, "path": path, "actor_kind": role,
                                  "status": status, "error_code": result.get("error", {}).get("code")})
        return status, result

    def sql_count(self, table: str) -> int:
        check(table in {"events", "tasks", "steps", "rules", "rule_versions", "evidence",
                        "workspace_fingerprints"}, "table not in count allowlist")
        conn = db.connect(self.db_path)
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    def project(self) -> str:
        if self.project_id is None:
            result = expect(self.call("POST", "/v1/projects", {"name": "acceptance-fixture",
                "host_id": self.host}), 201)
            self.project_id = result["object"]["project_id"]
        return self.project_id

    def task(self, *, title: str = "acceptance-task", criteria: dict | None = None,
             project_id: str | None = None) -> str:
        result = expect(self.call("POST", "/v1/tasks", {"title": title,
            "project_id": project_id or self.project(), "host_id": self.host,
            **({"acceptance_criteria": criteria} if criteria is not None else {})}), 201)
        self.task_id = result["object"]["task_id"]
        return self.task_id

    def step(self, task_id: str, criteria: dict, *, task_revision: int = 1) -> str:
        result = expect(self.call("POST", f"/v1/tasks/{task_id}/steps", {
            "title": "acceptance-step", "host_id": self.host,
            "expected_revision": task_revision, "acceptance_criteria": criteria}), 201)
        return result["step"]["step_id"]

    def transition_step(self, step_id: str, status: str, revision: int,
                        *, role: str = "agent", reason: str | None = None) -> dict:
        body = {"status": status, "expected_revision": revision, "host_id": self.host}
        if reason is not None:
            body["reason"] = reason
        return expect(self.call("POST", f"/v1/steps/{step_id}/transition", body,
                                role=role), 200)

    def executed(self, step_id: str) -> None:
        self.transition_step(step_id, "IN_PROGRESS", 1)
        self.transition_step(step_id, "EXECUTED", 2)

    def raw_prompt(self, *, task_id: str | None = None, project_id: str | None = None,
                   step_id: str | None = None, text: str = "fixture original words") -> dict:
        payload = {"text": text}
        if step_id is not None:
            payload["step_id"] = step_id
        body = {"event_type": "user.prompt", "source_system": "p1-component",
                "host_id": self.host, "payload": payload}
        if project_id is not None:
            body["project_id"] = project_id
        if task_id is not None:
            body["task_id"] = task_id
        return expect(self.call("POST", "/v1/events", body), 201)["event"]

    def tool(self, task_id: str, step_id: str | None, *, kind: str = "BUILD",
             exit_code: int = 0, command: str | None = None,
             tool_use_id: str | None = None, artifact_uri: str | None = None,
             role: str = "system") -> tuple[int, dict]:
        commands = {"BUILD": "fixture-build", "TEST": "fixture-test",
                    "DEVICE_TEST": "fixture-device"}
        payload = {"task_id": task_id, "step_id": step_id, "host_id": self.host,
                   "codex_session_id": "fixture-session", "turn_id": "fixture-turn",
                   "tool_use_id": tool_use_id or ids.new_id("evt"), "tool_name": "exec",
                   "tool_input": {"command": command or commands.get(kind, "fixture-command")},
                   "tool_response": {"exit_code": exit_code}, "kind": kind}
        if artifact_uri is not None:
            payload["artifact_uri"] = artifact_uri
        return self.call("POST", "/v1/tool-results", payload, role=role)


BUILD = {"requirements": [{"key": "build", "kind": "BUILD", "required_result": "PASS"}]}
TEST = {"requirements": [{"key": "test", "kind": "TEST", "required_result": "PASS"}]}
DEVICE = {"requirements": [{"key": "device", "kind": "DEVICE_TEST", "required_result": "PASS"}]}


def case_t01(f: Fixture) -> None:
    check(SCHEMA_VERSION >= 6, "P1-03 schema version 6 must remain supported by this build")
    applied = expect(f.call("GET", "/v1/meta/schema"), 200)["migrations"]
    check([m["version"] for m in applied] == list(range(1, SCHEMA_VERSION + 1)),
          "empty DB exact contiguous migration sequence through current Schema")
    conn = db.connect(f.root / "old.db")
    try:
        with db.transaction(conn):
            for migration in discover()[:2]:
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                             (migration.name, migration.version, migration.checksum,
                              clock.now_rfc3339()))
                set_meta(conn, "schema_version", migration.version)
            reg = registry.Registry(conn)
            reg.upsert_host(f.host)
            reg.upsert_actor(f.human, kind="human", home_host_id=f.host)
            project_id, task_id = ids.new_id("prj"), ids.new_id("tsk")
            events = EventStore(conn, schema_version=2)
            origin, _ = events.append(NewEvent(event_type="project.created",
                source_system="p1-component", actor_id=f.human, actor_kind="human",
                host_id=f.host, payload={"text": ""}, occurred_at=clock.now()))
            conn.execute("INSERT INTO projects(project_id,name,source_event_id,created_at,updated_at)"
                         " VALUES(?,?,?,?,?)", (project_id, "old project", origin["event_id"],
                                               clock.now_rfc3339(), clock.now_rfc3339()))
            task_event, _ = events.append(NewEvent(event_type="task.created",
                source_system="p1-component", actor_id=f.human, actor_kind="human",
                host_id=f.host, project_id=project_id, payload={"text": ""},
                occurred_at=clock.now()))
            conn.execute("INSERT INTO tasks(task_id,project_id,title,status,revision,source_event_id,"
                         "created_at,updated_at) VALUES(?,?,?,'open',7,?,?,?)",
                         (task_id, project_id, "old task", task_event["event_id"],
                          clock.now_rfc3339(), clock.now_rfc3339()))
        expected_upgrade = [migration.name for migration in discover() if migration.version > 2]
        check(expected_upgrade[:4] == ["m0003_authority", "m0004_task_step_state",
                                       "m0005_evidence_fingerprint", "m0006_codex_exec_observations"],
              "released P1 migration prefix must remain unchanged")
        check(migrate(conn) == expected_upgrade, "old DB exact migration list through current Schema")
        check(current_version(conn) == SCHEMA_VERSION, "old DB must reach current Schema")
        row = conn.execute("SELECT status,revision FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        check((row["status"], row["revision"]) == ("ACTIVE", 7), "P0 open maps ACTIVE without revision loss")
        check(migrate(conn) == [], "second migrate must be no-op")
        check(EventStore(conn, schema_version=current_version(conn)).get(task_event["event_id"]) is not None,
              "P0 Event remains readable")
    finally:
        conn.close()


def case_t02(f: Fixture) -> None:
    task = f.task()
    origin = f.raw_prompt(task_id=task, project_id=f.project())
    proposal = {"rule_key": "component-hard", "kind": "RULE", "severity": "HARD",
                "enforcement": "DENY", "content": "Do not write fixture path",
                "matcher": {"action": "write", "path_prefix": "/tmp/deny"},
                "scope": {"kind": "task", "project_id": f.project_id, "task_id": task},
                "origin_event_id": origin["event_id"], "host_id": f.host}
    proposed = expect(f.call("POST", "/v1/rules/proposals", proposal, role="agent"), 201)
    rule_id = proposed["rule"]["rule_id"]
    check(proposed["rule"]["status"] == "PROPOSED", "agent proposal not active")
    approval = {"host_id": f.host, "expected_revision": 1}
    expect(f.call("POST", f"/v1/rules/{rule_id}/approve", approval, role="agent"),
           403, "forbidden_scope")
    active = expect(f.call("POST", f"/v1/rules/{rule_id}/approve", approval), 200)
    check(active["rule"]["status"] == "ACTIVE", "human approval must activate")
    history = expect(f.call("GET", f"/v1/rules/{rule_id}/history"), 200)["history"]
    check(len(history) >= 2 and history[-1]["rule"]["version"] == 1,
          "rule history must retain proposal and approval")
    before = f.sql_count("rules")
    expect(f.call("POST", "/v1/auth/keys", {"actor_id": f.human,
        "scopes": ["authority:manage"], "label": "forged"}, role="agent"),
        403, "forbidden_scope")
    check(f.sql_count("rules") == before, "forged human key must not write Rule")


def case_t03(f: Fixture) -> None:
    task = f.task()
    project = f.project_id
    origin_global = f.raw_prompt(text="global hard rule")
    origin_task = f.raw_prompt(task_id=task, project_id=project, text="child rule")
    global_rule = {"rule_key": "global-deny", "kind": "RULE", "severity": "HARD",
                   "enforcement": "DENY", "content": "deny fixture path",
                   "matcher": {"action": "write", "path_prefix": "/tmp/deny"},
                   "scope": {"kind": "global"}, "origin_event_id": origin_global["event_id"],
                   "host_id": f.host}
    child_rule = {**global_rule, "rule_key": "child-context", "severity": "NORMAL",
                  "enforcement": "CONTEXT", "matcher": {},
                  "scope": {"kind": "task", "project_id": project, "task_id": task},
                  "origin_event_id": origin_task["event_id"]}
    global_result = expect(f.call("POST", "/v1/rules/proposals", global_rule), 201)
    child_result = expect(f.call("POST", "/v1/rules/proposals", child_rule), 201)
    for item in (global_result, child_result):
        expect(f.call("POST", f"/v1/rules/{item['rule']['rule_id']}/approve",
                      {"host_id": f.host, "expected_revision": 1}), 200)
    guard = expect(f.call("POST", "/v1/guard/check", {"task_id": task,
        "tool": "exec", "action": "write", "path": "/tmp/deny/file"}), 200)
    check(guard["decision"] == "deny", "HARD ancestor must not be shadowed")
    check(global_result["rule"]["rule_id"] in [item["rule_id"] for item in guard["matched"]],
          "Guard must report matching ancestor version")
    unknown = expect(f.call("POST", "/v1/guard/check", {"task_id": task,
        "tool": "exec", "action": "write"}), 200)
    check(unknown["decision"] != "allow", "missing path must not falsely allow")
    cross = {"host_id": f.host, "expected_revision": 2, "kind": "RULE",
             "severity": "HARD", "enforcement": "CONFIRM", "content": "exception",
             "matcher": {"action": "write", "path_prefix": "/tmp/deny"},
             "scope": {"kind": "task", "project_id": project, "task_id": task},
             "origin_event_id": origin_task["event_id"]}
    expect(f.call("POST", f"/v1/rules/{global_result['rule']['rule_id']}/supersede", cross),
           400, "invalid_request")


def case_t04(f: Fixture) -> None:
    task = f.task(criteria=BUILD)
    sid = f.step(task, BUILD)
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "EXECUTED",
        "host_id": f.host, "expected_revision": 1}, role="agent"),
        409, "invalid_state_transition")
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "IN_PROGRESS",
        "host_id": f.host}, role="agent"), 400, "missing_expected_revision")
    first = f.transition_step(sid, "IN_PROGRESS", 1)
    second = f.transition_step(sid, "EXECUTED", 2)
    check((first["step"]["revision"], second["step"]["revision"],
           second["task_revision"]) == (2, 3, 4), "Step and Task revisions diverge")
    status, history = f.call("GET", f"/v1/steps/{sid}/history")
    check(status == 200 and len(history["events"]) == 3, "Step history lacks changes")
    # Two independent clients present the same old revision; one must conflict.
    bodies = [{"status": "FAILED", "host_id": f.host, "expected_revision": 3,
               "event_id": ids.new_id("evt")} for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda body: f.call("POST", f"/v1/steps/{sid}/transition",
                                                    body, role="agent"), bodies))
    check(sorted(status for status, _ in results) == [200, 409], "two writers both succeeded")
    check(next(body for status, body in results if status == 409)["error"]["code"] ==
          "revision_conflict", "old writer wrong error")


def case_t05(f: Fixture) -> None:
    criteria = {"requirements": TEST["requirements"] + DEVICE["requirements"]}
    task = f.task(criteria=criteria)
    sid = f.step(task, criteria)
    f.executed(sid)
    before_evidence = f.sql_count("evidence")
    expect(f.tool(task, sid, kind="TEST", command="fixture-build",
                  tool_use_id="forged-build-as-test"), 400, "invalid_request")
    check(f.sql_count("evidence") == before_evidence,
          "forged TEST classification wrote Evidence")
    expect(f.call("POST", "/v1/events", {"event_type": "assistant.message",
        "source_system": "agent-claim", "host_id": f.host, "project_id": f.project_id,
        "task_id": task, "payload": {"text": "all tests passed"}}, role="agent"), 201)
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 422, "missing_evidence")
    expect(f.tool(task, sid, kind="BUILD", tool_use_id="only-build"), 201)
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 422, "missing_evidence")
    expect(f.tool(task, sid, kind="TEST", tool_use_id="test-ok"), 201)
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 422, "missing_evidence")
    expect(f.tool(task, sid, kind="DEVICE_TEST", tool_use_id="device-ok"), 201)
    verified = expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 200)
    check(len(verified["evidence_ids"]) >= 2, "VERIFIED lacks both Evidence IDs")
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "ACCEPTED",
        "host_id": f.host, "expected_revision": 4}), 422, "missing_evidence")
    check(expect(f.call("GET", f"/v1/steps/{sid}"), 200)["step"]["status"] == "VERIFIED",
          "unconfirmed Step entered ACCEPTED")
    # Component fixture confirmation proves the API/state contract only. T10
    # must independently prove that user.prompt came from a real Codex turn.
    prompt = f.raw_prompt(task_id=task, project_id=f.project_id, step_id=sid,
                          text="Component fixture confirms this Step")
    expect(f.call("POST", "/v1/evidence/confirm", {"task_id": task, "step_id": sid,
        "host_id": f.host, "origin_event_id": prompt["event_id"],
        "expected_revision": 4}), 201)
    accepted_step = expect(f.call("POST", f"/v1/steps/{sid}/transition", {
        "status": "ACCEPTED", "host_id": f.host, "expected_revision": 4}), 200)
    check(accepted_step["step"]["status"] == "ACCEPTED", "confirmed Step not accepted")
    # Task criteria are independent from Step criteria; task-level Evidence is
    # required, followed by a new revision-bound human confirmation.
    expect(f.tool(task, None, kind="TEST", tool_use_id="task-test"), 201)
    expect(f.tool(task, None, kind="DEVICE_TEST", tool_use_id="task-device"), 201)
    task_revision = expect(f.call("GET", f"/v1/tasks/{task}"), 200)["task"]["revision"]
    expect(f.call("POST", f"/v1/tasks/{task}/accept", {"expected_revision": task_revision,
        "host_id": f.host}), 422, "missing_evidence")
    prompt = f.raw_prompt(task_id=task, project_id=f.project_id,
                          text="Component fixture confirms this Task")
    expect(f.call("POST", "/v1/evidence/confirm", {"task_id": task,
        "host_id": f.host, "origin_event_id": prompt["event_id"],
        "expected_revision": task_revision}), 201)
    accepted_task = expect(f.call("POST", f"/v1/tasks/{task}/accept", {
        "expected_revision": task_revision, "host_id": f.host}), 200)
    check(accepted_task["task"]["status"] == "ACCEPTED", "confirmed Task not accepted")


def case_t06(f: Fixture) -> None:
    task = f.task(criteria=BUILD)
    sid = f.step(task, BUILD)
    f.executed(sid)
    expect(f.tool(task, sid, kind="BUILD", exit_code=1, tool_use_id="failed"), 201)
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 422, "missing_evidence")
    body = {"task_id": task, "step_id": sid, "host_id": f.host,
            "codex_session_id": "fixture-session", "turn_id": "fixture-turn",
            "tool_use_id": "same-result", "tool_name": "exec",
            "tool_input": {"command": "fixture-build"},
            "tool_response": {"exit_code": 0}, "kind": "BUILD"}
    first = expect(f.call("POST", "/v1/tool-results", body, role="system"), 201)
    check(first["call_event"]["event_type"] == "tool.call" and
          first["result_event"]["event_type"] == "tool.result", "Raw tool chain absent")
    check(first["result_event"]["actor_kind"] == "system" and
          first["evidence"]["source_event_id"] == first["result_event"]["event_id"],
          "Evidence source not trusted Raw tool result")
    before = (f.sql_count("events"), f.sql_count("evidence"))
    replay = expect(f.call("POST", "/v1/tool-results", body, role="system"), 200)
    check(replay["replayed"] and replay["evidence"]["evidence_id"] ==
          first["evidence"]["evidence_id"], "replay not stable")
    check((f.sql_count("events"), f.sql_count("evidence")) == before, "replay duplicated Truth")
    expect(f.call("POST", "/v1/tool-results",
                  {**body, "tool_response": {"exit_code": 1}}, role="system"),
           409, "event_id_conflict")


def case_t07(f: Fixture) -> None:
    task = f.task(criteria=BUILD)
    sid = f.step(task, BUILD)
    f.executed(sid)
    evidence = expect(f.tool(task, sid, kind="BUILD", tool_use_id="before-change"), 201)["evidence"]
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "host_id": f.host, "expected_revision": 3}, role="agent"), 200)
    (f.workspace / "source.txt").write_text("v2", encoding="utf-8")
    scan = expect(f.call("POST", "/v1/workspaces/fingerprint", {"project_id": f.project_id,
        "host_id": f.host}, role="system"), 200)
    compare = expect(f.call("POST", "/v1/workspaces/compare", {
        "left_sha256": evidence["fingerprint_sha256"], "right_sha256": scan["fingerprint_sha256"]},
        role="system"), 200)
    check(compare["comparison"] == "MISMATCH", "relevant source edit compared SAME")
    step = expect(f.call("GET", f"/v1/steps/{sid}"), 200)["step"]
    check(step["status"] == "STALE", "verified Step did not persist STALE")
    history = expect(f.call("GET", f"/v1/steps/{sid}/history"), 200)["events"]
    check(history[-1]["payload"].get("to") == "STALE", "missing STALE State Event")
    stored = expect(f.call("GET", f"/v1/evidence/{evidence['evidence_id']}"), 200)["evidence"]
    check(stored["evidence_id"] == evidence["evidence_id"], "stale Evidence was deleted")
    outside = f.root / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (f.workspace / "link.txt").symlink_to(outside)
    partial = expect(f.call("POST", "/v1/workspaces/fingerprint", {"project_id": f.project_id,
        "host_id": f.host}, role="system"), 200)
    check(not partial["snapshot"]["complete"], "symlink scan falsely complete")
    check(expect(f.call("POST", "/v1/workspaces/compare", {
        "left_sha256": partial["fingerprint_sha256"],
        "right_sha256": partial["fingerprint_sha256"]}, role="system"), 200)["comparison"] ==
        "UNKNOWN", "partial snapshot falsely SAME")


def case_t08(f: Fixture) -> None:
    task = f.task(criteria=BUILD)
    sid = f.step(task, BUILD)
    f.executed(sid)
    conn = db.connect(f.db_path)
    try:
        before = (f.sql_count("events"), f.sql_count("evidence"))
        conn.execute("CREATE TRIGGER fail_evidence_projection BEFORE INSERT ON evidence "
                     "BEGIN SELECT RAISE(ABORT,'injected projection failure'); END")
        response = f.tool(task, sid, kind="BUILD", tool_use_id="inject-failure")
        check(response[0] == 500, "projection fault did not fail the HTTP operation")
        check((f.sql_count("events"), f.sql_count("evidence")) == before,
              "Event or Evidence survived failed projection")
        conn.execute("DROP TRIGGER fail_evidence_projection")
        event = f.raw_prompt(task_id=task, project_id=f.project_id)
        for statement in (
            "UPDATE events SET payload_json='{}' WHERE event_id=?",
            "DELETE FROM events WHERE event_id=?",
            "INSERT OR REPLACE INTO events SELECT * FROM events WHERE event_id=?",
        ):
            try:
                conn.execute(statement, (event["event_id"],))
            except sqlite3.IntegrityError:
                continue
            raise CheckFailure("Raw Event mutation was accepted")
    finally:
        conn.close()


def case_t09(f: Fixture) -> None:
    task = f.task(criteria=BUILD)
    sid = f.step(task, BUILD)
    other_project = expect(f.call("POST", "/v1/projects", {"name": "other",
        "host_id": f.host}), 201)["object"]["project_id"]
    other_task = f.task(title="other", project_id=other_project)
    before = (f.sql_count("events"), f.sql_count("evidence"))
    expect(f.call("POST", "/v1/tool-results", {"task_id": other_task,
        "step_id": sid, "host_id": f.host, "codex_session_id": "fixture-session",
        "turn_id": "fixture-turn", "tool_use_id": "cross", "tool_name": "exec",
        "tool_input": {"command": "fixture-build"}, "tool_response": {"exit_code": 0},
        "kind": "BUILD"}, role="system"), 400, "invalid_request")
    expect(f.tool(task, sid, kind="BUILD", role="agent"), 403, "forbidden_scope")
    expect(f.call("POST", f"/v1/steps/{sid}/transition", {"status": "VERIFIED",
        "expected_revision": 1, "host_id": f.host}, role="agent"),
        409, "invalid_state_transition")
    check((f.sql_count("events"), f.sql_count("evidence")) == before,
          "rejected inputs changed Truth")
    audit = expect(f.call("GET", "/v1/audit"), 200)["entries"]
    audit_text = json.dumps(audit)
    check(all(token not in audit_text for token in f.tokens.values()),
          "audit leaked a bearer token")
    check(expect(f.call("GET", f"/v1/steps/{sid}"), 200)["step"]["status"] == "PLANNED",
          "negative input changed Step")


CASES: tuple[tuple[str, Callable[[Fixture], None]], ...] = (
    ("P1-T01", case_t01), ("P1-T02", case_t02), ("P1-T03", case_t03),
    ("P1-T04", case_t04), ("P1-T05", case_t05), ("P1-T06", case_t06),
    ("P1-T07", case_t07), ("P1-T08", case_t08), ("P1-T09", case_t09),
)


def provenance() -> dict[str, Any]:
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    return {"commit": commit, "dirty": bool(dirty.strip()), "dirty_paths": dirty.splitlines(),
            "schema_version": SCHEMA_VERSION, "python": platform.python_version()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new directory for JSON result, never database")
    parser.add_argument("--work", required=True, help="new scratch directory for database/workspace")
    parser.add_argument("--allow-dirty", action="store_true", help="development only; cannot count as Gate")
    args = parser.parse_args()
    out, work = Path(args.out).resolve(), Path(args.work).resolve()
    if out.exists() or work.exists() or out == work or out in work.parents or work in out.parents:
        parser.error("--out and --work must be distinct new directories")
    meta = provenance()
    if meta["dirty"] and not args.allow_dirty:
        print("BLOCKED: checkout is dirty; freeze a commit before validation", file=sys.stderr)
        return 2
    out.mkdir(parents=True, mode=0o700)
    work.mkdir(parents=True, mode=0o700)
    records: list[CaseRecord] = []
    for case_id, case in CASES:
        fixture = None
        try:
            fixture = Fixture(work, case_id)
            case(fixture)
            record = CaseRecord(case_id, "PASS", observations=fixture.observations)
        except BaseException as exc:  # preserve the first failure; keep running independent cases
            raw = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            # The fixture contains only generated IDs and harmless fixed text; never
            # include a bearer token in a report even if a library exception does.
            if fixture is not None:
                for token in fixture.tokens.values():
                    raw = raw.replace(token, "[REDACTED]")
            record = CaseRecord(case_id, "FAIL",
                observations=fixture.observations if fixture is not None else [],
                failure_class=type(exc).__name__, failure_output=raw,
                reproduction=[f"{ROOT / 'scripts/p1-acceptance.py'} --out <new-out> --work <new-work>"])
        finally:
            if fixture is not None:
                fixture.close()
        records.append(record)
        print(f"{case_id} {record.verdict}")
    payload = {"provenance": meta, "coverage": {
               "P1-T01..T09": "component_simulation",
               "P1-T06": "simulated_PostTool_payload_parser_and_idempotency_only",
               "real_hook_and_user_conversation": "P1-T10_not_run_here"},
               "verdict": "PASS" if all(item.verdict == "PASS" for item in records) else "FAIL",
               "cases": [record.__dict__ for record in records]}
    result_path = out / f"p1-components-{int(time.time())}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(result_path, 0o600)
    print(json.dumps({"result": str(result_path), "verdict": payload["verdict"],
                      "commit": meta["commit"], "dirty": meta["dirty"]}))
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
