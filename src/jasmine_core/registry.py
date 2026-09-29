"""Device and actor registry.

P0 only needs these to satisfy foreign keys and to make a captured event's
provenance explicit: which machine observed it and which identity produced it.
`upsert` is deliberately separate from the object/event write path — identity is
long-lived metadata, not truth, so re-registering the same host or actor is a
no-op update rather than a new Event.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import errors, ids

ACTOR_KINDS = ("human", "agent", "system")


class Registry:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_host(self, host_id: str, *, display_name: str | None = None,
                    kind: str = "workstation") -> dict[str, Any]:
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id", value=host_id)
        from .clock import now_rfc3339

        now = now_rfc3339()
        self._conn.execute(
            "INSERT INTO hosts (host_id, display_name, kind, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(host_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
            (host_id, display_name or host_id, kind, now, now),
        )
        return self.require_host(host_id)

    def require_host(self, host_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM hosts WHERE host_id = ?", (host_id,)).fetchone()
        if row is None:
            raise errors.NotFound("host", host_id)
        return dict(row)

    def upsert_actor(self, actor_id: str, *, kind: str, display_name: str | None = None,
                     home_host_id: str | None = None) -> dict[str, Any]:
        if not ids.is_id(actor_id, "act"):
            raise errors.InvalidRequest("actor_id must be an act_ id", field="actor_id", value=actor_id)
        if kind not in ACTOR_KINDS:
            raise errors.InvalidRequest(
                f"kind must be one of {', '.join(ACTOR_KINDS)}", field="kind", value=kind
            )
        if home_host_id is not None and self._conn.execute(
            "SELECT 1 FROM hosts WHERE host_id = ?", (home_host_id,)
        ).fetchone() is None:
            raise errors.NotFound("host", home_host_id)
        from .clock import now_rfc3339

        self._conn.execute(
            "INSERT INTO actors (actor_id, kind, display_name, home_host_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(actor_id) DO UPDATE SET display_name = excluded.display_name",
            (actor_id, kind, display_name or actor_id, home_host_id, now_rfc3339()),
        )
        return self.require_actor(actor_id)

    def require_actor(self, actor_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
        if row is None:
            raise errors.NotFound("actor", actor_id)
        return dict(row)

    def list_hosts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM hosts ORDER BY host_id")]

    def list_actors(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM actors ORDER BY actor_id")]
