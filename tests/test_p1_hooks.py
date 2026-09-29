"""Synthetic P1 hook and installer component tests; never a real Gate claim."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jasmine_core import ids
from jasmine_core.capture import p1_codex_hook as hook


class MockClient:
    calls: list[tuple[str, dict]] = []
    decision = "allow"
    broken = False
    project_id = ""
    task_id = ""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def post(self, path: str, body: dict):
        self.calls.append((path, body))
        if self.broken:
            raise OSError("Core unavailable")
        if path == "/v1/guard/check":
            return {"decision": self.decision}
        if path == "/v1/tool-results":
            return {"result_event": {"event_id": ids.new_id("evt")}}
        if path == "/v1/events":
            return {"event": {"event_id": body["event_id"]}}
        return {}

    def get(self, path: str):
        self.calls.append((path, {}))
        if path.startswith("/v1/tasks/"):
            return {"task": {"task_id": self.task_id, "project_id": self.project_id,
                             "status": "ACTIVE", "revision": 2}}
        if path.startswith("/v1/steps/"):
            return {"step": {"step_id": "stp_fixture", "task_id": self.task_id,
                             "status": "PLANNED", "revision": 1}}
        if path.startswith("/v1/rules/active"):
            return {"rules": []}
        raise AssertionError(path)


class P1Hooks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="p1-hook-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.nonce = "n" * 40
        self.binding = self.root / "binding.json"
        self.system_token = self.root / "system.token"
        self.human_token = self.root / "human.token"
        self.system_token.write_text("system-test-token\n")
        self.human_token.write_text("human-test-token\n")
        self.system_token.chmod(0o600)
        self.human_token.chmod(0o600)
        self.config = {"run_nonce": self.nonce, "project_id": ids.new_id("prj"),
            "task_id": ids.new_id("tsk"), "step_id": ids.new_id("stp"),
            "host_id": ids.new_id("hst"), "core_url": "http://127.0.0.1:8787",
            "token_file": str(self.system_token), "human_token_file": str(self.human_token),
            "trace_file": str(self.root / "trace.jsonl")}
        self.binding.write_text(json.dumps(self.config))
        self.binding.chmod(0o600)
        self.env = patch.dict(os.environ, {"JASMINE_CORE_GATE_NONCE": self.nonce})
        self.env.start(); self.addCleanup(self.env.stop)
        self.client = patch.object(hook, "CoreClient", MockClient)
        self.client.start(); self.addCleanup(self.client.stop)
        MockClient.calls = []
        MockClient.decision = "allow"
        MockClient.broken = False
        MockClient.project_id = self.config["project_id"]
        MockClient.task_id = self.config["task_id"]
        self.session = "real-codex-session"

    def prompt(self):
        return {"hook_event_name": "UserPromptSubmit", "session_id": self.session,
                "turn_id": "turn-1", "prompt": "explicit fixture approval"}

    def pre(self, command="pwd"):
        return {"hook_event_name": "PreToolUse", "session_id": self.session,
                "turn_id": "turn-1", "tool_use_id": "use-1", "tool_name": "Bash",
                "tool_input": {"command": command}}

    def test_first_real_prompt_claims_lease_and_wrong_session_is_unbound(self) -> None:
        self.assertEqual(hook.handle(self.pre(), self.binding)["hookSpecificOutput"]
                         ["permissionDecision"], "deny")
        context = hook.handle(self.prompt(), self.binding)
        self.assertEqual(context["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertTrue((self.root / "binding.json.lease").is_file())
        event = next(body for path, body in MockClient.calls if path == "/v1/events")
        self.assertEqual((event["project_id"], event["task_id"]),
                         (self.config["project_id"], self.config["task_id"]))
        self.assertEqual(event["payload"]["text"], "explicit fixture approval")
        another = {**self.pre(), "session_id": "another-session"}
        count = len(MockClient.calls)
        self.assertEqual(hook.handle(another, self.binding)["hookSpecificOutput"]
                         ["permissionDecision"], "deny")
        self.assertEqual(len(MockClient.calls), count)

    def test_unbound_process_does_not_apply_scratch_task(self) -> None:
        with patch.dict(os.environ, {"JASMINE_CORE_GATE_NONCE": "different"}):
            self.assertEqual(hook.handle(self.pre(), self.binding), {})
        self.assertEqual(MockClient.calls, [])

    def test_pre_deny_unknown_and_core_outage_fail_closed(self) -> None:
        hook.handle(self.prompt(), self.binding)
        MockClient.decision = "deny"
        denied = hook.handle(self.pre(), self.binding)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(denied["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(MockClient.calls[-1][0], "/v1/guard/check")
        self.assertEqual(hook.handle(self.pre("touch /tmp/a; true"), self.binding)
                         ["hookSpecificOutput"]["permissionDecision"], "deny")
        target = self.root / "real"
        target.mkdir()
        (self.root / "link").symlink_to(target, target_is_directory=True)
        self.assertEqual(hook.handle(self.pre(f"touch {self.root.resolve()}/link/file"), self.binding)
                         ["hookSpecificOutput"]["permissionDecision"], "deny")
        MockClient.broken = True
        self.assertEqual(hook.handle(self.pre("pwd"), self.binding)
                         ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_verify_allows_execution_but_post_captures_real_result(self) -> None:
        hook.handle(self.prompt(), self.binding)
        MockClient.decision = "verify"
        self.assertEqual(hook.handle(self.pre("pwd"), self.binding), {})
        post = {**self.pre("pwd"), "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 0, "output": "/tmp\n"}}
        self.assertEqual(hook.handle(post, self.binding), {})
        self.assertEqual([path for path, _ in MockClient.calls[-2:]],
                         ["/v1/tool-results", "/v1/workspaces/fingerprint"])
        self.assertTrue((self.root / "trace.jsonl").is_file())

    def test_installer_preserves_p0_and_dry_run_is_read_only(self) -> None:
        repo = self.root / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / ".codex").mkdir()
        source = Path(__file__).resolve().parents[1] / "scripts" / "jasmine-p1-hook.sh"
        shutil.copy2(source, repo / "scripts" / "jasmine-p1-hook.sh")
        target = repo / ".codex" / "hooks.json"
        p0 = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
            "command": "P0 jasmine-capture-hook.sh"}]}]}}
        target.write_text(json.dumps(p0) + "\n")
        installer = Path(__file__).resolve().parents[1] / "scripts" / "p1-codex-hook-install.py"
        args = ["/opt/homebrew/bin/python3.13", str(installer), "--binding", str(self.binding),
                "--project-root", str(repo), "--python", "/opt/homebrew/bin/python3.13"]
        before = target.read_bytes()
        dry = subprocess.run([*args, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(target.read_bytes(), before)
        write = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(write.returncode, 0, write.stderr)
        changed = json.loads(target.read_text())
        self.assertEqual(changed["hooks"]["UserPromptSubmit"][0], p0["hooks"]["UserPromptSubmit"][0])
        self.assertIn("PreToolUse", changed["hooks"])
        again = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(json.loads(again.stdout)["result"], "unchanged")
        self.assertEqual(len(list(target.parent.glob("hooks.json.bak.*"))), 1)
