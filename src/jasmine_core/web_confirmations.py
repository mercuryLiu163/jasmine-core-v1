"""Core-owned prepare/submit confirmation contract; see ADR 0015.

All digests cover strict original JSON. The same verification routine is used
by status, replay and the State validator. File scans never hold a transaction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import auth, clock, db, errors, fingerprint, ids
from .canonical import canonical_json
from .evidence import EvidenceStore
from .events import EventStore, _WEB_CONFIRMATION_CAPABILITY
from .models import NewEvent, WEB_SOURCE_SYSTEM
from .state import validate_criteria

CONTRACT = "web_confirmation_v1"
STATEMENT = "我确认已审阅此 Task/Step 的验收条件和适用规则，并对该材料绑定的当前版本作出人工确认。"
TRUST = "registered_human_bearer_key"
PREPARE_PATH = "/v1/evidence/confirmations/prepare"
SUBMIT_PATH = "/v1/evidence/confirmations/submit"
READ_SCOPES = ("objects:read", "state:read", "authority:read")
PREPARE_SCOPES = ("evidence:confirm", *READ_SCOPES)
STATUS_SCOPES = (*PREPARE_SCOPES, "evidence:read")
MAX_REQUEST_BYTES = 16 * 1024
MAX_REVIEW_BYTES = 256 * 1024
MAX_SCAN_BYTES = 256 * 1024 * 1024
SCAN_SECONDS = 15
REQUEST_SECONDS = 30


class ConfirmationError(errors.CoreError):
    def __init__(self, code: str, status: int = 409, **details):
        self.code, self.status = code, status
        super().__init__(code, **details)
        # A documented current_step_revision:null must remain present.
        self.details = details


def _ensure(condition):
    # Integrity checks remain active under python -O.
    if not condition:
        raise integrity_error()


def integrity_error():
    return ConfirmationError("confirmation_integrity_error", reason="stored_integrity_invalid")


def strict_json(value: Any, *, stored: bool = False) -> None:
    """Bounded iterative validation before using the legacy canonical utility."""
    pending = [(value, 0)]
    nodes = 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if depth > 32 or nodes > 50_000:
                raise ValueError("JSON limit")
            if item is None or type(item) in (bool, int):
                continue
            if type(item) is str:
                item.encode("utf-8", "strict")
            elif type(item) is float:
                if not math.isfinite(item):
                    raise ValueError("non-finite")
            elif type(item) is dict:
                if nodes + len(pending) + len(item) * 2 > 50_000:
                    raise ValueError("JSON limit")
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("JSON key")
                    pending.extend(((key, depth + 1), (child, depth + 1)))
            elif type(item) is list:
                if nodes + len(pending) + len(item) > 50_000:
                    raise ValueError("JSON limit")
                pending.extend((child, depth + 1) for child in item)
            else:
                raise ValueError("JSON type")
    except (ValueError, UnicodeError, OverflowError) as exc:
        if stored:
            raise integrity_error() from exc
        raise errors.InvalidRequest("invalid strict JSON material") from exc


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_json(raw, *, stored=False):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        strict_json(value, stored=stored)
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        if stored:
            raise integrity_error() from exc
        raise errors.InvalidRequest("invalid strict JSON body") from exc


def json_bytes(value, *, stored=True):
    strict_json(value, stored=stored)
    return canonical_json(value).encode("utf-8")


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def request_body(request):
    if request.query:
        raise errors.InvalidRequest("query parameters are not supported")
    if len(request.body) > MAX_REQUEST_BYTES:
        raise errors.PayloadTooLarge("Web confirmation body exceeds 16384 bytes")
    if (request.header("content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise errors.UnsupportedMediaType("application/json is required")
    value = parse_json(request.body)
    if type(value) is not dict:
        raise errors.InvalidRequest("body must be a JSON object")
    return value


def _id(value, prefix):
    if not ids.is_id(value, prefix):
        raise errors.InvalidRequest("invalid confirmation identifier")
    return value


def _positive(value):
    if type(value) is not int or value <= 0:
        raise errors.InvalidRequest("revision/version must be a positive integer")
    return value


def prepare_body(body):
    strict_json(body)
    allowed = {"event_id", "task_id", "step_id", "target_status", "expected_task_revision",
               "expected_step_revision", "confirmed_rule_id", "confirmed_rule_version"}
    if type(body) is not dict or set(body) - allowed:
        raise errors.InvalidRequest("unknown confirmation prepare fields")
    result = {name: body.get(name) for name in allowed}
    for name, prefix in (("event_id", "evt"), ("task_id", "tsk")):
        _id(result[name], prefix)
    _positive(result["expected_task_revision"])
    if result["target_status"] not in ("VERIFIED", "ACCEPTED"):
        raise errors.InvalidRequest("target_status must be VERIFIED or ACCEPTED")
    if result["step_id"] is not None:
        _id(result["step_id"], "stp")
        _positive(result["expected_step_revision"])
    elif result["expected_step_revision"] is not None:
        raise errors.InvalidRequest("Task confirmation must not name a Step revision")
    rule, version = result["confirmed_rule_id"], result["confirmed_rule_version"]
    if (rule is None) != (version is None):
        raise errors.InvalidRequest("confirmed Rule id/version must be paired")
    if rule is not None:
        _id(rule, "rul")
        _positive(version)
    return result


def submit_body(body):
    strict_json(body)
    if type(body) is not dict or set(body) != {"event_id", "preparation_id", "review_sha256", "affirmed", "note"}:
        raise errors.InvalidRequest("submit requires exact confirmation fields")
    for name in ("event_id", "preparation_id"):
        _id(body[name], "evt")
    if type(body["review_sha256"]) is not str or re.fullmatch("[0-9a-f]{64}", body["review_sha256"]) is None:
        raise errors.InvalidRequest("review_sha256 must be lowercase SHA256")
    if body["affirmed"] is not True:
        raise errors.InvalidRequest("affirmed must be boolean true")
    if type(body["note"]) is not str or len(body["note"].encode("utf-8")) > 4096:
        raise errors.InvalidRequest("note must be a string of at most 4096 UTF8 bytes")
    if body["event_id"] == body["preparation_id"]:
        raise errors.EventIdConflict("submit event_id must differ from preparation_id")
    return dict(body)


def semantic(principal, path, body):
    return digest({"contract_version": CONTRACT, "actor_id": principal.actor_id,
                   "key_id": principal.key_id, "method": "POST", "path": path, "body": body})


@contextmanager
def read_transaction(conn):
    if conn.in_transaction:
        raise RuntimeError("confirmation read requires its own transaction")
    conn.execute("BEGIN")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _budget(deadline):
    if time.monotonic() >= deadline:
        raise ConfirmationError("confirmation_scan_unavailable", 503, reason="timeout")


class WebConfirmationStore:
    def __init__(self, conn, *, schema_version):
        self.conn = conn
        self.schema_version = schema_version
        self.events = EventStore(conn, schema_version=schema_version)
        self.evidence = EvidenceStore(conn, schema_version=schema_version)

    def identity(self, principal, scopes=(), *, human=True):
        """Recheck current Registry and key rows, including inside writer lock."""
        key = self.conn.execute("SELECT * FROM api_keys WHERE key_id=?", (principal.key_id,)).fetchone()
        if key is None or key["revoked_at"] is not None or key["actor_id"] != principal.actor_id:
            raise errors.Unauthenticated("API key no longer valid")
        current = auth.Principal(key["actor_id"], key["key_id"], frozenset(auth.json_scopes(key["scopes"])))
        for scope in scopes:
            current.require(scope)
        actor = self.conn.execute("SELECT * FROM actors WHERE actor_id=?", (current.actor_id,)).fetchone()
        if actor is None:
            raise errors.Unauthenticated("actor no longer registered")
        if human and actor["kind"] != "human":
            raise errors.ForbiddenActorKind("Web confirmation requires registered human")
        host = actor["home_host_id"]
        if human and (host is None or self.conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (host,)).fetchone() is None):
            raise ConfirmationError("confirmation_identity_unbound", reason="home_host_missing")
        return {"actor_id": current.actor_id, "key_id": current.key_id,
                "host_id": host, "trust_model": TRUST}

    def me(self, principal):
        identity = self.identity(principal, human=False)
        actor = self.conn.execute("SELECT kind FROM actors WHERE actor_id=?", (principal.actor_id,)).fetchone()
        key = self.conn.execute("SELECT scopes FROM api_keys WHERE key_id=?", (principal.key_id,)).fetchone()
        return {"actor_id": principal.actor_id, "actor_kind": actor["kind"], "key_id": principal.key_id,
                "home_host_id": identity["host_id"], "scopes": sorted(auth.json_scopes(key["scopes"])),
                "capabilities": {CONTRACT: True}, "trust_model": TRUST}

    @staticmethod
    def owner(row, principal):
        if row["actor_id"] != principal.actor_id or row["key_id"] != principal.key_id:
            raise ConfirmationError("confirmation_owner_mismatch", 403, reason="owner_mismatch")

    def _configuration(self):
        # Environment and declared coverage identity only; never scans a file.
        root_raw = os.environ.get("JASMINE_CORE_WORKSPACE_ROOT")
        extra = parse_json(os.environ.get("JASMINE_CORE_FINGERPRINT_EXTRA_PATHS", "[]"), stored=True)
        if type(extra) is not list or any(type(x) is not str for x in extra):
            raise integrity_error()
        if not root_raw:
            raise ConfirmationError("confirmation_scan_unavailable", 503, reason="failed")
        strict_json(root_raw, stored=True)
        return {"root_configuration": root_raw, "extra_paths": sorted(extra),
                "algorithm": fingerprint.ALGORITHM, "git_coverage": fingerprint.GIT_COVERAGE,
                "plain_coverage": fingerprint.PLAIN_COVERAGE,
                "contract_version": CONTRACT, "render_version": CONTRACT, "statement": STATEMENT}

    def _scan(self, deadline):
        if self.conn.in_transaction:
            raise RuntimeError("confirmation scan must be outside all transactions")
        _budget(deadline)
        scan_deadline = min(deadline, time.monotonic() + SCAN_SECONDS)
        try:
            snapshot = fingerprint.capture(deadline=scan_deadline, max_files=20_000, max_bytes=MAX_SCAN_BYTES)
            _budget(scan_deadline)
        except TimeoutError as exc:
            raise ConfirmationError("confirmation_scan_unavailable", 503, reason="timeout") from exc
        except (errors.FingerprintUnavailable, OSError, ValueError) as exc:
            raise ConfirmationError("confirmation_scan_unavailable", 503, reason="failed") from exc
        # Incomplete scans are never hashed or persisted. Refuse coverage caps
        # before traversing large diagnostic metadata from a rejected scan.
        if type(snapshot) is dict and snapshot.get("complete") is False:
            reasons = snapshot.get("partial_reasons", [])
            strict_json(reasons, stored=True)
            if type(reasons) is not list or any(type(x) is not str for x in reasons):
                raise integrity_error()
            reason = "too_large" if any("limit" in x for x in reasons) else "partial"
            raise ConfirmationError("confirmation_scan_unavailable", 503, reason=reason)
        self._validate_snapshot(snapshot)
        if snapshot["complete"] is not True:
            raise integrity_error()
        return snapshot

    @staticmethod
    def _validate_snapshot(snapshot):
        strict_json(snapshot, stored=True)
        try:
            _ensure(type(snapshot) is dict and set(snapshot) == {"root", "git_head", "is_git", "algorithm", "coverage", "extra_paths", "dirty", "changed_paths", "selected_hashes", "manifest_sha256", "complete", "partial_reasons", "file_count", "captured_at"})
            _ensure(type(snapshot["root"]) is str and snapshot["root"])
            _ensure(snapshot["algorithm"] == fingerprint.ALGORITHM)
            _ensure(snapshot["coverage"] in (fingerprint.GIT_COVERAGE, fingerprint.PLAIN_COVERAGE))
            _ensure(type(snapshot["is_git"]) is bool and type(snapshot["complete"]) is bool)
            _ensure(snapshot["dirty"] is None or type(snapshot["dirty"]) is bool)
            _ensure(snapshot["git_head"] is None or type(snapshot["git_head"]) is str)
            for name in ("extra_paths", "changed_paths", "partial_reasons"):
                _ensure(type(snapshot[name]) is list and all(type(x) is str for x in snapshot[name]))
            _ensure(type(snapshot["selected_hashes"]) is dict)
            _ensure(all(type(x) is str and re.fullmatch("[0-9a-f]{64}", x) for x in snapshot["selected_hashes"].values()))
            _ensure(snapshot["manifest_sha256"] == digest(snapshot["selected_hashes"]))
            _ensure(type(snapshot["file_count"]) is int and snapshot["file_count"] == len(snapshot["selected_hashes"]))
            clock.parse_rfc3339(snapshot["captured_at"])
            if snapshot["complete"]:
                _ensure(not snapshot["partial_reasons"])
        except (KeyError, TypeError, ValueError) as exc:
            raise integrity_error() from exc

    def _context(self, body, identity, *, eligible=False):
        task = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (body["task_id"],)).fetchone()
        if task is None:
            raise errors.NotFound("task", body["task_id"])
        step = None
        if body["step_id"] is not None:
            step = self.conn.execute("SELECT * FROM steps WHERE step_id=?", (body["step_id"],)).fetchone()
            if step is None:
                raise errors.NotFound("step", body["step_id"])
            if step["task_id"] != task["task_id"]:
                raise errors.InvalidRequest("Step must belong to Task")
        try:
            task_doc = {"task_id": task["task_id"], "title": task["title"], "status": task["status"],
                        "revision": task["revision"], "acceptance_criteria": parse_json(task["acceptance_criteria_json"], stored=True)}
            step_doc = None if step is None else {
                "step_id": step["step_id"], "title": step["title"], "status": step["status"],
                "revision": step["revision"], "acceptance_criteria": parse_json(step["acceptance_criteria_json"], stored=True)}
            validate_criteria(task_doc["acceptance_criteria"])
            if step_doc:
                validate_criteria(step_doc["acceptance_criteria"])
        except errors.InvalidRequest as exc:
            raise integrity_error() from exc
        steps = [] if step is not None else [dict(x) for x in self.conn.execute(
            "SELECT step_id,status,revision FROM steps WHERE task_id=? ORDER BY step_id", (task["task_id"],))]
        rules = []
        for row in self.conn.execute(
            "SELECT r.*,v.kind,v.severity,v.enforcement,v.content,v.matcher_json,m.requirements_json "
            "FROM rules r JOIN rule_versions v ON r.rule_id=v.rule_id AND r.current_version=v.version "
            "LEFT JOIN rule_verification_requirements m ON m.rule_id=r.rule_id AND m.version=r.current_version "
            "WHERE r.status='ACTIVE' AND (r.scope_kind='global' OR (r.scope_kind='project' AND r.project_id=?) OR "
            "(r.scope_kind='task' AND r.task_id=?)) ORDER BY r.rule_id", (task["project_id"], task["task_id"])):
            activation = self.conn.execute(
                "SELECT c.change_event_id FROM rule_changes c JOIN events e ON e.event_id=c.change_event_id "
                "WHERE c.rule_id=? AND c.version=? AND c.status='ACTIVE' ORDER BY e.seq DESC LIMIT 1",
                (row["rule_id"], row["current_version"])).fetchone()
            if activation is None:
                raise integrity_error()
            rules.append({"rule_id": row["rule_id"], "version": row["current_version"],
                          **{key: row[key] for key in ("scope_kind", "project_id", "task_id", "kind", "severity", "enforcement", "content")},
                          "matcher": parse_json(row["matcher_json"], stored=True),
                          "verification_requirements": None if row["requirements_json"] is None else parse_json(row["requirements_json"], stored=True),
                          "activation_event_id": activation["change_event_id"]})
        confirmed = None if body["confirmed_rule_id"] is None else {
            "rule_id": body["confirmed_rule_id"], "version": body["confirmed_rule_version"]}
        result = {"actor": identity, "project_id": task["project_id"], "task": task_doc,
                  "step": step_doc, "task_steps": steps, "applicable_rules": rules,
                  "confirmed_rule": confirmed, "target_status": body["target_status"]}
        self._validate_context(result)
        if eligible:
            if (task["revision"] != body["expected_task_revision"] or
                    (step is not None and step["revision"] != body["expected_step_revision"])):
                raise ConfirmationError("revision_conflict", current_task_revision=task["revision"],
                                        current_step_revision=step["revision"] if step else None)
            reason = None
            if task["status"] != "ACTIVE":
                reason = "task_not_active"
            elif step is not None and step["status"] != ("EXECUTED" if body["target_status"] == "VERIFIED" else "VERIFIED"):
                reason = "step_target_state_mismatch"
            elif step is None and (body["target_status"] != "ACCEPTED" or not any(x["status"] == "ACCEPTED" for x in steps) or
                                  any(x["status"] not in ("ACCEPTED", "SKIPPED") for x in steps)):
                reason = "task_steps_ineligible"
            elif not (step_doc or task_doc)["acceptance_criteria"]["requirements"]:
                reason = "empty_criteria"
            if reason:
                raise ConfirmationError("confirmation_ineligible", reason=reason)
            if confirmed and not any(x["rule_id"] == confirmed["rule_id"] and x["version"] == confirmed["version"] for x in rules):
                if self.conn.execute("SELECT 1 FROM rules WHERE rule_id=?", (confirmed["rule_id"],)).fetchone() is None:
                    raise errors.NotFound("rule", confirmed["rule_id"])
                raise ConfirmationError("confirmation_context_conflict", changed=["rules"])
        return result

    @staticmethod
    def _validate_context(context):
        strict_json(context, stored=True)
        try:
            actor = context["actor"]
            _ensure(set(actor) == {"actor_id", "key_id", "host_id", "trust_model"})
            _ensure(actor["trust_model"] == TRUST)
            for key, prefix in (("actor_id", "act"), ("key_id", "key"), ("host_id", "hst")):
                _ensure(ids.is_id(actor[key], prefix))
            _ensure(ids.is_id(context["project_id"], "prj"))
            for value, prefix in ((context["task"], "tsk"), (context["step"], "stp")):
                if value is None:
                    continue
                _ensure(set(value) == {"task_id" if prefix == "tsk" else "step_id", "title", "status", "revision", "acceptance_criteria"})
                _ensure(ids.is_id(value["task_id" if prefix == "tsk" else "step_id"], prefix))
                _ensure(type(value["revision"]) is int and value["revision"] > 0)
                _ensure(type(value["title"]) is str and type(value["status"]) is str)
                validate_criteria(value["acceptance_criteria"])
            _ensure(type(context["task_steps"]) is list)
            for step in context["task_steps"]:
                _ensure(set(step) == {"step_id", "status", "revision"})
                _ensure(ids.is_id(step["step_id"], "stp") and type(step["status"]) is str)
                _ensure(type(step["revision"]) is int and step["revision"] > 0)
            _ensure(context["task_steps"] == sorted(context["task_steps"], key=lambda x: x["step_id"]))
            _ensure(type(context["applicable_rules"]) is list)
            for rule in context["applicable_rules"]:
                _ensure(set(rule) == {"rule_id", "version", "scope_kind", "project_id", "task_id", "kind", "severity", "enforcement", "content", "matcher", "verification_requirements", "activation_event_id"})
                _ensure(ids.is_id(rule["rule_id"], "rul") and ids.is_id(rule["activation_event_id"], "evt"))
                _ensure(type(rule["version"]) is int and rule["version"] > 0)
                _ensure(rule["scope_kind"] in ("global", "project", "task"))
                _ensure(rule["kind"] in ("RULE", "DECISION", "ACCEPTANCE"))
                _ensure(rule["severity"] in ("HARD", "NORMAL") and rule["enforcement"] in ("CONTEXT", "DENY", "CONFIRM", "VERIFY"))
                _ensure(type(rule["content"]) is str and type(rule["matcher"]) is dict)
                from .authority import _matcher
                _matcher(rule["matcher"], rule["enforcement"])
                if rule["scope_kind"] == "global":
                    _ensure(rule["project_id"] is None and rule["task_id"] is None)
                elif rule["scope_kind"] == "project":
                    _ensure(rule["project_id"] == context["project_id"] and rule["task_id"] is None)
                else:
                    _ensure(rule["project_id"] == context["project_id"] and rule["task_id"] == context["task"]["task_id"])
                if rule["verification_requirements"] is not None:
                    validate_criteria(rule["verification_requirements"])
                _ensure(rule["project_id"] is None or ids.is_id(rule["project_id"], "prj"))
                _ensure(rule["task_id"] is None or ids.is_id(rule["task_id"], "tsk"))
            _ensure(context["applicable_rules"] == sorted(context["applicable_rules"], key=lambda x: x["rule_id"]))
            pair = context["confirmed_rule"]
            if pair is not None:
                _ensure(set(pair) == {"rule_id", "version"} and ids.is_id(pair["rule_id"], "rul"))
                _ensure(type(pair["version"]) is int and pair["version"] > 0)
            _ensure(context["target_status"] in ("VERIFIED", "ACCEPTED"))
        except (AssertionError, KeyError, TypeError, errors.InvalidRequest) as exc:
            raise integrity_error() from exc

    def _append(self, event_type, identity, project_id, task_id, payload, *, event_id=None, source_id=None):
        strict_json(payload, stored=True)
        event, replayed = self.events.append(NewEvent(
            event_type=event_type, source_system=WEB_SOURCE_SYSTEM, source_event_id=source_id,
            occurred_at=clock.now(), actor_id=identity["actor_id"], actor_kind="human",
            host_id=identity["host_id"], project_id=project_id, task_id=task_id,
            session_id=None, payload=payload, event_id=event_id, hash_payload=None,
            client_occurred_at=None), _web_capability=_WEB_CONFIRMATION_CAPABILITY)
        if replayed:
            raise errors.EventIdConflict("Event has no matching Web confirmation projection")
        return event

    def _preparation(self, preparation_id, principal=None):
        row = self.conn.execute("SELECT * FROM web_confirmation_preparations WHERE preparation_id=?", (preparation_id,)).fetchone()
        if row is None:
            raise errors.NotFound("preparation", preparation_id)
        if principal:
            self.owner(row, principal)
        return dict(row)

    def _replay(self, event_id, principal, semantic_sha, operation):
        table, column = (("web_confirmation_preparations", "preparation_id") if operation == "prepare" else
                         ("web_confirmation_submissions", "command_event_id"))
        row = self.conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (event_id,)).fetchone()
        if row is not None:
            self.owner(row, principal)
            current_identity = self.identity(principal, PREPARE_SCOPES if operation == "prepare" else ("evidence:confirm",))
            if current_identity["host_id"] != row["host_id"]:
                raise ConfirmationError("confirmation_context_conflict", changed=["identity"])
            prep = self._preparation(row["preparation_id"], principal)
            preparation, consumption = self.verify(prep)
            if row["semantic_request_sha256"] != semantic_sha:
                raise errors.EventIdConflict("event_id already exists with different content")
            receipt = preparation if operation == "prepare" else consumption["receipt"]
            return {**receipt, "replayed": True}
        existing = self.events.get(event_id)
        if existing is not None:
            if existing["actor_id"] != principal.actor_id:
                raise ConfirmationError("confirmation_owner_mismatch", 403, reason="owner_mismatch")
            raise errors.EventIdConflict("event_id is already occupied")
        return None

    def prepare(self, body, principal, request_id, *, deadline=None):
        deadline = time.monotonic() + REQUEST_SECONDS if deadline is None else deadline
        self.identity(principal, PREPARE_SCOPES)
        body = prepare_body(body)
        semantic_sha = semantic(principal, PREPARE_PATH, body)
        with read_transaction(self.conn):
            replay = self._replay(body["event_id"], principal, semantic_sha, "prepare")
            if replay:
                return replay
            identity = self.identity(principal, PREPARE_SCOPES)
            context = self._context(body, identity, eligible=True)
            config = self._configuration()
        snapshot = self._scan(deadline)
        if snapshot["extra_paths"] != config["extra_paths"]:
            raise ConfirmationError("confirmation_context_conflict", changed=["configuration"])
        workspace = {"fingerprint_sha256": self.evidence._snapshot_identity(context["project_id"], snapshot),
                     "algorithm": snapshot["algorithm"], "coverage": snapshot["coverage"], "complete": True}
        review = {"contract_version": CONTRACT, **context, "workspace": workspace,
                  "configuration_sha256": digest(config), "statement": STATEMENT,
                  "evidence_sufficiency": "NOT_EVALUATED"}
        if len(json_bytes(review)) > MAX_REVIEW_BYTES:
            raise ConfirmationError("confirmation_review_too_large", max_bytes=MAX_REVIEW_BYTES)
        review_sha = digest(review)
        with db.translate_lock_errors(), db.transaction(self.conn):
            current_identity = self.identity(principal, PREPARE_SCOPES)
            replay = self._replay(body["event_id"], principal, semantic_sha, "prepare")
            if replay:
                return replay
            _budget(deadline)
            self._compare(context, self._context(body, current_identity), config)
            created = clock.now()
            response = {"preparation_id": body["event_id"], "contract_version": CONTRACT,
                        "review_document": review, "review_sha256": review_sha,
                        "created_at": clock.to_rfc3339(created), "expires_at": clock.to_rfc3339(created + timedelta(seconds=600)),
                        "sampled_at": snapshot["captured_at"]}
            self._append("human.confirmation_prepared", identity, context["project_id"], body["task_id"],
                         {"text": "", **response, "request_id": request_id,
                          "key_id": principal.key_id, "semantic_request_sha256": semantic_sha}, event_id=body["event_id"])
            self.evidence._snapshot(context["project_id"], body["event_id"], snapshot)
            values = {"preparation_id": body["event_id"], **{k: identity[k] for k in ("actor_id", "key_id", "host_id")},
                      "project_id": context["project_id"], **{k: body[k] for k in ("task_id", "step_id", "expected_task_revision", "expected_step_revision", "target_status", "confirmed_rule_id", "confirmed_rule_version")},
                      "fingerprint_sha256": workspace["fingerprint_sha256"], "review_json": canonical_json(review),
                      "review_sha256": review_sha, "semantic_request_sha256": semantic_sha,
                      **{k: response[k] for k in ("created_at", "expires_at", "sampled_at")}, "response_json": canonical_json(response)}
            self._insert_projection("web_confirmation_preparations", values)
            _budget(deadline)
        return {**response, "replayed": False}

    def _compare(self, old, new, config):
        changed = []
        if any(old[x] != new[x] for x in ("task", "step", "task_steps")):
            changed.append("revision")
        if old["applicable_rules"] != new["applicable_rules"]:
            changed.append("rules")
        if config != self._configuration():
            changed.append("configuration")
        if old["actor"] != new["actor"]:
            changed.append("identity")
        if changed:
            raise ConfirmationError("confirmation_context_conflict", changed=changed)

    def _insert_projection(self, table, values):
        self.conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))

    @staticmethod
    def _clock(preparation):
        now = clock.now()
        if now < clock.parse_rfc3339(preparation["created_at"]):
            raise ConfirmationError("confirmation_clock_invalid", 503, reason="clock_before_creation")
        if now >= clock.parse_rfc3339(preparation["expires_at"]):
            raise ConfirmationError("confirmation_expired", expires_at=preparation["expires_at"])

    @staticmethod
    def _consumed(consumption):
        if consumption:
            raise ConfirmationError("confirmation_consumed", **{k: consumption[k] for k in ("command_event_id", "source_event_id", "evidence_id")})

    @staticmethod
    def _body_from_preparation(prep):
        return {"event_id": prep["preparation_id"], **{key: prep[key] for key in ("task_id", "step_id", "target_status", "expected_task_revision", "expected_step_revision", "confirmed_rule_id", "confirmed_rule_version")}}

    def submit(self, body, principal, request_id, *, deadline=None):
        deadline = time.monotonic() + REQUEST_SECONDS if deadline is None else deadline
        identity = self.identity(principal, ("evidence:confirm",))
        body = submit_body(body)
        semantic_sha = semantic(principal, SUBMIT_PATH, body)
        with read_transaction(self.conn):
            replay = self._replay(body["event_id"], principal, semantic_sha, "submit")
            if replay:
                return replay
            prep = self._preparation(body["preparation_id"], principal)
            preparation, consumption = self.verify(prep)
            if body["review_sha256"] != prep["review_sha256"]:
                raise ConfirmationError("confirmation_digest_mismatch", reason="digest_mismatch")
            self._consumed(consumption)
            self._clock(prep)
            review = preparation["review_document"]
            prepare_request = self._body_from_preparation(prep)
            current_context = self._context(prepare_request, identity)
            config = self._configuration()
            self._compare(review, current_context, config)
            if digest(config) != review["configuration_sha256"]:
                raise ConfirmationError("confirmation_context_conflict", changed=["configuration"])
            snapshot_row = self.conn.execute("SELECT snapshot_json FROM workspace_fingerprints WHERE fingerprint_sha256=?", (prep["fingerprint_sha256"],)).fetchone()
            original_snapshot = parse_json(snapshot_row["snapshot_json"], stored=True)
        snapshot = self._scan(deadline)
        if fingerprint.compare(original_snapshot, snapshot) != "SAME":
            raise ConfirmationError("confirmation_context_conflict", changed=["fingerprint"])
        with db.translate_lock_errors(), db.transaction(self.conn):
            current_identity = self.identity(principal, ("evidence:confirm",))
            replay = self._replay(body["event_id"], principal, semantic_sha, "submit")
            if replay:
                return replay
            prep = self._preparation(body["preparation_id"], principal)
            preparation, consumption = self.verify(prep)
            _budget(deadline)
            self._consumed(consumption)
            self._clock(prep)
            self._compare(review, self._context(prepare_request, current_identity), config)
            binding = {"step_id": prep["step_id"], "task_revision": prep["expected_task_revision"],
                       "step_revision": prep["expected_step_revision"], "fingerprint_sha256": prep["fingerprint_sha256"],
                       "confirmed_rule_id": prep["confirmed_rule_id"], "confirmed_rule_version": prep["confirmed_rule_version"],
                       "target_status": prep["target_status"], "key_id": prep["key_id"],
                       "preparation_id": prep["preparation_id"], "review_sha256": prep["review_sha256"], "request_id": request_id}
            source = self._append("human.confirmation_submitted", identity, prep["project_id"], prep["task_id"],
                                  {"text": body["note"], "contract_version": CONTRACT, "statement": STATEMENT,
                                   "affirmed": True, "command_event_id": body["event_id"], **binding}, source_id=prep["preparation_id"])
            evidence_id = ids.new_id("evd")
            command = self._append("evidence.confirmed", identity, prep["project_id"], prep["task_id"],
                                   {"text": "", "confirmation_source_kind": "WEB_STRUCTURED_V1", "source_event_id": source["event_id"],
                                    "evidence_id": evidence_id, "semantic_request_sha256": semantic_sha, **binding},
                                   event_id=body["event_id"], source_id=source["event_id"])
            record = self.evidence._insert(evidence_id=evidence_id,
                task={"task_id": prep["task_id"], "revision": prep["expected_task_revision"]},
                step=None if prep["step_id"] is None else {"step_id": prep["step_id"], "revision": prep["expected_step_revision"]},
                kind="USER_CONFIRMATION", status="INFO", result={"confirmed": True},
                source_event_id=source["event_id"], change_event_id=command["event_id"],
                fingerprint_sha256=prep["fingerprint_sha256"], artifact_uri=None, artifact_sha256=None,
                actor_id=identity["actor_id"], host_id=identity["host_id"], confirmed_rule_id=prep["confirmed_rule_id"],
                confirmed_rule_version=prep["confirmed_rule_version"], check_reference=False)
            response = {"preparation_id": prep["preparation_id"], "contract_version": CONTRACT,
                        "review_sha256": prep["review_sha256"], "source_event": source, "event": command,
                        "evidence": record, "replayed": False}
            self._clock(prep)
            _budget(deadline)
            self._insert_projection("web_confirmation_submissions", {
                "command_event_id": command["event_id"], "preparation_id": prep["preparation_id"],
                "source_event_id": source["event_id"], "evidence_id": evidence_id,
                **{k: identity[k] for k in ("actor_id", "key_id", "host_id")},
                "semantic_request_sha256": semantic_sha, "request_id": request_id,
                "response_json": canonical_json(response), "created_at": command["recorded_at"]})
        return response

    def status(self, preparation_id, principal):
        _id(preparation_id, "evt")
        self.identity(principal, STATUS_SCOPES)
        with read_transaction(self.conn):
            self.identity(principal, STATUS_SCOPES)
            prep = self._preparation(preparation_id, principal)
            preparation, consumption = self.verify(prep)
            now = clock.now()
            availability = ("CONSUMED" if consumption else "CLOCK_INVALID" if now < clock.parse_rfc3339(prep["created_at"]) else
                            "EXPIRED" if now >= clock.parse_rfc3339(prep["expires_at"]) else "OPEN")
            return {"preparation": preparation, "consumption": consumption,
                    "availability": availability, "server_time": clock.to_rfc3339(now)}

    def _verified_event(self, event_id, event_type, identity, project_id, task_id, source_id):
        raw = self.events.get_row(event_id)
        if raw is None:
            raise integrity_error()
        # Parse stored bytes with the strict parser, including duplicate keys.
        event = self.events.get(event_id)
        event["payload"] = parse_json(raw["payload_json"], stored=True)
        strict_json(event, stored=True)
        fields = {key: event[key] for key in ("event_type", "source_system", "source_event_id", "actor_id", "host_id", "session_id", "project_id", "task_id", "payload")}
        fields["occurred_at"] = None
        if (event["body_sha256"] != digest(fields) or event["event_type"] != event_type or event["source_system"] != WEB_SOURCE_SYSTEM or
                event["actor_kind"] != "human" or event["actor_id"] != identity["actor_id"] or
                event["host_id"] != identity["host_id"] or event["project_id"] != project_id or event["task_id"] != task_id or
                event["source_event_id"] != source_id or event["session_id"] is not None or
                type(event["schema_version"]) is not int or event["schema_version"] < 12 or
                event["schema_version"] > self.schema_version):
            raise integrity_error()
        return event

    def verify(self, prep):
        """Original byte chain and immutable receipt verification. Never scans."""
        try:
            return self._verify(prep)
        except ConfirmationError:
            raise
        except (AssertionError, KeyError, TypeError, ValueError, OverflowError, errors.InvalidRequest, errors.NotFound, RecursionError) as exc:
            raise integrity_error() from exc

    def _verify(self, prep):
        review = parse_json(prep["review_json"], stored=True)
        _ensure(type(review) is dict and set(review) == {"contract_version", "actor", "project_id", "task", "step", "task_steps", "applicable_rules", "confirmed_rule", "workspace", "configuration_sha256", "target_status", "statement", "evidence_sufficiency"})
        self._validate_context(review)
        _ensure(review["contract_version"] == CONTRACT and review["statement"] == STATEMENT and review["evidence_sufficiency"] == "NOT_EVALUATED")
        _ensure(digest(review) == prep["review_sha256"] and len(json_bytes(review)) <= MAX_REVIEW_BYTES)
        _ensure(re.fullmatch("[0-9a-f]{64}", review["configuration_sha256"]))
        identity = {key: prep[key] for key in ("actor_id", "key_id", "host_id")}
        identity["trust_model"] = TRUST
        _ensure(identity == review["actor"] and review["project_id"] == prep["project_id"])
        _ensure(review["task"]["task_id"] == prep["task_id"] and review["task"]["revision"] == prep["expected_task_revision"])
        _ensure((review["step"] is None) == (prep["step_id"] is None))
        if review["step"]:
            _ensure(review["step"]["step_id"] == prep["step_id"] and review["step"]["revision"] == prep["expected_step_revision"])
        _ensure(review["confirmed_rule"] == (None if prep["confirmed_rule_id"] is None else {"rule_id": prep["confirmed_rule_id"], "version": prep["confirmed_rule_version"]}))
        _ensure(review["target_status"] == prep["target_status"])
        _ensure(set(review["workspace"]) == {"fingerprint_sha256", "algorithm", "coverage", "complete"})
        _ensure(review["workspace"]["complete"] is True)
        stored_fp = self.conn.execute("SELECT * FROM workspace_fingerprints WHERE fingerprint_sha256=?", (prep["fingerprint_sha256"],)).fetchone()
        _ensure(stored_fp is not None and stored_fp["project_id"] == prep["project_id"] and stored_fp["complete"] == 1)
        snapshot = parse_json(stored_fp["snapshot_json"], stored=True)
        self._validate_snapshot(snapshot)
        _ensure(snapshot["complete"] is True and stored_fp["root_path"] == snapshot["root"])
        _ensure(self.evidence._snapshot_identity(prep["project_id"], snapshot) == prep["fingerprint_sha256"])
        _ensure(review["workspace"] == {"fingerprint_sha256": prep["fingerprint_sha256"], "algorithm": snapshot["algorithm"], "coverage": snapshot["coverage"], "complete": True})
        _ensure(clock.parse_rfc3339(prep["expires_at"]) - clock.parse_rfc3339(prep["created_at"]) == timedelta(seconds=600))
        _ensure(clock.parse_rfc3339(prep["sampled_at"]) <= clock.parse_rfc3339(prep["created_at"]))
        response = {"preparation_id": prep["preparation_id"], "contract_version": CONTRACT,
                    "review_document": review, "review_sha256": prep["review_sha256"],
                    **{key: prep[key] for key in ("created_at", "expires_at", "sampled_at")}}
        _ensure(json_bytes(response) == json_bytes(parse_json(prep["response_json"], stored=True)))
        prepared_event = self._verified_event(prep["preparation_id"], "human.confirmation_prepared", identity, prep["project_id"], prep["task_id"], None)
        _ensure(clock.parse_rfc3339(prepared_event["recorded_at"]) >= clock.parse_rfc3339(prep["created_at"]))
        principal = auth.Principal(prep["actor_id"], prep["key_id"], frozenset())
        request_sha = semantic(principal, PREPARE_PATH, prepare_body(self._body_from_preparation(prep)))
        _ensure(request_sha == prep["semantic_request_sha256"])
        request_id = prepared_event["payload"]["request_id"]
        _ensure(type(request_id) is str)
        _ensure(json_bytes(prepared_event["payload"]) == json_bytes({"text": "", **response, "request_id": request_id,
                                              "key_id": prep["key_id"], "semantic_request_sha256": request_sha}))
        submission = self.conn.execute("SELECT * FROM web_confirmation_submissions WHERE preparation_id=?", (prep["preparation_id"],)).fetchone()
        if submission is None:
            # An orphan protected source/command cannot be treated as an OPEN success.
            _ensure(self.conn.execute("SELECT 1 FROM events WHERE source_system=? AND source_event_id=?", (WEB_SOURCE_SYSTEM, prep["preparation_id"])).fetchone() is None)
            return response, None
        sub = dict(submission)
        _ensure(all(sub[key] == prep[key] for key in ("actor_id", "key_id", "host_id")))
        source = self._verified_event(sub["source_event_id"], "human.confirmation_submitted", identity, prep["project_id"], prep["task_id"], prep["preparation_id"])
        command = self._verified_event(sub["command_event_id"], "evidence.confirmed", identity, prep["project_id"], prep["task_id"], source["event_id"])
        _ensure(prepared_event["seq"] < source["seq"] < command["seq"])
        _ensure(len({prepared_event["event_id"], source["event_id"], command["event_id"]}) == 3)
        _ensure(clock.parse_rfc3339(prep["created_at"]) <= clock.parse_rfc3339(source["recorded_at"]) < clock.parse_rfc3339(prep["expires_at"]))
        _ensure(sub["created_at"] == command["recorded_at"])
        binding = {"step_id": prep["step_id"], "task_revision": prep["expected_task_revision"],
                   "step_revision": prep["expected_step_revision"], "fingerprint_sha256": prep["fingerprint_sha256"],
                   "confirmed_rule_id": prep["confirmed_rule_id"], "confirmed_rule_version": prep["confirmed_rule_version"],
                   "target_status": prep["target_status"], "key_id": prep["key_id"], "preparation_id": prep["preparation_id"],
                   "review_sha256": prep["review_sha256"], "request_id": sub["request_id"]}
        note = source["payload"]["text"]
        body = submit_body({"event_id": command["event_id"], "preparation_id": prep["preparation_id"], "review_sha256": prep["review_sha256"], "affirmed": True, "note": note})
        submit_sha = semantic(principal, SUBMIT_PATH, body)
        _ensure(submit_sha == sub["semantic_request_sha256"])
        _ensure(json_bytes(source["payload"]) == json_bytes({"text": note, "contract_version": CONTRACT, "statement": STATEMENT,
                                      "affirmed": True, "command_event_id": command["event_id"], **binding}))
        _ensure(source["payload"]["affirmed"] is True)
        _ensure(json_bytes(command["payload"]) == json_bytes({"text": "", "confirmation_source_kind": "WEB_STRUCTURED_V1", "source_event_id": source["event_id"],
                                       "evidence_id": sub["evidence_id"], "semantic_request_sha256": submit_sha, **binding}))
        raw_evidence = self.conn.execute("SELECT * FROM evidence WHERE evidence_id=?", (sub["evidence_id"],)).fetchone()
        _ensure(raw_evidence is not None)
        record = dict(raw_evidence)
        record["result"] = parse_json(record.pop("result_json"), stored=True)
        record["reference_status"] = "NONE"
        strict_json(record, stored=True)
        expected = {"evidence_id": sub["evidence_id"], "task_id": prep["task_id"], "step_id": prep["step_id"],
                    "kind": "USER_CONFIRMATION", "status": "INFO", "result": {"confirmed": True},
                    "source_event_id": source["event_id"], "change_event_id": command["event_id"],
                    "fingerprint_sha256": prep["fingerprint_sha256"], "actor_id": prep["actor_id"], "host_id": prep["host_id"],
                    "confirmed_rule_id": prep["confirmed_rule_id"], "confirmed_rule_version": prep["confirmed_rule_version"],
                    "task_revision": prep["expected_task_revision"], "step_revision": prep["expected_step_revision"],
                    "reference_status": "NONE", "created_at": record["created_at"]}
        expected.update({key: None for key in ("artifact_uri", "artifact_sha256", "session_id", "turn_id", "tool_use_id", "tool_name", "command_sha256", "producer_config_sha256", "related_posttool_event_id")})
        _ensure(json_bytes(record) == json_bytes(expected) and record["result"]["confirmed"] is True)
        receipt = {"preparation_id": prep["preparation_id"], "contract_version": CONTRACT,
                   "review_sha256": prep["review_sha256"], "source_event": source, "event": command, "evidence": record, "replayed": False}
        _ensure(json_bytes(receipt) == json_bytes(parse_json(sub["response_json"], stored=True)))
        # The original source must postdate the Truth revision it confirms.
        cutoff = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE task_id=? AND seq<? AND "
            "event_type IN ('task.created','task.transitioned','task.criteria_updated','task.accepted',"
            "'step.created','step.transitioned','step.criteria_updated','step.staled') "
            "AND (? IS NULL OR event_type LIKE 'task.%' OR json_extract(payload_json,'$.step_id')=?)",
            (prep["task_id"], prepared_event["seq"], prep["step_id"], prep["step_id"])).fetchone()[0]
        _ensure(source["seq"] > cutoff)
        return response, {"command_event_id": sub["command_event_id"], "source_event_id": sub["source_event_id"],
                          "evidence_id": sub["evidence_id"], "receipt": receipt}

    def source_valid(self, row, source, project_id):
        try:
            sub = self.conn.execute("SELECT preparation_id FROM web_confirmation_submissions WHERE source_event_id=? AND evidence_id=? AND command_event_id=?",
                                    (source["event_id"], row["evidence_id"], row["change_event_id"])).fetchone()
            if sub is None:
                return False
            prep = self._preparation(sub["preparation_id"])
            _, consumption = self.verify(prep)
            return consumption is not None and prep["project_id"] == project_id and consumption["receipt"]["evidence"]["evidence_id"] == row["evidence_id"]
        except errors.CoreError:
            return False
