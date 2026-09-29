"""Immutable Evidence ingestion and current-workspace validation for P1-03.

Only a trusted system tool producer can create tool PASS/FAIL records. Human
confirmation is a separate API operation backed by an existing human Raw Event.
The validator reads every active applicable VERIFY/ACCEPTANCE Rule inside the
State transaction and refuses an unmapped version.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import clock, db, errors, fingerprint, ids
from .canonical import body_hash, canonical_json
from .events import EventStore
from .models import NewEvent

KINDS = frozenset({"COMMAND_RESULT", "BUILD", "TEST", "DEVICE_TEST",
                   "FILE_CHANGE", "ARTIFACT", "REVIEW", "USER_CONFIRMATION"})
RESULTS = frozenset({"PASS", "FAIL", "INFO"})
TOOL_KINDS = KINDS - {"USER_CONFIRMATION"}


def _fields(body: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest("unknown fields", fields=unknown)


def _id(value: Any, prefix: str, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not ids.is_id(value, prefix):
        raise errors.InvalidRequest(f"{field} must be a {prefix}_ id", field=field)
    return value


def _text(value: Any, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise errors.InvalidRequest(f"{field} must be nonempty text of at most {limit} characters",
                                    field=field)
    return value


def _deterministic_event_id(*parts: str) -> str:
    """Stable opaque evt_ ID for one hook delivery and its retry."""
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).digest()
    value = int.from_bytes(digest[:17], "big") >> 6  # first 130 bits
    alphabet = ids.CROCKFORD
    body = []
    for _ in range(26):
        body.append(alphabet[value & 31])
        value >>= 5
    return "evt_" + "".join(reversed(body))


def _requirement_set(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"requirements"} or not isinstance(value["requirements"], list):
        raise errors.InvalidRequest("requirements must be an object with a requirements array")
    if not value["requirements"]:
        raise errors.InvalidRequest("at least one Evidence requirement is required")
    seen: set[str] = set()
    for item in value["requirements"]:
        if not isinstance(item, dict) or set(item) - {
            "key", "kind", "required_result", "tool_name", "command_sha256", "artifact_sha256"
        }:
            raise errors.InvalidRequest("invalid Evidence requirement")
        key = item.get("key")
        if not isinstance(key, str) or not key or len(key) > 128 or key in seen:
            raise errors.InvalidRequest("requirement key must be unique and nonempty")
        seen.add(key)
        kind, result = item.get("kind"), item.get("required_result")
        if not isinstance(kind, str) or not isinstance(result, str) or kind not in KINDS or result not in (
            {"PASS", "INFO"} if kind == "USER_CONFIRMATION" else {"PASS"}
        ):
            raise errors.InvalidRequest("invalid Evidence kind or required_result")
        for name in ("tool_name", "command_sha256", "artifact_sha256"):
            if name in item:
                _text(item[name], name)
    return value


def _tool_result(raw: Any) -> tuple[str, dict[str, Any]]:
    """Read a process result, never an Agent's prose assertion of success."""
    if not isinstance(raw, dict):
        return "INFO", {"exit_code": None, "is_error": None}
    exit_code = raw.get("exit_code", raw.get("exitCode"))
    is_error = raw.get("is_error", raw.get("isError"))
    if type(exit_code) is not int:
        status = "FAIL" if is_error is True else "INFO"
        exit_code = None
    elif exit_code != 0 or is_error is True:
        status = "FAIL"
    else:
        status = "PASS"
    return status, {"exit_code": exit_code, "is_error": is_error if type(is_error) is bool else None}


class EvidenceStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int) -> None:
        self.conn = conn
        self.events = EventStore(conn, schema_version=schema_version)

    def _task_step(self, task_id: str, step_id: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise errors.NotFound("task", task_id)
        task = dict(row)
        step = None
        if step_id is not None:
            row = self.conn.execute("SELECT * FROM steps WHERE step_id=?", (step_id,)).fetchone()
            if row is None:
                raise errors.NotFound("step", step_id)
            step = dict(row)
            if step["task_id"] != task_id:
                raise errors.InvalidRequest("step does not belong to task")
        return task, step

    def _actor_host(self, actor_id: str, host_id: str) -> str:
        actor = self.conn.execute("SELECT kind FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
        if actor is None:
            raise errors.NotFound("actor", actor_id)
        if self.conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (host_id,)).fetchone() is None:
            raise errors.NotFound("host", host_id)
        return actor["kind"]

    @staticmethod
    def _snapshot_identity(project_id: str, snapshot: dict[str, Any]) -> str:
        return body_hash({"project_id": project_id, "root": snapshot["root"],
                              "git_head": snapshot["git_head"],
                              "manifest_sha256": snapshot["manifest_sha256"],
                              "complete": snapshot["complete"]})

    def _snapshot(self, project_id: str, source_event_id: str,
                  snapshot: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        snapshot = fingerprint.capture() if snapshot is None else snapshot
        identity = self._snapshot_identity(project_id, snapshot)
        if self.conn.execute("SELECT 1 FROM workspace_fingerprints WHERE fingerprint_sha256=?",
                             (identity,)).fetchone() is None:
            self.conn.execute(
                "INSERT INTO workspace_fingerprints"
                "(fingerprint_sha256,project_id,root_path,snapshot_json,complete,"
                "source_event_id,first_captured_at) VALUES(?,?,?,?,?,?,?)",
                (identity, project_id, snapshot["root"], canonical_json(snapshot),
                 int(snapshot["complete"]), source_event_id, snapshot["captured_at"]),
            )
        return identity, snapshot

    def capture(self, project_id: str, *, actor_id: str, host_id: str) -> dict[str, Any]:
        if not ids.is_id(project_id, "prj"):
            raise errors.InvalidRequest("project_id must be a prj_ id")
        with db.translate_lock_errors(), db.transaction(self.conn):
            if self.conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone() is None:
                raise errors.NotFound("project", project_id)
            actor_kind = self._actor_host(actor_id, host_id)
            snapshot = fingerprint.capture()
            digest = self._snapshot_identity(project_id, snapshot)
            row = self.conn.execute("SELECT source_event_id FROM workspace_fingerprints "
                                    "WHERE fingerprint_sha256=?", (digest,)).fetchone()
            if row is None:
                event, _ = self.events.append(NewEvent(
                    event_type="workspace.fingerprinted", source_system="core-api",
                    source_event_id=None, occurred_at=clock.now(), actor_id=actor_id,
                    actor_kind=actor_kind, host_id=host_id, project_id=project_id,
                    payload={"text": "", "fingerprint_sha256": digest,
                             "complete": snapshot["complete"]}, client_occurred_at=None))
                self._snapshot(project_id, event["event_id"], snapshot)
        return {"fingerprint_sha256": digest, "snapshot": snapshot}

    def compare(self, left_sha256: str, right_sha256: str) -> dict[str, Any]:
        rows = []
        for value in (left_sha256, right_sha256):
            row = self.conn.execute("SELECT project_id,snapshot_json FROM workspace_fingerprints "
                                    "WHERE fingerprint_sha256=?", (value,)).fetchone()
            if row is None:
                raise errors.NotFound("fingerprint", value)
            rows.append(row)
        if rows[0]["project_id"] != rows[1]["project_id"]:
            return {"comparison": "MISMATCH", "reason": "different projects"}
        return {"comparison": fingerprint.compare(json.loads(rows[0]["snapshot_json"]),
                                                   json.loads(rows[1]["snapshot_json"]))}

    def _event(self, *, event_type: str, actor_id: str, actor_kind: str, host_id: str,
               task: dict[str, Any], payload: dict[str, Any], event_id: str | None = None,
               session_id: str | None = None, source_system: str = "core-api",
               source_event_id: str | None = None,
               hash_payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        return self.events.append(NewEvent(
            event_type=event_type, source_system=source_system,
            source_event_id=source_event_id, occurred_at=clock.now(),
            actor_id=actor_id, actor_kind=actor_kind, host_id=host_id,
            session_id=session_id, project_id=task["project_id"], task_id=task["task_id"],
            payload={"text": "", **payload}, event_id=event_id,
            hash_payload={"text": "", **hash_payload} if hash_payload is not None else None,
            client_occurred_at=None))

    def _evidence(self, evidence_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is None:
            raise errors.NotFound("evidence", evidence_id)
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        item["reference_status"] = self._reference_status(item)
        return item

    def _reference_status(self, item: dict[str, Any]) -> str:
        uri = item.get("artifact_uri")
        if uri is None:
            return "NONE"
        if not isinstance(uri, str) or not uri.startswith("workspace:/"):
            return "BROKEN_REFERENCE"
        relative = uri.removeprefix("workspace:/")
        root = fingerprint.configured_root()
        path = root / relative
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                return "BROKEN_REFERENCE"
            digest_hash = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest_hash.update(chunk)
            digest = digest_hash.hexdigest()
        except OSError:
            return "BROKEN_REFERENCE"
        return "OK" if digest == item["artifact_sha256"] else "BROKEN_REFERENCE"

    def get(self, evidence_id: str) -> dict[str, Any]:
        return self._evidence(evidence_id)

    def list_task(self, task_id: str) -> list[dict[str, Any]]:
        self._task_step(task_id, None)
        return [self._evidence(row["evidence_id"]) for row in self.conn.execute(
            "SELECT evidence_id FROM evidence WHERE task_id=? ORDER BY created_at,evidence_id",
            (task_id,))]

    def _artifact(self, uri: str | None) -> tuple[str | None, str | None]:
        if uri is None:
            return None, None
        if not isinstance(uri, str) or not uri.startswith("workspace:/"):
            raise errors.InvalidRequest("artifact_uri must start workspace:/")
        relative = uri.removeprefix("workspace:/")
        if not relative or ".." in Path(relative).parts or Path(relative).is_absolute():
            raise errors.InvalidRequest("artifact_uri must name a workspace-relative file")
        root = fingerprint.configured_root()
        path = root / relative
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                return uri, None
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            return uri, digest.hexdigest()
        except OSError:
            return uri, None

    def _insert(self, *, evidence_id: str, task: dict[str, Any], step: dict[str, Any] | None,
                kind: str, status: str, result: dict[str, Any], source_event_id: str,
                change_event_id: str, fingerprint_sha256: str, artifact_uri: str | None,
                artifact_sha256: str | None, actor_id: str, host_id: str,
                session_id: str | None = None, turn_id: str | None = None,
                tool_use_id: str | None = None, tool_name: str | None = None,
                command_sha256: str | None = None, confirmed_rule_id: str | None = None,
                confirmed_rule_version: int | None = None) -> dict[str, Any]:
        self.conn.execute(
            "INSERT INTO evidence (evidence_id,task_id,step_id,kind,status,result_json,"
            "source_event_id,change_event_id,fingerprint_sha256,artifact_uri,artifact_sha256,"
            "actor_id,host_id,session_id,turn_id,tool_use_id,tool_name,command_sha256,"
            "confirmed_rule_id,confirmed_rule_version,task_revision,step_revision,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, task["task_id"], step["step_id"] if step else None,
             kind, status, canonical_json(result), source_event_id, change_event_id,
             fingerprint_sha256, artifact_uri, artifact_sha256, actor_id, host_id,
             session_id, turn_id, tool_use_id, tool_name, command_sha256,
             confirmed_rule_id, confirmed_rule_version, task["revision"],
             step["revision"] if step else None, clock.now_rfc3339()),
        )
        return self._evidence(evidence_id)

    def record_tool_result(self, body: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        _fields(body, {"task_id", "step_id", "host_id", "session_id", "codex_session_id",
                       "turn_id", "tool_use_id", "tool_name", "tool_input", "tool_response",
                       "kind", "artifact_uri"})
        task_id = _id(body.get("task_id"), "tsk", "task_id")
        step_id = _id(body.get("step_id"), "stp", "step_id", optional=True)
        host_id = _id(body.get("host_id"), "hst", "host_id")
        session_id = _id(body.get("session_id"), "ses", "session_id", optional=True)
        codex_session_id = _text(body.get("codex_session_id"), "codex_session_id")
        turn_id = _text(body.get("turn_id"), "turn_id")
        tool_use_id = _text(body.get("tool_use_id"), "tool_use_id")
        tool_name = _text(body.get("tool_name"), "tool_name")
        tool_input, tool_response = body.get("tool_input"), body.get("tool_response")
        if not isinstance(tool_input, dict) or tool_response is None:
            raise errors.InvalidRequest("tool_input must be an object and tool_response is required")
        kind = body.get("kind", "COMMAND_RESULT")
        if not isinstance(kind, str) or kind not in TOOL_KINDS:
            raise errors.InvalidRequest("kind must be a tool Evidence kind")
        status, parsed = _tool_result(tool_response)
        command = tool_input.get("command")
        command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest() if isinstance(command, str) else None
        seed = ("codex-posttool-v1", codex_session_id, turn_id, tool_use_id)
        with db.translate_lock_errors(), db.transaction(self.conn):
            task, step = self._task_step(task_id, step_id)
            actor_kind = self._actor_host(actor_id, host_id)
            if actor_kind != "system":
                raise errors.ForbiddenActorKind("only a trusted system tool producer may report tool results")
            if session_id is not None:
                row = self.conn.execute("SELECT task_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if row is None:
                    raise errors.NotFound("session", session_id)
                if row["task_id"] != task_id:
                    raise errors.InvalidRequest("session does not belong to task")
            artifact_uri, artifact_sha = self._artifact(body.get("artifact_uri"))
            call_event, call_replay = self._event(
                event_type="tool.call", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task, session_id=session_id,
                source_system="codex-posttool", source_event_id="|".join((*seed, "call")),
                event_id=_deterministic_event_id(*seed, "call"),
                payload={"codex_session_id": codex_session_id, "turn_id": turn_id,
                         "tool_use_id": tool_use_id, "tool_name": tool_name,
                         "tool_input": tool_input, "step_id": step_id})
            result_event, result_replay = self._event(
                event_type="tool.result", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task, session_id=session_id,
                source_system="codex-posttool", source_event_id="|".join((*seed, "result")),
                event_id=_deterministic_event_id(*seed, "result"),
                payload={"codex_session_id": codex_session_id, "turn_id": turn_id,
                         "tool_use_id": tool_use_id, "tool_name": tool_name,
                         "tool_response": tool_response, "step_id": step_id,
                         "call_event_id": call_event["event_id"]})
            if call_replay != result_replay:
                raise errors.EventIdConflict("tool call/result replay state is inconsistent")
            if result_replay:
                row = self.conn.execute("SELECT evidence_id FROM evidence WHERE source_event_id=?",
                                        (result_event["event_id"],)).fetchone()
                if row is None:
                    raise errors.EventIdConflict("replayed tool result has no Evidence projection")
                return {"evidence": self._evidence(row["evidence_id"]),
                        "call_event": call_event, "result_event": result_event,
                        "replayed": True}
            fp_sha, snapshot = self._snapshot(task["project_id"], result_event["event_id"])
            evidence_id = ids.new_id("evd")
            evidence_event, _ = self._event(
                event_type="evidence.recorded", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task, session_id=session_id,
                source_system="codex-posttool", source_event_id="|".join((*seed, "evidence")),
                event_id=_deterministic_event_id(*seed, "evidence"),
                payload={"evidence_id": evidence_id, "source_event_id": result_event["event_id"],
                         "kind": kind, "status": status, "step_id": step_id,
                         "fingerprint_sha256": fp_sha, "artifact_uri": artifact_uri,
                         "artifact_sha256": artifact_sha, "parsed_result": parsed},
                hash_payload={"source_event_id": result_event["event_id"], "kind": kind,
                              "status": status, "step_id": step_id,
                              "fingerprint_sha256": fp_sha, "artifact_uri": artifact_uri,
                              "artifact_sha256": artifact_sha, "parsed_result": parsed})
            record = self._insert(evidence_id=evidence_id, task=task, step=step, kind=kind,
                                  status=status, result=parsed,
                                  source_event_id=result_event["event_id"],
                                  change_event_id=evidence_event["event_id"],
                                  fingerprint_sha256=fp_sha, artifact_uri=artifact_uri,
                                  artifact_sha256=artifact_sha, actor_id=actor_id, host_id=host_id,
                                  session_id=session_id, turn_id=turn_id,
                                  tool_use_id=tool_use_id, tool_name=tool_name,
                                  command_sha256=command_sha256)
            return {"evidence": record, "call_event": call_event,
                    "result_event": result_event, "replayed": False}

    def record_confirmation(self, body: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        _fields(body, {"task_id", "step_id", "host_id", "origin_event_id",
                       "expected_revision", "event_id", "confirmed_rule_id",
                       "confirmed_rule_version"})
        task_id = _id(body.get("task_id"), "tsk", "task_id")
        step_id = _id(body.get("step_id"), "stp", "step_id", optional=True)
        host_id = _id(body.get("host_id"), "hst", "host_id")
        origin_id = _id(body.get("origin_event_id"), "evt", "origin_event_id")
        event_id = _id(body.get("event_id"), "evt", "event_id", optional=True)
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 1:
            raise errors.InvalidRequest("expected_revision must be a positive integer")
        confirmed_rule_id = _id(body.get("confirmed_rule_id"), "rul", "confirmed_rule_id",
                                optional=True)
        confirmed_rule_version = body.get("confirmed_rule_version")
        if (confirmed_rule_id is None) != (confirmed_rule_version is None):
            raise errors.InvalidRequest("confirmed_rule_id and version must be supplied together")
        if confirmed_rule_version is not None and (type(confirmed_rule_version) is not int or
                                                   confirmed_rule_version < 1):
            raise errors.InvalidRequest("confirmed_rule_version must be a positive integer")
        with db.translate_lock_errors(), db.transaction(self.conn):
            task, step = self._task_step(task_id, step_id)
            actor_kind = self._actor_host(actor_id, host_id)
            if actor_kind != "human":
                raise errors.ForbiddenActorKind("USER_CONFIRMATION requires a human actor")
            current_revision = step["revision"] if step else task["revision"]
            origin = self.events.get(origin_id)
            if origin is None:
                raise errors.NotFound("event", origin_id)
            if (origin["event_type"] != "user.prompt" or origin["actor_id"] != actor_id or
                    origin["actor_kind"] != "human" or origin["project_id"] != task["project_id"] or
                    origin["task_id"] != task_id):
                raise errors.InvalidRequest("origin must be this human's same-task user.prompt Event")
            snapshot = fingerprint.capture()
            fp_sha = self._snapshot_identity(task["project_id"], snapshot)
            payload = {"origin_event_id": origin_id, "step_id": step_id,
                       "expected_revision": expected, "fingerprint_sha256": fp_sha,
                       "confirmed_rule_id": confirmed_rule_id,
                       "confirmed_rule_version": confirmed_rule_version}
            evidence_id = ids.new_id("evd")
            event, replayed = self._event(
                event_type="evidence.confirmed", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task, payload={**payload, "evidence_id": evidence_id},
                hash_payload={key: value for key, value in payload.items()
                              if key != "fingerprint_sha256"}, event_id=event_id)
            if replayed:
                row = self.conn.execute("SELECT evidence_id FROM evidence WHERE change_event_id=?",
                                        (event["event_id"],)).fetchone()
                if row is None:
                    raise errors.EventIdConflict("confirmation replay lacks Evidence")
                return {"evidence": self._evidence(row["evidence_id"]),
                        "event": event, "replayed": True}
            if current_revision != expected:
                raise errors.RevisionConflict("confirmation revision is stale",
                                              current_revision=current_revision)
            if confirmed_rule_id is not None:
                rule = self.conn.execute("SELECT * FROM rules WHERE rule_id=?", (confirmed_rule_id,)).fetchone()
                if rule is None:
                    raise errors.NotFound("rule", confirmed_rule_id)
                if (rule["status"] != "ACTIVE" or rule["current_version"] != confirmed_rule_version or
                        not self._rule_applies(rule, task_id, task["project_id"])):
                    raise errors.InvalidRequest("confirmed Rule version is not active for this task")
            self._snapshot(task["project_id"], event["event_id"], snapshot)
            record = self._insert(
                evidence_id=evidence_id, task=task, step=step, kind="USER_CONFIRMATION",
                status="INFO", result={"confirmed": True}, source_event_id=origin_id,
                change_event_id=event["event_id"], fingerprint_sha256=fp_sha,
                artifact_uri=None, artifact_sha256=None, actor_id=actor_id,
                host_id=host_id, confirmed_rule_id=confirmed_rule_id,
                confirmed_rule_version=confirmed_rule_version)
            return {"evidence": record, "event": event, "replayed": False}

    @staticmethod
    def _rule_applies(rule: sqlite3.Row, task_id: str, project_id: str) -> bool:
        return (rule["scope_kind"] == "global" or
                rule["scope_kind"] == "project" and rule["project_id"] == project_id or
                rule["scope_kind"] == "task" and rule["task_id"] == task_id)


class CurrentEvidenceValidator:
    """StateStore's fail-closed EvidenceValidator implementation."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.store = EvidenceStore(conn, schema_version=5)

    def _rows(self, task_id: str, step_id: str | None) -> list[dict[str, Any]]:
        if step_id is None:
            rows = self.conn.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY created_at,evidence_id",
                                     (task_id,))
        else:
            rows = self.conn.execute("SELECT * FROM evidence WHERE task_id=? AND step_id=? "
                                     "ORDER BY created_at,evidence_id", (task_id, step_id))
        return [dict(row) for row in rows]

    def _source_valid(self, row: dict[str, Any], project_id: str) -> bool:
        event = self.store.events.get(row["source_event_id"])
        if event is None or event["task_id"] != row["task_id"] or event["project_id"] != project_id:
            return False
        if row["kind"] == "USER_CONFIRMATION":
            return (event["event_type"] == "user.prompt" and event["actor_kind"] == "human" and
                    event["actor_id"] == row["actor_id"])
        if (event["event_type"] != "tool.result" or event["actor_kind"] != "system" or
                event["actor_id"] != row["actor_id"]):
            return False
        source_status, _ = _tool_result(event["payload"].get("tool_response"))
        return source_status == row["status"]

    def _fresh(self, row: dict[str, Any], current: dict[str, Any], project_id: str,
               expected_revision: int, step_id: str | None, restart_seq: int) -> bool:
        stored = self.conn.execute("SELECT snapshot_json,project_id FROM workspace_fingerprints "
                                   "WHERE fingerprint_sha256=?", (row["fingerprint_sha256"],)).fetchone()
        if stored is None or stored["project_id"] != project_id:
            return False
        if fingerprint.compare(json.loads(stored["snapshot_json"]), current) != "SAME":
            return False
        if row["task_revision"] > expected_revision:
            return False
        if step_id is not None:
            if row["step_id"] != step_id or row["step_revision"] > expected_revision:
                return False
            source_seq = self.conn.execute("SELECT seq FROM events WHERE event_id=?",
                                           (row["change_event_id"],)).fetchone()
            if source_seq is None or source_seq["seq"] <= restart_seq:
                return False
        if row["kind"] == "USER_CONFIRMATION":
            if step_id is not None and row["step_revision"] != expected_revision:
                return False
            if step_id is None and row["task_revision"] != expected_revision:
                return False
        if self.store._reference_status(row) == "BROKEN_REFERENCE":
            return False
        return self._source_valid(row, project_id)

    def _match(self, requirement: dict[str, Any], row: dict[str, Any],
               *, rule_id: str | None = None, rule_version: int | None = None) -> bool:
        if row["kind"] != requirement["kind"] or row["status"] != requirement["required_result"]:
            return False
        for key in ("tool_name", "command_sha256", "artifact_sha256"):
            if key in requirement and row[key] != requirement[key]:
                return False
        if rule_id is not None and row["kind"] == "USER_CONFIRMATION":
            return row["confirmed_rule_id"] == rule_id and row["confirmed_rule_version"] == rule_version
        return True

    def _rules(self, task: dict[str, Any]) -> list[tuple[str, int, dict[str, Any], int]]:
        rows = self.conn.execute(
            "SELECT r.rule_id,r.current_version,m.requirements_json "
            "FROM rules r JOIN rule_versions v ON v.rule_id=r.rule_id AND v.version=r.current_version "
            "LEFT JOIN rule_verification_requirements m ON "
            "m.rule_id=r.rule_id AND m.version=r.current_version "
            "WHERE r.status='ACTIVE' AND (v.kind='ACCEPTANCE' OR v.enforcement='VERIFY') "
            "AND (r.scope_kind='global' OR (r.scope_kind='project' AND r.project_id=?) OR "
            "(r.scope_kind='task' AND r.task_id=?)) ORDER BY r.rule_id",
            (task["project_id"], task["task_id"]),
        ).fetchall()
        out = []
        for row in rows:
            if row["requirements_json"] is None:
                raise errors.MissingEvidence(
                    "active VERIFY/ACCEPTANCE Rule has no structured Evidence requirements",
                    rule_id=row["rule_id"], version=row["current_version"])
            activation = self.conn.execute(
                "SELECT MAX(e.seq) AS seq FROM rule_changes c JOIN events e "
                "ON e.event_id=c.change_event_id WHERE c.rule_id=? AND c.version=? "
                "AND c.status='ACTIVE'",
                (row["rule_id"], row["current_version"]),
            ).fetchone()["seq"]
            if activation is None:
                raise errors.MissingEvidence("active Rule lacks activation Event",
                                             rule_id=row["rule_id"])
            out.append((row["rule_id"], row["current_version"],
                        json.loads(row["requirements_json"]), int(activation)))
        return out

    def validate(self, request: Any) -> Any:
        if not self.conn.in_transaction:
            raise RuntimeError("Evidence validation must run inside the State write transaction")
        from .state import EvidenceValidationResult

        task, step = request.task, request.step
        task_id, step_id = task["task_id"], step["step_id"] if step else None
        current = fingerprint.capture()
        if not current["complete"]:
            raise errors.MissingEvidence("current workspace fingerprint is partial or unknown")
        restart_seq = 0
        if step_id is not None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE task_id=? "
                "AND event_type='step.transitioned' "
                "AND json_extract(payload_json,'$.step_id')=? "
                "AND json_extract(payload_json,'$.to')='IN_PROGRESS'",
                (task_id, step_id),
            ).fetchone()
            restart_seq = int(row["seq"])
        candidates = [row for row in self._rows(task_id, step_id)
                      if self._fresh(row, current, task["project_id"],
                                     request.expected_revision, step_id, restart_seq)]
        used: set[str] = set()

        def satisfy(criteria: dict[str, Any], *, rule_id: str | None = None,
                    rule_version: int | None = None, after_seq: int = 0) -> None:
            for requirement in _requirement_set(criteria)["requirements"]:
                matches = [row for row in candidates
                           if self._match(requirement, row, rule_id=rule_id,
                                          rule_version=rule_version) and
                           (after_seq == 0 or self.conn.execute(
                               "SELECT seq FROM events WHERE event_id=?",
                               (row["change_event_id"],)).fetchone()["seq"] > after_seq)]
                if not matches:
                    raise errors.MissingEvidence("Evidence requirement is not satisfied",
                                                 requirement_key=requirement["key"],
                                                 rule_id=rule_id, rule_version=rule_version)
                used.add(matches[-1]["evidence_id"])

        satisfy(request.criteria)
        versions: list[dict[str, Any]] = []
        for rule_id, version, criteria, activation_seq in self._rules(task):
            satisfy(criteria, rule_id=rule_id, rule_version=version, after_seq=activation_seq)
            versions.append({"rule_id": rule_id, "version": version})
        if request.require_user_confirmation:
            confirmations = [row for row in candidates if row["kind"] == "USER_CONFIRMATION" and
                             row["status"] in ("PASS", "INFO")]
            if not confirmations:
                raise errors.MissingEvidence("current human USER_CONFIRMATION is required")
            used.add(confirmations[-1]["evidence_id"])
        if not used:
            raise errors.MissingEvidence("no matching current Evidence")
        return EvidenceValidationResult(sorted(used), versions)
