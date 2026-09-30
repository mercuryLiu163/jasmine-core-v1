"""P1 Task/Step truth: revision locks, Event-first writes, fail-closed evidence.

The Evidence service injects an EvidenceValidator at Core construction. It must
check source Raw Events, criterion match, current fingerprint and confirmation
binding. No default can attest evidence. See ADR 0006.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from . import clock, db, errors, ids
from .canonical import canonical_json
from .events import EventStore
from .models import NewEvent

EVIDENCE_KINDS = frozenset({"COMMAND_RESULT", "BUILD", "TEST", "DEVICE_TEST", "FILE_CHANGE", "ARTIFACT", "REVIEW", "USER_CONFIRMATION"})
STEP_STATES = frozenset({"PLANNED", "IN_PROGRESS", "EXECUTED", "VERIFIED", "ACCEPTED", "FAILED", "BLOCKED", "STALE", "SKIPPED"})
STEP_EDGES = {
    "PLANNED": {"IN_PROGRESS", "SKIPPED"},
    "IN_PROGRESS": {"EXECUTED", "FAILED", "BLOCKED", "SKIPPED"},
    "EXECUTED": {"VERIFIED", "FAILED", "STALE", "SKIPPED"},
    "VERIFIED": {"ACCEPTED", "STALE"},
    "FAILED": {"IN_PROGRESS", "SKIPPED"},
    "BLOCKED": {"IN_PROGRESS", "SKIPPED"},
    "STALE": {"IN_PROGRESS", "SKIPPED"},
    "SKIPPED": {"IN_PROGRESS"},
    "ACCEPTED": set(),
}
TASK_EDGES = {
    "ACTIVE": {"BLOCKED", "CANCELLED"},
    "BLOCKED": {"ACTIVE", "CANCELLED"},
    "CANCELLED": {"ACTIVE"},
    "ACCEPTED": set(),
}


def validate_criteria(value: Any) -> dict[str, Any]:
    """Freeze a small, exact Evidence matcher contract for P1-03."""
    if value is None:
        value = {"requirements": []}
    if not isinstance(value, dict) or set(value) != {"requirements"} or not isinstance(value["requirements"], list):
        raise errors.InvalidRequest("acceptance_criteria requires a requirements array", field="acceptance_criteria")
    seen: set[str] = set()
    for item in value["requirements"]:
        if not isinstance(item, dict) or set(item) - {"key", "kind", "required_result", "tool_name", "command_sha256", "artifact_sha256"}:
            raise errors.InvalidRequest("invalid acceptance requirement fields", field="acceptance_criteria.requirements")
        key = item.get("key")
        kind = item.get("kind")
        result = item.get("required_result")
        if not isinstance(key, str) or not key or len(key) > 128 or key in seen:
            raise errors.InvalidRequest("requirement key must be unique and nonempty", field="acceptance_criteria.requirements.key")
        seen.add(key)
        if (not isinstance(kind, str) or not isinstance(result, str)
                or kind not in EVIDENCE_KINDS
                or result not in ({"PASS", "INFO"} if kind == "USER_CONFIRMATION" else {"PASS"})):
            raise errors.InvalidRequest("invalid Evidence kind or required_result", field="acceptance_criteria.requirements")
        if "tool_name" in item and (not isinstance(item["tool_name"], str)
                                    or not item["tool_name"] or len(item["tool_name"]) > 512):
            raise errors.InvalidRequest("tool_name must be nonempty text of at most 512 characters",
                                        field="tool_name")
        for name in ("command_sha256", "artifact_sha256"):
            if name in item and (not isinstance(item[name], str)
                                 or re.fullmatch(r"[0-9a-f]{64}", item[name]) is None):
                raise errors.InvalidRequest(f"{name} must be a lowercase SHA-256 hex digest", field=name)
    return value


@dataclass(frozen=True)
class EvidenceRequest:
    task: dict[str, Any]
    step: dict[str, Any] | None
    target_status: str
    criteria: dict[str, Any]
    actor_id: str
    actor_kind: str
    expected_revision: int
    require_user_confirmation: bool


@dataclass(frozen=True)
class EvidenceValidationResult:
    evidence_ids: list[str]
    rule_versions: list[dict[str, Any]]


class EvidenceValidator(Protocol):
    def validate(self, request: EvidenceRequest) -> EvidenceValidationResult:
        """Return real matching evd_ IDs or raise MissingEvidence.

        A production validator verifies each criterion, source Raw Event, result,
        current workspace fingerprint and binding to this revision. When
        require_user_confirmation is true it must also verify a distinct current
        USER_CONFIRMATION for this Task/Step and actor. Empty success is refused.
        """


class RefuseUnverifiedEvidence:
    def validate(self, request: EvidenceRequest) -> EvidenceValidationResult:
        raise errors.MissingEvidence("Evidence validation is unavailable; state remains unchanged",
                                     target_status=request.target_status)


@dataclass
class PendingEvent:
    spec: NewEvent


class StateStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int,
                 evidence_validator: EvidenceValidator | None = None) -> None:
        self.conn = conn
        self.events = EventStore(conn, schema_version=schema_version)
        self.evidence = evidence_validator or RefuseUnverifiedEvidence()

    def task(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise errors.NotFound("task", task_id)
        value = dict(row)
        value["acceptance_criteria"] = json.loads(value.pop("acceptance_criteria_json"))
        return value

    def step(self, step_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM steps WHERE step_id=?", (step_id,)).fetchone()
        if row is None:
            raise errors.NotFound("step", step_id)
        value = dict(row)
        value["acceptance_criteria"] = json.loads(value.pop("acceptance_criteria_json"))
        return value

    def steps(self, task_id: str) -> list[dict[str, Any]]:
        self.task(task_id)
        return [self.step(row["step_id"]) for row in self.conn.execute(
            "SELECT step_id FROM steps WHERE task_id=? ORDER BY created_at,step_id", (task_id,))]

    def history(self, task_id: str, *, step_id: str | None = None) -> list[dict[str, Any]]:
        self.task(task_id)
        if step_id is not None and self.step(step_id)["task_id"] != task_id:
            raise errors.NotFound("step", step_id)
        rows = self.conn.execute(
            "SELECT event_id FROM events WHERE task_id=? AND event_type IN "
            "('task.created','task.criteria_updated','task.transitioned','task.accepted',"
            "'step.created','step.criteria_updated','step.transitioned','step.staled') ORDER BY seq", (task_id,))
        events = [self.events.get(row["event_id"]) for row in rows]
        return [event for event in events if event is not None and
                (step_id is None or event["payload"].get("step_id") == step_id)]

    @staticmethod
    def _revision(snapshot: dict[str, Any], expected: Any, kind: str) -> None:
        if expected is None:
            raise errors.MissingExpectedRevision("expected_revision is required")
        if type(expected) is not int or expected < 1:
            raise errors.InvalidRequest("expected_revision must be a positive integer", field="expected_revision")
        if snapshot["revision"] != expected:
            raise errors.RevisionConflict("expected_revision does not match current revision",
                                          expected_revision=expected, current_revision=snapshot["revision"],
                                          current_snapshot=snapshot, kind=kind)

    @staticmethod
    def _role(actor_kind: str) -> None:
        if actor_kind not in ("human", "system"):
            raise errors.ForbiddenActorKind("acceptance and skip require a human or system actor")

    @staticmethod
    def _reason(reason: Any) -> str:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise errors.InvalidRequest("reason must be nonempty and at most 4096 characters", field="reason")
        return reason

    def _event(self, *, event_type: str, task: dict[str, Any], actor_id: str,
               actor_kind: str, host_id: str, payload: dict[str, Any],
               event_id: str | None = None) -> tuple[dict[str, Any], bool]:
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
        if event_id is not None and not ids.is_id(event_id, "evt"):
            raise errors.InvalidRequest("event_id must be an evt_ id", field="event_id")
        # Freeze the original response in the Event. A replay after later writes
        # returns this snapshot rather than the current projection.
        event_id = event_id or ids.new_id("evt")
        projection_at = clock.now_rfc3339()
        result = payload.get("result", {})
        for name in ("task", "step"):
            snapshot = result.get(name)
            if isinstance(snapshot, dict):
                snapshot["updated_at"] = projection_at
                if snapshot.get("created_at") is None:
                    snapshot["created_at"] = projection_at
                if name == "step" and event_type == "step.created":
                    snapshot["source_event_id"] = event_id
        payload["projection_at"] = projection_at
        digest_payload = {k: v for k, v in payload.items()
                          if k not in ("result", "projection_at", "from")
                          and not (k == "step_id" and event_type == "step.created")}
        spec = NewEvent(
            event_type=event_type, source_system="core-api", source_event_id=None,
            occurred_at=clock.now(), actor_id=actor_id, actor_kind=actor_kind,
            host_id=host_id, project_id=task["project_id"], task_id=task["task_id"],
            payload={"text": "", **payload}, event_id=event_id,
            hash_payload={"text": "", **digest_payload}, client_occurred_at=None)
        if self.events.get(event_id) is not None:
            return self.events.append(spec)
        return PendingEvent(spec), False

    def _commit(self, pending: PendingEvent) -> dict[str, Any]:
        event, replayed = self.events.append(pending.spec)
        if replayed:
            # BEGIN IMMEDIATE prevents another writer from racing the preflight.
            raise errors.EventIdConflict("event appeared during state write")
        return event

    @staticmethod
    def _validated(value: EvidenceValidationResult) -> tuple[list[str], list[dict[str, Any]]]:
        if not isinstance(value, EvidenceValidationResult):
            raise errors.MissingEvidence("Evidence validator returned an invalid result")
        evidence_ids, rule_versions = value.evidence_ids, value.rule_versions
        if not evidence_ids or not all(ids.is_id(item, "evd") for item in evidence_ids):
            raise errors.MissingEvidence("Evidence validator returned no valid Evidence IDs")
        if not isinstance(rule_versions, list) or any(
            not isinstance(item, dict) or set(item) != {"rule_id", "version"}
            or not ids.is_id(item["rule_id"], "rul") or type(item["version"]) is not int
            or item["version"] < 1 for item in rule_versions
        ):
            raise errors.MissingEvidence("Evidence validator returned invalid Rule versions")
        return evidence_ids, rule_versions

    def _result(self, event: dict[str, Any], replayed: bool) -> dict[str, Any]:
        if replayed:
            result = event["payload"].get("result")
            if not isinstance(result, dict):
                raise errors.EventIdConflict("event replay lacks its original snapshot")
            return {**result, "event": event, "replayed": True}
        raise RuntimeError("call only for replay")

    def create_step(self, task_id: str, body: dict[str, Any], *, actor_id: str, actor_kind: str,
                    can_accept: bool) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn):
            task = self.task(task_id)
            title = body.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 512:
                raise errors.InvalidRequest("title must be nonempty and at most 512 characters", field="title")
            description = body.get("description", "")
            if not isinstance(description, str):
                raise errors.InvalidRequest("description must be a string", field="description")
            criteria = validate_criteria(body.get("acceptance_criteria"))
            if criteria["requirements"]:
                if not can_accept:
                    raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
                self._role(actor_kind)
            expected = body.get("expected_revision")
            step_id = ids.new_id("stp")
            planned = {"step_id": step_id, "task_id": task_id, "title": title,
                       "description": description, "status": "PLANNED", "revision": 1,
                       "acceptance_criteria": criteria, "source_event_id": body.get("event_id"),
                       "created_at": None, "updated_at": None}
            result = {"step": planned, "task_revision": task["revision"] + 1}
            event, replayed = self._event(event_type="step.created", task=task, actor_id=actor_id,
                actor_kind=actor_kind, host_id=body.get("host_id"), event_id=body.get("event_id"),
                payload={"expected_revision": expected, "title": title, "description": description,
                         "acceptance_criteria": criteria, "step_id": step_id, "result": result})
            if replayed:
                return self._result(event, True)
            self._revision(task, expected, "task")
            if task["status"] != "ACTIVE":
                raise errors.InvalidStateTransition("Step creation requires ACTIVE Task", current_status=task["status"])
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("INSERT INTO steps (step_id,task_id,title,description,status,revision,acceptance_criteria_json,source_event_id,created_at,updated_at) VALUES (?,?,?,?,'PLANNED',1,?,?,?,?)",
                              (step_id, task_id, title, description, canonical_json(criteria), event["event_id"], now, now))
            self.conn.execute("UPDATE tasks SET revision=revision+1,updated_at=? WHERE task_id=?", (now, task_id))
            # The Event carries the immutable original values; never return live state on replay.
            return {"step": self.step(step_id), "task_revision": task["revision"] + 1,
                    "event": event, "replayed": False}


    def set_step_criteria(self, step_id: str, body: dict[str, Any], *, actor_id: str,
                          actor_kind: str, can_accept: bool, unit_of_work=None) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn, unit_of_work=unit_of_work):
            step = self.step(step_id)
            task = self.task(step["task_id"])
            criteria = validate_criteria(body.get("acceptance_criteria"))
            expected = body.get("expected_revision")
            event, replayed = self._event(event_type="step.criteria_updated", task=task,
                actor_id=actor_id, actor_kind=actor_kind, host_id=body.get("host_id"),
                event_id=body.get("event_id"), payload={"step_id": step_id,
                  "expected_revision": expected, "acceptance_criteria": criteria,
                  "result": {"step": {**step, "acceptance_criteria": criteria,
                                       "revision": step["revision"] + 1},
                             "task_revision": task["revision"] + 1}})
            if replayed:
                return self._result(event, True)
            self._revision(step, expected, "step")
            if not can_accept:
                raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
            self._role(actor_kind)
            if task["status"] != "ACTIVE" or step["status"] in ("VERIFIED", "ACCEPTED", "SKIPPED"):
                raise errors.InvalidStateTransition("criteria cannot change on verified or terminal Step")
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("UPDATE steps SET acceptance_criteria_json=?,revision=revision+1,updated_at=? WHERE step_id=?",
                              (canonical_json(criteria), now, step_id))
            self.conn.execute("UPDATE tasks SET revision=revision+1,updated_at=? WHERE task_id=?",
                              (now, task["task_id"]))
            return {"step": self.step(step_id), "task_revision": task["revision"] + 1,
                    "event": event, "replayed": False}

    def transition_step(self, step_id: str, body: dict[str, Any], *, actor_id: str,
                        actor_kind: str, can_accept: bool) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn):
            step = self.step(step_id)
            task = self.task(step["task_id"])
            target = body.get("status")
            if not isinstance(target, str) or target not in STEP_STATES:
                raise errors.InvalidRequest("invalid Step status", field="status")
            expected = body.get("expected_revision")
            reason = body.get("reason")
            event, replayed = self._event(event_type="step.transitioned", task=task,
                actor_id=actor_id, actor_kind=actor_kind, host_id=body.get("host_id"),
                event_id=body.get("event_id"), payload={"step_id": step_id, "from": step["status"],
                  "to": target, "expected_revision": expected, "reason": reason,
                  "result": {"step": {**step, "status": target, "revision": step["revision"] + 1},
                             "task_revision": task["revision"] + 1, "evidence_ids": [], "rule_versions": []}})
            if replayed:
                return self._result(event, True)
            self._revision(step, expected, "step")
            if task["status"] != "ACTIVE":
                raise errors.InvalidStateTransition("Step transition requires ACTIVE Task", task_status=task["status"])
            if target not in STEP_EDGES[step["status"]]:
                raise errors.InvalidStateTransition("invalid Step transition", from_status=step["status"], to_status=target)
            if target in ("SKIPPED", "ACCEPTED") or step["status"] == "SKIPPED":
                if not can_accept:
                    raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
                self._role(actor_kind)
            if target == "SKIPPED" or step["status"] in ("BLOCKED", "SKIPPED"):
                self._reason(reason)
            evidence_ids: list[str] = []
            rule_versions: list[dict[str, Any]] = []
            if target in ("VERIFIED", "ACCEPTED"):
                criteria = step["acceptance_criteria"]
                if not criteria["requirements"]:
                    raise errors.MissingEvidence("Step has no acceptance requirements", step_id=step_id)
                validated = self.evidence.validate(EvidenceRequest(task, step, target, criteria,
                    actor_id, actor_kind, expected, target == "ACCEPTED"))
                evidence_ids, rule_versions = self._validated(validated)
                event.spec.payload["evidence_ids"] = evidence_ids
                event.spec.payload["rule_versions"] = rule_versions
                event.spec.payload["result"]["evidence_ids"] = evidence_ids
                event.spec.payload["result"]["rule_versions"] = rule_versions
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("UPDATE steps SET status=?,revision=revision+1,updated_at=? WHERE step_id=?",
                              (target, now, step_id))
            self.conn.execute("UPDATE tasks SET revision=revision+1,updated_at=? WHERE task_id=?", (now, task["task_id"]))
            return {"step": self.step(step_id), "task_revision": task["revision"] + 1,
                    "evidence_ids": evidence_ids, "rule_versions": rule_versions,
                    "event": event, "replayed": False}

    def transition_task(self, task_id: str, body: dict[str, Any], *, actor_id: str,
                        actor_kind: str, can_accept: bool) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn):
            task = self.task(task_id)
            target = body.get("status")
            if not isinstance(target, str) or target not in TASK_EDGES:
                raise errors.InvalidRequest("invalid Task status", field="status")
            expected = body.get("expected_revision")
            reason = body.get("reason")
            event, replayed = self._event(event_type="task.transitioned", task=task,
                actor_id=actor_id, actor_kind=actor_kind, host_id=body.get("host_id"),
                event_id=body.get("event_id"), payload={"from": task["status"], "to": target,
                    "expected_revision": expected, "reason": reason,
                    "result": {"task": {**task, "status": target, "revision": task["revision"] + 1}}})
            if replayed:
                return self._result(event, True)
            self._revision(task, expected, "task")
            if target not in TASK_EDGES[task["status"]]:
                raise errors.InvalidStateTransition("invalid Task transition", from_status=task["status"], to_status=target)
            self._reason(reason)
            if task["status"] == "CANCELLED" or target == "CANCELLED":
                if not can_accept:
                    raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
                self._role(actor_kind)
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("UPDATE tasks SET status=?,revision=revision+1,updated_at=? WHERE task_id=?",
                              (target, now, task_id))
            return {"task": self.task(task_id), "event": event, "replayed": False}

    def set_task_criteria(self, task_id: str, body: dict[str, Any], *, actor_id: str,
                          actor_kind: str, can_accept: bool, unit_of_work=None) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn, unit_of_work=unit_of_work):
            task = self.task(task_id)
            criteria = validate_criteria(body.get("acceptance_criteria"))
            expected = body.get("expected_revision")
            event, replayed = self._event(event_type="task.criteria_updated", task=task,
                actor_id=actor_id, actor_kind=actor_kind, host_id=body.get("host_id"),
                event_id=body.get("event_id"), payload={"expected_revision": expected,
                  "acceptance_criteria": criteria, "result": {"task": {**task,
                  "acceptance_criteria": criteria, "revision": task["revision"] + 1}}})
            if replayed:
                return self._result(event, True)
            self._revision(task, expected, "task")
            if not can_accept:
                raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
            self._role(actor_kind)
            if task["status"] not in ("ACTIVE", "BLOCKED"):
                raise errors.InvalidStateTransition("criteria cannot change on terminal Task")
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("UPDATE tasks SET acceptance_criteria_json=?,revision=revision+1,updated_at=? WHERE task_id=?",
                              (canonical_json(criteria), now, task_id))
            return {"task": self.task(task_id), "event": event, "replayed": False}

    def accept_task(self, task_id: str, body: dict[str, Any], *, actor_id: str,
                    actor_kind: str, can_accept: bool) -> dict[str, Any]:
        with db.translate_lock_errors(), db.transaction(self.conn):
            task = self.task(task_id)
            expected = body.get("expected_revision")
            event, replayed = self._event(event_type="task.accepted", task=task,
                actor_id=actor_id, actor_kind=actor_kind, host_id=body.get("host_id"),
                event_id=body.get("event_id"), payload={"expected_revision": expected,
                    "result": {"task": {**task, "status": "ACCEPTED", "revision": task["revision"] + 1},
                               "evidence_ids": [], "rule_versions": []}})
            if replayed:
                return self._result(event, True)
            self._revision(task, expected, "task")
            if not can_accept:
                raise errors.ForbiddenScope("state:accept scope is required", required="state:accept")
            self._role(actor_kind)
            if task["status"] != "ACTIVE":
                raise errors.InvalidStateTransition("Task must be ACTIVE for acceptance", current_status=task["status"])
            steps = self.steps(task_id)
            if not steps or not any(step["status"] == "ACCEPTED" for step in steps) or any(
                step["status"] not in ("ACCEPTED", "SKIPPED") for step in steps):
                raise errors.InvalidStateTransition("Task needs an accepted Step and no unfinished Steps")
            criteria = task["acceptance_criteria"]
            if not criteria["requirements"]:
                raise errors.MissingEvidence("Task has no acceptance requirements", task_id=task_id)
            validated = self.evidence.validate(EvidenceRequest(task, None, "ACCEPTED", criteria,
                actor_id, actor_kind, expected, True))
            evidence_ids, rule_versions = self._validated(validated)
            event.spec.payload["evidence_ids"] = evidence_ids
            event.spec.payload["rule_versions"] = rule_versions
            event.spec.payload["result"]["evidence_ids"] = evidence_ids
            event.spec.payload["result"]["rule_versions"] = rule_versions
            event = self._commit(event)
            now = event["payload"]["projection_at"]
            self.conn.execute("UPDATE tasks SET status='ACCEPTED',revision=revision+1,updated_at=? WHERE task_id=?",
                              (now, task_id))
            return {"task": self.task(task_id), "evidence_ids": evidence_ids,
                    "rule_versions": rule_versions, "event": event, "replayed": False}
