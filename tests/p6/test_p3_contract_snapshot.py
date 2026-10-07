"""P6-03a: the P3 continuity read contract, and the dependency it is missing.

P6-03a's read slice (Checkpoint / Context Pack / Resume) targets routes that live only
on the P3 branch. Two things therefore need to be *checked* rather than asserted in
prose, because both are easy to get wrong in a way that looks fine:

1. the contract the views are built against must be the real P3 one — so the snapshot in
   `web/contracts/p3-continuity-read.v1.json` is re-derived from the branch and compared
   (skipped, loudly, when the branch is not present);
2. those routes must **not** be reachable through the baseline proxy — the baseline Core
   has no such route, and a path in the live allowlist that Core cannot serve is exactly
   the "plausible endpoint" defect the allowlist exists to prevent. The baseline route
   table is used to prove the routes are absent, so DEPENDENCY_PENDING is a verified
   statement rather than a claim.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

from jasmine_core.api.handlers import ROUTES  # noqa: E402

SNAPSHOT_PATH = ROOT / "web" / "contracts" / "p3-continuity-read.v1.json"
ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
P3_REF = "p3-checkpoint-context-resume"


def baseline_routes() -> list[tuple[str, re.Pattern[str]]]:
    return [(route.method, route.pattern) for route in ROUTES]


def ref_exists(ref: str) -> bool:
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                            cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0


class P3ContractSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SNAPSHOT_PATH.exists(), f"missing {SNAPSHOT_PATH}")
        self.snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    def test_snapshot_records_the_commit_it_came_from(self) -> None:
        source = self.snapshot["source"]
        self.assertEqual(source["ref"], P3_REF)
        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(source["derived_by"], "scripts/p6/p3_contract.py")
        self.assertGreater(source["schema_version_in_p3"], 8,
                           "P3 raises the schema; the baseline is 8")

    def test_snapshot_is_the_four_read_endpoints_only(self) -> None:
        routes = self.snapshot["read_routes"]
        self.assertEqual([r["method"] for r in routes], ["GET"] * 4)
        self.assertEqual(
            sorted(r["path_pattern"] for r in routes),
            sorted([
                r"^/v1/context/([^/]+)$",
                r"^/v1/tasks/([^/]+)/checkpoints/latest$",
                r"^/v1/checkpoints/([^/]+)$",
                r"^/v1/resumes/([^/]+)$",
            ]),
        )
        # The POST shapes are build operations and must not be smuggled into the read
        # slice; they are recorded as explicitly out of scope instead.
        self.assertEqual(len(self.snapshot["not_in_this_slice"]), 4)
        self.assertTrue(all(entry["method"] == "POST"
                            for entry in self.snapshot["not_in_this_slice"]))

    def test_every_read_route_records_a_scope_and_response_key(self) -> None:
        for route in self.snapshot["read_routes"]:
            with self.subTest(pattern=route["path_pattern"]):
                self.assertRegex(route["scope"], r"^[a-z]+:[a-z]+$")
                self.assertIn(route["response_key"], {"context", "checkpoint", "resume"})
                self.assertIsInstance(route["handler"], str)

    def test_response_field_sets_are_not_empty(self) -> None:
        # A view cannot be honest about a shape nobody recorded, so an empty field set is
        # a failure rather than a pass with fewer assertions.
        fields = self.snapshot["response_fields"]
        for name in ("checkpoint", "resume", "context"):
            with self.subTest(kind=name):
                self.assertGreater(len(fields[name]), 5, f"{name} field set looks unread")
        self.assertIn("projection", fields["checkpoint"])
        self.assertIn("comparison", fields["resume"])
        self.assertIn("truth_digest", fields["resume"])
        # `**rendering` is resolved to the producing function's return keys, so the
        # context field set is the whole shape rather than 23 fields and a mystery.
        self.assertIn("rendered_content", fields["context"])
        self.assertIn("token_count", fields["context"])

    def test_semantics_the_views_depend_on_are_recorded(self) -> None:
        semantics = self.snapshot["semantics"]
        for key in ("checkpoint_projection_is_not_truth", "latest_may_be_null",
                    "resume_note_is_historical", "resume_is_a_comparison_not_a_restore"):
            with self.subTest(key=key):
                self.assertGreater(len(semantics[key]), 40)

    @unittest.skipUnless(ref_exists(P3_REF), f"{P3_REF} is not present in this clone")
    def test_snapshot_still_matches_the_branch(self) -> None:
        from p3_contract import derive

        derived = derive(P3_REF)
        differing = [key for key in derived if self.snapshot.get(key) != derived.get(key)]
        self.assertEqual(differing, [], f"P3 contract drifted: {differing}")


class BaselineDoesNotHaveP3Routes(unittest.TestCase):
    """The dependency is missing, and that is checkable from Core's own route table."""

    def test_baseline_core_serves_none_of_the_four_endpoints(self) -> None:
        routes = baseline_routes()
        patterns = [route["path_pattern"] for route in
                    json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["read_routes"]]
        sample = {"ctx": "ctx_01M4B6VGC29ESRDRQR62DX8AK8",
                  "ckp": "ckp_01M4B6VGC29ESRDRQR62DX8AK8",
                  "rms": "rms_01M4B6VGC29ESRDRQR62DX8AK8",
                  "tsk": "tsk_01M4B6VGC29ESRDRQR62DX8AK8"}
        paths = [
            "/v1/context/" + sample["ctx"],
            "/v1/tasks/" + sample["tsk"] + "/checkpoints/latest",
            "/v1/checkpoints/" + sample["ckp"],
            "/v1/resumes/" + sample["rms"],
        ]
        for path in paths:
            with self.subTest(path=path):
                matching = [(method, pattern.pattern) for method, pattern in routes
                            if pattern.fullmatch(path)]
                self.assertEqual(matching, [],
                                 f"{path} is served by this baseline; the P3 dependency "
                                 f"would then not be missing and this test is stale")
        # And the reverse direction: the snapshot's patterns are not vacuous, they would
        # match the paths above if a P3 Core were serving them.
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(any(re.compile(pattern).fullmatch(p) for p in paths))

    def test_pending_paths_are_not_in_the_live_read_allowlist(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        live = [re.compile(pattern) for pattern in allowlist["paths"]]
        pending = [re.compile(route["path_pattern"])
                   for route in json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["read_routes"]]
        # A pending route in the live read list would be forwarded to a Core that cannot
        # answer it — the exact "plausible endpoint" failure the allowlist prevents.
        for pattern in pending:
            with self.subTest(pattern=pattern.pattern):
                self.assertFalse(any(pattern.pattern == live_pattern.pattern
                                     for live_pattern in live),
                                 f"{pattern.pattern} must not be in the baseline read list")
        for route in json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["read_routes"]:
            for sample in ("/v1/context/ctx_1", "/v1/checkpoints/ckp_1", "/v1/resumes/rms_1"):
                with self.subTest(sample=sample):
                    self.assertFalse(any(pattern.fullmatch(sample) for pattern in live))


if __name__ == "__main__":
    unittest.main()
