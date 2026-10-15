"""Claude Code dialect through the hook runtime and the real CLI process, against a real Core.

Layers: (1) component: `runtime.run_hook("claude-code", ...)` in-process; (2) local integration:
`python -m jasmine_core.cli hook --agent claude-code` as a subprocess on stdin/stdout, against a
Core HTTP server on a scratch DB. Native payloads are constructed from docs. No Claude Code
process, hook trust, or model turn is involved: nothing here shows native effect.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hosthook_support import HostHookHttp
from test_api import HOST, free_port
from test_hosthook_dialect_claude_code import fixture

from jasmine_core.hosthook import runtime
from jasmine_core.hosthook.spool import Spool

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)


class Base(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.proj = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-cc-")
        self.addCleanup(self.proj.cleanup)
        self.dir = Path(self.proj.name).resolve()
        (self.dir / "private").mkdir(mode=0o700)
        for name, token in (("system.key", self.system_token), ("human.key", self.human_hook_token)):
            path = self.dir / "private" / name
            path.write_text(token)
            path.chmod(0o600)
        (self.dir / ".jasmine").mkdir()
        self.binding_path = self.dir / ".jasmine" / "host-binding.json"
        self.write_binding()

    def write_binding(self, **changes) -> None:
        data = {"version": 1, "core_url": f"http://127.0.0.1:{self.port}", "host_id": HOST,
                "project_id": self.project, "task_id": self.task, "step_id": self.step,
                "api_key_file": str(self.dir / "private" / "system.key"),
                "human_api_key_file": str(self.dir / "private" / "human.key")}
        data.update(changes)
        self.binding_path.write_text(json.dumps({k: v for k, v in data.items() if v is not ...}))

    def native(self, name: str, **overrides) -> dict:
        return {**fixture(name), "cwd": str(self.dir), **overrides}


class RuntimeComponent(Base):
    def hook(self, name: str, **overrides) -> tuple[str, int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = runtime.run_hook("claude-code", json.dumps(self.native(name, **overrides)).encode(),
                                stdout=out, stderr=err, environ={}, now=lambda: NOW)
        return out.getvalue(), code, err.getvalue()

    def test_prompt_verbatim_and_context_in_claude_code_shape(self) -> None:
        out, code, err = self.hook("user_prompt_submit")
        self.assertEqual((code, err), (0, ""))
        body = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(body["hookEventName"], "UserPromptSubmit")
        self.assertIn("Hook goal", body["additionalContext"])
        row = self.db_rows("SELECT * FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")[0]
        self.assertEqual(row["actor_kind"], "human")
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["text"], fixture("user_prompt_submit")["prompt"])
        self.assertEqual(payload["native"]["turn_id"], fixture("user_prompt_submit")["prompt_id"])
        self.assertEqual(payload["agent"]["name"], "claude-code")

    def test_session_start_after_compaction_gets_a_pack(self) -> None:
        out, _, _ = self.hook("session_start_compact")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(self.db_rows("SELECT reason FROM context_packs")[0]["reason"], "SESSION_START")

    def test_deny_ask_and_allow_reach_the_native_output(self) -> None:
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        out, code, _ = self.hook("pre_tool_use_bash")
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((decision["permissionDecision"], code), ("deny", 0))
        self.assertIn("rul_", decision["permissionDecisionReason"])
        out, _, _ = self.hook("pre_tool_use_bash_compound")  # unresolved -> Guard confirm -> ask, kept as ask
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        out, code, err = self.hook("pre_tool_use_bash", tool_input={"command": "ls /elsewhere"}, tool_use_id="toolu_other")
        self.assertEqual((out, code), ("", 0), out + err)

    def test_native_file_tools_are_guarded_by_their_native_names(self) -> None:
        self.rule("no-write", "DENY", {"tool": "Write", "path_prefix": "/work/project/src"})
        out, _, _ = self.hook("pre_tool_use_write")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        # a rule that names only Bash does not constrain Write (ADR 0017 section 15, question 2)
        out, _, _ = self.hook("pre_tool_use_edit")
        self.assertNotEqual(out and json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_post_tool_use_becomes_evidence_with_trust(self) -> None:
        out, code, err = self.hook("post_tool_use_bash")
        self.assertEqual((out, code, err), ("", 0, ""))
        row = self.db_rows("SELECT e.status, e.tool_name, e.kind, v.payload_json FROM evidence e "
                           "JOIN events v ON v.event_id=e.source_event_id")[0]
        # no documented integer exit status in the constructed Claude Code response: INFO, never PASS
        self.assertEqual((row["status"], row["tool_name"], row["kind"]), ("INFO", "Bash", "COMMAND_RESULT"))
        self.assertEqual(json.loads(row["payload_json"])["host_hook"]["trust"], "NATIVE_HOOK")
        self.hook("post_tool_use_bash_with_exit_code")
        self.hook("post_tool_use_bash_failed")
        self.assertEqual(sorted(r["status"] for r in self.db_rows("SELECT status FROM evidence")), ["FAIL", "INFO", "PASS"])

    def test_pre_compact_and_stop_make_checkpoints(self) -> None:
        self.assertEqual(self.hook("pre_compact")[:2], ("", 0))
        self.assertEqual(self.hook("stop")[:2], ("", 0))
        self.assertEqual(sorted(r["reason"] for r in self.db_rows("SELECT reason FROM checkpoints")),
                         ["PRE_COMPACT", "SESSION_STOP"])
        self.assertEqual(self.hook("session_end")[:2], ("", 0))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='session.ended'")[0]["n"], 1)

    def test_replayed_hook_run_with_prompt_id_does_not_duplicate(self) -> None:
        self.hook("user_prompt_submit")
        self.hook("user_prompt_submit")
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='user.prompt' "
                                      "AND source_system='host-hook-v1'")[0]["n"], 1)

    def test_core_unreachable_is_fail_closed_with_a_gap_and_spools_captures(self) -> None:
        self.write_binding(core_url=f"http://127.0.0.1:{free_port()}")
        out, code, err = self.hook("pre_tool_use_bash")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("was not checked", err)
        out, code, _ = self.hook("post_tool_use_bash")
        self.assertEqual((out, code), ("", 0))
        spool = Spool(self.binding_path.parent / "spool")
        self.assertEqual(sorted(spool.read(p)["kind"] for p in spool.entries()).count("unchecked_gap"), 1)
        self.write_binding(core_url=f"http://127.0.0.1:{free_port()}", on_unavailable="allow")
        self.assertEqual(self.hook("pre_tool_use_bash")[:2], ("", 0))


class CliProcess(Base):
    def run_hook(self, name: str, **overrides) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        return subprocess.run([sys.executable, "-m", "jasmine_core.cli", "hook", "--agent", "claude-code"],
                              input=json.dumps(self.native(name, **overrides)), text=True, capture_output=True,
                              env=env, cwd=str(self.dir), timeout=60)

    def test_five_goals_through_the_real_cli_process(self) -> None:
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        done = self.run_hook("user_prompt_submit")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("Hook goal", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        prompt = self.db_rows("SELECT payload_json FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")
        self.assertEqual(json.loads(prompt[0]["payload_json"])["text"], fixture("user_prompt_submit")["prompt"])
        done = self.run_hook("pre_tool_use_bash")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        done = self.run_hook("post_tool_use_bash_with_exit_code")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        evidence = self.db_rows("SELECT e.status, v.payload_json FROM evidence e JOIN events v ON v.event_id=e.source_event_id")
        self.assertEqual(evidence[0]["status"], "PASS")
        self.assertEqual(json.loads(evidence[0]["payload_json"])["host_hook"]["trust"], "NATIVE_HOOK")
        self.assertEqual(self.run_hook("pre_compact").returncode, 0)
        done = self.run_hook("stop")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual(sorted(r["reason"] for r in self.db_rows("SELECT reason FROM checkpoints")),
                         ["PRE_COMPACT", "SESSION_STOP"])

    def test_no_binding_means_empty_output_and_no_error(self) -> None:
        self.binding_path.unlink()
        done = self.run_hook("pre_tool_use_bash")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))


if __name__ == "__main__":
    import unittest
    unittest.main()
