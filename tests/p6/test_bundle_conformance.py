#!/usr/bin/env python3.13
"""Tests for `scripts/p6/bundle_conformance.py` — the §8 evidence-format renderer.

These tests build a synthetic **frozen** bundle in a temp directory (via `freeze_bundle.py`, so the
manifest semantics are the real ones) and then exercise the properties the operator asked for:

* it writes into its own directory and never touches the source bundle;
* a bundle that is not frozen is refused;
* a tampered, missing or added source file is refused (the derivation aborts);
* an output path inside the bundle, or a symlink at a target name, is refused — the first version
  wrote **through** such a symlink and clobbered a source file while reporting success;
* every field of every source record, including non-empty request bodies (`postData`,
  `request_body`) and records in nested sub-run files such as `stability/`, is carried; the derived
  manifest's `field_accounting.not_carried` must be empty;
* absent content becomes a `not-recorded` marker, not a fabricated request/response/Truth diff;
* a failure during a derivation leaves no half-written output (a fresh output is never created, and
  refreshing an existing derivation restores the previous one);
* two runs are byte-identical and `--check` proves it;
* `--check` fails if a derived file was edited, and refuses symlinked entries;
* `--expect-commit` refuses a mismatched commit.

They run entirely in `tempfile` trees; no real registry bundle is read or written.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

import bundle_conformance as bc  # noqa: E402
from bundle_conformance import DERIVED_FILES, main as conformance_main  # noqa: E402
from freeze_bundle import main as freeze_main  # noqa: E402

COMMIT = "a" * 40


class ConformanceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="p6-conformance-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bundle = self.tmp / "bundle"
        self.bundle.mkdir()
        (self.bundle / "README.md").write_text(
            "# A synthetic bundle\n\nResult: **2/2** records passed.\n", encoding="utf-8")
        (self.bundle / "click-trace.jsonl").write_text(
            json.dumps({"ok": True, "step": "first record", "detail": "{}"}, ensure_ascii=False) + "\n"
            + json.dumps({"ok": False, "step": "second record", "detail": "{}"},
                         ensure_ascii=False) + "\n",
            encoding="utf-8")
        (self.bundle / "browser-requests.jsonl").write_text(
            json.dumps({"at": "2026-10-08T00:00:00.000Z", "method": "POST", "path": "/v1/pin",
                        "query": "", "authorization": "present(redacted)", "status": 200,
                        "mimeType": "application/json", "fixtureLabel": "simulated-test",
                        "requestId": "1.1", "xRequestId": "ui-1",
                        "postData": {"expected_revision": 1, "reason": "test"}}) + "\n",
            encoding="utf-8")
        (self.bundle / "evidence.json").write_text(json.dumps({
            "summary": {"checks": 1, "passed": 1, "failed": 0, "blocked": 0},
            "entries": [{"id": "C1", "check": "a core check", "ok": True, "detail": {}}],
            "calls": [{"method": "POST", "path": "/v1/pin", "status": 200, "actor_key": "full",
                       "request_body": {"expected_revision": 1},
                       "response_body": {"ok": True}}],
        }, ensure_ascii=False), encoding="utf-8")
        self.freeze()

    def freeze(self) -> None:
        result = freeze_main(["--freeze", str(self.bundle), "--commit", COMMIT,
                              "--kind", "synthetic test bundle", "--runs", "one pass"])
        self.assertEqual(result, 0)

    def source_hashes(self) -> dict[str, str]:
        import hashlib
        return {path.relative_to(self.bundle).as_posix():
                hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.bundle.rglob("*")) if path.is_file()}

    def read_jsonl(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.tmp.iterdir()
                      if p.name.startswith(".p6-conformance-staging-")
                      or p.name.endswith(".p6-previous"))


class Derivation(ConformanceCase):
    def test_writes_the_section_8_names_and_leaves_the_source_untouched(self) -> None:
        before = self.source_hashes()
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        for name in DERIVED_FILES:
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual(self.source_hashes(), before,
                         "the source bundle must be byte-identical after a derivation")
        self.assertEqual(freeze_main(["--verify", str(self.bundle)]), 0)

    def test_manifest_records_source_bundle_commit_and_source_hashes(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_bundle"]["commit"], COMMIT)
        self.assertTrue(manifest["source_bundle"]["path"].endswith("bundle"))
        recorded = manifest["source_files"]
        for path in sorted(self.bundle.rglob("*")):
            if path.is_file() and path.name != "BUNDLE-FROZEN.json":
                self.assertIn(path.relative_to(self.bundle).as_posix(), recorded)
        self.assertEqual(len(manifest["source_manifest_sha256"]), 64)

    def test_carries_every_field_including_request_bodies(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        requests = self.read_jsonl(out / "api-requests.jsonl")
        browser = [r for r in requests if r["recorded_in"] == "browser-requests.jsonl"]
        self.assertEqual(browser[0]["postData"], {"expected_revision": 1, "reason": "test"})
        self.assertEqual(browser[0]["requestId"], "1.1")
        calls = [r for r in requests if r["recorded_in"] == "evidence.json:calls"]
        self.assertEqual(calls[0]["request_body"], {"expected_revision": 1})
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["not_carried_total"], 0, manifest["field_accounting"])
        self.assertEqual(manifest["field_accounting"]["browser-requests.jsonl"]
                         ["records_with_a_non_empty_body"], 1)

    def test_reads_nested_sub_run_record_files(self) -> None:
        nested = self.bundle / "stability"
        nested.mkdir()
        (nested / "browser-requests.jsonl").write_text(
            json.dumps({"method": "GET", "path": "/v1/health", "status": 200,
                        "postData": None}) + "\n", encoding="utf-8")
        self.freeze()
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        requests = self.read_jsonl(out / "api-requests.jsonl")
        self.assertIn("stability/browser-requests.jsonl",
                      {entry["recorded_in"] for entry in requests})
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("stability/browser-requests.jsonl", manifest["field_accounting"])

    def test_absent_content_is_marked_not_recorded_never_invented(self) -> None:
        (self.bundle / "browser-requests.jsonl").unlink()
        (self.bundle / "evidence.json").unlink()
        self.freeze()
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        requests = self.read_jsonl(out / "api-requests.jsonl")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "not-recorded")
        self.assertIn("browser-requests.jsonl (any depth)", requests[0]["sources_that_were_checked"])
        truth = json.loads((out / "truth-before-after.json").read_text(encoding="utf-8"))
        self.assertEqual(truth["status"], "not-recorded")
        result = (out / "result.md").read_text(encoding="utf-8")
        self.assertIn("not acceptance", result)
        scenario = (out / "scenario.md").read_text(encoding="utf-8")
        self.assertIn("first record", scenario)
        self.assertIn("second record", scenario)

    def test_result_restates_the_source_and_does_not_change_the_verdict(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        result = (out / "result.md").read_text(encoding="utf-8")
        self.assertIn("1 PASS, 1 FAIL, 0 BLOCKED", result)
        self.assertIn("does not re-run, re-judge or upgrade", result)


class Refusals(ConformanceCase):
    def test_refuses_a_bundle_that_is_not_frozen(self) -> None:
        (self.bundle / "BUNDLE-FROZEN.json").unlink()
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 2)
        self.assertFalse(out.exists(), "a refused derivation must not create the output directory")

    def test_refuses_a_tampered_source(self) -> None:
        (self.bundle / "README.md").write_text("# edited after the freeze\n", encoding="utf-8")
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 1)
        self.assertFalse(out.exists())

    def test_refuses_a_source_with_an_added_file(self) -> None:
        (self.bundle / "extra.txt").write_text("not in the manifest", encoding="utf-8")
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 1)

    def test_refuses_a_source_with_a_missing_file(self) -> None:
        (self.bundle / "click-trace.jsonl").unlink()
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 1)

    def test_refuses_an_output_inside_the_bundle(self) -> None:
        inside = self.bundle / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(inside)]), 2)
        self.assertFalse(inside.exists())
        self.assertEqual(freeze_main(["--verify", str(self.bundle)]), 0)

    def test_refuses_a_target_that_is_a_symlink_to_a_source_file(self) -> None:
        """The first version wrote through this link and clobbered the source, then returned 0."""
        out = self.tmp / "derived"
        out.mkdir()
        (out / "result.md").symlink_to(self.bundle / "README.md")
        before = self.source_hashes()
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 2)
        self.assertEqual(self.source_hashes(), before, "the source must not be written through a link")
        self.assertEqual(freeze_main(["--verify", str(self.bundle)]), 0)

    def test_refuses_an_out_that_is_a_symlink(self) -> None:
        real = self.tmp / "real-out"
        real.mkdir()
        link = self.tmp / "linked-out"
        link.symlink_to(real)
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(link)]), 2)
        self.assertEqual(sorted(p.name for p in real.iterdir()), [])

    def test_refuses_a_commit_that_does_not_match(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out),
                                           "--expect-commit", "b" * 40]), 2)
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out),
                                           "--expect-commit", COMMIT]), 0)


class FailureHandling(ConformanceCase):
    def test_a_failed_install_leaves_no_output_and_no_staging(self) -> None:
        out = self.tmp / "derived"
        with mock.patch.object(bc, "install", side_effect=OSError("simulated install failure")):
            self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 1)
        self.assertFalse(out.exists(), "a fresh output must not exist after a failed derivation")
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_refresh_restores_the_previous_derivation(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        before = {name: (out / name).read_bytes() for name in DERIVED_FILES}
        real_replace = os.replace

        def flaky(source, destination, *args, **kwargs):
            # Compare resolved paths: `main` resolves --out, and on macOS /var is a symlink to
            # /private/var, so the unresolved test path would never match.
            if (Path(destination).resolve() == out.resolve()
                    and ".p6-conformance-staging-" in str(source)):
                raise OSError("simulated failure while installing the refresh")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(bc.os, "replace", side_effect=flaky):
            self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 1)
        self.assertEqual({name: (out / name).read_bytes() for name in DERIVED_FILES}, before,
                         "the previous derivation must be restored, not left half-replaced")
        self.assertEqual(self.leftovers(), [])


class Idempotence(ConformanceCase):
    def test_two_runs_are_byte_identical_and_check_passes(self) -> None:
        first, second = self.tmp / "first", self.tmp / "second"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(first)]), 0)
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(second)]), 0)
        for name in DERIVED_FILES:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(first),
                                           "--check"]), 0)

    def test_check_detects_an_edited_derived_file(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        (out / "result.md").write_text("a reviewer edited this\n", encoding="utf-8")
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out),
                                           "--check"]), 1)

    def test_check_refuses_a_symlinked_entry(self) -> None:
        out = self.tmp / "derived"
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out)]), 0)
        (out / "result.md").unlink()
        (out / "result.md").symlink_to(self.bundle / "README.md")
        self.assertEqual(conformance_main(["--bundle", str(self.bundle), "--out", str(out),
                                           "--check"]), 2)

    def test_check_needs_an_existing_derived_directory(self) -> None:
        self.assertEqual(conformance_main(["--bundle", str(self.bundle),
                                           "--out", str(self.tmp / "absent"), "--check"]), 2)


if __name__ == "__main__":
    unittest.main()
