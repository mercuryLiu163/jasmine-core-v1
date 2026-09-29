"""Request-shaped values shared by the Event Store and the object projections.

Kept separate from `events`/`objects` so the write path has one place where a
field is validated, one place that decides what counts as the semantic body for
idempotency (ADR 0003 §1.3), and one place that decides the shape returned to
clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import errors, ids

#: The fields that define an Event's identity for idempotency. Deliberately
#: excludes `event_id`, `recorded_at` and the caller's identity, so a retry of
#: the same logical write is recognised as a replay.
EVENT_HASH_FIELDS = (
    "event_type",
    "source_system",
    "source_event_id",
    "occurred_at",
    "actor_id",
    "host_id",
    "session_id",
    "project_id",
    "task_id",
    "payload",
)

EVENT_TYPES = frozenset({
    "user.prompt",
    "assistant.message",
    "session.started",
    "session.ended",
    "project.created",
    "task.created",
    "project.notes",
    "rule.proposed",
    "rule.approved",
    "rule.superseded",
    "rule.retired",
    "step.created",
    "step.transitioned",
    "step.criteria_updated",
    "task.transitioned",
    "task.criteria_updated",
    "task.accepted",
    "tool.call",
    "tool.result",
    "evidence.recorded",
    "evidence.confirmed",
    "workspace.fingerprinted",
    "step.staled",
})

MAX_TEXT_BYTES = 256 * 1024


def require(event: Any, name: str) -> str:
    if not isinstance(event, dict) or name not in event:
        raise errors.InvalidRequest(f"{name} is required", field=name)
    value = event[name]
    if not isinstance(value, str):
        raise errors.InvalidRequest(f"{name} must be a string", field=name)
    return value


def optional(event: dict[str, Any], name: str) -> str | None:
    value = event.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise errors.InvalidRequest(f"{name} must be a string", field=name)
    return value


def require_id(event: dict[str, Any], name: str, prefix: str) -> str:
    value = event.get(name)
    if not isinstance(value, str) or not ids.is_id(value, prefix):
        raise errors.InvalidRequest(f"{name} must be a {prefix}_ id", field=name, value=value)
    return value


def optional_id(event: dict[str, Any], name: str, prefix: str) -> str | None:
    value = event.get(name)
    if value is None:
        return None
    return require_id(event, name, prefix)


def require_text(event: dict[str, Any], name: str = "text") -> str:
    if name not in event:
        raise errors.InvalidRequest(f"payload.text is required", field=f"payload.{name}")
    value = event[name]
    if not isinstance(value, str):
        raise errors.InvalidRequest(f"payload.{name} must be a string", field=f"payload.{name}")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise errors.PayloadTooLarge(
            f"payload.{name} exceeds {MAX_TEXT_BYTES} bytes", field=f"payload.{name}"
        )
    return value


@dataclass(frozen=True)
class NewEvent:
    """A validated Raw Event request, before any identifier is assigned."""

    event_type: str
    source_system: str
    actor_id: str
    actor_kind: str
    host_id: str
    payload: dict[str, Any]
    occurred_at: datetime
    source_event_id: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    event_id: str | None = None
    #: Overrides what the idempotency hash sees inside `payload`. Used by object
    #: creation, whose stored payload carries a server-assigned `object_id` that
    #: a retry would otherwise hash differently.
    hash_payload: dict[str, Any] | None = None
    #: The occurrence time the *client* stated, if any. When the server supplies
    #: it (object creation without an explicit `occurred_at`) it must not enter
    #: the idempotency hash, or a retry could never be recognised as a replay.
    client_occurred_at: datetime | None = None

    @classmethod
    def from_request(cls, body: dict[str, Any], *, actor_id: str, actor_kind: str) -> "NewEvent":
        from .clock import now, parse_rfc3339

        allowed = set(EVENT_HASH_FIELDS) | {"event_id"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)

        event_type = require(body, "event_type")
        if event_type not in EVENT_TYPES:
            raise errors.InvalidRequest(
                f"event_type must be one of {', '.join(sorted(EVENT_TYPES))}", field="event_type"
            )
        source_system = require(body, "source_system")
        if not source_system.strip():
            raise errors.InvalidRequest("source_system must not be empty", field="source_system")

        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise errors.InvalidRequest("payload must be an object", field="payload")
        require_text(payload)

        occurred_at_raw = body.get("occurred_at")
        occurred_at: datetime | None = None
        if occurred_at_raw is not None:
            try:
                occurred_at = parse_rfc3339(occurred_at_raw)
            except ValueError as exc:
                raise errors.InvalidRequest(
                    f"occurred_at must be RFC 3339: {exc}", field="occurred_at"
                ) from exc
        # `occurred_at` is optional on purpose. A source that has no timestamp of
        # its own -- the Codex UserPromptSubmit hook, for example -- omits it, the
        # Core records its own acceptance time, and the omission keeps the field
        # out of the idempotency hash so a retried delivery of the same source
        # event is recognised as a replay rather than a conflict (ADR 0003 §1.3).

        event_id = optional_id(body, "event_id", "evt")
        return NewEvent(
            event_type=event_type,
            source_system=source_system,
            source_event_id=optional(body, "source_event_id"),
            occurred_at=occurred_at or now(),
            actor_id=actor_id,
            actor_kind=actor_kind,
            host_id=require_id(body, "host_id", "hst"),
            session_id=optional_id(body, "session_id", "ses"),
            project_id=optional_id(body, "project_id", "prj"),
            task_id=optional_id(body, "task_id", "tsk"),
            payload=payload,
            event_id=event_id,
            client_occurred_at=occurred_at,
        )

    def hash_fields(self) -> dict[str, Any]:
        """Exactly the fields ADR 0003 §1.3 says the body hash covers."""
        return {
            "event_type": self.event_type,
            "source_system": self.source_system,
            "source_event_id": self.source_event_id,
            "occurred_at": None if self.client_occurred_at is None
            else self.client_occurred_at.isoformat().replace("+00:00", "Z"),
            "actor_id": self.actor_id,
            "host_id": self.host_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "payload": self.payload if self.hash_payload is None else self.hash_payload,
        }

    def digest(self) -> str:
        from .canonical import body_hash

        return body_hash(self.hash_fields())


@dataclass(frozen=True)
class NewObject:
    """A create request for one of the three minimal P0 projections."""

    kind: str
    name: str
    event_type: str
    source_system: str
    description: str = ""
    project_id: str | None = None
    task_id: str | None = None
    host_id: str | None = None
    occurred_at: datetime | None = None
    source_event_id: str | None = None
    event_id: str | None = None
    #: The actor the body claims. Never the source of truth: `ObjectStore.create`
    #: compares it with the authenticated actor and raises `actor_mismatch`.
    actor_id: str | None = None
    acceptance_criteria: dict[str, Any] | None = None

    @staticmethod
    def project(body: dict[str, Any]) -> "NewObject":
        allowed = {"name", "description", "event_id", "host_id", "source_system",
                   "source_event_id", "occurred_at", "expected_revision", "actor_id"}
        _reject_unknown(body, allowed, "project")
        return NewObject(
            kind="project",
            name=_require_name(body),
            description=optional(body, "description") or "",
            event_type="project.created",
            source_system=optional(body, "source_system") or "core-api",
            host_id=require_id(body, "host_id", "hst"),
            occurred_at=_optional_time(body),
            source_event_id=optional(body, "source_event_id"),
            event_id=optional_id(body, "event_id", "evt"),
            actor_id=optional_id(body, "actor_id", "act"),
        )

    @staticmethod
    def task(body: dict[str, Any]) -> "NewObject":
        allowed = {"title", "description", "project_id", "event_id", "host_id",
                   "source_system", "source_event_id", "occurred_at", "expected_revision",
                   "actor_id", "acceptance_criteria"}
        _reject_unknown(body, allowed, "task")
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise errors.InvalidRequest("title is required", field="title")
        return NewObject(
            kind="task",
            name=title,
            description=optional(body, "description") or "",
            event_type="task.created",
            source_system=optional(body, "source_system") or "core-api",
            project_id=require_id(body, "project_id", "prj"),
            host_id=require_id(body, "host_id", "hst"),
            occurred_at=_optional_time(body),
            source_event_id=optional(body, "source_event_id"),
            event_id=optional_id(body, "event_id", "evt"),
            actor_id=optional_id(body, "actor_id", "act"),
            acceptance_criteria=body.get("acceptance_criteria"),
        )

    @staticmethod
    def session(body: dict[str, Any]) -> "NewObject":
        allowed = {"title", "project_id", "task_id", "host_id", "event_id", "source_system",
                   "source_event_id", "occurred_at", "expected_revision", "actor_id"}
        _reject_unknown(body, allowed, "session")
        project_id = optional_id(body, "project_id", "prj")
        task_id = optional_id(body, "task_id", "tsk")
        return NewObject(
            kind="session",
            name=optional(body, "title") or "",
            event_type="session.started",
            source_system=optional(body, "source_system") or "core-api",
            project_id=project_id,
            task_id=task_id,
            host_id=require_id(body, "host_id", "hst"),
            occurred_at=_optional_time(body),
            source_event_id=optional(body, "source_event_id"),
            event_id=optional_id(body, "event_id", "evt"),
            actor_id=optional_id(body, "actor_id", "act"),
        )

    def event_payload(self, object_id: str) -> dict[str, Any]:
        from .canonical import sha256_hex

        return {
            "text": "",
            "object_kind": self.kind,
            "object_id": object_id,
            "name": self.name,
            "name_sha256": sha256_hex(self.name),
            "description": self.description,
            "project_id": self.project_id,
            "task_id": self.task_id,
            **({"acceptance_criteria": self.acceptance_criteria} if self.kind == "task" else {}),
        }


def _reject_unknown(body: dict[str, Any], allowed: set[str], kind: str) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields for {kind}: {', '.join(unknown)}", fields=unknown)
    if "expected_revision" in body:
        # Creating an object has no prior revision to match against; accepting the
        # field would silently drop a client's concurrency intent (ADR 0002 §2.3).
        raise errors.UnexpectedExpectedRevision(
            "expected_revision is only valid on updates; P0 has no update endpoint",
            field="expected_revision",
        )


def _require_name(body: dict[str, Any]) -> str:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise errors.InvalidRequest("name is required", field="name")
    if len(name) > 512:
        raise errors.InvalidRequest("name must be at most 512 characters", field="name")
    return name


def _optional_time(body: dict[str, Any]) -> datetime | None:
    from .clock import parse_rfc3339

    raw = body.get("occurred_at")
    if raw is None:
        return None
    try:
        return parse_rfc3339(raw)
    except ValueError as exc:
        raise errors.InvalidRequest(f"occurred_at must be RFC 3339: {exc}", field="occurred_at") from exc
