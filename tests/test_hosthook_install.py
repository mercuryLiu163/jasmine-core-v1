"""Project-level hook installer (`jasmine-core hooks install|uninstall|status`).

Only scratch directories are touched; HOME is redirected to an empty scratch directory so any
write to a user-global agent directory would be detected. Component level: the tests read and
write config files and run the generated command line; they do not show that Codex or Claude
Code loads, trusts or runs the entries.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401

from jasmine_core import ids
from jasmine_core.hosthook import install
from jasmine_core.hosthook.install import InstallError

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CLI = shlex.join([sys.executable, "-m", "jasmine_core.cli"])
FOREIGN = {"type": "command", "command": "/usr/local/bin/other-tool --flag", "timeout": 5}


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class InstallBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-install-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home = base / "home"
        self.home.mkdir()
        self.project = base / "project with space"
        self.project.mkdir()
        self.binding = base / "bindings" / "host-binding.json"
        self.binding.parent.mkdir()
        self.binding.write_text(json.dumps({
            "version": 1, "core_url": "http://127.0.0.1:9", "host_id": ids.new_id("hst"),
            "api_key_file": str(base / "private" / "system.key")}))
        patcher = patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, agent: str) -> Path:
        return self.project / (".claude/settings.json" if agent == "claude-code" else ".codex/hooks.json")

    def load(self, agent: str) -> dict:
        return json.loads(self.settings(agent).read_text())

    def install(self, agent: str, binding: Path | None = None):
        return install.install(agent, self.project, binding or self.binding, PYTHON_CLI)

    def owned(self, agent: str, binding: Path | None = None) -> list[tuple[str, dict]]:
        out = []
        for event, groups in self.load(agent)["hooks"].items():
            for group in groups:
                for handler in group["hooks"]:
                    if install.owns(handler["command"], agent, binding):
                        out.append((event, handler))
        return out


class Merge(InstallBase):
    def test_claude_code_entries_merge_into_existing_settings(self) -> None:
        self.settings("claude-code").parent.mkdir()
        existing = {"permissions": {"allow": ["Bash(ls)"]}, "env": {"A": "1"},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}],
                              "Notification": [{"hooks": [FOREIGN]}]}}
        self.settings("claude-code").write_text(json.dumps(existing))
        result = self.install("claude-code")
        self.assertEqual(result["result"], "installed")
        document = self.load("claude-code")
        self.assertEqual(document["permissions"], existing["permissions"])
        self.assertEqual(document["env"], existing["env"])
        self.assertEqual(document["hooks"]["Notification"], existing["hooks"]["Notification"])
        self.assertEqual(document["hooks"]["PreToolUse"][0], existing["hooks"]["PreToolUse"][0])
        self.assertEqual(sorted(e for e, _ in self.owned("claude-code")),
                         ["PostToolUse", "PreCompact", "PreToolUse", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"])
        timeouts = {e: h["timeout"] for e, h in self.owned("claude-code")}
        self.assertEqual((timeouts["PreToolUse"], timeouts["Stop"]), (10, 30))

    def test_codex_entries_go_to_the_project_hooks_json_only(self) -> None:
        self.settings("codex").parent.mkdir()
        existing = {"description": "mine", "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [FOREIGN]}]}}
        self.settings("codex").write_text(json.dumps(existing))
        self.install("codex")
        document = self.load("codex")
        self.assertEqual(document["description"], "mine")
        self.assertEqual(document["hooks"]["SessionStart"][0], existing["hooks"]["SessionStart"][0])
        self.assertEqual({e for e, _ in self.owned("codex")}, {"SessionStart", "UserPromptSubmit", "PreToolUse",
                         "PostToolUse", "PreCompact", "PostCompact", "Stop", "SessionEnd"})
        self.assertFalse(self.settings("claude-code").exists())

    def test_install_is_idempotent_and_replaces_a_changed_binding(self) -> None:
        self.install("claude-code")
        first = self.settings("claude-code").read_text()
        self.install("claude-code")
        self.assertEqual(self.settings("claude-code").read_text(), first)
        other = self.binding.with_name("other.json")
        other.write_text(self.binding.read_text())
        self.install("claude-code", other)
        self.assertEqual(len(self.owned("claude-code")), 7)
        self.assertEqual(len(self.owned("claude-code", other)), 7)
        self.assertEqual(self.owned("claude-code", self.binding), [])

    def test_the_two_agents_do_not_touch_each_others_entries(self) -> None:
        self.install("codex")
        self.install("claude-code")
        install.uninstall("claude-code", self.project)
        self.assertEqual(len(self.owned("codex")), 8)
        self.assertEqual(self.owned("claude-code"), [])

    def test_fresh_project_gets_a_new_file_and_nothing_else(self) -> None:
        before = tree(Path(self.tmp.name))
        self.install("claude-code")
        added = [p for p in tree(Path(self.tmp.name)) if p not in before]
        self.assertEqual(added, ["project with space/.claude", "project with space/.claude/settings.json"])
        self.assertEqual(tree(self.home), [])  # nothing global
        self.assertEqual(self.load("claude-code")["hooks"]["PreToolUse"][0]["hooks"][0]["statusMessage"], "Jasmine Host Hook")

    def test_generated_command_is_runnable_by_a_shell_and_fails_closed_without_core(self) -> None:
        self.install("claude-code")
        command = self.owned("claude-code")[0][1]["command"]
        self.assertIn("'" + str(self.binding) + "'" if " " in str(self.binding) else str(self.binding), command)
        pre = next(h for e, h in self.owned("claude-code") if e == "PreToolUse")
        native = {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(self.project),
                  "tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "ls"}}
        done = subprocess.run(["/bin/sh", "-c", pre["command"]], input=json.dumps(native), text=True,
                              capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        # no key file and Core down: the default policy is deny
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


class Uninstall(InstallBase):
    def test_only_owned_entries_are_removed(self) -> None:
        self.settings("claude-code").parent.mkdir()
        mixed_command = install.command_for("claude-code", self.binding, PYTHON_CLI)
        existing = {"model": "x", "hooks": {
            "Stop": [{"hooks": [FOREIGN, {"type": "command", "command": mixed_command}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo jasmine-core hook --agent claude-code"}]}]}}
        self.settings("claude-code").write_text(json.dumps(existing))
        self.install("claude-code")
        result = install.uninstall("claude-code", self.project)
        self.assertEqual((result["result"], result["removed"]), ("uninstalled", 7))
        document = self.load("claude-code")
        self.assertEqual(document["model"], "x")
        self.assertEqual(document["hooks"]["Stop"], [{"hooks": [FOREIGN]}])
        self.assertEqual(document["hooks"]["PreToolUse"], existing["hooks"]["PreToolUse"])
        # a look-alike command that is not ours stays
        self.assertEqual(document["hooks"]["UserPromptSubmit"], existing["hooks"]["UserPromptSubmit"])
        self.assertEqual(sorted(document["hooks"]), ["PreToolUse", "Stop", "UserPromptSubmit"])

    def test_uninstall_with_a_binding_filter_and_when_nothing_is_installed(self) -> None:
        self.assertEqual(install.uninstall("codex", self.project)["result"], "not_installed")
        self.install("codex")
        other = self.binding.with_name("elsewhere.json")
        self.assertEqual(install.uninstall("codex", self.project, other)["removed"], 0)
        self.assertEqual(len(self.owned("codex")), 8)
        self.assertEqual(install.uninstall("codex", self.project, self.binding)["removed"], 8)
        self.assertEqual(self.load("codex")["hooks"], {})


class Status(InstallBase):
    def test_status_reports_events_other_entries_and_binding_problems(self) -> None:
        report = install.status("claude-code", self.project)
        self.assertEqual((report["exists"], report["installed"]), (False, False))
        self.settings("claude-code").parent.mkdir()
        self.settings("claude-code").write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN]}]}}))
        self.install("claude-code")
        report = install.status("claude-code", self.project)
        self.assertTrue(report["installed"])
        self.assertEqual((report["other_entries"], report["bindings"], report["binding_problems"]),
                         (1, [str(self.binding)], []))
        self.assertIn("not verified", report["native_effect"])
        self.binding.write_text("{}")
        self.assertEqual(len(install.status("claude-code", self.project)["binding_problems"]), 1)
        install.uninstall("claude-code", self.project)
        self.assertFalse(install.status("claude-code", self.project)["installed"])

    def test_partial_installation_is_visible(self) -> None:
        self.install("claude-code")
        document = self.load("claude-code")
        del document["hooks"]["Stop"]
        self.settings("claude-code").write_text(json.dumps(document))
        report = install.status("claude-code", self.project)
        self.assertEqual((report["installed"], report["partial"], report["events"]["Stop"]), (False, True, False))


class Refusals(InstallBase):
    def test_invalid_existing_content_is_never_overwritten(self) -> None:
        self.settings("claude-code").parent.mkdir()
        for content in ("{not json", "[]", json.dumps({"hooks": []}), json.dumps({"hooks": {"Stop": {}}})):
            with self.subTest(content):
                self.settings("claude-code").write_text(content)
                with self.assertRaises(InstallError):
                    self.install("claude-code")
                self.assertEqual(self.settings("claude-code").read_text(), content)

    def test_home_directory_and_global_dirs_are_refused(self) -> None:
        for agent in ("claude-code", "codex"):
            with self.subTest(agent), self.assertRaises(InstallError):
                install.install(agent, self.home, self.binding, PYTHON_CLI)
        (self.home / ".claude").mkdir()
        self.assertEqual(tree(self.home), [".claude"])
        # a project directory that is itself the global agent directory
        with self.assertRaises(InstallError):
            install.install("claude-code", self.home / ".claude", self.binding, PYTHON_CLI)
        self.assertEqual(tree(self.home), [".claude"])

    def test_symlinked_config_dir_is_refused(self) -> None:
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        (self.project / ".claude").symlink_to(elsewhere)
        with self.assertRaises(InstallError):
            self.install("claude-code")
        self.assertEqual(tree(elsewhere), [])

    def test_unusable_binding_and_unknown_agent_and_missing_project(self) -> None:
        with self.assertRaises(InstallError):
            install.install("claude-code", self.project, self.binding.with_name("missing.json"), PYTHON_CLI)
        with self.assertRaises(InstallError):
            install.install("nobody", self.project, self.binding, PYTHON_CLI)
        with self.assertRaises(InstallError):
            install.install("codex", self.project / "nope", self.binding, PYTHON_CLI)
        self.assertFalse((self.project / ".claude").exists())


class Cli(InstallBase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "jasmine_core.cli", "hooks", *args], text=True, capture_output=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=60)

    def test_install_status_uninstall_via_the_cli(self) -> None:
        common = ["--agent", "claude-code", "--project-dir", str(self.project)]
        self.assertEqual(self.cli("status", *common).returncode, 1)
        done = self.cli("install", *common, "--binding", str(self.binding), "--command", PYTHON_CLI)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["result"], "installed")
        self.assertEqual(self.cli("status", *common).returncode, 0)
        self.assertEqual(self.cli("install", *common).returncode, 2)  # --binding is required
        done = self.cli("uninstall", *common)
        self.assertEqual(json.loads(done.stdout)["removed"], 7)
        self.assertEqual(self.cli("install", "--agent", "codex", "--project-dir", str(self.home),
                                  "--binding", str(self.binding)).returncode, 2)
        self.assertEqual(tree(self.home), [])


if __name__ == "__main__":
    unittest.main()
