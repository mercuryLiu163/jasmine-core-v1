#!/usr/bin/env python3.13
"""Freeze an evidence bundle, and verify a frozen one byte for byte.

The rule the P6 bundles follow is **"written once, then left alone"**: corrections go to a new
timestamped directory rather than editing a bundle a reviewer may already have read. That rule
is only worth anything if the freeze is checkable, and until now `BUNDLE-FROZEN.json` was
written by hand during each run — which is exactly the kind of step that silently drifts.

Two modes:

* `--freeze` writes `BUNDLE-FROZEN.json` over the bundle's own files, in sorted relative-path
  order, with each file's size and sha256 plus the binding facts (commit, asset digest, tree
  digest, fixture label). `BUNDLE-FROZEN.json` is never listed inside itself.
* `--verify` re-hashes the files and compares them with the manifest. Exit **1** on any
  mismatch (a changed, missing or added file), and exit **2** when there is no manifest at all —
  the same rule the contract checks follow, so "could not look" can never be mistaken for
  "verified".

Deliberately not a gate: it says nothing about whether the evidence is *good*, only whether it
is the same bytes that were frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "BUNDLE-FROZEN.json"
#: Written by the run itself and regenerated per invocation; it is evidence, so it is frozen.
#: `BUNDLE-FROZEN.json` is the only file excluded, because it cannot hash itself.
EXCLUDED = {MANIFEST}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_files(bundle: Path) -> list[Path]:
    return sorted(
        (path for path in bundle.rglob("*") if path.is_file() and path.name not in EXCLUDED),
        key=lambda path: path.relative_to(bundle).as_posix(),
    )


def build_manifest(bundle: Path, *, commit: str, kind: str, asset: str | None,
                   asset_sha256: str | None, tree_sha256: str | None,
                   fixture_header: str | None, fixture_value: str | None,
                   runs: str | None, supersedes: str | None,
                   dependency: dict | None, ran_from: str | None = None) -> dict:
    files = [
        {
            "path": path.relative_to(bundle).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in bundle_files(bundle)
    ]
    manifest: dict = {
        "frozen_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "commit": commit,
        "kind": kind,
        # Caller-supplied, with a deliberately neutral default. An earlier version wrote "an isolated
        # git archive of the commit, its own fixture and proxy" for every bundle, which was false for
        # runs made in a worktree (the suites, the failure attributions) — an independent review
        # caught a manifest contradicting its own README. A manifest must not assert a provenance the
        # caller did not state.
        "ran_from": ran_from or "not stated by the caller — the bundle's own README says where the "
                                "run happened",
    }
    if runs:
        manifest["runs"] = runs
    if supersedes:
        manifest["supersedes"] = supersedes
    if asset:
        manifest["product_bundle"] = {
            "name": asset,
            "sha256": asset_sha256,
            "dist_tree_sha256_relative_paths": tree_sha256,
        }
    if dependency:
        manifest["dependency"] = dependency
    if fixture_header:
        manifest["simulation_label"] = {
            "header": fixture_header,
            "value": fixture_value,
            "recorded_in": "browser-requests.jsonl as fixtureLabel on every response",
        }
    manifest["policy"] = "written once, then left alone; corrections go to a new timestamped directory"
    manifest["files"] = files
    return manifest


def verify(bundle: Path) -> int:
    manifest_path = bundle / MANIFEST
    if not manifest_path.is_file():
        print(f"no {MANIFEST} in {bundle}: nothing to verify against", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = {entry["path"]: entry for entry in manifest.get("files", [])}
    problems: list[str] = []
    present: set[str] = set()
    for path in bundle_files(bundle):
        relative = path.relative_to(bundle).as_posix()
        present.add(relative)
        entry = recorded.get(relative)
        if entry is None:
            problems.append(f"added since the freeze: {relative}")
            continue
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            problems.append(f"changed since the freeze: {relative} ({entry['sha256'][:12]} -> {digest[:12]})")
        elif path.stat().st_size != entry["bytes"]:
            problems.append(f"size differs without a digest change: {relative}")
    for relative in sorted(set(recorded) - present):
        problems.append(f"missing since the freeze: {relative}")
    if problems:
        print(f"{bundle} does not match its frozen manifest:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"{bundle}: {len(recorded)} files match {MANIFEST} "
          f"({manifest.get('commit', '?')[:12]})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="the bundle directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true", help="write BUNDLE-FROZEN.json")
    mode.add_argument("--verify", action="store_true", help="check the bundle against its manifest")
    parser.add_argument("--commit", help="the full commit SHA the bundle binds")
    parser.add_argument("--kind", help="what kind of gate this bundle is")
    parser.add_argument("--runs", help="e.g. '11/11, twice with identical step sequences'")
    parser.add_argument("--supersedes", help="the bundle this one replaces")
    parser.add_argument("--asset", help="product asset file name the run served")
    parser.add_argument("--asset-sha256", help="sha256 of that asset")
    parser.add_argument("--tree-sha256", help="sha256 over the sorted 'sha256  ./path' lines of dist")
    parser.add_argument("--fixture-header", default="X-P6-Fixture")
    parser.add_argument("--fixture-value", help="the simulation label stamped on every reply")
    parser.add_argument("--dependency-json", help="JSON object describing the dependency status")
    parser.add_argument("--ran-from", dest="ran_from",
                        help="where the run happened, stated by the caller; the manifest must not "
                             "invent a provenance (see the default in `build_manifest`)")
    args = parser.parse_args(argv)

    bundle: Path = args.bundle
    if args.verify:
        return verify(bundle)
    if not bundle.is_dir():
        parser.error(f"{bundle} is not a directory")
    if not args.commit or not args.kind:
        parser.error("--freeze needs --commit and --kind")
    dependency = json.loads(args.dependency_json) if args.dependency_json else None
    manifest = build_manifest(
        bundle,
        commit=args.commit,
        kind=args.kind,
        asset=args.asset,
        asset_sha256=args.asset_sha256,
        tree_sha256=args.tree_sha256,
        fixture_header=args.fixture_header if args.fixture_value else None,
        fixture_value=args.fixture_value,
        runs=args.runs,
        supersedes=args.supersedes,
        dependency=dependency,
        ran_from=args.ran_from,
    )
    (bundle / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"froze {len(manifest['files'])} files in {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
