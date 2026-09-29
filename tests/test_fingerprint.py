"""Server workspace hashing, mismatch and explicit incomplete snapshots."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401
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
        first = capture(self.root)
        self.assertFalse(first["complete"])
        self.assertNotIn("link.txt", first["selected_hashes"])
        self.assertEqual(compare(first, capture(self.root)), "UNKNOWN")
