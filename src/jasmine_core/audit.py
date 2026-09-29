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
from .canonical import canonical_json, redact, redact_text, sha256_hex

_INSERT = """
INSERT INTO audit_log (audit_id, seq, at, request_id, actor_id, method, path, decision,
                       status_code, scope, target_id, error_code, detail_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


#: A caller-supplied value must not be able to make the audit row unbounded.
#: The body cap is 1 MiB; an audit row is metadata and does not need to be.
MAX_FIELD_CHARS = 512
MAX_DETAIL_CHARS = 4096


class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, *, request_id: str, method: str, path: str, decision: str,
               actor_id: str | None = None, status_code: int | None = None,
               scope: str | None = None, target_id: str | None = None,
               error_code: str | None = None, detail: dict[str, Any] | None = None) -> str:
        audit_id = ids.new_id("aud")
        # `path`, `request_id` and every detail value are caller-influenced, and
        # ADR 0004 §1.2 promises the masking holds "even if the caller
        # mistakenly passes it". A caller-supplied `X-Request-Id`, a path
        # segment, or an echoed `details.value` are exactly the channels through
        # which a bearer token would otherwise reach the table.
        safe_detail = canonical_json(redact(detail or {}))
        if len(safe_detail) > MAX_FIELD_CHARS:
            # Clipping serialized JSON can make it invalid and lose the audit
            # row entirely. Preserve a bounded, valid metadata summary.
            safe_detail = canonical_json({"truncated": True,
                                          "detail_sha256": sha256_hex(safe_detail)})
        row_values = (
            audit_id, _clip(str(request_id)), _clip(str(method)), _safe_path(path),
            _clip(str(decision)), _clip(str(actor_id)) if actor_id else None, scope,
            _clip(str(target_id)) if target_id else None, error_code,
        )
        with db.translate_lock_errors(), db.transaction(self._conn):
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM audit_log"
            ).fetchone()
            self._conn.execute(
                _INSERT,
                (
                    row_values[0], int(row["next"]), clock.now_rfc3339(), row_values[1],
                    row_values[5], row_values[2], row_values[3], row_values[4], status_code,
                    row_values[6], row_values[7], row_values[8], safe_detail,
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


def _safe_path(path: str) -> str:
    """Redact a request path and query, and clip it.

    Both the raw and the percent-decoded forms are masked, because a caller can
    put anything in a path segment or query parameter and the audit log is not
    the place to discover what.
    """
    from urllib.parse import unquote

    masked = redact_text(str(path))
    decoded = unquote(str(path))
    if decoded != str(path):
        masked = redact_text(masked) + " " + redact_text(decoded)
    return _clip(masked)


def _clip(value: str, limit: int = MAX_FIELD_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"…[truncated {len(value) - limit} chars]"


def summarise_error(exc: errors.CoreError) -> dict[str, Any]:
    """The only error information allowed out of the request path.

    `CoreError.details` can carry a full current Rule on revision conflict.
    The audit stores only metadata, never the values of those details.
    """
    return {
        "code": exc.code,
        "message": _clip(exc.message),
        "detail_keys": sorted(exc.details),
    }
