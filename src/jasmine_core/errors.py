"""Typed errors mapped to HTTP responses by the API layer.

The codes are frozen in ADR 0003 §1.4. Every error carries a stable machine
code so clients (Dashboard, capture entry, validation runner) can branch on it
without parsing prose.
"""

from __future__ import annotations

from typing import Any


class CoreError(Exception):
    """Base class for every error the Core reports to a caller."""

    status = 500
    code = "internal_error"

    def __init__(self, message: str = "", **details: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details: dict[str, Any] = {k: v for k, v in details.items() if v is not None}

    def to_payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": request_id,
                "details": self.details,
            }
        }


class InvalidRequest(CoreError):
    status = 400
    code = "invalid_request"


class MissingExpectedRevision(CoreError):
    status = 400
    code = "missing_expected_revision"


class UnexpectedExpectedRevision(CoreError):
    status = 400
    code = "unexpected_expected_revision"


class Unauthenticated(CoreError):
    status = 401
    code = "unauthenticated"


class ForbiddenScope(CoreError):
    status = 403
    code = "forbidden_scope"


class ActorMismatch(CoreError):
    status = 403
    code = "actor_mismatch"


class ForbiddenActorKind(CoreError):
    status = 403
    code = "forbidden_actor_kind"


class NotFound(CoreError):
    status = 404
    code = "not_found"

    def __init__(self, kind: str, ident: str) -> None:
        super().__init__(f"{kind} {ident} not found", kind=kind, id=ident)
        self.code = f"{kind}_not_found"


class EventIdConflict(CoreError):
    status = 409
    code = "event_id_conflict"


class SourceEventDuplicate(CoreError):
    status = 409
    code = "source_event_duplicate"


class RevisionConflict(CoreError):
    status = 409
    code = "revision_conflict"


class RuleKeyConflict(CoreError):
    status = 409
    code = "rule_key_conflict"


class MethodNotAllowed(CoreError):
    status = 405
    code = "method_not_allowed"


class PayloadTooLarge(CoreError):
    status = 413
    code = "payload_too_large"


class UnsupportedMediaType(CoreError):
    status = 415
    code = "unsupported_media_type"


class SchemaVersionUnsupported(CoreError):
    status = 503
    code = "schema_version_unsupported"


class MigrationConflict(CoreError):
    """The schema cannot be advanced by this process right now."""

    status = 503
    code = "migration_conflict"


class DatabaseBusy(CoreError):
    """Another writer held the SQLite write lock past ``busy_timeout``."""

    status = 503
    code = "database_busy"


class InvalidStateTransition(CoreError):
    status = 409
    code = "invalid_state_transition"


class MissingEvidence(CoreError):
    status = 422
    code = "missing_evidence"


class FingerprintUnavailable(MissingEvidence):
    code = "fingerprint_unavailable"


class InvalidInterpretationSource(CoreError):
    status = 400
    code = "invalid_interpretation_source"


class InterpretationIdempotencyConflict(CoreError):
    status = 409
    code = "interpretation_idempotency_conflict"
