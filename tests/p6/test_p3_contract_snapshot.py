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


#: The baseline the contract was derived against (main, schema 8).
BASELINE_REF = "850fbecac516ca55e396dc13bf722a28b1dd7837"
#: The module whose presence decides whether this Core can serve the P3 routes at all.
DEPENDENCY_MODULE = "src/jasmine_core/continuity.py"


def baseline_routes() -> list[tuple[str, re.Pattern[str]]]:
    return [(route.method, route.pattern) for route in ROUTES]


def dependency_in_this_tree() -> bool:
    return (ROOT / DEPENDENCY_MODULE).is_file()


def pinned_baseline_has_dependency() -> bool:
    result = subprocess.run(["git", "cat-file", "-e", f"{BASELINE_REF}:{DEPENDENCY_MODULE}"],
                            cwd=ROOT, capture_output=True)
    return result.returncode == 0


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

    def test_each_route_records_every_scope_its_handler_requires(self) -> None:
        """The route table names one scope per route; the handlers require more.

        P3's `get_context` runs `_context_read`, which adds `checkpoint:read`, and all four
        reads run `_continuity_read`, which adds five more. Recording only the route scope
        would tell an operator that a token the contract calls sufficient is enough — and
        real P3 would refuse it. This asserts the *semantics* rather than a hardcoded list,
        so a contract that loses these scopes fails here.
        """
        contract = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        shared = {"objects:read", "state:read", "authority:read", "evidence:read", "events:read"}
        for route in contract["read_routes"]:
            full = {route["scope"], *route["extra_scopes"]}
            with self.subTest(pattern=route["path_pattern"]):
                self.assertTrue(shared <= full,
                                f"{route['path_pattern']} is missing {sorted(shared - full)}; "
                                f"every P3 continuity read runs _continuity_read")
                if route["response_key"] in ("context", "resume"):
                    self.assertIn("checkpoint:read", full,
                                  f"{route['path_pattern']} runs a helper that requires "
                                  f"checkpoint:read, so the route-table scope is not enough")
                self.assertEqual(len(full), len([route["scope"], *route["extra_scopes"]]),
                                 "a scope is listed twice, once in scope and once in extra_scopes")

    def test_each_route_records_its_scopes_in_the_order_core_checks_them(self) -> None:
        """Order is observable, because Core names the *first* requirement it fails.

        P3's dispatch requires the route scope, then runs the handler. `_context_read`
        calls `_continuity_read` and only **then** requires `checkpoint:read`, so for a
        token missing both `checkpoint:read` and `objects:read`, real P3 names
        `objects:read`. Recording a function's own literals before descending into the
        helpers it calls would reverse that and name the wrong scope.
        """
        shared = ["objects:read", "state:read", "authority:read", "evidence:read", "events:read"]
        expected = {
            r"^/v1/context/([^/]+)$": ["context:read", *shared, "checkpoint:read"],
            r"^/v1/tasks/([^/]+)/checkpoints/latest$": ["checkpoint:read", *shared],
            r"^/v1/checkpoints/([^/]+)$": ["checkpoint:read", *shared],
            r"^/v1/resumes/([^/]+)$": ["resume:read", *shared, "checkpoint:read"],
        }
        contract = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual({route["path_pattern"] for route in contract["read_routes"]},
                         set(expected))
        for route in contract["read_routes"]:
            with self.subTest(pattern=route["path_pattern"]):
                self.assertEqual([route["scope"], *route["extra_scopes"]],
                                 expected[route["path_pattern"]])

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
    """The dependency's absence is a fact about the *pinned baseline*, not about any tree.

    Asserting "**this** Core serves none of the four" was true only before P3 is merged; the
    integration rehearsal recorded it as a false failure at the merged ref. The ref-relative form
    holds in both states: with the continuity module present the Core must serve all four, and
    without it, none.
    """

    def test_the_pinned_baseline_lacks_the_dependency_and_this_core_agrees_with_its_own_tree(self) -> None:
        self.assertFalse(pinned_baseline_has_dependency(),
                         f"{BASELINE_REF[:12]} must not contain {DEPENDENCY_MODULE}")
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
        served = [path for path in paths
                  if any(pattern.fullmatch(path) for _, pattern in routes)]
        if dependency_in_this_tree():
            self.assertEqual(sorted(served), sorted(paths),
                             "this tree contains the P3 modules, so its Core must serve all four")
        else:
            for path in paths:
                with self.subTest(path=path):
                    matching = [(method, pattern.pattern) for method, pattern in routes
                                if pattern.fullmatch(path)]
                    self.assertEqual(matching, [],
                                     f"{path} is served by this baseline, which has no P3 module")
        # And the reverse direction: the snapshot's patterns are not vacuous, they would
        # match the paths above if a P3 Core were serving them.
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(any(re.compile(pattern).fullmatch(p) for p in paths))

    def test_pending_paths_are_not_in_the_live_read_allowlist(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        live = [re.compile(pattern) for pattern in allowlist["paths"]]
        # Compare against the *shipped* pending patterns, not the snapshot's spelling. The
        # snapshot writes ids loosely (`([^/]+)`); the proxy writes them strictly
        # (`[a-z]{3}_[0-9A-Z]{26}`). Comparing the two by string missed the case that
        # matters — the strict pattern being copied into `paths` — so both are checked.
        shipped_pending = [re.compile(pattern) for pattern in allowlist["p3_pending_paths"]]
        snapshot_pending = [re.compile(route["path_pattern"])
                            for route in json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["read_routes"]]

        for pattern in [*shipped_pending, *snapshot_pending]:
            with self.subTest(pattern=pattern.pattern):
                self.assertFalse(any(pattern.pattern == live_pattern.pattern
                                     for live_pattern in live),
                                 f"{pattern.pattern} must not be in the baseline read list")

        # And behaviourally, with **real id-shaped** paths. Probing 1-character ids would
        # not match a strict pending pattern, so a leak would go unnoticed.
        id_body = "01M4B6VGC29ESRDRQR62DX8AK8"
        real_paths = [
            f"/v1/context/ctx_{id_body}",
            f"/v1/tasks/tsk_{id_body}/checkpoints/latest",
            f"/v1/checkpoints/ckp_{id_body}",
            f"/v1/resumes/rms_{id_body}",
        ]
        for path in real_paths:
            with self.subTest(path=path):
                matching = [pattern.pattern for pattern in live if pattern.fullmatch(path)]
                self.assertEqual(matching, [],
                                 f"{path} would be forwarded by the baseline proxy; the P3 "
                                 f"dependency would then not be missing and this test is stale")


class DashboardTypesMatchTheContract(unittest.TestCase):
    """The closed loop: P3 branch -> frozen snapshot -> dashboard types.

    The snapshot guard proves the contract still matches P3. This proves the TypeScript
    the views render against still matches the snapshot, so an added or renamed P3 field
    cannot leave the UI quietly rendering a shape it no longer has.
    """

    INTERFACES = {"Checkpoint": "checkpoint", "Resume": "resume", "ContextPack": "context"}

    def test_typescript_interfaces_carry_exactly_the_contract_fields(self) -> None:
        source = (ROOT / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for interface, key in self.INTERFACES.items():
            with self.subTest(interface=interface):
                body = re.search(r"export interface " + interface + r" \{(.*?)\n\}",
                                 source, re.S)
                self.assertIsNotNone(body, f"{interface} is missing from types.ts")
                # Field names may contain digits (`rendered_sha256`).
                declared = set(re.findall(r"^\s+([a-z][a-z0-9_]*)\??:", body.group(1), re.M))
                expected = set(snapshot["response_fields"][key])
                self.assertEqual(sorted(expected - declared), [],
                                 f"{interface} is missing contract fields")
                self.assertEqual(sorted(declared - expected), [],
                                 f"{interface} declares fields the contract does not have")

    def test_the_dependency_status_is_written_where_a_reader_will_see_it(self) -> None:
        source = (ROOT / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
        self.assertIn("DEPENDENCY PENDING", source)
        self.assertIn("p3-continuity-read.v1.json", source,
                      "the types must name the snapshot they came from")


class P3ReadsAreGatedNotImplicit(unittest.TestCase):
    """The pending paths live in their own section, and become live only on request.

    The JSON cannot prove the runtime behaviour — `web/tests/p3Reads.test.ts` does that
    against the real matcher — but it can prove the *shape* the loader depends on, and
    that nobody has quietly moved the four patterns into the live list.
    """

    def setUp(self) -> None:
        self.allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        self.snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    def test_pending_paths_are_present_and_separate(self) -> None:
        pending = self.allowlist.get("p3_pending_paths")
        self.assertIsInstance(pending, list)
        self.assertEqual(len(pending), 4)
        for pattern in pending:
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, self.allowlist["paths"],
                                 "a pending P3 path must not be in the live read list")
        self.assertIn("p3_pending_note", self.allowlist)

    def test_pending_patterns_cover_the_snapshot_routes(self) -> None:
        """Same four paths, deliberately stricter spelling.

        The snapshot keeps P3's own route spelling (`([^/]+)`); the allowlist tightening
        of `c7c5480` means the live list only ever spells real ids. So equality of strings
        would be the wrong assertion — what must hold is that both describe the same four
        paths, which is checked against real id-shaped samples.
        """
        body = "01M4B6VGC29ESRDRQR62DX8AK8"
        samples = [f"/v1/context/ctx_{body}",
                   f"/v1/tasks/tsk_{body}/checkpoints/latest",
                   f"/v1/checkpoints/ckp_{body}",
                   f"/v1/resumes/rms_{body}"]
        pending = [re.compile(p) for p in self.allowlist["p3_pending_paths"]]
        routes = [re.compile(r["path_pattern"]) for r in self.snapshot["read_routes"]]
        # Each sample is matched by exactly one of each, and both sets match all four — so
        # the gated patterns describe exactly the contracted paths and add none.
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(sum(1 for p in pending if p.fullmatch(sample)), 1)
                self.assertEqual(sum(1 for p in routes if p.fullmatch(sample)), 1)
        self.assertEqual(len(pending), len(routes))

    def test_pending_patterns_match_real_id_shaped_paths(self) -> None:
        # A pattern that matched nothing would 'gate' nothing, and the views would never
        # get past the proxy even on a P3 Core.
        body = "01M4B6VGC29ESRDRQR62DX8AK8"
        samples = [f"/v1/context/ctx_{body}",
                   f"/v1/tasks/tsk_{body}/checkpoints/latest",
                   f"/v1/checkpoints/ckp_{body}",
                   f"/v1/resumes/rms_{body}"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(any(re.compile(pattern).fullmatch(sample)
                                    for pattern in self.allowlist["p3_pending_paths"]))

    def test_the_note_says_why_they_are_not_live(self) -> None:
        note = self.allowlist["p3_pending_note"].lower()
        self.assertIn("p3", note)
        self.assertIn("p6_enable_p3_reads", note)
        # The reason a reader needs: this baseline's Core cannot serve them.
        self.assertIn("not served by this baseline", note)


if __name__ == "__main__":
    unittest.main()
