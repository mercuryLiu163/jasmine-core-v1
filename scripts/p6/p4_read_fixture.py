#!/usr/bin/env python3.13
"""A **simulated** P4 memory read fixture — not a Jasmine Core, and not P4 verification.

P6-03b1's dependency is missing: baseline `850fbec` is schema 8 and has no
`src/jasmine_core/memory/` at all, so there is nothing real to point the dashboard at. This
exists so the four Memory views can be exercised end to end while the dependency is pending.

It is loud about being a simulation, on purpose:

* it prints a banner on startup and stamps `X-P6-Fixture: simulated-p4-reads` on **every**
  reply, so a screenshot or a recorded response cannot be mistaken for a real Core;
* it asserts every payload's top-level field set equals the frozen contract *before serving
  anything*, so a drifted fixture fails at startup instead of producing misleading evidence;
* it enforces the scopes the frozen contract records, and names the same scope Core would.

What it cannot do: produce P4's real values. It has no Truth store, no engine and no jobs.
The nested documents it serves mirror the pinned contract's key sets with fixture values.

Fidelity limits worth stating rather than discovering:

* `engine_claim` is a *string* in P4 (`describe_engine_claim` composes prose from the item's
  columns); this fixture serves prose-shaped fixture text, not P4's sentences.
* The `receipt` on a job or an attempt is `None` here; P4 decodes real JSON into it.
* `deployment` inside health is P4's `ProjectAccess.describe()`; this serves a fixture
  mapping with the same role, not that function's output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "web" / "contracts" / "p4-memory-read.v1.json"

FIXTURE_HEADER = "simulated-p4-reads"
ID_BODY = "01M4B6VGC29ESRDRQR62DX8AK8"
JOB = f"mjb_{ID_BODY}"
JOB_2 = f"mjb_{'01M4B6VGC29ESRDRQR62DX8AK9'}"
ITEM = f"mem_{ID_BODY}"
ITEM_2 = f"mem_{'01M4B6VGC29ESRDRQR62DX8AK9'}"
TRACE = f"mtr_{ID_BODY}"
TRACE_2 = f"mtr_{'01M4B6VGC29ESRDRQR62DX8AK9'}"
PROJECT = f"prj_{ID_BODY}"
PROJECT_OTHER = f"prj_{'01M4B6VGC29ESRDRQR62DX8AKZ'}"
HOST = f"hst_{ID_BODY}"
ACTOR = f"act_{ID_BODY}"
SESSION = f"ses_{ID_BODY}"
EVENT = f"evt_{ID_BODY}"
TASK = f"tsk_{ID_BODY}"

#: A valid-shaped id that the fixture does not hold, so `not_found` can be exercised without
#: faking a malformed id (which the views refuse locally).
UNKNOWN = f"mem_{'01M4B6VGC29ESRDRQR62DX8AK7'}"

ID_RE = re.compile(r"^[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")

#: Sign-in validates with an authenticated `GET /v1/projects`, so every token needs
#: `objects:read` before it can reach a Memory view at all.
ALL_SCOPES = ("objects:read", "memory:read")


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def contract_field_sets(contract: dict[str, Any]) -> dict[str, list[str]]:
    """Top-level response keys per route, keyed by the route's response identity."""
    return {route["response_key"]: route["response_fields"] for route in contract["read_routes"]}


def health_payload(contract: dict[str, Any]) -> dict[str, Any]:
    nested = contract["nested_fields"]
    payload = {
        "status": "ok",
        "engine_id": "p4-fixture-engine",
        "configured": True,
        "reachable": True,
        "api_version": "fixture-v1",
        "origin_host": "engine.invalid",
        "features": {"recall": True},
        "last_check_at": "2026-10-07T12:00:00Z",
        "last_success_at": "2026-10-07T12:00:00Z",
        "last_error_code": None,
        "safe_error_summary": None,
        # `jobs` and `items` are count maps, so they carry their own key sets.
        "jobs": {key: 0 for key in nested["job_counts"]},
        "items": {key: 0 for key in nested["health_items"]},
        "degraded": False,
        "core_impact": "none",
        "deployment": {"mode": "single_user", "projects": [PROJECT]},
        "checked_at": "2026-10-07T12:00:01Z",
        "non_authoritative": True,
    }
    assert set(payload) == set(contract_field_sets(contract)["health"]), (
        "fixture health shape drifted from the frozen contract")
    return payload


def job_row(contract: dict[str, Any], *, ident: str, status: str = "DONE") -> dict[str, Any]:
    row = dict.fromkeys(contract["nested_fields"]["job"])
    row.update({
        "job_id": ident,
        "kind": "retain",
        "memory_id": ITEM,
        "source_event_id": EVENT,
        "project_id": PROJECT,
        "task_id": TASK,
        "memory_type": "VERIFIED_OUTCOME",
        "bank_id": f"bank_{ID_BODY}",
        "document_id": f"doc_{ID_BODY}",
        "content_sha256": "a" * 64,
        "status": status,
        "retry_count": 0,
        "max_attempts": 3,
        "next_attempt_at": "2026-10-07T12:00:00Z",
        "lease_owner": None,
        "lease_expires_at": None,
        "claim_generation": 1,
        "last_error_code": None,
        "safe_error_summary": None,
        "receipt": None,
        "created_at": "2026-10-07T12:00:00Z",
        "updated_at": "2026-10-07T12:01:00Z",
        "completed_at": "2026-10-07T12:01:00Z",
    })
    assert set(row) == set(contract["nested_fields"]["job"]), "fixture job shape drifted"
    return row


def attempt_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    row = dict.fromkeys(contract["nested_fields"]["attempt"])
    row.update({
        "attempt_id": f"mja_{ID_BODY}",
        "job_id": JOB,
        "attempt_number": 1,
        "phase": "retain",
        "outcome": "ok",
        "error_code": None,
        "safe_error_summary": None,
        "receipt": None,
        "lease_owner": "fixture-worker",
        "started_at": "2026-10-07T12:00:30Z",
        "finished_at": "2026-10-07T12:01:00Z",
    })
    assert set(row) == set(contract["nested_fields"]["attempt"]), "fixture attempt shape drifted"
    return [row]


def lifecycle_summary(contract: dict[str, Any], *, status: str = "ACTIVE") -> dict[str, Any]:
    summary = dict.fromkeys(contract["nested_fields"]["lifecycle"])
    summary.update({
        "status": status,
        "recallable": status == "ACTIVE",
        "pinned": False,
        "pin_affects": "inspector_ordering_only",
        "engine_state": "RETAINED",
        "remote_document_deleted": False,
        "last_recalled_at": "2026-10-07T12:02:00Z",
        "recall_count": 3,
        "lifecycle_reason": None,
    })
    assert set(summary) == set(contract["nested_fields"]["lifecycle"]), (
        "fixture lifecycle shape drifted from the frozen contract")
    return summary


def item_row(contract: dict[str, Any], *, ident: str, status: str = "ACTIVE") -> dict[str, Any]:
    item = dict.fromkeys(contract["nested_fields"]["item"])
    item.update({
        "memory_id": ident,
        "bank_id": f"bank_{ID_BODY}",
        "scope_kind": "project",
        "project_id": PROJECT,
        "task_id": TASK,
        "memory_type": "VERIFIED_OUTCOME",
        "document_id": f"doc_{ID_BODY}",
        "source_event_id": EVENT,
        "evidence_ids": [f"evd_{ID_BODY}"],
        "origin_rule_id": None,
        "origin_rule_version": None,
        "origin_event_id": None,
        "content_sha256": "b" * 64,
        "sanitized_text": "fixture memory text (simulated, not from a real engine)",
        "status": status,
        "pinned": False,
        "revision": 1,
        "engine_state": "RETAINED",
        "receipt": None,
        "last_error_code": None,
        "safe_error_summary": None,
        "remote_deleted_at": None,
        "lifecycle_reason": None,
        "last_recalled_at": "2026-10-07T12:02:00Z",
        "recall_count": 3,
        "created_at": "2026-10-07T12:00:00Z",
        "updated_at": "2026-10-07T12:02:00Z",
    })
    assert set(item) == set(contract["nested_fields"]["item"]), "fixture item shape drifted"
    return item


def trace_row(contract: dict[str, Any], *, ident: str, mode: str = "recall") -> dict[str, Any]:
    trace = dict.fromkeys(contract["nested_fields"]["trace"])
    trace.update({
        "trace_id": ident,
        "mode": mode,
        "status": "ok",
        "project_id": PROJECT,
        "task_id": TASK,
        "session_id": SESSION,
        "actor_id": ACTOR,
        "query_sha256": "c" * 64,
        "query_length": 42,
        "requested_types": ["VERIFIED_OUTCOME"],
        "banks": [f"bank_{ID_BODY}"],
        "filtered": [],
        "budget_tokens": 512,
        "used_tokens": 128,
        "authority_refs": [],
        "state_revision": 7,
        "error_code": None,
        "safe_error_summary": None,
        "created_at": "2026-10-07T12:03:00Z",
    })
    assert set(trace) == set(contract["nested_fields"]["trace"]), "fixture trace shape drifted"
    return trace


def jobs_payload(contract: dict[str, Any], *, empty: bool) -> dict[str, Any]:
    fields = contract_field_sets(contract)["jobs"]
    if empty:
        payload = {"jobs": [], "count": 0,
                   "counts": {key: 0 for key in contract["nested_fields"]["job_counts"]}}
    else:
        rows = [job_row(contract, ident=JOB), job_row(contract, ident=JOB_2, status="FAILED")]
        counts = {key: 0 for key in contract["nested_fields"]["job_counts"]}
        counts["done"], counts["failed"] = 1, 1
        payload = {"jobs": rows, "count": len(rows), "counts": counts}
    assert set(payload) == set(fields), "fixture jobs shape drifted from the frozen contract"
    assert payload["count"] == len(payload["jobs"]), "count must match the rows served"
    return payload


def job_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = {"job": job_row(contract, ident=JOB), "attempts": attempt_rows(contract)}
    assert set(payload) == set(contract_field_sets(contract)["job"]), (
        "fixture job-detail shape drifted from the frozen contract")
    return payload


def items_payload(contract: dict[str, Any], *, empty: bool) -> dict[str, Any]:
    fields = contract_field_sets(contract)["items"]
    if empty:
        payload = {"items": [], "count": 0, "non_authoritative": True}
    else:
        # The list endpoint adds `lifecycle` to each row; the contract records that, so the
        # fixture serves it rather than the bare item shape.
        rows = [{**item_row(contract, ident=ident),
                 "lifecycle": lifecycle_summary(contract)} for ident in (ITEM, ITEM_2)]
        payload = {"items": rows, "count": len(rows), "non_authoritative": True}
    assert set(payload) == set(fields), "fixture items shape drifted from the frozen contract"
    assert payload["count"] == len(payload["items"]), "count must match the rows served"
    return payload


def item_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "memory": item_row(contract, ident=ITEM),
        "sources": [{"source_event_id": EVENT, "role": "origin",
                     "at": "2026-10-07T12:00:00Z"}],
        "jobs": [job_row(contract, ident=JOB)],
        "recallable": True,
        "lifecycle": lifecycle_summary(contract),
        "engine_claim": "fixture: Core recorded the retain; the engine has not been asked.",
        "non_authoritative": True,
    }
    assert set(payload) == set(contract_field_sets(contract)["item"]), (
        "fixture item-detail shape drifted from the frozen contract")
    return payload


def traces_payload(contract: dict[str, Any], *, empty: bool) -> dict[str, Any]:
    fields = contract_field_sets(contract)["traces"]
    if empty:
        payload = {"traces": [], "count": 0}
    else:
        rows = [trace_row(contract, ident=TRACE),
                trace_row(contract, ident=TRACE_2, mode="reflect")]
        payload = {"traces": rows, "count": len(rows)}
    assert set(payload) == set(fields), "fixture traces shape drifted from the frozen contract"
    assert payload["count"] == len(payload["traces"]), "count must match the rows served"
    return payload


def trace_payload(contract: dict[str, Any]) -> dict[str, Any]:
    trace = trace_row(contract, ident=TRACE)
    # The detail read carries `items`; the list row does not.
    trace = {**trace, "items": [{"result_id": f"mqr_{ID_BODY}", "rank": 1,
                                 "memory_id": ITEM, "score": 0.9}]}
    payload = {"trace": trace}
    assert set(payload) == set(contract_field_sets(contract)["trace"]), (
        "fixture trace-detail shape drifted from the frozen contract")
    return payload


class Handler(BaseHTTPRequestHandler):
    contract: dict[str, Any] = {}
    tokens: dict[str, set[str]] = {}

    # -- plumbing ---------------------------------------------------------------
    @property
    def parsed(self) -> tuple[str, dict[str, list[str]]]:
        url = urlparse(self.path)
        return url.path, parse_qs(url.query)

    def _scopes(self) -> set[str] | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return self.tokens.get(header[len("Bearer "):].strip())

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the fixture quiet-ish
        sys.stderr.write("p4-fixture: " + (fmt % args) + "\n")

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-P6-Fixture", FIXTURE_HEADER)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str, **details: Any) -> None:
        self._send(status, {"error": {"code": code, "message": message,
                                      "request_id": "aud_fixture", "details": details}})

    def _require_route(self, scopes: set[str] | None, pattern: str) -> bool:
        """Enforce the full scope set the frozen contract records for a route."""
        route = next(r for r in self.contract["read_routes"]
                     if r["path_pattern"] == pattern)
        # P4's reads require exactly their route scope: unlike P3 there is no shared helper
        # adding more, which this contract records and the fixture enforces.
        return self._require(scopes, route["scope"], *route.get("extra_scopes", []))

    def _require(self, scopes: set[str] | None, *needed: str) -> bool:
        if scopes is None:
            self._error(401, "unauthenticated", "token missing, unknown or revoked")
            return False
        # Named in the order Core requires them, not alphabetically.
        missing = [scope for scope in dict.fromkeys(needed) if scope not in scopes]
        if missing:
            self._error(403, "forbidden_scope",
                        f"this key does not carry the {missing[0]} scope",
                        required=missing[0], granted=sorted(scopes))
            return False
        return True

    # -- the Core shell the dashboard signs in with -----------------------------
    def _shell(self, path: str) -> bool:
        if path == "/v1/health":
            self._send(200, {"status": "ok", "core_version": "p4-fixture",
                             "schema_version": 11})
            return True
        if path == "/v1/projects":
            self._require(self._scopes(), "objects:read")
            self._send(200, {"projects": [{"project_id": PROJECT, "name": "P4 fixture project",
                                           "host_id": HOST}], "count": 1})
            return True
        if path == "/v1/hosts":
            self._require(self._scopes(), "objects:read")
            self._send(200, {"hosts": [{"host_id": HOST, "display_name": "p4-fixture-host"}]})
            return True
        return False

    # -- the seven memory reads -------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query = self.parsed
        scopes = self._scopes()
        if self._shell(path):
            return

        # An empty `scope` token models P4's "an actor with no grants gets an empty list,
        # not the whole database" — the same 200 the real Core answers, so the view can be
        # tested for rendering an empty result rather than an error.
        grantless = scopes is not None and "memory:read" in scopes and "projects:none" in scopes

        if path == "/v1/memory/health":
            if not self._require_route(scopes, r"^/v1/memory/health$"):
                return
            self._send(200, health_payload(self.contract))
            return

        if path == "/v1/memory/jobs":
            if not self._require_route(scopes, r"^/v1/memory/jobs$"):
                return
            self._send(200, jobs_payload(self.contract, empty=grantless))
            return

        job = re.fullmatch(r"/v1/memory/jobs/([^/]+)", path)
        if job:
            if not self._require_route(scopes, r"^/v1/memory/jobs/([^/]+)$"):
                return
            ident = job.group(1)
            if not ID_RE.fullmatch(ident):
                self._error(400, "invalid_request", "invalid job_id", field="job_id")
                return
            if ident not in (JOB, JOB_2):
                self._error(404, "not_found", f"memory_job {ident} not found")
                return
            self._send(200, job_payload(self.contract))
            return

        if path == "/v1/memory/items":
            if not self._require_route(scopes, r"^/v1/memory/items$"):
                return
            project = (query.get("project_id") or [None])[0]
            if project == PROJECT_OTHER:
                # P4 reports a project the actor may not use as `not_found`, so the inspector
                # cannot enumerate another project's work.
                self._error(404, "not_found", f"project {project} not found")
                return
            self._send(200, items_payload(self.contract, empty=grantless))
            return

        item = re.fullmatch(r"/v1/memory/items/([^/]+)", path)
        if item:
            if not self._require_route(scopes, r"^/v1/memory/items/([^/]+)$"):
                return
            ident = item.group(1)
            if not ID_RE.fullmatch(ident):
                self._error(400, "invalid_request", "invalid memory_id", field="memory_id")
                return
            if ident not in (ITEM, ITEM_2):
                self._error(404, "not_found", f"memory {ident} not found")
                return
            self._send(200, item_payload(self.contract))
            return

        if path == "/v1/memory/traces":
            if not self._require_route(scopes, r"^/v1/memory/traces$"):
                return
            self._send(200, traces_payload(self.contract, empty=grantless))
            return

        trace = re.fullmatch(r"/v1/memory/traces/([^/]+)", path)
        if trace:
            if not self._require_route(scopes, r"^/v1/memory/traces/([^/]+)$"):
                return
            ident = trace.group(1)
            if not ID_RE.fullmatch(ident):
                self._error(400, "invalid_request", "invalid trace_id", field="trace_id")
                return
            if ident not in (TRACE, TRACE_2):
                self._error(404, "not_found", f"memory_trace {ident} not found")
                return
            self._send(200, trace_payload(self.contract))
            return

        self._error(404, "not_found", f"no route for {path}")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Every write is P6-03b2, so this slice's fixture refuses them by name."""
        self._error(405, "method_not_allowed",
                    "P6-03b1 is read-only; retry, the lifecycle commands and reflect are "
                    "P6-03b2 and are not part of this slice")


def make_server(port: int, tokens: dict[str, set[str]], contract: dict[str, Any]) -> ThreadingHTTPServer:
    Handler.contract = contract
    Handler.tokens = tokens
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def parse_tokens(argv: list[str]) -> tuple[argparse.Namespace, dict[str, set[str]]]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8917)
    parser.add_argument("--token", action="append",
                        help="bearer token the fixture accepts (never a real key); repeat "
                             "with a matching --scopes to accept several")
    parser.add_argument("--scopes", action="append",
                        help="comma-separated scopes for the matching --token (repeatable)")
    args = parser.parse_args(argv)
    tokens = args.token or ["p4fixture-token"]
    scopes = args.scopes or [",".join(ALL_SCOPES)]
    if len(scopes) == 1 and len(tokens) > 1:
        scopes = scopes * len(tokens)
    if len(tokens) != len(scopes):
        parser.error("--token and --scopes must be given the same number of times")
    return args, {token: {scope.strip() for scope in entry.split(",") if scope.strip()}
                  for token, entry in zip(tokens, scopes)}


def main(argv: list[str] | None = None) -> int:
    args, tokens = parse_tokens(list(sys.argv[1:] if argv is None else argv))
    contract = load_contract()

    # Validate every payload against the frozen contract before serving anything, so a
    # drifted fixture fails loudly at startup instead of producing misleading evidence.
    for name, payload in (("health", health_payload(contract)),
                          ("jobs", jobs_payload(contract, empty=False)),
                          ("jobs-empty", jobs_payload(contract, empty=True)),
                          ("job", job_payload(contract)),
                          ("items", items_payload(contract, empty=False)),
                          ("items-empty", items_payload(contract, empty=True)),
                          ("item", item_payload(contract)),
                          ("traces", traces_payload(contract, empty=False)),
                          ("trace", trace_payload(contract))):
        print(f"p4-fixture: {name} shape matches the frozen contract "
              f"({len(payload)} fields observed)", file=sys.stderr)

    print("=" * 78, file=sys.stderr)
    print("SIMULATED P4 MEMORY READ FIXTURE — NOT a Jasmine Core, and NOT P4 verification.",
          file=sys.stderr)
    print("It answers fixed shapes derived from the pinned commit", file=sys.stderr)
    print(f"  {contract['source']['ref']}", file=sys.stderr)
    print("The baseline Core has none of these routes; this exists so the P6-03b1 views can",
          file=sys.stderr)
    print("be exercised end to end. Values are the fixture's, never a real engine's.",
          file=sys.stderr)
    print(f"Every reply carries X-P6-Fixture: {FIXTURE_HEADER}.", file=sys.stderr)
    for token, scopes in tokens.items():
        print(f"accepts token {token!r} with scopes: {', '.join(sorted(scopes))}", file=sys.stderr)
    print("=" * 78, file=sys.stderr)

    server = make_server(args.port, tokens, contract)
    print(f"p4-fixture listening on http://127.0.0.1:{server.server_address[1]}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
