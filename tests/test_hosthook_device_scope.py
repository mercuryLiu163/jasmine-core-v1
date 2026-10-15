"""Device-level hook registration and bindings (ADR 0019).

Scratch directories only: HOME and the agents' config-dir variables are redirected, so a write to the
real user configuration would show up as a failure. Component level: nothing here shows that
Claude Code, Codex or Grok Build load or run the registered entries.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401

from jasmine_core import ids
from jasmine_core.hosthook import install, runtime
from jasmine_core.hosthook.binding import BindingError, load_binding, load_device
from jasmine_core.hosthook.install import InstallError

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CLI = shlex.join([sys.executable, "-m", "jasmine_core.cli"])
NOW = datetime(2026, 10, 13, 8, 30, 15, tzinfo=timezone.utc)
FOREIGN = {"type": "command", "command": "/usr/local/bin/my-own-hook --flag", "timeout": 5}


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="jasmine-device-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {"CLAUDE_CONFIG_DIR": str(self.base / "cc"), "CODEX_HOME": str(self.base / "cx"),
                    "GROK_HOME": str(self.base / "gk")}
        patcher = patch.dict(os.environ, {"HOME": str(self.home), **self.env})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.private = self.base / "private"
        self.private.mkdir(mode=0o700)
        key = self.private / "system.key"
        key.write_text("token\n")
        key.chmod(0o600)
        self.device = self.base / "device" / "device-binding.json"
        self.device.parent.mkdir()
        self.write_device()

    def write_device(self, **changes) -> dict:
        data = {"version": 1, "core_url": "http://127.0.0.1:9", "host_id": ids.new_id("hst"),
                "api_key_file": str(self.private / "system.key"), "on_unavailable": "deny"}
        data.update(changes)
        data = {k: v for k, v in data.items() if v is not ...}
        self.device.write_text(json.dumps(data))
        return data

    def project(self, name: str = "proj", **data) -> Path:
        directory = self.base / name
        (directory / ".jasmine").mkdir(parents=True, exist_ok=True)
        body = {"version": 1, "project_id": ids.new_id("prj"), "task_id": ids.new_id("tsk"), **data}
        (directory / ".jasmine" / "host-binding.json").write_text(json.dumps(body))
        return directory

    def user_file(self, agent: str) -> Path:
        return install.user_target(install._dialect(agent), dict(self.env))

    def load(self, agent: str) -> dict:
        return json.loads(self.user_file(agent).read_text())


class Bindings(Scratch):
    def test_project_file_holds_ownership_only_and_the_device_supplies_the_rest(self) -> None:
        directory = self.project()
        binding = load_binding(directory / ".jasmine" / "host-binding.json", self.device)
        self.assertEqual(binding.core_url, "http://127.0.0.1:9")
        self.assertEqual((binding.device_path, binding.on_unavailable), (self.device, "deny"))
        self.assertEqual(binding.spool_dir, self.device.parent / "spool")
        self.assertTrue(binding.project_id and binding.task_id)

    def test_a_project_file_cannot_redirect_the_device(self) -> None:
        for field, value in (("core_url", "http://127.0.0.1:1"), ("api_key_file", "/tmp/x"), ("on_unavailable", "allow"),
                             ("host_id", ids.new_id("hst")), ("spool_dir", "/tmp/s")):
            with self.subTest(field):
                directory = self.project("p-" + field, **{field: value})
                with self.assertRaises(BindingError) as caught:
                    load_binding(directory / ".jasmine" / "host-binding.json", self.device)
                self.assertIn(field, str(caught.exception))

    def test_project_can_withhold_the_pack_but_not_force_it_on(self) -> None:
        path = self.project("a", inject_context=False) / ".jasmine" / "host-binding.json"
        self.assertFalse(load_binding(path, self.device).inject_context)
        self.write_device(inject_context=False)
        path = self.project("b", inject_context=True) / ".jasmine" / "host-binding.json"
        self.assertFalse(load_binding(path, self.device).inject_context)

    def test_device_file_is_validated_on_its_own(self) -> None:
        self.assertEqual(load_device(self.device).project_id, None)
        self.write_device(project_id=ids.new_id("prj"))
        with self.assertRaises(BindingError):
            load_device(self.device)
        self.write_device(core_url="http://example.com:80")
        with self.assertRaises(BindingError):
            load_device(self.device)

    def test_full_project_bindings_still_work_without_a_device(self) -> None:
        directory = self.project("legacy", core_url="http://127.0.0.1:9", host_id=ids.new_id("hst"),
                                 api_key_file=str(self.private / "system.key"))
        self.assertIsNone(load_binding(directory / ".jasmine" / "host-binding.json").device_path)


class DeviceRuntime(Scratch):
    def hook(self, cwd: Path, name: str = "pre_tool_use_bash", device=...):
        from test_hosthook_dialect_grok_build import fixture
        native = fixture(name)
        native["cwd"] = native["workspaceRoot"] = str(cwd)
        out, err = io.StringIO(), io.StringIO()
        code = runtime.run_hook("grok-build", json.dumps(native).encode(), stdout=out, stderr=err, environ={},
                                now=lambda: NOW, device_binding=str(self.device) if device is ... else device)
        return out.getvalue(), code, err.getvalue()

    def test_a_directory_without_a_project_file_is_left_alone(self) -> None:
        bare = self.base / "elsewhere"
        bare.mkdir()
        for name in ("pre_tool_use_bash", "user_prompt_submit", "post_tool_use_bash", "stop"):
            self.assertEqual(self.hook(bare, name), ("", 0, ""), name)
        self.assertFalse((self.device.parent / "spool").exists())  # not even a spool entry

    def test_a_bound_directory_with_core_down_fails_closed_with_the_device_policy(self) -> None:
        out, code, err = self.hook(self.project())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("not checked", err)
        # the gap record lands in the device spool
        self.assertTrue(any((self.device.parent / "spool").iterdir()))
        self.write_device(on_unavailable="allow")
        self.assertEqual(self.hook(self.project("p2"), "pre_tool_use_edit")[0], "")

    def test_a_missing_device_file_with_a_bound_project_denies_rather_than_passes(self) -> None:
        directory = self.project()
        self.device.unlink()
        out, _, err = self.hook(directory)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("binding unusable", err)

    def test_a_project_file_that_tries_to_redirect_is_unusable_and_denied(self) -> None:
        directory = self.project(core_url="http://127.0.0.1:1")
        out, _, err = self.hook(directory)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("core_url", err)


try:
    from test_hosthook_runtime import RuntimeBase
except ImportError:  # pragma: no cover
    RuntimeBase = None

if RuntimeBase is not None:
    class DeviceThroughCore(RuntimeBase):
        """One device binding, a thin project file, a real HTTP Core."""

        def setUp(self) -> None:
            super().setUp()
            self.device = self.project_dir / "device" / "device-binding.json"
            self.device.parent.mkdir()
            self.device.write_text(json.dumps({
                "version": 1, "core_url": f"http://127.0.0.1:{self.port}", "host_id": self.write_binding()["host_id"],
                "api_key_file": str(self.system_key), "human_api_key_file": str(self.human_key),
                "on_unavailable": "deny"}))
            self.binding_path.write_text(json.dumps({"version": 1, "project_id": self.project, "task_id": self.task,
                                                    "step_id": self.step}))

        def fire(self, agent: str, native: dict):
            out, err = io.StringIO(), io.StringIO()
            runtime.run_hook(agent, json.dumps({**native, "cwd": str(self.project_dir)}).encode(), stdout=out,
                             stderr=err, environ={}, now=lambda: NOW, device_binding=str(self.device))
            return out.getvalue(), err.getvalue()

        def test_the_same_device_binding_serves_every_dialect_and_the_rule_still_denies(self) -> None:
            from test_hosthook_dialect_claude_code import fixture as cc
            from test_hosthook_dialect_codex import fixture as cx
            from test_hosthook_dialect_grok_build import fixture as gb
            self.rule("no-rm", "DENY", {"tool_category": "shell", "action": "rm", "path_prefix": "/protected"})
            for agent, native in (("codex", cx("pre_tool_use_bash")), ("claude-code", cc("pre_tool_use_bash")),
                                  ("grok-build", gb("pre_tool_use_bash"))):
                with self.subTest(agent):
                    out, err = self.fire(agent, native)
                    self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny", err)

        def test_prompts_use_the_device_human_key_and_context_comes_back(self) -> None:
            from test_hosthook_dialect_codex import fixture as cx
            out, err = self.fire("codex", cx("user_prompt_submit"))
            self.assertIn("Hook goal", json.loads(out)["hookSpecificOutput"]["additionalContext"], err)
            row = self.db_rows("SELECT actor_kind FROM events WHERE event_type='user.prompt' "
                               "AND source_system='host-hook-v1'")[0]
            self.assertEqual(row["actor_kind"], "human")

        def test_spooled_events_replay_through_the_device_binding(self) -> None:
            from test_hosthook_dialect_codex import fixture as cx
            live = json.loads(self.device.read_text())
            self.device.write_text(json.dumps({**live, "core_url": f"http://127.0.0.1:{self.dead_port()}"}))
            self.fire("codex", cx("post_tool_use_bash"))
            self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)
            self.device.write_text(json.dumps(live))
            out = io.StringIO()
            code = runtime.run_replay(None, None, None, str(self.device), environ={}, stdout=out, stderr=io.StringIO())
            self.assertEqual((code, json.loads(out.getvalue())["sent"]), (0, 1))
            self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)


class UserScopeInstall(Scratch):
    def install(self, agent: str, **kw):
        return install.install_user(agent, self.device, PYTHON_CLI, confirmed=True, environ=dict(self.env), **kw)

    def owned(self, agent: str):
        out = []
        for event, groups in self.load(agent)["hooks"].items():
            for group in groups:
                for handler in group["hooks"]:
                    if install.owns(handler["command"], agent):
                        out.append((event, handler))
        return out

    def test_it_refuses_without_explicit_confirmation_and_writes_nothing(self) -> None:
        with self.assertRaises(InstallError) as caught:
            install.install_user("codex", self.device, PYTHON_CLI, environ=dict(self.env))
        self.assertIn("--confirm-user-scope", str(caught.exception))
        self.assertEqual(list(self.base.glob("cx*")), [])

    def test_the_users_own_hooks_stay_and_a_backup_is_kept(self) -> None:
        path = self.user_file("claude-code")
        path.parent.mkdir(parents=True)
        mine = {"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}],
                                        "Notification": [{"hooks": [FOREIGN]}]}}
        path.write_text(json.dumps(mine))
        before = path.read_bytes()
        result = self.install("claude-code")
        document = self.load("claude-code")
        self.assertEqual(document["model"], "x")
        self.assertEqual(document["hooks"]["Notification"], mine["hooks"]["Notification"])
        self.assertEqual(document["hooks"]["PreToolUse"][0], mine["hooks"]["PreToolUse"][0])  # theirs first, intact
        self.assertEqual(len(document["hooks"]["PreToolUse"]), 2)
        backup = Path(result["backup"])
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(result["scope"], "user")
        for event, handler in self.owned("claude-code"):
            self.assertIn("--device-binding", handler["command"])
            self.assertNotIn("--binding ", handler["command"])

    def test_idempotent_and_uninstall_removes_only_what_it_owns(self) -> None:
        path = self.user_file("codex")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [FOREIGN]}]}}))
        self.install("codex")
        self.install("codex")
        self.assertEqual(len(self.owned("codex")), 8)
        self.assertEqual(len(self.load("codex")["hooks"]["Stop"]), 2)
        result = install.uninstall_user("codex", environ=dict(self.env))
        self.assertEqual((result["removed"], result["scope"]), (8, "user"))
        self.assertEqual(self.load("codex"), {"hooks": {"Stop": [{"hooks": [FOREIGN]}]}})
        self.assertTrue(Path(result["backup"]).exists())
        self.assertEqual(install.uninstall_user("codex", environ=dict(self.env))["result"], "not_installed")

    def test_uninstall_leaves_no_empty_skeleton_behind(self) -> None:
        # a settings file without any hooks goes back to exactly its own keys
        claude = self.user_file("claude-code")
        claude.parent.mkdir(parents=True)
        claude.write_text(json.dumps({"model": "opus", "theme": "auto"}))
        self.install("claude-code")
        self.assertEqual(install.uninstall_user("claude-code", environ=dict(self.env))["removed"], 7)
        self.assertEqual(self.load("claude-code"), {"model": "opus", "theme": "auto"})
        # Codex keeps the user's own (empty) `hooks` key and description
        codex = self.user_file("codex")
        codex.parent.mkdir(parents=True)
        original = {"description": "mine", "hooks": {}}
        codex.write_text(json.dumps(original))
        self.install("codex")
        install.uninstall_user("codex", environ=dict(self.env))
        self.assertEqual(self.load("codex"), original)
        # a file that only we created disappears again
        grok = self.user_file("grok-build")
        self.install("grok-build")
        self.assertTrue(grok.exists())
        result = install.uninstall_user("grok-build", environ=dict(self.env))
        self.assertTrue(result.get("file_removed"))
        self.assertFalse(grok.exists())

    def test_grok_gets_its_own_file_and_other_files_are_untouched(self) -> None:
        hooks = self.user_file("grok-build").parent
        hooks.mkdir(parents=True)
        mine = hooks / "mine.json"
        mine.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [FOREIGN]}]}}))
        before = mine.read_bytes()
        result = self.install("grok-build")
        self.assertEqual(mine.read_bytes(), before)
        self.assertNotIn("backup", result)  # nothing existed to save
        self.assertEqual({e for e, _ in self.owned("grok-build")}, {"SessionStart", "UserPromptSubmit", "PreToolUse",
                         "PostToolUse", "PreCompact", "PostCompact", "Stop", "SessionEnd"})

    def test_status_reports_the_user_scope_and_the_device_binding(self) -> None:
        self.assertFalse(install.status_user("claude-code", environ=dict(self.env))["installed"])
        self.install("claude-code")
        report = install.status_user("claude-code", environ=dict(self.env))
        self.assertEqual((report["installed"], report["scope"], report["bindings"]), (True, "user", [str(self.device)]))
        self.assertEqual(report["binding_problems"], [])
        self.device.unlink()
        self.assertTrue(install.status_user("claude-code", environ=dict(self.env))["binding_problems"])

    def test_grok_and_claude_code_together_are_flagged(self) -> None:
        first = self.install("claude-code")
        self.assertEqual(first["notes"], [])
        second = self.install("grok-build")
        self.assertTrue(any("grok-build also loads the claude-code user hook file" in n for n in second["notes"]), second)
        again = self.install("claude-code")
        self.assertTrue(any("grok-build also loads" in n for n in again["notes"]))

    def test_unusable_device_binding_and_symlinks_are_refused(self) -> None:
        self.device.write_text("{}")
        with self.assertRaises(InstallError):
            self.install("codex")
        self.write_device()
        (self.base / "real").mkdir()
        os.symlink(self.base / "real", self.base / "cx")
        with self.assertRaises(InstallError):
            self.install("codex")
        self.assertEqual(list((self.base / "real").iterdir()), [])

    def test_without_the_agents_env_var_the_default_is_under_home(self) -> None:
        for name in self.env:
            os.environ.pop(name, None)
        self.assertEqual(install.user_target(install._dialect("codex"), None), self.home / ".codex" / "hooks.json")
        self.assertEqual(install.user_target(install._dialect("grok-build"), None),
                         self.home / ".grok" / "hooks" / "jasmine-core.json")

    def test_cli_install_status_uninstall_with_redirected_config_dirs(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

        def cli(*args):
            return subprocess.run([sys.executable, "-m", "jasmine_core.cli", "hooks", *args, "--agent", "codex",
                                   "--scope", "user"], text=True, capture_output=True, env=env, timeout=60)

        refused = cli("install", "--device-binding", str(self.device))
        self.assertEqual(refused.returncode, 2)
        done = cli("install", "--device-binding", str(self.device), "--confirm-user-scope", "--command", PYTHON_CLI)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(cli("status").returncode, 0)
        self.assertEqual(json.loads(cli("uninstall").stdout)["removed"], 8)
        self.assertEqual(list(self.home.iterdir()), [], "the real HOME must stay untouched")


if __name__ == "__main__":
    unittest.main()
