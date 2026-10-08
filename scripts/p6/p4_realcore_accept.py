#!/usr/bin/env python3.13
"""P6-03 real-Core acceptance: drive the **isolated real P4 Core** and record what it does.

Read `web/REAL-CORE-ACCEPTANCE-PLAN.md` first: this is that plan's matrix, executed. It starts
`scripts/p6/p4_realcore_env.py` (real P4 Core at the pinned commit, real Truth, real jobs, P4's
`StubHindsightServer` as a **wire-double engine**), talks to it over loopback with keys P4 minted,
and writes one JSON entry per check so a reader can see the request, the status and the body.

Three rules it will not break, all borrowed from P4's own Gate runner:

* **A missing precondition is `BLOCKED`, never `PASS` and never `FAIL`**, with the reason named.
* **HTTP 200 is not success.** Success is the stored artifact: an Event read back, a revision
  advanced, a row's own state changed.
* **Nothing is fabricated.** Every row in the Core's database was produced by P4's own scan,
  worker and claim paths; nothing here inserts one.

Every entry records `mode: real-core+wire` — real P4 code, wire-double engine — because P4's own
Gate requires `mode: real` and a real deployment, which this is not. Nothing prints or stores a
token: the request log carries paths, bodies and statuses only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REF = "0c4f8f0e2118711ac9aa2a7006fddc6982962248"
MODE = "real-core+wire"
FREEZE = REPO_ROOT / "scripts" / "p6" / "freeze_bundle.py"


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def log(message: str) -> None:
    print(f"p4-realcore-accept: {message}", file=sys.stderr, flush=True)


class Core:
    """A thin HTTP client. It never records the Authorization header it sends."""

    def __init__(self, origin: str, tokens: dict[str, str]) -> None:
        self.origin = origin
        self.tokens = tokens
        self.calls: list[dict[str, Any]] = []

    def call(self, method: str, path: str, key: str | None = None,
             body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.origin + path, method=method, data=data)
        if key:
            request.add_header("Authorization", f"Bearer {self.tokens[key]}")
        if data:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, payload = response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"__raw": raw[:400]}
            status = error.code
        self.calls.append({"method": method, "path": path, "status": status,
                           "request_body": body, "response_body": payload,
                           "actor_key": key})
        return status, payload


def key_sets(contract: dict[str, Any]) -> dict[str, list[str]]:
    return {route["response_key"]: route["response_fields"] for route in contract["read_routes"]}


def event_id(seq: int) -> str:
    """A well-formed `evt_` id: the prefix plus 26 Crockford digits.

    The first version built this from a fixed 23-character body plus four digits and then cut it to
    30 characters, which silently dropped the only varying digit: every request carried the *same*
    `event_id`, so the run's own replay key made four later checks answer `event_id_conflict` — the
    harness blaming the Core for a harness bug.
    """
    return "evt_" + f"{seq:026d}"


class Checks:
    def __init__(self, core: Core, descriptor: dict[str, Any], read_contract: dict[str, Any],
                 write_contract: dict[str, Any]) -> None:
        self.core = core
        self.d = descriptor
        self.read_contract = read_contract
        self.write_contract = write_contract
        self.entries: list[dict[str, Any]] = []
        self.seq = 0

    # -- helpers -------------------------------------------------------------------------------
    def record(self, ident: str, check: str, ok: bool | None, *, status: int | None = None,
               detail: Any = None, note: str = "", mode: str = MODE) -> None:
        entry = {"id": ident, "check": check, "mode": mode, "ok": ok, "status": status,
                 "detail": detail, "note": note}
        self.entries.append(entry)
        mark = "BLOCKED" if ok is None else ("ok  " if ok else "FAIL")
        log(f"{mark} {ident} {check}")

    def next_event(self) -> str:
        self.seq += 1
        return event_id(self.seq)

    def route(self, suffix: str) -> dict[str, Any]:
        # The frozen patterns are anchored (`.../pin$`), so compare without the anchor.
        return next(w for w in self.write_contract["writes"]
                    if w["path_pattern"].rstrip("$").endswith(suffix))

    def shape_of(self, body: Any) -> list[str]:
        return sorted(body) if isinstance(body, dict) else []

    # -- reads ---------------------------------------------------------------------------------
    def reads(self, item: str, job: str, trace: str | None) -> None:
        expected = key_sets(self.read_contract)
        paths = {
            "health": ("health", "/v1/memory/health"),
            "jobs": ("jobs", "/v1/memory/jobs"),
            "job": ("job", f"/v1/memory/jobs/{job}"),
            "items": ("items", "/v1/memory/items"),
            "item": ("item", f"/v1/memory/items/{item}"),
            "traces": ("traces", "/v1/memory/traces"),
        }
        if trace:
            paths["trace"] = ("trace", f"/v1/memory/traces/{trace}")
        for name, (contract_key, path) in paths.items():
            status, body = self.core.call("GET", path, "human_manage")
            want = sorted(expected.get(contract_key, []))
            got = self.shape_of(body)
            self.record(f"R1.{name}", f"{path} answers real P4 with the frozen contract's keys",
                        status == 200 and got == want, status=status,
                        detail={"expected": want, "got": got},
                        note="contract: web/contracts/p4-memory-read.v1.json at the same SHA")
        if trace is None:
            self.record("R1.trace", "trace detail read", None,
                        note="no trace existed before the reads; reflect ran earlier in the run")

    def not_found(self, unknown_item: str, unknown_job: str) -> None:
        status, body = self.core.call("GET", f"/v1/memory/items/{unknown_item}", "human_manage")
        self.record("R3.unknown_item", "an unknown item is Core's own memory_not_found",
                    status == 404 and body.get("error", {}).get("code") == "memory_not_found",
                    status=status, detail=body.get("error"))
        status, body = self.core.call("GET", f"/v1/memory/jobs/{unknown_job}", "human_manage")
        self.record("R3.unknown_job", "an unknown job is Core's own memory_job_not_found",
                    status == 404 and body.get("error", {}).get("code") == "memory_job_not_found",
                    status=status, detail=body.get("error"))
        status, body = self.core.call("GET", "/v1/memory/items/not-an-id", "human_manage")
        self.record("R3.malformed_item", "a malformed item id is refused by Core as invalid_request",
                    status == 400 and body.get("error", {}).get("code") == "invalid_request",
                    status=status, detail=body.get("error"))

    def permissions(self, item: str) -> None:
        status, body = self.core.call("GET", "/v1/memory/items", "human_readonly")
        self.record("R2.read_scope", "a key with memory:read reads (real scope check passes it)",
                    status == 200, status=status)
        event = self.next_event()
        status, body = self.core.call("POST", f"/v1/memory/items/{item}/pin", "human_readonly",
                                      {"expected_revision": 1, "host_id": self.d["host_id"],
                                       "event_id": event})
        error = body.get("error", {}) if isinstance(body, dict) else {}
        self.record("W2.no_manage_scope",
                    "a key without memory:manage is refused by Core, naming the scope",
                    status == 403 and error.get("code") == "forbidden_scope" and
                    error.get("details", {}).get("required") == "memory:manage",
                    status=status, detail=error)
        refusals = self.d["issuance_refusals"]
        self.record("W2.issuance_agent_manage",
                    "Core refuses to ISSUE an agent key carrying memory:manage / memory:reflect",
                    len(refusals) == 2 and all(not r["issued"] and
                                               r["code"] == "forbidden_actor_kind" for r in refusals),
                    status=403, detail=refusals,
                    note="issuance, not a request — the fixture's artificial agent key cannot exist "
                         "on real P4")

    def redaction(self, failed_item: str, failed_job: str) -> None:
        for ident, path, row in (("R4.item", f"/v1/memory/items/{failed_item}", "memory"),
                                 ("R4.job", f"/v1/memory/jobs/{failed_job}", "job")):
            status_h, human = self.core.call("GET", path, "human_manage")
            status_a, agent = self.core.call("GET", path, "agent_read")
            human_row = human.get(row, {}) if isinstance(human, dict) else {}
            agent_row = agent.get(row, {}) if isinstance(agent, dict) else {}
            human_text = human_row.get("safe_error_summary")
            agent_has_key = isinstance(agent_row, dict) and "safe_error_summary" in agent_row
            agent_text = agent_row.get("safe_error_summary")
            self.record(ident,
                        "the withholding rule on a row that really carries engine text: "
                        "the human sees it, the agent has the key with null",
                        status_h == 200 and status_a == 200 and isinstance(human_text, str)
                        and human_text != "" and agent_has_key and agent_text is None,
                        status=status_h, 
                        detail={"human_text": human_text, "agent_has_key": agent_has_key,
                                "agent_text": agent_text})

    # -- writes --------------------------------------------------------------------------------
    def lifecycle(self, command: str, item: str, revision: int, reason: str | None = None,
                  ident: str | None = None) -> tuple[int, Any, str]:
        event = self.next_event()
        body: dict[str, Any] = {"expected_revision": revision, "host_id": self.d["host_id"],
                                "event_id": event}
        if reason is not None:
            body["reason"] = reason
        status, payload = self.core.call("POST", f"/v1/memory/items/{item}/{command}",
                                         "human_manage", body)
        route = self.route(f"/{command}")
        fresh = route["response_shapes"][0]
        self.record(ident or f"W1.{command}",
                    f"{command} is accepted by real P4 with the frozen response shape",
                    status == 200 and self.shape_of(payload) == sorted(fresh),
                    status=status, detail={"sent": sorted(body), "got": self.shape_of(payload),
                                           "expected": sorted(fresh)})
        return status, payload, event

    def writes(self, item: str, other: str, third: str, failed_job: str, done_job: str,
               running_job: str, pending_job: str, project: str) -> None:
        # W1: payload accepted, and the seven-key fresh transition.
        status, pin, pin_event = self.lifecycle("pin", item, 1, "acceptance: pin")

        # W5: the command Event read back by id, and D2: it is a real Event row.
        if status == 200:
            event_id_value = pin["event"]["event_id"]
            status_e, stored = self.core.call("GET", f"/v1/events/{event_id_value}", "human_manage")
            self.record("W5.pin_event",
                        "the command Event the write returned is readable by id on the audit surface",
                        status_e == 200 and stored.get("event", {}).get("event_id") == event_id_value,
                        status=status_e, detail={"event_type": stored.get("event", {}).get("event_type")})
            # D2: the nested event in the write reply must be the same shape the audit surface serves.
            listing = self.core.call("GET", "/v1/events?limit=1", "human_manage")[1]
            sample = (listing.get("events") or [{}])[0]
            self.record("D2.nested_event_shape",
                        "the nested command event is a real Event row, not a fixture invention",
                        self.shape_of(pin["event"]) == self.shape_of(sample),
                        detail={"write_reply": self.shape_of(pin["event"]),
                                "audit_surface": self.shape_of(sample)})
            self.record("W6.revision_advanced",
                        "the write moved the object's own revision (before -> after in the reply)",
                        pin.get("before", {}).get("revision") == 1 and
                        pin.get("after", {}).get("revision") == 2,
                        detail={"before": pin.get("before", {}).get("revision"),
                                "after": pin.get("after", {}).get("revision")})
            status_d, detail = self.core.call("GET", f"/v1/memory/items/{item}", "human_manage")
            row = detail.get("memory", {})
            self.record("W6.detail_readback",
                        "the item detail re-read shows the new revision and the pin",
                        status_d == 200 and row.get("revision") == 2 and row.get("pinned") is True,
                        status=status_d, detail={"revision": row.get("revision"),
                                                 "pinned": row.get("pinned")})
            # W4a: replay of the same command.
            event = self.next_event()
            same = {"expected_revision": 1, "host_id": self.d["host_id"], "event_id": pin_event,
                    "reason": "acceptance: pin"}
            status_r, replay = self.core.call("POST", f"/v1/memory/items/{item}/pin",
                                              "human_manage", same)
            route = self.route("/pin")
            self.record("W4.replay",
                        "the same event_id and body replay: five keys, replayed true, no new Event",
                        status_r == 200 and replay.get("replayed") is True and
                        self.shape_of(replay) == sorted(route["response_shapes"][1]) and
                        replay.get("event", {}).get("event_id") == pin_event,
                        status=status_r, detail={"keys": self.shape_of(replay),
                                                 "replayed": replay.get("replayed")})
            # D1: the same event_id with a CHANGED command is a conflict, not a replay.
            changed = dict(same, reason="acceptance: a different command")
            status_c, conflict = self.core.call("POST", f"/v1/memory/items/{item}/pin",
                                               "human_manage", changed)
            error = conflict.get("error", {}) if isinstance(conflict, dict) else {}
            self.record("D1.replay_identity",
                        "P4 hashes the command payload: the same event_id with a changed field is "
                        "event_id_conflict, where the fixture replayed",
                        status_c == 409 and error.get("code") == "event_id_conflict",
                        status=status_c, detail={"code": error.get("code"),
                                                 "details": error.get("details")})
        else:
            self.record("W5.pin_event", "pin event readback", False, note="the pin did not succeed")

        # W4b: a genuinely stale revision.
        status, body = self.core.call("POST", f"/v1/memory/items/{item}/unpin", "human_manage",
                                      {"expected_revision": 1, "host_id": self.d["host_id"],
                                       "event_id": self.next_event()})
        error = body.get("error", {}) if isinstance(body, dict) else {}
        self.record("W4.revision_conflict",
                    "a stale expected_revision is revision_conflict, with Core's current revision",
                    status == 409 and error.get("code") == "revision_conflict" and
                    error.get("details", {}).get("current_revision") == 2,
                    status=status, detail=error.get("details"))

        # W1/D3/D4: obsolete queues a real job and the list reflects the mutation.
        status, obsolete, _ = self.lifecycle("obsolete", other, 1, "acceptance: obsolete",
                                             ident="W1.obsolete")
        if status == 200:
            job = obsolete.get("job")
            self.record("D3.lifecycle_job",
                        "a lifecycle command returns a REAL queued job, where the fixture returned null",
                        isinstance(job, dict) and str(job.get("job_id", "")).startswith("mjb_"),
                        detail={"job_id": (job or {}).get("job_id"), "kind": (job or {}).get("kind")})
            status_j, job_read = self.core.call("GET", f"/v1/memory/jobs/{job['job_id']}",
                                                "human_manage")
            self.record("D3.job_readback", "the queued job is readable by id",
                        status_j == 200 and job_read.get("job", {}).get("job_id") == job["job_id"],
                        status=status_j)
            status_l, listing = self.core.call("GET", "/v1/memory/items", "human_manage")
            row = next((r for r in listing.get("items", []) if r["memory_id"] == other), {})
            self.record("D4.list_consistency",
                        "the items LIST reflects the mutation (the fixture's list did not)",
                        status_l == 200 and row.get("status") == "OBSOLETE" and
                        row.get("revision") == 2,
                        status=status_l, detail={"status": row.get("status"),
                                                 "revision": row.get("revision")})

        # W1: delete, on a third item.
        self.lifecycle("delete", third, 1, "acceptance: delete", ident="W1.delete")

        # W9: reflect — eight keys, a real trace, and the trace read back.
        status, reflect = self.core.call("POST", "/v1/memory/reflect", "human_manage",
                                         {"project_id": project, "query": "what did we learn?",
                                          "event_id": self.next_event()})
        route = self.route("/reflect")
        self.record("W9.reflect",
                    "reflect answers the frozen eight keys, marked non-authoritative by Core",
                    status == 200 and self.shape_of(reflect) == sorted(route["response_shapes"][0])
                    and reflect.get("non_authoritative") is True,
                    status=status, detail={"keys": self.shape_of(reflect),
                                           "trace_id": reflect.get("trace_id"),
                                           "status_field": reflect.get("status")})
        trace_id_value = reflect.get("trace_id") if isinstance(reflect, dict) else None
        if trace_id_value:
            status_t, trace = self.core.call("GET", f"/v1/memory/traces/{trace_id_value}",
                                             "human_manage")
            expected = sorted(set(self.read_contract["nested_fields"]["trace"]) |
                              set(self.read_contract["nested_fields"]["trace_detail"]))
            self.record("W5.reflect_trace",
                        "reflect's audit record is a real trace, readable by id",
                        status_t == 200 and set(trace.get("trace", {})) >= set(expected),
                        status=status_t, detail={"trace_id": trace_id_value,
                                                 "extra": sorted(set(trace.get("trace", {})) - set(expected))})
        return trace_id_value

    def retry(self, failed_job: str, done_job: str, running_job: str, pending_job: str) -> None:
        status, before = self.core.call("GET", f"/v1/memory/jobs/{failed_job}", "human_manage")
        original = before.get("job", {})
        status, body = self.core.call("POST", f"/v1/memory/jobs/{failed_job}/retry", "human_manage",
                                      {"host_id": self.d["host_id"],
                                       "event_id": self.next_event()})
        route = self.route("/retry")
        job = body.get("job", {}) if isinstance(body, dict) else {}
        self.record("W7.retry_failed",
                    "a real FAILED job is retried: the budget resets, the document is untouched",
                    status == 200 and self.shape_of(body) == sorted(route["response_shapes"][0]) and
                    job.get("status") == "PENDING" and job.get("retry_count") == 0 and
                    job.get("document_id") == original.get("document_id") and
                    job.get("content_sha256") == original.get("content_sha256") and
                    job.get("bank_id") == original.get("bank_id"),
                    status=status, detail={"status": job.get("status"),
                                           "retry_count": job.get("retry_count"),
                                           "document_unchanged":
                                               job.get("document_id") == original.get("document_id")})
        for ident, job_id, state in (("W7.retry_done", done_job, "DONE"),
                                     ("W7.retry_running", running_job, "RUNNING"),
                                     ("W7.retry_pending", pending_job, "PENDING")):
            status, body = self.core.call("POST", f"/v1/memory/jobs/{job_id}/retry", "human_manage",
                                          {"host_id": self.d["host_id"],
                                           "event_id": self.next_event()})
            error = body.get("error", {}) if isinstance(body, dict) else {}
            self.record(ident, f"a {state} job is refused with invalid_state_transition",
                        status == 409 and error.get("code") == "invalid_state_transition",
                        status=status, detail=error)

    def no_rule_or_state_change(self, project: str, task: str, step: str) -> None:
        before_rules = self.core.call("GET", f"/v1/rules/active?project_id={project}",
                                      "human_manage")[1]
        before_task = self.core.call("GET", f"/v1/tasks/{task}", "human_manage")[1]
        before_step = self.core.call("GET", f"/v1/steps/{step}", "human_manage")[1]
        self._snapshot = {"rules": len(before_rules.get("rules", []) or []),
                          "task_revision": before_task.get("task", {}).get("revision"),
                          "step_status": before_step.get("step", {}).get("status")}

    def assert_no_rule_or_state_change(self, project: str, task: str, step: str) -> None:
        after_rules = self.core.call("GET", f"/v1/rules/active?project_id={project}",
                                     "human_manage")[1]
        after_task = self.core.call("GET", f"/v1/tasks/{task}", "human_manage")[1]
        after_step = self.core.call("GET", f"/v1/steps/{step}", "human_manage")[1]
        after = {"rules": len(after_rules.get("rules", []) or []),
                 "task_revision": after_task.get("task", {}).get("revision"),
                 "step_status": after_step.get("step", {}).get("status")}
        self.record("W10.no_rule_or_state_change",
                    "no memory write changed Rule or State",
                    after == getattr(self, "_snapshot", None),
                    detail={"before": getattr(self, "_snapshot", None), "after": after})


def write_readme(out: Path, summary: dict[str, Any], entries: list[dict[str, Any]],
                 descriptor: dict[str, Any]) -> None:
    """The bundle README, written from the run's own evidence — never from memory."""
    lines = [
        "# P6-03 real-Core acceptance — seven reads and six writes against real P4",
        "",
        f"Core from the pinned commit `{summary['ref']}` (schema {summary['schema_version']}), built",
        "with `git archive` into an isolated tree with its own database, workspace and ports.",
        f"**{summary['passed']}/{summary['checks']}** checks passed, {summary['failed']} failed,",
        f"{summary['blocked']} blocked. `mode: {MODE}` — real Core code, **wire-double engine**.",
        "",
        "## What this proves, and what it does not",
        "",
        "* It proves, against real P4 code: the seven reads' frozen key sets; `forbidden_scope` and",
        "  the real **issuance** rule that refuses an agent key carrying `memory:manage` or",
        "  `memory:reflect`; the withholding rule on a row that really carries engine text; all six",
        "  writes' payloads and response shapes; `event_id` replay; `revision_conflict` with Core's",
        "  current revision; the command Event read back by id; `reflect`'s eight keys and its real",
        "  trace; the retry invariants on real `FAILED`/`DONE`/`RUNNING`/`PENDING` jobs; and that no",
        "  memory write changed Rule or State.",
        "* It settles the fixture's admitted divergences: P4 hashes the command payload (the same",
        "  `event_id` with a changed field is `event_id_conflict`, where the fixture replayed); a",
        "  lifecycle reply carries a real queued job, where the fixture returned `null`; and the read",
        "  **list** reflects mutations, which the fixture's list did not.",
        "* It does **NOT** prove anything about a real Hindsight deployment: the engine is P4's own",
        "  `StubHindsightServer`, a wire double on loopback. P4's own Gate requires `mode: real` and a",
        "  real deployment, so **P4 phase acceptance is not claimed** and engine-dependent facts",
        "  (extraction quality, reflect content) remain unverified.",
        "* It is not a source audit. It exercises behaviour; it cannot show the absence of defects.",
        "",
        "## Checks",
        "",
        "| # | Check | Mode | Result |",
        "| --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(entries, start=1):
        mark = "BLOCKED" if entry["ok"] is None else ("PASS" if entry["ok"] else "**FAIL**")
        lines.append(f"| {index} | {entry['check']} | `{entry['mode']}` | {mark} |")
    if any(entry["ok"] is None for entry in entries):
        lines += ["", "### Blocked, with the reason", ""]
        for entry in entries:
            if entry["ok"] is None:
                lines.append(f"* `{entry['id']}` — {entry['note'] or '(no reason recorded)'}")
    lines += [
        "",
        "## Files",
        "",
        "* `evidence.json` — every check with its request, status and response body, and every HTTP",
        "  call the harness made (paths, bodies, statuses; **no Authorization header is recorded**).",
        "* `descriptor.json` — the isolated Core's ids, schema version, mode labels and the jobs/items",
        "  it produced, with the key material removed.",
        "* `env.stderr.txt` — the environment's own banner and worker log.",
        "* `README.md` — this file, generated from `evidence.json`.",
        "",
        "## Reproduce",
        "",
        "```",
        "python3.13 scripts/p6/p4_realcore_accept.py --out <dir> --freeze",
        "```",
        "",
        f"Engine label: `{descriptor['mode']['engine']}` — {descriptor['mode']['engine_note']}",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def assert_no_token_in_bundle(out: Path, tokens: dict[str, str]) -> None:
    """Refuse to freeze a bundle that contains a key this run minted, and say which file."""
    for name, token in tokens.items():
        found = subprocess.run(["grep", "-rlF", "--", token, str(out)],
                               capture_output=True, text=True).stdout.split()
        if found:
            raise SystemExit(f"REFUSING TO FREEZE: token {name} appears in {found}")


def freeze(out: Path, ref: str, checks: int) -> None:
    commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    dependency = json.dumps({
        "name": "P4 memory slice", "status": "NOT MERGED", "source_ref": ref,
        "acceptance": "P4 phase acceptance NOT passed",
        "consequence": "the Core is real P4 code; the engine is a wire double, so engine-dependent "
                       "facts are unverified and this is not a Gate verdict",
    })
    subprocess.run([sys.executable, str(FREEZE), "--freeze", str(out), "--commit", commit,
                    "--kind", "real-core acceptance (isolated real P4 Core, wire-double engine)",
                    "--runs", f"one pass, {checks} checks",
                    "--dependency-json", dependency], check=True)
    subprocess.run([sys.executable, str(FREEZE), "--verify", str(out)], check=True)


def run(args: argparse.Namespace) -> int:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    # The environment lives OUTSIDE the bundle. An earlier version defaulted it to `<out>/env`, and
    # the freeze then swallowed the extracted P4 tree (306 files) and — worse — the environment's
    # `tokens.json`. The bundle holds evidence only; the run's keys stay in the temp tree.
    env_dir = Path(args.env_dir) if args.env_dir else Path(
        tempfile.mkdtemp(prefix="p6-realcore-env-"))
    if out.resolve() in env_dir.resolve().parents or env_dir.resolve() == out.resolve():
        raise SystemExit("--env-dir must not be inside --out: the bundle holds evidence only")
    if env_dir.exists() and not args.reuse_env:
        subprocess.run(["rm", "-rf", str(env_dir)], check=True)
    env_dir.mkdir(parents=True, exist_ok=True)
    core_port = args.port or free_port()

    log(f"starting the isolated environment at {env_dir} (Core on {core_port})")
    env_log = (out / "env.stderr.txt").open("w")
    env = subprocess.Popen([sys.executable, str(REPO_ROOT / "scripts" / "p6" / "p4_realcore_env.py"),
                            "build", "--out", str(env_dir), "--ref", args.ref,
                            "--port", str(core_port), "--max-ticks", "8"]
                           + (["--repo", str(args.repo)] if args.repo else []),
                           stdout=subprocess.DEVNULL, stderr=env_log)
    origin = f"http://127.0.0.1:{core_port}"
    try:
        deadline = time.time() + 120
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{origin}/v1/health", timeout=2) as response:
                    if response.status == 200:
                        ready = True
                        break
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.3)
        if not ready:
            log("the environment never answered /v1/health")
            return 1

        descriptor = json.loads((env_dir / "descriptor.json").read_text())
        tokens = json.loads((env_dir / "tokens.json").read_text())
        read_contract = json.loads((REPO_ROOT / "web" / "contracts" /
                                    "p4-memory-read.v1.json").read_text())
        write_contract = json.loads((REPO_ROOT / "web" / "contracts" /
                                     "p4-memory-write.v1.json").read_text())
        core = Core(origin, tokens)
        checks = Checks(core, descriptor, read_contract, write_contract)

        items = descriptor["items"]
        jobs = {j["status"]: j["job_id"] for j in descriptor["jobs"]}
        failed_item = next(i["memory_id"] for i in items if i["safe_error_summary"])
        healthy = [i["memory_id"] for i in items if not i["safe_error_summary"]]
        project = descriptor["seeded"]["project_id"]
        task = descriptor["seeded"]["task_id"]
        step = descriptor["seeded"]["step_id"]

        core.call("GET", f"/v1/health")  # a warm-up read, not evidence
        checks.no_rule_or_state_change(project, task, step)
        checks.permissions(healthy[0])
        trace_id_value = None
        if len(healthy) >= 3:
            trace_id_value = checks.writes(healthy[0], healthy[1], healthy[2],
                                           jobs.get("FAILED", ""), jobs.get("DONE", ""),
                                           jobs.get("RUNNING", ""), jobs.get("PENDING", ""), project)
        else:
            checks.record("W1", "the write matrix", None,
                          note="the environment did not hold enough healthy items; see the descriptor")
        checks.retry(jobs.get("FAILED", ""), jobs.get("DONE", ""), jobs.get("RUNNING", ""),
                     jobs.get("PENDING", ""))
        checks.redaction(failed_item, jobs.get("FAILED", ""))
        checks.reads(healthy[0], jobs.get("FAILED", ""), trace_id_value)
        checks.not_found("mem_" + "0" * 26, "mjb_" + "0" * 26)
        checks.assert_no_rule_or_state_change(project, task, step)

        blocked = [e for e in checks.entries if e["ok"] is None]
        failed = [e for e in checks.entries if e["ok"] is False]
        summary = {
            "environment": "isolated real P4 Core",
            "ref": args.ref,
            "schema_version": descriptor["schema_version"],
            "mode": descriptor["mode"],
            "checks": len(checks.entries),
            "passed": len(checks.entries) - len(blocked) - len(failed),
            "failed": len(failed),
            "blocked": len(blocked),
            "evidence_file": "evidence.json",
            "requests_recorded": len(core.calls),
        }
        (out / "evidence.json").write_text(json.dumps(
            {"summary": summary, "entries": checks.entries, "calls": core.calls},
            indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "descriptor.json").write_text(json.dumps(
            {k: v for k, v in descriptor.items() if k != "keys"}, indent=2), encoding="utf-8")
        write_readme(out, summary, checks.entries, descriptor)
        shutil.copyfile(REPO_ROOT / "web" / "REAL-CORE-ACCEPTANCE-PLAN.md", out / "plan.md")
        assert_no_token_in_bundle(out, tokens)
        log(f"{summary['passed']}/{summary['checks']} passed, {summary['failed']} failed, "
            f"{summary['blocked']} blocked")
        if not failed and args.freeze and not args.no_freeze:
            freeze(out, args.ref, summary["checks"])
        return 1 if failed else 0
    finally:
        env.terminate()
        try:
            env.wait(timeout=10)
        except subprocess.TimeoutExpired:
            env.kill()
        env_log.close()
        if not args.keep_env and args.env_dir is None:
            # The environment held the run's keys; it does not outlive the run unless asked.
            shutil.rmtree(env_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="the evidence directory")
    parser.add_argument("--ref", default=DEFAULT_REF, help="the pinned P4 commit")
    parser.add_argument("--port", type=int, help="loopback port for the isolated Core")
    parser.add_argument("--env-dir", type=Path, help="reuse an environment directory")
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository to extract --ref from (default: this worktree)")
    parser.add_argument("--no-freeze", action="store_true",
                        help="do not write BUNDLE-FROZEN.json (a sub-run of another bundle)")
    parser.add_argument("--reuse-env", action="store_true", help="do not wipe --env-dir")
    parser.add_argument("--freeze", action="store_true",
                        help="write BUNDLE-FROZEN.json when every check passed")
    parser.add_argument("--keep-env", action="store_true",
                        help="leave the temporary environment directory in place")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
