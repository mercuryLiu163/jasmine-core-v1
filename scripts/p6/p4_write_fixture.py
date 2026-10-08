#!/usr/bin/env python3.13
"""A **simulated** P4 memory *write* fixture — not a Jasmine Core, and not P4 verification.

P6-03b2's dependency is missing for the same reason P6-03b1's was: baseline `850fbec` is
schema 8 and has no `src/jasmine_core/memory/` at all, so there is nothing real to point the
dashboard at. This exists so the six write actions can be exercised end to end *on paper*
while the dependency is pending. It is deliberately loud about being a simulation:

* it prints a banner on startup and stamps `X-P6-Fixture: simulated-p4-writes` on **every**
  reply, so a screenshot or a recorded response cannot be mistaken for a real Core;
* it asserts every recorded write response shape and the six-route coverage against the
  frozen contract **before serving anything**, so a drifted fixture fails at startup instead
  of producing misleading evidence;
* it enforces the scopes the frozen contract records, names the same scope Core would, and
  checks the actor kind before it touches a body.

**P4 phase acceptance has not passed.** Nothing here was validated against a real P4 Core. A
green run of this fixture, or of its tests, establishes that the *dashboard's* expectations
are self-consistent with the frozen snapshot; it establishes nothing about P4.

How this relates to the read fixture
------------------------------------

It subclasses `scripts/p6/p4_read_fixture.Handler`, so the shell routes (`/v1/health`,
`/v1/projects`, `/v1/hosts`, `/v1/meta/schema`, `/v1/actors`) and the seven memory reads are
served by the same code, with the same conventions (`for_actor`, `_send`, `_error`,
`_require`, `_scopes`, `AGENT_SCOPE`). It adds `do_POST` for the contract's six writes and
overrides the two by-id read routes so a mutation is visible on readback. It reuses the read
fixture's `for_actor` unchanged; only the `X-P6-Fixture` value is its own.

What this fixture cannot reproduce (stated rather than discovered)
------------------------------------------------------------------

* **It is not P4's validator, but its wire codes and statuses are the pinned commit's.**
  Verified against `0c4f8f0e2118711ac9aa2a7006fddc6982962248`, `src/jasmine_core/errors.py`:
  `forbidden_actor_kind` 403, `forbidden_scope` 403, `missing_expected_revision` **400**,
  `revision_conflict` 409, `invalid_state_transition` 409, `event_id_conflict` 409,
  `invalid_request` 400, and `NotFound(kind, id)` as `f"{kind}_not_found"` — so a lifecycle
  or retry on an unknown object is `memory_not_found` / `memory_job_not_found`, and an
  unauthorised reflect project is `project_not_found`. The CamelCase names
  (`MissingExpectedRevision`, `RevisionConflict`, `ForbiddenActorKind`, `InvalidStateTransition`)
  are Python classes, not wire codes. The refusal *messages* are the fixture's paraphrase.
* **Retry validates no body field, exactly as P4 does not.** `memory/api.py::retry_job` passes
  `host_id` and `event_id` straight to `request_retry` without checking either, so a malformed
  value is accepted here too; the lifecycle commands do validate both, through `_transition`.
  An earlier version of this fixture refused a malformed retry value with 400 — stricter than
  the Core it stands in for — which adversarial review found.
* **Retry follows `memory/jobs.py::request_retry`.** Only a `FAILED` job is retried, and the
  update is `status='PENDING', retry_count=0` — the budget is **reset**, not incremented.
  `DONE`, `RUNNING` and `PENDING` are each refused with `invalid_state_transition` (a
  `PENDING` job is "already queued"; accepting it would append an Event for a transition that
  did not happen). The replay check runs before those state checks, as it does in P4.
* **The replay identity is looser than P4's idempotency hash.** This fixture keys replay and
  `event_id_conflict` on `(object_id, command)` alone. P4's `hash_payload` also covers the
  command payload (`expected_revision`, `reason`, …), so a re-used `event_id` with a changed
  revision or reason replays here and conflicts in P4. Stated so the looseness is not
  mistaken for fidelity.
* **The actor kind is a scope stand-in.** P4 decides human/system from the actor record
  (`core.actor_kind(principal.actor_id)`). The fixture has no actor registry, so a token
  carrying `actor:agent` is treated as a non-human actor and a `memory:manage` command from it
  is refused with `forbidden_actor_kind`. That is the fixture's mechanism, not P4's.
* **Reflect's route is modelled as already explicit.** P4's `reflect` refuses unless
  `ReflectRequest.explicit_user_request` is true; only the API route sets it. This fixture
  *is* that route — it represents a request that sets `explicit_user_request=True` — so it
  does not model that refusal and never sends `explicit_user_request`.
* **Withholding on a write reply is only observable artificially.** `for_actor` is applied to
  every reply here, including writes, exactly as P4's `_respond` does. But `memory:manage` is
  human/system-only, so the only write an agent can reach is `reflect` — and a real key
  issuance would not grant `memory:reflect` to an agent either. A test token carrying both
  `memory:reflect` and `actor:agent` is therefore artificial: it exists only so the
  withholding rule can be seen on a write response, and is not a scope combination P4 ships.
* **There is no Truth store, no engine and no jobs.** A lifecycle command changes an
  in-memory dict, not a database, and queues no remote work: the `job` in a lifecycle
  response is always `null` where P4 returns the queued invalidate/delete job. A retry does
  not run anything. `reflect` composes fixture prose; it does not call a model.
* **The audit Event is fixture-shaped.** The write contract pins only the top-level response
  keys, not the nested `event`. The event here carries the fields a caller needs to see a
  replay is the *same* command; it is not P4's EventStore row.
* **Readback is by-id only.** The two by-id read routes reflect mutations; the list routes
  are served by the read fixture unchanged and therefore do not (and do not list the extra
  fixture jobs).

Fixture-only control routes (a real Core has none of these)
-----------------------------------------------------------

A browser Gate cannot provoke some states on its own — a concurrent writer, a job in the
wrong status, or a reply that is lost after the write applied. Four **fixture controls**
exist for that, all under `/v1/_p6_fixture/`:

* `POST /v1/_p6_fixture/drift` (`?item={mem}` or JSON `{"item": "mem_..."}`) advances that
  item's `revision` by one **without any command Event**, so the next lifecycle command's
  `expected_revision` is genuinely stale and Core answers a real `409 revision_conflict` with
  the new current revision.
* `POST /v1/_p6_fixture/set-job-status` (`?job={mjb}&status={DONE|RUNNING|FAILED|PENDING}`
  or JSON `{"job": ..., "status": ...}`) sets that job's status and returns
  `{"ok": true, "job": {...}}`. It leaves `retry_count` alone: the control picks the state a
  retry branches on, and a retry is what resets that budget to 0.
* `POST /v1/_p6_fixture/drop-next` arms a one-shot: the next lifecycle/retry write that
  *would* apply is applied for real (state mutated, command Event recorded) and then answered
  as a transport failure — `504` with the fixture's proxy-shaped error body — instead of
  `200`. The caller keeps its `event_id`; resubmitting it gets the 5-key `replayed: true`
  shape and writes no second transition. A replay or a refused write does not consume the
  arming, only a fresh applied transition does.
* `GET /v1/_p6_fixture/state` returns the simulation's own audit as **lists** — the Gate
  reads it directly:

  ```json
  {"items": [{"memory_id": "mem_...", "revision": 3, "pinned": true,
              "status": "ACTIVE", "engine_state": "RETAINED"}],
   "jobs": [{"job_id": "mjb_...", "status": "FAILED", "retry_count": 0}],
   "commands": [{"event_id": "evt_...", "command": "pin", "object_id": "mem_...",
                 "replayed": false}]}
  ```

  `items[].revision` starts at the read fixture's value (1) and rises by exactly one per
  applied command; `commands` gets exactly one entry per *applied* command, so a replay, a
  refusal or a control adds none.

These are **not part of the product surface and not part of the P4 write surface**: they are
not in the frozen contract, they are not in `write_routes`, and the dashboard proxy must
never allow the prefix. They require **no bearer token**, because they are fixture-internal
and are only ever called directly on the fixture's own port — never through the dashboard.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

# -- the simulated read fixture this one extends ------------------------------
#
# Loaded by path rather than by a bare `import p4_read_fixture`: a test that loads this
# module from anywhere must not depend on `scripts/p6` being on `sys.path`, and if the read
# fixture is already imported (its own test module does that) it is reused rather than
# executed a second time.

def _load_base():
    name = "p4_read_fixture"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / "p4_read_fixture.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()

ROOT = base.ROOT
READ_CONTRACT_PATH = base.CONTRACT_PATH
WRITE_CONTRACT_PATH = ROOT / "web" / "contracts" / "p4-memory-write.v1.json"

#: The write fixture's own label. Deliberately *not* the read fixture's value: a response
#: from this process must never be mistakable for one from the read-only fixture.
FIXTURE_HEADER = "simulated-p4-writes"

#: The fixture-control prefix. Nothing under it is P4, nothing under it is in the frozen
#: contract, and nothing under it is token-protected: it exists so the Gate can provoke a
#: concurrent write or a lost reply, and it is only ever called on the fixture's own port.
CONTROL_PREFIX = "/v1/_p6_fixture/"

#: P4's `JOB_STATUSES`, so the `set-job-status` control cannot invent a fifth status.
JOB_STATUSES = ("PENDING", "RUNNING", "DONE", "FAILED")

#: A token carrying this scope is treated as a non-human actor. Re-exported from the read
#: fixture so both fixtures key on the same convention; see the module docstring for why it
#: is a stand-in for P4's actor record.
AGENT_SCOPE = base.AGENT_SCOPE

#: Sign-in needs `objects:read`; the write fixture's default token can do everything it
#: serves, so a bare `python3.13 scripts/p6/p4_write_fixture.py` is exercisable by hand.
ALL_SCOPES = ("objects:read", "memory:read", "memory:manage", "memory:reflect")
DEFAULT_TOKEN = "p4fixture-write-token"

ITEM = base.ITEM
ITEM_2 = base.ITEM_2
JOB = base.JOB
JOB_2 = base.JOB_2
PROJECT = base.PROJECT
PROJECT_OTHER = base.PROJECT_OTHER
HOST = base.HOST
ITEM_UNKNOWN = base.UNKNOWN
JOB_UNKNOWN = base.JOB_UNKNOWN
TRACE = base.TRACE
ID_RE = base.ID_RE

#: Two further fixture jobs so every retry branch is reachable without hand-editing state:
#: `PENDING` and `RUNNING` are refused/transitioned differently, and a fixture that held only
#: a DONE and a FAILED job could not exercise them. Their ids stay in the read fixture's
#: shared `ID_BODY` family so they are valid `mjb_` ids.
JOB_PENDING = f"mjb_{'01M4B6VGC29ESRDRQR62DX8AKA'}"
JOB_RUNNING = f"mjb_{'01M4B6VGC29ESRDRQR62DX8AKB'}"
#: A `FAILED` job whose budget is already non-zero, so "a retry resets `retry_count` to 0"
#: is observable rather than a reset of 0 to 0.
JOB_FAILED = f"mjb_{'01M4B6VGC29ESRDRQR62DX8AKC'}"

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: The stamp a mutation writes. P4 uses `clock.now()`; the fixture is deterministic on
#: purpose so a replay and a readback are reproducible across runs.
RECORDED_AT = "2026-10-07T12:05:00Z"

#: The six writes `do_POST` implements, as `(kind, command)`. This is the assertion target
#: for the frozen contract: if the contract gains, loses or renames a write, startup fails
#: rather than serving a route nobody implemented.
EXPECTED_ROUTES = frozenset({
    ("retry", None),
    ("reflect", None),
    ("lifecycle", "obsolete"),
    ("lifecycle", "delete"),
    ("lifecycle", "pin"),
    ("lifecycle", "unpin"),
})


def load_contract() -> dict[str, Any]:
    return json.loads(WRITE_CONTRACT_PATH.read_text(encoding="utf-8"))


def read_contract() -> dict[str, Any]:
    return base.load_contract()


def is_id(value: object, prefix: str) -> bool:
    """The fixture's `ids.is_id`: the right prefix and a well-formed body."""
    return (isinstance(value, str) and value.startswith(f"{prefix}_")
            and ID_RE.fullmatch(value) is not None)


def generated_id(prefix: str, number: int) -> str:
    """A deterministic, valid-shaped id, so an auto-generated event id is reproducible."""
    digits: list[str] = []
    value = max(number, 1)
    while True:
        digits.append(CROCKFORD[value % 32])
        value //= 32
        if value == 0:
            break
    body = "0" * (26 - len(digits)) + "".join(reversed(digits))
    return f"{prefix}_{body}"


# -- deterministic in-memory state --------------------------------------------

def default_state() -> dict[str, Any]:
    """Every mutable fact the fixture holds, reset at startup and between tests."""
    item = {"status": "ACTIVE", "pinned": False, "revision": 1,
            "engine_state": "RETAINED", "lifecycle_reason": None,
            "remote_deleted_at": None}
    return {
        "items": {ITEM: dict(item), ITEM_2: dict(item)},
        "jobs": {
            JOB: {"status": "DONE", "retry_count": 0},
            JOB_2: {"status": "FAILED", "retry_count": 0},
            JOB_PENDING: {"status": "PENDING", "retry_count": 0},
            JOB_RUNNING: {"status": "RUNNING", "retry_count": 1},
            JOB_FAILED: {"status": "FAILED", "retry_count": 2},
        },
        # event_id -> {"identity": (object_id, command), "event": {...}}
        "events": {},
        # Feeds `generated_id` for lifecycle/retry commands that carry no event_id.
        "sequence": 0,
        # The one-shot `drop-next` control: consumed by the next applied transition.
        "drop_next": False,
    }


def command_log(state: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per *applied* command, in order. A replay adds none.

    Derived from the recorded events (a Python dict keeps insertion order) rather than from
    a second list, so "the log has one entry" and "the event_id is recorded" cannot drift.
    """
    return [{"event_id": record["event"]["event_id"], "command": record["identity"][1],
             "object_id": record["identity"][0], "replayed": False}
            for record in state["events"].values()]


def control_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    """The `/v1/_p6_fixture/state` audit. Not a Core payload; the fixture's own evidence.

    The `items`, `jobs` and `commands` lists are the Gate's interface: `revision` starts at
    the read fixture's value and rises by one per *applied* command, and `commands` gets one
    entry per applied command (a replay, a refusal or a control adds none).
    """
    return {
        "fixture": FIXTURE_HEADER,
        "control": "state",
        "non_authoritative": True,
        "items": [{"memory_id": ident, "revision": item["revision"],
                   "pinned": item["pinned"], "status": item["status"],
                   "engine_state": item["engine_state"]}
                  for ident, item in state["items"].items()],
        "jobs": [{"job_id": ident, "status": job["status"],
                  "retry_count": job["retry_count"]}
                 for ident, job in state["jobs"].items()],
        "drop_next_armed": state["drop_next"],
        "commands": command_log(state),
    }


def apply_lifecycle(command: str, state: dict[str, Any], *, reason: str | None) -> dict[str, Any]:
    """The item's next state, mirroring P4's `_TRANSITIONS`/`_apply` for the columns it sets.

    `obsolete` sets the status and clears the pin; it does not change `engine_state` (the
    engine's soft retire is queued separately). `delete` is a local tombstone plus
    `DELETE_PENDING`, never `DELETED` while the remote copy is still there. `pin`/`unpin`
    touch only the pin and the revision, and none of the four touches `content_sha256`.
    """
    after = dict(state)
    after["revision"] = state["revision"] + 1
    if command == "obsolete":
        after["status"] = "OBSOLETE"
        after["pinned"] = False
        after["lifecycle_reason"] = reason
    elif command == "delete":
        after["status"] = "DELETED"
        after["pinned"] = False
        after["engine_state"] = "DELETE_PENDING"
        after["lifecycle_reason"] = reason
    elif command == "pin":
        after["pinned"] = True
    elif command == "unpin":
        after["pinned"] = False
    else:  # pragma: no cover - the contract's route table guards this
        raise AssertionError(f"unknown lifecycle command {command!r}")
    return after


# -- readback payloads (the read fixture's shapes, driven by state) ------------

def item_state_row(read: dict[str, Any], ident: str, state: dict[str, Any]) -> dict[str, Any]:
    row = base.item_row(read, ident=ident)
    row.update({
        "status": state["status"],
        "pinned": state["pinned"],
        "revision": state["revision"],
        "engine_state": state["engine_state"],
        "lifecycle_reason": state["lifecycle_reason"],
        "remote_deleted_at": state["remote_deleted_at"],
        "updated_at": RECORDED_AT,
    })
    assert set(row) == set(read["nested_fields"]["item"]), "fixture item shape drifted"
    return row


def item_lifecycle_summary(read: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    summary = base.lifecycle_summary(read)
    summary.update({
        "status": state["status"],
        "recallable": state["status"] == "ACTIVE",
        "pinned": state["pinned"],
        "engine_state": state["engine_state"],
        "remote_document_deleted": state["remote_deleted_at"] is not None,
        "lifecycle_reason": state["lifecycle_reason"],
    })
    assert set(summary) == set(read["nested_fields"]["lifecycle"]), "lifecycle shape drifted"
    return summary


def item_detail_payload(read: dict[str, Any], ident: str,
                        state: dict[str, Any]) -> dict[str, Any]:
    payload = base.item_payload(read)
    payload["memory"] = item_state_row(read, ident, state)
    payload["lifecycle"] = item_lifecycle_summary(read, state)
    payload["recallable"] = state["status"] == "ACTIVE"
    assert set(payload) == set(base.contract_field_sets(read)["item"]), "item-detail drifted"
    return payload


def item_snapshot(read: dict[str, Any], ident: str, state: dict[str, Any]) -> dict[str, Any]:
    """P4's `_snapshot`: metadata only, never the sanitized text."""
    row = item_state_row(read, ident, state)
    return {key: row[key] for key in
            ("memory_id", "document_id", "bank_id", "memory_type", "status", "pinned",
             "engine_state", "revision", "content_sha256")}


def job_state_row(read: dict[str, Any], ident: str, state: dict[str, Any]) -> dict[str, Any]:
    row = base.job_row(read, ident=ident, status=state["status"])
    row["retry_count"] = state["retry_count"]
    row["updated_at"] = RECORDED_AT
    assert set(row) == set(read["nested_fields"]["job"]), "fixture job shape drifted"
    return row


def job_detail_payload(read: dict[str, Any], ident: str,
                       state: dict[str, Any]) -> dict[str, Any]:
    payload = {"job": job_state_row(read, ident, state),
               "attempts": base.attempt_rows(read)}
    assert set(payload) == set(base.contract_field_sets(read)["job"]), "job-detail drifted"
    return payload


# -- write responses, asserted against the frozen contract ---------------------

def find_write(write: dict[str, Any], *, handler: str | None = None,
               path_pattern: str | None = None) -> dict[str, Any]:
    for entry in write["writes"]:
        if handler is not None and entry["handler"] == handler:
            return entry
        if path_pattern is not None and entry["path_pattern"] == path_pattern:
            return entry
    raise AssertionError(f"the frozen contract records no write with handler={handler!r} "
                         f"path_pattern={path_pattern!r}")


def write_shape(write: dict[str, Any], *, handler: str, index: int) -> set[str]:
    shapes = find_write(write, handler=handler)["response_shapes"]
    assert index < len(shapes), f"the contract records no response shape {index} for {handler}"
    return set(shapes[index])


def event_row(*, event_id: str, event_type: str, object_kind: str, object_id: str,
              host_id: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """A fixture-shaped command Event. The contract does not pin the nested event."""
    row = {"event_id": event_id, "event_type": event_type,
           "source_system": FIXTURE_HEADER, "object_kind": object_kind,
           "object_id": object_id, "actor_id": base.ACTOR, "actor_kind": "human",
           "host_id": host_id, "recorded_at": RECORDED_AT}
    row.update(extra or {})
    return row


def retry_payload(read: dict[str, Any], write: dict[str, Any], ident: str,
                  job_state: dict[str, Any], event: dict[str, Any], *,
                  replayed: bool) -> dict[str, Any]:
    payload = {"job": job_state_row(read, ident, job_state), "event": event,
               "replayed": replayed}
    assert set(payload) == write_shape(write, handler="retry_job", index=0), (
        "fixture retry response drifted from the frozen contract")
    return payload


def lifecycle_payload(read: dict[str, Any], write: dict[str, Any], ident: str,
                      after_state: dict[str, Any], event: dict[str, Any], *, command: str,
                      replayed: bool, before_state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"memory": item_state_row(read, ident, after_state), "event": event,
               "replayed": replayed, "job": None, "non_authoritative": True}
    if replayed:
        index = 1
    else:
        assert before_state is not None, "a fresh transition carries before and after"
        payload["before"] = item_snapshot(read, ident, before_state)
        payload["after"] = item_snapshot(read, ident, after_state)
        index = 0
    assert set(payload) == write_shape(write, handler=f'_lifecycle_command("{command}")',
                                       index=index), (
        "fixture lifecycle response drifted from the frozen contract")
    return payload


def bank_outcome() -> dict[str, Any]:
    """P4's `BankOutcome.as_dict()`, with fixture values."""
    return {"bank_id": f"bank_{base.ID_BODY}", "scope_kind": "project", "status": "ok",
            "error_code": None, "safe_error_summary": None, "result_count": 1}


def reflect_payload(write: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "status": "ok",
        # Text on purpose: a fixture whose `text` was already null could not show that a
        # human key sees it while the artificial agent key loses `safe_error_summary`.
        "text": "fixture: reflect synthesis (simulated text, not from a real engine)",
        "non_authoritative": True,
        "based_on_memory_ids": [ITEM],
        "banks": [bank_outcome()],
        "error_code": None,
        # An `ok` reflect with a summary is odd for a real Core; it is here so the
        # withholding rule has something to withhold on the one write an agent can reach.
        "safe_error_summary": "fixture: reflect output is not Truth (simulated text)",
        "trace_id": TRACE,
    }
    assert set(payload) == write_shape(write, handler="reflect", index=0), (
        "fixture reflect response drifted from the frozen contract")
    return payload


def shape_report(read: dict[str, Any], write: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One representative payload per recorded write response shape.

    Each builder asserts its own key set against the frozen contract, so calling this is the
    pre-serve drift check as well as the source for the startup lines.
    """
    state = default_state()
    before = state["items"][ITEM]
    after = dict(before, revision=before["revision"] + 1, pinned=True)
    fresh_event = event_row(event_id=base.EVENT, event_type="memory.pinned",
                            object_kind="memory", object_id=ITEM, host_id=HOST)
    replay_event = dict(fresh_event)
    retry_event = event_row(event_id=base.EVENT, event_type="memory.job.retried",
                            object_kind="memory_job", object_id=JOB, host_id=HOST)
    return {
        "retry": retry_payload(read, write, JOB, state["jobs"][JOB], retry_event,
                               replayed=False),
        "lifecycle": lifecycle_payload(read, write, ITEM, after, fresh_event, command="pin",
                                       replayed=False, before_state=before),
        "lifecycle-replay": lifecycle_payload(read, write, ITEM, after, replay_event,
                                              command="pin", replayed=True),
        "reflect": reflect_payload(write),
    }


def write_routes(write: dict[str, Any]) -> list[dict[str, Any]]:
    """The contract's writes, each mapped to the `do_POST` branch that implements it.

    The mapping is *derived* from the contract's own `handler` strings, so a write the
    fixture has no branch for raises here rather than 404-ing in production.
    """
    routes: list[dict[str, Any]] = []
    for entry in write["writes"]:
        handler = entry["handler"]
        if handler == "retry_job":
            kind, command = "retry", None
        elif handler == "reflect":
            kind, command = "reflect", None
        elif handler.startswith('_lifecycle_command("') and handler.endswith('")'):
            command = handler[len('_lifecycle_command("'):-2]
            assert command in ("obsolete", "delete", "pin", "unpin"), (
                f"the fixture has no lifecycle branch for {command!r}")
            kind = "lifecycle"
        else:
            raise AssertionError(
                f"the write fixture implements no do_POST branch for handler {handler!r}; "
                f"the frozen contract and the fixture have drifted")
        routes.append({"kind": kind, "command": command, "spec": entry,
                       "pattern": re.compile(entry["path_pattern"])})
    return routes


def assert_six_writes(write: dict[str, Any]) -> list[dict[str, Any]]:
    """The contract's writes must be exactly the six routes `do_POST` implements."""
    routes = write_routes(write)
    observed = {(route["kind"], route["command"]) for route in routes}
    assert len(routes) == len(EXPECTED_ROUTES), (
        f"the frozen contract records {len(routes)} writes; do_POST implements "
        f"{len(EXPECTED_ROUTES)}")
    assert observed == EXPECTED_ROUTES, (
        f"do_POST does not implement exactly the contract's writes: "
        f"symmetric difference {sorted(observed ^ EXPECTED_ROUTES)}")
    return routes


# -- the handler ---------------------------------------------------------------

class Handler(base.Handler):
    """The read fixture's handler, plus the contract's six writes.

    `contract` stays the *read* contract because every inherited route (`_require_route`,
    the shell and the seven reads) reads it. The write contract lives beside it.
    """

    write_contract: dict[str, Any] = {}
    state: dict[str, Any] = {}

    @classmethod
    def reset_state(cls) -> None:
        cls.state = default_state()

    # -- plumbing ---------------------------------------------------------------
    def _send(self, status: int, payload: Any, *, agent: bool = False) -> None:
        # Same policy as the read fixture (P4 applies it inside `_respond`, so it covers
        # every handler) with this fixture's own label: a write reply must not be
        # mistakable for a read-only fixture's.
        body = json.dumps(base.for_actor(payload, agent=agent)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-P6-Fixture", FIXTURE_HEADER)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict[str, Any] | None:
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except ValueError:
            size = 0
        raw = self.rfile.read(size) if size > 0 else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "invalid_request", "the request body must be a JSON object")
            return None
        if not isinstance(parsed, dict):
            self._error(400, "invalid_request", "the request body must be a JSON object")
            return None
        return parsed

    # -- the two by-id reads, reflecting state ----------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query = self.parsed
        if path.startswith(CONTROL_PREFIX):
            self._control("GET", path, query)
            return
        # The dashboard's audit readback reads the command Event back through the *read* surface
        # rather than trusting the write response. The read fixture has no events route (its Gate
        # never needed one), so the write fixture serves the Events it recorded; a real Core would
        # answer this from the EventStore.
        event = re.fullmatch(r"/v1/events/([^/]+)", path)
        if event is not None:
            if not self._require(self._scopes(), "objects:read"):
                return
            ident = event.group(1)
            if not base.ID_RE.fullmatch(ident):
                self._error(400, "invalid_request", "invalid event_id", field="event_id")
                return
            self._read_event(ident)
            return
        item = re.fullmatch(r"/v1/memory/items/([^/]+)", path)
        if item is not None:
            self._read_item(item.group(1))
            return
        job = re.fullmatch(r"/v1/memory/jobs/([^/]+)", path)
        if job is not None:
            self._read_job(job.group(1))
            return
        # Every other read is the read fixture's, unchanged.
        super().do_GET()

    def _read_event(self, ident: str) -> None:
        """The command Event the fixture recorded, by id.

        It is the same dict the write response carried, kept rather than rebuilt, so "the audit
        knows this command" and "the response said so" cannot drift apart.
        """
        record = self.state["events"].get(ident)
        if record is None:
            self._error(404, "not_found", f"event {ident} not found")
            return
        self._send(200, {"event": record["event"]})

    # -- fixture-only controls (no token, no contract, no product surface) -------
    def _control(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        """The four controls. Not P4, not scoped, not reachable through the proxy."""
        if method == "GET" and path == f"{CONTROL_PREFIX}state":
            self._send(200, control_state_payload(self.state))
            return
        if method == "POST" and path == f"{CONTROL_PREFIX}drop-next":
            self.state["drop_next"] = True
            self._send(200, {"control": "drop-next", "armed": True})
            return
        if method == "POST" and path == f"{CONTROL_PREFIX}drift":
            self._control_drift(query)
            return
        if method == "POST" and path == f"{CONTROL_PREFIX}set-job-status":
            self._control_set_job_status(query)
            return
        self._error(404, "not_found", f"no fixture control for {method} {path}")

    def _control_drift(self, query: dict[str, list[str]]) -> None:
        body = self._json_body()
        if body is None:
            return
        unknown = sorted(set(body) - {"item"})
        if unknown:
            self._error(400, "invalid_request", f"unknown fields: {', '.join(unknown)}",
                        fields=unknown)
            return
        ident = (query.get("item") or [None])[0] or body.get("item")
        if not is_id(ident, "mem"):
            self._error(400, "invalid_request", "item must be a mem_ id", field="item",
                        value=ident)
            return
        if ident not in self.state["items"]:
            self._error(404, "not_found", f"memory {ident} not found")
            return
        # No command Event and no command-log entry: this stands in for another actor's
        # write, which is exactly what makes the next caller's `expected_revision` stale.
        item = self.state["items"][ident]
        item["revision"] += 1
        self._send(200, {"control": "drift", "memory_id": ident,
                         "revision": item["revision"], "non_authoritative": True})

    def _control_set_job_status(self, query: dict[str, list[str]]) -> None:
        """Put a job into DONE/RUNNING/FAILED/PENDING so every retry branch is reachable.

        `retry_count` is deliberately left alone: the control changes the state a retry
        branches on, and a retry is the thing that resets the budget to 0.
        """
        body = self._json_body()
        if body is None:
            return
        unknown = sorted(set(body) - {"job", "status"})
        if unknown:
            self._error(400, "invalid_request", f"unknown fields: {', '.join(unknown)}",
                        fields=unknown)
            return
        job_id = (query.get("job") or [None])[0] or body.get("job")
        status = (query.get("status") or [None])[0] or body.get("status")
        if not is_id(job_id, "mjb"):
            self._error(400, "invalid_request", "job must be a mjb_ id", field="job",
                        value=job_id)
            return
        if status not in JOB_STATUSES:
            self._error(400, "invalid_request",
                        f"status must be one of {', '.join(JOB_STATUSES)}", field="status",
                        value=status)
            return
        if job_id not in self.state["jobs"]:
            self._error(404, "not_found", f"memory_job {job_id} not found")
            return
        self.state["jobs"][job_id]["status"] = status
        self._send(200, {"control": "set-job-status", "ok": True,
                         "job": job_state_row(self.contract, job_id,
                                              self.state["jobs"][job_id])})

    def _drop_reply(self) -> bool:
        """Consume a one-shot `drop-next` arming. `True` means the reply was dropped."""
        if not self.state["drop_next"]:
            return False
        self.state["drop_next"] = False
        self._error(504, "fixture_write_reply_dropped",
                    "fixture control: the write applied but its reply was dropped; resend "
                    "the same event_id to see the replay", control="drop-next")
        return True

    def _read_route(self, response_key: str) -> str:
        return next(route["path_pattern"] for route in self.contract["read_routes"]
                    if route["response_key"] == response_key)

    def _read_item(self, ident: str) -> None:
        scopes = self._scopes()
        if not self._require_route(scopes, self._read_route("item")):
            return
        if not is_id(ident, "mem"):
            self._error(400, "invalid_request", "invalid memory_id", field="memory_id")
            return
        if ident not in self.state["items"]:
            self._error(404, "not_found", f"memory {ident} not found")
            return
        self._send(200, item_detail_payload(self.contract, ident, self.state["items"][ident]),
                   agent=self._agent(scopes))

    def _read_job(self, ident: str) -> None:
        scopes = self._scopes()
        if not self._require_route(scopes, self._read_route("job")):
            return
        if not is_id(ident, "mjb"):
            self._error(400, "invalid_request", "invalid job_id", field="job_id")
            return
        if ident not in self.state["jobs"]:
            self._error(404, "not_found", f"memory_job {ident} not found")
            return
        self._send(200, job_detail_payload(self.contract, ident, self.state["jobs"][ident]),
                   agent=self._agent(scopes))

    # -- the six writes ----------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query = self.parsed
        if path.startswith(CONTROL_PREFIX):
            self._control("POST", path, query)
            return
        scopes = self._scopes()
        for route in write_routes(self.write_contract):
            match = route["pattern"].fullmatch(path)
            if match is None:
                continue
            ident = match.group(1) if match.groups() else None
            self._write(route, ident, scopes)
            return
        self._error(404, "not_found", f"no route for {path}")

    def _write(self, route: dict[str, Any], ident: str | None,
               scopes: set[str] | None) -> None:
        spec = route["spec"]
        # 1. authentication, then 2. the route's scope, named as Core names it.
        if not self._require(scopes, spec["scope"]):
            return
        agent = self._agent(scopes)
        # 3. actor kind. The stand-in for P4's `core.actor_kind(...)`: a token carrying
        # `actor:agent` cannot hold a lifecycle-management command, whatever its scopes.
        if spec["scope"] == "memory:manage" and agent:
            self._error(403, "forbidden_actor_kind",
                        "only a human or trusted system actor may issue this command",
                        scope=spec["scope"])
            return
        if route["kind"] == "reflect":
            self._reflect(spec, scopes, agent)
            return
        prefix = "mjb" if route["kind"] == "retry" else "mem"
        if not self._path_id(ident, prefix):
            return
        body = self._json_body()
        if body is None:
            return
        # 4. the allow-list, before any field is read.
        if not self._allow_list(body, spec):
            return
        if route["kind"] == "retry":
            self._retry(ident, body, scopes, agent, spec)
        else:
            self._lifecycle(ident, body, scopes, agent, spec, route["command"])

    def _path_id(self, ident: str | None, prefix: str) -> bool:
        field = "job_id" if prefix == "mjb" else "memory_id"
        if ident is None or not is_id(ident, prefix):
            self._error(400, "invalid_request", f"{field} must be a {prefix}_ id",
                        field=field, value=ident)
            return False
        return True

    def _allow_list(self, body: dict[str, Any], spec: dict[str, Any]) -> bool:
        unknown = sorted(set(body) - set(spec["allowed_body_fields"]))
        if unknown:
            self._error(400, "invalid_request", f"unknown fields: {', '.join(unknown)}",
                        fields=unknown)
            return False
        return True

    def _expected_revision(self, body: dict[str, Any]) -> int | None:
        if "expected_revision" not in body:
            # `errors.MissingExpectedRevision` is status 400 with this snake_case wire code.
            self._error(400, "missing_expected_revision", "expected_revision is required")
            return None
        value = body["expected_revision"]
        if type(value) is not int or value < 1:
            self._error(400, "invalid_request",
                        "expected_revision must be a positive integer",
                        field="expected_revision")
            return None
        return value

    def _host_id(self, body: dict[str, Any], *, required: bool) -> bool:
        value = body.get("host_id")
        if value is None:
            if required:
                self._error(400, "invalid_request", "host_id is required", field="host_id")
                return False
            return True
        if not is_id(value, "hst"):
            self._error(400, "invalid_request", "host_id must be a hst_ id",
                        field="host_id", value=value)
            return False
        return True

    def _event_id(self, body: dict[str, Any]) -> bool:
        value = body.get("event_id")
        if value is None:
            return True
        if not is_id(value, "evt"):
            self._error(400, "invalid_request", "event_id must be an evt_ id",
                        field="event_id", value=value)
            return False
        return True

    def _reason(self, body: dict[str, Any]) -> bool:
        value = body.get("reason")
        if value is None:
            return True
        if not isinstance(value, str) or len(value) > 500:
            self._error(400, "invalid_request",
                        "reason must be text of at most 500 characters", field="reason")
            return False
        return True

    def _replay_of(self, event_id: str | None,
                   identity: tuple[str, str]) -> dict[str, Any] | None | bool:
        """`None` when there is no recorded event, `False` on a cross-command conflict.

        A recorded `event_id` is a replay only for the same object *and* command; any other
        use is Core's `event_id_conflict`, not a silent second transition.
        """
        if event_id is None:
            return None
        record = self.state["events"].get(event_id)
        if record is None:
            return None
        if record["identity"] != identity:
            self._error(409, "event_id_conflict",
                        f"event_id {event_id} was already used for "
                        f"{record['identity'][1]} on {record['identity'][0]}",
                        event_id=event_id)
            return False
        return record

    def _record_event(self, event: dict[str, Any], event_id: str | None,
                      identity: tuple[str, str]) -> dict[str, Any]:
        if event_id is None:
            self.state["sequence"] += 1
            event_id = generated_id("evt", self.state["sequence"])
        event["event_id"] = event_id
        self.state["events"][event_id] = {"identity": identity, "event": event}
        return event

    # -- retry -------------------------------------------------------------------
    def _retry(self, ident: str, body: dict[str, Any], scopes: set[str] | None,
               agent: bool, spec: dict[str, Any]) -> None:
        # **No `host_id`/`event_id` shape validation here, because P4 has none.**
        # `memory/api.py::retry_job` passes `body.get("host_id", "")` and `body.get("event_id")`
        # straight to `request_retry` without checking either, while the lifecycle path does
        # validate them through `_transition`. An earlier version of this fixture checked both
        # and answered 400, which would have made the simulation *stricter* than the thing it
        # stands in for — found by adversarial review, and it is the kind of difference that
        # would turn a real Core's acceptance into a fixture refusal.
        if ident not in self.state["jobs"]:
            self._error(404, "memory_job_not_found", f"memory_job {ident} not found")
            return
        event_id = body.get("event_id") if isinstance(body.get("event_id"), str) else None
        replay = self._replay_of(event_id, (ident, "retry"))
        if replay is False:
            return
        state = self.state["jobs"][ident]
        if replay is not None:
            self._send(200, retry_payload(self.contract, self.write_contract, ident, state,
                                          replay["event"], replayed=True), agent=agent)
            return
        # Mirror `request_retry` exactly: DONE, RUNNING and PENDING are all refused with the
        # one `InvalidStateTransition` code, and only a FAILED job is requeued.
        if state["status"] == "DONE":
            self._error(409, "invalid_state_transition",
                        "a DONE Memory job is not retried; the document is already retained",
                        job_id=ident, job_status="DONE")
            return
        if state["status"] == "RUNNING":
            self._error(409, "invalid_state_transition",
                        "this Memory job is claimed by a worker; wait for its lease to expire",
                        job_id=ident, job_status="RUNNING")
            return
        if state["status"] == "PENDING":
            self._error(409, "invalid_state_transition",
                        "this Memory job is already queued",
                        job_id=ident, job_status="PENDING")
            return
        if state["status"] != "FAILED":
            self._error(409, "invalid_state_transition", "this Memory job cannot be retried",
                        job_id=ident, job_status=state["status"])
            return
        before = dict(state)
        # `SET status='PENDING', retry_count=0`: the budget is reset, not incremented.
        after = {"status": "PENDING", "retry_count": 0}
        self.state["jobs"][ident] = after
        event = event_row(event_id="", event_type="memory.job.retried", object_kind="memory_job",
                          object_id=ident, host_id=body.get("host_id"),
                          extra={"job_id": ident, "kind": "retain",
                                 "memory_id": ITEM,
                                 "document_id": base.job_row(self.contract, ident=ident)
                                 ["document_id"],
                                 "bank_id": base.job_row(self.contract, ident=ident)["bank_id"],
                                 "previous_status": before["status"],
                                 "previous_retry_count": before["retry_count"]})
        event = self._record_event(event, event_id, (ident, "retry"))
        if self._drop_reply():
            return
        self._send(200, retry_payload(self.contract, self.write_contract, ident, after, event,
                                      replayed=False), agent=agent)

    # -- the four lifecycle commands ----------------------------------------------
    def _lifecycle(self, ident: str, body: dict[str, Any], scopes: set[str] | None,
                   agent: bool, spec: dict[str, Any], command: str) -> None:
        # The field order mirrors `lifecycle.py`'s `_transition` (after the allow-list):
        # expected_revision, then host_id, then event_id, then reason.
        expected = self._expected_revision(body)
        if expected is None:
            return
        if not self._host_id(body, required=True):
            return
        if not self._event_id(body):
            return
        if not self._reason(body):
            return
        if ident not in self.state["items"]:
            self._error(404, "memory_not_found", f"memory {ident} not found")
            return
        event_id = body.get("event_id") if isinstance(body.get("event_id"), str) else None
        replay = self._replay_of(event_id, (ident, command))
        if replay is False:
            return
        if replay is not None:
            self._send(200, lifecycle_payload(
                self.contract, self.write_contract, ident, self.state["items"][ident],
                replay["event"], command=command, replayed=True), agent=agent)
            return
        state = self.state["items"][ident]
        # 5. State before revision, mirroring P4's recorded ordering in `memory/lifecycle.py`:
        # "you cannot obsolete a tombstone" is a property of the target that no revision can
        # change, so it is answered as such rather than as a stale revision.
        if state["status"] == "DELETED":
            self._error(409, "invalid_state_transition",
                        "a deleted memory item is a tombstone and cannot be changed",
                        memory_id=ident, memory_status=state["status"])
            return
        if command == "obsolete" and state["status"] == "OBSOLETE":
            self._error(409, "invalid_state_transition", "this memory item is already obsolete",
                        memory_id=ident, memory_status=state["status"])
            return
        if command in ("pin", "unpin") and state["status"] != "ACTIVE":
            self._error(409, "invalid_state_transition",
                        "an obsolete memory item cannot be pinned; recall and the Context "
                        "Pack exclude it regardless of the pin",
                        memory_id=ident, memory_status=state["status"])
            return
        # 6. ... and only then the revision.
        if state["revision"] != expected:
            self._error(409, "revision_conflict", "the memory item revision is stale",
                        memory_id=ident, current_revision=state["revision"],
                        expected_revision=expected)
            return
        before = dict(state)
        after = apply_lifecycle(command, state, reason=body.get("reason"))
        self.state["items"][ident] = after
        event = event_row(event_id="", event_type=f"memory.{command}",
                          object_kind="memory", object_id=ident, host_id=body.get("host_id"),
                          extra={"action": command,
                                 "expected_revision": expected,
                                 "reason": body.get("reason"),
                                 "before": item_snapshot(self.contract, ident, before),
                                 "after": item_snapshot(self.contract, ident, after)})
        event = self._record_event(event, event_id, (ident, command))
        if self._drop_reply():
            return
        self._send(200, lifecycle_payload(self.contract, self.write_contract, ident, after,
                                          event, command=command, replayed=False,
                                          before_state=before), agent=agent)

    # -- reflect -------------------------------------------------------------------
    def _reflect(self, spec: dict[str, Any], scopes: set[str] | None, agent: bool) -> None:
        body = self._json_body()
        if body is None:
            return
        if not self._allow_list(body, spec):
            return
        project_id = body.get("project_id")
        if not is_id(project_id, "prj"):
            self._error(400, "invalid_request", "project_id must be a prj_ id",
                        field="project_id", value=project_id)
            return
        if project_id != PROJECT:
            # `ProjectAccess.require` reports a project the actor may not use as
            # `NotFound("project", ...)` -> `project_not_found`, so the write surface cannot
            # be used to enumerate another project's work.
            self._error(404, "project_not_found", f"project {project_id} not found")
            return
        query = body.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            self._error(400, "invalid_request",
                        "query must be nonempty text of at most 4000 characters",
                        field="query")
            return
        max_tokens = body.get("max_tokens")
        if max_tokens is not None and type(max_tokens) is not int:
            self._error(400, "invalid_request", "max_tokens must be an integer",
                        field="max_tokens")
            return
        if not self._host_id(body, required=False):
            return
        if not self._event_id(body):
            return
        self._send(200, reflect_payload(self.write_contract), agent=agent)


def make_server(port: int, tokens: dict[str, set[str]], read: dict[str, Any],
                write: dict[str, Any]) -> ThreadingHTTPServer:
    Handler.contract = read  # the inherited shell and reads keep reading this
    Handler.write_contract = write
    Handler.tokens = tokens
    Handler.reset_state()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def pre_serve_checks(read: dict[str, Any], write: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Assert route coverage and every response shape before a socket is opened."""
    routes = assert_six_writes(write)
    print(f"p4-write-fixture: the frozen contract records exactly the six writes do_POST "
          f"implements ({', '.join(sorted(route['kind'] for route in routes))})",
          file=sys.stderr)
    report = shape_report(read, write)
    for name, payload in report.items():
        print(f"p4-write-fixture: {name} shape matches the frozen contract "
              f"({len(payload)} fields observed)", file=sys.stderr)
    return report


def parse_tokens(argv: list[str]) -> tuple[argparse.Namespace, dict[str, set[str]]]:
    """The read fixture's `--token`/`--scopes` semantics, with this fixture's defaults."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8918)
    parser.add_argument("--token", action="append",
                        help="bearer token the fixture accepts (never a real key); repeat "
                             "with a matching --scopes to accept several")
    parser.add_argument("--scopes", action="append",
                        help="comma-separated scopes for the matching --token (repeatable)")
    args = parser.parse_args(argv)
    tokens = args.token or [DEFAULT_TOKEN]
    scopes = args.scopes or [",".join(ALL_SCOPES)]
    if len(scopes) == 1 and len(tokens) > 1:
        scopes = scopes * len(tokens)
    if len(tokens) != len(scopes):
        parser.error("--token and --scopes must be given the same number of times")
    return args, {token: {scope.strip() for scope in entry.split(",") if scope.strip()}
                  for token, entry in zip(tokens, scopes)}


def main(argv: list[str] | None = None) -> int:
    args, tokens = parse_tokens(list(sys.argv[1:] if argv is None else argv))
    read = read_contract()
    write = load_contract()

    # Assert the frozen contract before serving anything, so a drifted fixture fails loudly
    # at startup instead of producing misleading evidence.
    pre_serve_checks(read, write)

    print("=" * 78, file=sys.stderr)
    print("SIMULATED P4 MEMORY WRITE FIXTURE — NOT a Jasmine Core, and NOT P4 verification.",
          file=sys.stderr)
    print("P4 phase acceptance has NOT passed. It answers fixed shapes derived from the",
          file=sys.stderr)
    print("  pinned commit", file=sys.stderr)
    print(f"  {write['source']['ref']}", file=sys.stderr)
    print("and mutates an in-memory dict. There is no Truth store, no engine and no real",
          file=sys.stderr)
    print("jobs; a lifecycle command changes nothing outside this process.", file=sys.stderr)
    print(f"Every reply carries X-P6-Fixture: {FIXTURE_HEADER}.", file=sys.stderr)
    print("The actor-kind check is a scope stand-in (actor:agent), not P4's actor record.",
          file=sys.stderr)
    print("Wire codes and retry semantics follow the pinned errors.py / memory/jobs.py:",
          file=sys.stderr)
    print("  snake_case codes, missing_expected_revision is 400, only FAILED retries and",
          file=sys.stderr)
    print("  retry_count resets to 0; DONE/RUNNING/PENDING are invalid_state_transition.",
          file=sys.stderr)
    print(f"Fixture controls (no token, NOT P4, NOT in the contract): "
          f"POST {CONTROL_PREFIX}drift, POST {CONTROL_PREFIX}set-job-status, "
          f"POST {CONTROL_PREFIX}drop-next, GET {CONTROL_PREFIX}state", file=sys.stderr)
    for token, scopes in tokens.items():
        # Masked, not printed: this banner is redirected into evidence bundles, and the bundle's
        # own README claims no token appears in any file. The first six characters are enough for
        # an operator to tell their keys apart in a terminal.
        print(f"accepts token {token[:6]}… with scopes: {', '.join(sorted(scopes))}",
              file=sys.stderr)
    print("=" * 78, file=sys.stderr)

    server = make_server(args.port, tokens, read, write)
    print(f"p4-write-fixture listening on http://127.0.0.1:{server.server_address[1]}",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
