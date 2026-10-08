"""P6-03b2 guards: the frozen contract is still P4's, and the surface is closed by default.

The specification was written before the implementation so the payloads and permissions were
settled on paper. The surface is now built behind its own switch, so "nothing of it has leaked
in" would be the wrong claim and the guards say what is true instead:

1. the frozen contract is re-derived from the pinned commit and diffed, and the CLI exits **2**
   where that commit is absent rather than passing because it could not look;
2. the six writes are exactly the ones the read contract recorded as out of its slice — a write
   quietly moving into the read slice, or a read into the write spec, fails here;
3. the six are reachable **only** through the gated `p4_write_paths` list: absent from the live
   read list and from the always-on milestone-B write list, never reachable with GET;
4. the client issues exactly the six, and the specification documents what is built, what is
   verified against a simulation, and that criterion 9b (a real P4 Core) is unmet.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_contract_provenance_stamp import assert_derived_at_stamp  # noqa: E402

SPEC_PATH = ROOT / "web" / "contracts" / "p4-memory-write.v1.json"
READ_PATH = ROOT / "web" / "contracts" / "p4-memory-read.v1.json"
ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
ENDPOINTS = ROOT / "web" / "src" / "api" / "endpoints.ts"
SPEC_DOC = ROOT / "web" / "P6-03B2-WRITE-SPEC.md"
SCRIPT = ROOT / "scripts" / "p6" / "p4_write_spec.py"

SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
READ = json.loads(READ_PATH.read_text(encoding="utf-8"))
PINNED = SPEC["source"]["ref"]


class P4WriteSpecIsStillP4s(unittest.TestCase):
    def test_source_is_a_pinned_full_sha(self) -> None:
        self.assertRegex(PINNED, r"^[0-9a-f]{40}$")
        self.assertEqual(PINNED, READ["source"]["ref"],
                         "the write spec and the read contract must pin the same commit, or the "
                         "two halves describe different P4s")

    def test_the_integration_stamp_is_absent_or_consistent(self) -> None:
        """The stamp adds provenance without replacing the pinned dependency.

        It is absent until the integration worktree regenerates the contract; once present it
        must be complete, name only a full SHA, and its `identical` flag must agree with its own
        difference list.
        """
        assert_derived_at_stamp(
            self, SPEC["source"],
            dependency_ref="0c4f8f0e2118711ac9aa2a7006fddc6982962248")

    def test_the_contract_keeps_p4_acceptance_unpassed_and_points_at_the_status_doc(self) -> None:
        """The contract records P4's facts; our implementation status lives elsewhere.

        It used to carry "nothing here is implemented" among the recorded semantics. That entry
        was never P4's behaviour — it described *this dashboard* — and once the write surface was
        built it became a false statement inside a frozen artefact. It is now excluded by a rule
        rather than by memory: every recorded semantic must cite a P4 source file.
        """
        self.assertIn("NOT passed", SPEC["source"]["acceptance"])
        self.assertIn("P6-03B2-COMPLIANCE.md", SPEC["status"],
                      "the contract must point at where implementation status is actually tracked")
        for entry in SPEC["semantics"]["recorded"]:
            with self.subTest(key=entry["key"]):
                self.assertRegex(entry["statement"], r"Sources?: .*\.py",
                                 "a recorded semantic without a P4 source is not P4's behaviour")

    def test_no_implementation_status_is_recorded_among_p4_semantics(self) -> None:
        keys = {entry["key"] for entry in SPEC["semantics"]["recorded"]}
        self.assertNotIn("not_implemented_yet", keys)
        self.assertEqual(len(keys), 7)

    def test_it_is_the_six_writes_the_read_slice_excluded(self) -> None:
        """A write moving into the read slice, or a read into this spec, fails here."""
        spec_writes = {(w["method"], w["path_pattern"]) for w in SPEC["writes"]}
        read_excluded = {(w["method"], w["path_pattern"]) for w in READ["not_in_this_slice"]}
        self.assertEqual(spec_writes, read_excluded)
        self.assertEqual(len(spec_writes), 6)
        self.assertTrue(all(method == "POST" for method, _ in spec_writes))

    def test_scopes_split_management_from_reflect(self) -> None:
        by_scope: dict[str, list[str]] = {}
        for write in SPEC["writes"]:
            by_scope.setdefault(write["scope"], []).append(write["path_pattern"])
        self.assertEqual(set(by_scope), {"memory:manage", "memory:reflect"})
        self.assertEqual(len(by_scope["memory:manage"]), 5)
        self.assertEqual(by_scope["memory:reflect"], [r"^/v1/memory/reflect$"],
                         "reflect has its own scope on purpose, so it can be withheld "
                         "independently of lifecycle management")

    def test_reflect_is_marked_as_the_generator(self) -> None:
        generator = [w for w in SPEC["writes"] if w["is_generator"]]
        self.assertEqual(len(generator), 1)
        self.assertEqual(generator[0]["path_pattern"], r"^/v1/memory/reflect$")

    def test_the_body_rules_match_p4(self) -> None:
        """The two facts that were wrong on the first derivation, pinned so they cannot come back.

        `expected_revision` is enforced by a *helper* (`_positive_revision`), so reading only the
        handler missed it; and `event_id`/`reason` are validated only `if ... is not None`, so
        treating every `field=` raise as proof of requirement listed two optional fields as
        required. Both directions were wrong at once.
        """
        lifecycle = [w for w in SPEC["writes"] if w["scope"] == "memory:manage"
                     and "/items/" in w["path_pattern"]]
        self.assertEqual(len(lifecycle), 4)
        for write in lifecycle:
            with self.subTest(pattern=write["path_pattern"]):
                self.assertEqual(write["required_body_fields"],
                                 ["expected_revision", "host_id"])
                self.assertEqual(write["allowed_body_fields"],
                                 ["event_id", "expected_revision", "host_id", "reason"])
                self.assertEqual(write["id_prefix"], "mem")
        retry = next(w for w in SPEC["writes"] if w["path_pattern"].endswith("/retry$"))
        # `request_retry` never validates either body field, so neither is required.
        self.assertEqual(retry["required_body_fields"], [])
        self.assertEqual(retry["id_prefix"], "mjb")
        reflect = next(w for w in SPEC["writes"] if w["is_generator"])
        self.assertEqual(reflect["required_body_fields"], ["project_id", "query"])

    def test_both_lifecycle_response_shapes_are_recorded(self) -> None:
        """A fresh transition and a replay differ; one shape would break on the other."""
        for write in SPEC["writes"]:
            if "/items/" not in write["path_pattern"]:
                continue
            with self.subTest(pattern=write["path_pattern"]):
                shapes = sorted((len(s), tuple(s)) for s in write["response_shapes"])
                self.assertEqual([n for n, _ in shapes], [5, 7])
                bigger = next(keys for n, keys in shapes if n == 7)
                smaller = next(keys for n, keys in shapes if n == 5)
                self.assertEqual(set(bigger) - set(smaller), {"before", "after"})

    def test_snapshot_matches_the_pinned_commit(self) -> None:
        try:
            subprocess.run(["git", "cat-file", "-e", f"{PINNED}^{{commit}}"], cwd=ROOT,
                           check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise unittest.SkipTest(
                f"the pinned P4 commit {PINNED[:12]} is not in this checkout "
                f"({error.__class__.__name__})") from error
        result = subprocess.run([sys.executable, str(SCRIPT), "--check", "--ref", PINNED],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_exits_two_when_the_commit_is_absent(self) -> None:
        result = subprocess.run([sys.executable, str(SCRIPT), "--check", "--ref", "0" * 40],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class ImplementedButClosedByDefault(unittest.TestCase):
    """"Built behind a default-off switch" has to mean something a test can check.

    The three properties that make the write surface acceptable to ship while P4 is unmerged
    are each checked against the artefact that enforces them, not asserted in prose:

    1. the six paths are reachable **only** through the gated `p4_write_paths` list, and are in
       neither the live read list nor the always-on milestone B write list;
    2. the client issues exactly the six writes and nothing else — no retain, no bank selector,
       no `idempotency_key`;
    3. the specification document says what is built, what is not verified against a real Core,
       and still carries the acceptance criteria a real P4 Gate must meet.
    """

    def test_no_write_path_is_in_the_live_allowlist_or_the_always_on_write_list(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        live = [re.compile(pattern) for pattern in allowlist["paths"]]
        always_on = [re.compile(pattern) for pattern in allowlist["write_paths"]]
        gated = [re.compile(pattern) for pattern in allowlist["p4_write_paths"]]
        for write in SPEC["writes"]:
            compiled = re.compile(write["path_pattern"])
            with self.subTest(pattern=write["path_pattern"]):
                # Behaviourally, with the literals those patterns match.
                sample = sample_path(write)
                self.assertFalse(any(pattern.fullmatch(sample) for pattern in live),
                                 f"{sample} would be forwarded as a read")
                self.assertFalse(any(pattern.fullmatch(sample) for pattern in always_on),
                                 f"{sample} would be forwarded with the milestone B writes")
                self.assertTrue(any(pattern.fullmatch(sample) for pattern in gated),
                                f"{sample} has no gated proxy pattern, so it could never be "
                                f"reached even by an operator who enabled the writes")

    def test_the_gated_list_covers_exactly_the_six_contract_routes(self) -> None:
        """Three proxy patterns for six routes: the four lifecycle commands share one.

        The count is not the invariant — coverage is. Every contract route must match exactly one
        gated pattern (so a route with no pattern could never be reached, and a route matched by
        two would mean the list had been edited incoherently), and the number of patterns must
        stay small enough that the six routes are still readable.
        """
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        gated = [re.compile(pattern) for pattern in allowlist["p4_write_paths"]]
        self.assertEqual(len(gated), 3, "four lifecycle commands should share one pattern")
        for write in SPEC["writes"]:
            with self.subTest(pattern=write["path_pattern"]):
                self.assertEqual(write["method"], "POST")
                sample = sample_path(write)
                matches = [pattern.pattern for pattern in gated if pattern.fullmatch(sample)]
                self.assertEqual(len(matches), 1, f"{sample} matched by {matches}")

    def test_no_write_path_is_among_the_pending_reads(self) -> None:
        """The pending list is for reads. A write joining it would be a quiet scope expansion."""
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        pending = [re.compile(pattern) for pattern in allowlist["p4_pending_paths"]]
        for write in SPEC["writes"]:
            compiled = re.compile(write["path_pattern"])
            with self.subTest(pattern=write["path_pattern"]):
                self.assertFalse(any(compiled.pattern == pattern.pattern for pattern in pending))

    def test_the_client_issues_exactly_the_six_writes(self) -> None:
        source = ENDPOINTS.read_text(encoding="utf-8")
        start = source.index("// P6-03b2: the six memory writes.")
        section = source[start:source.index("listHosts:")]
        calls = re.findall(r"client\.(get|post|put|delete)<", section)
        self.assertEqual(set(calls), {"post"}, "the b2 section is write-only")
        self.assertEqual(len(calls), 3, "three helpers: retry, lifecycle, reflect")
        # The lifecycle helper takes the command as a parameter, so the six routes appear as
        # one pattern plus the four command literals in the router/panels.
        self.assertIn("/memory/jobs/${id(jobId)}/retry", section)
        self.assertIn("/memory/items/${id(memoryId)}/${command}", section)
        self.assertIn("'/memory/reflect'", section)
        # And nothing outside the frozen surface.
        for forbidden in ("/memory/retain", "bankSelector", "idempotency_key", "memory/obsolete'"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, section)

    def test_the_spec_document_records_what_is_built_and_what_is_not(self) -> None:
        doc = SPEC_DOC.read_text(encoding="utf-8")
        # It must not still claim to be unimplemented, and it must not imply P4 was verified.
        self.assertNotIn("SPECIFICATION ONLY. Nothing here is implemented", doc)
        self.assertIn("P6_ENABLE_P4_WRITES", doc)
        self.assertIn("P4 phase acceptance has not passed", doc)
        self.assertIn("P6-03B2-COMPLIANCE.md", doc)
        # The acceptance criteria have to actually be there, or the document is decoration.
        for marker in ("Acceptance criteria, before any of it ships", "replayed", "expected_revision",
                       "forbidden_actor_kind"):
            with self.subTest(marker=marker):
                self.assertIn(marker, doc)


def sample_path(write: dict) -> str:
    """A literal path for a contract pattern, using the route's own id prefix."""
    prefix = write["id_prefix"] or ""
    body = "01M4B6VGC29ESRDRQR62DX8AK8"
    # Strip only the anchors: `replace("^", "")` also ate the negation caret inside `([^/]+)`,
    # which left the sample matching nothing and the assertion failing for the wrong reason.
    path = write["path_pattern"]
    if path.startswith("^"):
        path = path[1:]
    if path.endswith("$"):
        path = path[:-1]
    path = re.sub(r"\(\[\^/\]\+\)", f"{prefix}_{body}" if prefix else body, path)
    return path
