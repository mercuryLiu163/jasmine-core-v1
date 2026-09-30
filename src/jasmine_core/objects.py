"""The three minimal P0 projections: project, task, session.

Every create runs inside one transaction and always follows
`EventStore.append` (ADR 0002 §2.2: Event first, projection second, one commit).
The projection's `source_event_id` is `UNIQUE` and its foreign key to `events` is
not deferred, so a projection cannot exist without exactly one originating Event.
Cross-kind sharing is closed by the `trg_*_source_event_kind` triggers in
`m0001_baseline`, because `UNIQUE` alone is only per table.

P0 deliberately exposes no update or delete: the Task state machine belongs to
P1, and there is no endpoint that could contradict an append-only Event.
"""

from __future__ import annotations

import sqlite3
import json
from dataclasses import replace
from typing import Any

from . import db, errors, ids, registry
from .canonical import canonical_json
from .events import EventStore
from .models import NewEvent, NewObject

TABLES = {"project": "projects", "task": "tasks", "session": "sessions"}
ID_PREFIX = {"project": "prj", "task": "tsk", "session": "ses"}
PRIMARY_KEY = {"project": "project_id", "task": "task_id", "session": "session_id"}
COLUMNS = {
    "project": frozenset({"project_id", "name", "description", "status", "revision",
                          "source_event_id", "created_at", "updated_at"}),
    "task": frozenset({"task_id", "project_id", "title", "description", "status", "revision",
                       "source_event_id", "created_at", "updated_at"}),
    "session": frozenset({"session_id", "project_id", "task_id", "host_id", "actor_id", "status",
                          "revision", "source_event_id", "started_at", "ended_at",
                          "created_at", "updated_at"}),
}

#: Server-assigned keys that live in the stored payload but are excluded from the
#: idempotency hash, so a retried create of the same object hashes identically
#: even though the server allocates a fresh object_id each time (ADR 0003 §1.3).
_HASH_EXCLUDED = frozenset({"object_id", "object_kind", "name_sha256"})


def _hash_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _HASH_EXCLUDED}


class ObjectStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int) -> None:
        self._conn = conn
        self.events = EventStore(conn, schema_version=schema_version)
        self.registry = registry.Registry(conn)

    # -- reading -----------------------------------------------------------

    def get(self, kind: str, object_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT * FROM {TABLES[kind]} WHERE {PRIMARY_KEY[kind]} = ?", (object_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if kind == "task" and "acceptance_criteria_json" in result:
            result["acceptance_criteria"] = json.loads(result.pop("acceptance_criteria_json"))
        return result

    def require(self, kind: str, object_id: str) -> dict[str, Any]:
        found = self.get(kind, object_id)
        if found is None:
            raise errors.NotFound(kind, object_id)
        return found

    def list(self, kind: str, *, project_id: str | None = None, task_id: str | None = None,
             limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None and "project_id" in COLUMNS[kind]:
            clauses.append("project_id = ?")
            params.append(project_id)
        if task_id is not None and "task_id" in COLUMNS[kind]:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([max(1, min(limit, 1000)), max(0, offset)])
        rows = self._conn.execute(
            f"SELECT * FROM {TABLES[kind]}{where} ORDER BY created_at, {PRIMARY_KEY[kind]}"
            " LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self.get(kind, row[PRIMARY_KEY[kind]]) for row in rows]

    def count(self, kind: str) -> int:
        return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {TABLES[kind]}").fetchone()["n"])

    # -- writing -----------------------------------------------------------

    def create(self, spec: NewObject, *, actor_id: str, unit_of_work=None) -> dict[str, Any]:
        """Create one object and its source Event in a single transaction.

        ``actor_id`` is the *authenticated* identity. A body that names a
        different actor is refused with `403 actor_mismatch` (ADR 0004 §1.3), and
        the Event's ``actor_kind`` is read from the registry rather than trusted
        from the request, so an append-only row can never misstate who acted.

        Returns ``{"object", "event", "replayed"}``. On a replay the originally
        stored object is returned unchanged and no row is written.
        """
        with db.transaction(self._conn, unit_of_work=unit_of_work):
            actor = self.registry.require_actor(actor_id)
            if spec.actor_id is not None and spec.actor_id != actor_id:
                raise errors.ActorMismatch(
                    "the body names a different actor than the authenticated one",
                    authenticated_actor_id=actor_id,
                    body_actor_id=spec.actor_id,
                )
            self.registry.require_host(spec.host_id or "")
            # References are resolved before the Event is written so a bad
            # reference costs no Event at all, and so a session that names only a
            # task still gets a project column for ADR 0003 §1.2 filtering.
            spec = self._check_references(spec)
            if spec.kind == "task":
                from .state import validate_criteria
                criteria = validate_criteria(spec.acceptance_criteria)
                if criteria["requirements"] and actor["kind"] not in ("human", "system"):
                    raise errors.ForbiddenActorKind("only human or system may set Task acceptance requirements")
            object_id = ids.new_id(ID_PREFIX[spec.kind])
            event, replayed = self.events.append(
                self._event_spec(spec, object_id, actor_id, actor["kind"])
            )
            if replayed:
                return {"object": self._replayed_object(spec, event),
                        "event": event, "replayed": True}
            record = self._insert_projection(spec, object_id, event)
            return {"object": record, "event": event, "replayed": False}

    def _replayed_object(self, spec: NewObject, event: dict[str, Any]) -> dict[str, Any]:
        stored_id = event["payload"].get("object_id")
        if event["payload"].get("object_kind") != spec.kind or not isinstance(stored_id, str):
            # The hash matched but this id belongs to something else, which cannot
            # happen through this code path; refuse rather than guess.
            raise errors.EventIdConflict(
                "event_id is already in use by a different object",
                existing_event_id=event["event_id"],
                existing_body_sha256=event["body_sha256"],
            )
        found = self.get(spec.kind, stored_id)
        if found is None:
            raise errors.EventIdConflict(
                "event_id was already stored but its object is missing; "
                "refusing to fabricate a second one",
                existing_event_id=event["event_id"],
                existing_body_sha256=event["body_sha256"],
            )
        if spec.kind == "task":
            # The Task may since have acquired Steps, a new revision, or a new
            # status. A create replay is the original created snapshot.
            payload = event["payload"]
            return {
                "task_id": stored_id, "project_id": payload["project_id"],
                "title": payload["name"], "description": payload["description"],
                "status": "ACTIVE", "revision": 1,
                "acceptance_criteria": payload.get("acceptance_criteria") or {"requirements": []},
                "source_event_id": event["event_id"],
                "created_at": event["recorded_at"], "updated_at": event["recorded_at"],
            }
        return found

    def _event_spec(self, spec: NewObject, object_id: str, actor_id: str,
                    actor_kind: str) -> NewEvent:
        from .clock import now

        if not spec.host_id:
            raise errors.InvalidRequest("host_id is required", field="host_id")
        # The creating Event carries the same project/task columns as the object
        # it creates, so `GET /v1/events?project_id=...` (ADR 0003 §1.2) finds a
        # `task.created` event and the link is a real column, not JSON.
        return NewEvent(
            event_type=spec.event_type,
            source_system=spec.source_system,
            source_event_id=spec.source_event_id,
            occurred_at=spec.occurred_at or now(),
            actor_id=actor_id,
            actor_kind=actor_kind,
            host_id=spec.host_id,
            project_id=spec.project_id,
            task_id=spec.task_id,
            payload=spec.event_payload(object_id),
            event_id=spec.event_id,
            hash_payload=_hash_payload(spec.event_payload(object_id)),
            client_occurred_at=spec.occurred_at,
        )

    def _insert_projection(self, spec: NewObject, object_id: str,
                           event: dict[str, Any]) -> dict[str, Any]:
        now = event["recorded_at"]
        if spec.kind == "project":
            self._conn.execute(
                "INSERT INTO projects (project_id, name, description, status, revision,"
                " source_event_id, created_at, updated_at) VALUES (?, ?, ?, 'active', 1, ?, ?, ?)",
                (object_id, spec.name, spec.description, event["event_id"], now, now),
            )
        elif spec.kind == "task":
            self._conn.execute(
                "INSERT INTO tasks (task_id, project_id, title, description, status, revision,"
                " source_event_id, created_at, updated_at, acceptance_criteria_json)"
                " VALUES (?, ?, ?, ?, 'ACTIVE', 1, ?, ?, ?, ?)",
                (object_id, spec.project_id, spec.name, spec.description,
                 event["event_id"], now, now,
                 canonical_json(spec.acceptance_criteria or {"requirements": []})),
            )
        else:
            # The actor comes from the Event, not from the caller, so the session
            # row can never disagree with the Event that created it.
            self._conn.execute(
                "INSERT INTO sessions (session_id, project_id, task_id, host_id, actor_id, status,"
                " revision, source_event_id, started_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'open', 1, ?, ?, ?, ?)",
                (object_id, spec.project_id, spec.task_id, spec.host_id, event["actor_id"],
                 event["event_id"], event["occurred_at"], now, now),
            )
        record = self.get(spec.kind, object_id)
        assert record is not None
        return record

    def _check_references(self, spec: NewObject) -> NewObject:
        """Validate the caller's references; return a spec with them resolved.

        A session may name only a task, but the project is then known from that
        task, and both the session and its Event are stored with it so
        `GET /v1/events?project_id=` finds the creating event.
        """
        if spec.kind == "task":
            if self.get("project", spec.project_id or "") is None:
                raise errors.NotFound("project", spec.project_id or "")
            return spec
        if spec.kind != "session":
            return spec
        if spec.project_id is not None and self.get("project", spec.project_id) is None:
            raise errors.NotFound("project", spec.project_id)
        if spec.task_id is None:
            return spec
        task = self.get("task", spec.task_id)
        if task is None:
            raise errors.NotFound("task", spec.task_id)
        if spec.project_id is not None and task["project_id"] != spec.project_id:
            raise errors.InvalidRequest(
                "task does not belong to the given project",
                field="project_id", task_project_id=task["project_id"],
            )
        if spec.project_id is None:
            spec = replace(spec, project_id=task["project_id"])
        return spec
