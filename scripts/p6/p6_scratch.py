#!/usr/bin/env python3
"""Build a throwaway Core instance with realistic P1-scope data for the P6 UI.

This is a **local fixture builder**, not a product feature and not evidence of a
passing Gate. It writes everything into a private scratch directory, mints
tokens there with mode 0600, and never touches the operator's Core database,
hooks or accounts.

What it produces, all through the real HTTP API of the real Core build:

* one scratch SQLite database (schema v6) and a scratch workspace root;
* a human, an agent and a system actor on one host;
* a *read-only* token limited to the P6-01 scopes
  (``objects:read``, ``events:read``, ``state:read``, ``authority:read``,
  ``evidence:read``) for the browser;
* two projects, several Tasks and Steps covering the whole Step state
  vocabulary, sessions, raw Events, an ACTIVE and a PROPOSED Rule, PASS/FAIL/
  INFO Evidence and one confirmed Evidence.

Usage::

    python3 scripts/p6/p6_scratch.py build --out /private/tmp/p6-scratch
    python3 scripts/p6/p6_scratch.py serve --out /private/tmp/p6-scratch --port 8789

``build`` prints a JSON descriptor. ``serve`` blocks and serves that database on
loopback so the Vite dev proxy can reach it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core import SCHEMA_VERSION, auth, db, ids, registry  # noqa: E402
from jasmine_core.api.server import Application, _Handler  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402

READ_SCOPES = ["objects:read", "events:read", "state:read", "authority:read", "evidence:read"]
WRITE_SCOPES = READ_SCOPES + [
    "objects:write", "events:write", "state:write", "state:accept",
    "authority:propose", "authority:manage", "evidence:confirm",
    "guard:check", "fingerprint:read",
]
SYSTEM_SCOPES = ["evidence:write", "evidence:read", "fingerprint:scan", "fingerprint:read"]


class Scratch:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.workspace = out / "workspace"
        self.db_path = out / "core.db"
        self.producer_file = out / "evidence-producers.json"
        self.host = ids.new_id("hst")
        self.reader = ids.new_id("act")
        self.human = ids.new_id("act")
        self.agent = ids.new_id("act")
        self.system = ids.new_id("act")
        self.tokens: dict[str, str] = {}
        self.port = 0
        self.app: Application | None = None
        self.server = None
        self.thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def build(self) -> dict[str, Any]:
        from http.server import ThreadingHTTPServer

        self.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._git_workspace()
        producers = [
            {"kind": kind, "tool_name": "exec", "command_sha256": _sha_command(command)}
            for kind, command in (("BUILD", "scratch-build"), ("TEST", "scratch-test"))
        ]
        self.producer_file.write_text(json.dumps(producers), encoding="utf-8")
        os.chmod(self.producer_file, 0o600)
        os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(self.workspace)
        os.environ["JASMINE_CORE_EVIDENCE_PRODUCERS"] = str(self.producer_file)

        conn = db.connect(self.db_path)
        migrate(conn)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_host(self.host, display_name="p6-scratch-host")
            for ident, kind, name in (
                (self.reader, "human", "p6 read-only viewer"),
                (self.human, "human", "p6 fixture operator"),
                (self.agent, "agent", "p6 fixture agent"),
                (self.system, "system", "p6 fixture runner"),
            ):
                reg.upsert_actor(ident, kind=kind, display_name=name, home_host_id=self.host)
        issuer = auth.Auth(conn)
        self.tokens = {
            "read": issuer.issue_key(actor_id=self.reader, label="p6-ui-readonly",
                                     scopes=READ_SCOPES)["token"],
            "human": issuer.issue_key(actor_id=self.human, label="p6-fixture-human",
                                      scopes=WRITE_SCOPES)["token"],
            "agent": issuer.issue_key(actor_id=self.agent, label="p6-fixture-agent",
                                      scopes=["objects:read", "objects:write", "events:read",
                                              "events:write", "state:read", "state:write",
                                              "authority:read", "authority:propose",
                                              "evidence:read", "guard:check"])["token"],
            "system": issuer.issue_key(actor_id=self.system, label="p6-fixture-system",
                                       scopes=SYSTEM_SCOPES)["token"],
        }
        conn.close()

        self.port = _free_port()
        self.app = Application(self.db_path, log=lambda _line: None)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = self.app
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        try:
            seeded = self.seed()
        finally:
            self.stop()
        token_file = self.out / "tokens.json"
        token_file.write_text(json.dumps(self.tokens, indent=2), encoding="utf-8")
        os.chmod(token_file, 0o600)
        descriptor = {
            "scratch_root": str(self.out),
            "db": str(self.db_path),
            "workspace_root": str(self.workspace),
            "host_id": self.host,
            "schema_version": SCHEMA_VERSION,
            "actors": {"read": self.reader, "human": self.human, "agent": self.agent,
                       "system": self.system},
            "token_file": str(token_file),
            "read_only_token_scopes": READ_SCOPES,
            "seeded": seeded,
        }
        (self.out / "descriptor.json").write_text(json.dumps(descriptor, indent=2),
                                                  encoding="utf-8")
        return descriptor

    def serve(self, port: int) -> None:
        from jasmine_core.api.server import serve

        os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(self.workspace)
        os.environ["JASMINE_CORE_EVIDENCE_PRODUCERS"] = str(self.producer_file)
        serve(self.db_path, host="127.0.0.1", port=port,
              log=lambda line: print(f"jasmine-core: {line}", file=sys.stderr, flush=True))

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.app is not None:
            self.app.close()

    def _git_workspace(self) -> None:
        if (self.workspace / ".git").exists():
            return
        for command in (["git", "init", "-q", str(self.workspace)],
                        ["git", "-C", str(self.workspace), "-c", "user.name=P6 Scratch",
                         "-c", "user.email=p6-scratch@local.invalid", "commit", "-q",
                         "--allow-empty", "-m", "initial"]):
            subprocess.run(command, check=True, capture_output=True, timeout=20)

    # -- HTTP --------------------------------------------------------------

    def call(self, method: str, path: str, body: dict | None = None, *,
             role: str = "human") -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.tokens[role]},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def expect(self, method: str, path: str, body: dict | None, status: int,
               *, role: str = "human") -> dict:
        actual, payload = self.call(method, path, body, role=role)
        if actual != status:
            raise SystemExit(f"{method} {path} -> {actual} {payload} (wanted {status})")
        return payload

    # -- the scenario ------------------------------------------------------

    def seed(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"projects": [], "rules": [], "evidence": []}
        alpha = self.expect("POST", "/v1/projects",
                            {"name": "Jasmine Core V1", "host_id": self.host}, 201)["object"]
        beta = self.expect("POST", "/v1/projects",
                           {"name": "Hermes device app", "host_id": self.host}, 201)["object"]
        summary["projects"] = [alpha["project_id"], beta["project_id"]]

        session = self.expect("POST", "/v1/sessions", {
            "title": "P6 scratch conversation", "project_id": alpha["project_id"],
            "host_id": self.host}, 201)["object"]

        primary = self.expect("POST", "/v1/tasks", {
            "title": "Wire the read-only dashboard to the frozen API",
            "description": "Browser only reads; Core stays the single source of truth.",
            "project_id": alpha["project_id"], "host_id": self.host}, 201)["object"]
        blocked_task = self.expect("POST", "/v1/tasks", {
            "title": "Await the P2 Interpretation contract",
            "project_id": alpha["project_id"], "host_id": self.host}, 201)["object"]
        other = self.expect("POST", "/v1/tasks", {
            "title": "Unrelated task in the second project",
            "project_id": beta["project_id"], "host_id": self.host}, 201)["object"]
        summary["tasks"] = [primary["task_id"], blocked_task["task_id"], other["task_id"]]

        criteria = {"requirements": [
            {"key": "build", "kind": "BUILD", "required_result": "PASS"},
            {"key": "test", "kind": "TEST", "required_result": "PASS"}]}

        # A Step that reaches ACCEPTED through real PASS Evidence.
        accepted = self.expect("POST", f"/v1/tasks/{primary['task_id']}/steps", {
            "title": "Render the Task board from GET /v1/tasks/{id}/steps",
            "host_id": self.host, "expected_revision": 1,
            "acceptance_criteria": criteria}, 201)["step"]
        self._transition(accepted["step_id"], "IN_PROGRESS", 1)
        self._transition(accepted["step_id"], "EXECUTED", 2)
        build = self.expect("POST", "/v1/tool-results", {
            "task_id": primary["task_id"], "step_id": accepted["step_id"], "host_id": self.host,
            "codex_session_id": "scratch-session", "turn_id": "turn-1",
            "tool_use_id": ids.new_id("evt"), "tool_name": "exec", "kind": "BUILD",
            "tool_input": {"command": "scratch-build"},
            "tool_response": {"exit_code": 0}}, 201, role="system")
        test = self.expect("POST", "/v1/tool-results", {
            "task_id": primary["task_id"], "step_id": accepted["step_id"], "host_id": self.host,
            "codex_session_id": "scratch-session", "turn_id": "turn-2",
            "tool_use_id": ids.new_id("evt"), "tool_name": "exec", "kind": "TEST",
            "tool_input": {"command": "scratch-test"},
            "tool_response": {"exit_code": 0}}, 201, role="system")
        summary["evidence"] += [build["evidence"]["evidence_id"], test["evidence"]["evidence_id"]]
        self._transition(accepted["step_id"], "VERIFIED", 3, role="human")

        # A Step that is still EXECUTED, and one that FAILED.
        running = self.expect("POST", f"/v1/tasks/{primary['task_id']}/steps", {
            "title": "Add Rule and Evidence detail views", "host_id": self.host,
            "expected_revision": self._task_revision(primary["task_id"])}, 201)["step"]
        self._transition(running["step_id"], "IN_PROGRESS", 1)
        self._transition(running["step_id"], "EXECUTED", 2)
        failing = self.expect("POST", f"/v1/tasks/{primary['task_id']}/steps", {
            "title": "Flaky acceptance probe", "host_id": self.host,
            "expected_revision": self._task_revision(primary["task_id"])}, 201)["step"]
        self._transition(failing["step_id"], "IN_PROGRESS", 1)
        failed = self.expect("POST", "/v1/tool-results", {
            "task_id": primary["task_id"], "step_id": failing["step_id"], "host_id": self.host,
            "codex_session_id": "scratch-session", "turn_id": "turn-3",
            "tool_use_id": ids.new_id("evt"), "tool_name": "exec", "kind": "TEST",
            "tool_input": {"command": "scratch-test"},
            "tool_response": {"exit_code": 1}}, 201, role="system")
        summary["evidence"].append(failed["evidence"]["evidence_id"])
        self._transition(failing["step_id"], "FAILED", 2, reason="scratch-test exited 1")

        # A PLANNED Step and a BLOCKED Task.
        planned = self.expect("POST", f"/v1/tasks/{primary['task_id']}/steps", {
            "title": "Keyboard source-trace walkthrough", "host_id": self.host,
            "expected_revision": self._task_revision(primary["task_id"])}, 201)["step"]
        self.expect("POST", f"/v1/tasks/{blocked_task['task_id']}/transition", {
            "status": "BLOCKED", "reason": "P2 API not frozen", "host_id": self.host,
            "expected_revision": 1}, 200)
        summary["steps"] = {"accepted": accepted["step_id"], "executed": running["step_id"],
                            "failed": failing["step_id"], "planned": planned["step_id"]}

        # Raw conversation: a human prompt, an agent reply and a tool call Event.
        prompt = self.expect("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "p6-scratch",
            "session_id": session["session_id"], "project_id": alpha["project_id"],
            "task_id": primary["task_id"], "host_id": self.host,
            "payload": {"text": "Show me the dashboard for the accepted Step.",
                        "step_id": accepted["step_id"]}}, 201)["event"]
        self.expect("POST", "/v1/events", {
            "event_type": "assistant.message", "source_system": "p6-scratch",
            "session_id": session["session_id"], "project_id": alpha["project_id"],
            "task_id": primary["task_id"], "host_id": self.host,
            "payload": {"text": "Reading the Step list from Core."}}, 201)
        self.expect("POST", "/v1/events", {
            "event_type": "tool.call", "source_system": "p6-scratch",
            "session_id": session["session_id"], "project_id": alpha["project_id"],
            "task_id": primary["task_id"], "host_id": self.host,
            "payload": {"text": "exec scratch-test", "tool_name": "exec"}}, 201)
        self.expect("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "p6-scratch",
            "project_id": beta["project_id"], "task_id": other["task_id"],
            "host_id": self.host,
            "payload": {"text": "This prompt belongs to the other project."}}, 201)

        # Authority: one ACTIVE Rule, one PROPOSED Rule. A project-scoped origin
        # Event must be project-only, and a global one must name neither.
        project_origin = self.expect("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "p6-scratch",
            "project_id": alpha["project_id"], "host_id": self.host,
            "payload": {"text": "Project-level instruction for the scratch Rule."}}, 201)["event"]
        global_origin = self.expect("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "p6-scratch",
            "host_id": self.host,
            "payload": {"text": "Global tone preference."}}, 201)["event"]
        active_rule = self.expect("POST", "/v1/rules/proposals", {
            "rule_key": "p6-scratch-deny-secrets", "kind": "RULE", "severity": "HARD",
            "enforcement": "DENY", "content": "Never write captured tokens to disk.",
            "matcher": {"action": "write", "path_prefix": "/tmp/forbidden"},
            "scope": {"kind": "project", "project_id": alpha["project_id"]},
            "origin_event_id": project_origin["event_id"], "host_id": self.host}, 201)["rule"]
        self.expect("POST", f"/v1/rules/{active_rule['rule_id']}/approve", {
            "host_id": self.host, "expected_revision": 1}, 200)
        proposed = self.expect("POST", "/v1/rules/proposals", {
            "rule_key": "p6-scratch-context-tone", "kind": "RULE", "severity": "NORMAL",
            "enforcement": "CONTEXT", "content": "Prefer short answers in Chinese and English.",
            "matcher": {}, "scope": {"kind": "global"},
            "origin_event_id": global_origin["event_id"], "host_id": self.host}, 201,
            role="agent")["rule"]
        summary["rules"] = {"active": active_rule["rule_id"], "proposed": proposed["rule_id"]}

        # A confirmed Evidence needs a genuine same-task human prompt after the
        # current State Event; the prompt above is earlier, so use a second one.
        later = self.expect("POST", "/v1/events", {
            "event_type": "user.prompt", "source_system": "p6-scratch",
            "session_id": session["session_id"], "project_id": alpha["project_id"],
            "task_id": primary["task_id"], "host_id": self.host,
            "payload": {"text": "I confirm the keyboard walkthrough Step.",
                        "step_id": planned["step_id"]}}, 201)["event"]
        step = self.expect("GET", f"/v1/steps/{planned['step_id']}", None, 200)["step"]
        confirmed = self.expect("POST", "/v1/evidence/confirm", {
            "task_id": primary["task_id"], "step_id": planned["step_id"], "host_id": self.host,
            "origin_event_id": later["event_id"],
            "expected_revision": step["revision"]}, 201)
        summary["evidence"].append(confirmed["evidence"]["evidence_id"])
        return summary

    def _task_revision(self, task_id: str) -> int:
        return int(self.expect("GET", f"/v1/tasks/{task_id}", None, 200)["task"]["revision"])

    def _transition(self, step_id: str, status: str, revision: int,
                    *, reason: str | None = None, role: str = "agent") -> dict:
        body: dict[str, Any] = {"status": status, "expected_revision": revision,
                                "host_id": self.host}
        if reason is not None:
            body["reason"] = reason
        return self.expect("POST", f"/v1/steps/{step_id}/transition", body, 200, role=role)


def _sha_command(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p6_scratch")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="create a fresh scratch Core and seed it")
    build.add_argument("--out", required=True)
    build.add_argument("--force", action="store_true",
                       help="delete an existing scratch database first")
    serve = sub.add_parser("serve", help="serve an existing scratch database")
    serve.add_argument("--out", required=True)
    serve.add_argument("--port", type=int, default=8789)
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    if args.command == "build":
        if args.force and out.exists():
            import shutil

            shutil.rmtree(out)
        scratch = Scratch(out)
        print(json.dumps(scratch.build(), indent=2, ensure_ascii=False))
        return 0
    scratch = Scratch(out)
    if not scratch.db_path.exists():
        print(f"no scratch database at {scratch.db_path}; run build first", file=sys.stderr)
        return 2
    scratch.serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
