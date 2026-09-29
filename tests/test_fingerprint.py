"""Server workspace hashing, mismatch and explicit incomplete snapshots."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

from support import SRC  # noqa: F401
from jasmine_core import fingerprint
from jasmine_core.evidence import EvidenceStore
from jasmine_core.fingerprint import capture, compare


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-fingerprint-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_git_hashes_tracked_and_relevant_untracked(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("ignored.txt\n")
        (self.root / "tracked.txt").write_text("one")
        subprocess.run(["git", "-C", str(self.root), "add", ".gitignore", "tracked.txt"], check=True)
        (self.root / "untracked.txt").write_text("fresh")
        (self.root / "ignored.txt").write_text("secret-not-read")
        (self.root / "core.db").write_text("db-not-read")
        first = capture(self.root)
        self.assertTrue(first["complete"])
        self.assertEqual(set(first["selected_hashes"]), {".gitignore", "tracked.txt", "untracked.txt"})
        self.assertEqual(compare(first, capture(self.root)), "SAME")
        (self.root / "untracked.txt").write_text("changed")
        self.assertEqual(compare(first, capture(self.root)), "MISMATCH")

    def test_nongit_symlink_is_partial_and_never_same(self) -> None:
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("outside")
        self.addCleanup(outside.unlink)
        (self.root / "source.txt").write_text("source")
        (self.root / "link.txt").symlink_to(outside)
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS":
                                      '["source.txt","link.txt"]'}):
            first = capture(self.root)
            second = capture(self.root)
        self.assertFalse(first["complete"])
        self.assertNotIn("link.txt", first["selected_hashes"])
        self.assertEqual(compare(first, second), "UNKNOWN")

    def test_nongit_without_selected_inputs_does_not_read_arbitrary_secret(self) -> None:
        (self.root / ".env").write_text("PRIVATE_VALUE")
        snapshot = capture(self.root)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["selected_hashes"], {})

    def test_fifo_input_is_rejected_without_waiting_for_writer(self) -> None:
        fifo = self.root / "declared.fifo"
        os.mkfifo(fifo)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from pathlib import Path; "
                "from jasmine_core.fingerprint import hash_workspace_file; "
                "hash_workspace_file(Path(sys.argv[2]), 'declared.fifo')")
        result = subprocess.run([sys.executable, "-c", code, str(SRC), str(self.root)],
                                capture_output=True, text=True, timeout=3, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular file", result.stderr)

    def test_nested_evidence_source_and_exact_hook_binding_are_hashed(self) -> None:
        (self.root / "src" / "evidence").mkdir(parents=True)
        source = self.root / "src" / "evidence" / "check.py"
        source.write_text("one")
        (self.root / ".codex").mkdir()
        binding = self.root / ".codex" / "hooks.json"
        binding.write_text("{}")
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS":
                                      '["src/evidence/check.py"]'}):
            first = capture(self.root)
        self.assertTrue(first["complete"])
        self.assertEqual(set(first["selected_hashes"]), {"src/evidence/check.py", ".codex/hooks.json"})
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS":
                                      '["src/evidence/check.py"]'}):
            source.write_text("two")
            self.assertEqual(compare(first, capture(self.root)), "MISMATCH")
            source.write_text("one")
            binding.write_text('{"hooks":{}}')
            self.assertEqual(compare(first, capture(self.root)), "MISMATCH")

    def test_git_metadata_failure_does_not_fallback_to_scanning_ignored_file(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "secret.txt").write_text("sensitive")
        with patch.object(fingerprint, "_git", return_value=None):
            snapshot = capture(self.root)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["selected_hashes"], {})

    def test_explicit_ignored_relevant_input_is_hashed(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("fixture.dat\n")
        (self.root / "fixture.dat").write_text("first")
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["fixture.dat"]'}):
            first = capture(self.root)
            self.assertTrue(first["complete"])
            self.assertIn("fixture.dat", first["selected_hashes"])
            (self.root / "fixture.dat").write_text("second")
            self.assertEqual(compare(first, capture(self.root)), "MISMATCH")

    def test_coverage_change_has_distinct_fingerprint_identity(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "tracked.txt").write_text("same")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True)
        first = capture(self.root)
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["tracked.txt"]'}):
            second = capture(self.root)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(compare(first, second), "UNKNOWN")
        self.assertNotEqual(EvidenceStore._snapshot_identity("prj_fixture", first),
                            EvidenceStore._snapshot_identity("prj_fixture", second))

    def test_symlink_parent_and_byte_limit_are_partial(self) -> None:
        outside = self.root.parent / (self.root.name + "-outside-dir")
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir())
        (outside / "target.txt").write_text("outside")
        self.addCleanup(lambda: (outside / "target.txt").unlink())
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with patch.object(fingerprint, "_git", side_effect=[str(self.root).encode(), b"head", b"link/target.txt\0", b""]):
            snapshot = capture(self.root)
        self.assertFalse(snapshot["complete"])
        self.assertNotIn("link/target.txt", snapshot["selected_hashes"])
        (self.root / "big.txt").write_text("123456789")
        with patch.object(fingerprint, "MAX_BYTES", 4), patch.dict(
                os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["big.txt"]'}):
            limited = capture(self.root)
        self.assertFalse(limited["complete"])
        self.assertNotIn("big.txt", limited["selected_hashes"])
