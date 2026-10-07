#!/usr/bin/env python3.13
"""Derive the P3 continuity read contract from the P3 branch itself.

P6-03a is the read-only Checkpoint / Context Pack / Resume slice, and its dependency
(P3) is **not merged** into this baseline: `850fbec` is schema 8 and has no
`context/`, `continuity.py` or `task_snapshot.py` at all. The taskbook's rule for a
missing dependency is to work against a contract request and scope the verdict
accurately — which only means anything if the contract is the *real* one.

So this script derives the contract from the P3 ref rather than from anyone's memory:

* the four read routes and their scopes, parsed out of P3's `handlers.py` route table;
* the response field sets, parsed with `ast` out of the dict literals P3 actually
  builds — `continuity._persist` for a checkpoint and a resume, `context.builder
  ._persist` for a context pack — so a renamed or added field shows up as a diff
  instead of a quietly wrong view.

`--check` re-derives and compares against the checked-in snapshot, so drift is caught
where the branch is available (and skipped where it is not). `--write` regenerates it.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "web" / "contracts" / "p3-continuity-read.v1.json"

#: The four read-only endpoints of this slice, in the order the views present them.
#: The POST shapes (`context/build`, `checkpoints`, `tasks/{id}/resume`) are build
#: operations and are deliberately absent — this slice reads.
READ_ROUTES = (
    ("GET", r"^/v1/context/([^/]+)$", "context:read", "context"),
    ("GET", r"^/v1/tasks/([^/]+)/checkpoints/latest$", "checkpoint:read", "checkpoint"),
    ("GET", r"^/v1/checkpoints/([^/]+)$", "checkpoint:read", "checkpoint"),
    ("GET", r"^/v1/resumes/([^/]+)$", "resume:read", "resume"),
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def show(ref: str, path: str) -> str:
    return git("show", f"{ref}:{path}")


def _dict_keys(source: str, func_name: str, marker: str) -> list[str]:
    """Keys of the dict literal named `marker` inside `func_name`, plus **spreads."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                        and isinstance(inner.targets[0], ast.Name)
                        and inner.targets[0].id == marker
                        and isinstance(inner.value, ast.Dict)):
                    keys: list[str] = []
                    for key in inner.value.keys:
                        if key is None:
                            continue  # `**rendering` — recorded separately
                        if isinstance(key, ast.Constant):
                            keys.append(str(key.value))
                    return sorted(keys)
    return []


def _returns_dict_keys(source: str, func_name: str) -> list[str]:
    """Keys of the dict literal `func_name` returns — used to resolve a `**spread`.

    A spread like `**rendering` is only meaningful if it is resolved to the fields the
    producing function actually returns; recording the name alone would leave the
    context field set silently short.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                    return sorted(str(k.value) for k in inner.value.keys
                                  if isinstance(k, ast.Constant))
    return []


def _spread_names(source: str, func_name: str, marker: str) -> list[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                        and isinstance(inner.targets[0], ast.Name)
                        and inner.targets[0].id == marker
                        and isinstance(inner.value, ast.Dict)):
                    # `{**a, 'b': 1}` parses as keys=[None, Constant('b')] with the
                    # spread expression in the *values* list, aligned by index.
                    return sorted(value.id
                                  for key, value in zip(inner.value.keys, inner.value.values)
                                  if key is None and isinstance(value, ast.Name))
    return []


def derive(ref: str) -> dict[str, Any]:
    handlers = show(ref, "src/jasmine_core/api/handlers.py")
    continuity = show(ref, "src/jasmine_core/continuity.py")
    builder = show(ref, "src/jasmine_core/context/builder.py")
    continuity_migration = show(ref, "src/jasmine_core/migrations/m0009_continuity.py")
    init = show(ref, "src/jasmine_core/__init__.py")

    schema = int(re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", init).group(1))

    # Route -> scope, taken from P3's own route table so a changed scope is a diff.
    routes: list[dict[str, Any]] = []
    for method, pattern, scope, response_key in READ_ROUTES:
        needle = re.compile(r'Route\(\s*"' + method + r'"\s*,\s*re\.compile\(r"'
                            + re.escape(pattern) + r'"\)\s*,\s*([a-z_]+)\s*,\s*"([^"]+)"')
        match = needle.search(handlers)
        routes.append({
            "method": method,
            "path_pattern": pattern,
            "scope": match.group(2) if match else None,
            "handler": match.group(1) if match else None,
            "response_key": response_key,
            "extra_scopes": (["checkpoint:read"] if response_key == "resume" else []),
            "query": (["session_id"] if pattern.endswith("/checkpoints/latest$") else []),
        })

    # Checkpoint fields: the table's columns, with the `_json` ones parsed into the
    # names the reader exposes (`projection`, `workspace`, `note`, `trigger_provenance`).
    columns = re.findall(r"^\s{8}([a-z_]+) (?:TEXT|INTEGER)", continuity_migration, re.M)
    parsed = sorted({c[:-5] for c in columns if c.endswith("_json")} - {"result"})
    checkpoint_fields = sorted((set(columns) - {c for c in columns if c.endswith("_json")}) | set(parsed))

    resume_fields = _dict_keys(continuity, "_persist", "result")
    context_fields = _dict_keys(builder, "_persist", "result")
    # Resolve each `**spread` to the fields its producer returns, so the recorded field
    # set is complete rather than "23 fields and a mystery".
    renderer = show(ref, "src/jasmine_core/context/renderer.py")
    spreads: dict[str, list[str]] = {}
    for name in _spread_names(builder, "_persist", "result"):
        if name == "rendering":
            spreads[name] = _returns_dict_keys(renderer, "render")
        else:
            spreads[name] = []

    return {
        "contract": "jasmine.p3-continuity-read.v1",
        "source": {
            "ref": ref,
            "commit": git("rev-parse", ref).strip(),
            "derived_by": "scripts/p6/p3_contract.py",
            "schema_version_in_p3": schema,
        },
        "baseline": {
            "commit": "850fbec",
            "schema_version": 8,
            "these_routes_exist": False,
            "why": ("P3 (Context/Checkpoint/Resume) is not merged into this baseline: Core "
                    "here has no context/, continuity.py or task_snapshot.py, and no route "
                    "for any of the four endpoints below. P6-03a is therefore delivered "
                    "against this contract with the dependency marked pending."),
        },
        "read_routes": routes,
        "not_in_this_slice": [
            {"method": "POST", "path_pattern": "^/v1/context/build$", "scope": "context:build",
             "why": "builds a pack; a write, not a read"},
            {"method": "POST", "path_pattern": "^/v1/context/([^/]+)/check-current$",
             "scope": "context:build", "why": "recomputes currency; not a read"},
            {"method": "POST", "path_pattern": "^/v1/checkpoints$", "scope": "checkpoint:write",
             "why": "creates a checkpoint"},
            {"method": "POST", "path_pattern": "^/v1/tasks/([^/]+)/resume$", "scope": "resume:build",
             "why": "builds a resume receipt"},
        ],
        "response_fields": {
            "checkpoint": checkpoint_fields,
            "resume": resume_fields,
            "context": sorted(set(context_fields) | {k for v in spreads.values() for k in v}),
            "context_spread_resolved": spreads,
        },
        "semantics": {
            "checkpoint_projection_is_not_truth": (
                "A checkpoint's `projection` is an immutable snapshot of what the Task "
                "looked like at that revision (projection_version jasmine.task-snapshot.v1), "
                "carried with `projection_digest`, `full_context_digest`, `workspace_digest` "
                "and the `task_revision` it was taken at. It is a record, never a "
                "current-state claim."),
            "latest_may_be_null": (
                "`GET /v1/tasks/{id}/checkpoints/latest` answers 200 with "
                "`{\"checkpoint\": null}` when the Task has no checkpoint yet. That is an "
                "empty result, not an error and not an absent Task."),
            "resume_note_is_historical": (
                "A resume carries `note` as {value, authority: false, historical: true} — "
                "the note of the checkpoint it resumed from. The view must present it as "
                "historical and non-authoritative."),
            "resume_is_a_comparison_not_a_restore": (
                "`comparison` and `next_actions` describe how the checkpoint and the "
                "current projection differ; nothing is applied. `truth_digest` is the "
                "current Truth digest, not a restore point."),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p3_contract")
    parser.add_argument("--ref", default="p3-checkpoint-context-resume")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--print", dest="show_only", action="store_true")
    args = parser.parse_args(argv)

    try:
        derived = derive(args.ref)
    except subprocess.CalledProcessError as error:
        print(f"cannot read ref {args.ref!r}: {error}", file=sys.stderr)
        return 2 if args.write else 0  # absent ref: nothing to compare, not a failure

    if args.show_only:
        print(json.dumps(derived, indent=2, ensure_ascii=False))
        return 0
    if args.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(derived, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(f"wrote {SNAPSHOT.relative_to(ROOT)} from {derived['source']['commit'][:12]}")
        return 0

    if not SNAPSHOT.exists():
        print(f"missing {SNAPSHOT.relative_to(ROOT)}; run with --write", file=sys.stderr)
        return 1
    checked_in = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if checked_in == derived:
        print(f"P3 contract snapshot matches {args.ref} @ {derived['source']['commit'][:12]}")
        return 0
    fields = [k for k in derived if checked_in.get(k) != derived.get(k)]
    print(f"P3 contract snapshot DRIFTED from {args.ref}: {fields}", file=sys.stderr)
    for key in fields:
        print(f"  {key}:\n    snapshot: {json.dumps(checked_in.get(key), sort_keys=True)[:400]}\n"
              f"    branch:   {json.dumps(derived.get(key), sort_keys=True)[:400]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
