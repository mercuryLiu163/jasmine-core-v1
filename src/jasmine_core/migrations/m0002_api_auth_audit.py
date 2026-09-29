"""API credentials and the append-only audit log (ADR 0004).

`api_keys` stores only the SHA-256 of the bearer token, so a database copy
cannot be replayed against the API. `audit_log` is append-only like `events`
and deliberately holds metadata only: no prompt text, no token, no stack
trace (see ADR 0004 §1.2).

`audit_log.seq` is allocated the same way as `events.seq`: COALESCE(MAX(seq),0)+1
inside the caller's write transaction. A dedicated `core_meta` counter would
be a second source of ordering truth for no benefit.
"""

from __future__ import annotations

NAME = "m0002_api_auth_audit"
VERSION = 2

STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE api_keys (
        key_id      TEXT PRIMARY KEY,
        actor_id    TEXT NOT NULL REFERENCES actors(actor_id),
        label       TEXT NOT NULL,
        scopes      TEXT NOT NULL,
        token_sha256 TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL,
        revoked_at  TEXT,
        last_used_at TEXT
    )
    """,
    """
    CREATE INDEX ix_api_keys_actor ON api_keys(actor_id)
    """,
    """
    CREATE TABLE audit_log (
        audit_id   TEXT PRIMARY KEY,
        seq        INTEGER NOT NULL UNIQUE,
        at         TEXT NOT NULL,
        request_id TEXT NOT NULL,
        actor_id   TEXT,
        method     TEXT NOT NULL,
        path       TEXT NOT NULL,
        decision   TEXT NOT NULL CHECK (decision IN ('allow', 'deny', 'error')),
        status_code INTEGER,
        scope      TEXT,
        target_id  TEXT,
        error_code TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        CHECK (json_valid(detail_json))
    )
    """,
    """
    CREATE INDEX ix_audit_at ON audit_log(seq)
    """,
    """
    CREATE INDEX ix_audit_actor ON audit_log(actor_id, seq)
    """,
    """
    CREATE TRIGGER trg_audit_immutable_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is denied');
    END
    """,
    """
    CREATE TRIGGER trg_audit_immutable_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is denied');
    END
    """,
)
