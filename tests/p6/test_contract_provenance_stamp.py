"""Guards for the integration provenance stamp the three frozen contracts carry.

Each contract records, in `source`, the full integration commit its content was re-derived at
(`derived_at_ref`), whether that re-derivation changed anything (`derived_at_identical`) and
which keys changed (`derived_at_differences`). The stamp is optional until the integration
worktree regenerates a contract, so the guard has to accept both states and reject a
half-written one, and the comparison behind it must compare *content* rather than provenance.

The per-contract tests in `test_p4_contract_snapshot.py`, `test_p4_write_spec.py` and
`test_p3_contract_snapshot.py` import `assert_derived_at_stamp`. The tests below exercise the
comparison directly, because every ref locally available is content-identical to its
dependency, so no generator run can prove that a difference is actually recorded.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

from contract_provenance import (  # noqa: E402
    DERIVED_AT_KEYS,
    apply_derived_at,
    content_differences,
    without_derived_at,
)


def assert_derived_at_stamp(case: unittest.TestCase, source: dict[str, Any], *,
                            dependency_ref: str,
                            dependency_commit: str | None = None) -> None:
    """Assert the stamp is all-present-or-all-absent, consistent, and additive.

    `dependency_ref`/`dependency_commit` are the *original* dependency the contract was derived
    from — the P4 SHA, or the P3 branch plus the commit it resolved to. The stamp records where
    the content was re-derived; it must never replace that.
    """
    case.assertEqual(source.get("ref"), dependency_ref,
                     "the stamp must not replace the dependency the contract came from")
    if dependency_commit is not None:
        case.assertEqual(source.get("commit"), dependency_commit,
                         "the stamp must not replace the dependency commit")

    present = [key for key in DERIVED_AT_KEYS if key in source]
    case.assertIn(present, ([], list(DERIVED_AT_KEYS)),
                  "source.derived_at_* must be all present or all absent, not half-written")
    if not present:
        return

    case.assertRegex(source["derived_at_ref"], r"^[0-9a-f]{40}$",
                     "derived_at_ref must be the full integration commit, not a branch or an "
                     "abbreviation")
    case.assertIsInstance(source["derived_at_identical"], bool)
    differences = source["derived_at_differences"]
    case.assertIsInstance(differences, list)
    case.assertTrue(all(isinstance(difference, str) for difference in differences),
                    "derived_at_differences must be a list of key names")
    case.assertEqual(source["derived_at_identical"], differences == [],
                     "derived_at_identical must mean exactly 'the two derivations agree'")


class ProvenanceStampMechanism(unittest.TestCase):
    """The stamp's comparison, on a stand-in with the contracts' `source`-plus-content shape."""

    def contract(self) -> dict[str, Any]:
        return {
            "contract": "example.v1",
            "source": {
                "ref": "0" * 40,
                "kind": "pinned commit (full SHA, not a branch)",
                "schema_version_in_p4": 11,
                "derived_by": "scripts/p6/example.py",
                "acceptance": "not passed",
            },
            "read_routes": [{"path_pattern": "^/v1/example$"}],
        }

    def test_provenance_is_not_a_content_difference(self) -> None:
        """Re-deriving at a commit that only changes the stamp must report no difference."""
        dependency = self.contract()
        integration = copy.deepcopy(dependency)
        integration["source"].update({
            "ref": "1" * 40,
            "commit": "2" * 40,
            "schema_version_in_p4": 12,
            "schema_version_in_p3": 12,
            "derived_at_ref": "3" * 40,
            "derived_at_identical": True,
            "derived_at_differences": [],
        })
        self.assertEqual(content_differences(dependency, integration), [])

    def test_a_real_change_is_reported_by_key(self) -> None:
        dependency = self.contract()
        integration = copy.deepcopy(dependency)
        integration["read_routes"] = []
        integration["source"]["kind"] = "branch"
        self.assertEqual(content_differences(dependency, integration),
                         ["read_routes", "source.kind"],
                         "top-level keys and source sub-keys must both be named, sorted")

    def test_the_stamp_records_where_and_whether(self) -> None:
        dependency = self.contract()
        integration = copy.deepcopy(dependency)
        integration["read_routes"] = []
        apply_derived_at(dependency, "4" * 40, integration)
        self.assertEqual(dependency["source"]["derived_at_ref"], "4" * 40)
        self.assertIs(dependency["source"]["derived_at_identical"], False)
        self.assertEqual(dependency["source"]["derived_at_differences"], ["read_routes"])
        self.assertEqual(dependency["source"]["ref"], "0" * 40,
                         "stamping must leave the dependency's own ref in place")

    def test_stripping_removes_only_the_stamp(self) -> None:
        dependency = self.contract()
        integration = copy.deepcopy(dependency)
        apply_derived_at(dependency, "4" * 40, integration)
        stripped = without_derived_at(dependency)
        self.assertEqual(set(stripped["source"]) & set(DERIVED_AT_KEYS), set())
        self.assertEqual(stripped["source"]["ref"], "0" * 40,
                         "the drift guard must still compare the dependency pin")
        self.assertEqual(stripped["read_routes"], dependency["read_routes"])
        self.assertEqual(stripped["contract"], dependency["contract"])


if __name__ == "__main__":
    unittest.main()
