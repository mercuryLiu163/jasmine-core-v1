"""P1 Gate runner control flow; mocks never satisfy the real T10 case."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/p1-real-conversation.py"


def module():
    spec = importlib.util.spec_from_file_location("p1_real_conversation", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class RealGateRunner(unittest.TestCase):
    def test_prepare_bootstraps_private_fixture_and_exits_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p1-gate-prepare-") as temp:
            out = Path(temp) / "gate"
            result = subprocess.run([sys.executable, str(SCRIPT), "--prepare", "--out",
                                     str(out), "--project-root", str(ROOT)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads((out / "p1-t10-prepare.json").read_text())
            self.assertEqual((report["verdict"], report["phase"]), ("BLOCKED", "prepared"))
            manifest = json.loads((out / "manifest.json").read_text())
            binding = json.loads((out / "binding.json").read_text())
            self.assertNotIn(binding["run_nonce"], (out / "p1-t10-prepare.json").read_text())
            self.assertEqual((manifest["task_id"], manifest["step_id"]),
                             (binding["task_id"], binding["step_id"]))
            for name in ("binding.json", "human.token", "system.token", "agent.token"):
                self.assertEqual((out / name).stat().st_mode & 0o077, 0)
            self.assertEqual(out.stat().st_mode & 0o077, 0)
            self.assertFalse((out / "hook-trace.jsonl").exists())
            self.assertFalse((out / "binding.json.lease").exists())

    def test_run_without_operator_trust_stays_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p1-gate-blocked-") as temp:
            out = Path(temp) / "gate"
            subprocess.run([sys.executable, str(SCRIPT), "--prepare", "--out", str(out),
                            "--project-root", str(ROOT)], capture_output=True, text=True,
                           timeout=30, check=False)
            result = subprocess.run([sys.executable, str(SCRIPT), "--run", "--out", str(out)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            records = list(out.glob("p1-t10-run-*.json"))
            self.assertEqual(len(records), 1)
            report = json.loads(records[0].read_text())
            self.assertEqual(report["verdict"], "BLOCKED")
            self.assertFalse(report["turns"])
            again = subprocess.run([sys.executable, str(SCRIPT), "--run", "--out", str(out)],
                                   capture_output=True, text=True, timeout=15)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(len(list(out.glob("p1-t10-run-*.json"))), 2,
                             "retry overwrote original Gate failure")

    def test_failure_after_start_stops_only_owned_core(self) -> None:
        gate = module()
        with tempfile.TemporaryDirectory(prefix="p1-gate-cleanup-") as temp:
            out = Path(temp)
            binding = out / "binding.json"
            nonce = "mock-only-nonce-not-a-real-gate-0123456789"
            manifest = {"prepared_commit": "frozen", "project_root": str(ROOT),
                        "workspace": str(out / "workspace"), "db": str(out / "core.db"),
                        "binding": str(binding), "hook_config": str(ROOT / ".codex/hooks.json"),
                        "port": 42123, "project_id": "prj_fixture", "task_id": "tsk_fixture",
                        "step_id": "stp_fixture", "denied": str(out / "denied"),
                        "allowed": str(out / "allowed"), "stale": str(out / "stale"),
                        "denied_command": f"touch {out / 'denied'}",
                        "allowed_command": f"touch {out / 'allowed'}",
                        "rules": [{"rule_id": "rul_denied"}, {"rule_id": "rul_verify"}]}
            gate._write_private(out / "manifest.json", manifest)
            gate._write_private(binding, {"run_nonce": nonce, "core_url": "http://127.0.0.1:42123",
                "project_id": "prj_fixture", "task_id": "tsk_fixture", "step_id": "stp_fixture",
                "host_id": "hst_fixture", "token_file": str(out / "system.token"),
                "human_token_file": str(out / "human.token"), "trace_file": str(out / "hook-trace.jsonl")})
            gate._write_private(out / "human.token", "human-fixture-token")
            gate._write_private(out / "system.token", "system-fixture-token")
            gate._write_private(out / "agent.token", "agent-fixture-token")
            class Owned:
                pid = 12345
            args = type("Args", (), {"out": str(out), "codex_bin": sys.executable,
                                     "user_reviewed_trust": True, "codex_timeout": 1})()
            with patch.dict(os.environ, {"JASMINE_CORE_GATE_NONCE": nonce}), \
                 patch.object(gate, "_git", return_value={"dirty": False, "commit": "frozen"}), \
                 patch.object(gate, "_binding", return_value=json.loads(binding.read_text())), \
                 patch.object(gate, "_db_binding", return_value={}), \
                 patch.object(gate, "_hook_config", return_value={}), \
                 patch.object(gate, "_start_core", return_value=Owned()) as start, \
                 patch.object(gate, "_owned_endpoint", return_value={}), \
                 patch.object(gate, "_api", return_value={"decision": "deny", "matched": [
                     {"rule_id": "rul_denied", "version": 1}]}), \
                 patch.object(gate, "_transition", return_value={}), \
                 patch.object(gate, "_real_turn", side_effect=gate.Failed("mock tool failure")), \
                 patch.object(gate, "_stop_core") as stop:
                with self.assertRaisesRegex(gate.Failed, "mock tool failure"):
                    gate._run(args, {"turns": []})
            self.assertEqual(start.call_count, 1)
            stop.assert_called_once_with(start.return_value, manifest["port"])

    def test_timeout_keeps_original_subprocess_output_and_blocks_without_hook(self) -> None:
        gate = module()
        with tempfile.TemporaryDirectory(prefix="p1-gate-timeout-") as temp:
            out = Path(temp)
            args = type("Args", (), {"codex_bin": "codex", "codex_timeout": 1})()
            manifest = {"workspace": str(out), "project_root": str(ROOT)}
            record = {"turns": []}
            timeout = subprocess.TimeoutExpired(["codex", "exec"], 1,
                                                output=b'partial stdout\n', stderr=b'partial stderr\n')
            with patch.object(gate.subprocess, "run", side_effect=timeout):
                with self.assertRaises(gate.Blocked):
                    gate._real_turn(args, out, manifest, "01-tools", "probe", None,
                                    "mock-private-nonce", record)
            self.assertEqual((out / "01-tools-codex.jsonl").read_text(), "partial stdout\n")
            self.assertEqual((out / "01-tools-stderr.txt").read_text(), "partial stderr\n")
            self.assertTrue(record["turns"][0]["timed_out"])

    def test_fabricated_project_local_hook_file_is_blocked(self) -> None:
        gate = module()
        with tempfile.TemporaryDirectory(prefix="p1-gate-hookpath-") as temp:
            root = Path(temp)
            fabricated = root / "other-hooks.json"
            fabricated.write_text('{"hooks": {}}')
            with self.assertRaises(gate.Blocked):
                gate._hook_config(fabricated, root, root / "binding.json")

    def test_observed_hook_without_lease_is_failed(self) -> None:
        gate = module()
        with tempfile.TemporaryDirectory(prefix="p1-gate-lease-") as temp:
            out = Path(temp)
            args = type("Args", (), {"codex_bin": "codex", "codex_timeout": 1})()
            manifest = {"workspace": str(out), "project_root": str(ROOT),
                        "binding": str(out / "binding.json")}
            record = {"turns": []}
            codex_output = '{"type":"thread.started","thread_id":"real-session"}\n'
            completed = subprocess.CompletedProcess(["codex", "exec"], 0, codex_output, "")
            trace = {"hook_event_name": "UserPromptSubmit", "result": "captured",
                     "session_id": "real-session", "event_id": "evt_fixture"}
            with patch.object(gate.subprocess, "run", return_value=completed), \
                 patch.object(gate, "_traces", side_effect=[[], [trace]]):
                with self.assertRaisesRegex(gate.Failed, "without a valid bound session lease"):
                    gate._real_turn(args, out, manifest, "01-tools", "probe", None,
                                    "mock-private-nonce", record)


if __name__ == "__main__":
    unittest.main()
