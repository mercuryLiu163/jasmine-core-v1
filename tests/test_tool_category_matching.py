"""Agent-neutral rule matching (ADR 0018): `matcher.tool_category` and Guard's `tool_category`."""

from __future__ import annotations

import json
import unittest

import support  # noqa: F401

from jasmine_core import errors
from jasmine_core.authority import TOOL_CATEGORIES


class MatcherValidation(unittest.TestCase):
    def test_categories_are_one_definition_shared_with_the_protocol(self) -> None:
        from jasmine_core.hosthook import protocol
        self.assertIs(protocol.TOOL_CATEGORIES, TOOL_CATEGORIES)
        self.assertEqual(TOOL_CATEGORIES, ("shell", "file.read", "file.write", "file.edit", "web", "mcp", "other"))

    def test_matcher_accepts_known_categories_only(self) -> None:
        from jasmine_core.authority import _matcher
        self.assertEqual(_matcher({"tool_category": "shell", "action": "rm"}, "DENY"),
                         {"tool_category": "shell", "action": "rm"})
        with self.assertRaises(errors.InvalidRequest):
            _matcher({"tool_category": "Bash"}, "DENY")
        with self.assertRaises(errors.InvalidRequest):
            _matcher({"tool_kind": "shell"}, "DENY")


try:
    from hosthook_support import HostHookHttp
    from test_hosthook_runtime import RuntimeBase
except ImportError:  # pragma: no cover
    RuntimeBase = None

if RuntimeBase is not None:
    import io
    from datetime import datetime, timezone

    from jasmine_core.hosthook import runtime

    NOW = datetime(2026, 10, 13, 8, 30, 15, tzinfo=timezone.utc)

    class OneRuleThreeAgents(RuntimeBase):
        """One category rule, written once, blocks the same intent in every dialect."""

        def fire(self, agent: str, native: dict) -> dict | None:
            native = {**native, "cwd": str(self.project_dir)}
            out, err = io.StringIO(), io.StringIO()
            runtime.run_hook(agent, json.dumps(native).encode(), stdout=out, stderr=err, environ={}, now=lambda: NOW)
            return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None

        def test_same_rule_blocks_codex_claude_code_and_grok(self) -> None:
            from test_hosthook_dialect_claude_code import fixture as cc
            from test_hosthook_dialect_codex import fixture as cx
            from test_hosthook_dialect_grok_build import fixture as gb
            self.rule("no-rm", "DENY", {"tool_category": "shell", "action": "rm", "path_prefix": "/protected"})
            for agent, native in (("codex", cx("pre_tool_use_bash")), ("claude-code", cc("pre_tool_use_bash")),
                                  ("grok-build", gb("pre_tool_use_bash"))):
                with self.subTest(agent):
                    decision = self.fire(agent, native)
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertIn("rul_", decision["permissionDecisionReason"])

        def test_native_name_rules_stay_agent_specific(self) -> None:
            from test_hosthook_dialect_claude_code import fixture as cc
            from test_hosthook_dialect_grok_build import fixture as gb
            self.rule("no-rm-bash", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
            self.assertEqual(self.fire("claude-code", cc("pre_tool_use_bash"))["permissionDecision"], "deny")
            # Grok's shell tool is not named Bash: the exact-name rule does not match, so a rule that
            # could apply but cannot be shown to is not silently an allow
            decision = self.fire("grok-build", gb("pre_tool_use_bash"))
            self.assertIsNone(decision)

        def test_guard_endpoint_takes_a_category(self) -> None:
            self.rule("no-write-secret", "DENY", {"tool_category": "file.write", "path_prefix": "/secret"})

            token = self.key(["guard:check"], label="guard")

            def check(category: str, action: str) -> tuple[int, dict]:
                return self.call("POST", "/v1/guard/check", {
                    "project_id": self.project, "task_id": self.task, "tool_category": category,
                    "action": action, "path": "/secret/k"}, token=token)

            code, result = check("file.write", "write")
            self.assertEqual((code, result["decision"]), (200, "deny"))
            code, result = check("file.read", "read")
            self.assertEqual((code, result["decision"]), (200, "allow"))
            code, result = check("bogus", "x")
            self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
