"""Installer and wrapper contract tests using only throwaway checkout/state."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
PYTHON = (sys.executable if sys.version_info >= (3, 11) else
          next(str(p) for p in (Path("/opt/homebrew/bin/python3.13"),
                                Path("/opt/homebrew/bin/python3.12"),
                                Path("/opt/homebrew/bin/python3.11")) if p.exists()))


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=60, **kwargs)


class HookScripts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jasmine-hook-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "checkout with space"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / ".codex").mkdir()
        shutil.copytree(REPO / "src", self.repo / "src")
        for name in ("p0-codex-hook-install.sh", "jasmine-capture-hook.sh"):
            shutil.copy2(REPO / "scripts" / name, self.repo / "scripts" / name)
        self.installer = self.repo / "scripts/p0-codex-hook-install.sh"
        self.wrapper = self.repo / "scripts/jasmine-capture-hook.sh"
        self.hooks = self.repo / ".codex/hooks.json"
        self.home = self.base / "home"
        (self.home / ".codex").mkdir(parents=True)
        self.global_hooks = self.home / ".codex/hooks.json"
        self.global_hooks.write_bytes(b'{"hooks":{"UserPromptSubmit":[]}}\n')
        self.config = self.home / ".codex/config.toml"
        self.config.write_bytes(b'# no trust\n')
        self.state = self.base / "state"
        self.state.mkdir()
        self.db = self.state / "core.db"
        self.token = self.state / "capture-token"
        self.token.write_text("t" * 43)
        self.token.chmod(0o600)
        self.env = dict(os.environ, HOME=str(self.home), JASMINE_CORE_DB=str(self.db),
                        JASMINE_CORE_STATE_DIR=str(self.state), PYTHONPATH=str(self.repo / "src"))
        boot = run([PYTHON, "-m", "jasmine_core.cli", "bootstrap",
                    "--scopes", "events:write", "events:read"], env=self.env,
                   cwd=self.repo)
        self.assertEqual(boot.returncode, 0, boot.stderr)

    def install(self, *args, env=None):
        return run([str(self.installer), "--python", PYTHON, *args],
                   env=env or self.env, cwd=self.repo)

    def test_scripts_parse_and_are_executable(self):
        for script in (self.installer, self.wrapper):
            self.assertTrue(os.access(script, os.X_OK))
            self.assertEqual(run(["bash", "-n", str(script)]).returncode, 0)

    def test_dry_run_changes_no_files_or_permissions(self):
        self.wrapper.chmod(0o644)
        before = self.wrapper.stat().st_mode
        failed = self.install("--dry-run")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.wrapper.stat().st_mode, before)
        self.wrapper.chmod(0o755)
        db_before = {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        global_before = (self.global_hooks.read_bytes(), self.config.read_bytes())
        result = self.install("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would-write", result.stdout)
        self.assertFalse(self.hooks.exists())
        self.assertEqual(db_before, {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()})
        self.assertEqual(global_before, (self.global_hooks.read_bytes(), self.config.read_bytes()))

    def test_install_preserves_other_handlers_and_token_stays_out(self):
        other = {"hooks": [{"type": "command", "command": "echo other"}]}
        self.hooks.write_text(json.dumps({"hooks": {"UserPromptSubmit": [other],
                                                 "Stop": [other]}}))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.hooks.read_text())
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0], other)
        self.assertEqual(data["hooks"]["Stop"], [other])
        command = data["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"]
        argv = shlex.split(command)
        self.assertIn("--python", argv)
        expected_python = run([PYTHON, "-c", "import sys; print(sys.executable)"]).stdout.strip()
        self.assertEqual(argv[argv.index("--python") + 1], expected_python)
        self.assertIn(str(self.wrapper), command)
        self.assertNotIn(self.token.read_text(), self.hooks.read_text())
        self.assertEqual(self.install().returncode, 0)
        self.assertIn("unchanged", self.install().stdout)
        self.assertEqual((self.global_hooks.read_bytes(), self.config.read_bytes()),
                         (b'{"hooks":{"UserPromptSubmit":[]}}\n', b'# no trust\n'))

    def test_force_preserves_other_hooks_in_shared_entry(self):
        other_hook = {"type": "command", "command": "echo keep"}
        mine = {"type": "command", "command": "'/old/jasmine-capture-hook.sh' --host stale"}
        shared = {"matcher": "", "hooks": [other_hook, mine]}
        self.hooks.write_text(json.dumps({"hooks": {"UserPromptSubmit": [shared]}}))
        result = self.install("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(self.hooks.read_text())["hooks"]["UserPromptSubmit"]
        self.assertEqual(entries[0], {"matcher": "", "hooks": [other_hook]})
        self.assertEqual(len(entries), 2)

    def test_changed_entry_refused_then_force_backs_up_original_bytes(self):
        self.assertEqual(self.install().returncode, 0)
        data = json.loads(self.hooks.read_text())
        data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] += " --old"
        stale = json.dumps(data, separators=(",", ":")).encode()
        self.hooks.write_bytes(stale)
        before = self.hooks.stat()
        refused = self.install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--force", refused.stderr)
        self.assertEqual(self.hooks.read_bytes(), stale)
        replaced = self.install("--force")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        backups = list(self.hooks.parent.glob("hooks.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), stale)
        self.assertEqual(self.hooks.stat().st_mode & 0o777, before.st_mode & 0o777)

    def test_global_and_outside_target_are_refused(self):
        before = self.global_hooks.read_bytes()
        for args in (("--global",), ("--hooks-json", str(self.global_hooks)),
                     ("--hooks-json", str(self.base / "outside.json"))):
            with self.subTest(args=args):
                result = self.install(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.global_hooks.read_bytes(), before)
                self.assertFalse((self.base / "outside.json").exists())

    def test_symlinked_project_codex_directory_cannot_write_global_hooks(self):
        (self.repo / ".codex").rmdir()
        (self.repo / ".codex").symlink_to(self.home / ".codex", target_is_directory=True)
        before = (self.global_hooks.read_bytes(), self.config.read_bytes())
        for args in (("--dry-run",), ()):
            with self.subTest(args=args):
                result = self.install(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlinked project .codex", result.stderr)
                self.assertEqual((self.global_hooks.read_bytes(), self.config.read_bytes()), before)
                self.assertEqual(list((self.home / ".codex").glob("hooks.json.bak.*")), [])

    def test_nonempty_wal_is_refused_without_changing_it(self):
        wal = Path(str(self.db) + "-wal")
        wal.write_bytes(b"pending WAL bytes")
        before = wal.read_bytes()
        result = self.install("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active WAL", result.stderr)
        self.assertEqual(wal.read_bytes(), before)
        self.assertFalse(self.hooks.exists())

    def test_pinned_interpreter_does_not_fallback(self):
        nonexistent = self.base / "removed-python"
        result = run([str(self.wrapper), "--python", str(nonexistent),
                      "--state-dir", str(self.state)], env=self.env, input="{}")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertIn("configured --python", result.stderr)
        self.assertFalse((self.state / "hook-invocations.log").exists())

    def test_missing_token_and_old_interpreter_are_refused(self):
        self.token.unlink()
        self.assertIn("no capture token", self.install().stderr)
        self.token.write_text("t" * 43)
        old = self.base / "not-python"
        old.write_text("#!/bin/sh\nexit 0\n")
        old.chmod(0o755)
        result = run([str(self.installer), "--python", str(old)], env=self.env,
                     cwd=self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.hooks.exists())

    def test_wrapper_uses_exact_token_path_and_explicit_interpreter(self):
        (self.state / "something.token").write_text("s" * 43)
        self.token.unlink()
        result = run([str(self.wrapper), "--python", PYTHON,
                      "--state-dir", str(self.state)], env=self.env, input="{}")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertIn("JASMINE_CORE_TOKEN", result.stderr)
        self.assertNotIn("s" * 43, result.stdout + result.stderr)
        trace = self.state / "hook-invocations.log"
        self.assertTrue(trace.is_file())


if __name__ == "__main__":
    unittest.main()
