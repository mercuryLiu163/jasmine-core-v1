"""Versioned Authority and deterministic, advisory Guard decisions (P1-01)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from . import clock, db, errors, ids
from .canonical import body_hash, canonical_json
from .events import EventStore
from .models import NewEvent

RULE_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
KINDS = frozenset({"RULE", "DECISION", "ACCEPTANCE"})
SEVERITIES = frozenset({"HARD", "NORMAL"})
ENFORCEMENTS = frozenset({"CONTEXT", "DENY", "CONFIRM", "VERIFY"})
MATCHER_KEYS = frozenset({"tool", "action", "path_prefix"})
STATUS = frozenset({"PROPOSED", "ACTIVE", "SUPERSEDED", "RETIRED"})


def _fields(body: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest("unknown fields", fields=unknown)


def _positive_revision(body: dict[str, Any]) -> int:
    if "expected_revision" not in body:
        raise errors.MissingExpectedRevision("expected_revision is required")
    value = body["expected_revision"]
    if type(value) is not int or value < 1:
        raise errors.InvalidRequest("expected_revision must be a positive integer", field="expected_revision")
    return value


def _id(body: dict[str, Any], key: str, prefix: str, *, required: bool = True) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not ids.is_id(value, prefix):
        raise errors.InvalidRequest(f"{key} must be a {prefix}_ id", field=key)
    return value


def _path(value: str) -> str:
    if not value.startswith("/") or "//" in value or "\\" in value or "\x00" in value:
        raise errors.InvalidRequest("path must be a canonical absolute POSIX path", field="path")
    parts = PurePosixPath(value).parts
    if any(part in (".", "..") for part in value.split("/")):
        raise errors.InvalidRequest("path must not contain dot segments", field="path")
    normal = str(PurePosixPath(value))
    if normal != value:
        raise errors.InvalidRequest("path must be canonical", field="path")
    return value


def _matcher(raw: Any, enforcement: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise errors.InvalidRequest("matcher must be an object", field="matcher")
    _fields(raw, set(MATCHER_KEYS))
    if enforcement in ("DENY", "CONFIRM") and not raw:
        raise errors.InvalidRequest("DENY/CONFIRM requires a deterministic matcher", field="matcher")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value or len(value) > 512:
            raise errors.InvalidRequest(f"matcher.{key} must be nonempty text", field=f"matcher.{key}")
        result[key] = _path(value) if key == "path_prefix" else value
    return result


def _content(body: dict[str, Any]) -> dict[str, Any]:
    kind = body.get("kind")
    severity = body.get("severity")
    enforcement = body.get("enforcement")
    content = body.get("content")
    if (not isinstance(kind, str) or not isinstance(severity, str) or
            not isinstance(enforcement, str) or kind not in KINDS or
            severity not in SEVERITIES or enforcement not in ENFORCEMENTS):
        raise errors.InvalidRequest("invalid kind, severity or enforcement")
    if not isinstance(content, str) or not content.strip() or len(content) > 4096:
        raise errors.InvalidRequest("content must be nonempty text of at most 4096 characters")
    matcher = _matcher(body.get("matcher", {}), enforcement)
    # A semantic requirement cannot pretend to be a deterministic denial.
    if enforcement in ("DENY", "CONFIRM") and kind != "RULE":
        raise errors.InvalidRequest("DENY/CONFIRM requires kind RULE")
    return {"kind": kind, "severity": severity, "enforcement": enforcement,
            "content": content, "matcher": matcher}


class AuthorityStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int) -> None:
        self.conn = conn
        self.events = EventStore(conn, schema_version=schema_version)

    def _scope(self, raw: Any) -> tuple[str, str | None, str | None]:
        if not isinstance(raw, dict):
            raise errors.InvalidRequest("scope must be an object", field="scope")
        _fields(raw, {"kind", "project_id", "task_id"})
        kind, project_id, task_id = raw.get("kind"), raw.get("project_id"), raw.get("task_id")
        if kind not in ("global", "project", "task"):
            raise errors.InvalidRequest("scope.kind must be global, project or task")
        if kind == "global":
            if project_id is not None or task_id is not None:
                raise errors.InvalidRequest("global scope cannot name a project or task")
            return kind, None, None
        if not ids.is_id(project_id, "prj") or self.conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is None:
            raise errors.NotFound("project", str(project_id))
        if kind == "project":
            if task_id is not None:
                raise errors.InvalidRequest("project scope cannot name a task")
            return kind, project_id, None
        if not ids.is_id(task_id, "tsk"):
            raise errors.InvalidRequest("task_id must be a tsk_ id", field="task_id")
        row = self.conn.execute("SELECT project_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise errors.NotFound("task", task_id)
        if row["project_id"] != project_id:
            raise errors.InvalidRequest("task does not belong to project")
        return kind, project_id, task_id

    def _origin(self, event_id: str, scope: tuple[str, str | None, str | None]) -> None:
        event = self.events.get(event_id)
        if event is None:
            raise errors.NotFound("event", event_id)
        kind, project_id, task_id = scope
        if kind == "global" and (event["project_id"] is not None or event["task_id"] is not None):
            raise errors.InvalidRequest("origin Event does not have global scope")
        if kind == "project" and (event["project_id"] != project_id or event["task_id"] is not None):
            raise errors.InvalidRequest("origin Event does not have project scope")
        if kind == "task" and (event["project_id"] != project_id or event["task_id"] != task_id):
            raise errors.InvalidRequest("origin Event does not have task scope")

    def _actor(self, actor_id: str, host_id: str) -> str:
        row = self.conn.execute("SELECT kind FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
        if row is None:
            raise errors.NotFound("actor", actor_id)
        if self.conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (host_id,)).fetchone() is None:
            raise errors.NotFound("host", host_id)
        return row["kind"]

    def _event(self, *, event_type: str, actor_id: str, actor_kind: str,
               host_id: str, project_id: str | None, task_id: str | None,
               payload: dict[str, Any], event_id: str | None = None,
               hash_payload: dict[str, Any] | None = None) -> NewEvent:
        return NewEvent(event_type=event_type, source_system="core-api", source_event_id=None,
                        occurred_at=clock.now(), actor_id=actor_id, actor_kind=actor_kind,
                        host_id=host_id, project_id=project_id, task_id=task_id,
                        payload={"text": "", **payload}, event_id=event_id,
                        hash_payload={"text": "", **hash_payload} if hash_payload is not None else None)

    def _replay(self, spec: NewEvent, *, expected_rule_id: str | None = None) -> dict[str, Any] | None:
        if spec.event_id is None:
            return None
        old = self.events.get(spec.event_id)
        if old is None:
            return None
        if old["body_sha256"] != spec.digest():
            raise errors.EventIdConflict("event_id already exists with different content",
                                         existing_event_id=spec.event_id)
        change = self.conn.execute("SELECT * FROM rule_changes WHERE change_event_id=?",
                                   (spec.event_id,)).fetchone()
        if change is None:
            raise errors.EventIdConflict("event_id belongs to another operation",
                                         existing_event_id=spec.event_id)
        if expected_rule_id is not None and change["rule_id"] != expected_rule_id:
            raise errors.EventIdConflict("event_id belongs to a different rule",
                                         existing_event_id=spec.event_id)
        return {"rule": self.get(change["rule_id"], version=change["version"],
                                 status=change["status"], revision=change["revision"]),
                "event": old, "replayed": True}

    def get(self, rule_id: str, *, version: int | None = None, status: str | None = None,
            revision: int | None = None) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
        if row is None:
            raise errors.NotFound("rule", rule_id)
        ver = version or row["current_version"]
        data = self.conn.execute("SELECT * FROM rule_versions WHERE rule_id=? AND version=?",
                                 (rule_id, ver)).fetchone()
        if data is None:
            raise errors.NotFound("rule_version", f"{rule_id}:{ver}")
        origin = self.events.get(data["origin_event_id"])
        active_revision = revision or row["revision"]
        status_row = self.conn.execute(
            "SELECT change_event_id FROM rule_changes WHERE rule_id=? AND revision=?",
            (rule_id, active_revision),
        ).fetchone()
        status_event_id = status_row["change_event_id"] if status_row else None
        status_event = self.events.get(status_event_id) if status_event_id else None
        requirements_row = self.conn.execute(
            "SELECT requirements_json FROM rule_verification_requirements WHERE rule_id=? AND version=?",
            (rule_id, ver),
        ).fetchone()
        return {"rule_id": rule_id, "rule_key": row["rule_key"], "version": ver,
                "revision": active_revision, "status": status or row["status"],
                "scope": {"kind": row["scope_kind"], "project_id": row["project_id"],
                          "task_id": row["task_id"]},
                "kind": data["kind"], "severity": data["severity"],
                "enforcement": data["enforcement"], "content": data["content"],
                "matcher": json.loads(data["matcher_json"]),
                "verification_requirements": json.loads(requirements_row["requirements_json"])
                if requirements_row is not None else None,
                "origin_event_id": data["origin_event_id"],
                "origin_actor_kind": origin["actor_kind"] if origin else None,
                "origin_event_type": origin["event_type"] if origin else None,
                "change_event_id": data["change_event_id"],
                "status_event_id": status_event_id,
                "status_actor_kind": status_event["actor_kind"] if status_event else None}

    def list_active(self, *, project_id: str | None = None,
                    task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id is not None:
            row = self.conn.execute("SELECT project_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise errors.NotFound("task", task_id)
            if project_id is not None and row["project_id"] != project_id:
                raise errors.InvalidRequest("task does not belong to project")
            project_id = row["project_id"]
        if project_id is not None and self.conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is None:
            raise errors.NotFound("project", project_id)
        rows = self.conn.execute(
            "SELECT rule_id FROM rules WHERE status='ACTIVE' AND "
            "(scope_kind='global' OR (scope_kind='project' AND project_id=?) OR "
            "(scope_kind='task' AND task_id=?)) ORDER BY scope_kind, rule_key, rule_id",
            (project_id, task_id),
        ).fetchall()
        return [self.get(row["rule_id"]) for row in rows]

    def history(self, rule_id: str) -> list[dict[str, Any]]:
        self.get(rule_id)
        rows = self.conn.execute(
            "SELECT change_event_id, version, status, revision, superseded_version, changed_at "
            "FROM rule_changes WHERE rule_id=? ORDER BY revision", (rule_id,)
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            if row["superseded_version"] is not None:
                history.append({"rule": self.get(rule_id, version=row["superseded_version"],
                                                  status="SUPERSEDED", revision=row["revision"]),
                                "change_event_id": row["change_event_id"],
                                "changed_at": row["changed_at"]})
            history.append({"rule": self.get(rule_id, version=row["version"],
                                              status=row["status"], revision=row["revision"]),
                            "change_event_id": row["change_event_id"],
                            "changed_at": row["changed_at"]})
        return history

    def propose(self, body: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        _fields(body, {"rule_key", "kind", "severity", "enforcement", "content", "matcher",
                       "scope", "origin_event_id", "host_id", "event_id",
                       "verification_requirements"})
        if "expected_revision" in body:
            raise errors.UnexpectedExpectedRevision("creation does not accept expected_revision")
        key = body.get("rule_key")
        if not isinstance(key, str) or RULE_KEY.fullmatch(key) is None:
            raise errors.InvalidRequest("rule_key must be a lowercase stable key", field="rule_key")
        content = _content(body)
        from .evidence import _requirement_set
        requirements = (_requirement_set(body["verification_requirements"])
                        if "verification_requirements" in body else None)
        if requirements is not None and content["kind"] != "ACCEPTANCE" and content["enforcement"] != "VERIFY":
            raise errors.InvalidRequest("verification_requirements requires ACCEPTANCE or VERIFY")
        host_id = _id(body, "host_id", "hst")
        origin_event_id = _id(body, "origin_event_id", "evt")
        event_id = _id(body, "event_id", "evt", required=False)
        with db.translate_lock_errors(), db.transaction(self.conn):
            scope = self._scope(body.get("scope"))
            self._origin(origin_event_id, scope)
            actor_kind = self._actor(actor_id, host_id)
            payload = {"action": "propose", "rule_key": key, "scope": body["scope"],
                       "origin_event_id": origin_event_id, **content}
            if requirements is not None:
                payload["verification_requirements"] = requirements
            rule_id = ids.new_id("rul")
            spec = self._event(event_type="rule.proposed", actor_id=actor_id,
                               actor_kind=actor_kind, host_id=host_id,
                               project_id=scope[1], task_id=scope[2],
                               payload={**payload, "rule_id": rule_id}, event_id=event_id,
                               hash_payload=payload)
            replay = self._replay(spec)
            if replay is not None:
                return replay
            if self.conn.execute(
                "SELECT 1 FROM rules WHERE rule_key=? AND scope_kind=? AND "
                "project_id IS ? AND task_id IS ?", (key, *scope)
            ).fetchone() is not None:
                raise errors.RuleKeyConflict("rule_key already exists in scope", rule_key=key)
            event, _ = self.events.append(spec)
            now = event["recorded_at"]
            self.conn.execute(
                "INSERT INTO rules(rule_id,rule_key,scope_kind,project_id,task_id,current_version,"
                "status,revision,created_at,updated_at) VALUES(?,?,?,?,?,1,'PROPOSED',1,?,?)",
                (rule_id, key, *scope, now, now),
            )
            self.conn.execute(
                "INSERT INTO rule_versions(rule_id,version,kind,severity,enforcement,content,"
                "matcher_json,origin_event_id,change_event_id,created_at)"
                " VALUES(?,1,?,?,?,?,?,?,?,?)",
                (rule_id, content["kind"], content["severity"], content["enforcement"],
                 content["content"], canonical_json(content["matcher"]),
                 origin_event_id, event["event_id"], now),
            )
            if requirements is not None:
                self.conn.execute(
                    "INSERT INTO rule_verification_requirements"
                    "(rule_id,version,requirements_json,source_event_id,actor_id,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (rule_id, 1, canonical_json(requirements), event["event_id"], actor_id, now),
                )
            self._change(event["event_id"], rule_id, 1, "PROPOSED", 1, now)
            return {"rule": self.get(rule_id), "event": event, "replayed": False}

    def _change(self, event_id: str, rule_id: str, version: int, status: str,
                revision: int, at: str, *, superseded_version: int | None = None) -> None:
        self.conn.execute("INSERT INTO rule_changes VALUES(?,?,?,?,?,?,?)",
                          (event_id, rule_id, version, status, revision, superseded_version, at))

    def _check_revision(self, row: sqlite3.Row, expected: int) -> None:
        if row["revision"] != expected:
            raise errors.RevisionConflict("rule revision is stale",
                                          current_revision=row["revision"],
                                          current=self.get(row["rule_id"]))

    def transition(self, rule_id: str, action: str, body: dict[str, Any],
                   *, actor_id: str) -> dict[str, Any]:
        if action not in ("approve", "retire", "supersede"):
            raise ValueError(action)
        allowed = {"expected_revision", "host_id", "event_id"}
        if action == "supersede":
            allowed |= {"kind", "severity", "enforcement", "content", "matcher",
                        "origin_event_id", "scope", "verification_requirements"}
        _fields(body, allowed)
        expected = _positive_revision(body)
        host_id = _id(body, "host_id", "hst")
        event_id = _id(body, "event_id", "evt", required=False)
        new_content = _content(body) if action == "supersede" else None
        from .evidence import _requirement_set
        requirements = (_requirement_set(body["verification_requirements"])
                        if action == "supersede" and "verification_requirements" in body else None)
        if requirements is not None and new_content["kind"] != "ACCEPTANCE" and new_content["enforcement"] != "VERIFY":
            raise errors.InvalidRequest("verification_requirements requires ACCEPTANCE or VERIFY")
        origin_event_id = _id(body, "origin_event_id", "evt") if action == "supersede" else None
        with db.translate_lock_errors(), db.transaction(self.conn):
            row = self.conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
            if row is None:
                raise errors.NotFound("rule", rule_id)
            actor_kind = self._actor(actor_id, host_id)
            if actor_kind not in ("human", "system"):
                raise errors.ForbiddenActorKind("only human or trusted system actors may manage Authority")
            scope = (row["scope_kind"], row["project_id"], row["task_id"])
            if action == "supersede":
                if self._scope(body.get("scope")) != scope:
                    raise errors.InvalidRequest("cross-scope supersede is not supported")
                self._origin(origin_event_id, scope)
            payload = {"action": action, "rule_id": rule_id, "expected_revision": expected}
            if action == "supersede":
                payload.update({"scope": body["scope"], "origin_event_id": origin_event_id,
                                **new_content})
                if requirements is not None:
                    payload["verification_requirements"] = requirements
            spec = self._event(event_type=f"rule.{action}d" if action == "approve" else
                               ("rule.retired" if action == "retire" else "rule.superseded"),
                               actor_id=actor_id, actor_kind=actor_kind, host_id=host_id,
                               project_id=scope[1], task_id=scope[2], payload=payload,
                               event_id=event_id)
            replay = self._replay(spec, expected_rule_id=rule_id)
            if replay is not None:
                return replay
            self._check_revision(row, expected)
            if action == "approve" and row["status"] != "PROPOSED":
                raise errors.InvalidRequest("only PROPOSED rules may be approved")
            if action in ("retire", "supersede") and row["status"] != "ACTIVE":
                raise errors.InvalidRequest("only ACTIVE rules may be retired or superseded")
            event, _ = self.events.append(spec)
            next_revision = expected + 1
            next_version = row["current_version"] + (1 if action == "supersede" else 0)
            next_status = "RETIRED" if action == "retire" else "ACTIVE"
            now = event["recorded_at"]
            if action == "supersede":
                self.conn.execute(
                    "INSERT INTO rule_versions(rule_id,version,kind,severity,enforcement,content,"
                    "matcher_json,origin_event_id,change_event_id,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (rule_id, next_version, new_content["kind"], new_content["severity"],
                     new_content["enforcement"], new_content["content"],
                     canonical_json(new_content["matcher"]), origin_event_id,
                     event["event_id"], now),
                )
                if requirements is not None:
                    self.conn.execute(
                        "INSERT INTO rule_verification_requirements"
                        "(rule_id,version,requirements_json,source_event_id,actor_id,created_at)"
                        " VALUES(?,?,?,?,?,?)",
                        (rule_id, next_version, canonical_json(requirements),
                         event["event_id"], actor_id, now),
                    )
            self.conn.execute(
                "UPDATE rules SET current_version=?,status=?,revision=?,updated_at=? "
                "WHERE rule_id=? AND revision=?",
                (next_version, next_status, next_revision, now, rule_id, expected),
            )
            self._change(event["event_id"], rule_id, next_version, next_status,
                         next_revision, now,
                         superseded_version=row["current_version"] if action == "supersede" else None)
            return {"rule": self.get(rule_id), "event": event, "replayed": False}

    def guard(self, body: dict[str, Any]) -> dict[str, Any]:
        _fields(body, {"project_id", "task_id", "tool", "action", "path"})
        project_id = _id(body, "project_id", "prj", required=False)
        task_id = _id(body, "task_id", "tsk", required=False)
        for key in ("tool", "action", "path"):
            if key in body and body[key] is not None and not isinstance(body[key], str):
                raise errors.InvalidRequest(f"{key} must be text", field=key)
        path = _path(body["path"]) if body.get("path") else None
        unresolved_context = not body.get("tool") or not body.get("action")
        # Without project/task identity, a scoped DENY/CONFIRM/VERIFY rule
        # could be omitted. Do not describe that partial lookup as ALLOW.
        if task_id is None:
            if project_id is None:
                unresolved_context |= self.conn.execute(
                    "SELECT 1 FROM rules r JOIN rule_versions v ON "
                    "v.rule_id=r.rule_id AND v.version=r.current_version "
                    "WHERE r.status='ACTIVE' AND r.scope_kind!='global' "
                    "AND v.enforcement!='CONTEXT' LIMIT 1"
                ).fetchone() is not None
            else:
                unresolved_context |= self.conn.execute(
                    "SELECT 1 FROM rules r JOIN rule_versions v ON "
                    "v.rule_id=r.rule_id AND v.version=r.current_version "
                    "WHERE r.status='ACTIVE' AND r.scope_kind='task' "
                    "AND r.project_id=? AND v.enforcement!='CONTEXT' LIMIT 1", (project_id,)
                ).fetchone() is not None
        matches: list[dict[str, Any]] = []
        uncertain: list[dict[str, Any]] = []
        for rule in self.list_active(project_id=project_id, task_id=task_id):
            matcher = rule["matcher"]
            mismatch = False
            missing = False
            for key in ("tool", "action", "path_prefix"):
                if key not in matcher:
                    continue
                actual = path if key == "path_prefix" else body.get(key)
                if not actual:
                    missing = True
                elif key == "path_prefix":
                    prefix = matcher[key]
                    if prefix != "/" and actual != prefix and not actual.startswith(prefix + "/"):
                        mismatch = True
                elif actual != matcher[key]:
                    mismatch = True
            if mismatch:
                continue
            if missing:
                uncertain.append(rule)
            else:
                matches.append(rule)
        priority = {"DENY": 0, "CONFIRM": 1, "VERIFY": 2, "CONTEXT": 3}
        applicable = sorted(matches, key=lambda r: (priority[r["enforcement"]], r["rule_id"]))
        uncertain = sorted(uncertain, key=lambda r: r["rule_id"])
        decision = "allow"
        reason = "no blocking deterministic rule matched"
        for rule in applicable:
            if rule["enforcement"] in ("DENY", "CONFIRM", "VERIFY"):
                decision = rule["enforcement"].lower()
                reason = f"matched {rule['rule_id']} version {rule['version']}"
                break
        # A definite DENY is final. Otherwise missing context may hide a DENY
        # or CONFIRM, and a broad VERIFY match must not take precedence over
        # that unresolved restriction.
        if decision != "deny" and (uncertain or unresolved_context):
            decision = "confirm"
            reason = "insufficient context to evaluate all applicable rules"
        return {"decision": decision, "reason": reason, "capability": "ADVISORY",
                "request_sha256": body_hash(body),
                "matched": [{"rule_id": r["rule_id"], "rule_key": r["rule_key"],
                             "version": r["version"], "enforcement": r["enforcement"],
                             "severity": r["severity"],
                             "origin_event_id": r["origin_event_id"]} for r in applicable],
                "uncertain": [{"rule_id": r["rule_id"], "version": r["version"]}
                              for r in uncertain]}
