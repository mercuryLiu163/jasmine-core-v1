"""Guards for `scripts/p6/freeze_bundle.py`.

The freeze manifest is evidence about evidence, so the three ways it could be worthless are
each a test here:

1. it could *not* notice a changed file — the whole point of freezing;
2. it could swallow a missing or added file (a bundle that quietly lost a screenshot, or grew
   one after being read);
3. it could exit 0 when there is no manifest at all, which would make every unfrozen directory
   look verified.

The verification is also pointed at the real P6-03b1 bundle when it is present: that bundle's
manifest was written by hand before this tool existed, so it is the strongest available check
that the tool agrees with the frozen evidence rather than only with itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "p6" / "freeze_bundle.py"
INDEX = ROOT / "validation-runs" / "p6" / "P6-02B-INDEX.md"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class TheFreezeNoticesEveryChange(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.bundle = Path(self._temp.name)
        (self.bundle / "screenshots").mkdir()
        (self.bundle / "README.md").write_text("evidence\n", encoding="utf-8")
        (self.bundle / "click-trace.jsonl").write_text('{"step": "one"}\n', encoding="utf-8")
        (self.bundle / "screenshots" / "01.png").write_bytes(b"\x89PNG\r\n")
        frozen = run(
            "--freeze", str(self.bundle), "--commit", "a" * 40, "--kind", "unit test bundle",
            "--fixture-value", "simulated-p4-writes",
        )
        assert frozen.returncode == 0, frozen.stderr
        self.manifest = json.loads((self.bundle / "BUNDLE-FROZEN.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_a_fresh_freeze_verifies(self) -> None:
        result = run("--verify", str(self.bundle))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 files match", result.stdout)

    def test_the_manifest_does_not_hash_itself(self) -> None:
        paths = [entry["path"] for entry in self.manifest["files"]]
        self.assertNotIn("BUNDLE-FROZEN.json", paths)
        self.assertEqual(paths, sorted(paths), "paths must be in sorted order")

    def test_a_changed_file_fails_verification(self) -> None:
        (self.bundle / "click-trace.jsonl").write_text('{"step": "two"}\n', encoding="utf-8")
        result = run("--verify", str(self.bundle))
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed since the freeze", result.stderr)

    def test_a_missing_file_fails_verification(self) -> None:
        (self.bundle / "screenshots" / "01.png").unlink()
        result = run("--verify", str(self.bundle))
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing since the freeze", result.stderr)

    def test_an_added_file_fails_verification(self) -> None:
        # A bundle that grows after being read is not the bundle that was reviewed.
        (self.bundle / "extra.md").write_text("added later\n", encoding="utf-8")
        result = run("--verify", str(self.bundle))
        self.assertEqual(result.returncode, 1)
        self.assertIn("added since the freeze", result.stderr)

    def test_no_manifest_exits_two_rather_than_zero(self) -> None:
        (self.bundle / "BUNDLE-FROZEN.json").unlink()
        result = run("--verify", str(self.bundle))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("nothing to verify against", result.stderr)

    def test_the_binding_facts_are_written_not_just_the_files(self) -> None:
        self.assertEqual(self.manifest["commit"], "a" * 40)
        self.assertEqual(self.manifest["kind"], "unit test bundle")
        self.assertEqual(self.manifest["simulation_label"]["value"], "simulated-p4-writes")
        self.assertIn("written once", self.manifest["policy"])

    def test_verification_does_not_write_anything(self) -> None:
        before = (self.bundle / "BUNDLE-FROZEN.json").read_bytes()
        run("--verify", str(self.bundle))
        self.assertEqual((self.bundle / "BUNDLE-FROZEN.json").read_bytes(), before)


class TheFrozenP6BundlesStillMatch(unittest.TestCase):
    """The tool must agree with the real bundles, not only with its own output."""

    def test_the_current_p6_03b1_bundle_matches_its_manifest(self) -> None:
        if not INDEX.is_file():
            self.skipTest("the evidence index is not present in this checkout")
        index = INDEX.read_text(encoding="utf-8")
        # The index names the current bundle for P6-03b1; read it rather than hardcoding a
        # timestamp, so a new freeze moves this test with it instead of leaving it behind.
        section = index[index.index("## P6-03b1 bundles"):]
        marker = "| **Current Gate** | `"
        start = section.index(marker) + len(marker)
        name = section[start:section.index("`", start)]
        bundle = ROOT / "validation-runs" / "p6" / name
        self.assertTrue(bundle.is_dir(), f"the index names a bundle that is absent: {name}")
        result = run("--verify", str(bundle))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
