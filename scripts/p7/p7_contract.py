#!/usr/bin/env python3.13
"""Derive the P7-01 Validation read contract from `validation/reads.py`.

P7-01 is the read-only Validation surface, and its contract must describe the
code that will serve it rather than anyone's memory of it. So, exactly as
`scripts/p6/p4_contract.py` does for P4, this derives the contract from the
implementation source:

* the five `GET` routes — path pattern, handler and route-table scope — parsed
  from the `route_cls(...)` table in `reads.py`;
* each route's top-level response keys, parsed with `ast` out of the
  `Response(200, {...})` literals in its handler, with conditional spreads
  (`**({...} if cond else {})`) resolved to the keys both branches add.

**The source is the working tree by default.** Unlike the P6 dependency
contracts, P7-01 is not a pinned external commit: it is the code in this
checkout. `--ref <ref>` pins the derivation to a git ref instead (a full or
abbreviated ref works; the file must exist there), which is how a later,
committed freeze would be recorded. Where the named ref (or the file at it) is
absent the script **exits 2 with a loud message**, never 0: a drift guard that
passes because it could not look is worse than no guard, which P6-03a already
had to correct.

`--derived-at <ref>` re-derives at that ref as well and records the integration
provenance stamp (`source.derived_at_*`) using `scripts/p6/contract_provenance.py`,
so a contract re-derived across an integration is recorded rather than hidden.

`--write` regenerates the snapshot, `--print` shows it, `--check` re-derives and
compares it.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "p6"))
from contract_provenance import (  # noqa: E402
    apply_derived_at,
    resolve_commit,
    without_derived_at,
)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "web" / "contracts" / "p7-validation-read.v1.json"
READS_PATH = "src/jasmine_core/validation/reads.py"

#: The derivation source: the working tree, or a git ref given with `--ref`.
WORKTREE = "WORKTREE"

#: The five read-only routes, in the order the surface presents them. Every one
#: carries `validation:read`. `response_key` names the view for a reader of the
#: contract; `response_fields` is derived from the handler itself.
READ_ROUTES = (
    (r"^/v1/validation/runs$", "runs"),
    (r"^/v1/validation/runs/([^/]+)$", "run"),
    (r"^/v1/validation/runs/([^/]+)/manifest$", "manifest"),
    (r"^/v1/validation/runs/([^/]+)/artifacts/([^/]+)$", "artifact"),
    (r"^/v1/validation/runs/([^/]+)/artifacts/([^/]+)/content$", "content"),
)


class AbsentRefError(RuntimeError):
    """The requested source ref (or `reads.py` at it) is not in this checkout."""


def _source(ref: str) -> str:
    if ref == WORKTREE:
        path = ROOT / READS_PATH
        if not path.is_file():
            raise AbsentRefError(f"{READS_PATH} does not exist in the working tree")
        return path.read_text(encoding="utf-8")
    try:
        return subprocess.run(["git", "show", f"{ref}:{READS_PATH}"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise AbsentRefError(
            f"git show {ref}:{READS_PATH} failed ({exc.__class__.__name__}); "
            f"{ref!r} is not a ref in this checkout, or {READS_PATH} does not exist at it"
        ) from exc


def _routes(source: str) -> list[dict[str, Any]]:
    """Every route in the P7-01 table, in table order."""
    out: list[dict[str, Any]] = []
    for match in re.finditer(
            r'route_cls\(\s*"(GET|POST)"\s*,\s*re\.compile\(r"((?:[^"\\]|\\.)*)"\)\s*,\s*'
            r'([a-z_]+(?:\([^)]*\))?)\s*,\s*"([^"]+)"\s*\)', source):
        out.append({"method": match.group(1), "path_pattern": match.group(2),
                    "handler": match.group(3), "scope": match.group(4)})
    if not out:
        raise SystemExit("parsed no routes from the P7-01 route table")
    return out


def _assignments(function: ast.FunctionDef) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out.setdefault(node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                out.setdefault(node.target.id, node.value)
    return out


def _dict_keys(node: ast.Dict, assignments: dict[str, ast.AST]) -> set[str]:
    keys: set[str] = set()
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif key is None:
            keys |= _spread_keys(value, assignments)
    return keys


def _spread_keys(value: ast.AST, assignments: dict[str, ast.AST]) -> set[str]:
    """Keys a `**spread` adds, following a conditional or a local dict literal."""
    if isinstance(value, ast.Dict):
        return _dict_keys(value, assignments)
    if isinstance(value, ast.IfExp):
        return _spread_keys(value.body, assignments) | _spread_keys(value.orelse, assignments)
    if isinstance(value, ast.Name) and isinstance(assignments.get(value.id), ast.Dict):
        return _dict_keys(assignments[value.id], assignments)  # type: ignore[arg-type]
    raise SystemExit(f"cannot resolve spread {ast.unparse(value)!r}; resolve it in the "
                     f"generator rather than dropping keys")


def _response_keys(source: str, handler: str) -> list[str]:
    """Union of the top-level keys of every `Response(200, {...})` in `handler`.

    A handler with both a masked and an original branch (content) contributes the
    keys of both, so the contract describes the union of what each form can
    return. Optional keys carried on a conditional spread are included because
    they are part of the response shape.
    """
    keys: set[str] = set()
    found = False
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == handler):
            continue
        assignments = _assignments(node)
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "Response" and len(inner.args) >= 2):
                continue
            payload = inner.args[1]
            if isinstance(payload, ast.Dict):
                keys |= _dict_keys(payload, assignments)
                found = True
    if not found:
        raise SystemExit(f"{handler}: found no Response(<status>, {{...}}) to derive keys from")
    return sorted(keys)


def derive(ref: str = WORKTREE) -> dict[str, Any]:
    source = _source(ref)
    routes = _routes(source)
    reads: list[dict[str, Any]] = []
    for pattern, response_key in READ_ROUTES:
        match = next((route for route in routes
                      if route["method"] == "GET" and route["path_pattern"] == pattern), None)
        if match is None:
            raise SystemExit(f"route {pattern!r} is not a GET route in the P7-01 table")
        reads.append({
            "method": "GET",
            "path_pattern": pattern,
            "handler": match["handler"],
            "scope": match["scope"],
            "response_key": response_key,
            "response_fields": _response_keys(source, match["handler"]),
        })
    if any(route["method"] != "GET" for route in routes):
        raise SystemExit("the P7-01 surface exposes a non-GET route; the contract says read-only")
    return {
        "contract": "p7-validation-read.v1",
        "source": {
            "ref": ref,
            "kind": ("working tree (the P7-01 implementation is uncommitted in this "
                     "checkout)" if ref == WORKTREE else "pinned commit (not a branch)"),
            "derived_by": "scripts/p7/p7_contract.py",
            "derived_at_ref": None,
            "derived_at_identical": None,
            "derived_at_differences": [],
        },
        "read_routes": reads,
        "read_scopes": sorted({route["scope"] for route in reads}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p7_contract", description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=WORKTREE,
                        help="git ref to derive from (default: the working tree)")
    parser.add_argument("--write", action="store_true", help="regenerate the snapshot")
    parser.add_argument("--check", action="store_true", help="compare against the snapshot")
    parser.add_argument("--print", dest="show", action="store_true", help="print the snapshot")
    parser.add_argument("--derived-at", default=None, metavar="REF",
                        help="also derive at this git ref (normally the integration commit) "
                             "and record the provenance stamp in `source`")
    args = parser.parse_args(argv)

    try:
        derived = derive(args.ref)
    except AbsentRefError as error:
        print(f"P7-01 contract source {args.ref!r} is absent, so the contract cannot be "
              f"re-derived: {error}. A drift guard that passes because it could not look is "
              f"worse than no guard.", file=sys.stderr)
        return 2

    if args.derived_at:
        try:
            resolved_at = resolve_commit(args.derived_at)
        except subprocess.CalledProcessError as error:
            print(f"--derived-at {args.derived_at!r} is not in this checkout, so the contract "
                  f"cannot be re-derived there ({error.__class__.__name__}).", file=sys.stderr)
            return 2
        except ValueError as error:
            print(f"--derived-at {args.derived_at!r} is not a full commit SHA: {error}",
                  file=sys.stderr)
            return 2
        try:
            integration = derive(resolved_at)
        except AbsentRefError as error:
            print(f"{READS_PATH} is absent at --derived-at {resolved_at}, so the integration "
                  f"derivation cannot run: {error}", file=sys.stderr)
            return 2
        apply_derived_at(derived, resolved_at, integration)

    if args.show:
        json.dump(derived, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(derived, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(f"wrote {SNAPSHOT.relative_to(ROOT)} from {args.ref}")
        return 0

    if args.check:
        if not SNAPSHOT.exists():
            print(f"{SNAPSHOT.relative_to(ROOT)} does not exist; run --write", file=sys.stderr)
            return 1
        frozen = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        # Only the integration stamp is ignored; `source.ref` and the derived
        # routes are still compared, so a wrong source still fails.
        if without_derived_at(frozen) == without_derived_at(derived):
            print(f"P7-01 contract snapshot matches {args.ref}")
            return 0
        print(f"P7-01 contract snapshot DRIFTED from {args.ref}", file=sys.stderr)
        frozen_content = without_derived_at(frozen)
        derived_content = without_derived_at(derived)
        for key in sorted(set(frozen_content) | set(derived_content)):
            if frozen_content.get(key) != derived_content.get(key):
                print(f"  differs: {key}", file=sys.stderr)
        return 1

    parser.error("choose --write, --check or --print")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
