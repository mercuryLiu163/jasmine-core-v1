"""The two operator-facing scripts: the hook installer and the hook wrapper.

These are the scripts a person runs, so they are tested for the things that would
silently produce a wrong result: writing a token into a hooks file, quoting a
path badly, claiming to be idempotent while rewriting, or touching the global
configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401  (path setup)

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "p0-codex-hook-install.sh"
WRAPPER = REPO / "scripts" / "jasmine-capture-hook.sh"


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kwargs)


class ScriptShape(unittest.TestCase):
    def test_both_scripts_exist_and_are_executable(self) -> None:
        for script in (INSTALLER, WRAPPER):
            with self.subTest(script=script.name):
                self.assertTrue(script.is_file(), script)
                self.assertTrue(os.access(script, os.X_OK), f"{script} is not executable")

    def test_both_scripts_parse_as_shell(self) -> None:
        for script in (INSTALLER, WRAPPER):
            with self.subTest(script=script.name):
                result = run(["bash", "-n", str(script)])
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_they_declare_strict_mode(self) -> None:
        for script in (INSTALLER, WRAPPER):
            with self.subTest(script=script.name):
                self.assertIn("set -uo pipefail", script.read_text())


class InstallerNeverTouchesGlobalState(unittest.TestCase):
    """A project-local entry is the whole point; a global one would not be."""

    def setUp(self) -> None:
        self.installer = INSTALLER.read_text()

    def test_the_installer_states_it_will_not_touch_the_global_configuration(self) -> None:
        # The promise is also made in the text the operator reads, so a future
        # change that breaks it contradicts what this file says.
        self.assertIn("will NOT", self.installer)
        self.assertIn("compute, guess or inject a trusted_hash", self.installer)
        self.assertIn("Trusting the entry is a decision", self.installer)

    def test_the_default_target_is_project_local(self) -> None:
        self.assertIn('HOOKS_JSON="$ROOT/.codex/hooks.json"', self.installer)
        # A global target must be opt-in, not the default.
        self.assertIn("--global", self.installer)
        self.assertIn("asks first", self.installer)


class InstallerBehaviour(unittest.TestCase):
    """Exercise the installer against a throwaway HOME and a real checkout."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-installer-")
        root = Path(cls._tmp.name)
        cls.home = root / "home"
        cls.home.mkdir()
        (cls.home / ".codex").mkdir()
        (cls.home / ".codex" / "config.toml").write_text(
            '[hooks.state]\n\n'
            '[hooks.state."/Users/mercuryliu/.codex/hooks.json:user_prompt_submit:0:0"]\n'
            'trusted_hash = "sha256:deadbeef"\n', encoding="utf-8")
        (cls.home / ".codex" / "hooks.json").write_text(json.dumps({
            "description": "Local Codex lifecycle hooks",
            "hooks": {"UserPromptSubmit": [{"hooks": [{
                "type": "command",
                "command": "/usr/bin/python3 '/Users/other/jasmine_memory/codex_hook.py'",
                "timeout": 8, "additionalContextLimit": 1500}]}]},
        }, indent=2), encoding="utf-8")

        cls.state_root = root
        cls.db = root / "core.db"
        cls.token = root / "capture-token"
        env = dict(os.environ, HOME=str(cls.home), JASMINE_CORE_DB=str(cls.db),
                   JASMINE_CORE_STATE_DIR=str(root))
        cls.token.write_text("t" * 43, encoding="utf-8")
        cls.token.chmod(0o600)
        boot = run([sys.executable, "-m", "jasmine_core.cli", "bootstrap",
                    "--scopes", "events:write", "events:read"],
                   env=dict(env, PYTHONPATH=str(REPO / "src")), cwd=REPO)
        assert boot.returncode == 0, boot.stderr
        cls.bootstrap = json.loads(boot.stdout)
        cls.env = env
        cls.hooks_json = REPO / ".codex" / "hooks.json"
        cls._had_hooks = cls.hooks_json.is_file()
        if cls._had_hooks:
            cls._hooks_backup = cls.hooks_json.read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._had_hooks:
            cls.hooks_json.write_bytes(cls._hooks_backup)
        elif cls.hooks_json.is_file():
            cls.hooks_json.unlink()
        for stray in REPO.glob(".codex/hooks.json.bak.*"):
            stray.unlink()
        for stray in REPO.glob("*.bak.*"):
            stray.unlink()
        cls._tmp.cleanup()

    def install(self, *args) -> subprocess.CompletedProcess:
        return run([str(INSTALLER), *args], env=dict(self.env, PYTHONPATH=str(REPO / "src")),
                   cwd=REPO)

    def test_a_dry_run_writes_nothing(self) -> None:
        if self.hooks_json.is_file():
            self.hooks_json.unlink()
        result = self.install("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would-write", result.stdout)
        self.assertFalse(self.hooks_json.exists())

    def test_it_installs_a_quoted_path_only_entry(self) -> None:
        result = self.install("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.hooks_json.read_text(encoding="utf-8"))
        entry = config["hooks"]["UserPromptSubmit"][-1]["hooks"][0]
        command = entry["command"]
        # Every path with a space in it must be single-quoted, because Codex
        # runs the command through a shell.
        for token in command.split():
            if " " in token or token.startswith("/"):
                self.assertTrue(token.startswith("'") or "'" in token, token)
        self.assertIn(str(REPO / "scripts" / "jasmine-capture-hook.sh"), command)
        # No token, and no interpreter path: both are resolved at run time.
        self.assertNotIn(self.token.read_text(), command)
        self.assertNotIn("/opt/homebrew", command)
        self.assertNotIn("python3", command)
        # No matcher: UserPromptSubmit has no tool to match on, so a matcher
        # would be a false promise of scoping.
        self.assertNotIn("matcher", json.dumps(config))

    def test_it_is_idempotent(self) -> None:
        self.install("--force")
        first = self.hooks_json.read_text(encoding="utf-8")
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("unchanged", second.stdout)
        self.assertEqual(self.hooks_json.read_text(encoding="utf-8"), first)

    def test_it_refuses_to_silently_replace_a_stale_entry(self) -> None:
        self.install("--force")
        unchanged = self.install()
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        # A stale entry is still one of ours -- the same wrapper, an old host id.
        # It must be reported and held, not quietly replaced.
        config = json.loads(self.hooks_json.read_text(encoding="utf-8"))
        config["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"] = (
            f"'{REPO}/scripts/jasmine-capture-hook.sh' --host 'hst_stale' --db '/x' "
            f"--state-dir '/y'")
        self.hooks_json.write_text(json.dumps(config, indent=2), encoding="utf-8")
        stale = self.hooks_json.read_text(encoding="utf-8")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--force", result.stderr)
        self.assertEqual(self.hooks_json.read_text(encoding="utf-8"), stale)

    def test_it_creates_no_file_anywhere_else(self) -> None:
        def snapshot() -> dict:
            out = {}
            for base in (self.home, self.state_root):
                for path in sorted(base.rglob("*")):
                    if path.is_file():
                        out[str(path)] = path.read_bytes()
            return out

        before = snapshot()
        self.install("--force")
        after = snapshot()
        created = sorted(set(after) - set(before))
        allowed = [p for p in created if p.endswith(".bak.")]
        unexpected = [p for p in created if p not in allowed]
        self.assertEqual(unexpected, [], f"the installer created {unexpected}")
        for path, content in before.items():
            if path == str(self.hooks_json):
                continue
            self.assertEqual(after[path], content, f"{path} changed")

    def test_it_preserves_the_global_configuration_byte_for_byte(self) -> None:
        global_hooks = self.home / ".codex" / "hooks.json"
        config_toml = self.home / ".codex" / "config.toml"
        before = (global_hooks.read_bytes(), config_toml.read_bytes())
        self.install("--force")
        self.assertEqual((global_hooks.read_bytes(), config_toml.read_bytes()), before)

    def test_it_backs_up_the_file_it_replaces(self) -> None:
        self.install("--force")
        first = self.hooks_json.read_text(encoding="utf-8")
        self.install("--force")
        backups = list(REPO.glob(".codex/hooks.json.bak.*"))
        self.assertTrue(backups)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), first)

    def test_it_reports_a_missing_token_rather_than_proceeding(self) -> None:
        self.token.unlink()
        try:
            result = self.install()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no capture token", result.stderr)
        finally:
            self.token.write_text("t" * 43, encoding="utf-8")
            self.token.chmod(0o600)

    def test_it_reports_a_missing_database_rather_than_proceeding(self) -> None:
        result = self.install("--db", str(REPO / "nope.db"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no core.db", result.stderr)

    def test_it_rejects_a_python_below_the_floor(self) -> None:
        old = self.home / "bin" / "python3"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        old.chmod(0o755)
        result = run([str(INSTALLER), "--python", str(old)], cwd=REPO,
                     env=dict(self.env, PYTHONPATH=str(REPO / "src")))
        self.assertNotEqual(result.returncode, 0)


class WrapperResolvesEverythingAtRunTime(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-wrapper-")
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)

    def test_a_missing_token_still_prints_the_context_object_and_exits_zero(self) -> None:
        result = run([str(WRAPPER), "--state-dir", str(self.state)],
                     env=dict(os.environ, HOME=str(self.state)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertIn("JASMINE_CORE_TOKEN", result.stderr)

    def test_an_interpreter_below_the_floor_is_rejected_not_used(self) -> None:
        # /usr/bin/python3 is 3.9 on macOS. If the hook used it the import would
        # fail and the turn would look like a capture that silently did nothing.
        (self.state / "capture-token").write_text("t" * 43, encoding="utf-8")
        old = "/usr/bin/python3"
        version = run([old, "-c", "import sys; print(sys.version_info[:2])"]).stdout.strip()
        if version.startswith("(3, 9"):
            result = run([str(WRAPPER), "--state-dir", str(self.state),
                          "--host", "hst_x"],
                         env=dict(os.environ, HOME=str(self.state),
                                  JASMINE_PYTHON=old,
                                  JASMINE_CORE_URL="http://127.0.0.1:1"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})

    def test_a_missing_token_still_traces_and_exits_zero(self) -> None:
        result = run([str(WRAPPER), "--state-dir", str(self.state)],
                     env=dict(os.environ, HOME=str(self.state)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        # The Gate tells "the entry never ran" from "the entry ran and could not
        # capture" by this trace line, so it must exist even on the early exits.
        trace = self.state / "hook-invocations.log"
        self.assertTrue(trace.is_file(), "no invocation trace was written")
        self.assertFalse(json.loads(trace.read_text().strip())["captured"])

    def test_the_token_is_exported_only_to_the_child_not_echoed(self) -> None:
        token = "s" * 43
        (self.state / "capture-token").write_text(token, encoding="utf-8")
        result = run([str(WRAPPER), "--state-dir", str(self.state), "--host", "hst_x"],
                     env=dict(os.environ, HOME=str(self.state),
                              JASMINE_CORE_URL="http://127.0.0.1:1"))
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(token, result.stdout)
        self.assertNotIn(token, result.stderr)


if __name__ == "__main__":
    unittest.main()
