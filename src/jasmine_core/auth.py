"""Bearer token authentication and scopes (ADR 0004 §1.3).

Only the SHA-256 of a token is stored, so a copy of `core.db` cannot be
replayed against the API. A failed authentication writes no Truth but still
writes an audit row, which is why the audit write lives on its own transaction
and must never be able to fail the request it is recording.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import clock, db, errors, ids
from .canonical import canonical_json, sha256_hex

TOKEN_BYTES = 32
SCOPES = ("admin", "objects:read", "objects:write", "events:read", "events:write",
          "authority:read", "authority:propose", "authority:manage", "guard:check",
          "state:read", "state:write", "state:accept", "evidence:read",
          "evidence:write", "evidence:attest", "evidence:confirm", "fingerprint:scan", "fingerprint:read", "interpretations:read", "interpretations:process", "interpretations:manage", "resolutions:read", "resolutions:process", "reviews:read", "reviews:manage", "checkpoint:read", "checkpoint:write", "resume:read", "resume:build")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``actor_id`` is the only source of Truth."""

    actor_id: str
    key_id: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise errors.ForbiddenScope(
                f"this key does not carry the {scope} scope", required=scope,
                granted=sorted(self.scopes),
            )


class Auth:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def issue_key(self, *, actor_id: str, label: str, scopes: list[str]) -> dict[str, Any]:
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise errors.InvalidRequest("scopes must be an array of strings", field="scopes")
        unknown = sorted(set(scopes) - set(SCOPES))
        if unknown:
            raise errors.InvalidRequest(
                f"unknown scopes: {', '.join(unknown)}", unknown_scopes=unknown, allowed=list(SCOPES)
            )
        if not scopes:
            raise errors.InvalidRequest("at least one scope is required", field="scopes")
        if self._conn.execute("SELECT 1 FROM actors WHERE actor_id = ?", (actor_id,)).fetchone() is None:
            raise errors.NotFound("actor", actor_id)
        kind = self._conn.execute("SELECT kind FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()["kind"]
        if kind == "agent" and ({"interpretations:manage", "reviews:manage"} & set(scopes)):
            raise errors.ForbiddenActorKind("agents cannot manage interpretations or reviews")
        if kind == "agent" and "authority:manage" in scopes:
            raise errors.ForbiddenActorKind("agent keys cannot carry authority:manage")
        if kind == "agent" and "state:accept" in scopes:
            raise errors.ForbiddenActorKind("agent keys cannot carry state:accept")
        if kind != "system" and ("evidence:write" in scopes or "evidence:attest" in scopes or
                                 "fingerprint:scan" in scopes):
            raise errors.ForbiddenActorKind("only trusted system keys can capture tool Evidence or fingerprints")
        if kind != "human" and "evidence:confirm" in scopes:
            raise errors.ForbiddenActorKind("only human keys can confirm Evidence")
        key_id = ids.new_id("key")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with db.translate_lock_errors(), db.transaction(self._conn):
            self._conn.execute(
                "INSERT INTO api_keys (key_id, actor_id, label, scopes, token_sha256, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key_id, actor_id, label, canonical_json(sorted(scopes)),
                 sha256_hex(token), clock.now_rfc3339()),
            )
        return {"key_id": key_id, "actor_id": actor_id, "label": label,
                "scopes": sorted(scopes), "token": token,
                "warning": "the token is shown once and is not recoverable"}

    def authenticate(self, header: str | None) -> Principal:
        if not header:
            raise errors.Unauthenticated("an Authorization: Bearer header is required")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            raise errors.Unauthenticated("expected an Authorization: Bearer header")
        presented = presented.strip()
        row = self._conn.execute(
            "SELECT key_id, actor_id, scopes, revoked_at FROM api_keys WHERE token_sha256 = ?",
            (sha256_hex(presented),),
        ).fetchone()
        if row is None:
            # Same message for an unknown and a wrong token: do not confirm which.
            raise errors.Unauthenticated("the bearer token is not recognised")
        if row["revoked_at"] is not None:
            raise errors.Unauthenticated("this API key has been revoked")
        # Deliberately read-only. Recording last_used_at here would mean a
        # caller with the wrong scope had already written to `api_keys` before
        # the refusal, which is a write Truth does not promise to be unchanged.
        return Principal(actor_id=row["actor_id"], key_id=row["key_id"],
                         scopes=frozenset(json_scopes(row["scopes"])))

    def touch(self, key_id: str) -> None:
        """Record that a key was used, called only after the request is allowed.

        Best effort by design: losing a last-used timestamp must not fail the
        request it was describing.
        """
        try:
            with db.translate_lock_errors(), db.transaction(self._conn):
                self._conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                    (clock.now_rfc3339(), key_id),
                )
        except (db.errors.DatabaseBusy, sqlite3.OperationalError):
            pass

    def list_keys(self) -> list[dict[str, Any]]:
        """Metadata only. There is no endpoint that can return a token again."""
        return [
            {
                "key_id": row["key_id"], "actor_id": row["actor_id"], "label": row["label"],
                "scopes": sorted(json_scopes(row["scopes"])),
                "created_at": row["created_at"], "revoked_at": row["revoked_at"],
                "last_used_at": row["last_used_at"],
            }
            for row in self._conn.execute("SELECT * FROM api_keys ORDER BY created_at, key_id")
        ]

    def revoke(self, key_id: str) -> None:
        row = self._conn.execute("SELECT revoked_at FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
        if row is None:
            raise errors.NotFound("key", key_id)
        if row["revoked_at"] is not None:
            return
        with db.translate_lock_errors(), db.transaction(self._conn):
            self._conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ?", (clock.now_rfc3339(), key_id)
            )


def json_scopes(raw: str) -> list[str]:
    import json

    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise errors.CoreError(f"stored scopes are malformed: {raw!r}")
    return value
