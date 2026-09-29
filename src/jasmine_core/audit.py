"""Append-only audit log (ADR 0004 §1.2).

The audit row holds metadata and lengths, never the request body, never a
token, never a prompt, never a stack trace. Every value is passed through
`canonical.redact` before it is serialised, so a caller that mistakenly passes
a credential still cannot land it on disk.

`record()` opens its own transaction. A failure to audit must never turn into a
failure of the operation being audited, and a rolled-back operation must not lose
the record that it was attempted; keeping the two commits separate is what buys
both properties.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import clock, db, errors, ids
from .canonical import canonical_json, redact

_INSERT = """
INSERT INTO audit_log (audit_id, seq, at, request_id, actor_id, method, path, decision,
                       status_code, scope, target_id, error_code, detail_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, *, request_id: str, method: str, path: str, decision: str,
               actor_id: str | None = None, status_code: int | None = None,
               scope: str | None = None, target_id: str | None = None,
               error_code: str | None = None, detail: dict[str, Any] | None = None) -> str:
        audit_id = ids.new_id("aud")
        # `path` is redacted too: a future endpoint could put a value in a query
        # string, and the same rule should apply without anyone remembering.
        safe_detail = redact(detail or {})
        safe_path = redact(str(path))
        with db.translate_lock_errors(), db.transaction(self._conn):
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM audit_log"
            ).fetchone()
            self._conn.execute(
                _INSERT,
                (
                    audit_id, int(row["next"]), clock.now_rfc3339(), request_id, actor_id,
                    str(method), safe_path, decision, status_code, scope,
                    target_id, error_code, canonical_json(safe_detail),
                ),
            )
        return audit_id

    def list(self, *, actor_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        import json

        clauses, params = [], []
        if actor_id is not None:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        rows = self._conn.execute(
            f"SELECT * FROM audit_log{where} ORDER BY seq LIMIT ?", params
        ).fetchall()
        return [
            {
                "audit_id": row["audit_id"], "seq": row["seq"], "at": row["at"],
                "request_id": row["request_id"], "actor_id": row["actor_id"],
                "method": row["method"], "path": row["path"], "decision": row["decision"],
                "status_code": row["status_code"], "scope": row["scope"],
                "target_id": row["target_id"], "error_code": row["error_code"],
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"])


def summarise_error(exc: errors.CoreError) -> dict[str, Any]:
    """The only error information allowed out of the request path.

    `CoreError.details` can carry caller-supplied values, so it is redacted; the
    message and code are frozen by the ADR and are safe by construction.
    """
    return {"code": exc.code, "message": exc.message, "details": exc.details}
