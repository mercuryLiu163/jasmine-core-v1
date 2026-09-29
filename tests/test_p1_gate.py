"""P1 real Gate must remain BLOCKED before a real trusted conversation exists."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RealGatePreflight(unittest.TestCase):
    def test_missing_runtime_records_blocked_and_exits_two(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p1-gate-preflight-") as temp:
            base = Path(temp)
            nonce = "private-fixture-nonce-0123456789"
            env = dict(os.environ, JASMINE_CORE_GATE_NONCE=nonce)
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/p1-real-conversation.py"),
                "--out", str(base / "record"), "--project-root", str(base),
                "--binding", str(base / "absent-binding.json"),
                "--hook-config", str(base / "absent-hooks.json"),
                "--db", str(base / "absent-core.db"),
                "--core-pid", str(os.getpid()),
                "--codex-bin", "definitely-absent-codex-fixture",
            ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            report_files = list((base / "record").glob("p1-t10-*.json"))
            self.assertEqual(len(report_files), 1)
            report = json.loads(report_files[0].read_text(encoding="utf-8"))
            self.assertEqual((report["case_id"], report["verdict"], report["phase"]),
                             ("P1-T10", "BLOCKED", "preflight"))
            self.assertNotIn(nonce, report_files[0].read_text(encoding="utf-8"))
            self.assertNotIn("PASS", result.stdout)
