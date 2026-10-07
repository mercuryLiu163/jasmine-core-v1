#!/usr/bin/env python3.13
"""P6-02 milestone B, T03 Gate: audited human modification, item by item.

The read slice is proved by `p6_verify.py` and `browser_trace.mjs`. This gate
proves the *writes*, and it deliberately goes through the **dashboard proxy**
(`:5183`), not straight to Core, so the evidence covers the path the browser
actually uses. Every request is sent with the real human key the fixture issues
and the body shape the UI builds — the fields asserted here are the ones
`web/tests/writeBodies.test.tsx` pins from the other side.

For each of the seven shipped actions it records the frozen tuple the handoff
asks for:

    method · path · required scope · actor kind · host · source Event ·
    idempotency (replay key) · expected/observed revision · Core's answer ·
    the command Event · object readback · audit rows

and then proves the negatives that matter more than the happy path:

* a read-only token is refused (`forbidden_scope`), and the refusal changes
  nothing;
* an agent actor is refused where Core demands a human;
* a stale `expected_revision` is refused (`409`) and leaves the object alone;
* the **same** replay key with the **same** body is a replay, not a second
  command (no duplicate Rule / no second revision);
* the same replay key with a **different** body is `event_id_conflict`;
* `rerun` and `reject` leave Truth (`objects`, `state_events`) unchanged.

Audit readback is a direct read of the fixture's `audit_log`. That is an
operator-side harness read, **not** a browser capability: the dashboard proxy
has no `/v1/audit` path at all, and no fixture token carries `admin`.

Everything is reported as data, including failures. A step that cannot run is
reported `BLOCKED` with the reason rather than being skipped silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"

#: Durable state the Task board renders: a command that is not an application
#: must leave this untouched.
TRUTH_TABLES = ("projects", "tasks", "steps")
#: What applying a plan actually writes, read back to prove approve is not a no-op.
APPLICATION_TABLES = ("resolutions", "source_applications", "source_application_changes")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Client:
    """Minimal JSON client that never raises: a Core refusal is data."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.log: list[dict[str, Any]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                token: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {token or self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if request_id:
            headers["X-Request-Id"] = request_id
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode() or "{}")
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload = {"raw": raw[:400]}
            status = error.code
        except Exception as error:  # noqa: BLE001 - a transport failure is a result
            payload = {"error": {"code": "transport_error", "message": str(error)}}
            status = None
        entry = {"method": method, "path": path, "status": status,
                 "request_id": request_id, "body_keys": sorted(body) if body else [],
                 "error_code": (payload.get("error") or {}).get("code") if isinstance(payload, dict) else None}
        self.log.append(entry)
        return {"status": status, "payload": payload, "sent_keys": sorted(body) if body else []}


def new_key(tag: str) -> str:
    return f"t03-{tag}-{uuid.uuid4().hex[:10]}"


def new_event_id() -> str:
    """A client-minted `evt_` id in Core's exact shape, as the UI mints one."""
    crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    import secrets

    return "evt_" + "".join(crockford[b & 0x1F] for b in secrets.token_bytes(26))


# --------------------------------------------------------------------------- #
# Evidence collection
# --------------------------------------------------------------------------- #

class Gate:
    def __init__(self, descriptor: dict[str, Any], tokens: dict[str, str], db_path: Path,
                 proxy: str, core: str) -> None:
        self.d = descriptor
        self.tokens = tokens
        self.db_path = db_path
        self.human = Client(proxy, tokens["p2_human"])
        self.reader = Client(proxy, tokens["p2_read"])
        self.agent = Client(proxy, tokens["agent"])
        self.core = Client(core, tokens["p2_human"])
        self.results: list[dict[str, Any]] = []
        self.audit_before = self.audit_count()

    # -- audit / truth readbacks (operator-side DB reads) -------------------- #

    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def audit_count(self) -> int:
        with self._ro() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"])

    def audit_rows_for(self, path: str, *, method: str = "POST",
                       actor_id: str | None = None) -> list[dict[str, Any]]:
        """Audit rows the gate's own request produced, newest last."""
        with self._ro() as conn:
            rows = conn.execute(
                "SELECT seq, at, request_id, actor_id, method, path, decision, status_code, "
                "scope, target_id, error_code FROM audit_log WHERE method=? AND path=? "
                "ORDER BY seq",
                (method, path),
            ).fetchall()
            out = [dict(row) for row in rows]
        if actor_id is not None:
            out = [row for row in out if row["actor_id"] == actor_id]
        return out

    def _digest(self, tables: tuple[str, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        with self._ro() as conn:
            for table in tables:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                payload = "\n".join(
                    json.dumps(dict(row), sort_keys=True, default=str) for row in rows
                )
                out[table] = {"rows": len(rows),
                              "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
        return out

    def truth_fingerprint(self) -> dict[str, Any]:
        """The durable, human-visible state a command must not silently change.

        `projects`/`tasks`/`steps` are what the Task board renders — the objects a
        person treats as the system's state. `resolutions`/`source_applications`
        are deliberately excluded: approving a Review is *supposed* to bind a
        plan to its source Event, which `application_fingerprint` measures.
        """
        return self._digest(TRUTH_TABLES)

    def application_fingerprint(self) -> dict[str, Any]:
        """What an approval applies: the plan binding and the applied result."""
        return self._digest(APPLICATION_TABLES)

    # -- reporting ----------------------------------------------------------- #

    def record(self, action: str, verdict: str, frozen: dict[str, Any],
               checks: list[dict[str, Any]], note: str = "") -> None:
        self.results.append({"action": action, "verdict": verdict, "frozen": frozen,
                             "checks": checks, "note": note})

    def get(self, path: str, token: str | None = None) -> Any:
        return self.human.request("GET", path, token=token)["payload"]

    def head(self, interpretation_id: str) -> dict[str, Any]:
        body = self.get(f"/v1/interpretations/{interpretation_id}")
        return body["interpretation"]["maintenance_head"]


# --------------------------------------------------------------------------- #
# The seven actions
# --------------------------------------------------------------------------- #

def _check(label: str, ok: bool, evidence: Any) -> dict[str, Any]:
    return {"check": label, "ok": bool(ok), "evidence": evidence}


def action_interpretation_correct(g: Gate) -> None:
    """`POST /v1/interpretations/{int}/correct` — human, revisions, audit."""
    root = g.d["p2"]["applied_interpretation_id"]
    head = g.head(root)
    target = head["current_interpretation_id"]
    revision = head["revision"]
    detail = g.get(f"/v1/interpretations/{target}")["interpretation"]
    source = detail["event_id"]
    # The panel's draft is the interpretation's own raw_result_json, pretty
    # printed (`InterpretationView.prettyJson(item.raw_result_json)`), so the
    # gate sends that same real document rather than inventing one. Core's
    # output schema requires 1..16 candidates, so an empty list is a 400.
    corrected = json.loads(detail["raw_result_json"])
    key = new_key("correct")
    request_id = f"t03-correct-{uuid.uuid4().hex[:8]}"
    result = g.human.request("POST", f"/v1/interpretations/{target}/correct", {
        "idempotency_key": key,
        "host_id": g.d["host_id"],
        "reason": "T03 gate: correct with a real human key",
        "expected_revision": revision,
        "result": corrected,
    }, request_id=request_id)
    after = g.head(root)
    body = result["payload"]
    child = ((body.get("result") or {}).get("child_interpretation_id")
             if isinstance(body, dict) else None)
    checks = [
        _check("HTTP 201 from the proxy path", result["status"] == 201, result["status"]),
        _check("body keys are Core's allow-list",
               result["sent_keys"] == ["expected_revision", "host_id", "idempotency_key", "reason", "result"],
               result["sent_keys"]),
        _check("the corrected result is the interpretation's own raw_result_json",
               isinstance(corrected, dict) and len(corrected.get("candidates") or []) >= 1,
               {"candidates": len(corrected.get("candidates") or []),
                "keys": sorted(corrected)[:8]}),
        _check("head revision advanced exactly one step", after["revision"] == revision + 1,
               {"before": revision, "after": after["revision"]}),
        _check("a child interpretation was created", bool(child), child),
        _check("command is visible as an interpretation change", _change_row(g, target, "CORRECT"),
               _change_row(g, target, "CORRECT")),
        _check("audit recorded the attempt with the human actor",
               any(row["actor_id"] == g.d["actors"]["human"] and row["status_code"] == 201
                   for row in g.audit_rows_for(f"/v1/interpretations/{target}/correct")),
               [row for row in g.audit_rows_for(f"/v1/interpretations/{target}/correct")][-2:]),
        _check("audit carries our X-Request-Id",
               any(row["request_id"] == request_id
                   for row in g.audit_rows_for(f"/v1/interpretations/{target}/correct")),
               request_id),
    ]
    _finish(g, "interpretation.correct", {
        "method": "POST", "path": f"/v1/interpretations/{target}/correct",
        "scope": "interpretations:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": source,
        "replay_key": {"field": "idempotency_key", "value_prefix": key[:12]},
        "expected_revision": revision, "observed_revision": after["revision"],
        "status": result["status"], "child_interpretation_id": child,
    }, checks)


def action_interpretation_reject(g: Gate) -> None:
    """`reject` must not touch Truth, and must advance the head."""
    root = g.d["p2"]["applied_interpretation_id"]
    head = g.head(root)
    target = head["current_interpretation_id"]
    revision = head["revision"]
    truth_before = g.truth_fingerprint()
    key = new_key("reject")
    result = g.human.request("POST", f"/v1/interpretations/{target}/reject", {
        "idempotency_key": key,
        "host_id": g.d["host_id"],
        "reason": "T03 gate: reject leaves Truth untouched",
        "expected_revision": revision,
    })
    after = g.head(root)
    truth_after = g.truth_fingerprint()
    checks = [
        _check("HTTP 201", result["status"] == 201, result["status"]),
        _check("body keys are Core's allow-list (no result, no event_id)",
               result["sent_keys"] == ["expected_revision", "host_id", "idempotency_key", "reason"],
               result["sent_keys"]),
        _check("head revision advanced exactly one step", after["revision"] == revision + 1,
               {"before": revision, "after": after["revision"]}),
        _check("Truth row counts unchanged", truth_before == truth_after,
               {"before": truth_before, "after": truth_after}),
        _check("REJECT recorded in interpretation_changes", _change_row(g, target, "REJECT"),
               _change_row(g, target, "REJECT")),
    ]
    _finish(g, "interpretation.reject", {
        "method": "POST", "path": f"/v1/interpretations/{target}/reject",
        "scope": "interpretations:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": None,
        "replay_key": {"field": "idempotency_key", "value_prefix": key[:12]},
        "expected_revision": revision, "observed_revision": after["revision"],
        "status": result["status"], "truth_unchanged": truth_before == truth_after,
    }, checks)


def action_interpretation_rerun(g: Gate) -> None:
    """`rerun` needs `interpretations:process` on top of `:manage`."""
    root = g.d["p2"]["applied_interpretation_id"]
    head = g.head(root)
    target = head["current_interpretation_id"]
    revision = head["revision"]
    truth_before = g.truth_fingerprint()
    key = new_key("rerun")
    result = g.human.request("POST", f"/v1/interpretations/{target}/rerun", {
        "idempotency_key": key,
        "host_id": g.d["host_id"],
        "reason": "T03 gate: rerun through the fixture provider",
        "expected_revision": revision,
    })
    after = g.head(root)
    truth_after = g.truth_fingerprint()
    body = result["payload"]
    child = ((body.get("result") or {}).get("child_interpretation_id")
             if isinstance(body, dict) else None)
    checks = [
        _check("HTTP 201", result["status"] == 201, result["status"]),
        _check("a child interpretation was created", bool(child), child),
        _check("head moved on", after["revision"] > revision,
               {"before": revision, "after": after["revision"]}),
        _check("RERUN recorded", _change_row(g, target, "RERUN"), _change_row(g, target, "RERUN")),
        _check("Truth row counts unchanged", truth_before == truth_after,
               {"before": truth_before, "after": truth_after}),
    ]
    note = ""
    if result["status"] == 500 or (isinstance(body, dict)
                                   and (body.get("error") or {}).get("code") in
                                   {"provider_unavailable", "provider_error"}):
        note = ("Core's HTTP rerun depends on a live provider; the fixture serves "
                "component_simulation, so this path is only provable against the fixture "
                "provider, not a real model.")
    _finish(g, "interpretation.rerun", {
        "method": "POST", "path": f"/v1/interpretations/{target}/rerun",
        "scope": "interpretations:manage + interpretations:process",
        "actor": g.d["actors"]["human"], "actor_kind": "human", "host_id": g.d["host_id"],
        "source_event_id": None,
        "replay_key": {"field": "idempotency_key", "value_prefix": key[:12]},
        "expected_revision": revision, "observed_revision": after["revision"],
        "status": result["status"], "child_interpretation_id": child,
        "truth_unchanged": truth_before == truth_after, "provider": g.d["provider"],
    }, checks, note)


def _review_actions(g: Gate, review_id: str) -> list[dict[str, Any]] | None:
    """Read the review and its server plan; the UI sends exactly these actions."""
    review = g.get(f"/v1/reviews/{review_id}")["review"]
    preview = review.get("preview") or {}
    items = ((preview.get("plan") or {}).get("items")) or []
    if not items:
        return None
    return [{"candidate_index": item["candidate_index"], "action": item["action"], "payload": {}}
            for item in items]


def action_review_approve(g: Gate) -> None:
    """Approve replays the **server** plan; the browser never invents one."""
    review_id = g.d["p2"]["pending_review_id"]
    review = g.get(f"/v1/reviews/{review_id}")["review"]
    actions = _review_actions(g, review_id)
    if actions is None:
        _finish(g, "review.approve", {
            "method": "POST", "path": f"/v1/reviews/{review_id}/approve",
            "scope": "reviews:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        }, [_check("Core serves a preview plan to approve", False, review.get("preview"))],
            "no preview plan: the UI refuses to open Approve in this state (see ReviewView)")
        return
    key = new_key("approve")
    application_before = g.application_fingerprint()
    result = g.human.request("POST", f"/v1/reviews/{review_id}/approve", {
        "idempotency_key": key,
        "host_id": g.d["host_id"],
        "reason": "T03 gate: approve the server plan",
        "expected_revision": review["revision"],
        "expected_revisions": preview_of(review)["expected_revisions"],
        "expected_context_digest": preview_of(review)["expected_context_digest"],
        "actions": actions,
    })
    after = g.get(f"/v1/reviews/{review_id}")["review"]
    application_after = g.application_fingerprint()
    applied = isinstance(result["payload"], dict) and (result["payload"].get("result") or {}).get("disposition")
    checks = [
        _check("HTTP 200/201 from the proxy path", result["status"] in (200, 201), result["status"]),
        _check("Core reports the plan's disposition", applied in {"APPLIED", "PENDING_REVIEW", "SOURCE_REPLAYED"},
               applied),
        _check("the approval actually bound a plan to its source Event",
               application_after != application_before,
               {"before": application_before, "after": application_after}),
        _check("Core returned a plan mutation/outcome", isinstance(result["payload"], dict),
               sorted(result["payload"])[:8] if isinstance(result["payload"], dict) else None),
        _check("review left PENDING", after["status"] != "PENDING",
               {"before": review["status"], "after": after["status"]}),
        _check("review revision advanced", after["revision"] > review["revision"],
               {"before": review["revision"], "after": after["revision"]}),
        _check("actions were the server plan, one per candidate",
               all(a["candidate_index"] == item["candidate_index"]
                   for a, item in zip(actions, preview_of(review)["plan"]["items"])),
               actions),
        _check("audit recorded the human approve",
               any(row["actor_id"] == g.d["actors"]["human"]
                   for row in g.audit_rows_for(f"/v1/reviews/{review_id}/approve")),
               g.audit_rows_for(f"/v1/reviews/{review_id}/approve")[-1:]),
    ]
    _finish(g, "review.approve", {
        "method": "POST", "path": f"/v1/reviews/{review_id}/approve",
        "scope": "reviews:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"],
        "source_event_id": preview_of(review).get("source_event_id"),
        "replay_key": {"field": "idempotency_key", "value_prefix": key[:12]},
        "expected_revision": review["revision"], "observed_revision": after["revision"],
        "status": result["status"], "resolution_id": review.get("resolution_id"),
        "plan_digest": preview_of(review).get("expected_context_digest"),
    }, checks)


def action_review_reject(g: Gate) -> None:
    review_id = g.d["p2"]["reject_review_id"]
    review = g.get(f"/v1/reviews/{review_id}")["review"]
    truth_before = g.truth_fingerprint()
    key = new_key("review-reject")
    result = g.human.request("POST", f"/v1/reviews/{review_id}/reject", {
        "idempotency_key": key,
        "host_id": g.d["host_id"],
        "reason": "T03 gate: reject the plan",
        "expected_revision": review["revision"],
    })
    after = g.get(f"/v1/reviews/{review_id}")["review"]
    truth_after = g.truth_fingerprint()
    checks = [
        _check("HTTP 200/201", result["status"] in (200, 201), result["status"]),
        _check("review is REJECTED", after["status"] == "REJECTED",
               {"before": review["status"], "after": after["status"]}),
        _check("review revision advanced", after["revision"] > review["revision"],
               {"before": review["revision"], "after": after["revision"]}),
        _check("Truth row counts unchanged", truth_before == truth_after,
               {"before": truth_before, "after": truth_after}),
    ]
    _finish(g, "review.reject", {
        "method": "POST", "path": f"/v1/reviews/{review_id}/reject",
        "scope": "reviews:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": None,
        "replay_key": {"field": "idempotency_key", "value_prefix": key[:12]},
        "expected_revision": review["revision"], "observed_revision": after["revision"],
        "status": result["status"], "truth_unchanged": truth_before == truth_after,
    }, checks)


def action_rule_propose(g: Gate) -> None:
    """Creation: no `reason`, no `idempotency_key`, no `expected_revision`."""
    project = g.d["p2"]["project_id"]
    task = g.d["p2"]["task_id"]
    origin = g.d["p2"]["event_ids"]["pending"]
    rule_key = f"t03.gate.propose.{uuid.uuid4().hex[:8]}"
    event_id = new_event_id()
    body = {
        "rule_key": rule_key,
        "kind": "RULE",
        "severity": "NORMAL",
        "enforcement": "CONTEXT",
        "content": "T03 gate proposal.",
        "matcher": {},
        "scope": {"kind": "task", "project_id": project, "task_id": task},
        "origin_event_id": origin,
        "host_id": g.d["host_id"],
        "event_id": event_id,
    }
    result = g.human.request("POST", "/v1/rules/proposals", body)
    payload = result["payload"]
    rule = payload.get("rule") or {}
    checks = [
        _check("HTTP 201", result["status"] == 201, result["status"]),
        _check("sent keys are exactly creation's allow-list (no reason/idempotency_key)",
               result["sent_keys"] == sorted(
                   {"rule_key", "kind", "severity", "enforcement", "content", "matcher",
                    "scope", "origin_event_id", "host_id", "event_id"}),
               result["sent_keys"]),
        _check("no expected_revision was sent (creation rejects it)",
               "expected_revision" not in result["sent_keys"], result["sent_keys"]),
        _check("new Rule is PROPOSED", rule.get("status") == "PROPOSED", rule.get("status")),
        _check("replayed is false on a first submission", payload.get("replayed") is False,
               payload.get("replayed")),
        _check("proposal Event is the client's event_id",
               (payload.get("event") or {}).get("event_id") == event_id,
               {"sent": event_id, "returned": (payload.get("event") or {}).get("event_id")}),
        _check("origin Event is the one sent",
               (rule.get("origin_event_id")) == origin, rule.get("origin_event_id")),
        _check("audit recorded the human proposal",
               any(row["actor_id"] == g.d["actors"]["human"]
                   for row in g.audit_rows_for("/v1/rules/proposals")),
               g.audit_rows_for("/v1/rules/proposals")[-1:]),
    ]
    _finish(g, "rule.propose", {
        "method": "POST", "path": "/v1/rules/proposals",
        "scope": "authority:propose", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": origin,
        "replay_key": {"field": "event_id", "value": event_id},
        "expected_revision": None, "observed_revision": rule.get("revision"),
        "status": result["status"], "rule_id": rule.get("rule_id"),
    }, checks)


def action_rule_approve(g: Gate) -> None:
    rule_id = g.d["seeded"]["rules"]["proposed"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    event_id = new_event_id()
    result = g.human.request("POST", f"/v1/rules/{rule_id}/approve", {
        "expected_revision": rule["revision"], "host_id": g.d["host_id"], "event_id": event_id,
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    payload = result["payload"]
    checks = [
        _check("HTTP 200", result["status"] == 200, result["status"]),
        _check("sent keys are exactly approve's allow-list",
               result["sent_keys"] == ["event_id", "expected_revision", "host_id"],
               result["sent_keys"]),
        _check("Rule is now ACTIVE", after["status"] == "ACTIVE",
               {"before": rule["status"], "after": after["status"]}),
        _check("revision advanced by one", after["revision"] == rule["revision"] + 1,
               {"before": rule["revision"], "after": after["revision"]}),
        _check("version unchanged by approve", after["version"] == rule["version"],
               {"before": rule["version"], "after": after["version"]}),
        _check("replayed is false", payload.get("replayed") is False, payload.get("replayed")),
    ]
    _finish(g, "rule.approve", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/approve",
        "scope": "authority:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": rule.get("origin_event_id"),
        "replay_key": {"field": "event_id", "value": event_id},
        "expected_revision": rule["revision"], "observed_revision": after["revision"],
        "status": result["status"], "rule_id": rule_id,
    }, checks)


def action_rule_supersede(g: Gate) -> None:
    rule_id = g.d["seeded"]["rules"]["active"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    # Same scope as the Rule: Core refuses cross-scope supersede, and the UI
    # locks the field to the Rule's own scope for exactly that reason.
    # A project-scope Rule needs a project-scope origin Event: `_origin` compares
    # the Event's scope with the Rule's, so the task-scoped prompt Event that the
    # propose path uses is refused here.
    origin = g.d["p2"]["event_ids"]["project_scope"]
    event_id = new_event_id()
    result = g.human.request("POST", f"/v1/rules/{rule_id}/supersede", {
        "expected_revision": rule["revision"],
        "host_id": g.d["host_id"],
        "scope": rule["scope"],
        "origin_event_id": origin,
        "kind": rule["kind"],
        "severity": rule["severity"],
        "enforcement": rule["enforcement"],
        "content": f"{rule['content']} (T03 gate supersede)",
        "matcher": rule["matcher"],
        "event_id": event_id,
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    checks = [
        _check("HTTP 200", result["status"] == 200, result["status"]),
        _check("scope sent is the Rule's own scope",
               result["sent_keys"].count("scope") == 1 and rule["scope"]["kind"] == "project",
               rule["scope"]),
        _check("version advanced (supersede keeps the same Rule id)",
               after["version"] == rule["version"] + 1,
               {"before": rule["version"], "after": after["version"]}),
        _check("revision advanced by one", after["revision"] == rule["revision"] + 1,
               {"before": rule["revision"], "after": after["revision"]}),
        _check("Rule is still ACTIVE", after["status"] == "ACTIVE", after["status"]),
        _check("new content is the sent content", "T03 gate supersede" in after["content"],
               after["content"]),
        _check("history now has the superseded snapshot",
               any(entry["rule"]["status"] == "SUPERSEDED"
                   for entry in g.get(f"/v1/rules/{rule_id}/history")["history"]),
               [entry["rule"]["status"] for entry in
                g.get(f"/v1/rules/{rule_id}/history")["history"]]),
    ]
    _finish(g, "rule.supersede", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/supersede",
        "scope": "authority:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": origin,
        "replay_key": {"field": "event_id", "value": event_id},
        "expected_revision": rule["revision"], "observed_revision": after["revision"],
        "status": result["status"], "rule_id": rule_id,
        "version_before": rule["version"], "version_after": after["version"],
    }, checks)


def action_rule_retire(g: Gate) -> None:
    rule_id = g.d["p2"]["retire_rule_id"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    event_id = new_event_id()
    result = g.human.request("POST", f"/v1/rules/{rule_id}/retire", {
        "expected_revision": rule["revision"], "host_id": g.d["host_id"], "event_id": event_id,
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    checks = [
        _check("HTTP 200", result["status"] == 200, result["status"]),
        _check("sent keys are exactly retire's allow-list",
               result["sent_keys"] == ["event_id", "expected_revision", "host_id"],
               result["sent_keys"]),
        _check("Rule is RETIRED", after["status"] == "RETIRED",
               {"before": rule["status"], "after": after["status"]}),
        _check("revision advanced by one", after["revision"] == rule["revision"] + 1,
               {"before": rule["revision"], "after": after["revision"]}),
        _check("retired Rule is no longer listed as ACTIVE for its scope",
               rule_id not in [r["rule_id"] for r in
                               g.get(f"/v1/rules/active?project_id={g.d['p2']['project_id']}")
                               ["rules"]],
               rule_id),
    ]
    _finish(g, "rule.retire", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/retire",
        "scope": "authority:manage", "actor": g.d["actors"]["human"], "actor_kind": "human",
        "host_id": g.d["host_id"], "source_event_id": rule.get("origin_event_id"),
        "replay_key": {"field": "event_id", "value": event_id},
        "expected_revision": rule["revision"], "observed_revision": after["revision"],
        "status": result["status"], "rule_id": rule_id,
    }, checks)


# --------------------------------------------------------------------------- #
# Negatives
# --------------------------------------------------------------------------- #

def negative_read_token(g: Gate) -> None:
    """A read-only token must be refused, and the refusal must change nothing."""
    rule_id = g.d["seeded"]["rules"]["active"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    truth_before = g.truth_fingerprint()
    result = g.reader.request("POST", f"/v1/rules/{rule_id}/retire", {
        "expected_revision": rule["revision"], "host_id": g.d["host_id"],
        "event_id": new_event_id(),
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    checks = [
        _check("403 forbidden_scope",
               result["status"] == 403 and result["payload"]["error"]["code"] == "forbidden_scope",
               {"status": result["status"], "code": result["payload"].get("error")}),
        _check("Rule state and revision unchanged",
               after["status"] == rule["status"] and after["revision"] == rule["revision"],
               {"before": rule["revision"], "after": after["revision"]}),
        _check("Truth unchanged", truth_before == g.truth_fingerprint(), truth_before),
        _check("the refusal itself is audited",
               any(row["status_code"] == 403 and row["error_code"] == "forbidden_scope"
                   for row in g.audit_rows_for(f"/v1/rules/{rule_id}/retire")),
               g.audit_rows_for(f"/v1/rules/{rule_id}/retire")[-1:]),
    ]
    _finish(g, "negative: read-only token", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/retire", "token": "p2_read (read slice only)",
        "expected": "403 forbidden_scope, no state change",
    }, checks)


def negative_agent_actor(g: Gate) -> None:
    """Core demands a human/system actor to manage Authority."""
    rule_id = g.d["p2"]["retire_rule_id"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    result = g.agent.request("POST", f"/v1/rules/{rule_id}/approve", {
        "expected_revision": rule["revision"], "host_id": g.d["host_id"],
        "event_id": new_event_id(),
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    code = (result["payload"].get("error") or {}).get("code") if isinstance(result["payload"], dict) else None
    checks = [
        _check("refused, not applied", result["status"] in (403, 401), result["status"]),
        _check("refusal names the actor-kind rule or a missing scope",
               code in {"forbidden_actor_kind", "forbidden_scope"}, code),
        _check("Rule unchanged", after["revision"] == rule["revision"],
               {"before": rule["revision"], "after": after["revision"]}),
    ]
    _finish(g, "negative: agent actor", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/approve", "token": "agent",
        "expected": "refused (actor kind / scope), no state change",
    }, checks)


def negative_stale_revision(g: Gate) -> None:
    """A stale basis is refused and the object is left alone."""
    rule_id = g.d["p2"]["retire_rule_id"]
    rule = g.get(f"/v1/rules/{rule_id}")["rule"]
    stale = max(1, rule["revision"] - 1)
    result = g.human.request("POST", f"/v1/rules/{rule_id}/retire", {
        "expected_revision": stale, "host_id": g.d["host_id"], "event_id": new_event_id(),
    })
    after = g.get(f"/v1/rules/{rule_id}")["rule"]
    code = (result["payload"].get("error") or {}).get("code") if isinstance(result["payload"], dict) else None
    checks = [
        _check("409 revision_conflict", result["status"] == 409 and code == "revision_conflict",
               {"status": result["status"], "code": code}),
        _check("Core returned the current revision for re-review",
               "current" in json.dumps(result["payload"]) or "current_revision" in json.dumps(result["payload"]),
               sorted(result["payload"]) if isinstance(result["payload"], dict) else None),
        _check("nothing changed", after["revision"] == rule["revision"]
               and after["status"] == rule["status"],
               {"before": rule["revision"], "after": after["revision"]}),
    ]
    _finish(g, "negative: stale revision", {
        "method": "POST", "path": f"/v1/rules/{rule_id}/retire",
        "expected": "409 revision_conflict, draft kept, no change",
        "expected_revision_sent": stale, "actual_revision": rule["revision"],
    }, checks)


def negative_replay(g: Gate) -> None:
    """Same key + same body = replay. Same key + different body = conflict."""
    project = g.d["p2"]["project_id"]
    task = g.d["p2"]["task_id"]
    origin = g.d["p2"]["event_ids"]["pending"]
    rule_key = f"t03.gate.replay.{uuid.uuid4().hex[:8]}"
    event_id = new_event_id()
    body = {
        "rule_key": rule_key, "kind": "RULE", "severity": "NORMAL", "enforcement": "CONTEXT",
        "content": "T03 gate replay probe.", "matcher": {},
        "scope": {"kind": "task", "project_id": project, "task_id": task},
        "origin_event_id": origin, "host_id": g.d["host_id"], "event_id": event_id,
    }
    with g._ro() as conn:
        rules_before = int(conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()["n"])
    first = g.human.request("POST", "/v1/rules/proposals", body)
    replay = g.human.request("POST", "/v1/rules/proposals", body)
    changed = dict(body)
    changed["content"] = "T03 gate replay probe, DIFFERENT body."
    conflict = g.human.request("POST", "/v1/rules/proposals", changed)
    with g._ro() as conn:
        rules_after = int(conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()["n"])
    replay_payload = replay["payload"] if isinstance(replay["payload"], dict) else {}
    conflict_code = ((conflict["payload"].get("error") or {}).get("code")
                     if isinstance(conflict["payload"], dict) else None)
    checks = [
        _check("first submission created the Rule", first["status"] == 201, first["status"]),
        _check("replay returns 200 with replayed=true",
               replay["status"] == 200 and replay_payload.get("replayed") is True,
               {"status": replay["status"], "replayed": replay_payload.get("replayed")}),
        _check("replay returns the SAME rule_id",
               (replay_payload.get("rule") or {}).get("rule_id")
               == (first["payload"].get("rule") or {}).get("rule_id"),
               {"first": (first["payload"].get("rule") or {}).get("rule_id"),
                "replay": (replay_payload.get("rule") or {}).get("rule_id")}),
        _check("no second Rule row was created", rules_after == rules_before + 1,
               {"before": rules_before, "after": rules_after}),
        _check("same key + different body is event_id_conflict",
               conflict["status"] == 409 and conflict_code == "event_id_conflict",
               {"status": conflict["status"], "code": conflict_code}),
    ]
    _finish(g, "negative: replay and conflict", {
        "method": "POST", "path": "/v1/rules/proposals",
        "expected": "replay is replayed=true with no new row; a different body under the same key is refused",
        "replay_key": {"field": "event_id", "value": event_id},
    }, checks)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def preview_of(review: dict[str, Any]) -> dict[str, Any]:
    return review.get("preview") or {}


def _change_row(g: Gate, interpretation_id: str, action: str) -> list[str]:
    with g._ro() as conn:
        rows = conn.execute(
            "SELECT action FROM interpretation_changes WHERE root_interpretation_id=? "
            "AND action=? ORDER BY rowid DESC LIMIT 1",
            (g.d["p2"]["applied_interpretation_id"], action),
        ).fetchall()
        return [row["action"] for row in rows]


def _finish(g: Gate, action: str, frozen: dict[str, Any], checks: list[dict[str, Any]],
            note: str = "") -> None:
    verdict = PASS if checks and all(check["ok"] for check in checks) else FAIL
    g.record(action, verdict, frozen, checks, note)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add("# P6-02 milestone B — T03 Gate (audited human modification)")
    add("")
    add(f"- fixture: `{report['fixture']}`")
    add(f"- proxy under test: `{report['proxy']}` (the browser path)")
    add(f"- core: `{report['core']}`")
    add(f"- actor: `{report['actor']}` (human) · host `{report['host_id']}`")
    add(f"- human key scopes ({len(report['human_scopes'])}): {', '.join(report['human_scopes'])}")
    add(f"- provider: {report['provider']}")
    add(f"- audit rows appended during the gate: {report['audit_delta']}")
    add(f"- overall: **{report['overall']}**")
    add("")
    add("## Per-action evidence")
    add("")
    for result in report["results"]:
        icon = {"PASS": "PASS", "FAIL": "FAIL", "BLOCKED": "BLOCKED"}[result["verdict"]]
        add(f"### {result['action']} — {icon}")
        add("")
        for key, value in result["frozen"].items():
            add(f"- `{key}`: `{json.dumps(value, ensure_ascii=False)}`")
        if result.get("note"):
            add(f"- _note_: {result['note']}")
        add("")
        for check in result["checks"]:
            mark = "x" if check["ok"] else " "
            add(f"- [{mark}] {check['check']} — `{json.dumps(check['evidence'], ensure_ascii=False)[:220]}`")
        add("")
    add("## What this does not show")
    add("")
    for item in report["limits"]:
        add(f"- {item}")
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p6_t03_gate")
    parser.add_argument("--out", required=True, help="fixture directory (holds core.db)")
    parser.add_argument("--proxy", default="http://127.0.0.1:5183")
    parser.add_argument("--core", default="http://127.0.0.1:8795")
    parser.add_argument("--report", required=True, help="JSON report path")
    parser.add_argument("--markdown", help="markdown report path")
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    descriptor = json.loads((out / "descriptor.json").read_text(encoding="utf-8"))
    tokens = json.loads((out / "tokens.json").read_text(encoding="utf-8"))
    for key in ("p2_human", "p2_read", "agent"):
        if key not in tokens:
            print(f"missing token {key!r} in {out / 'tokens.json'}", file=sys.stderr)
            return 2

    g = Gate(descriptor, tokens, out / "core.db", args.proxy, args.core)

    steps = [
        action_interpretation_correct,
        action_interpretation_reject,
        action_interpretation_rerun,
        action_review_approve,
        action_review_reject,
        action_rule_propose,
        action_rule_approve,
        action_rule_supersede,
        action_rule_retire,
        negative_read_token,
        negative_agent_actor,
        negative_stale_revision,
        negative_replay,
    ]
    for step in steps:
        try:
            step(g)
        except Exception as error:  # noqa: BLE001 - a broken step is reported, never hidden
            g.record(step.__name__, BLOCKED, {}, [_check("step ran to completion", False, repr(error))],
                     "the harness itself raised; treat this action as unproven")

    verdicts = [result["verdict"] for result in g.results]
    overall = PASS if verdicts and all(v == PASS for v in verdicts) else (
        BLOCKED if BLOCKED in verdicts else FAIL)

    report = {
        "fixture": str(out),
        "proxy": args.proxy,
        "core": args.core,
        "actor": descriptor["actors"]["human"],
        "host_id": descriptor["host_id"],
        "human_scopes": descriptor.get("human_write_token_scopes", []),
        "provider": descriptor.get("provider"),
        "audit_delta": g.audit_count() - g.audit_before,
        "overall": overall,
        "results": g.results,
        "requests": g.human.log,
        "limits": [
            "The audit readback is a direct read-only SQLite read of the fixture's "
            "`audit_log`. No fixture token carries `admin`, and the dashboard proxy has no "
            "`/v1/audit` path, so the browser cannot read audit at all — by design.",
            "Every write here goes through the dashboard proxy with a real HTTP request; the "
            "UI is separately proven to build these exact bodies by "
            "`web/tests/writeBodies.test.tsx`.",
            "`interpretation.rerun` runs the fixture's `component_simulation` provider. It "
            "proves the audited command path, not a real model call.",
            "Truth is compared by row counts plus the resolved objects read back through the "
            "API; this gate does not attempt a full semantic diff of every table.",
        ],
    }
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = render(report)
    if args.markdown:
        Path(args.markdown).write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0 if overall == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
