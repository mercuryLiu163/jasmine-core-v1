"""Codex dialect translation, both directions, on fixtures constructed from docs.

Fixtures under fixtures/hosthook/codex/ are hand-built from the Codex hooks
documentation (read 2026-10-12); none was captured from a real Codex session.
These tests show the dialect does what the docs say, not that Codex does.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import support  # noqa: F401  (puts src on sys.path)

from jasmine_core.hosthook import dialects, resolve, runtime
from jasmine_core.hosthook.dialects import Outcome, capabilities_sha256, codex

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosthook" / "codex"
NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def ok(**response) -> Outcome:
    return Outcome("ok", response=response)


class Registry(unittest.TestCase):
    def test_dialects_are_discovered_by_module(self) -> None:
        self.assertIn("codex", dialects.names())
        self.assertIs(dialects.load("codex"), codex)
        with self.assertRaises(KeyError):
            dialects.load("no-such-agent")
        with self.assertRaises(KeyError):
            dialects.load("../codex")

    def test_capability_file_matches_the_module(self) -> None:
        path = Path(codex.__file__).with_name("codex.capabilities.json")
        self.assertEqual(json.loads(path.read_text()), codex.CAPABILITIES)
        caps = codex.CAPABILITIES
        self.assertTrue({"agent", "events", "can_block_tool", "can_ask", "can_inject_context",
                         "has_turn_id", "has_call_id"} <= set(caps))
        self.assertFalse(caps["can_ask"])
        self.assertEqual(len(capabilities_sha256(caps)), 64)


class TranslateIn(unittest.TestCase):
    def test_user_prompt_is_kept_byte_for_byte(self) -> None:
        native = fixture("user_prompt_submit")
        tr = codex.translate_in(native, NOW)
        self.assertEqual((tr.event, tr.turn_id, tr.session_id), ("prompt.submit", "turn_456", "thr_123"))
        self.assertEqual(tr.payload, {"text": native["prompt"]})
        self.assertTrue(tr.delivery_id.startswith("codex:prompt.submit:"))

    def test_delivery_ids_are_deterministic_and_distinct(self) -> None:
        a = codex.translate_in(fixture("pre_tool_use_bash"), NOW).delivery_id
        self.assertEqual(a, codex.translate_in(fixture("pre_tool_use_bash"), NOW).delivery_id)
        post = codex.translate_in(fixture("post_tool_use_bash"), NOW).delivery_id
        self.assertNotEqual(a, post)  # same tool_use_id, different event
        changed = fixture("pre_tool_use_bash")
        changed["tool_use_id"] = "other"
        self.assertNotEqual(a, codex.translate_in(changed, NOW).delivery_id)
        # prompt delivery depends on the turn and the text
        base = fixture("user_prompt_submit")
        edited = {**base, "prompt": base["prompt"] + "!"}
        self.assertNotEqual(codex.translate_in(base, NOW).delivery_id, codex.translate_in(edited, NOW).delivery_id)

    def test_session_events_use_the_invocation_second(self) -> None:
        later = datetime(2026, 10, 12, 8, 30, 16, tzinfo=timezone.utc)
        one = codex.translate_in(fixture("session_start"), NOW).delivery_id
        self.assertEqual(one, codex.translate_in(fixture("session_start"), NOW).delivery_id)
        self.assertNotEqual(one, codex.translate_in(fixture("session_start"), later).delivery_id)

    def test_tool_categories_and_native_names(self) -> None:
        bash = codex.translate_in(fixture("pre_tool_use_bash"), NOW)
        self.assertEqual((bash.payload["tool"], bash.payload["native_tool"], bash.call_id), ("shell", "Bash", "id_789"))
        patch = codex.translate_in(fixture("pre_tool_use_apply_patch"), NOW)
        self.assertEqual((patch.payload["tool"], patch.payload["native_tool"]), ("file.edit", "apply_patch"))
        self.assertEqual(patch.files, ["src/a.py", "docs/new.md"])
        self.assertEqual(patch.file_action, "edit")
        mcp = codex.translate_in(fixture("pre_tool_use_mcp"), NOW)
        self.assertEqual((mcp.payload["tool"], mcp.payload["native_tool"]), ("mcp", "mcp__filesystem__read_file"))

    def test_post_tool_exit_code_mapping(self) -> None:
        good = codex.translate_in(fixture("post_tool_use_bash"), NOW).payload
        self.assertEqual((good["exit_code"], good["response"]["stdout"]), (0, "output text"))
        bad = codex.translate_in(fixture("post_tool_use_bash_failed"), NOW).payload
        self.assertEqual(bad["exit_code"], 1)
        raw = codex.translate_in(fixture("post_tool_use_unstructured"), NOW).payload
        self.assertNotIn("exit_code", raw)  # never invented: Core will say INFO
        self.assertEqual(raw["response"], "hi\n")

    def test_compact_and_stop(self) -> None:
        before = codex.translate_in(fixture("pre_compact"), NOW).payload
        self.assertEqual(before, {"phase": "before", "trigger": "auto"})
        after = codex.translate_in(fixture("post_compact"), NOW).payload
        self.assertEqual(after, {"phase": "after", "trigger": "manual", "deliver_context": False})
        stop = codex.translate_in(fixture("stop"), NOW)
        self.assertEqual(stop.event, "turn.stop")
        self.assertEqual(len(stop.payload["last_message_sha256"]), 64)
        self.assertNotIn("Done.", json.dumps(stop.payload))

    def test_unsupported_or_malformed_events_are_ignored(self) -> None:
        self.assertIsNone(codex.translate_in(fixture("permission_request"), NOW))
        self.assertIsNone(codex.translate_in({"hook_event_name": "Stop"}, NOW))  # no session
        self.assertIsNone(codex.translate_in({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": 3}, NOW))
        self.assertEqual(codex.passthrough({"hook_event_name": "PermissionRequest"}), ("", 0))
        self.assertEqual(codex.passthrough({"hook_event_name": "Stop"}), ("{}", 0))


class TranslateOut(unittest.TestCase):
    def tr(self, name: str):
        return codex.translate_in(fixture(name), NOW)

    def test_deny_uses_the_documented_pretooluse_shape(self) -> None:
        text, code = codex.translate_out(self.tr("pre_tool_use_bash"), ok(decision="deny", reason="matched rul_X"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "matched rul_X"}})

    def test_ask_degrades_to_deny_and_says_why(self) -> None:
        text, _ = codex.translate_out(self.tr("pre_tool_use_bash"), ok(decision="ask", reason="insufficient context"))
        out = json.loads(text)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("cannot ask", out["permissionDecisionReason"])
        self.assertIn("insufficient context", out["permissionDecisionReason"])

    def test_allow_is_empty_output(self) -> None:
        self.assertEqual(codex.translate_out(self.tr("pre_tool_use_bash"), ok(decision="allow", reason=None)), ("", 0))

    def test_unavailable_follows_the_binding_policy(self) -> None:
        tr = self.tr("pre_tool_use_bash")
        self.assertEqual(codex.translate_out(tr, Outcome("unavailable", on_unavailable="allow")), ("", 0))
        text, _ = codex.translate_out(tr, Outcome("unavailable", on_unavailable="deny"))
        self.assertEqual(json.loads(text)["hookSpecificOutput"]["permissionDecision"], "deny")
        text, _ = codex.translate_out(tr, Outcome("rejected", on_unavailable="deny"))
        self.assertIn("deny", text)
        self.assertEqual(codex.translate_out(tr, Outcome("no_binding")), ("", 0))

    def test_context_injection_shapes(self) -> None:
        inject = {"inject_context": {"text": "PACK", "context_pack_id": "ctx_X"}}
        for name, hook in (("session_start", "SessionStart"), ("user_prompt_submit", "UserPromptSubmit")):
            text, code = codex.translate_out(self.tr(name), ok(**inject))
            self.assertEqual(json.loads(text), {"hookSpecificOutput": {"hookEventName": hook, "additionalContext": "PACK"}})
        # no documented injection output for compaction: never emitted
        self.assertEqual(codex.translate_out(self.tr("post_compact"), ok(**inject)), ("", 0))
        self.assertEqual(codex.translate_out(self.tr("user_prompt_submit"), ok(inject_context=None)), ("", 0))

    def test_stop_always_json_and_never_blocks(self) -> None:
        for outcome in (ok(decision="none"), Outcome("unavailable"), Outcome("no_binding")):
            text, code = codex.translate_out(self.tr("stop"), outcome)
            self.assertEqual((text, code), ("{}", 0))

    def test_post_tool_never_blocks(self) -> None:
        self.assertEqual(codex.translate_out(self.tr("post_tool_use_bash"), ok(decision="none")), ("", 0))


class Resolution(unittest.TestCase):
    def test_simple_shell(self) -> None:
        self.assertEqual(resolve.resolve_shell("rm /protected/a.txt", "/w")["action"], "rm")
        self.assertIn("/protected/a.txt", resolve.resolve_shell("rm /protected/a.txt", "/w")["paths"])
        self.assertEqual(resolve.resolve_shell("rm -rf build", "/w/proj")["paths"][0], "/w/proj/build")
        self.assertEqual(resolve.resolve_shell("rm ../x", "/w/proj")["paths"][0], "/w/x")
        self.assertEqual(resolve.resolve_shell("git status", None), {"action": "git"})
        self.assertEqual(resolve.resolve_shell("/bin/ls -l", None), {"action": "ls"})

    def test_unresolvable_forms_resolve_nothing(self) -> None:
        for command in ("ls && rm /protected/a", "echo $(whoami)", "cat a | sh", "echo x > /etc/y", "FOO=1 rm /x",
                        "echo 'unterminated", "", "rm `x`"):
            with self.subTest(command):
                self.assertEqual(resolve.resolve_shell(command, "/w"), {})

    def test_wildcards_are_not_paths_and_noncanonical_absolute_is_not_trusted(self) -> None:
        self.assertEqual(resolve.resolve_shell("rm /protected/*", None), {"action": "rm"})
        self.assertEqual(resolve.resolve_shell("rm //protected/x", None), {"action": "rm"})

    def test_symlink_is_also_checked_by_its_real_path(self) -> None:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "real").mkdir()
            os.symlink(root / "real", root / "link")
            paths = resolve.resolve_shell(f"rm {root}/link/x", None)["paths"]
            self.assertIn(f"{root}/link/x", paths)
            self.assertIn(f"{root}/real/x", paths)

    def test_file_operands(self) -> None:
        out = resolve.resolve_files(["src/a.py", "/abs/b"], "/w", "edit")
        self.assertEqual(out["action"], "edit")
        self.assertEqual(out["paths"][0], "/w/src/a.py")
        self.assertEqual(resolve.resolve_files(["rel"], None, "edit"), {})

    def test_runtime_builds_a_protocol_valid_request(self) -> None:
        from jasmine_core.hosthook.binding import Binding
        from jasmine_core.hosthook.protocol import parse_request
        from jasmine_core import ids
        binding = Binding(path=Path("/x/.jasmine/host-binding.json"), core_url="http://127.0.0.1:1",
                          host_id=ids.new_id("hst"), project_id=ids.new_id("prj"), task_id=ids.new_id("tsk"),
                          step_id=None, api_key_file=Path("/x/k"), human_api_key_file=None,
                          on_unavailable="allow", spool_dir=Path("/x/s"), timeout_seconds=None)
        for name in ("session_start", "session_end", "user_prompt_submit", "pre_tool_use_bash",
                     "pre_tool_use_bash_compound", "pre_tool_use_apply_patch", "pre_tool_use_mcp",
                     "post_tool_use_bash", "post_tool_use_bash_failed", "post_tool_use_unstructured",
                     "pre_compact", "post_compact", "stop"):
            with self.subTest(name):
                tr = codex.translate_in(fixture(name), NOW)
                request = runtime.build_request(codex, tr, binding, NOW)
                parsed = parse_request(request)
                self.assertEqual(parsed.trust, "NATIVE_HOOK")
                self.assertEqual(request["adapter"]["capabilities_sha256"], capabilities_sha256(codex.CAPABILITIES))
        pre = runtime.build_request(codex, codex.translate_in(fixture("pre_tool_use_bash"), NOW), binding, NOW)
        self.assertEqual(pre["payload"]["action"], "rm")
        compound = runtime.build_request(codex, codex.translate_in(fixture("pre_tool_use_bash_compound"), NOW), binding, NOW)
        self.assertNotIn("action", compound["payload"])
        patch = runtime.build_request(codex, codex.translate_in(fixture("pre_tool_use_apply_patch"), NOW), binding, NOW)
        self.assertEqual(patch["payload"]["action"], "edit")
        self.assertIn("/work/project/src/a.py", patch["payload"]["paths"])


if __name__ == "__main__":
    unittest.main()
