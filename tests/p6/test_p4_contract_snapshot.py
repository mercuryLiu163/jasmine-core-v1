"""P6-03b1 contract guards: the P4 snapshot, its provenance, and the proxy's gates.

Three properties, each a way this slice could quietly stop telling the truth:

1. **The snapshot is still the pinned commit's contract.** It is re-derived from the source
   and diffed. Where the pinned commit is absent the test skips *loudly* — and the CLI
   itself exits 2 rather than 0, which is asserted directly, because P6-03a learned that a
   drift guard which passes when it could not look is worse than no guard.
2. **The dependency is still missing, and still unmerged.** The baseline must serve none of
   the seven routes, and the P4 writes must be recorded as out of this slice rather than
   having crept into the read list.
3. **The pending paths cannot become live by drift.** `p4_pending_paths` is a separate list
   from `paths`, the live list is untouched, and none of the seven is reachable on a
   baseline proxy.
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
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jasmine_core.api.handlers import ROUTES  # noqa: E402

SNAPSHOT_PATH = ROOT / "web" / "contracts" / "p4-memory-read.v1.json"
ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
TYPES_PATH = ROOT / "web" / "src" / "api" / "types.ts"
SCRIPT = ROOT / "scripts" / "p6" / "p4_contract.py"

SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
PINNED = SNAPSHOT["source"]["ref"]

READ_PATHS = (
    "/v1/memory/health",
    "/v1/memory/jobs",
    f"/v1/memory/jobs/mjb_{'01M4B6VGC29ESRDRQR62DX8AK8'}",
    "/v1/memory/items",
    f"/v1/memory/items/mem_{'01M4B6VGC29ESRDRQR62DX8AK8'}",
    "/v1/memory/traces",
    f"/v1/memory/traces/mtr_{'01M4B6VGC29ESRDRQR62DX8AK8'}",
)


def baseline_routes() -> list[tuple[str, re.Pattern[str]]]:
    return [(route.method, route.pattern) for route in ROUTES]


class P4ContractSnapshot(unittest.TestCase):
    def test_source_is_a_pinned_full_sha_not_a_branch(self) -> None:
        """A branch name would let the contract move under a `git pull`."""
        self.assertRegex(PINNED, r"^[0-9a-f]{40}$",
                         "the contract must be pinned to a full commit SHA")
        self.assertNotEqual(SNAPSHOT["source"]["ref"], "p4-integration",
                            "pinning to the branch name is what this guard exists to prevent")
        self.assertIn("pinned commit", SNAPSHOT["source"]["kind"])

    def test_snapshot_says_the_dependency_is_missing_and_acceptance_unpassed(self) -> None:
        self.assertIs(SNAPSHOT["baseline"]["these_routes_exist"], False)
        self.assertEqual(SNAPSHOT["baseline"]["schema_version"], 8)
        # The operator's requirement: pinned source, but P4 acceptance explicitly not passed.
        self.assertIn("NOT passed", SNAPSHOT["source"]["acceptance"])

    def test_snapshot_is_the_seven_read_endpoints_only(self) -> None:
        self.assertEqual(len(SNAPSHOT["read_routes"]), 7)
        self.assertTrue(all(route["method"] == "GET" for route in SNAPSHOT["read_routes"]))
        self.assertEqual({route["path_pattern"] for route in SNAPSHOT["read_routes"]},
                         {r"^/v1/memory/health$", r"^/v1/memory/jobs$",
                          r"^/v1/memory/jobs/([^/]+)$", r"^/v1/memory/items$",
                          r"^/v1/memory/items/([^/]+)$", r"^/v1/memory/traces$",
                          r"^/v1/memory/traces/([^/]+)$"})

    def test_every_read_requires_only_memory_read(self) -> None:
        """P4's reads need exactly their route scope: unlike P3 there is no shared helper.

        Worth asserting rather than assuming: P3's handlers *did* add five scopes through
        `_continuity_read`, and a contract that copied P3's shape would over-state this one.
        """
        for route in SNAPSHOT["read_routes"]:
            with self.subTest(pattern=route["path_pattern"]):
                self.assertEqual(route["scope"], "memory:read")
        self.assertEqual(SNAPSHOT["semantics"]["derived"]["read_scopes"], ["memory:read"])

    def test_the_writes_are_recorded_out_of_slice_not_in_the_read_list(self) -> None:
        writes = {(r["method"], r["path_pattern"]) for r in SNAPSHOT["not_in_this_slice"]}
        self.assertEqual(len(writes), 6)
        self.assertTrue(all(method == "POST" for method, _ in writes))
        self.assertEqual(
            {scope for r in SNAPSHOT["not_in_this_slice"] for scope in [r["scope"]]},
            {"memory:manage", "memory:reflect"},
            "retry and the lifecycle commands manage; reflect has its own scope")
        read_patterns = {r["path_pattern"] for r in SNAPSHOT["read_routes"]}
        self.assertFalse(read_patterns & {p for _, p in writes},
                         "a write must not appear in the read list")

    def test_absent_retain_and_bank_selector_routes(self) -> None:
        derived = SNAPSHOT["semantics"]["derived"]
        self.assertIs(derived["retain_endpoint_absent"], True)
        self.assertIs(derived["bank_selector_route_absent"], True)

    def test_non_authoritative_handlers_are_recorded(self) -> None:
        # Core emits the field itself on exactly these three; the views read it rather than
        # printing their own claim.
        self.assertEqual(set(SNAPSHOT["non_authoritative_handlers"]),
                         {"list_items", "get_item", "memory_health"})

    def test_snapshot_still_matches_the_pinned_commit(self) -> None:
        try:
            subprocess.run(["git", "cat-file", "-e", f"{PINNED}^{{commit}}"], cwd=ROOT,
                           check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise unittest.SkipTest(
                f"the pinned P4 commit {PINNED[:12]} is not in this checkout, so the snapshot "
                f"cannot be re-derived ({error.__class__.__name__}); the CLI exits 2 here, "
                f"this test can only skip") from error
        result = subprocess.run([sys.executable, str(SCRIPT), "--check", "--ref", PINNED],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"the P4 snapshot drifted from {PINNED}:\n{result.stdout}{result.stderr}")

    def test_check_exits_two_when_the_commit_is_absent(self) -> None:
        """Loud, not silent: a guard that passes because it could not look is worthless."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--ref", "0" * 40],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot be re-derived", result.stderr)


class BaselineDoesNotHaveP4Routes(unittest.TestCase):
    def test_baseline_core_serves_none_of_the_seven_endpoints(self) -> None:
        routes = baseline_routes()
        samples = ["/v1/memory/health", "/v1/memory/jobs", "/v1/memory/items",
                   "/v1/memory/traces", *READ_PATHS[2::2]]
        for path in samples:
            with self.subTest(path=path):
                matching = [(method, pattern.pattern) for method, pattern in routes
                            if pattern.fullmatch(path)]
                self.assertEqual(matching, [],
                                 f"{path} is served by this baseline; the P4 dependency would "
                                 f"then not be missing and this test is stale")

    def test_pending_paths_are_not_in_the_live_read_allowlist(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        live = [re.compile(pattern) for pattern in allowlist["paths"]]
        shipped = [re.compile(pattern) for pattern in allowlist["p4_pending_paths"]]
        self.assertEqual(len(shipped), 7)

        # Compare against the *shipped* pending patterns, not a loose re-spelling: the
        # snapshot writes ids loosely and the proxy strictly, and comparing the two misses
        # the case that matters — the strict pattern being copied into `paths`.
        for pattern in shipped:
            with self.subTest(pattern=pattern.pattern):
                self.assertFalse(any(pattern.pattern == live_pattern.pattern
                                     for live_pattern in live),
                                 f"{pattern.pattern} must not be in the baseline read list")

        # And behaviourally, with real id-shaped paths.
        for path in READ_PATHS:
            with self.subTest(path=path):
                matching = [pattern.pattern for pattern in live if pattern.fullmatch(path)]
                self.assertEqual(matching, [],
                                 f"{path} would be forwarded by the baseline proxy")

    def test_the_live_allowlist_is_unchanged_by_this_slice(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(allowlist["paths"]), 25,
                         "P6-03b1 must not add a live path; the reads are pending")
        self.assertEqual(len(allowlist["p3_pending_paths"]), 4, "P6-03a's list is untouched")

    def test_every_live_path_is_a_real_baseline_route(self) -> None:
        """The requirement's own guard, restated here so a P4 edit cannot weaken it.

        `test_dashboard_proxy_contract.py` owns this assertion for the whole allowlist;
        repeating it for the P4 slice means a careless `p4_pending_paths` -> `paths` move
        fails in the file that is about P6-03b1 too, not only in a P6-02 file.
        """
        from test_dashboard_proxy_contract import sample_path
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        samples = [sample_path(route.pattern) for route in ROUTES]
        self.assertTrue(all(sample is not None for sample in samples))
        for pattern in allowlist["paths"]:
            compiled = re.compile(pattern)
            with self.subTest(pattern=pattern):
                backed = [sample for sample in samples
                          if sample is not None and compiled.fullmatch(sample)]
                self.assertTrue(backed,
                                f"allowlist pattern {pattern!r} matches no real Core route")


class DashboardTypesMatchTheP4Contract(unittest.TestCase):
    """The closed loop: pinned commit -> frozen snapshot -> dashboard interfaces."""

    #: interface name in types.ts -> key in the snapshot's `nested_fields`
    INTERFACES = {
        "MemoryJob": "job",
        "MemoryJobAttempt": "attempt",
        "MemoryLifecycle": "lifecycle",
        "MemoryItem": "item",
        "MemoryTrace": "trace",
        "MemoryJobCounts": "job_counts",
        "MemoryHealthCounts": "health_items",
        # `MemoryHealth` is the health *response*: the report's 17 keys plus the handler's
        # `non_authoritative`. It is compared against the route's response fields below.
    }

    def _declared_fields(self, source: str, interface: str) -> set[str]:
        body = re.search(r"export interface " + interface + r" \{(.*?)\n\}",
                         source, re.S)
        self.assertIsNotNone(body, f"{interface} is not declared in types.ts")
        return set(re.findall(r"^\s{2}([a-z][a-z0-9_]*)\??:", body.group(1), re.M))

    def test_interfaces_carry_exactly_the_contract_fields(self) -> None:
        source = TYPES_PATH.read_text(encoding="utf-8")
        for interface, key in self.INTERFACES.items():
            with self.subTest(interface=interface):
                declared = self._declared_fields(source, interface)
                expected = set(SNAPSHOT["nested_fields"][key])
                self.assertEqual(
                    declared, expected,
                    f"{interface} and the contract disagree: "
                    f"missing {sorted(expected - declared)}, extra {sorted(declared - expected)}")

    def test_the_trace_detail_interface_is_the_list_row_plus_four(self) -> None:
        source = TYPES_PATH.read_text(encoding="utf-8")
        listed = self._declared_fields(source, "MemoryTrace")
        self.assertEqual(listed, set(SNAPSHOT["nested_fields"]["trace"]))
        self.assertEqual(len(listed), 16, "the list row is the 16 columns store.traces() reads")

    def test_the_health_interface_is_the_health_response(self) -> None:
        """Health is the one route whose body *is* the report; it adds `non_authoritative`."""
        source = TYPES_PATH.read_text(encoding="utf-8")
        declared = self._declared_fields(source, "MemoryHealth")
        route = next(r for r in SNAPSHOT["read_routes"] if r["response_key"] == "health")
        self.assertEqual(declared, set(route["response_fields"]))
        self.assertEqual(declared, set(SNAPSHOT["nested_fields"]["health"]) | {"non_authoritative"})

    def test_the_row_extensions_add_only_what_the_contract_records(self) -> None:
        source = TYPES_PATH.read_text(encoding="utf-8")
        # Each extension must add exactly what the contract says, named from the snapshot
        # rather than hardcoded: the trace detail adds four fields, not the one the first
        # draft of this contract recorded.
        trace_extra = set(SNAPSHOT["nested_fields"]["trace_detail"]) - set(
            SNAPSHOT["nested_fields"]["trace"])
        self.assertEqual(trace_extra,
                         {"filtered", "authority_refs", "state_revision", "items"})
        for interface, base, extra in (("MemoryItemListRow", "MemoryItem", {"lifecycle"}),
                                       ("MemoryTraceDetail", "MemoryTrace", trace_extra)):
            with self.subTest(interface=interface):
                body = re.search(r"export interface " + interface + r" extends " + base +
                                 r" \{(.*?)\n\}", source, re.S)
                self.assertIsNotNone(body, f"{interface} must extend {base}")
                self.assertEqual(set(re.findall(r"^\s{2}([a-z][a-z0-9_]*)\??:",
                                                body.group(1), re.M)), extra)

    def test_the_memory_endpoint_layer_issues_get_only(self) -> None:
        """A `POST` among the memory endpoints would be a b2 write in a b1 slice.

        Scoped to the memory section on purpose: `endpoints.ts` also carries P6-02's audited
        writes, so a whole-file check would either fail for the wrong reason or have to be
        weakened until it proved nothing.
        """
        source = (ROOT / "web" / "src" / "api" / "endpoints.ts").read_text(encoding="utf-8")
        start = source.index("memoryHealth:")
        end = source.index("listHosts:")
        section = source[start:end]
        self.assertIn("memoryHealth", section)
        self.assertEqual(set(re.findall(r"client\.(get|post|put|delete)<", section)), {"get"},
                         "the P4 endpoint layer issues GET only; writes are P6-03b2")
        for forbidden in ("/memory/reflect", "/retry", "/obsolete", "expected_revision"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, section,
                                 f"{forbidden!r} is a P6-03b2 write and must not appear in the "
                                 f"b1 endpoint layer")


if __name__ == "__main__":
    unittest.main()


class NoEngineAccessFromTheBrowser(unittest.TestCase):
    """Acceptance criterion 8, made checkable.

    The criterion was first written as "a grep over the bundle for engine credentials and
    endpoints comes back empty", which is **false as stated**: the bundle contains the word
    "Hindsight" three times, in disclaimers that say the browser does *not* touch it, and a
    word search cannot tell a disclaimer from a credential. What actually matters is that no
    engine origin, no engine credential identifier and no engine call exists — so that is what
    is asserted here, and the plan now says so.
    """

    #: Origins a bundled front-end legitimately carries: XML namespace URIs and React's own
    #: error-decoder link. Anything else would be a host this page could talk to.
    ALLOWED_ORIGINS = {
        "http://www.w3.org/1998/Math/MathML",
        "http://www.w3.org/1999/xhtml",
        "http://www.w3.org/1999/xlink",
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/XML/1998/namespace",
        "https://reactjs.org/docs/error-decoder.html",
    }

    def built_bundle(self) -> str:
        assets = sorted((ROOT / "web" / "dist" / "assets").glob("index-*.js"))
        if not assets:
            raise unittest.SkipTest(
                "no built bundle in web/dist; run `cd web && vite build` to check this")
        return assets[-1].read_text(encoding="utf-8", errors="replace")

    def test_the_bundle_can_only_talk_to_its_own_origin(self) -> None:
        origins = set(re.findall(r"https?://[A-Za-z0-9./_-]+", self.built_bundle()))
        self.assertEqual(origins - self.ALLOWED_ORIGINS, set(),
                         "the bundle carries an origin the dashboard has no business reaching")

    def test_the_bundle_carries_no_engine_credential_identifier(self) -> None:
        text = self.built_bundle().lower()
        for identifier in ("api_key", "apikey", "access_token", "refresh_token", "password=",
                           "client_secret"):
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, text,
                                 f"{identifier!r} suggests a credential the browser should "
                                 f"never hold")

    def test_the_memory_endpoints_stay_under_the_same_origin_client(self) -> None:
        """Every memory call goes through `client`, which is relative to our own origin."""
        source = (ROOT / "web" / "src" / "api" / "endpoints.ts").read_text(encoding="utf-8")
        start = source.index("memoryHealth:")
        section = source[start:source.index("listHosts:")]
        calls = re.findall(r"client\.get<[^>]*>\(\s*'([^']+)'", section)
        self.assertTrue(calls)
        for path in calls:
            with self.subTest(path=path):
                self.assertTrue(path.startswith("/memory/"),
                                f"{path!r} is not a same-origin memory path")
                self.assertNotIn("http", path)

    def test_no_engine_client_exists_in_the_slice(self) -> None:
        """The engine lives behind Core; a front-end module naming it would be the leak."""
        for path in ("web/src/views/MemoryViews.tsx", "web/src/views/MemoryDependency.tsx",
                     "web/src/api/endpoints.ts"):
            text = (ROOT / path).read_text(encoding="utf-8")
            for forbidden in ("hindsight", "engine_url", "engineUrl", "bank_selector",
                              "bankSelector", "MEMORY_ENGINE"):
                with self.subTest(path=path, forbidden=forbidden):
                    self.assertNotIn(forbidden, text.lower(),
                                     f"{path} names {forbidden!r}; the browser must not have an "
                                     f"engine client")
