"""P6-03b2 guards: the write specification is still P4's, and nothing of it has leaked in.

The specification is written before the implementation precisely so the payloads and permissions
are settled on paper. That only helps if it stays true, so:

1. the frozen spec is re-derived from the pinned commit and diffed, and the CLI exits **2** where
   that commit is absent rather than passing because it could not look;
2. the six writes are exactly the ones the read contract recorded as out of its slice — a write
   quietly moving into the read slice, or a read into the write spec, fails here;
3. **none of the six is reachable today**: not in the proxy's live allowlist, not among the pending
   reads, and not in the client. That is what "specification only" has to mean, and an
   untested claim of it would be worth nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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

    def test_the_spec_says_nothing_is_implemented_and_acceptance_is_unpassed(self) -> None:
        self.assertIn("SPECIFICATION ONLY", SPEC["status"])
        self.assertIn("NOT passed", SPEC["source"]["acceptance"])
        recorded = " ".join(s["statement"] for s in SPEC["semantics"]["recorded"])
        self.assertIn("Nothing in this specification is built", recorded,
                      "the spec must say in its own recorded semantics that none of it exists yet")

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


class NoneOfItIsReachableYet(unittest.TestCase):
    """"Specification only" has to mean something a test can check."""

    def test_no_write_path_is_in_the_live_allowlist(self) -> None:
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        live = [re.compile(pattern) for pattern in allowlist["paths"]]
        for write in SPEC["writes"]:
            compiled = re.compile(write["path_pattern"])
            with self.subTest(pattern=write["path_pattern"]):
                self.assertFalse(any(compiled.pattern == pattern.pattern for pattern in live))
        # And behaviourally, with the literals those patterns match.
        samples = ["/v1/memory/jobs/mjb_01M4B6VGC29ESRDRQR62DX8AK8/retry",
                   "/v1/memory/items/mem_01M4B6VGC29ESRDRQR62DX8AK8/obsolete",
                   "/v1/memory/items/mem_01M4B6VGC29ESRDRQR62DX8AK8/delete",
                   "/v1/memory/items/mem_01M4B6VGC29ESRDRQR62DX8AK8/pin",
                   "/v1/memory/items/mem_01M4B6VGC29ESRDRQR62DX8AK8/unpin",
                   "/v1/memory/reflect"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(any(pattern.fullmatch(sample) for pattern in live),
                                 f"{sample} would be forwarded by the baseline proxy")

    def test_no_write_path_is_among_the_pending_reads(self) -> None:
        """The pending list is for reads. A write joining it would be a quiet scope expansion."""
        allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        pending = [re.compile(pattern) for pattern in allowlist["p4_pending_paths"]]
        for write in SPEC["writes"]:
            compiled = re.compile(write["path_pattern"])
            with self.subTest(pattern=write["path_pattern"]):
                self.assertFalse(any(compiled.pattern == pattern.pattern for pattern in pending))

    def test_the_client_has_no_memory_write(self) -> None:
        source = ENDPOINTS.read_text(encoding="utf-8")
        start = source.index("memoryHealth:")
        section = source[start:source.index("listHosts:")]
        self.assertEqual(set(re.findall(r"client\.(get|post|put|delete)<", section)), {"get"},
                         "the client must not be able to issue a b2 write yet")
        for forbidden in ("/memory/reflect", "/retry", "/obsolete", "/pin", "/unpin",
                          "expected_revision"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, section)

    def test_the_spec_document_says_specification_only(self) -> None:
        doc = SPEC_DOC.read_text(encoding="utf-8")
        self.assertIn("SPECIFICATION ONLY", doc)
        self.assertIn("P4 phase acceptance has not passed", doc)
        # The acceptance criteria have to actually be there, or the document is decoration.
        for marker in ("Acceptance criteria, before any of it ships", "replayed", "expected_revision",
                       "ForbiddenActorKind"):
            with self.subTest(marker=marker):
                self.assertIn(marker, doc)


if __name__ == "__main__":
    unittest.main()
