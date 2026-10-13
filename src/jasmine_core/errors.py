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


class MemoryDeploymentNotReady(CoreError):
    """P4 is running in a mode this database cannot serve yet.

    503 rather than 500: this is a deployment that is not ready, not a defect,
    and the message is authored here rather than quoted from an exception, so
    ADR 0003's "a 5xx never echoes internals" is not in tension with telling the
    operator what to do. See `memory.scope.ProjectAccess.require_mode_ready`.
    """

    status = 503
    code = "memory_deployment_not_ready"


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


class ValidationNotConfigured(CoreError):
    """The Validation run root is unset, unusable, or only partly readable.

    503 rather than 5xx-by-accident: the operator has not configured
    `JASMINE_CORE_VALIDATION_RUNS` (or it moved), so no Validation read can be
    answered. `details.reason` is the stable branchable value; a knowingly
    incomplete index also lands here with `reason: "index_incomplete"` so no page
    can silently omit a record.
    """

    status = 503
    code = "validation_not_configured"


class ValidationIndexTooLarge(CoreError):
    """A walk cap (`MAX_SCANNED_ENTRIES`/`MAX_RUN_SHAPED`) was exceeded.

    Refused rather than truncated: a silently partial index would be a page walk
    that omits records without saying so.
    """

    status = 503
    code = "validation_index_too_large"


class ValidationRunCorrupt(CoreError):
    """A run-shaped record exists but is unparseable or missing required fields."""

    status = 409
    code = "validation_run_corrupt"


class ValidationRunAmbiguous(CoreError):
    """Two run-shaped files share this stem, so the run id names no single record."""

    status = 409
    code = "validation_run_ambiguous"


class ValidationManifestCorrupt(CoreError):
    """A bundle manifest is present but breaks a §3 rule; its artifacts are unaddressable."""

    status = 409
    code = "validation_manifest_corrupt"


class ArtifactModified(CoreError):
    """Served artifact bytes disagree with the manifest's declared `sha256`/`bytes`."""

    status = 409
    code = "artifact_modified"


class OriginalWithheld(CoreError):
    """`form=original` was requested but the masker is not a no-op on this content."""

    status = 403
    code = "original_withheld"


class HostHookProtocolUnsupported(CoreError):
    """ADR 0017: the request names a protocol_version this Core does not speak."""

    status = 400
    code = "host_hook_protocol_unsupported"


class HostHookDeliveryConflict(CoreError):
    """ADR 0017: a delivery_id was reused with different content."""

    status = 409
    code = "host_hook_delivery_conflict"
