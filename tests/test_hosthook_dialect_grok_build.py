"""Grok Build dialect on fixtures constructed from the grok 1.0.41 hooks guide, plus the
deferred-context bridge and agent-neutral rule matching (ADR 0018) through a real HTTP Core.

Component level. No Grok Build session was run: these tests show the dialect and runtime do
what the documentation says, not that Grok Build does.
"""

from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import support  # noqa: F401  (puts src on sys.path)

from jasmine_core.hosthook import dialects, runtime
from jasmine_core.hosthook.dialects import Outcome, capabilities_sha256, grok_build

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosthook" / "grok-build"
NOW = datetime(2026, 10, 13, 8, 30, 15, tzinfo=timezone.utc)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def ok(**response) -> Outcome:
    return Outcome("ok", response=response)


def tr(name: str):
    return grok_build.translate_in(fixture(name), NOW)


class Registry(unittest.TestCase):
    def test_discovered_and_capability_file_matches(self) -> None:
        self.assertIn("grok-build", dialects.names())
        self.assertIs(dialects.load("grok-build"), grok_build)
        path = Path(grok_build.__file__).with_name("grok_build.capabilities.json")
        self.assertEqual(json.loads(path.read_text()), grok_build.CAPABILITIES)
        caps = grok_build.CAPABILITIES
        self.assertEqual((caps["can_ask"], caps["inject_mode"], caps["inject_events"]),
                         (True, "deferred", ["tool.after"]))
        self.assertEqual(len(capabilities_sha256(caps)), 64)


class TranslateIn(unittest.TestCase):
    def test_camel_case_envelope(self) -> None:
        prompt = tr("user_prompt_submit")
        self.assertEqual((prompt.event, prompt.turn_id, prompt.session_id), ("prompt.submit", "prm-1", "grok-sess-1"))
        self.assertEqual(prompt.payload, {"text": fixture("user_prompt_submit")["prompt"]})
        self.assertTrue(prompt.delivery_id.startswith("grok-build:prompt.submit:"))
        start = tr("session_start")
        self.assertEqual((start.event, start.payload), ("session.start", {"source": "startup"}))

    def test_tool_categories_names_and_operands(self) -> None:
        bash = tr("pre_tool_use_bash")
        self.assertEqual((bash.payload["tool"], bash.payload["native_tool"], bash.call_id),
                         ("shell", "run_terminal_command", "call-1"))
        edit = tr("pre_tool_use_edit")
        self.assertEqual((edit.payload["tool"], edit.files, edit.file_action), ("file.edit", ["src/app.py"], "edit"))
        mcp = tr("pre_tool_use_mcp")
        self.assertEqual((mcp.payload["tool"], mcp.payload["native_tool"]), ("mcp", "linear__save_issue"))
        for name, category in (("bash", "shell"), ("run_terminal_cmd", "shell"), ("read_file", "file.read"),
                               ("write", "file.write"), ("apply_patch", "file.edit"), ("web_fetch", "web"),
                               ("spawn_subagent", "other")):
            with self.subTest(name):
                self.assertEqual(grok_build._category(name), category)

    def test_apply_patch_operands_come_from_the_patch_text(self) -> None:
        native = fixture("pre_tool_use_edit")
        native["toolName"] = "apply_patch"
        native["toolInput"] = {"input": "*** Begin Patch\n*** Update File: a/b.py\n*** Add File: c.md\n*** End Patch\n"}
        got = grok_build.translate_in(native, NOW)
        self.assertEqual((got.files, got.file_action), (["a/b.py", "c.md"], "edit"))

    def test_post_tool_result_exit_code_is_carried_when_present(self) -> None:
        good = tr("post_tool_use_bash").payload
        self.assertEqual((good["exit_code"], good["response"]["output_for_prompt"]), (0, "hi\n"))
        self.assertEqual(tr("post_tool_use_bash_failed").payload["exit_code"], 1)
        native = fixture("post_tool_use_bash")
        native["toolResult"] = "truncated text"  # oversized results arrive as a plain string
        self.assertNotIn("exit_code", grok_build.translate_in(native, NOW).payload)

    def test_compaction_both_phases_and_stop(self) -> None:
        self.assertEqual(tr("pre_compact").payload, {"phase": "before", "trigger": "manual"})
        self.assertEqual(tr("post_compact").payload, {"phase": "after", "trigger": "manual"})
        stop = tr("stop")
        self.assertEqual(stop.event, "turn.stop")
        self.assertEqual(len(stop.payload["last_message_sha256"]), 64)
        self.assertNotIn("Done.", json.dumps(stop.payload))

    def test_events_that_are_not_this_sessions_are_ignored(self) -> None:
        self.assertIsNone(tr("stop_session_end"))  # teardown Stop: SessionEnd covers it
        self.assertIsNone(tr("stop_subagent"))
        self.assertIsNone(tr("session_end_subagent"))
        self.assertEqual(tr("session_end").payload, {"reason": "exit"})
        self.assertIsNone(grok_build.translate_in({"hook_event_name": "StopCancelled", "sessionId": "s"}, NOW))
        self.assertIsNone(grok_build.translate_in({"hook_event_name": "Stop"}, NOW))
        self.assertEqual(grok_build.passthrough({}), ("", 0))

    def test_subagent_tool_calls_are_still_checked(self) -> None:
        native = fixture("pre_tool_use_bash")
        native["subagentType"] = "explore"
        self.assertEqual(grok_build.translate_in(native, NOW).event, "tool.before")


class TranslateOut(unittest.TestCase):
    def test_deny_and_ask_use_the_documented_shape(self) -> None:
        for decision in ("deny", "ask"):
            text, code = grok_build.translate_out(tr("pre_tool_use_bash"), ok(decision=decision, reason="matched rul_X"))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(text), {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": "matched rul_X"}})

    def test_allow_is_silence_and_unavailable_follows_the_binding(self) -> None:
        pre = tr("pre_tool_use_bash")
        self.assertEqual(grok_build.translate_out(pre, ok(decision="allow", reason=None)), ("", 0))
        self.assertEqual(grok_build.translate_out(pre, Outcome("unavailable", on_unavailable="allow")), ("", 0))
        text, _ = grok_build.translate_out(pre, Outcome("unavailable", on_unavailable="deny"))
        self.assertEqual(json.loads(text)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(grok_build.translate_out(pre, Outcome("no_binding")), ("", 0))

    def test_nothing_is_injected_where_grok_discards_stdout(self) -> None:
        inject = {"inject_context": {"text": "PACK", "context_pack_id": "ctx_X"}}
        for name in ("session_start", "user_prompt_submit", "post_compact", "pre_tool_use_bash", "stop"):
            self.assertEqual(grok_build.translate_out(tr(name), ok(**inject)), ("", 0), name)

    def test_deferred_context_is_emitted_as_posttooluse_additional_context(self) -> None:
        text, code = grok_build.translate_out(tr("post_tool_use_bash"), Outcome("ok", response={}, deferred_context="PACK"))
        self.assertEqual((json.loads(text), code), ({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "PACK"}}, 0))
        self.assertEqual(grok_build.translate_out(tr("post_tool_use_bash"), ok()), ("", 0))


try:
    from hosthook_support import HostHookHttp
    from test_hosthook_runtime import RuntimeBase
except ImportError:  # pragma: no cover
    RuntimeBase = None


if RuntimeBase is not None:
    class GrokRuntime(RuntimeBase):
        def hook(self, name: str, *, now=NOW, **overrides):  # type: ignore[override]
            native = fixture(name)
            native["cwd"] = native["workspaceRoot"] = str(self.project_dir)
            native.update(overrides)
            out, err = io.StringIO(), io.StringIO()
            code = runtime.run_hook("grok-build", json.dumps(native).encode(), stdout=out, stderr=err,
                                    environ={}, now=lambda: now)
            return out.getvalue(), code, err.getvalue()

        def test_context_pack_is_delivered_once_after_the_first_tool_call(self) -> None:
            out, code, err = self.hook("user_prompt_submit")
            self.assertEqual((out, code, err), ("", 0, ""))  # nothing at the prompt
            self.assertEqual(self.db_rows("SELECT count(*) AS n FROM context_packs")[0]["n"], 1)
            out, _, err = self.hook("post_tool_use_bash")
            body = json.loads(out)["hookSpecificOutput"]
            self.assertEqual((body["hookEventName"], err), ("PostToolUse", ""))
            self.assertIn("Hook goal", body["additionalContext"])
            out, _, _ = self.hook("post_tool_use_bash_failed")
            self.assertEqual(out, "")  # consumed: delivered once
            self.assertEqual(self.db_rows("SELECT status FROM evidence ORDER BY status")[0]["status"], "FAIL")

        def test_newest_pack_wins_and_pending_survives_a_core_outage(self) -> None:
            self.hook("session_start")
            self.hook("user_prompt_submit")
            self.write_binding(core_url=f"http://127.0.0.1:{self.dead_port()}")
            out, _, err = self.hook("post_tool_use_bash")
            self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])  # local file, no Core needed
            self.assertIn("spooled", err)  # the evidence itself waits for replay

        def test_binding_without_injection_defers_nothing(self) -> None:
            self.write_binding(inject_context=False)
            self.hook("user_prompt_submit")
            self.assertEqual(self.hook("post_tool_use_bash")[0], "")

        def test_pending_context_is_per_session(self) -> None:
            self.hook("user_prompt_submit")
            out, _, _ = self.hook("post_tool_use_bash", sessionId="other-session")
            self.assertEqual(out, "")  # the pack belongs to grok-sess-1
            self.assertIn("additionalContext", json.loads(self.hook("post_tool_use_bash_failed")[0])["hookSpecificOutput"])

        def test_pending_context_expires_and_is_private(self) -> None:
            import os
            import stat
            import time
            from jasmine_core.hosthook.spool import Spool
            spool = Spool(self.project_dir / "spool-x")
            spool.put_deferred("s", "PACK")
            path = spool._deferred_path("s")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            self.assertEqual(spool.take_deferred("s"), "PACK")
            self.assertIsNone(spool.take_deferred("s"))  # once
            spool.put_deferred("s", "OLD")
            document = json.loads(path.read_text())
            document["saved_at_ns"] = time.time_ns() - (Spool.DEFERRED_TTL_SECONDS + 5) * 1_000_000_000
            path.write_text(json.dumps(document))
            self.assertIsNone(spool.take_deferred("s"))
            self.assertFalse(path.exists())

        def test_post_compact_pack_is_deferred(self) -> None:
            self.assertEqual(self.hook("post_compact")[0], "")
            out, _, _ = self.hook("post_tool_use_bash")
            self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])
            self.assertEqual([r["reason"] for r in self.db_rows("SELECT reason FROM context_packs")], ["POST_COMPACT"])

        def test_a_category_rule_blocks_grok_without_naming_its_tool(self) -> None:
            self.rule("no-rm-any-agent", "DENY", {"tool_category": "shell", "action": "rm",
                                                  "path_prefix": "/protected"})
            decision = json.loads(self.hook("pre_tool_use_bash")[0])["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("rul_", decision["permissionDecisionReason"])
            out, _, err = self.hook("pre_tool_use_bash", toolInput={"command": "ls /elsewhere"}, toolUseId="call-9")
            self.assertEqual(out, "", err)


if __name__ == "__main__":
    unittest.main()
