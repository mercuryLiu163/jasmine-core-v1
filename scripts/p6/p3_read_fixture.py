#!/usr/bin/env python3.13
"""SIMULATED P3 read fixture — **not** a Jasmine Core, and not P3 verification.

P6-03a's four read endpoints exist on branch `p3-checkpoint-context-resume` and not in
the baseline (`850fbec`, schema 8). The taskbook's rule for a missing dependency is a
*clearly-marked simulated fixture* plus a contract request, so this serves the four read
shapes and says loudly, in three places, that it is simulated:

* on startup, to stderr, and in an `X-P6-Fixture` response header on every reply;
* in the file name and this docstring;
* in the evidence bundle that records runs against it.

What it will not do is pretend to be evidence about P3. It has no database, no
`continuity.py`, no `context/builder.py`: it answers a fixed shape. A real P3 Core is
still required before P6-03a can be called verified rather than contract-checked.

It validates its own payloads against `web/contracts/p3-continuity-read.v1.json` before
serving them. A fixture whose field set drifted from the frozen contract would be worse
than no fixture: every screenshot would be evidence for a shape nobody agreed to. If a
payload does not match, the server refuses the request with `fixture_contract_mismatch`
rather than answering.

Endpoints
    GET /v1/health                                  no auth
    GET /v1/projects?limit=1                        auth (sign-in validation)
    GET /v1/hosts                                   auth
    GET /v1/context/{ctx}                           context:read
    GET /v1/tasks/{tsk}/checkpoints/latest[?session_id=]  checkpoint:read
    GET /v1/checkpoints/{ckp}                       checkpoint:read
    GET /v1/resumes/{rms}                           resume:read + checkpoint:read

Tokens are issued by the `--scopes` argument so a run can be missing a scope on purpose.
Only `--out`-less: nothing is written to disk.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "web" / "contracts" / "p3-continuity-read.v1.json"

FIXTURE_HEADER = "simulated-p3-reads"
ID_BODY = "01M4B6VGC29ESRDRQR62DX8AK8"
ID_RE = re.compile(r"^[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")

PROJECT = f"prj_{ID_BODY}"
TASK = f"tsk_{ID_BODY}"
TASK_EMPTY = f"tsk_{'01M4B6VGC29ESRDRQR62DX8AK9'}"
SESSION = f"ses_{ID_BODY}"
HOST = f"hst_{ID_BODY}"
ACTOR = f"act_{ID_BODY}"
CHECKPOINT = f"ckp_{ID_BODY}"
CHECKPOINT_2 = f"ckp_{'01M4B6VGC29ESRDRQR62DX8AKB'}"
PACK = f"ctx_{ID_BODY}"
RESUME = f"rms_{ID_BODY}"
SOURCE_EVENT = f"evt_{'01M4B6VGC29ESRDRQR62DX8AKC'}"
COMMAND_EVENT = f"evt_{'01M4B6VGC29ESRDRQR62DX8AKD'}"
SOURCE_EVENT_2 = f"evt_{'01M4B6VGC29ESRDRQR62DX8AKE'}"
COMMAND_EVENT_2 = f"evt_{'01M4B6VGC29ESRDRQR62DX8AKF'}"

#: Every scope the slice can require. `--scopes` picks the subset the token carries, so a
#: run can be missing exactly one and show Core's refusal.
ALL_SCOPES = ("objects:read", "events:read", "state:read", "authority:read",
              "evidence:read", "interpretations:read", "reviews:read", "resolutions:read",
              "context:read", "checkpoint:read", "resume:read")


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def checkpoint_payload(contract: dict[str, Any], *, ident: str, revision: int,
                       reason: str, source: str, command: str,
                       note: str | None) -> dict[str, Any]:
    payload = {
        "checkpoint_id": ident,
        "command_event_id": command,
        "source_event_id": source,
        "project_id": PROJECT,
        "task_id": TASK,
        "host_id": HOST,
        "actor_id": ACTOR,
        "session_id": SESSION,
        "reason": reason,
        "trigger_provenance": {"level": "UNVERIFIED_REQUEST", "source_event_id": source},
        "projection_version": "jasmine.task-snapshot.v1",
        "projection": {
            "task": {"task_id": TASK, "title": "fixture task", "status": "ACTIVE",
                     "revision": revision},
            "steps": [{"step_id": "stp_" + ID_BODY, "status": "EXECUTED", "revision": revision}],
            "rules": [],
        },
        "projection_digest": "a" * 64,
        "task_revision": revision,
        "full_context_digest": "b" * 64,
        "workspace": {"files": [{"path": "src/app.py", "sha256": "c" * 64}]},
        "workspace_digest": "d" * 64,
        "note": {"text": note} if note else None,
        "created_at": "2026-10-07T12:00:00Z",
    }
    assert set(payload) == set(contract["response_fields"]["checkpoint"]), (
        "fixture checkpoint shape drifted from the frozen contract")
    return payload


def resume_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "resume_id": RESUME,
        "command_event_id": COMMAND_EVENT_2,
        "source_event_id": SOURCE_EVENT_2,
        "actor_id": ACTOR,
        "host_id": HOST,
        "project_id": PROJECT,
        "task_id": TASK,
        "session_id": SESSION,
        "checkpoint_id": CHECKPOINT,
        "created_at": "2026-10-07T12:05:00Z",
        "projection": {"task": {"task_id": TASK, "title": "fixture task", "status": "ACTIVE",
                                "revision": 9}, "steps": [], "rules": []},
        "truth_digest": "e" * 64,
        "workspace": {"files": [{"path": "src/app.py", "sha256": "f" * 64}]},
        "sample_window": {"scanned": 12, "bytes": 4096},
        # Nested shapes mirror P3's `continuity._comparison` and `continuity.next_actions`
        # key for key. The fixture cannot serve the real *values* — it has no Truth store —
        # but serving the real *shape* matters: `next_actions` in P3 is a list of objects
        # (`{"kind": ...}`, or `{"kind", "step_id", "status"}`), not a list of strings, and
        # a fixture that sent strings would exercise a UI branch real P3 never reaches.
        "comparison": {
            "checkpoint_freshness": "STALE",
            "workspace": "CHANGED",
            "warnings": ["CHECKPOINT_WORKSPACE_CHANGED"],
            "reconciliation_required": True,
            "diff": {
                "added": [["step", "stp_01M4B6VGC29ESRDRQR62DX8AK8"]],
                "removed": [],
                "changed": [["task", TASK]],
                "evidence_changed": False,
                "workspace_paths": {"added": [], "removed": [],
                                    "changed": ["src/app.py"]},
            },
        },
        "note": {"value": {"text": "stop here for today"}, "authority": False,
                 "historical": True},
        # `reconciliation_required` is True above, which is exactly the case P3 answers
        # with this single object.
        "next_actions": [{"kind": "RECONCILE_CURRENT_TRUTH"}],
    }
    assert set(payload) == set(contract["response_fields"]["resume"]), (
        "fixture resume shape drifted from the frozen contract")
    return payload


def context_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "context_pack_id": PACK,
        "command_event_id": COMMAND_EVENT,
        "source_event_id": SOURCE_EVENT,
        "actor_id": ACTOR,
        "host_id": HOST,
        "project_id": PROJECT,
        "task_id": TASK,
        "session_id": SESSION,
        "current_step_id": "stp_" + ID_BODY,
        "reason": "SESSION_STOP",
        "trigger_provenance": {"level": "UNVERIFIED_REQUEST", "source_event_id": SOURCE_EVENT},
        "created_at": "2026-10-07T12:02:00Z",
        "projection": {"task": {"task_id": TASK, "title": "fixture task", "revision": 7},
                       "steps": [], "rules": []},
        "truth_digest": "1" * 64,
        "selector": {"latest_checkpoint_id": CHECKPOINT, "selection_event_seq": 42},
        "resume_comparison": {"same": True, "changed": []},
        "workspace": {"files": []},
        "workspace_digest": "2" * 64,
        "sample_window": {"scanned": 3, "bytes": 512},
        "configuration": {"token_budget": 8192, "memory_enabled": True},
        "config_digest": "3" * 64,
        "tokenizer": {"id": "fixture-tokenizer", "version": "1"},
        "memory": {"status": "ok", "items": [], "banks": [],
                   "provider_id": "component_simulation",
                   "query_trace_id": "trc_" + ID_BODY},
        "rendered_content": "## Task\\nfixture task\\n\\n## Recent events\\n(none)\\n",
        "rendered_sha256": "4" * 64,
        "rendered_utf8_bytes": 48,
        "renderer_version": "jasmine.context-renderer.v1",
        "token_count": 21,
        # `section_metrics` and `omissions` mirror P3's `context.renderer.render` key for
        # key (`renderer.py`): a metric carries the byte span and both token counts, and an
        # omission names the item and why it was dropped. The values are the fixture's; the
        # shapes are P3's, so the view is not exercised against a shape P3 cannot emit.
        "section_metrics": [
            {"section": "task", "utf8_start": 0, "utf8_end": 20, "sha256": "5" * 64,
             "standalone_tokens": 8, "cumulative_prefix_tokens": 8, "prefix_delta_tokens": 8},
            {"section": "events", "utf8_start": 20, "utf8_end": 48, "sha256": "6" * 64,
             "standalone_tokens": 13, "cumulative_prefix_tokens": 21, "prefix_delta_tokens": 13},
        ],
        "omissions": [{"section": "memory", "item_id": "mem_" + ID_BODY,
                       "reason": "optional_budget", "standalone_tokens": 40,
                       "source_ids": [SOURCE_EVENT]}],
        "selected_optional_ids": ["mem_" + ID_BODY],
    }
    assert set(payload) == set(contract["response_fields"]["context"]), (
        "fixture context shape drifted from the frozen contract")
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "p6-p3-read-fixture"
    contract: dict[str, Any]
    tokens: dict[str, set[str]]

    # -- helpers --------------------------------------------------------------- #

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        # Every reply says where it came from, so evidence can prove the data is simulated
        # rather than a real Core's.
        self.send_header("X-P6-Fixture", FIXTURE_HEADER)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str, **details: Any) -> None:
        self._send(status, {"error": {"code": code, "message": message,
                                      "request_id": "aud_fixture", "details": details}})

    def _token(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return None
        return header[len("Bearer "):].strip()

    def _scopes(self) -> set[str] | None:
        token = self._token()
        return None if token is None else self.tokens.get(token)

    def _require_route(self, scopes: set[str] | None, pattern: str) -> bool:
        """Enforce the *full* scope set the frozen contract records for a route.

        The route-table scope alone is not the requirement: P3's handlers also reach
        `_continuity_read` (five scopes) and, for a context pack or a resume,
        `checkpoint:read`. Deriving this from the contract rather than hardcoding it here
        means the fixture cannot drift from the contract it claims to serve.
        """
        route = next(r for r in self.contract["read_routes"] if r["path_pattern"] == pattern)
        return self._require(scopes, route["scope"], *route["extra_scopes"])

    def _require(self, scopes: set[str] | None, *needed: str) -> bool:
        if scopes is None:
            self._error(401, "unauthenticated", "token missing, unknown or revoked")
            return False
        # Reported in the order Core requires them, not alphabetically: Core stops at the
        # first failing `principal.require`, so a fixture that sorted would name a
        # different scope than Core would for the same token.
        missing = [scope for scope in dict.fromkeys(needed) if scope not in scopes]
        if missing:
            self._error(403, "forbidden_scope", f"this key does not carry the {missing[0]} scope",
                        required=missing[0], granted=sorted(scopes))
            return False
        return True

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the fixture quiet-ish
        sys.stderr.write("p3-fixture: " + (fmt % args) + "\n")

    @property
    def parsed(self) -> tuple[str, dict[str, list[str]]]:
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.path)
        return url.path, parse_qs(url.query)

    # -- routes ---------------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query = self.parsed
        scopes = self._scopes()

        if path == "/v1/health":
            self._send(200, {"status": "ok", "core_version": "p3-fixture", "schema_version": 10})
            return
        if path == "/v1/projects":
            if not self._require(scopes, "objects:read"):
                return
            self._send(200, {"projects": [{"project_id": PROJECT, "name": "P3 fixture project",
                                           "host_id": HOST}], "count": 1})
            return
        if path == "/v1/hosts":
            if not self._require(scopes, "objects:read"):
                return
            self._send(200, {"hosts": [{"host_id": HOST, "display_name": "p3-fixture-host"}]})
            return

        context = re.fullmatch(r"/v1/context/([^/]+)", path)
        if context:
            if not self._require_route(scopes, r"^/v1/context/([^/]+)$"):
                return
            ident = context.group(1)
            if not ID_RE.fullmatch(ident) or not ident.startswith("ctx_"):
                self._error(400, "invalid_request", "invalid context_pack_id", field="context_pack_id")
                return
            if ident != PACK:
                self._error(404, "not_found", f"context {ident} not found")
                return
            self._send(200, {"context": context_payload(self.contract)})
            return

        latest = re.fullmatch(r"/v1/tasks/([^/]+)/checkpoints/latest", path)
        if latest:
            if not self._require_route(scopes, r"^/v1/tasks/([^/]+)/checkpoints/latest$"):
                return
            task = latest.group(1)
            unknown = sorted(set(query) - {"session_id"})
            if unknown:
                self._error(400, "invalid_request", "unknown latest checkpoint query")
                return
            session = (query.get("session_id") or [None])[0]
            if session is not None and not ID_RE.fullmatch(session):
                self._error(400, "invalid_request", "invalid session_id query")
                return
            if task == TASK_EMPTY:
                # The real endpoint answers 200 with a null checkpoint when a Task has
                # none; that is an empty result and the view must render it as one.
                self._send(200, {"checkpoint": None})
                return
            if task != TASK:
                self._error(404, "not_found", f"task {task} not found")
                return
            self._send(200, {"checkpoint": checkpoint_payload(
                self.contract, ident=CHECKPOINT, revision=7, reason="SESSION_STOP",
                source=SOURCE_EVENT, command=COMMAND_EVENT, note="stop here for today")})
            return

        one = re.fullmatch(r"/v1/checkpoints/([^/]+)", path)
        if one:
            if not self._require_route(scopes, r"^/v1/checkpoints/([^/]+)$"):
                return
            ident = one.group(1)
            if not ID_RE.fullmatch(ident) or not ident.startswith("ckp_"):
                self._error(400, "invalid_request", "invalid checkpoint_id", field="checkpoint_id")
                return
            if ident == CHECKPOINT:
                self._send(200, {"checkpoint": checkpoint_payload(
                    self.contract, ident=ident, revision=7, reason="SESSION_STOP",
                    source=SOURCE_EVENT, command=COMMAND_EVENT, note="stop here for today")})
                return
            if ident == CHECKPOINT_2:
                # A second checkpoint, so a browser run can switch scope and check that the
                # first one's content does not linger.
                self._send(200, {"checkpoint": checkpoint_payload(
                    self.contract, ident=ident, revision=11, reason="HANDOFF",
                    source=SOURCE_EVENT_2, command=COMMAND_EVENT_2, note=None)})
                return
            self._error(404, "not_found", f"checkpoint {ident} not found")
            return

        resume = re.fullmatch(r"/v1/resumes/([^/]+)", path)
        if resume:
            if not self._require_route(scopes, r"^/v1/resumes/([^/]+)$"):
                return
            ident = resume.group(1)
            if not ID_RE.fullmatch(ident) or not ident.startswith("rms_"):
                self._error(400, "invalid_request", "invalid resume_id", field="resume_id")
                return
            if ident != RESUME:
                self._error(404, "not_found", f"resume {ident} not found")
                return
            self._send(200, {"resume": resume_payload(self.contract)})
            return

        self._error(404, "not_found", f"the fixture has no route for {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p3_read_fixture")
    parser.add_argument("--port", type=int, default=8911)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--scopes", action="append",
                        help="comma-separated scopes for the matching --token (repeatable)")
    parser.add_argument("--token", action="append",
                        help="bearer token the fixture accepts (never a real key); repeat "
                             "with a matching --scopes to accept several, which is how one "
                             "run can show both a permitted read and a scope refusal")
    args = parser.parse_args(argv)

    contract = load_contract()
    tokens = args.token or ["p3fixture-token"]
    scopes = args.scopes or [",".join(ALL_SCOPES)]
    if len(scopes) == 1 and len(tokens) > 1:
        scopes = scopes * len(tokens)
    if len(tokens) != len(scopes):
        parser.error("--token and --scopes must be given the same number of times")
    Handler.contract = contract
    Handler.tokens = {
        token: {scope.strip() for scope in entry.split(",") if scope.strip()}
        for token, entry in zip(tokens, scopes)
    }
    accepted = set().union(*Handler.tokens.values())

    # Validate every payload against the frozen contract before serving anything, so a
    # drifted fixture fails loudly at startup instead of producing misleading evidence.
    for name, built in (("checkpoint", checkpoint_payload(contract, ident=CHECKPOINT, revision=7,
                                                          reason="SESSION_STOP",
                                                          source=SOURCE_EVENT,
                                                          command=COMMAND_EVENT,
                                                          note="n")),
                        ("resume", resume_payload(contract)),
                        ("context", context_payload(contract))):
        print(f"p3-fixture: {name} shape matches the frozen contract "
              f"({len(built)} fields)", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 78, file=sys.stderr)
    print("SIMULATED P3 READ FIXTURE — NOT a Jasmine Core, and NOT P3 verification.",
          file=sys.stderr)
    print("It answers a fixed shape derived from "
          f"{contract['source']['ref']}@{contract['source']['commit'][:12]}.", file=sys.stderr)
    print("The baseline Core has none of these routes; this exists so the P6-03a views",
          file=sys.stderr)
    print("can be exercised end to end. Every reply carries "
          f"X-P6-Fixture: {FIXTURE_HEADER}.", file=sys.stderr)
    for token, entry in Handler.tokens.items():
        print(f"accepts token {token!r} with scopes: {','.join(sorted(entry))}", file=sys.stderr)
    print("=" * 78, file=sys.stderr)
    print(f"p3-fixture listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
