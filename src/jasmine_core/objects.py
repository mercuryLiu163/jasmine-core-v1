"""The three minimal P0 projections: project, task, session.

Every create runs inside one transaction and always follows
`EventStore.append` (ADR 0002 §2.2: Event first, projection second, one commit).
The projection's `source_event_id` is `UNIQUE` and its foreign key to `events` is
not deferred, so a projection cannot exist without exactly one originating Event
and two objects cannot share one.

P0 deliberately exposes no update or delete: the Task state machine belongs to
P1, and there is no endpoint that could contradict an append-only Event.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import errors, ids
from .db import transaction
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

    # -- reading -----------------------------------------------------------

    def get(self, kind: str, object_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT * FROM {TABLES[kind]} WHERE {PRIMARY_KEY[kind]} = ?", (object_id,)
        ).fetchone()
        return None if row is None else dict(row)

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
        return [dict(row) for row in rows]

    def count(self, kind: str) -> int:
        return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {TABLES[kind]}").fetchone()["n"])

    # -- writing -----------------------------------------------------------

    def create(self, spec: NewObject, *, actor_id: str, actor_kind: str) -> dict[str, Any]:
        """Create one object and its source Event in a single transaction.

        Returns ``{"object", "event", "replayed"}``. On a replay the originally
        stored object is returned unchanged and no row is written.
        """
        with transaction(self._conn):
            object_id = ids.new_id(ID_PREFIX[spec.kind])
            event, replayed = self.events.append(
                self._event_spec(spec, object_id, actor_id, actor_kind)
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
        return found

    def _event_spec(self, spec: NewObject, object_id: str, actor_id: str,
                    actor_kind: str) -> NewEvent:
        from .clock import now

        if not spec.host_id:
            raise errors.InvalidRequest("host_id is required", field="host_id")
        return NewEvent(
            event_type=spec.event_type,
            source_system=spec.source_system,
            source_event_id=spec.source_event_id,
            occurred_at=spec.occurred_at or now(),
            actor_id=actor_id,
            actor_kind=actor_kind,
            host_id=spec.host_id,
            payload=spec.event_payload(object_id),
            event_id=spec.event_id,
            hash_payload=_hash_payload(spec.event_payload(object_id)),
            client_occurred_at=spec.occurred_at,
        )

    def _insert_projection(self, spec: NewObject, object_id: str,
                           event: dict[str, Any]) -> dict[str, Any]:
        self._check_references(spec)
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
                " source_event_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'open', 1, ?, ?, ?)",
                (object_id, spec.project_id, spec.name, spec.description,
                 event["event_id"], now, now),
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

    def _check_references(self, spec: NewObject) -> None:
        if spec.kind == "task":
            if self.get("project", spec.project_id or "") is None:
                raise errors.NotFound("project", spec.project_id or "")
        elif spec.kind == "session":
            if spec.project_id is not None and self.get("project", spec.project_id) is None:
                raise errors.NotFound("project", spec.project_id)
            if spec.task_id is not None:
                task = self.get("task", spec.task_id)
                if task is None:
                    raise errors.NotFound("task", spec.task_id)
                if spec.project_id is not None and task["project_id"] != spec.project_id:
                    raise errors.InvalidRequest(
                        "task does not belong to the given project",
                        field="project_id", task_project_id=task["project_id"],
                    )
