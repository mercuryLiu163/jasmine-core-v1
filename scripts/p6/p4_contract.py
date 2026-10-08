#!/usr/bin/env python3.13
"""Derive the P4 memory read contract from the pinned P4 commit.

P6-03b1 is the read-only Memory inspector, and its dependency (P4) is **not merged**:
baseline `850fbec` is schema 8 and has no `src/jasmine_core/memory/` at all. So, exactly as
`p3_contract.py` does for P3, this derives the contract from the P4 source rather than from
anyone's memory of it.

**The source is pinned to a commit, not a branch.** `P4_REF` is a full SHA, so the contract
cannot drift when that branch moves; re-pinning is an explicit, reviewable edit. A contract
pinned to a branch name would silently change shape under a `git pull`.

What is derived, and how:

* the seven `GET` routes — path, handler and route-table scope — parsed from the route table
  in `memory/api.py`;
* each route's response keys, parsed from the `_respond(200, {...})` literals in its handler,
  with `**spread` resolved to the keys of whatever produced the spread value;
* the nested shapes the inspector renders — a job row, an item row, a trace row, an attempt,
  the lifecycle summary, the health report and its counts — from the `memory_items` /
  `memory_jobs` / `memory_query_traces` / `memory_job_attempts` table columns and from the
  dict literals the builders return;
* mechanical semantic facts: which handlers mark their payload non-authoritative, which routes
  are writes and therefore outside this slice, the id prefix each route validates, and the
  *absence* of any retain route or bank-selecting route.

What is **not** auto-derived: the prose semantics in `SEMANTICS`. Those are recorded from
reading the pinned commit and each names the file it came from. They are asserted for
presence in the snapshot, not re-derived, and the snapshot says so — claiming otherwise would
be the same overclaim this project keeps having to correct.

`--check` re-derives and compares against the frozen snapshot. Where the pinned commit is
absent it **skips with a loud message and exits 2**, not 0: a drift guard that passes because
it could not look is worse than no guard, and P6-03a already learned that lesson.
`--write` regenerates the snapshot. `--print` shows it.
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
SNAPSHOT = ROOT / "web" / "contracts" / "p4-memory-read.v1.json"

#: The P4 commit this contract is pinned to. A full SHA on purpose: see the module docstring.
P4_REF = "0c4f8f0e2118711ac9aa2a7006fddc6982962248"

#: The seven read-only endpoints of this slice, in the order the views present them. Every
#: one carries `memory:read`. The six writes (`retry`, the four lifecycle commands and
#: `reflect`) are P6-03b2 and are recorded only as out-of-slice.
READ_ROUTES = (
    (r"^/v1/memory/health$", "health"),
    (r"^/v1/memory/jobs$", "jobs"),
    (r"^/v1/memory/jobs/([^/]+)$", "job"),
    (r"^/v1/memory/items$", "items"),
    (r"^/v1/memory/items/([^/]+)$", "item"),
    (r"^/v1/memory/traces$", "traces"),
    (r"^/v1/memory/traces/([^/]+)$", "trace"),
)

#: Prose semantics the inspector must not contradict, each with the file it was read from.
#: Recorded, not derived — see the module docstring for why that distinction is stated.
SEMANTICS = (
    ("non_authoritative_from_core",
     "Core's own item-list, item-detail and health payloads carry `non_authoritative: true`; "
     "the inspector reads that field rather than asserting it. Source: memory/api.py."),
    ("no_retain_endpoint",
     "There is no public retain endpoint: content reaches the engine only through a worker "
     "acting on a job derived from committed truth. Source: memory/api.py docstring."),
    ("no_bank_selector",
     "There is no public bank selector: the bank is derived from the item's own scope, so an "
     "agent cannot choose `jasmine-global` or promote project content into it. Source: "
     "memory/api.py docstring."),
    ("reflect_is_human_only",
     "`reflect` requires `memory:reflect`, which key issuance will not grant to an agent; it "
     "is the only path that runs the engine's generator and is unreachable from a Context "
     "build. It is out of this slice. Source: memory/api.py docstring."),
    ("authorisation_from_actor",
     "Project authorisation is decided from the authenticated actor, never from a body field "
     "or a query parameter; an unauthorised project is reported as `not_found` so the "
     "inspector cannot enumerate another project's work. Source: memory/api.py docstring."),
    ("empty_not_everything",
     "An actor with no grants gets an empty list rather than the whole database. Source: "
     "memory/api.py `list_jobs`/`list_items`/`list_traces`."),
    ("agent_redaction",
     "An agent key may read jobs but only for a project it may read, and engine-derived text "
     "is withheld because error summaries can carry it. Source: memory/api.py `list_jobs`."),
    ("memory_is_not_authority",
     "Memory is NON_AUTHORITATIVE: it cannot change Rule or State, and no view may present it "
     "as Truth. Sources: taskbook §6 and memory/api.py."),
    ("credentials_stay_in_core",
     "Engine credentials never reach the browser; the engine lives behind Core. Sources: "
     "taskbook §6 and memory/transport.py."),
    ("health_probe_not_in_a_request",
     "`health.report(probe=True)` can add a `probe` key, but probe is reachable only from "
     "the CLI and the worker, never from a request handler, so a request response carries "
     "the 17 report keys plus `non_authoritative`. Source: memory/health.py, memory/api.py."),
    ("contract_pinned_acceptance_unpassed",
     "The contract source is pinned to a commit, but P4 phase acceptance has NOT passed: "
     "nothing here has been validated against a real P4 Core."),
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def show(ref: str, path: str) -> str:
    return git("show", f"{ref}:{path}")


def _table_columns(source: str, table: str) -> list[str]:
    """Column names of one `CREATE TABLE`, in declaration order.

    Scoped to the named table on purpose: a loose regex over a whole migration also matches
    other tables' columns and silently inflates the shape.
    """
    block = re.search(r"CREATE TABLE " + table + r" \((.*?)\n    \)", source, re.S)
    if block is None:
        raise SystemExit(f"could not find table {table!r} in the P4 migration")
    columns = re.findall(r"^\s+([a-z][a-z0-9_]*)\s+(?:TEXT|INTEGER|REAL|BLOB)",
                        block.group(1), re.M)
    if not columns:
        raise SystemExit(f"found {table!r} but parsed no columns from it")
    return columns


def _decode_columns(columns: list[str], json_columns: list[str]) -> list[str]:
    """A table's columns with `x_json` replaced by the decoded `x` the reader exposes."""
    out = [c for c in columns if c not in json_columns]
    out += [c.removesuffix("_json") for c in json_columns]
    return sorted(out)


def _dict_literal_keys(source: str, func_name: str) -> list[str]:
    """Keys of the dict literal `func_name` returns (the first `return {` in it)."""
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                keys = [k.value for k in inner.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if keys:
                    return sorted(keys)
        # A function that builds `payload = {...}` (or `payload: T = {...}`) and returns it.
        for inner in ast.walk(node):
            value = None
            if isinstance(inner, ast.Assign):
                value = inner.value
            elif isinstance(inner, ast.AnnAssign) and inner.value is not None:
                value = inner.value
            if isinstance(value, ast.Dict):
                keys = [k.value for k in value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if keys:
                    return sorted(keys)
    raise SystemExit(f"could not find a dict literal returned by {func_name!r}")


def _effective_dict_keys(source: str, func_name: str, var_name: str) -> list[str]:
    """Every key `var_name` ends up with, including keys added by subscript assignment.

    `health_counts` builds `counts = {three keys}` and then grows it in a loop
    (`counts[state.lower()] = ...` over a literal tuple). Reading only the literal would
    under-report the shape, which is the same mistake a literal-only reading made for P3's
    `diff` before review caught it.
    """
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for inner in ast.walk(node):
            value = inner.value if isinstance(inner, (ast.Assign, ast.AnnAssign)) else None
            target = inner.targets[0] if isinstance(inner, ast.Assign) else (
                inner.target if isinstance(inner, ast.AnnAssign) else None)
            if (isinstance(target, ast.Name) and target.id == var_name
                    and isinstance(value, ast.Dict)):
                keys |= {k.value for k in value.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name) and target.value.id == var_name):
                index = target.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    keys.add(index.value)
                elif (isinstance(index, ast.Call)
                      and isinstance(index.func, ast.Attribute)
                      and index.func.attr == "lower"
                      and isinstance(index.func.value, ast.Name)):
                    # `counts[state.lower()]` — the loop variable is the *receiver* of
                    # `.lower()`, not an argument: reading `index.args` finds nothing and
                    # silently drops the three loop-added keys.
                    loop_var = index.func.value.id
                    for loop in ast.walk(node):
                        if (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
                                and loop.target.id == loop_var
                                and isinstance(loop.iter, (ast.Tuple, ast.List, ast.Set))):
                            for element in loop.iter.elts:
                                if (isinstance(element, ast.Constant)
                                        and isinstance(element.value, str)):
                                    keys.add(element.value.lower())
        return sorted(keys)
    raise SystemExit(f"could not find {func_name!r} to read {var_name!r} from")


def _assignments(source: str, func_name: str) -> dict[str, str]:
    """`name = <expr>` inside one function, with the expression unparsed to text."""
    out: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Assign) and isinstance(inner.targets[0], ast.Name)):
                out[inner.targets[0].id] = ast.unparse(inner.value)
    return out


def _handler_response_keys(api_source: str, handler: str,
                           spread_keys: dict[str, list[str]]) -> list[str]:
    """Keys of every `_respond(200, {...})` in `handler`, resolving `**spread`.

    A spread is resolved by looking up the expression that produced the spread value, so
    `{**svc.health(), "non_authoritative": True}` yields the health report's keys plus the
    literal one, rather than a mystery.
    """
    keys: set[str] = set()
    assigned = _assignments(api_source, handler)
    for node in ast.walk(ast.parse(api_source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == handler):
            continue
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_respond" and len(inner.args) >= 2):
                continue
            payload = inner.args[1]
            if not isinstance(payload, ast.Dict):
                continue
            for key, value in zip(payload.keys, payload.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
                elif key is None:
                    # `{**detail}` (a local) or `{**svc.health()}` (a call): resolve the
                    # producing expression, following a local's assignment first.
                    expression = ast.unparse(value)
                    if isinstance(value, ast.Name):
                        expression = assigned.get(value.id, expression)
                    if expression in spread_keys:
                        keys.update(spread_keys[expression])
                    else:
                        raise SystemExit(
                            f"{handler}: cannot resolve spread {expression!r}; "
                            f"add it to the spread resolver rather than dropping it")
    if not keys:
        raise SystemExit(f"{handler}: found no _respond(200, {{...}}) to derive keys from")
    return sorted(keys)


def _routes(api_source: str) -> list[dict[str, Any]]:
    """Every route in P4's table, read-only or not."""
    out: list[dict[str, Any]] = []
    for match in re.finditer(
            r'route_cls\(\s*"(GET|POST)"\s*,\s*re\.compile\(r"((?:[^"\\]|\\.)*)"\)\s*,\s*'
            r'([a-z_]+(?:\([^)]*\))?)\s*,\s*"([^"]+)"\s*\)', api_source):
        out.append({"method": match.group(1), "path_pattern": match.group(2),
                    "handler": match.group(3), "scope": match.group(4)})
    if not out:
        raise SystemExit("parsed no routes from the P4 route table")
    return out


def _id_prefix(api_source: str, handler: str) -> str | None:
    """The id prefix a handler validates, if it validates one."""
    for node in ast.walk(ast.parse(api_source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == handler):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_require_id" and len(inner.args) >= 2
                    and isinstance(inner.args[1], ast.Constant)):
                return str(inner.args[1].value)
    return None


def _limits(api_source: str, handler: str) -> int | None:
    """The default `limit` a list handler applies."""
    for node in ast.walk(ast.parse(api_source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == handler):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_int" and len(inner.args) >= 3
                    and isinstance(inner.args[1], ast.Constant)
                    and inner.args[1].value == "limit"
                    and isinstance(inner.args[2], ast.Constant)):
                return int(inner.args[2].value)
    return None


def derive(ref: str) -> dict[str, Any]:
    api_source = show(ref, "src/jasmine_core/memory/api.py")
    store = show(ref, "src/jasmine_core/memory/store.py")
    lifecycle = show(ref, "src/jasmine_core/memory/lifecycle.py")
    jobs = show(ref, "src/jasmine_core/memory/jobs.py")
    health = show(ref, "src/jasmine_core/memory/health.py")
    types = show(ref, "src/jasmine_core/memory/types.py")
    migration = show(ref, "src/jasmine_core/migrations/m0011_memory.py")
    init = show(ref, "src/jasmine_core/__init__.py")

    schema = int(re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", init).group(1))

    item_columns = re.findall(r'"([a-z][a-z0-9_]*)"', re.search(
        r"_ITEM_COLUMNS = \((.*?)\n\)", store, re.S).group(1))
    job_columns = _table_columns(migration, "memory_jobs")
    trace_columns = _table_columns(migration, "memory_query_traces")
    attempt_columns = _table_columns(migration, "memory_job_attempts")
    job_statuses = re.search(r"JOB_STATUSES[^=]*=\s*\(([^)]*)\)", types).group(1)
    job_statuses = re.findall(r'"([A-Z]+)"', job_statuses)

    nested = {
        "job": _decode_columns(job_columns, ["receipt_json"]),
        "attempt": _decode_columns(attempt_columns, ["receipt_json"]),
        "job_counts": sorted(status.lower() for status in job_statuses),
        "item": _decode_columns(item_columns, ["evidence_ids_json", "receipt_json"]),
        "lifecycle": _dict_literal_keys(lifecycle, "lifecycle_summary"),
        "health": _dict_literal_keys(health, "report"),
        "health_items": _effective_dict_keys(store, "health_counts", "counts"),
        "trace": _decode_columns(
            trace_columns,
            ["requested_types_json", "banks_json", "filtered_json", "authority_refs_json"]),
    }

    # Spreads a handler resolves through a local or a call: map the producing expression to
    # the keys it contributes, so `{**detail, "non_authoritative": True}` is complete.
    spread_keys = {
        "svc.health()": nested["health"],
        "svc.lifecycle.detail(memory_id, actor_id=principal.actor_id)":
            _dict_literal_keys(lifecycle, "detail"),
        "_empty_counts()": nested["job_counts"],
    }

    routes = _routes(api_source)
    reads: list[dict[str, Any]] = []
    for pattern, response_key in READ_ROUTES:
        match = next((r for r in routes
                      if r["method"] == "GET" and r["path_pattern"] == pattern), None)
        if match is None:
            raise SystemExit(f"route {pattern!r} is not a GET route at the pinned commit")
        handler = match["handler"]
        reads.append({
            "method": "GET",
            "path_pattern": pattern,
            "handler": handler,
            "scope": match["scope"],
            "response_key": response_key,
            "response_fields": _handler_response_keys(api_source, handler, spread_keys),
            "id_prefix": _id_prefix(api_source, handler),
            "query": sorted({a or b for a, b in re.findall(
                r'_optional\(request, "([a-z_]+)"\)|_int\(request, "([a-z_]+)"',
                _function_source(api_source, handler))}),
            "default_limit": _limits(api_source, handler),
        })

    writes = [r for r in routes if r["method"] != "GET"]
    if any("retain" in r["path_pattern"] for r in routes):
        raise SystemExit("the pinned commit exposes a retain route; the contract says none")
    if any(re.search(r"/bank", r["path_pattern"]) for r in routes):
        raise SystemExit("the pinned commit exposes a bank-selecting route; the contract says none")

    non_authoritative = sorted(
        r["handler"] for r in routes
        if '"non_authoritative"' in _function_source(api_source, r["handler"])
        or "non_authoritative" in _function_source(api_source, r["handler"]))

    return {
        "contract": "p4-memory-read.v1",
        "source": {
            "ref": ref,
            "kind": "pinned commit (full SHA, not a branch)",
            "schema_version_in_p4": schema,
            "derived_by": "scripts/p6/p4_contract.py",
            "acceptance": "P4 phase acceptance NOT passed: nothing here was validated "
                          "against a real P4 Core",
        },
        "baseline": {
            "commit": "850fbec",
            "schema_version": 8,
            "these_routes_exist": False,
            "why": "P4 (Memory adapter, jobs, recall, lifecycle) is not merged into this "
                   "baseline: Core here has no src/jasmine_core/memory/ and no route under "
                   "/v1/memory/. P6-03b1 is therefore delivered against this contract with "
                   "the dependency marked pending, and the live proxy stays closed.",
        },
        "read_routes": reads,
        "not_in_this_slice": [
            {"method": r["method"], "path_pattern": r["path_pattern"], "handler": r["handler"],
             "scope": r["scope"],
             "why": "a write or a generator action; P6-03b2, behind its own permission and "
                    "audit Gate"}
            for r in writes
        ],
        "nested_fields": nested,
        "non_authoritative_handlers": non_authoritative,
        "semantics": {
            "derived": {
                "writes_are_separate": True,
                "retain_endpoint_absent": True,
                "bank_selector_route_absent": True,
                "bank_filter_is_a_validated_query": "bank_id",
                "read_scopes": sorted({r["scope"] for r in reads}),
                "write_scopes": sorted({r["scope"] for r in writes}),
                "id_prefixes": sorted({r["id_prefix"] for r in reads if r["id_prefix"]}),
            },
            "recorded": [{"key": key, "statement": statement}
                         for key, statement in SEMANTICS],
            "recorded_note": "Prose semantics are recorded from reading the pinned commit, "
                             "each naming its source file. They are not auto-derived, and "
                             "asserting otherwise would be an overclaim.",
        },
    }


def _function_source(source: str, func_name: str) -> str:
    """The source text of one function, so per-handler greps do not leak across handlers."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node) or ""
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=P4_REF,
                        help="P4 commit to derive from (default: the pinned SHA)")
    parser.add_argument("--write", action="store_true", help="regenerate the snapshot")
    parser.add_argument("--check", action="store_true", help="compare against the snapshot")
    parser.add_argument("--print", dest="show", action="store_true", help="print the snapshot")
    args = parser.parse_args(argv)

    try:
        derived = derive(args.ref)
    except subprocess.CalledProcessError as error:
        print(f"P4 ref {args.ref!r} is not in this checkout, so the contract cannot be "
              f"re-derived ({error.__class__.__name__}). A drift guard that passes because it "
              f"could not look is worse than no guard.", file=sys.stderr)
        return 2

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
        if frozen == derived:
            print(f"P4 contract snapshot matches {args.ref}")
            return 0
        print(f"P4 contract snapshot DRIFTED from {args.ref}", file=sys.stderr)
        for key in sorted(set(frozen) | set(derived)):
            if frozen.get(key) != derived.get(key):
                print(f"  differs: {key}", file=sys.stderr)
        return 1

    parser.error("choose --write, --check or --print")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
