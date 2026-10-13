"""Core-side handling of Host Hook Protocol events (ADR 0017).

Agent-neutral. Each event reuses an existing Core capability (Raw Event store,
Guard, tool-result Evidence, Context builder, Checkpoints). Failures of the
derived pieces (Context, Checkpoint, fingerprint refresh, Evidence when the
workspace is not configured) never undo the Raw Event: they are reported in
`degraded`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import clock, db, errors
from ..canonical import canonical_json, sha256_hex
from ..events import host_hook_scope
from ..models import NewEvent
from .protocol import SOURCE_SYSTEM, HostHookRequest, empty_response, parse_request

#: Guard decisions ordered from strictest to weakest.
_GUARD_ORDER = ("deny", "confirm", "verify", "allow")
_GUARD_TO_DECISION = {"deny": "deny", "confirm": "ask", "verify": "allow", "allow": "allow"}
_NOT_FOUND = {"host": "hosts", "project": "projects", "task": "tasks", "step": "steps"}


def _event_id(*parts: str) -> str:
    from ..evidence import _deterministic_event_id

    return _deterministic_event_id(SOURCE_SYSTEM, *parts)


def _key(delivery_id: str, reason: str) -> str:
    return "hh-" + hashlib.sha256(f"{delivery_id}\0{reason}".encode()).hexdigest()[:40]


@dataclass
class _Result:
    decision: str = "none"
    reason: str | None = None
    inject: dict[str, str] | None = None
    event_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    checkpoint_id: str | None = None
    degraded: list[str] = field(default_factory=list)
    guard: dict[str, Any] | None = None


class HostHookService:
    def __init__(self, core: Any) -> None:
        self.core = core
        self.conn: sqlite3.Connection = core.conn

    # -- entry -----------------------------------------------------------------

    def handle(self, body: Any, principal: Any) -> tuple[int, dict[str, Any]]:
        req = parse_request(body)
        actor = self.core.registry.require_actor(principal.actor_id)
        if actor["kind"] not in ("human", "system"):
            raise errors.ForbiddenActorKind("host hook reports require a human or system actor")
        if actor["home_host_id"] != req.host_id:
            raise errors.ActorMismatch("actor must be bound to the stated host", host_id=req.host_id)
        digest = sha256_hex(req.raw)
        stored = self._receipt(req.delivery_id)
        if stored is not None:
            return self._replay(stored, digest, req)
        project_id = self._check_binding(req)
        with host_hook_scope():
            response = self._dispatch(req, actor, project_id)
        try:
            with db.translate_lock_errors(), db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO host_hook_deliveries (delivery_id, request_sha256, event, agent_name,"
                    " host_id, actor_id, trust, response_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (req.delivery_id, digest, req.event, req.agent["name"], req.host_id,
                     actor["actor_id"], req.trust, canonical_json(response), clock.now_rfc3339()))
        except sqlite3.IntegrityError:
            # A concurrent identical delivery stored its receipt first.
            stored = self._receipt(req.delivery_id)
            if stored is None:
                raise
            return self._replay(stored, digest, req)
        return 201, {**response, "replayed": False}

    def _receipt(self, delivery_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM host_hook_deliveries WHERE delivery_id=?",
                                 (delivery_id,)).fetchone()

    @staticmethod
    def _replay(stored: sqlite3.Row, digest: str, req: HostHookRequest) -> tuple[int, dict[str, Any]]:
        if stored["request_sha256"] != digest:
            raise errors.HostHookDeliveryConflict(
                "delivery_id already exists with different content", delivery_id=req.delivery_id,
                existing_request_sha256=stored["request_sha256"], request_sha256=digest)
        return 200, {**json.loads(stored["response_json"]), "replayed": True}

    def _check_binding(self, req: HostHookRequest) -> str | None:
        """Resolve stated references; never guess ownership (ADR 0002)."""
        def exists(table: str, column: str, value: str, kind: str) -> sqlite3.Row:
            row = self.conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (value,)).fetchone()
            if row is None:
                raise errors.NotFound(kind, value)
            return row

        exists("hosts", "host_id", req.host_id, "host")
        project_id = req.project_id
        if project_id:
            exists("projects", "project_id", project_id, "project")
        if req.task_id:
            task = exists("tasks", "task_id", req.task_id, "task")
            if project_id and task["project_id"] != project_id:
                raise errors.InvalidRequest("task does not belong to the stated project", field="binding.task_id")
            project_id = task["project_id"]
        if req.step_id:
            step = exists("steps", "step_id", req.step_id, "step")
            if step["task_id"] != req.task_id:
                raise errors.InvalidRequest("step does not belong to the stated task", field="binding.step_id")
        return project_id

    def _dispatch(self, req: HostHookRequest, actor: dict[str, Any], project_id: str | None) -> dict[str, Any]:
        result = _Result()
        handler = getattr(self, "_on_" + req.event.replace(".", "_"))
        handler(req, actor, project_id, result)
        if req.task_id is None and "no_task_binding" not in result.degraded:
            result.degraded.insert(0, "no_task_binding")
        response = empty_response(req)
        response.update(decision=result.decision, reason=result.reason, inject_context=result.inject,
                        degraded=result.degraded)
        response["recorded"] = {"event_ids": result.event_ids, "evidence_ids": result.evidence_ids,
                                "checkpoint_id": result.checkpoint_id}
        if result.guard is not None:
            response["guard"] = result.guard
        return response

    # -- shared pieces ---------------------------------------------------------

    def _common(self, req: HostHookRequest) -> dict[str, Any]:
        return {"agent": req.agent, "adapter": req.adapter, "native": req.native, "trust": req.trust,
                "delivery_id": req.delivery_id, "binding_step_id": req.step_id}

    def _append(self, req: HostHookRequest, actor: dict[str, Any], project_id: str | None,
                event_type: str, role: str, extra: dict[str, Any], *, text: str = "") -> dict[str, Any]:
        spec = NewEvent(
            event_type=event_type, source_system=SOURCE_SYSTEM,
            source_event_id=req.delivery_id + (f"#{role}" if role else ""),
            occurred_at=req.occurred_at or clock.now(), actor_id=actor["actor_id"], actor_kind=actor["kind"],
            host_id=req.host_id, project_id=project_id, task_id=req.task_id,
            payload={"text": text, **self._common(req), **extra},
            event_id=_event_id(req.delivery_id, role), client_occurred_at=req.occurred_at)
        with db.translate_lock_errors(), db.transaction(self.conn):
            event, _ = self.core.objects.events.append(spec)
        return event

    def _context(self, req: HostHookRequest, actor: dict[str, Any], source_event_id: str,
                 reason: str, res: _Result) -> None:
        if req.task_id is None:
            return
        body = {"task_id": req.task_id, "host_id": req.host_id, "source_event_id": source_event_id,
                "idempotency_key": _key(req.delivery_id, reason), "reason": reason}
        if req.step_id:
            body["current_step_id"] = req.step_id
        try:
            built = self.core.context.build(body, actor_id=actor["actor_id"])
        except errors.CoreError as exc:
            res.degraded.append(f"context_unavailable:{exc.code}")
            return
        pack = built["context"]
        res.inject = {"text": pack["rendered_content"], "context_pack_id": pack["context_pack_id"]}
        res.event_ids.append(pack["command_event_id"])

    def _checkpoint(self, req: HostHookRequest, actor: dict[str, Any], source_event_id: str,
                    reason: str, res: _Result) -> None:
        if req.task_id is None:
            return
        body = {"task_id": req.task_id, "host_id": req.host_id, "source_event_id": source_event_id,
                "idempotency_key": _key(req.delivery_id, reason), "reason": reason}
        if req.step_id:
            body["current_step_id"] = req.step_id
        try:
            made = self.core.continuity.create(body, actor_id=actor["actor_id"])
        except errors.CoreError as exc:
            res.degraded.append(f"checkpoint_unavailable:{exc.code}")
            return
        checkpoint = made["checkpoint"]
        res.checkpoint_id = checkpoint["checkpoint_id"]
        res.event_ids.append(checkpoint["command_event_id"])

    # -- events ----------------------------------------------------------------

    def _on_session_start(self, req, actor, project_id, res: _Result) -> None:
        event = self._append(req, actor, project_id, "session.started", "",
                             {"source": req.payload.get("source")})
        res.event_ids.append(event["event_id"])
        self._context(req, actor, event["event_id"], "SESSION_START", res)

    def _on_prompt_submit(self, req, actor, project_id, res: _Result) -> None:
        event = self._append(req, actor, project_id, "user.prompt", "",
                             {"origin": "host_hook"}, text=req.payload["text"])
        res.event_ids.append(event["event_id"])
        if req.task_id is None:
            return
        if actor["kind"] != "human":
            res.degraded.append("prompt_not_human_attributed")
            return
        self._context(req, actor, event["event_id"], "USER_PROMPT", res)

    def _on_tool_before(self, req, actor, project_id, res: _Result) -> None:
        p = req.payload
        paths = list(p.get("paths") or ([p["path"]] if p.get("path") else []))
        base: dict[str, Any] = {"tool": p["native_tool"]}
        if project_id:
            base["project_id"] = project_id
        if req.task_id:
            base["task_id"] = req.task_id
        if p.get("action"):
            base["action"] = p["action"]
        results = []
        for path in paths or [None]:
            body = dict(base)
            if path is not None:
                body["path"] = path
            try:
                results.append(self.core.authority.guard(body))
            except errors.InvalidRequest:
                if path is None:
                    raise
                res.degraded.append("path_unresolved")
                results.append(self.core.authority.guard(base))
        strictest = min(results, key=lambda r: _GUARD_ORDER.index(r["decision"]))
        res.decision = _GUARD_TO_DECISION[strictest["decision"]]
        res.reason = strictest["reason"] if res.decision in ("deny", "ask") else None
        res.guard = {"decision": strictest["decision"], "capability": strictest["capability"],
                     "request_sha256": strictest["request_sha256"], "matched": strictest["matched"]}

    def _on_tool_after(self, req, actor, project_id, res: _Result) -> None:
        if actor["kind"] != "system":
            raise errors.ForbiddenActorKind("tool results require a trusted system actor")
        p = req.payload
        response = {k: v for k, v in (("exit_code", p.get("exit_code")), ("is_error", p.get("is_error"))) if v is not None}
        response["native_response"] = p["response"]
        extra = {"host_hook": {"trust": req.trust, "delivery_id": req.delivery_id,
                               "agent": req.agent["name"], "adapter": req.adapter["name"],
                               "capabilities_sha256": req.adapter["capabilities_sha256"]}}
        if req.task_id is not None:
            body = {"task_id": req.task_id, "host_id": req.host_id,
                    "native_session_id": req.native["session_id"],
                    "turn_id": req.native.get("turn_id") or "none",
                    "tool_use_id": req.native.get("call_id") or req.delivery_id,
                    "tool_name": p["native_tool"], "tool_input": p["input"], "tool_response": response}
            if req.step_id:
                body["step_id"] = req.step_id
            try:
                out = self.core.evidence.record_tool_result(
                    body, actor_id=actor["actor_id"], source_system=SOURCE_SYSTEM, payload_extra=extra)
            except errors.FingerprintUnavailable as exc:
                res.degraded.append(f"evidence_unavailable:{exc.code}")
            else:
                record = out["evidence"]
                res.event_ids += [out["call_event"]["event_id"], out["result_event"]["event_id"],
                                  record["change_event_id"]]
                res.evidence_ids.append(record["evidence_id"])
                try:
                    self.core.evidence.capture(project_id, actor_id=actor["actor_id"], host_id=req.host_id)
                except errors.CoreError:
                    res.degraded.append("fingerprint_refresh_failed")
                return
        event = self._append(req, actor, project_id, "tool.result", "result",
                             {"tool": p["tool"], "tool_name": p["native_tool"], "tool_response": response,
                              "tool_input": p["input"]})
        res.event_ids.append(event["event_id"])

    def _on_context_compact(self, req, actor, project_id, res: _Result) -> None:
        phase = req.payload["phase"]
        event = self._append(req, actor, project_id, "host.compaction_reported", "",
                             {"phase": phase, "trigger": req.payload.get("trigger")})
        res.event_ids.append(event["event_id"])
        if phase == "before":
            self._checkpoint(req, actor, event["event_id"], "PRE_COMPACT", res)
        elif req.payload.get("deliver_context") is not False:
            self._context(req, actor, event["event_id"], "POST_COMPACT", res)

    def _on_turn_stop(self, req, actor, project_id, res: _Result) -> None:
        event = self._append(req, actor, project_id, "host.turn_stopped", "",
                             {"last_message_sha256": req.payload.get("last_message_sha256")})
        res.event_ids.append(event["event_id"])
        self._checkpoint(req, actor, event["event_id"], "SESSION_STOP", res)

    def _on_session_end(self, req, actor, project_id, res: _Result) -> None:
        event = self._append(req, actor, project_id, "session.ended", "",
                             {"reason": req.payload.get("reason")})
        res.event_ids.append(event["event_id"])
