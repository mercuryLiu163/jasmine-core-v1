"""Immutable Evidence ingestion and current-workspace validation for P1-03.

Only a trusted system tool producer can create tool PASS/FAIL records. Human
confirmation is a separate API operation backed by an existing human Raw Event.
The validator reads every active applicable VERIFY/ACCEPTANCE Rule inside the
State transaction and refuses an unmapped version.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import stat
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
MAX_CODEX_JSONL_BYTES = 192 * 1024
MAX_HOOK_TRACE_BYTES = 32 * 1024


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
                if name.endswith("_sha256") and (len(item[name]) != 64 or
                        any(char not in "0123456789abcdef" for char in item[name])):
                    raise errors.InvalidRequest(f"{name} must be lowercase SHA-256 hex")
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


def _sha256(value: Any, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(char not in "0123456789abcdef" for char in value)):
        raise errors.InvalidRequest(f"{field} must be lowercase SHA-256 hex", field=field)
    return value


def _raw_jsonl(value: Any, digest: Any, field: str, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise errors.InvalidRequest(f"{field} must be nonempty UTF-8 text within its size cap", field=field)
    if hashlib.sha256(value.encode("utf-8")).hexdigest() != _sha256(digest, field + "_sha256"):
        raise errors.InvalidRequest(f"{field} digest does not match its bytes", field=field)
    try:
        lines = [json.loads(line) for line in value.splitlines()]
    except (ValueError, TypeError) as exc:
        raise errors.InvalidRequest(f"{field} contains malformed JSONL", field=field) from exc
    if not lines or any(not isinstance(item, dict) for item in lines):
        raise errors.InvalidRequest(f"{field} must contain JSON objects", field=field)
    return lines


def _observed_command(raw: Any) -> str:
    if not isinstance(raw, str):
        raise errors.InvalidRequest("Codex command_execution.command must be text")
    try:
        envelope = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise errors.InvalidRequest("Codex command envelope has invalid quoting") from exc
    if len(envelope) != 3 or envelope[0] != "/bin/bash" or envelope[1] not in ("-c", "-lc"):
        raise errors.InvalidRequest("Codex command must use the supported three-argument Bash envelope")
    return envelope[2]


def _codex_execution(lines: list[dict[str, Any]], session: str,
                     command: str) -> tuple[str, int]:
    thread = [(i, item) for i, item in enumerate(lines) if item.get("type") == "thread.started"]
    started_turn = [i for i, item in enumerate(lines) if item.get("type") == "turn.started"]
    finished_turn = [i for i, item in enumerate(lines) if item.get("type") == "turn.completed"]
    if (len(thread) != 1 or thread[0][1].get("thread_id") != session or
            len(started_turn) != 1 or len(finished_turn) != 1 or
            not thread[0][0] < started_turn[0] < finished_turn[0] or
            any(item.get("type") in ("turn.failed", "turn.cancelled") for item in lines)):
        raise errors.InvalidRequest("Codex JSONL lacks one completed session turn")
    calls: list[tuple[int, str, dict[str, Any]]] = []
    for index, event in enumerate(lines):
        phase = event.get("type")
        if phase not in ("thread.started", "turn.started", "turn.completed",
                         "item.started", "item.completed"):
            raise errors.InvalidRequest("Codex JSONL contains an unexpected event type")
        if phase in ("thread.started", "turn.started", "turn.completed"):
            if "item" in event:
                raise errors.InvalidRequest("Codex lifecycle event contains an unexpected item")
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            raise errors.InvalidRequest("Codex item event must contain an item object")
        kind = item.get("type")
        if kind not in ("agent_message", "command_execution"):
            raise errors.InvalidRequest("Codex JSONL contains an unexpected item type")
        if kind == "command_execution":
            phase = event.get("type")
            if phase not in ("item.started", "item.completed"):
                raise errors.InvalidRequest("Codex command has an unsupported lifecycle phase")
            calls.append((index, phase, item))
    if len(calls) != 2 or [entry[1] for entry in calls] != ["item.started", "item.completed"]:
        raise errors.InvalidRequest("Codex JSONL must contain one command start/completion pair")
    first, last = calls[0][2], calls[1][2]
    item_id = first.get("id")
    if (not isinstance(item_id, str) or not item_id or last.get("id") != item_id or
            first.get("command") != last.get("command") or
            _observed_command(first.get("command")) != command or
            first.get("status") != "in_progress" or last.get("status") != "completed" or
            type(last.get("exit_code")) is not int or
            not started_turn[0] < calls[0][0] < calls[1][0] < finished_turn[0]):
        raise errors.InvalidRequest("Codex command does not match one completed prepared Bash call")
    return item_id, last["exit_code"]


def _hook_chain(lines: list[dict[str, Any]], *, prompt_event_id: str,
                post_event_id: str, session: str, turn: str, use: str,
                input_sha256: str) -> None:
    if any(item.get("session_id") != session or item.get("turn_id") != turn for item in lines):
        raise errors.InvalidRequest("hook trace includes another session or turn")
    prompt = [item for item in lines if item.get("hook_event_name") == "UserPromptSubmit"]
    pre = [item for item in lines if item.get("hook_event_name") == "PreToolUse"]
    post = [item for item in lines if item.get("hook_event_name") == "PostToolUse"]
    matching_pre = [item for item in pre if item.get("tool_input_sha256") == input_sha256]
    if (len(prompt) != 1 or prompt[0].get("result") != "captured" or
            prompt[0].get("event_id") != prompt_event_id or len(matching_pre) != 1 or
            matching_pre[0].get("tool_use_id") != use or
            matching_pre[0].get("result") not in ("guard:allow", "guard:verify") or
            len(post) != 1 or post[0].get("result") != "captured" or
            post[0].get("event_id") != post_event_id or
            post[0].get("tool_use_id") != use or
            post[0].get("tool_input_sha256") != input_sha256 or
            any(item.get("tool_input_sha256") == input_sha256 and item is not matching_pre[0]
                for item in pre)):
        raise errors.InvalidRequest("hook trace does not contain one linked prompt/Pre/Post chain")
    if len(pre) != 1 or len(lines) != 3 or [item.get("hook_event_name") for item in lines] != [
            "UserPromptSubmit", "PreToolUse", "PostToolUse"]:
        raise errors.InvalidRequest("hook trace contains another tool attempt")


def _producer_kind(kind: str, tool_name: str, command_sha256: str | None) -> str | None:
    if kind == "COMMAND_RESULT":
        return None
    config_path = os.environ.get("JASMINE_CORE_EVIDENCE_PRODUCERS")
    if not config_path:
        raise errors.InvalidRequest("specialized Evidence kind has no trusted producer mapping")
    try:
        path = Path(config_path)
        if (not path.is_absolute() or path != path.resolve(strict=True) or
                path.resolve().is_relative_to(fingerprint.configured_root())):
            raise ValueError("producer mapping must be outside the workspace")
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("producer mapping directory must be owner-only")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or
                    metadata.st_mode & 0o077 or metadata.st_size > 64 * 1024):
                raise ValueError("producer mapping must be a private regular file")
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                raw = stream.read(64 * 1024 + 1)
        finally:
            os.close(descriptor)
        mappings = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise errors.FingerprintUnavailable("trusted Evidence producer config is unavailable") from exc
    if not isinstance(mappings, list) or not all(isinstance(item, dict) for item in mappings):
        raise errors.FingerprintUnavailable("trusted Evidence producer config is invalid")
    if not any(item.get("kind") == kind and item.get("tool_name") == tool_name and
               item.get("command_sha256") == command_sha256 for item in mappings):
        raise errors.InvalidRequest("tool result is not mapped to this Evidence kind")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
                              "algorithm": snapshot["algorithm"], "coverage": snapshot["coverage"],
                              "extra_paths": snapshot["extra_paths"],
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
            if actor_kind != "system":
                raise errors.ForbiddenActorKind("only a trusted system actor may refresh a workspace")
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
            staled = self._stale_verified(project_id, snapshot, actor_id=actor_id,
                                          actor_kind=actor_kind, host_id=host_id)
        return {"fingerprint_sha256": digest, "snapshot": snapshot, "staled_steps": staled}

    def _stale_verified(self, project_id: str, current: dict[str, Any], *, actor_id: str,
                        actor_kind: str, host_id: str) -> list[str]:
        """Persist invalidation after an explicit trusted refresh, Event first."""
        staled: list[str] = []
        rows = self.conn.execute(
            "SELECT s.*,t.project_id,t.revision AS task_revision FROM steps s "
            "JOIN tasks t ON t.task_id=s.task_id WHERE t.project_id=? "
            "AND s.status='VERIFIED' ORDER BY s.step_id", (project_id,),
        ).fetchall()
        for row in rows:
            step = dict(row)
            state_event = self.conn.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND event_type='step.transitioned' "
                "AND json_extract(payload_json,'$.step_id')=? "
                "AND json_extract(payload_json,'$.to')='VERIFIED' ORDER BY seq DESC LIMIT 1",
                (step["task_id"], step["step_id"]),
            ).fetchone()
            references = json.loads(state_event["payload_json"]) if state_event else {}
            evidence_ids = references.get("evidence_ids", [])
            invalid = not current["complete"] or not evidence_ids
            for evidence_id in evidence_ids:
                evidence_row = self.conn.execute(
                    "SELECT * FROM evidence WHERE evidence_id=? AND task_id=? AND step_id=?",
                    (evidence_id, step["task_id"], step["step_id"]),
                ).fetchone()
                if evidence_row is None:
                    invalid = True
                    break
                evidence = dict(evidence_row)
                stored = self.conn.execute(
                    "SELECT snapshot_json FROM workspace_fingerprints WHERE fingerprint_sha256=?",
                    (evidence["fingerprint_sha256"],),
                ).fetchone()
                if (stored is None or fingerprint.compare(json.loads(stored["snapshot_json"]), current) != "SAME" or
                        self._reference_status(evidence) == "BROKEN_REFERENCE"):
                    invalid = True
                    break
                if evidence["kind"] not in ("COMMAND_RESULT", "USER_CONFIRMATION"):
                    try:
                        mapped_sha = _producer_kind(evidence["kind"], evidence["tool_name"],
                                                    evidence["command_sha256"])
                    except (errors.InvalidRequest, errors.FingerprintUnavailable):
                        invalid = True
                        break
                    if evidence["producer_config_sha256"] != mapped_sha:
                        invalid = True
                        break
            for version in references.get("rule_versions", []):
                active = self.conn.execute(
                    "SELECT 1 FROM rules WHERE rule_id=? AND current_version=? AND status='ACTIVE'",
                    (version.get("rule_id"), version.get("version")),
                ).fetchone()
                if active is None:
                    invalid = True
            if not invalid:
                continue
            task = {"task_id": step["task_id"], "project_id": project_id}
            event, _ = self._event(
                event_type="step.staled", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task,
                payload={"step_id": step["step_id"], "from": "VERIFIED", "to": "STALE",
                         "previous_revision": step["revision"],
                         "previous_evidence_ids": evidence_ids,
                         "current_fingerprint_sha256": self._snapshot_identity(project_id, current)},
            )
            now = event["recorded_at"]
            self.conn.execute("UPDATE steps SET status='STALE',revision=revision+1,updated_at=? "
                              "WHERE step_id=? AND status='VERIFIED'", (now, step["step_id"]))
            self.conn.execute("UPDATE tasks SET revision=revision+1,updated_at=? WHERE task_id=?",
                              (now, step["task_id"]))
            staled.append(step["step_id"])
        return staled

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
        try:
            digest, _ = fingerprint.hash_workspace_file(root, relative)
        except (OSError, ValueError):
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
        try:
            digest, _ = fingerprint.hash_workspace_file(root, relative)
            return uri, digest
        except (OSError, ValueError):
            return uri, None

    def _insert(self, *, evidence_id: str, task: dict[str, Any], step: dict[str, Any] | None,
                kind: str, status: str, result: dict[str, Any], source_event_id: str,
                change_event_id: str, fingerprint_sha256: str, artifact_uri: str | None,
                artifact_sha256: str | None, actor_id: str, host_id: str,
                session_id: str | None = None, turn_id: str | None = None,
                tool_use_id: str | None = None, tool_name: str | None = None,
                command_sha256: str | None = None, producer_config_sha256: str | None = None,
                confirmed_rule_id: str | None = None,
                confirmed_rule_version: int | None = None,
                related_posttool_event_id: str | None = None) -> dict[str, Any]:
        self.conn.execute(
            "INSERT INTO evidence (evidence_id,task_id,step_id,kind,status,result_json,"
            "source_event_id,change_event_id,fingerprint_sha256,artifact_uri,artifact_sha256,"
            "actor_id,host_id,session_id,turn_id,tool_use_id,tool_name,command_sha256,producer_config_sha256,"
            "confirmed_rule_id,confirmed_rule_version,task_revision,step_revision,created_at,"
            "related_posttool_event_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, task["task_id"], step["step_id"] if step else None,
             kind, status, canonical_json(result), source_event_id, change_event_id,
             fingerprint_sha256, artifact_uri, artifact_sha256, actor_id, host_id,
             session_id, turn_id, tool_use_id, tool_name, command_sha256, producer_config_sha256,
             confirmed_rule_id, confirmed_rule_version, task["revision"],
             step["revision"] if step else None, clock.now_rfc3339(), related_posttool_event_id),
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
        command = tool_input.get("cmd", tool_input.get("command"))
        command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest() if isinstance(command, str) else None
        producer_config_sha256 = _producer_kind(kind, tool_name, command_sha256)
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
                source_system="codex-posttool", source_event_id=canonical_json([*seed, "call"]),
                event_id=_deterministic_event_id(*seed, "call"),
                payload={"codex_session_id": codex_session_id, "turn_id": turn_id,
                         "tool_use_id": tool_use_id, "tool_name": tool_name,
                         "tool_input": tool_input, "step_id": step_id})
            result_event, result_replay = self._event(
                event_type="tool.result", actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, task=task, session_id=session_id,
                source_system="codex-posttool", source_event_id=canonical_json([*seed, "result"]),
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
                source_system="codex-posttool", source_event_id=canonical_json([*seed, "evidence"]),
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
                                  command_sha256=command_sha256,
                                  producer_config_sha256=producer_config_sha256)
            return {"evidence": record, "call_event": call_event,
                    "result_event": result_event, "replayed": False}

    def record_codex_exec_observation(self, body: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        """Attest one complete native CLI turn against one existing Post INFO result.

        The CLI item has no Hook use ID. The caller is a trusted local runner
        asserting that both full captures came from the same invocation; Core
        refuses every ambiguous chain within those captures.
        """
        _fields(body, {"task_id", "step_id", "host_id", "origin_prompt_event_id",
                       "related_posttool_event_id", "codex_jsonl", "codex_jsonl_sha256",
                       "hook_trace_jsonl", "hook_trace_sha256", "codex_executable"})
        task_id = _id(body.get("task_id"), "tsk", "task_id")
        step_id = _id(body.get("step_id"), "stp", "step_id")
        host_id = _id(body.get("host_id"), "hst", "host_id")
        prompt_id = _id(body.get("origin_prompt_event_id"), "evt", "origin_prompt_event_id")
        post_id = _id(body.get("related_posttool_event_id"), "evt", "related_posttool_event_id")
        executable = body.get("codex_executable")
        if not isinstance(executable, dict) or set(executable) != {"path", "version", "sha256"}:
            raise errors.InvalidRequest("codex_executable requires path, version, sha256")
        path = _text(executable["path"], "codex_executable.path", limit=4096)
        if not Path(path).is_absolute() or os.path.normpath(path) != path or os.path.realpath(path) != path:
            raise errors.InvalidRequest("codex_executable.path must be a resolved absolute path")
        _text(executable["version"], "codex_executable.version")
        _sha256(executable["sha256"], "codex_executable.sha256")
        cli = _raw_jsonl(body.get("codex_jsonl"), body.get("codex_jsonl_sha256"),
                         "codex_jsonl", MAX_CODEX_JSONL_BYTES)
        trace = _raw_jsonl(body.get("hook_trace_jsonl"), body.get("hook_trace_sha256"),
                           "hook_trace_jsonl", MAX_HOOK_TRACE_BYTES)
        request_digest = body_hash(body)
        with db.translate_lock_errors(), db.transaction(self.conn):
            task, step = self._task_step(task_id, step_id)
            if self._actor_host(actor_id, host_id) != "system":
                raise errors.ForbiddenActorKind("native observation requires a system actor")
            existing = self.conn.execute(
                "SELECT evidence_id,source_event_id FROM evidence WHERE related_posttool_event_id=?",
                (post_id,)).fetchone()
            if existing is not None:
                event = self.events.get(existing["source_event_id"])
                if (event is None or event["payload"].get("observation_request_sha256") != request_digest or
                        event["actor_id"] != actor_id or event["host_id"] != host_id or
                        event["task_id"] != task_id or event["project_id"] != task["project_id"] or
                        event["payload"].get("step_id") != step_id):
                    raise errors.EventIdConflict("PostToolUse already has another native observation")
                return {"evidence": self._evidence(existing["evidence_id"]),
                        "result_event": event, "related_posttool_event_id": post_id,
                        "replayed": True}

            prompt, post = self.events.get(prompt_id), self.events.get(post_id)
            if prompt is None or post is None:
                raise errors.InvalidRequest("prompt or PostToolUse Event is missing")
            pp, rp = prompt["payload"], post["payload"]
            if (prompt["event_type"] != "user.prompt" or prompt["source_system"] != "codex-p1-bound" or
                    prompt["actor_kind"] != "human" or prompt["task_id"] != task_id or
                    prompt["project_id"] != task["project_id"] or prompt["host_id"] != host_id or
                    pp.get("step_id") != step_id or
                    post["event_type"] != "tool.result" or post["source_system"] != "codex-posttool" or
                    post["actor_kind"] != "system" or post["actor_id"] != actor_id or
                    post["task_id"] != task_id or post["project_id"] != task["project_id"] or
                    post["host_id"] != host_id or rp.get("step_id") != step_id or
                    post["seq"] <= prompt["seq"]):
                raise errors.InvalidRequest("prompt and PostToolUse are not one bound task chain")
            session = pp.get("source_session_id")
            turn = pp.get("turn_id")
            use = rp.get("tool_use_id")
            if (not all(isinstance(x, str) and x for x in (session, turn, use)) or
                    rp.get("codex_session_id") != session or rp.get("turn_id") != turn):
                raise errors.InvalidRequest("PostToolUse session/turn does not match prompt")
            call = self.events.get(rp.get("call_event_id")) if ids.is_id(rp.get("call_event_id"), "evt") else None
            if call is None or call["event_type"] != "tool.call" or call["source_system"] != "codex-posttool" or \
                    call["actor_id"] != actor_id or call["task_id"] != task_id or \
                    call["project_id"] != task["project_id"] or call["host_id"] != host_id or \
                    call["seq"] >= post["seq"]:
                raise errors.InvalidRequest("PostToolUse linked call is invalid")
            cp = call["payload"]
            if any(cp.get(key) != rp.get(key) for key in ("codex_session_id", "turn_id", "tool_use_id", "tool_name", "step_id")):
                raise errors.InvalidRequest("PostToolUse call/result identity differs")
            tool_input = cp.get("tool_input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if cp.get("tool_name") != "Bash" or not isinstance(command, str) or not command:
                raise errors.InvalidRequest("linked call is not a Bash command")
            input_sha = hashlib.sha256(canonical_json(tool_input).encode()).hexdigest()
            _hook_chain(trace, prompt_event_id=prompt_id, post_event_id=post_id,
                        session=session, turn=turn, use=use, input_sha256=input_sha)
            item_id, exit_code = _codex_execution(cli, session, command)
            old = self.conn.execute("SELECT * FROM evidence WHERE source_event_id=?", (post_id,)).fetchall()
            if len(old) != 1 or old[0]["kind"] != "COMMAND_RESULT" or old[0]["status"] != "INFO" or \
                    old[0]["step_id"] != step_id or old[0]["task_id"] != task_id or \
                    old[0]["actor_id"] != actor_id or old[0]["host_id"] != host_id or \
                    old[0]["turn_id"] != turn or old[0]["tool_use_id"] != use or \
                    old[0]["tool_name"] != "Bash" or \
                    old[0]["command_sha256"] != hashlib.sha256(command.encode()).hexdigest():
                raise errors.InvalidRequest("original PostToolUse INFO Evidence does not match")
            if (task["revision"] != old[0]["task_revision"] or
                    step["revision"] != old[0]["step_revision"]):
                raise errors.RevisionConflict("task or step changed since PostToolUse")
            stored = self.conn.execute("SELECT snapshot_json FROM workspace_fingerprints WHERE fingerprint_sha256=?",
                                       (old[0]["fingerprint_sha256"],)).fetchone()
            current = fingerprint.capture()
            if stored is None or fingerprint.compare(json.loads(stored["snapshot_json"]), current) != "SAME":
                raise errors.MissingEvidence("workspace changed since PostToolUse")
            status = "PASS" if exit_code == 0 else "FAIL"
            command_sha = hashlib.sha256(command.encode()).hexdigest()
            # The full source bytes live in the immutable Event. A smaller
            # semantic hash keeps the Event identity stable on exact replay.
            payload = {"origin_prompt_event_id": prompt_id, "related_posttool_event_id": post_id,
                       "call_event_id": call["event_id"], "native_item_id": item_id,
                       "codex_session_id": session, "turn_id": turn, "tool_use_id": use,
                       "tool_name": "Bash", "command_sha256": command_sha,
                       "exit_code": exit_code, "codex_executable": executable,
                       "codex_jsonl": body["codex_jsonl"], "codex_jsonl_sha256": body["codex_jsonl_sha256"],
                       "hook_trace_jsonl": body["hook_trace_jsonl"],
                       "hook_trace_sha256": body["hook_trace_sha256"],
                       "observation_request_sha256": request_digest, "step_id": step_id}
            if len(canonical_json({"text": "", **payload}).encode()) > 256 * 1024:
                raise errors.InvalidRequest("native observation exceeds Event body cap")
            seed = ("codex-exec-jsonl-v1", post_id)
            result_event, replayed = self._event(
                event_type="tool.result", actor_id=actor_id, actor_kind="system", host_id=host_id,
                task=task, source_system="codex-exec-jsonl", session_id=post["session_id"],
                source_event_id=canonical_json(seed), event_id=_deterministic_event_id(*seed), payload=payload)
            if replayed:
                raise errors.EventIdConflict("native result replay state is inconsistent")
            fp_sha, _ = self._snapshot(task["project_id"], result_event["event_id"], current)
            evidence_id = ids.new_id("evd")
            evidence_event, _ = self._event(
                event_type="evidence.recorded", actor_id=actor_id, actor_kind="system",
                host_id=host_id, task=task, session_id=post["session_id"],
                source_system="codex-exec-jsonl", source_event_id=canonical_json([*seed, "evidence"]),
                event_id=_deterministic_event_id(*seed, "evidence"),
                payload={"evidence_id": evidence_id, "source_event_id": result_event["event_id"],
                         "related_posttool_event_id": post_id, "kind": "COMMAND_RESULT",
                         "status": status, "step_id": step_id, "fingerprint_sha256": fp_sha})
            record = self._insert(
                evidence_id=evidence_id, task=task, step=step, kind="COMMAND_RESULT", status=status,
                result={"exit_code": exit_code, "native_item_id": item_id,
                        "codex_jsonl_sha256": body["codex_jsonl_sha256"],
                        "hook_trace_sha256": body["hook_trace_sha256"]},
                source_event_id=result_event["event_id"], change_event_id=evidence_event["event_id"],
                fingerprint_sha256=fp_sha, artifact_uri=None, artifact_sha256=None,
                actor_id=actor_id, host_id=host_id, session_id=post["session_id"], turn_id=turn,
                tool_use_id=use, tool_name="Bash", command_sha256=command_sha,
                producer_config_sha256=None, related_posttool_event_id=post_id)
            return {"evidence": record, "result_event": result_event,
                    "related_posttool_event_id": post_id, "replayed": False}

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
            if step_id is not None and origin["payload"].get("step_id") != step_id:
                raise errors.InvalidRequest("origin user.prompt does not name this Step")
            if step is not None:
                revision_source = self.conn.execute(
                    "SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE task_id=? "
                    "AND event_type IN ('step.created','step.transitioned','step.criteria_updated',"
                    "'step.staled') AND json_extract(payload_json,'$.step_id')=?",
                    (task_id, step_id),
                ).fetchone()["seq"]
            else:
                revision_source = self.conn.execute(
                    "SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE task_id=? "
                    "AND event_type IN ('task.created','task.transitioned','task.criteria_updated',"
                    "'step.created','step.transitioned','step.criteria_updated','step.staled')",
                    (task_id,),
                ).fetchone()["seq"]
            if origin["seq"] <= revision_source:
                raise errors.InvalidRequest("origin user.prompt predates the current State revision")
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
        if row.get("related_posttool_event_id") is not None:
            return self._native_source_valid(row, event)
        source_status, _ = _tool_result(event["payload"].get("tool_response"))
        return source_status == row["status"]

    def _native_source_valid(self, row: dict[str, Any], event: dict[str, Any]) -> bool:
        """Reparse the stored complete sources before a State write trusts PASS."""
        if event["source_system"] != "codex-exec-jsonl":
            return False
        p = event["payload"]
        post = self.store.events.get(row["related_posttool_event_id"])
        prompt = self.store.events.get(p.get("origin_prompt_event_id")) if ids.is_id(
            p.get("origin_prompt_event_id"), "evt") else None
        call = self.store.events.get(p.get("call_event_id")) if ids.is_id(
            p.get("call_event_id"), "evt") else None
        if (post is None or prompt is None or call is None or
                post["event_type"] != "tool.result" or post["source_system"] != "codex-posttool" or
                prompt["event_type"] != "user.prompt" or prompt["source_system"] != "codex-p1-bound" or
                call["event_type"] != "tool.call" or call["source_system"] != "codex-posttool" or
                post["payload"].get("call_event_id") != call["event_id"] or
                p.get("related_posttool_event_id") != post["event_id"] or
                p.get("step_id") != row["step_id"] or
                any(item["task_id"] != row["task_id"] or item["project_id"] != event["project_id"]
                    for item in (post, prompt, call)) or
                any(item["host_id"] != row["host_id"] for item in (post, prompt, call)) or
                post["actor_id"] != row["actor_id"] or call["actor_id"] != row["actor_id"]):
            return False
        cp, rp, pp = call["payload"], post["payload"], prompt["payload"]
        command = cp.get("tool_input", {}).get("command") if isinstance(cp.get("tool_input"), dict) else None
        if (not isinstance(command, str) or cp.get("tool_name") != "Bash" or
                rp.get("tool_name") != "Bash" or
                any(cp.get(k) != rp.get(k) for k in ("codex_session_id", "turn_id", "tool_use_id", "step_id")) or
                pp.get("source_session_id") != rp.get("codex_session_id") or
                pp.get("turn_id") != rp.get("turn_id") or
                p.get("codex_session_id") != rp.get("codex_session_id") or
                p.get("turn_id") != rp.get("turn_id") or p.get("tool_use_id") != rp.get("tool_use_id") or
                row["turn_id"] != rp.get("turn_id") or row["tool_use_id"] != rp.get("tool_use_id") or
                row["command_sha256"] != hashlib.sha256(command.encode()).hexdigest()):
            return False
        old = self.conn.execute("SELECT * FROM evidence WHERE source_event_id=?", (post["event_id"],)).fetchall()
        if len(old) != 1 or old[0]["status"] != "INFO" or old[0]["kind"] != "COMMAND_RESULT" or \
                old[0]["fingerprint_sha256"] != row["fingerprint_sha256"]:
            return False
        try:
            cli = _raw_jsonl(p.get("codex_jsonl"), p.get("codex_jsonl_sha256"),
                             "codex_jsonl", MAX_CODEX_JSONL_BYTES)
            trace = _raw_jsonl(p.get("hook_trace_jsonl"), p.get("hook_trace_sha256"),
                               "hook_trace_jsonl", MAX_HOOK_TRACE_BYTES)
            _hook_chain(trace, prompt_event_id=prompt["event_id"], post_event_id=post["event_id"],
                        session=rp["codex_session_id"], turn=rp["turn_id"],
                        use=rp["tool_use_id"], input_sha256=hashlib.sha256(
                            canonical_json(cp["tool_input"]).encode()).hexdigest())
            item_id, code = _codex_execution(cli, rp["codex_session_id"], command)
        except (errors.InvalidRequest, KeyError, TypeError):
            return False
        return (item_id == p.get("native_item_id") and code == p.get("exit_code") and
                row["status"] == ("PASS" if code == 0 else "FAIL"))

    def _fresh(self, row: dict[str, Any], current: dict[str, Any], project_id: str,
               expected_revision: int, task_revision: int,
               step_id: str | None, restart_seq: int) -> bool:
        stored = self.conn.execute("SELECT snapshot_json,project_id FROM workspace_fingerprints "
                                   "WHERE fingerprint_sha256=?", (row["fingerprint_sha256"],)).fetchone()
        if stored is None or stored["project_id"] != project_id:
            return False
        if fingerprint.compare(json.loads(stored["snapshot_json"]), current) != "SAME":
            return False
        if row["task_revision"] > (task_revision if step_id is not None else expected_revision):
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
        if row["kind"] not in ("COMMAND_RESULT", "USER_CONFIRMATION"):
            try:
                current_producer = _producer_kind(row["kind"], row["tool_name"],
                                                  row["command_sha256"])
            except (errors.InvalidRequest, errors.FingerprintUnavailable):
                return False
            if row["producer_config_sha256"] != current_producer:
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
        try:
            current = fingerprint.capture()
        except errors.FingerprintUnavailable as exc:
            raise errors.MissingEvidence("current workspace fingerprint is unavailable") from exc
        if not current["complete"]:
            raise errors.MissingEvidence("current workspace fingerprint is partial or unknown")
        def cycle_cutoff(row: dict[str, Any]) -> int:
            owner_step = step_id or row["step_id"]
            if owner_step is not None:
                return int(self.conn.execute(
                    "SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE task_id=? "
                    "AND json_extract(payload_json,'$.step_id')=? "
                    "AND (event_type='step.criteria_updated' OR "
                    "(event_type='step.transitioned' AND "
                    "json_extract(payload_json,'$.to')='IN_PROGRESS'))",
                    (task_id, owner_step),
                ).fetchone()["seq"])
            # Task-scoped Evidence cannot predate a changed Task criterion or a
            # later Step execution cycle. Step Evidence uses its own cycle.
            return int(self.conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE task_id=? AND "
                "(event_type='task.criteria_updated' OR (event_type='step.transitioned' "
                "AND json_extract(payload_json,'$.to')='IN_PROGRESS'))",
                (task_id,),
            ).fetchone()["seq"])
        candidates = [row for row in self._rows(task_id, step_id)
                      if self._fresh(row, current, task["project_id"],
                                     request.expected_revision, task["revision"],
                                     step_id, cycle_cutoff(row))]
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
