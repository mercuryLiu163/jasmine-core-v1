"""Baseline truth schema: identity, append-only events, object projections.

Frozen by ADR 0001-0004.

Immutability of `events` is enforced by triggers, not only by the absence of
write endpoints, so a direct `UPDATE`/`DELETE` is rejected by the database.

The Event -> object foreign keys are `DEFERRABLE INITIALLY DEFERRED` because a
creating event and the object it creates reference each other; they are
checked at COMMIT, so a transaction either has both rows or neither. The
projection -> Event foreign key is deliberately *not* deferred: that makes
"write the Event first, then the projection" a schema-enforced order.
"""

from __future__ import annotations

NAME = "m0001_baseline"
VERSION = 1

STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE core_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE schema_migrations (
        name       TEXT PRIMARY KEY,
        version    INTEGER NOT NULL UNIQUE,
        checksum   TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE hosts (
        host_id       TEXT PRIMARY KEY,
        display_name  TEXT NOT NULL,
        kind          TEXT NOT NULL DEFAULT 'workstation',
        first_seen_at TEXT NOT NULL,
        last_seen_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE actors (
        actor_id     TEXT PRIMARY KEY,
        kind         TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')),
        display_name TEXT NOT NULL,
        home_host_id TEXT REFERENCES hosts(host_id),
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE events (
        event_id        TEXT PRIMARY KEY,
        seq             INTEGER NOT NULL UNIQUE,
        schema_version  INTEGER NOT NULL,
        event_type      TEXT NOT NULL,
        source_system   TEXT NOT NULL,
        source_event_id TEXT,
        occurred_at     TEXT NOT NULL,
        recorded_at     TEXT NOT NULL,
        actor_id        TEXT NOT NULL REFERENCES actors(actor_id),
        actor_kind      TEXT NOT NULL CHECK (actor_kind IN ('human', 'agent', 'system')),
        host_id         TEXT NOT NULL REFERENCES hosts(host_id),
        session_id      TEXT REFERENCES sessions(session_id) DEFERRABLE INITIALLY DEFERRED,
        project_id      TEXT REFERENCES projects(project_id) DEFERRABLE INITIALLY DEFERRED,
        task_id         TEXT REFERENCES tasks(task_id) DEFERRABLE INITIALLY DEFERRED,
        payload_json    TEXT NOT NULL,
        body_sha256     TEXT NOT NULL,
        CHECK (json_valid(payload_json))
    )
    """,
    """
    CREATE INDEX ix_events_session ON events(session_id, seq)
    """,
    """
    CREATE INDEX ix_events_task ON events(task_id, seq)
    """,
    """
    CREATE INDEX ix_events_project ON events(project_id, seq)
    """,
    """
    CREATE INDEX ix_events_type ON events(event_type, seq)
    """,
    """
    CREATE UNIQUE INDEX ux_events_source ON events(source_system, source_event_id)
        WHERE source_event_id IS NOT NULL
    """,
    """
    CREATE TRIGGER trg_events_immutable_update
    BEFORE UPDATE ON events
    BEGIN
        SELECT RAISE(ABORT, 'events are append-only: UPDATE is denied');
    END
    """,
    """
    CREATE TRIGGER trg_events_immutable_delete
    BEFORE DELETE ON events
    BEGIN
        SELECT RAISE(ABORT, 'events are append-only: DELETE is denied');
    END
    """,
    # Without this guard, `INSERT OR REPLACE` on an existing event_id would
    # rewrite the stored Raw Event. The UPDATE/DELETE triggers above do not stop
    # it: SQLite only fires delete triggers during REPLACE resolution when
    # recursive_triggers is on, which db.PRAGMAS also sets. This trigger closes
    # the hole independently of connection settings.
    """
    CREATE TRIGGER trg_events_no_replace
    BEFORE INSERT ON events
    WHEN EXISTS (SELECT 1 FROM events WHERE event_id = NEW.event_id)
    BEGIN
        SELECT RAISE(ABORT, 'events are append-only: event_id already exists');
    END
    """,
    """
    CREATE TABLE projects (
        project_id      TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'active',
        revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE tasks (
        task_id         TEXT PRIMARY KEY,
        project_id      TEXT NOT NULL REFERENCES projects(project_id),
        title           TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'open',
        revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX ix_tasks_project ON tasks(project_id, created_at)
    """,
    """
    CREATE TABLE sessions (
        session_id      TEXT PRIMARY KEY,
        project_id      TEXT REFERENCES projects(project_id),
        task_id         TEXT REFERENCES tasks(task_id),
        host_id         TEXT NOT NULL REFERENCES hosts(host_id),
        actor_id        TEXT NOT NULL REFERENCES actors(actor_id),
        status          TEXT NOT NULL DEFAULT 'open',
        revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX ix_sessions_task ON sessions(task_id, started_at)
    """,
    """
    CREATE INDEX ix_sessions_project ON sessions(project_id, started_at)
    """,
)
