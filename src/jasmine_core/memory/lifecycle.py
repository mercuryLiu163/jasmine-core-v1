"""Memory lifecycle: obsolete, delete, pin, unpin (ADR 0011 §9; design §9).

Design §9 requires the Memory Inspector to support `obsolete/delete/pin`, and
the taskbook is explicit that a read-only Inspector does not satisfy it: these
are authenticated, audited Core API operations, and the Dashboard is a consumer
of them rather than a second implementation.

Each of the four has a different blast radius, and the differences are the whole
design of this module:

* **obsolete** is logical and reversible. The item keeps its metadata, its
  provenance and every Raw Event and Evidence it points at; it stops appearing in
  Recall and in a Context Pack, and the Inspector shows why. Remotely, each
  memory unit of the document is *invalidated* — the engine's own soft-retire,
  documented as excluded from recall and consolidation but kept for audit — so
  the remote state matches the local state without pretending to have erased
  anything. The remote invalidate is best-effort and its outcome is recorded; a
  remote failure never un-obsoletes the item.

* **delete** is physical on the remote side and a tombstone locally. The remote
  document and its memories are removed through a queued job, because that call
  is network work. Locally the item is excluded from Recall *immediately*, and
  the row survives with its provenance. Nothing about the Core source is
  touched: the Rule, the State, the Evidence and the Raw Event all remain, which
  is what makes a delete reversible in the only way that matters — by re-retaining
  from the source, not by restoring a backup.

  The claim P4 refuses to make is "deleted" while the remote copy is still
  there. `engine_state` moves to `DELETE_PENDING`, then `DELETED` only when the
  engine confirms, and `DELETE_FAILED` if it does not. Until then the item is
  still excluded from Recall, so a failed remote delete is a storage leak rather
  than a correctness problem.

* **pin** is display-only. It changes Inspector ordering and nothing else. It
  cannot resurrect an obsolete or deleted item, cannot widen the Memory budget,
  and cannot make a Memory item load as Authority — those three refusals are
  asserted in the tests because each is a plausible thing for a future edit to
  get wrong.

Every command appends an Event carrying the actor, the source, the expected and
resulting revision, and the before/after state, and every command takes
`expected_revision` so two operators acting on the same item cannot silently
overwrite each other.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock, db, errors, ids
from ..events import EventStore
from ..models import NewEvent
from . import jobs as jobs_module
from . import scope as scope_module
from .store import MemoryStore
from .types import ITEM_STATUSES

#: `expected_revision` guards the command, not the content. The sanitized text
#: and the document id are immutable by trigger, so there is nothing else a
#: concurrent operator could swap out from under a delete.
_EVENT_BY_ACTION = {
    "obsolete": "memory.obsoleted",
    "delete": "memory.deleted",
    "pin": "memory.pinned",
    "unpin": "memory.unpinned",
}
_ALLOWED_ACTIONS = frozenset(_EVENT_BY_ACTION)


class MemoryLifecycle:
    """The four operator commands, plus the shared event/revision machinery."""

    def __init__(self, conn: sqlite3.Connection, store: MemoryStore, *,
                 mode: str = scope_module.SINGLE_USER) -> None:
        self._conn = conn
        self._store = store
        self._events = EventStore(conn, schema_version=0)
        self._jobs = jobs_module.JobStore(conn, store)
        # The mode belongs here, not only in `detail()`. Constructing a
        # `ProjectAccess` with the default inside the write path is how an
        # `acl` deployment ends up enforcing the ACL on the read path and not on
        # the write path — and a mode enforced on GET and absent on POST is worse
        # than no mode, because the documentation says it is enforced.
        self.mode = mode
        self._access = scope_module.ProjectAccess(conn, mode=mode)

    # -- public commands ---------------------------------------------------

    def obsolete(self, memory_id: str, body: dict[str, Any], *, actor_id: str,
                 actor_kind: str, host_id: str) -> dict[str, Any]:
        return self._transition(memory_id, "obsolete", body, actor_id=actor_id,
                                actor_kind=actor_kind, host_id=host_id)

    def delete(self, memory_id: str, body: dict[str, Any], *, actor_id: str,
               actor_kind: str, host_id: str) -> dict[str, Any]:
        return self._transition(memory_id, "delete", body, actor_id=actor_id,
                                actor_kind=actor_kind, host_id=host_id)

    def pin(self, memory_id: str, body: dict[str, Any], *, actor_id: str,
            actor_kind: str, host_id: str) -> dict[str, Any]:
        return self._transition(memory_id, "pin", body, actor_id=actor_id,
                                actor_kind=actor_kind, host_id=host_id)

    def unpin(self, memory_id: str, body: dict[str, Any], *, actor_id: str,
              actor_kind: str, host_id: str) -> dict[str, Any]:
        return self._transition(memory_id, "unpin", body, actor_id=actor_id,
                                actor_kind=actor_kind, host_id=host_id)

    # -- shared machinery --------------------------------------------------

    def _transition(self, memory_id: str, action: str, body: dict[str, Any], *,
                    actor_id: str, actor_kind: str, host_id: str) -> dict[str, Any]:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"unknown memory action: {action!r}")
        _reject_unknown(body, {"expected_revision", "host_id", "event_id", "reason"})
        if actor_kind not in ("human", "system"):
            raise errors.ForbiddenActorKind(
                "only a human or trusted system actor may change the Memory lifecycle"
            )
        expected = _positive_revision(body)
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
        event_id = body.get("event_id")
        if event_id is not None and not ids.is_id(event_id, "evt"):
            raise errors.InvalidRequest("event_id must be an evt_ id", field="event_id")
        reason = body.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise errors.InvalidRequest("reason must be text of at most 500 characters",
                                        field="reason")

        with db.translate_lock_errors(), db.transaction(self._conn):
            # A typed 404 for an id that is not a `mem_` id or does not exist,
            # and — in `acl` mode — for an item in a project this actor may not
            # see. The three are deliberately indistinguishable.
            self._require_item(memory_id)
            item = self._store.item(memory_id)
            assert item is not None
            if item["project_id"] is not None:
                self._access.require(project_id=item["project_id"], actor_id=actor_id)

            before = _snapshot(item)
            declared = _project(before, action)
            spec = NewEvent(
                event_type=_EVENT_BY_ACTION[action], source_system="core-api",
                source_event_id=None, occurred_at=clock.now(), actor_id=actor_id,
                actor_kind=actor_kind, host_id=host_id,
                project_id=item["project_id"], task_id=item["task_id"],
                payload={
                    "text": "",
                    "action": action,
                    "memory_id": memory_id,
                    "document_id": item["document_id"],
                    "bank_id": item["bank_id"],
                    "source_event_id": item["source_event_id"],
                    "expected_revision": expected,
                    "before": before,
                    # The declared outcome, computed from `before` before
                    # anything is written. An Event cannot contain the observed
                    # after state -- it is appended first, and Events are
                    # immutable -- so it carries what the command says will
                    # happen, and the response carries what did.
                    "after": declared,
                    "reason": reason,
                },
                event_id=event_id,
                # Hashed over the command, not over the item's current state: a
                # re-sent identical `event_id` must be recognised as a replay
                # even though the first delivery already changed the item.
                hash_payload={
                    "text": "", "action": action, "memory_id": memory_id,
                    "document_id": item["document_id"], "bank_id": item["bank_id"],
                    "source_event_id": item["source_event_id"],
                    "expected_revision": expected, "reason": reason,
                },
            )
            stored, replayed = self._events.append(spec)
            if replayed:
                current = self._store.item(memory_id)
                return {"memory": current, "event": stored, "replayed": True,
                        "job": None}
            # State first, then revision. "You cannot obsolete a tombstone" is a
            # property of the target that no revision can change, and answering
            # it is more useful than "your revision is stale" — which is also the
            # answer a caller would get for a perfectly legal command that lost a
            # race. Checking the state first also means an illegal command
            # appends no Event at all.
            self._check_transition(item, action)
            if item["revision"] != expected:
                raise errors.RevisionConflict(
                    "the memory item revision is stale",
                    memory_id=memory_id, current_revision=item["revision"],
                    expected_revision=expected,
                )
            stamp = stored["recorded_at"]
            updated = self._apply(item, action, reason=reason, at=stamp)
            after = _snapshot(updated)
            if {k: after[k] for k in declared} != declared:
                # The Event's declared outcome and the observed one must agree,
                # or the audit record is describing something that did not
                # happen. This is a bug in `_project`, not a caller error.
                raise errors.CoreError(
                    "memory lifecycle transition did not match its declared outcome",
                    memory_id=memory_id, action=action, declared=declared, observed=after,
                )
            job = self._queue_remote(item, action, stored, at=stamp)
            return {"memory": updated, "event": stored, "replayed": False,
                    "job": job, "before": before, "after": after}

    def _check_transition(self, item: dict[str, Any], action: str) -> None:
        status = item["status"]
        if status not in ITEM_STATUSES:  # pragma: no cover - CHECK guards this
            raise errors.CoreError("unknown memory status", status=status)
        if status == "DELETED":
            # A tombstone is terminal. Re-retaining the same source creates a new
            # document id only if the source changed, so reviving a deleted item
            # in place would resurrect content an operator removed on purpose.
            raise errors.InvalidStateTransition(
                "a deleted memory item is a tombstone and cannot be changed",
                memory_id=item["memory_id"], status=status,
            )
        if action in ("obsolete", "delete") and status == "OBSOLETE" and action == "obsolete":
            raise errors.InvalidStateTransition(
                "this memory item is already obsolete", memory_id=item["memory_id"], status=status,
            )
        if action in ("pin", "unpin") and status != "ACTIVE":
            # Pinning an obsolete item would look like a way to keep it around.
            # Ordering is the only thing pin may affect, and an obsolete item is
            # not in the list pin orders.
            raise errors.InvalidStateTransition(
                "an obsolete memory item cannot be pinned; recall and the Context Pack "
                "exclude it regardless of the pin",
                memory_id=item["memory_id"], status=status,
            )

    def _apply(self, item: dict[str, Any], action: str, *, reason: str | None,
               at: str) -> dict[str, Any]:
        memory_id = item["memory_id"]
        revision = int(item["revision"]) + 1
        if action == "obsolete":
            self._conn.execute(
                "UPDATE memory_items SET status='OBSOLETE', pinned=0, revision=?,"
                " lifecycle_reason=?, updated_at=? WHERE memory_id=? AND revision=?",
                (revision, reason, at, memory_id, item["revision"]),
            )
        elif action == "delete":
            # Excluded from Recall by `status`, and the engine side is marked as
            # pending so nothing claims the remote copy is already gone.
            self._conn.execute(
                "UPDATE memory_items SET status='DELETED', pinned=0, revision=?,"
                " engine_state='DELETE_PENDING', lifecycle_reason=?, updated_at=?"
                " WHERE memory_id=? AND revision=?",
                (revision, reason, at, memory_id, item["revision"]),
            )
        elif action == "pin":
            self._conn.execute(
                "UPDATE memory_items SET pinned=1, revision=?, updated_at=?"
                " WHERE memory_id=? AND revision=? AND status='ACTIVE'",
                (revision, at, memory_id, item["revision"]),
            )
        else:
            self._conn.execute(
                "UPDATE memory_items SET pinned=0, revision=?, updated_at=?"
                " WHERE memory_id=? AND revision=?",
                (revision, at, memory_id, item["revision"]),
            )
        updated = self._store.item(memory_id)
        assert updated is not None
        return updated

    def _queue_remote(self, item: dict[str, Any], action: str, event: dict[str, Any],
                      *, at: str) -> dict[str, Any] | None:
        """Queue the remote side of a command, inside the same transaction.

        The item is already locally authoritative-for-recall when this runs, so a
        crash after this point leaves a queued job, not a silent divergence. A
        crash *before* it would leave the local state correct and the remote
        stale, which the next `obsolete` re-run or an Inspector read-back reveals;
        that direction is the safe one.
        """
        if action == "delete":
            return self._jobs.enqueue_delete(memory_id=item["memory_id"],
                                             source_event_id=event["event_id"], at=at)
        if action == "obsolete":
            # The engine's soft retire is per memory unit, so it is queued as
            # engine work rather than done inline: it is network I/O and must
            # not sit inside this transaction. The local exclusion from Recall
            # has already happened, so a crash here leaves a queued job rather
            # than a silent divergence.
            return self._jobs.enqueue_invalidate(memory_id=item["memory_id"],
                                                 source_event_id=event["event_id"], at=at)
        return None

    # -- read-back helpers -------------------------------------------------

    def _require_item(self, memory_id: str) -> sqlite3.Row:
        if not ids.is_id(memory_id, "mem"):
            raise errors.InvalidRequest("memory_id must be a mem_ id", field="memory_id",
                                        value=memory_id)
        row = self._conn.execute("SELECT * FROM memory_items WHERE memory_id=?",
                                 (memory_id,)).fetchone()
        if row is None:
            # Not found, or in a project this actor may not see. See
            # `scope.ProjectAccess.require` for why the two are indistinguishable.
            raise errors.NotFound("memory", memory_id)
        return row

    def detail(self, memory_id: str, *, actor_id: str) -> dict[str, Any]:
        """The Inspector's read of one item, with provenance and last recall."""
        if not ids.is_id(memory_id, "mem"):
            raise errors.NotFound("memory", memory_id)
        item = self._store.item(memory_id)
        if item is None:
            raise errors.NotFound("memory", memory_id)
        if item["project_id"] is not None:
            self._access.require(project_id=item["project_id"], actor_id=actor_id)
        return {
            "memory": item,
            "sources": self._store.sources_of(memory_id),
            "jobs": self._jobs_for(memory_id),
            "recallable": item["status"] == "ACTIVE",
            "lifecycle": lifecycle_summary(item),
            "engine_claim": describe_engine_claim(
                item, invalidated=self._invalidated_remote(memory_id)),
        }

    def _invalidated_remote(self, memory_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM memory_jobs WHERE memory_id=? AND kind='invalidate' AND status='DONE'",
            (memory_id,),
        ).fetchone()
        return row is not None

    def _jobs_for(self, memory_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memory_jobs WHERE memory_id=? ORDER BY created_at, job_id",
            (memory_id,),
        ).fetchall()
        return [jobs_module.job_row(row) for row in rows]


def _snapshot(item: dict[str, Any]) -> dict[str, Any]:
    """The before/after an audit Event carries. Metadata only, never the text.

    The sanitized content is already in `memory_items` and is reachable under
    `memory:read`; duplicating it into every command Event would grow the
    Event Store for no gain and put the text into rows that outlive the item.
    """
    return {
        "memory_id": item["memory_id"],
        "document_id": item["document_id"],
        "bank_id": item["bank_id"],
        "memory_type": item["memory_type"],
        "status": item["status"],
        "pinned": bool(item["pinned"]),
        "engine_state": item["engine_state"],
        "revision": item["revision"],
        "content_sha256": item["content_sha256"],
    }


#: What each command changes. Computed from the `before` snapshot, so the value
#: the Event declares and the value the row ends up with are produced by the same
#: table and cannot drift.
_TRANSITIONS: dict[str, dict[str, Any]] = {
    "obsolete": {"status": "OBSOLETE", "pinned": False, "engine_state": None,
                 "revision": "next"},
    "delete": {"status": "DELETED", "pinned": False, "engine_state": "DELETE_PENDING",
               "revision": "next"},
    "pin": {"status": None, "pinned": True, "engine_state": None, "revision": "next"},
    "unpin": {"status": None, "pinned": False, "engine_state": None, "revision": "next"},
}


def _project(before: dict[str, Any], action: str) -> dict[str, Any]:
    change = _TRANSITIONS[action]
    after = dict(before)
    for column, value in change.items():
        if value is None:
            continue
        after[column] = int(before["revision"]) + 1 if value == "next" else value
    return after


def _positive_revision(body: dict[str, Any]) -> int:
    if "expected_revision" not in body:
        raise errors.MissingExpectedRevision("expected_revision is required")
    value = body["expected_revision"]
    if type(value) is not int or value < 1:
        raise errors.InvalidRequest("expected_revision must be a positive integer",
                                    field="expected_revision")
    return value


def _reject_unknown(body: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)


def lifecycle_summary(item: dict[str, Any]) -> dict[str, Any]:
    """What the Inspector shows for one item, derived from frozen columns only."""
    return {
        "status": item["status"],
        "recallable": item["status"] == "ACTIVE",
        "pinned": bool(item["pinned"]),
        "pin_affects": "inspector_ordering_only",
        "engine_state": item["engine_state"],
        "remote_document_deleted": item["remote_deleted_at"] is not None,
        "last_recalled_at": item["last_recalled_at"],
        "recall_count": item["recall_count"],
        "lifecycle_reason": item["lifecycle_reason"],
    }


def describe_engine_claim(item: dict[str, Any], *, invalidated: bool = False) -> str:
    """Plain statement of what is and is not true remotely, for an Inspector row.

    Split into "what Core does" and "what the engine has been told", because the
    two can legitimately disagree: an obsolete item is excluded from Recall
    locally the moment the command is committed, and the engine's soft retire
    happens later. Collapsing that into one sentence is how an Inspector ends up
    telling an operator the engine agrees when it does not.
    """
    state = item["engine_state"]
    if state == "DELETED":
        return "the remote document and its memories were removed"
    if state == "DELETE_PENDING":
        return "excluded locally; a queued job will remove the remote copy"
    if state == "DELETE_FAILED":
        return "excluded locally; the remote copy may still exist and the deletion failed"
    if item["status"] == "OBSOLETE":
        if invalidated:
            return ("excluded locally and soft-retired in the engine; the engine keeps the "
                    "units for audit and can revert them")
        return "excluded locally; a queued job will soft-retire the engine's memory units"
    if state == "RETAINED":
        return "retained in the engine; logical lifecycle state is local"
    if state == "RETAIN_FAILED":
        return "never confirmed as retained by the engine"
    return "not yet sent to the engine"
