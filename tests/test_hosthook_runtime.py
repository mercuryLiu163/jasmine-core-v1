"""Hook runtime (binding, transport, spool, replay) against a real HTTP Core.

In-process calls of `runtime.run_hook`; native fixtures are constructed from
docs. No real agent is involved.
"""

from __future__ import annotations

import io
import json
import os
import socket
import stat
from datetime import datetime, timezone
from pathlib import Path

from hosthook_support import HostHookHttp
from test_api import free_port
from test_hosthook_dialect_codex import fixture

from jasmine_core.hosthook import runtime
from jasmine_core.hosthook.binding import BindingError, find_binding, load_binding
from jasmine_core.hosthook.spool import Spool

NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)


class RuntimeBase(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        import tempfile
        self._project = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-project-")
        self.addCleanup(self._project.cleanup)
        self.project_dir = Path(self._project.name).resolve()
        self.private = self.project_dir / "private"
        self.private.mkdir(mode=0o700)
        self.system_key = self.write_key("system.key", self.system_token)
        self.human_key = self.write_key("human.key", self.human_hook_token)
        (self.project_dir / ".jasmine").mkdir()
        self.binding_path = self.project_dir / ".jasmine" / "host-binding.json"
        self.write_binding()

    def write_key(self, name: str, token: str) -> Path:
        path = self.private / name
        path.write_text(token + "\n")
        path.chmod(0o600)
        return path

    def write_binding(self, **changes) -> dict:
        from test_api import HOST
        data = {"version": 1, "core_url": f"http://127.0.0.1:{self.port}", "host_id": HOST,
                "project_id": self.project, "task_id": self.task, "step_id": self.step,
                "api_key_file": str(self.system_key), "human_api_key_file": str(self.human_key),
                "on_unavailable": "deny"}
        data.update(changes)
        data = {k: v for k, v in data.items() if v is not ...}
        self.binding_path.write_text(json.dumps(data))
        return data

    def hook(self, name: str, *, now=NOW, **overrides) -> tuple[str, int, str]:
        native = fixture(name)
        native["cwd"] = str(self.project_dir)
        native.update(overrides)
        out, err = io.StringIO(), io.StringIO()
        code = runtime.run_hook("codex", json.dumps(native).encode(), stdout=out, stderr=err,
                                environ={}, now=lambda: now)
        return out.getvalue(), code, err.getvalue()

    def dead_port(self) -> int:
        return free_port()


class Flow(RuntimeBase):
    def test_prompt_is_stored_and_context_comes_back_in_the_codex_shape(self) -> None:
        out, code, err = self.hook("user_prompt_submit")
        self.assertEqual((code, err), (0, ""))
        body = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(body["hookEventName"], "UserPromptSubmit")
        self.assertIn("Hook goal", body["additionalContext"])
        row = self.db_rows("SELECT * FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")[0]
        self.assertEqual(row["actor_kind"], "human")  # human key used for prompts
        self.assertEqual(json.loads(row["payload_json"])["text"], fixture("user_prompt_submit")["prompt"])

    def test_deny_ask_and_allow_reach_the_native_output(self) -> None:
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        out, code, _ = self.hook("pre_tool_use_bash")
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((decision["permissionDecision"], code), ("deny", 0))
        self.assertIn("rul_", decision["permissionDecisionReason"])
        out, _, _ = self.hook("pre_tool_use_bash_compound")  # unresolved -> ask -> degraded to deny
        self.assertIn("cannot ask", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])
        allowed = fixture("pre_tool_use_bash")
        # a distinct call id: the fixture's id with other input would be a delivery conflict (409)
        out, code, err = self.hook("pre_tool_use_bash", tool_input={"command": "ls /elsewhere"},
                                   tool_use_id="call_other")
        self.assertEqual((out, code), ("", 0), out + err)

    def test_post_tool_use_becomes_evidence_with_trust(self) -> None:
        out, code, err = self.hook("post_tool_use_bash")
        self.assertEqual((out, code, err), ("", 0, ""))
        row = self.db_rows("SELECT status, tool_name, kind FROM evidence")[0]
        self.assertEqual((row["status"], row["tool_name"], row["kind"]), ("PASS", "Bash", "COMMAND_RESULT"))
        self.hook("post_tool_use_bash_failed")
        self.assertEqual(sorted(r["status"] for r in self.db_rows("SELECT status FROM evidence")), ["FAIL", "PASS"])

    def test_session_start_stop_and_compact(self) -> None:
        out, _, _ = self.hook("session_start")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(self.hook("stop")[:2], ("{}", 0))
        self.assertEqual(self.hook("pre_compact")[:2], ("", 0))
        self.assertEqual(self.hook("post_compact")[:2], ("", 0))
        reasons = sorted(r["reason"] for r in self.db_rows("SELECT reason FROM checkpoints"))
        self.assertEqual(reasons, ["PRE_COMPACT", "SESSION_STOP"])
        self.assertEqual([r["reason"] for r in self.db_rows("SELECT reason FROM context_packs")], ["SESSION_START"])

    def test_same_native_event_twice_is_one_record(self) -> None:
        self.hook("post_tool_use_bash")
        self.hook("post_tool_use_bash")
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM host_hook_deliveries")[0]["n"], 1)

    def test_no_binding_means_empty_output_and_no_error(self) -> None:
        self.binding_path.unlink()
        for name, expected in (("user_prompt_submit", ""), ("pre_tool_use_bash", ""), ("stop", "{}")):
            self.assertEqual(self.hook(name), (expected, 0, ""))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM host_hook_deliveries")[0]["n"], 0)

    def test_unknown_agent_and_garbage_stdin(self) -> None:
        err = io.StringIO()
        self.assertEqual(runtime.run_hook("nobody", b"{}", stdout=io.StringIO(), stderr=err, environ={}), 1)
        self.assertIn("unknown agent", err.getvalue())
        out = io.StringIO()
        self.assertEqual(runtime.run_hook("codex", b"not json", stdout=out, stderr=io.StringIO(), environ={}), 0)
        self.assertEqual(out.getvalue(), "")


class CoreUnavailable(RuntimeBase):
    def setUp(self) -> None:
        super().setUp()
        self.live_binding = self.write_binding()
        self.write_binding(core_url=f"http://127.0.0.1:{self.dead_port()}")
        self.spool = Spool(self.binding_path.parent / "spool")

    def test_capture_events_spool_with_private_modes_and_do_not_block(self) -> None:
        out, code, err = self.hook("post_tool_use_bash")
        self.assertEqual((out, code), ("", 0))
        self.assertIn("spooled for replay", err)
        entries = self.spool.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(stat.S_IMODE(self.spool.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(entries[0].stat().st_mode), 0o600)
        out, code, _ = self.hook("stop")
        self.assertEqual((out, code), ("{}", 0))
        out, code, _ = self.hook("user_prompt_submit")
        self.assertEqual((out, code), ("", 0))
        self.assertEqual(len(self.spool.entries()), 3)

    def test_tool_before_is_fail_closed_by_default_and_leaves_a_gap_record(self) -> None:
        self.write_binding(core_url=f"http://127.0.0.1:{self.dead_port()}", on_unavailable=...)  # key absent
        self.assertNotIn("on_unavailable", self.binding_path.read_text())
        out, code, err = self.hook("pre_tool_use_bash")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("was not checked", err)
        gap = self.spool.read(self.spool.entries()[0])
        self.assertEqual(gap["kind"], "unchecked_gap")
        self.assertNotIn("rm /protected", json.dumps(gap))  # hashes only
        self.assertEqual(len(gap["input_sha256"]), 64)

    def test_on_unavailable_allow_is_an_explicit_opt_in_and_still_records_the_gap(self) -> None:
        self.write_binding(core_url=f"http://127.0.0.1:{self.dead_port()}", on_unavailable="allow")
        out, code, err = self.hook("pre_tool_use_bash")
        self.assertEqual((out, code), ("", 0))
        self.assertIn("was not checked", err)
        self.assertEqual(self.spool.read(self.spool.entries()[0])["kind"], "unchecked_gap")

    def test_unusable_binding_defaults_to_deny(self) -> None:
        self.binding_path.write_text("{not json")
        out, _, _ = self.hook("pre_tool_use_bash")
        self.assertIn("deny", out)

    def test_replay_is_idempotent_and_ordered(self) -> None:
        self.hook("user_prompt_submit")
        self.hook("post_tool_use_bash")
        self.hook("pre_tool_use_bash")  # gap, never sent
        self.write_binding()  # Core is reachable again
        out = io.StringIO()
        self.assertEqual(runtime.run_replay(str(self.binding_path), None, None, environ={}, stdout=out, stderr=io.StringIO()), 0)
        self.assertEqual(json.loads(out.getvalue()), {"sent": 2, "replayed_by_core": 0, "rejected": 0, "remaining": 0, "gaps": 1})
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='user.prompt' "
                                      "AND source_system='host-hook-v1'")[0]["n"], 1)
        # sending a spooled delivery again (a crash after Core stored it, before the spool entry was removed)
        self.write_binding(core_url=f"http://127.0.0.1:{self.dead_port()}")
        self.hook("post_tool_use_bash")
        self.write_binding()
        entry = self.spool.entries()[-1]
        document = self.spool.read(entry)
        runtime.post(load_binding(self.binding_path), document["request"])
        counts = runtime.replay(self.spool, load_binding(self.binding_path), io.StringIO())
        self.assertEqual((counts["sent"], counts["replayed_by_core"]), (1, 1))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)

    def test_replay_stops_while_core_is_still_down(self) -> None:
        self.hook("post_tool_use_bash")
        counts = runtime.replay(self.spool, load_binding(self.binding_path), io.StringIO())
        self.assertEqual((counts["sent"], counts["remaining"]), (0, 1))
        self.assertEqual(len(self.spool.entries()), 1)

    def test_a_successful_delivery_drains_the_spool(self) -> None:
        self.hook("post_tool_use_bash")
        self.write_binding()
        out, code, _ = self.hook("stop")
        self.assertEqual((out, code), ("{}", 0))
        self.assertEqual(self.spool.entries(), [])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)

    def test_replay_without_any_binding_or_spool_dir_is_an_error(self) -> None:
        err = io.StringIO()
        import tempfile
        elsewhere = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-elsewhere-")
        self.addCleanup(elsewhere.cleanup)
        old = os.getcwd()
        os.chdir(elsewhere.name)
        try:
            self.assertEqual(runtime.run_replay(None, None, None, environ={}, stdout=io.StringIO(), stderr=err), 2)
        finally:
            os.chdir(old)


class Rejections(RuntimeBase):
    def test_permanent_rejection_is_not_spooled_and_follows_the_tool_policy(self) -> None:
        from jasmine_core import ids
        self.write_binding(task_id=ids.new_id("tsk"), step_id=...)
        out, code, err = self.hook("post_tool_use_bash")
        self.assertEqual((out, code), ("", 0))
        self.assertIn("Core rejected tool.after: HTTP 404", err)
        self.assertEqual(Spool(self.binding_path.parent / "spool").entries(), [])
        out, _, _ = self.hook("pre_tool_use_bash")
        self.assertIn("deny", out)  # no decision was obtained; default policy deny
        self.write_binding(task_id=ids.new_id("tsk"), step_id=..., on_unavailable="allow")
        out, _, _ = self.hook("pre_tool_use_bash")
        self.assertEqual(out, "")  # explicit opt-in
        self.write_binding(task_id=ids.new_id("tsk"), step_id=..., on_unavailable="deny")
        out, _, _ = self.hook("pre_tool_use_bash")
        self.assertIn("deny", out)

    def test_key_file_with_loose_permissions_is_unusable(self) -> None:
        self.system_key.chmod(0o644)
        out, code, err = self.hook("post_tool_use_bash")
        self.assertEqual((out, code), ("", 0))
        self.assertIn("key unavailable", err)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)
        self.assertEqual(len(Spool(self.binding_path.parent / "spool").entries()), 1)


class BindingFile(RuntimeBase):
    def test_loading_validates_everything(self) -> None:
        binding = load_binding(self.binding_path)
        self.assertEqual(binding.task_id, self.task)
        self.assertEqual(binding.spool_dir, self.binding_path.parent / "spool")
        self.assertIs(binding.key_file_for("prompt.submit"), binding.human_api_key_file)
        self.assertIs(binding.key_file_for("tool.after"), binding.api_key_file)
        bad = [{"core_url": "http://example.com:80"}, {"core_url": "https://127.0.0.1:1"}, {"version": 2},
               {"on_unavailable": "maybe"}, {"api_key_file": "relative.key"}, {"unknown": 1},
               {"task_id": "tsk_nope"}, {"timeout_seconds": 0}]
        for change in bad:
            with self.subTest(change):
                self.write_binding(**change)
                with self.assertRaises(BindingError):
                    load_binding(self.binding_path)

    def test_no_token_in_the_binding_and_discovery(self) -> None:
        self.write_binding()
        self.assertNotIn(self.system_token, self.binding_path.read_text())
        nested = self.project_dir / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(find_binding(None, {}, str(nested)), self.binding_path)
        self.assertEqual(find_binding(str(self.binding_path), {}, None), self.binding_path)
        self.assertEqual(find_binding(None, {"JASMINE_HOST_BINDING": str(self.binding_path)}, None), self.binding_path)
        self.assertIsNone(find_binding(None, {}, "/"))
