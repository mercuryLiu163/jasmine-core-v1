"""The Raw Event Store: append-only, idempotent, transaction-scoped.

Every write method here assumes the caller already holds the write transaction
opened by `db.transaction()`, because the whole point of ADR 0002 is that the
Event and the object it created are committed together. The store never opens a
transaction itself, so it cannot be half-used.

Idempotency is keyed on `event_id` (ADR 0003 §1.3):
  * unseen id                   -> appended
  * existing id, same body hash -> replayed, the stored row is returned unchanged
  * existing id, different hash -> 409 event_id_conflict, nothing is written

A second guard, the `(source_system, source_event_id)` unique index, stops a
capture client that retries under a fresh `event_id` from writing one user
message twice.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import clock, db, errors, ids
from .canonical import canonical_json, sha256_hex
from .models import EVENT_TYPES, NewEvent

#: table -> primary key column, used to resolve the caller's stated references
#: before the database does it for us with an untyped error.
_PK = {
    "hosts": "host_id",
    "actors": "actor_id",
    "sessions": "session_id",
    "projects": "project_id",
    "tasks": "task_id",
}

#: Bounded so a pathological clock or a hostile client cannot force an unbounded
#: retry loop inside one transaction.
ID_ALLOCATION_ATTEMPTS = 8

_INSERT = """
INSERT INTO events (
    event_id, seq, schema_version, event_type, source_system, source_event_id,
    occurred_at, recorded_at, actor_id, actor_kind, host_id,
    session_id, project_id, task_id, payload_json, body_sha256
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "seq": row["seq"],
        "schema_version": row["schema_version"],
        "event_type": row["event_type"],
        "source_system": row["source_system"],
        "source_event_id": row["source_event_id"],
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "actor_id": row["actor_id"],
        "actor_kind": row["actor_kind"],
        "host_id": row["host_id"],
        "session_id": row["session_id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "payload": json.loads(row["payload_json"]),
        "body_sha256": row["body_sha256"],
    }


class EventStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int) -> None:
        self._conn = conn
        self._schema_version = schema_version

    # -- reading -----------------------------------------------------------

    def get(self, event_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return None if row is None else event_row_to_dict(row)

    def get_row(self, event_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()

    def find_by_source(self, source_system: str, source_event_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE source_system = ? AND source_event_id = ?",
            (source_system, source_event_id),
        ).fetchone()
        return None if row is None else event_row_to_dict(row)

    def list(self, *, session_id: str | None = None, task_id: str | None = None,
             project_id: str | None = None, event_type: str | None = None,
             source_system: str | None = None, limit: int = 100,
             after_seq: int | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("session_id", session_id), ("task_id", task_id),
                              ("project_id", project_id), ("event_type", event_type),
                              ("source_system", source_system)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        rows = self._conn.execute(
            f"SELECT * FROM events{where} ORDER BY seq LIMIT ?", params
        ).fetchall()
        return [event_row_to_dict(row) for row in rows]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])

    def text_sha256(self, event_id: str) -> str | None:
        row = self.get_row(event_id)
        if row is None:
            return None
        return sha256_hex(json.loads(row["payload_json"]).get("text", ""))

    # -- writing -----------------------------------------------------------

    def append(self, spec: NewEvent, *, event_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """Append one Raw Event, or raise the frozen replay/conflict errors.

        Returns ``(event, replayed)``. ``replayed`` is True when the returned
        event was already stored by an earlier identical request, in which case
        this call wrote nothing. Must be called inside `db.transaction()`.
        """
        with db.translate_lock_errors():
            return self._append(spec, event_id)

    def _append(self, spec: NewEvent, event_id: str | None) -> tuple[dict[str, Any], bool]:
        wanted = event_id or spec.event_id
        validate_payload(spec)
        self._check_references(spec)
        digest = spec.digest()

        if wanted is not None:
            existing = self.get(wanted)
            if existing is not None:
                return self._resolve_replay(existing, digest), True

        try:
            return self._write(spec, wanted, digest), False
        except sqlite3.IntegrityError as exc:
            if wanted is not None:
                existing = self.get(wanted)
                if existing is not None:
                    return self._resolve_replay(existing, digest), True
            if spec.source_event_id is not None:
                existing = self.find_by_source(spec.source_system, spec.source_event_id)
                if existing is not None:
                    raise errors.SourceEventDuplicate(
                        "an event with the same source_system and source_event_id is already stored",
                        source_system=spec.source_system,
                        source_event_id=spec.source_event_id,
                        existing_event_id=existing["event_id"],
                    ) from exc
            raise

    def _resolve_replay(self, existing: dict[str, Any], digest: str) -> dict[str, Any]:
        if existing["body_sha256"] == digest:
            return existing
        raise errors.EventIdConflict(
            "event_id already exists with different content",
            existing_event_id=existing["event_id"],
            existing_body_sha256=existing["body_sha256"],
            request_body_sha256=digest,
        )

    def _write(self, spec: NewEvent, wanted: str | None, digest: str) -> dict[str, Any]:
        payload_json = canonical_json(spec.payload)
        # Sampled per write, not per store: a long-lived process must stamp each
        # Event with the moment it was accepted, and the value lands in a table
        # that can never be corrected.
        recorded_at = clock.now_rfc3339()
        occurred_at = clock.to_rfc3339(spec.occurred_at) if spec.occurred_at else recorded_at
        seq = _next_seq(self._conn)
        for attempt in range(ID_ALLOCATION_ATTEMPTS):
            event_id = wanted or ids.new_id("evt")
            try:
                self._conn.execute(
                    _INSERT,
                    (
                        event_id, seq, self._schema_version, spec.event_type, spec.source_system,
                        spec.source_event_id, occurred_at, recorded_at, spec.actor_id,
                        spec.actor_kind, spec.host_id, spec.session_id, spec.project_id,
                        spec.task_id, payload_json, digest,
                    ),
                )
            except sqlite3.IntegrityError:
                # Only a client-supplied id can clash; a server-generated one is
                # retried with a fresh random suffix.
                if wanted is None and attempt < ID_ALLOCATION_ATTEMPTS - 1:
                    seq += 1
                    continue
                raise
            stored = self.get(event_id)
            assert stored is not None  # just inserted in this transaction
            return stored
        raise errors.CoreError("could not allocate a unique event_id")  # pragma: no cover

    def _check_references(self, spec: NewEvent) -> None:
        """Refuse explicit references that do not exist, with the frozen codes.

        ADR 0002 §2.4: a Raw Event may have no project, task or session at all,
        but a reference the caller *did* state must resolve. Letting the foreign
        key do it would surface a raw ``sqlite3.IntegrityError`` at COMMIT instead
        of the documented 404.
        """
        for column, ident, kind in (
            ("hosts", spec.host_id, "host"),
            ("actors", spec.actor_id, "actor"),
            ("sessions", spec.session_id, "session"),
            ("projects", spec.project_id, "project"),
            ("tasks", spec.task_id, "task"),
        ):
            if ident is None:
                continue
            row = self._conn.execute(
                f"SELECT 1 FROM {column} WHERE {_PK[column]} = ?", (ident,)
            ).fetchone()
            if row is None:
                raise errors.NotFound(kind, ident)


def _next_seq(conn: sqlite3.Connection) -> int:
    # Safe without extra locking: the caller holds the write transaction's lock.
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events").fetchone()
    return int(row["next"])


def validate_payload(spec: NewEvent) -> None:
    """Enforce the payload contract at the store, not only at the HTTP edge.

    Callers inside the process (the capture entry, later stages) must get the
    same refusal a remote client would, so the check cannot live only in
    `NewEvent.from_request`.
    """
    from .models import require_text

    if not isinstance(spec.payload, dict):
        raise errors.InvalidRequest("payload must be an object", field="payload")
    if spec.event_type not in EVENT_TYPES:
        raise errors.InvalidRequest(
            f"event_type must be one of {', '.join(sorted(EVENT_TYPES))}", field="event_type"
        )
    require_text(spec.payload)
    _reject_non_finite(spec.payload)


def _reject_non_finite(payload: Any) -> None:
    """`NaN`/`Infinity` are Python-only JSON extensions.

    `json.dumps` emits them by default and the `json_valid` CHECK on `events`
    would then reject the INSERT with a raw IntegrityError at COMMIT. Refusing
    them here keeps the error a typed 400.
    """
    if isinstance(payload, float) and (payload != payload or payload in (float("inf"), float("-inf"))):
        raise errors.InvalidRequest("payload must not contain NaN or Infinity", field="payload")
    if isinstance(payload, dict):
        for key, value in payload.items():
            _reject_non_finite(key)
            _reject_non_finite(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _reject_non_finite(item)
