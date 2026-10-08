#!/usr/bin/env python3.13
"""Derive the P6-03b2 write specification from the pinned P4 commit.

P6-03b2 is the **write** half of the Memory inspector: retry, the four lifecycle commands and
reflect. Nothing of it is implemented. This exists so the payloads, the permissions and the
acceptance criteria are frozen **before** anyone writes code against them — which is the whole
reason P6-02's writes needed a permission Gate discovered late, and P6-03b1's contract needed a
scope defect found by review.

Same discipline as `p4_contract.py`, for the same reasons:

* the source is the pinned **full SHA**, never a branch, so the spec cannot move under a `git pull`;
* the six routes, their scopes, their allowed body fields and their response key sets are parsed
  from P4's own source — `_body(request, {...})`, `_reject_unknown(body, {...})` and the dict
  literals the called functions return — so a renamed field shows up as a diff;
* **required** fields come from the code that raises when they are missing, not from prose;
* the prose semantics are *recorded* with their source file and marked as recorded. Asserting that
  they were derived would be the overclaim this project keeps having to correct;
* where the pinned commit is absent, `--check` exits **2**, not 0.

One thing this deliberately does **not** do: decide whether b2 should be built. It records what P4
offers and what would have to be true before a UI may offer it.
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
SNAPSHOT = ROOT / "web" / "contracts" / "p4-memory-write.v1.json"

# Reuse the read contract's AST helpers rather than copying them: two derivations of the same
# source that drift apart would be worse than one.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4_contract import (  # noqa: E402
    P4_REF,
    _function_source,
    _id_prefix,
    _routes,
    show,
)

#: Routes that take a body but write nothing on their own. `reflect` runs the engine's generator,
#: so it is neither a read nor a lifecycle write; it is listed separately rather than folded in.
GENERATOR_ROUTES = {r"^/v1/memory/reflect$"}

#: Prose semantics the write surface must honour, each with the file it was read from. Recorded,
#: not derived — see the module docstring.
SEMANTICS = (
    ("human_or_system_only",
     "`request_retry` and the lifecycle commands refuse any actor that is not human or system "
     "with `forbidden_actor_kind` at 403 (the class is `errors.ForbiddenActorKind`); `reflect` "
     "requires `memory:reflect`, which key issuance will not grant to an agent. Sources: "
     "memory/jobs.py, memory/lifecycle.py, memory/api.py, errors.py."),
    ("reflect_needs_an_explicit_user_request",
     "`reflect` is the only path to the engine's generator and requires an explicit user request; "
     "the adapter refuses when `explicit_user_request` is false. A complicated ordinary question "
     "goes to recall. It is unreachable from a Context build. Sources: memory/adapter.py, "
     "memory/api.py."),
    ("lifecycle_needs_expected_revision",
     "A lifecycle command without `expected_revision` is refused with `missing_expected_revision` "
     "at 400 (the class is `errors.MissingExpectedRevision`), and a stale one with "
     "`revision_conflict` at 409; the state is checked before the revision, so 'you cannot "
     "obsolete a tombstone' is answered as `invalid_state_transition` rather than as a stale "
     "revision, and an illegal command appends no Event at all. Source: memory/lifecycle.py, "
     "errors.py."),
    ("retry_never_changes_what_is_retained",
     "A retry may not change the job's `document_id`, `content_sha256` or `bank_id`: those columns "
     "are immutable by trigger and the command touches none of them. A `DONE` job is refused "
     "rather than retained twice, a `RUNNING` job is refused rather than stolen from its worker, "
     "and a `PENDING` job is refused because it is already queued — accepting it would append an "
     "audit Event for a transition that did not happen. All three answer "
     "`invalid_state_transition` at 409, and only a `FAILED` job is retried, with `retry_count` "
     "**reset to 0** rather than incremented. Source: memory/jobs.py, errors.py."),
    ("replay_returns_the_original",
     "Both the retry and the lifecycle commands are idempotent on `event_id`: a repeat returns the "
     "original command Event and writes no second transition. The retry hashes the command payload "
     "(excluding `previous_status`/`previous_retry_count`), so the same request re-sent after it "
     "already succeeded is recognised as the same command. Source: memory/jobs.py, "
     "memory/lifecycle.py."),
    ("memory_is_never_authority",
     "No write here may change Rule or State, and every reply that describes a memory object says "
     "it is not Truth: the four lifecycle commands carry `non_authoritative` and `reflect` carries "
     "it too, while a retry reply is only `job`/`event`/`replayed` and names no memory at all — "
     "which is why this rule is stated per reply rather than per endpoint. Sources: taskbook §6, "
     "memory/api.py."),
    ("write_responses_are_actor_scoped",
     "P4 withholds engine free text from any actor that is not human or system, inside `_respond`, "
     "so it applies to every write response too. Source: memory/api.py."),
)

#: Every entry above must name a P4 source file: this list is the contract's record of what *P4*
#: does, and a statement about our own implementation does not belong in it. One did — "nothing
#: here is implemented" — and when the write surface was built that entry became a false
#: statement inside a frozen contract. Implementation status lives in
#: `web/P6-03B2-COMPLIANCE.md` instead, and the guard test enforces the citation rule so it
#: cannot come back.


def _allowed_body_fields(source: str, func_name: str) -> list[str]:
    """The body fields a handler accepts, from the call that rejects everything else.

    Two spellings exist in P4: `_body(request, {...})` for the handlers that take a body directly,
    and `_reject_unknown(body, {...})` for the lifecycle factory. Reading either from a literal is
    exact; reading them from prose is how a field gets invented.
    """
    segment = _function_source(source, func_name)
    for call in ("_body", "_reject_unknown"):
        match = re.search(call + r"\((?:request|body)\s*,\s*\{(.*?)\}", segment, re.S)
        if match:
            return sorted(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', match.group(1)))
    raise SystemExit(f"could not find an allowed-body literal in {func_name!r}")


def _helper_bodies(source: str, func_name: str) -> str:
    """The text of `func_name` plus every local helper it calls, transitively.

    A required field is not always declared in the handler: `expected_revision` is enforced by
    `_positive_revision`, which the lifecycle transition calls. Reading only the handler missed it
    and then reported the *optional* fields as required — wrong in both directions at once.
    """
    tree = ast.parse(source)
    funcs = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    seen: list[str] = []
    stack = [func_name]
    while stack:
        name = stack.pop(0)
        if name in seen or name not in funcs:
            continue
        seen.append(name)
        for node in ast.walk(funcs[name]):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in funcs):
                stack.append(node.func.id)
    return "\n".join(_function_source(source, name) for name in seen)


def _required_body_fields(source: str, func_name: str, allowed: list[str]) -> list[str]:
    """Fields the code refuses to proceed without, following the helpers it calls.

    Two shapes are read, both of them literal and both of them P4's:

    * `if "x" not in body: raise ...` — possibly inside a helper, which is how
      `expected_revision` is enforced;
    * a `raise` naming `field="x"` that is **not** guarded by a presence check. The guard matters:
      `event_id` and `reason` are validated only `if ... is not None`, so treating their raises as
      proof of requirement would list two optional fields as required.
    """
    segment = _helper_bodies(source, func_name)
    required: set[str] = set()
    for name in allowed:
        if re.search(r'if\s+"' + name + r'"\s+not\s+in\s+body', segment):
            required.add(name)
            continue
        guarded = re.search(r'if\s+' + name + r'\s+is\s+not\s+None', segment) or \
            re.search(r'if\s+"' + name + r'"\s+in\s+body', segment)
        if not guarded and re.search(
                r'raise errors\.\w+\([^)]*field="' + name + r'"', segment, re.S):
            required.add(name)
    return sorted(required)


def _returned_key_sets(source: str, func_name: str) -> list[list[str]]:
    """Every distinct dict-literal key set a function returns, so both branches are recorded.

    The lifecycle command is the reason this exists: a fresh transition returns
    `before`/`after`, while a replay does not, so the response has two shapes. A spec that
    recorded one would describe a payload the UI would fail on.
    """
    sets: list[list[str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                keys = sorted(k.value for k in inner.value.keys
                              if isinstance(k, ast.Constant) and isinstance(k.value, str))
                if keys and keys not in sets:
                    sets.append(keys)
    if not sets:
        raise SystemExit(f"{func_name!r} returns no dict literal to read a shape from")
    return sets


def _reflect_outcome_keys(types_source: str) -> list[str]:
    """`ReflectOutcome.as_dict` — the reflect response body."""
    for node in ast.walk(ast.parse(types_source)):
        if not (isinstance(node, ast.ClassDef) and node.name == "ReflectOutcome"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.FunctionDef) and inner.name == "as_dict":
                for ret in ast.walk(inner):
                    if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                        return sorted(k.value for k in ret.value.keys
                                      if isinstance(k, ast.Constant) and isinstance(k.value, str))
    raise SystemExit("could not read ReflectOutcome.as_dict")


def derive(ref: str) -> dict[str, Any]:
    api = show(ref, "src/jasmine_core/memory/api.py")
    jobs = show(ref, "src/jasmine_core/memory/jobs.py")
    lifecycle = show(ref, "src/jasmine_core/memory/lifecycle.py")
    types = show(ref, "src/jasmine_core/memory/types.py")
    init = show(ref, "src/jasmine_core/__init__.py")
    schema = int(re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", init).group(1))

    writes: list[dict[str, Any]] = []
    for route in _routes(api):
        if route["method"] == "GET":
            continue
        pattern = route["path_pattern"]
        handler = route["handler"]
        # The lifecycle routes share a factory, so their body rules come from it, not from the
        # route entry.
        body_owner = "_transition" if handler.startswith("_lifecycle_command") else handler
        allowed = _allowed_body_fields(lifecycle if body_owner == "_transition" else api, body_owner)
        required = _required_body_fields(api, handler, allowed) if body_owner != "_transition" else []
        if body_owner == "_transition":
            required = _required_body_fields(lifecycle, "_transition", allowed)
        entry = {
            "method": route["method"],
            "path_pattern": pattern,
            "handler": handler,
            "scope": route["scope"],
            "allowed_body_fields": allowed,
            "required_body_fields": required,
            # Read from the handler's own `_require_id` call: the route patterns use
            # `([^/]+)`, so the prefix is not in the path at all.
            "id_prefix": _id_prefix(api, "_lifecycle_command" if handler.startswith("_lifecycle")
                                    else handler),
            "is_generator": pattern in GENERATOR_ROUTES,
        }
        if body_owner == "_transition":
            entry["response_shapes"] = [
                sorted(keys + ["non_authoritative"])
                for keys in _returned_key_sets(lifecycle, "_transition")
            ]
        elif handler == "retry_job":
            entry["response_shapes"] = _returned_key_sets(jobs, "request_retry")
        elif handler == "reflect":
            entry["response_shapes"] = [_reflect_outcome_keys(types)]
        else:
            raise SystemExit(f"no response shape rule for {handler!r}")
        writes.append(entry)

    if len(writes) != 6:
        raise SystemExit(f"expected 6 write routes, derived {len(writes)}")

    return {
        "contract": "p4-memory-write.v1",
        "status": "FROZEN CONTRACT — the six writes P4 offers, derived from the pinned SHA. "
                  "Implementation and validation status is tracked separately in "
                  "web/P6-03B2-COMPLIANCE.md; nothing here is a claim that the surface works.",
        "source": {
            "ref": ref,
            "kind": "pinned commit (full SHA, not a branch)",
            "schema_version_in_p4": schema,
            "derived_by": "scripts/p6/p4_write_spec.py",
            "acceptance": "P4 phase acceptance NOT passed: nothing here was validated against a "
                          "real P4 Core, and no write may ship before its own permission and "
                          "audit Gate",
        },
        "writes": writes,
        "scope_split": {
            "memory:manage": sorted({w["scope"] for w in writes if w["scope"] == "memory:manage"}),
            "memory:reflect": sorted({w["scope"] for w in writes if w["scope"] == "memory:reflect"}),
            "note": "`retry` and the four lifecycle commands share `memory:manage`; `reflect` has "
                    "its own scope so it can be withheld independently of lifecycle management.",
        },
        "response_shape_note": "A lifecycle command has two shapes: a fresh transition returns "
                               "`before`/`after` and a replay does not. A client that assumes one "
                               "will break on the other.",
        "semantics": {
            "recorded": [{"key": key, "statement": statement} for key, statement in SEMANTICS],
            "recorded_note": "Prose semantics are recorded from reading the pinned commit, each "
                             "naming its source file. They are not auto-derived.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=P4_REF)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", dest="show", action="store_true")
    args = parser.parse_args(argv)

    try:
        derived = derive(args.ref)
    except subprocess.CalledProcessError as error:
        print(f"P4 ref {args.ref!r} is not in this checkout, so the write spec cannot be "
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
            print(f"P4 write spec matches {args.ref}")
            return 0
        print(f"P4 write spec DRIFTED from {args.ref}", file=sys.stderr)
        for key in sorted(set(frozen) | set(derived)):
            if frozen.get(key) != derived.get(key):
                print(f"  differs: {key}", file=sys.stderr)
        return 1
    parser.error("choose --write, --check or --print")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
