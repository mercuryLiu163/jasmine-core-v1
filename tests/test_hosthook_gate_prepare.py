"""`scripts/hosthook-real-conversation.py --prepare` (never `--run`).

The CLI path given to the script is a stand-in file that would leave a marker if it were ever
executed, so these tests also show that --prepare launches no agent. HOME is redirected to an empty
scratch directory. Nothing here is a Gate result.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from jasmine_core.hosthook import install

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hosthook-real-conversation.py"


class Prepare(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.marker = self.base / "cli-was-executed"
        self.cli = self.base / "fake-cli"
        self.cli.write_text(f"#!/bin/sh\ntouch '{self.marker}'\n")
        self.cli.chmod(0o755)

    def prepare(self, agent: str, out: Path | str, *extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(ROOT / "src")}
        return subprocess.run([sys.executable, str(SCRIPT), "--agent", agent, "--prepare", "--out", str(out),
                               "--cli-bin", str(self.cli), *extra], text=True, capture_output=True, env=env, timeout=120)

    def test_prepare_builds_a_private_scratch_fixture_for_each_agent(self) -> None:
        for agent, config in (("claude-code", ".claude/settings.json"), ("codex", ".codex/hooks.json"),
                              ("grok-build", ".grok/hooks/jasmine-core.json")):
            with self.subTest(agent):
                out = self.base / f"run-{agent}"
                done = self.prepare(agent, out)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
                report = json.loads(done.stdout)
                self.assertEqual((report["verdict"], report["mode"]), ("PREPARED", "prepare"))
                self.assertIn("unverified", report["reason"])
                self.assertEqual(report["cli"]["path"], str(self.cli))
                self.assertEqual(len(report["cli"]["sha256"]), 64)
                self.assertIn("not detected", report["cli_version"])
                self.assertTrue(any("5. Run:" in step and "--run" in step for step in report["manual_steps"]))
                if agent == "codex":
                    self.assertTrue(any("/hooks" in step for step in report["manual_steps"]))
                if agent == "grok-build":
                    self.assertTrue(any("/hooks-trust" in step for step in report["manual_steps"]))
                # private files
                for name in ("manifest.json", "prepare-report.json", "private/system.key", "private/human.key",
                             "project/.jasmine/host-binding.json"):
                    self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), 0o600, name)
                self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
                # project-level registration only, in the scratch project
                self.assertEqual(report["hook_config"], str(out / "project" / config))
                self.assertTrue(install.status(agent, out / "project")["installed"])
                # binding: no token, fail-closed, keys by path
                binding = json.loads((out / "project/.jasmine/host-binding.json").read_text())
                token = (out / "private/system.key").read_text()
                self.assertNotIn(token, json.dumps(binding))
                self.assertEqual(binding["on_unavailable"], "deny")
                # scratch DB: an ACTIVE deny rule, no host-hook traffic, and no Core left running
                conn = sqlite3.connect(out / "core.db")
                try:
                    self.assertEqual(conn.execute("SELECT count(*) FROM host_hook_deliveries").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT count(*) FROM rules").fetchone()[0], 1)
                    # the gate rule is agent-neutral (ADR 0018): it must not name any agent's tool
                    matcher = json.loads(conn.execute("SELECT matcher_json FROM rule_versions").fetchone()[0])
                    self.assertEqual(matcher["tool_category"], "shell")
                    self.assertNotIn("tool", matcher)
                finally:
                    conn.close()
                port = report["core_port"]
                import socket
                with socket.socket() as sock:
                    sock.settimeout(0.5)
                    self.assertNotEqual(sock.connect_ex(("127.0.0.1", port)), 0, "scratch Core must be stopped")
        self.assertFalse(self.marker.exists(), "--prepare must not execute the agent CLI")
        self.assertEqual(list(self.home.iterdir()), [], "nothing may be written to HOME")

    def test_user_scope_prepare_uses_a_device_binding_and_only_the_redirected_config(self) -> None:
        config = self.base / "agent-config"
        env = {"CODEX_HOME": str(config)}
        base_env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(ROOT / "src"), **env}
        out = self.base / "run-user"
        args = [sys.executable, str(SCRIPT), "--agent", "codex", "--prepare", "--out", str(out), "--cli-bin", str(self.cli),
                "--hook-scope", "user"]
        refused = subprocess.run(args, text=True, capture_output=True, env=base_env, timeout=120)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("--confirm-user-scope", json.loads(refused.stdout)["reason"])
        self.assertFalse(config.exists())
        done = subprocess.run([*args, "--confirm-user-scope"], text=True, capture_output=True, env=base_env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        report = json.loads(done.stdout)
        self.assertEqual(report["hook_config"], str(config / "hooks.json"))
        self.assertTrue(any("USER-LEVEL" in step and "hooks uninstall" in step for step in report["manual_steps"]))
        thin = json.loads((out / "project/.jasmine/host-binding.json").read_text())
        self.assertEqual(set(thin), {"version", "project_id", "task_id", "step_id"})
        device = json.loads((out / "device/device-binding.json").read_text())
        self.assertNotIn("project_id", device)
        self.assertEqual(stat.S_IMODE((out / "device/device-binding.json").stat().st_mode), 0o600)
        self.assertIn("--device-binding", (config / "hooks.json").read_text())
        self.assertEqual(list(self.home.iterdir()), [], "the real HOME must stay untouched")

    def test_the_nonce_is_only_in_the_database_and_manifest(self) -> None:
        out = self.base / "run"
        self.assertEqual(self.prepare("claude-code", out).returncode, 0)
        nonce = json.loads((out / "manifest.json").read_text())["nonce"]
        hits = [str(p) for p in (out / "project").rglob("*") if p.is_file() and ".git" not in p.parts
                and nonce.encode() in p.read_bytes()]
        self.assertEqual(hits, [])
        report = (out / "prepare-report.json").read_text()
        self.assertNotIn(nonce, report)

    def test_refusals(self) -> None:
        out = self.base / "taken"
        out.mkdir()
        done = self.prepare("codex", out)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout)["verdict"], "BLOCKED")
        self.assertEqual(self.prepare("codex", "relative/dir").returncode, 2)
        (self.home / ".claude").mkdir()
        (self.home / ".grok").mkdir()
        self.assertEqual(self.prepare("grok-build", self.home / ".grok" / "gate").returncode, 2)
        self.assertEqual(self.prepare("claude-code", self.home / ".claude" / "gate").returncode, 2)
        self.assertEqual(self.prepare("claude-code", self.base / "x", "--cli-bin", str(self.base / "missing")).returncode, 2)
        self.assertFalse((self.home / ".claude" / "gate").exists())
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
