#!/usr/bin/env python3.13
"""The integration provenance stamp shared by the three frozen-contract generators.

P6-03's contracts are derived from **unmerged** dependency pins: the P4 read and write
contracts from a full P4 SHA, the P3 read contract from the P3 branch. The operator now
integrates those dependencies and requires the provenance to say so: a contract's content must
record the *integration* commit it was re-derived at, while still naming the dependency it came
from, and a machine-checkable record of whether the re-derivation changed anything.

So a generator given ``--derived-at <ref>`` derives its contract twice — once at its own
dependency ref, once at the integration ref — compares the two, and writes three fields into
``source``:

* ``derived_at_ref`` — the full SHA ``--derived-at`` resolved to;
* ``derived_at_identical`` — whether the two derivations agree;
* ``derived_at_differences`` — the differing top-level keys, or ``source.<name>`` for a
  differing ``source`` sub-key, sorted.

The comparison ignores provenance, not content. ``source.ref``/``source.commit`` name where the
contract came from and would differ by construction; ``source.derived_at_*`` is the stamp being
computed; and the schema-version fields track the *dependency's* schema, which the stamp does
not re-derive. Everything else is compared, so a content change at the integration commit is
recorded rather than hidden behind an identical-looking stamp.

Without ``--derived-at`` the fields are absent entirely, so the checked-in frozen files and
every existing invocation stay byte-identical. ``--check`` strips only the stamp before
comparing, so it passes against a frozen file with or without it while still failing on a wrong
dependency pin.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

#: The stamp itself. ``--check`` ignores exactly these and nothing else.
DERIVED_AT_KEYS = ("derived_at_ref", "derived_at_identical", "derived_at_differences")

#: Provenance that must not count as a content difference between the two derivations.
IGNORED_SOURCE_KEYS = frozenset({
    "ref",
    "commit",
    *DERIVED_AT_KEYS,
    "schema_version_in_p4",
    "schema_version_in_p3",
})

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def resolve_commit(ref: str) -> str:
    """The full commit SHA ``ref`` names, refusing anything that is not one.

    A stamp is only checkable if it is the complete SHA the integration worktree actually
    derived at: a branch name, an abbreviated SHA or a tree-ish would let it move.
    """
    resolved = subprocess.run(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
    if not _FULL_SHA.fullmatch(resolved):
        raise ValueError(f"git rev-parse {ref}^{{commit}} returned {resolved!r}, which is not "
                         f"a full 40-hex commit SHA")
    return resolved


def content_differences(dependency: dict[str, Any],
                        integration: dict[str, Any]) -> list[str]:
    """Keys whose *content* differs between the dependency and integration derivations.

    Top-level keys are reported as themselves; a differing ``source`` sub-key is reported as
    ``source.<name>`` so one changed schema-version citation cannot hide inside a large object.
    """
    differences = [key for key in set(dependency) | set(integration)
                   if key != "source" and dependency.get(key) != integration.get(key)]
    dependency_source = dependency.get("source", {})
    integration_source = integration.get("source", {})
    differences += [f"source.{key}" for key in set(dependency_source) | set(integration_source)
                    if key not in IGNORED_SOURCE_KEYS
                    and dependency_source.get(key) != integration_source.get(key)]
    return sorted(differences)


def apply_derived_at(contract: dict[str, Any], derived_at_ref: str,
                     integration: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``contract`` with the integration ref and what re-deriving there changed.

    ``contract`` stays the dependency's derivation — only ``source`` gains the stamp — so the
    frozen file keeps naming the dependency it was derived from.
    """
    differences = content_differences(contract, integration)
    contract["source"]["derived_at_ref"] = derived_at_ref
    contract["source"]["derived_at_identical"] = not differences
    contract["source"]["derived_at_differences"] = differences
    return contract


def without_derived_at(contract: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``contract`` with the stamp removed, for the drift guard's comparison.

    Only the stamp is removed: ``source.ref``/``source.commit`` are still compared, so pinning
    the wrong dependency still fails.
    """
    stripped = dict(contract)
    stripped["source"] = {key: value
                          for key, value in contract.get("source", {}).items()
                          if key not in DERIVED_AT_KEYS}
    return stripped
