"""Claude Code dialect translation, both directions, on fixtures constructed from docs.

Fixtures under fixtures/hosthook/claude-code/ are hand-built from the Claude Code hooks
documentation (read 2026-10-12 through a summarising fetch); none was captured from a real
Claude Code session. These tests show the dialect does what the docs say, not that Claude Code does.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import support  # noqa: F401  (puts src on sys.path)

from jasmine_core.hosthook import dialects, runtime
from jasmine_core.hosthook.dialects import Outcome, capabilities_sha256, claude_code

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosthook" / "claude-code"
NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 12, 8, 30, 16, tzinfo=timezone.utc)
PROMPT_ID = "550e8400-e29b-41d4-a716-446655440000"
ALL_FIXTURES = sorted(p.stem for p in FIXTURES.glob("*.json"))


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def ok(**response) -> Outcome:
    return Outcome("ok", response=response)


class Registry(unittest.TestCase):
    def test_hyphenated_agent_name_is_registered(self) -> None:
        self.assertIn("claude-code", dialects.names())
        self.assertIs(dialects.load("claude-code"), claude_code)
        with self.assertRaises(KeyError):
            dialects.load("claude_code_x")

    def test_capability_file_matches_the_module(self) -> None:
        path = Path(claude_code.__file__).with_name("claude_code.capabilities.json")
        self.assertEqual(json.loads(path.read_text()), claude_code.CAPABILITIES)
        caps = claude_code.CAPABILITIES
        self.assertTrue(caps["can_block_tool"] and caps["can_ask"] and caps["can_inject_context"])
        self.assertEqual(caps["inject_events"], ["session.start", "prompt.submit"])
        self.assertEqual(len(capabilities_sha256(caps)), 64)
        self.assertNotEqual(capabilities_sha256(caps), capabilities_sha256(dialects.load("codex").CAPABILITIES))


class TranslateIn(unittest.TestCase):
    def test_user_prompt_is_kept_byte_for_byte_and_prompt_id_is_the_turn(self) -> None:
        native = fixture("user_prompt_submit")
        tr = claude_code.translate_in(native, NOW)
        self.assertEqual((tr.event, tr.turn_id, tr.session_id), ("prompt.submit", PROMPT_ID, "abc123"))
        self.assertEqual(tr.payload, {"text": native["prompt"]})
        self.assertTrue(tr.delivery_id.startswith("claude-code:prompt.submit:"))

    def test_turn_id_substitute_without_prompt_id(self) -> None:
        a = claude_code.translate_in(fixture("user_prompt_submit_no_prompt_id"), NOW)
        self.assertTrue(a.turn_id.startswith("cc-"))
        # content-derived: stable across re-runs and across seconds
        self.assertEqual(a.turn_id, claude_code.translate_in(fixture("user_prompt_submit_no_prompt_id"), LATER).turn_id)
        other = {**fixture("user_prompt_submit_no_prompt_id"), "prompt": "different"}
        self.assertNotEqual(a.turn_id, claude_code.translate_in(other, NOW).turn_id)
        # without a native prompt_id the delivery id carries the invocation second, so the same
        # prompt typed twice is two events; the same invocation (a replay of the request) is one
        self.assertEqual(a.delivery_id, claude_code.translate_in(fixture("user_prompt_submit_no_prompt_id"), NOW).delivery_id)
        self.assertNotEqual(a.delivery_id, claude_code.translate_in(fixture("user_prompt_submit_no_prompt_id"), LATER).delivery_id)

    def test_with_prompt_id_the_delivery_is_exact_and_second_independent(self) -> None:
        a = claude_code.translate_in(fixture("user_prompt_submit"), NOW).delivery_id
        self.assertEqual(a, claude_code.translate_in(fixture("user_prompt_submit"), LATER).delivery_id)
        again = {**fixture("user_prompt_submit"), "prompt_id": "other-id"}
        self.assertNotEqual(a, claude_code.translate_in(again, NOW).delivery_id)

    def test_tool_delivery_ids_use_the_tool_use_id(self) -> None:
        pre = claude_code.translate_in(fixture("pre_tool_use_bash"), NOW)
        self.assertEqual(pre.call_id, "toolu_01ABC")
        self.assertEqual(pre.delivery_id, claude_code.translate_in(fixture("pre_tool_use_bash"), LATER).delivery_id)
        post = claude_code.translate_in(fixture("post_tool_use_bash"), NOW)
        self.assertNotEqual(pre.delivery_id, post.delivery_id)
        missing = fixture("pre_tool_use_bash")
        del missing["tool_use_id"]
        self.assertEqual(len(claude_code.translate_in(missing, NOW).call_id), 64)

    def test_session_events_use_the_invocation_second(self) -> None:
        one = claude_code.translate_in(fixture("session_start"), NOW).delivery_id
        self.assertEqual(one, claude_code.translate_in(fixture("session_start"), NOW).delivery_id)
        self.assertNotEqual(one, claude_code.translate_in(fixture("session_start"), LATER).delivery_id)

    def test_grep_without_a_path_is_scoped_to_the_cwd(self) -> None:
        native = {**fixture("pre_tool_use_bash"), "tool_name": "Grep", "tool_input": {"pattern": "x"}}
        got = claude_code.translate_in(native, NOW)
        self.assertEqual((got.files, got.file_action), (["."], "read"))

    def test_tool_categories_and_file_operands(self) -> None:
        cases = {"pre_tool_use_bash": ("shell", "Bash", None, None),
                 "pre_tool_use_write": ("file.write", "Write", ["/work/project/src/new.py"], "write"),
                 "pre_tool_use_edit": ("file.edit", "Edit", ["src/a.py"], "edit"),
                 "pre_tool_use_read": ("file.read", "Read", ["/protected/secret.txt"], "read"),
                 "pre_tool_use_webfetch": ("web", "WebFetch", None, None),
                 "pre_tool_use_mcp": ("mcp", "mcp__filesystem__read_file", None, None)}
        for name, (category, native_tool, files, action) in cases.items():
            with self.subTest(name):
                tr = claude_code.translate_in(fixture(name), NOW)
                self.assertEqual((tr.payload["tool"], tr.payload["native_tool"], tr.files, tr.file_action),
                                 (category, native_tool, files, action))
        other = claude_code.translate_in({**fixture("pre_tool_use_bash"), "tool_name": "TodoWrite"}, NOW)
        self.assertEqual(other.payload["tool"], "other")

    def test_post_tool_exit_code_mapping_is_never_invented(self) -> None:
        plain = claude_code.translate_in(fixture("post_tool_use_bash"), NOW).payload
        self.assertNotIn("exit_code", plain)  # no documented integer status: Core will say INFO, not PASS
        self.assertEqual(plain["response"]["stdout"], "output text")
        self.assertEqual(claude_code.translate_in(fixture("post_tool_use_bash_with_exit_code"), NOW).payload["exit_code"], 0)
        self.assertEqual(claude_code.translate_in(fixture("post_tool_use_bash_failed"), NOW).payload["exit_code"], 1)
        interrupted = claude_code.translate_in(fixture("post_tool_use_bash_interrupted"), NOW).payload
        self.assertIs(interrupted["is_error"], True)
        self.assertNotIn("is_error", plain)  # interrupted:false says nothing

    def test_compact_stop_and_session_end(self) -> None:
        self.assertEqual(claude_code.translate_in(fixture("pre_compact"), NOW).payload, {"phase": "before", "trigger": "auto"})
        stop = claude_code.translate_in(fixture("stop"), NOW)
        self.assertEqual(stop.event, "turn.stop")
        self.assertEqual(len(stop.payload["last_message_sha256"]), 64)
        self.assertNotIn("Done.", json.dumps(stop.payload))
        end = claude_code.translate_in(fixture("session_end"), NOW)
        self.assertEqual((end.event, end.payload), ("session.end", {"reason": "other"}))
        compacted = claude_code.translate_in(fixture("session_start_compact"), NOW)
        self.assertEqual((compacted.event, compacted.payload), ("session.start", {"source": "compact"}))

    def test_unsupported_or_malformed_events_are_ignored(self) -> None:
        self.assertIsNone(claude_code.translate_in({**fixture("stop"), "hook_event_name": "SubagentStop"}, NOW))
        self.assertIsNone(claude_code.translate_in({"hook_event_name": "Stop"}, NOW))
        self.assertIsNone(claude_code.translate_in({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": 3}, NOW))
        self.assertEqual(claude_code.passthrough({"hook_event_name": "Stop"}), ("", 0))


class TranslateOut(unittest.TestCase):
    def tr(self, name: str):
        return claude_code.translate_in(fixture(name), NOW)

    def test_deny_uses_the_documented_pretooluse_shape(self) -> None:
        text, code = claude_code.translate_out(self.tr("pre_tool_use_bash"), ok(decision="deny", reason="matched rul_X"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "matched rul_X"}})

    def test_ask_is_mapped_natively_not_downgraded(self) -> None:
        text, code = claude_code.translate_out(self.tr("pre_tool_use_bash"), ok(decision="ask", reason="insufficient context"))
        out = json.loads(text)["hookSpecificOutput"]
        self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"], code), ("ask", "insufficient context", 0))

    def test_allow_is_empty_output_never_a_permission_grant(self) -> None:
        self.assertEqual(claude_code.translate_out(self.tr("pre_tool_use_bash"), ok(decision="allow", reason=None)), ("", 0))

    def test_reason_is_bounded(self) -> None:
        text, _ = claude_code.translate_out(self.tr("pre_tool_use_bash"), ok(decision="deny", reason="x" * 5000))
        self.assertEqual(len(json.loads(text)["hookSpecificOutput"]["permissionDecisionReason"]), claude_code.REASON_LIMIT)

    def test_unavailable_follows_the_binding_policy(self) -> None:
        tr = self.tr("pre_tool_use_bash")
        self.assertIn("deny", claude_code.translate_out(tr, Outcome("unavailable"))[0])  # default is deny
        self.assertEqual(claude_code.translate_out(tr, Outcome("unavailable", on_unavailable="allow")), ("", 0))
        self.assertEqual(json.loads(claude_code.translate_out(tr, Outcome("rejected", on_unavailable="deny"))[0])
                         ["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(claude_code.translate_out(tr, Outcome("no_binding")), ("", 0))

    def test_context_injection_shapes(self) -> None:
        inject = {"inject_context": {"text": "PACK", "context_pack_id": "ctx_X"}}
        for name, hook in (("user_prompt_submit", "UserPromptSubmit"), ("session_start", "SessionStart"),
                           ("session_start_compact", "SessionStart")):
            with self.subTest(name):
                text, code = claude_code.translate_out(self.tr(name), ok(**inject))
                self.assertEqual(json.loads(text), {"hookSpecificOutput": {"hookEventName": hook, "additionalContext": "PACK"}})
        for name in ("pre_compact", "stop", "post_tool_use_bash", "session_end"):
            with self.subTest(name):
                self.assertEqual(claude_code.translate_out(self.tr(name), ok(**inject)), ("", 0))

    def test_failure_to_reach_core_on_capture_events_is_silent_and_never_blocks(self) -> None:
        for name in ("user_prompt_submit", "post_tool_use_bash", "stop", "pre_compact", "session_end", "session_start"):
            with self.subTest(name):
                self.assertEqual(claude_code.translate_out(self.tr(name), Outcome("unavailable")), ("", 0))


class RuntimeRequests(unittest.TestCase):
    def test_runtime_builds_a_protocol_valid_request_for_every_fixture(self) -> None:
        from jasmine_core import ids
        from jasmine_core.hosthook.binding import Binding
        from jasmine_core.hosthook.protocol import parse_request
        binding = Binding(path=Path("/x/.jasmine/host-binding.json"), core_url="http://127.0.0.1:1",
                          host_id=ids.new_id("hst"), project_id=ids.new_id("prj"), task_id=ids.new_id("tsk"),
                          step_id=None, api_key_file=Path("/x/k"), human_api_key_file=None,
                          on_unavailable="deny", spool_dir=Path("/x/s"), timeout_seconds=None)
        for name in ALL_FIXTURES:
            with self.subTest(name):
                tr = claude_code.translate_in(fixture(name), NOW)
                request = runtime.build_request(claude_code, tr, binding, NOW)
                parsed = parse_request(request)
                self.assertEqual((parsed.trust, request["agent"]["name"], request["adapter"]["name"]),
                                 ("NATIVE_HOOK", "claude-code", "claude-code"))
                self.assertEqual(request["adapter"]["capabilities_sha256"], capabilities_sha256(claude_code.CAPABILITIES))

    def test_guard_inputs_for_shell_and_file_tools(self) -> None:
        from jasmine_core import ids
        from jasmine_core.hosthook.binding import Binding
        binding = Binding(path=Path("/x/.jasmine/host-binding.json"), core_url="http://127.0.0.1:1",
                          host_id=ids.new_id("hst"), project_id=None, task_id=None, step_id=None,
                          api_key_file=Path("/x/k"), human_api_key_file=None, on_unavailable="deny",
                          spool_dir=Path("/x/s"), timeout_seconds=None)

        def build(name):
            return runtime.build_request(claude_code, claude_code.translate_in(fixture(name), NOW), binding, NOW)["payload"]
        self.assertEqual(build("pre_tool_use_bash")["action"], "rm")
        self.assertNotIn("action", build("pre_tool_use_bash_compound"))
        edit = build("pre_tool_use_edit")
        self.assertEqual(edit["action"], "edit")
        self.assertIn("/work/project/src/a.py", edit["paths"])  # relative operand resolved against cwd
        self.assertEqual(build("pre_tool_use_read")["action"], "read")


class FixturesAreLabelled(unittest.TestCase):
    def test_readme_states_they_are_constructed_from_docs(self) -> None:
        text = (FIXTURES / "README.md").read_text()
        self.assertIn("constructed by hand from the Claude Code hooks documentation", text)
        self.assertIn("not captured from a real Claude Code session", text.replace("**", ""))


if __name__ == "__main__":
    unittest.main()
