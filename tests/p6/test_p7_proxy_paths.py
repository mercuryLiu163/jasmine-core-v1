"""P6-03c: the dashboard's Validation paths match the frozen P7-01 contract.

`web/server/core-paths.json` carries five `p7_pending_paths` that are opt-in behind
`P6_ENABLE_P7_READS`. Two failure modes this guards against, the same pair the P3 and P4
contract tests guard:

1. a pending pattern that names no route in the frozen P7-01 contract — a path the browser
   could ask for that Core would answer 404, while looking deliberate in review;
2. a pending pattern that is broader than the contract route it stands for, so a path the
   contract never defined would be forwarded.

The P7-01 slice is developed in its own worktree, so its contract is copied here verbatim
(``web/contracts/p7-validation-read.v1.json``, ``source.ref`` pinned to the P7-01
implementation commit). This test reads that file, not the P7-01 worktree, so the P6 gate
holds on its own.

This is deliberately separate from ``test_dashboard_proxy_contract.py``: that test derives
samples from the *baseline* Core's route table, and the P7-01 routes do not exist there yet.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
CONTRACT_PATH = ROOT / "web" / "contracts" / "p7-validation-read.v1.json"

#: The id shapes the P7-01 slice uses, which are NOT Core public ids: the run id is the
#: recorder's and the artifact id is bundle-scoped (see the allowlist's `p7_pending_note`).
RUN_ID = "run-20261008T101010Z"
ARTIFACT_ID = "art_stdout"


def concrete(pattern: str) -> str:
    """One concrete path a contract route pattern matches.

    The contract's patterns use exactly one construct, ``([^/]+)``, so substituting a
    well-formed id for each capture yields a path the pattern matches.
    """
    body = pattern
    assert body.startswith("^/") and body.endswith("$"), pattern
    body = body[1:-1]
    while "([^/]+)" in body:
        # The run id capture always comes before the artifact id capture.
        replacement = RUN_ID if f"/{RUN_ID}" not in body else ARTIFACT_ID
        body = body.replace("([^/]+)", replacement, 1)
    return body


class P7ProxyPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(ALLOWLIST_PATH.exists(), f"missing {ALLOWLIST_PATH}")
        self.assertTrue(CONTRACT_PATH.exists(), f"missing {CONTRACT_PATH}")
        self.allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.pending = [re.compile(pattern) for pattern in self.allowlist["p7_pending_paths"]]
        self.routes = [route for route in self.contract["read_routes"]
                       if route["method"] == "GET"]

    def test_the_contract_is_the_frozen_p7_01_one(self) -> None:
        self.assertEqual(self.contract["contract"], "p7-validation-read.v1")
        self.assertTrue(self.contract["source"]["ref"],
                        "the copied contract must record the commit it was derived from")
        self.assertEqual(self.contract["read_scopes"], ["validation:read"])

    def test_pending_paths_are_pinned_exactly(self) -> None:
        """Silent widening is the failure mode, so pin the set exactly."""
        self.assertEqual(
            self.allowlist["p7_pending_paths"],
            [
                "^/v1/validation/runs$",
                r"^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z$",
                r"^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/manifest$",
                r"^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/artifacts/"
                r"art_[a-z0-9][a-z0-9_.-]{0,63}$",
                r"^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/artifacts/"
                r"art_[a-z0-9][a-z0-9_.-]{0,63}/content$",
            ],
        )

    def test_every_pending_path_matches_a_contract_route(self) -> None:
        for pattern in self.pending:
            with self.subTest(pattern=pattern.pattern):
                matched = [route["path_pattern"] for route in self.routes
                           if pattern.fullmatch(concrete(route["path_pattern"]))]
                self.assertEqual(
                    len(matched), 1,
                    f"{pattern.pattern!r} must match exactly one contract route, got {matched}",
                )

    def test_every_contract_route_has_a_pending_path(self) -> None:
        self.assertEqual(len(self.routes), len(self.pending),
                         "one allowlist pattern per contract route")
        for route in self.routes:
            with self.subTest(route=route["path_pattern"]):
                path = concrete(route["path_pattern"])
                self.assertTrue(
                    any(pattern.fullmatch(path) for pattern in self.pending),
                    f"the contract route {route['path_pattern']!r} ({path}) is not forwarded",
                )

    def test_pending_paths_are_not_in_the_always_on_list(self) -> None:
        live = [re.compile(pattern) for pattern in self.allowlist["paths"]]
        for pattern in self.pending:
            for path in ("/v1/validation/runs",):
                self.assertFalse(
                    any(live_pattern.fullmatch(path) for live_pattern in live),
                    "the read slice is opt-in: no validation path may be always on",
                )

    def test_no_pending_path_is_a_write(self) -> None:
        self.assertNotIn("p7_write_paths", self.allowlist)
        writes = [re.compile(pattern) for pattern in self.allowlist.get("write_paths", [])]
        for path in ("/v1/validation/runs", f"/v1/validation/runs/{RUN_ID}"):
            self.assertFalse(
                any(pattern.fullmatch(path) for pattern in writes),
                f"{path} must not be reachable as a write: the slice is read-only",
            )

    def test_the_id_shapes_cannot_be_widened_or_traversed(self) -> None:
        allowed = lambda path: any(pattern.fullmatch(path) for pattern in self.pending)
        for hostile in (
            # not the recorder's run grammar
            "/v1/validation/runs/run-2026-10-08",
            "/v1/validation/runs/run-20261008T10101Z",
            "/v1/validation/runs/run-20261008T101010",
            # traversal and absolute forms
            "/v1/validation/runs/..",
            "/v1/validation/runs/../../etc/passwd",
            f"/v1/validation/runs/{RUN_ID}/../../runs",
            # artifact ids outside `art_[a-z0-9][a-z0-9_.-]{0,63}`
            f"/v1/validation/runs/{RUN_ID}/artifacts/ART_STDOUT",
            f"/v1/validation/runs/{RUN_ID}/artifacts/art",
            f"/v1/validation/runs/{RUN_ID}/artifacts/art_!!!",
            # extra segments past the declared routes
            f"/v1/validation/runs/{RUN_ID}/artifacts/{ARTIFACT_ID}/content/extra",
            # a route the slice deliberately does not have
            "/v1/validation/runs/summary",
            "/v1/validation/artifacts",
        ):
            with self.subTest(path=hostile):
                self.assertFalse(allowed(hostile), f"{hostile} must not be forwarded")

    def test_the_content_route_ignores_the_query_because_core_owns_it(self) -> None:
        # The proxy matches the pathname; `?form=original&download=1` is Core's decision, and
        # Core withholds the original when the masker is not a no-op.
        path = f"/v1/validation/runs/{RUN_ID}/artifacts/{ARTIFACT_ID}/content"
        self.assertTrue(any(pattern.fullmatch(path) for pattern in self.pending))
        self.assertTrue(any(pattern.fullmatch(path.split("?")[0]) for pattern in self.pending))


if __name__ == "__main__":
    unittest.main()
