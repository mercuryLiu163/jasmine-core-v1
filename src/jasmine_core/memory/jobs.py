"""Durable asynchronous Memory jobs: enqueue, claim, lease, retry, recovery (ADR 0011 §8).

Design module §12 draws the ordering: `state transaction commits first → enqueue
PENDING → worker retain → DONE or retry with backoff`. The interesting part is
what happens in the window the design does not draw.

**The gap between commit and enqueue.** "Asynchronous" is not durability. If the
process dies after the State transaction commits and before the job row exists,
the content is eligible and there is no job, and a design that only enqueues
inline loses it silently. P4 closes the gap by making the job a *projection* of
committed Events rather than a side effect of a write path:

    `enqueue_from_sources` walks the immutable Event stream above a persisted
    cursor and creates a PENDING job for every eligible source that does not
    already have one. The job inserts and the cursor update commit in the same
    short transaction, so a crash mid-scan either has both or neither, and
    re-running picks up exactly where it stopped.

The unique index `ux_memory_jobs_source(source_event_id, kind)` is what makes
that safe. It is a schema guarantee, not a code convention: a scan that runs
twice over the same Event, on two workers, or after a cursor rollback, cannot
produce a second job. A rolled-back Event is never seen by the scan, because the
scan reads the committed `events` table.

**The gap between a remote success and DONE.** A worker can die after Hindsight
accepted the document and before the local `DONE`. The document id is
deterministic (`eligibility.document_id_for`) and the engine's `update_mode` is
`replace`, so re-sending is an idempotent upsert rather than a second memory.
What P4 claims is therefore at-least-once delivery with an idempotent sink — not
end-to-end exactly-once, which cannot be proven and is not claimed.

**Claiming.** `claim_due` takes the write lock for the length of one `SELECT`
and one `UPDATE`, releases it, and only then does the worker call the engine.
Two workers therefore cannot both claim the same job (the second sees the first's
`RUNNING` row inside the same `BEGIN IMMEDIATE`), and the SQLite write lock is
never held across a network call. A claim carries a lease; a `RUNNING` job whose
lease expired is claimable again, so a worker killed mid-retain loses nothing.

**Retry.** A retryable failure returns the job to `PENDING` with an exponential
backoff and `next_attempt_at`; a permanent failure, or an exhausted attempt
budget, goes to `FAILED`. `last_error_code` is a summary — the full history is
the append-only `memory_job_attempts`, because overwriting one column is how a
"why did this fail" question becomes unanswerable.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from typing import Any

from .. import clock, db, errors, ids
from ..canonical import canonical_json, sha256_hex
from . import eligibility, filtering, scope as scope_module
from .store import ATTEMPT_ID_PREFIX, JOB_ID_PREFIX, MemoryStore, _scope_clause
from .types import JOB_KINDS, JOB_STATUSES

#: Key in `core_meta` holding the recovery scan cursor. A `core_meta` row rather
#: than a P4 table because it must commit in the *same* transaction as the job
#: inserts, and `db.set_meta` is already transaction-safe.
CURSOR_KEY = "memory.recovery.seq"

#: Defaults, frozen here and overridable per call. Recorded on the job row's
#: `max_attempts` so a later change of default does not alter what an existing
#: job is allowed to do.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 120
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
#: Fraction of the backoff added as random jitter, so a restarted fleet does not
#: retry the same batch in lockstep after an outage.
JITTER = 0.25

#: Failure classes. Split on whether trying again could plausibly change the
#: answer, not on the HTTP status, because the worker's decision is "retry or
#: give up", and a 500 from a saturated engine is worth another attempt while a
#: rejected document is not.
RETRYABLE_CODES = frozenset({
    "engine_unreachable", "engine_timeout", "engine_rate_limited", "engine_server_error",
    "engine_retain_failed", "engine_delete_failed", "engine_retain_unconfirmed",
    "engine_conflict",
})
PERMANENT_CODES = frozenset({
    "engine_unauthorized", "engine_forbidden", "engine_rejected", "engine_not_found",
    "engine_protocol_error", "engine_response_too_large", "engine_http_error",
    "query_too_long", "content_too_large", "engine_not_configured",
    "document_deleted_locally",
})


@dataclass(frozen=True)
class ClaimedJob:
    """A job this worker owns, with the content it must send."""

    job: dict[str, Any]
    item: dict[str, Any]
    bank: scope_module.BankRef


#: Which `engine_state` values each job kind may be written *from*.
#:
#: This is the guard against a late worker overwriting a terminal result. A
#: worker whose lease expired mid-`retain` can still come back holding a failure;
#: if a `fail()` were allowed to write from `RETAINED`, that worker would rewrite
#: a successful item as `RETAIN_FAILED` while its job row said `DONE` — the
#: remote document exists and the Inspector would deny it. A retain failure is
#: therefore only writable from the two states a retain can leave behind, and a
#: delete failure only from the two a delete can leave behind.
ENGINE_STATES_FOR_RETAIN_FAILURE = ("PENDING_RETAIN", "RETAIN_FAILED")
ENGINE_STATES_FOR_DELETE_FAILURE = ("DELETE_PENDING", "DELETED", "DELETE_FAILED")
ENGINE_STATES_FOR_INVALIDATE_FAILURE = ("PENDING_RETAIN", "RETAINED", "RETAIN_FAILED")


def failure_predecessors(kind: str) -> tuple[str, ...]:
    if kind == "retain":
        return ENGINE_STATES_FOR_RETAIN_FAILURE
    if kind == "delete":
        return ENGINE_STATES_FOR_DELETE_FAILURE
    return ENGINE_STATES_FOR_INVALIDATE_FAILURE


def is_retryable(error_code: str | None) -> bool:
    if error_code is None:
        return False
    if error_code in PERMANENT_CODES:
        return False
    return error_code in RETRYABLE_CODES


def backoff_seconds(attempt: int, *, rng: random.Random | None = None) -> int:
    """Exponential backoff with jitter, clamped.

    `attempt` is 1-based: the delay after the first failure is `BASE`, after the
    second `2 * BASE`, and so on. The clamp matters because an exponential
    without one eventually exceeds every deployment's patience and turns a
    transient outage into a permanent give-up.
    """
    raw = BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1))
    raw = min(raw, MAX_BACKOFF_SECONDS)
    if JITTER <= 0:
        return raw
    spread = int(raw * JITTER)
    # Clamped *after* the jitter as well: clamping the base alone would let a
    # retry wait 25% longer than the documented maximum.
    return min(raw + (rng or random).randint(-spread, spread), MAX_BACKOFF_SECONDS)


def _next_attempt_at(seconds: int, *, at: str | None = None) -> str:
    from datetime import timedelta

    base = clock.parse_rfc3339(at or clock.now_rfc3339())
    return clock.to_rfc3339(base + timedelta(seconds=max(0, seconds)))


class JobStore:
    """The `memory_jobs` queue and its append-only attempt history."""

    def __init__(self, conn: sqlite3.Connection, store: MemoryStore) -> None:
        self._conn = conn
        self._store = store

    # -- enqueue -----------------------------------------------------------

    def enqueue(self, candidate: eligibility.Candidate, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                at: str | None = None) -> tuple[dict[str, Any] | None, bool]:
        """Create the Memory item and its PENDING job in the caller's transaction.

        Returns `(job, created)`. `created=False` means this source already had a
        job, which is the normal outcome of a re-scan and is not an error.

        The item is created here rather than at enqueue time by the caller
        because the item and the job are one decision: a job that exists without
        an item has nothing to send, and an item without a job would never be
        retained and would look, to an Inspector, like retained memory.
        """
        stamp = at or clock.now_rfc3339()
        existing = self._conn.execute(
            "SELECT * FROM memory_jobs WHERE source_event_id=? AND kind='retain'",
            (candidate.source_event_id,),
        ).fetchone()
        if existing is not None:
            return job_row(existing), False
        item = self._store.item_by_document(candidate.document_id)
        if item is None:
            memory_id = self._store.insert_item(
                bank=eligibility.resolve_destination(candidate),
                memory_type=candidate.memory_type,
                document_id=candidate.document_id,
                source_event_id=candidate.source_event_id,
                provenance=candidate.provenance,
                sanitized_text=candidate.body,
                content_sha256=sha256_hex(candidate.body),
                task_id=candidate.task_id,
                at=stamp,
            )
            item = self._store.item(memory_id)
            assert item is not None
        job_id = ids.new_id(JOB_ID_PREFIX)
        self._conn.execute(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, project_id,"
            " task_id, memory_type, bank_id, document_id, content_sha256, status, retry_count,"
            " max_attempts, next_attempt_at, created_at, updated_at)"
            " VALUES (?,'retain',?,?,?,?,?,?,?,?,'PENDING',0,?,?,?,?)",
            (job_id, item["memory_id"], candidate.source_event_id, candidate.project_id or None,
             candidate.task_id, candidate.memory_type, item["bank_id"], item["document_id"],
             item["content_sha256"], max_attempts, stamp, stamp, stamp),
        )
        row = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
        return job_row(row), True

    def enqueue_from_sources(self, registry: eligibility.SourceRegistry, *,
                             limit: int = 200, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                             at: str | None = None) -> dict[str, Any]:
        """The recovery scan: eligible committed sources → PENDING jobs.

        The scan runs *outside* the write transaction and the transaction covers
        only the job inserts and the cursor update. Two reasons, both learned the
        hard way:

        * **Lock duration.** The eligibility check resolves Evidence artifact
          references, which reads and hashes workspace files. Doing that inside
          `BEGIN IMMEDIATE` holds the SQLite write lock for as long as the I/O
          takes — measured at 8 s for 24 × 1 GiB artifacts, against a 5 s
          `busy_timeout` — so an ordinary scan makes every concurrent Truth write
          fail with `database_busy`. A read of committed data needs no lock, and
          correctness does not depend on the read being inside the transaction:
          the unique index on `(source_event_id, kind)` is what makes a
          duplicate impossible, and a rolled-back Event is invisible to a reader
          either way.
        * **The cursor.** It advances to the highest `seq` the window *examined*,
          not to the highest candidate. Advancing only past candidates livelocks
          on any window with no eligible source, and silently strands every
          eligible Event behind it.
        """
        stamp = at or clock.now_rfc3339()
        after_seq = int(db.get_meta(self._conn, CURSOR_KEY, 0) or 0)
        reasons: list[dict[str, Any]] = []
        window = eligibility.Window(after_seq=after_seq, limit=limit)
        candidates = registry.scan(self._conn, window=window, reasons=reasons)
        created: list[str] = []
        skipped: list[str] = []
        with db.translate_lock_errors(), db.transaction(self._conn):
            # Re-read the cursor inside the transaction: another worker may have
            # advanced it between the read above and the lock being granted, and
            # rewinding it would only cost a re-scan.
            cursor_after = max(int(db.get_meta(self._conn, CURSOR_KEY, 0) or 0),
                               window.cursor_after)
            for candidate in candidates:
                _job, made = self.enqueue(candidate, max_attempts=max_attempts, at=stamp)
                if made:
                    created.append(candidate.document_id)
                else:
                    skipped.append(candidate.document_id)
            db.set_meta(self._conn, CURSOR_KEY, cursor_after)
        return {"cursor_after": cursor_after, "events_examined": window.highest_examined,
                "candidates": len(candidates),
                "created": created, "already_present": skipped,
                "ineligible": reasons,
                "sources": registry.names}

    def cursor(self) -> int:
        return int(db.get_meta(self._conn, CURSOR_KEY, 0) or 0)

    def reset_cursor(self, value: int) -> None:
        """Move the scan cursor. Operator-only; used to replay the whole stream.

        Replaying is safe: the unique index turns every already-known source
        into a no-op, so a full replay re-derives nothing and loses nothing.
        """
        with db.translate_lock_errors(), db.transaction(self._conn):
            db.set_meta(self._conn, CURSOR_KEY, int(value))

    # -- claim -------------------------------------------------------------

    def claim_due(self, *, owner: str, lease_seconds: int = DEFAULT_LEASE_SECONDS,
                  now: str | None = None) -> ClaimedJob | None:
        """Atomically take ownership of one due job.

        `BEGIN IMMEDIATE` is the whole concurrency story: the select and the
        update share one write transaction, so a second worker either waits and
        then sees `RUNNING`, or reads the row before the first commits and then
        blocks on its own update.

        **One in-flight job per `memory_id`.** A `retain` and the `delete` an
        operator ordered later are two jobs for the same document, and nothing
        else in the system ordered them. Without this predicate both can be
        claimed at once: the delete removes the remote document, the slow retain
        — already past its `item is ACTIVE` check — writes it back, and the
        local item is a `DELETED` tombstone whose remote copy nobody will ever
        remove again, because the only job that would have removed it already
        succeeded. Serialising on `memory_id` makes the ordering total: whichever
        is claimed first finishes before the other can start, and both end states
        are consistent.

        The reclaim branch deliberately has no such guard. A `RUNNING` job whose
        lease expired must always be claimable, or a blocked `PENDING` sibling
        could be picked first and leave the expired one stranded forever; at most
        one job per document can be `RUNNING`, so reclaiming cannot re-open the
        window.
        """
        if not owner:
            raise errors.InvalidRequest("lease owner is required", field="owner")
        stamp = now or clock.now_rfc3339()
        expires = _next_attempt_at(lease_seconds, at=stamp)
        with db.translate_lock_errors(), db.transaction(self._conn):
            row = self._conn.execute(
                "SELECT * FROM memory_jobs AS j WHERE"
                " ((j.status='PENDING' AND j.next_attempt_at<=?"
                "   AND NOT EXISTS (SELECT 1 FROM memory_jobs AS r"
                "                   WHERE r.status='RUNNING' AND r.memory_id=j.memory_id))"
                "  OR (j.status='RUNNING' AND j.lease_expires_at IS NOT NULL"
                "      AND j.lease_expires_at<=?))"
                " ORDER BY j.next_attempt_at, j.job_id LIMIT 1",
                (stamp, stamp),
            ).fetchone()
            if row is None:
                return None
            job = job_row(row)
            expired = job["status"] == "RUNNING"
            attempt_number = int(job["retry_count"]) + 1
            cursor = self._conn.execute(
                "UPDATE memory_jobs SET status='RUNNING', lease_owner=?, lease_expires_at=?,"
                " retry_count=?, updated_at=? WHERE job_id=? AND status=?",
                (owner, expires, attempt_number, stamp, job["job_id"], job["status"]),
            )
            if cursor.rowcount != 1:
                return None
            self._attempt(job["job_id"], attempt_number, "CLAIMED", "SUCCESS", owner=owner, at=stamp,
                          safe_error_summary="lease recovered from an expired claim" if expired else None)
            refreshed = self._conn.execute(
                "SELECT * FROM memory_jobs WHERE job_id=?", (job["job_id"],)
            ).fetchone()
            claimed = job_row(refreshed)
        item = self._store.item(claimed["memory_id"])
        if item is None:  # pragma: no cover - FK guarantees the row exists
            raise errors.CoreError("claimed job has no memory item", job_id=claimed["job_id"])
        bank = (scope_module.global_bank() if item["scope_kind"] == "global"
                else scope_module.project_bank(item["project_id"]))
        return ClaimedJob(job=claimed, item=item, bank=bank)

    def _assert_claim_held(self, job: ClaimedJob, *, error_code: str | None = None) -> None:
        """Refuse to act on a job whose lease this worker no longer holds.

        `status='RUNNING'` alone does not identify a claim, because a lease that
        expires leaves the status exactly as it was. Worker A claims a job, its
        lease lapses, worker B claims the same job — still `RUNNING`, different
        `lease_owner`, higher `retry_count` — and A comes back holding a
        receipt. Matching on status lets A fail or finish B's attempt: it can
        write `FAILED` over an in-flight retain, or `DONE` over B's result, and
        the rowcount check passes because the row *is* still RUNNING. The owner
        and the attempt number are the two fields that changed, so both must
        match, and they are read in the same transaction that writes.
        """
        row = self._conn.execute(
            "SELECT lease_owner, retry_count, status FROM memory_jobs WHERE job_id=?",
            (job.job["job_id"],),
        ).fetchone()
        if row is None:  # pragma: no cover - FK guarantees the row exists
            raise errors.NotFound("memory_job", job.job["job_id"])
        held = (row["lease_owner"] == job.job["lease_owner"]
                and int(row["retry_count"]) == int(job.job["retry_count"]))
        if not held:
            raise errors.InvalidStateTransition(
                "this Memory job was reclaimed by another worker while its lease "
                "expired; its result belongs to the current attempt",
                job_id=job.job["job_id"], **({"error_code": error_code} if error_code else {}),
            )

    def _finish(self, job: ClaimedJob, *, receipt: dict[str, Any], at: str) -> None:
        """Move a claimed job to DONE, or refuse if it is no longer this job.

        Two independent guards. `_assert_claim_held` rejects a claim this worker
        lost; the rowcount check rejects a row somebody else finished after the
        read. Neither is redundant: one identifies *whose* claim this is, the
        other that it still exists at write time.
        """
        self._assert_claim_held(job)
        cursor = self._conn.execute(
            "UPDATE memory_jobs SET status='DONE', lease_owner=NULL, lease_expires_at=NULL,"
            " last_error_code=NULL, safe_error_summary=NULL, receipt_json=?, completed_at=?,"
            " updated_at=? WHERE job_id=? AND status='RUNNING' AND lease_owner=?"
            " AND retry_count=?",
            (canonical_json(receipt), at, at, job.job["job_id"],
             job.job["lease_owner"], int(job.job["retry_count"])),
        )
        if cursor.rowcount != 1:
            raise errors.InvalidStateTransition(
                "this Memory job is no longer running; another worker completed it",
                job_id=job.job["job_id"],
            )

    def _attempt(self, job_id: str, attempt_number: int, phase: str, outcome: str, *,
                 owner: str, at: str, receipt: dict[str, Any] | None = None,
                 error_code: str | None = None,
                 safe_error_summary: str | None = None) -> None:
        """Append one immutable attempt record.

        Plain `INSERT`, never `INSERT OR REPLACE` and never an `UPDATE`: a retried
        lease for the same attempt number must not overwrite the earlier record of
        what the first attempt did. A duplicate `(job, attempt, phase)` is a
        genuine conflict and is raised — the table is append-only by trigger, so
        the previous version of this method tried to "recover" with an `UPDATE`
        that the trigger always aborts, converting a clear conflict into an
        opaque `IntegrityError` at COMMIT.
        """
        self._conn.execute(
            "INSERT INTO memory_job_attempts (attempt_id, job_id, attempt_number, phase,"
            " outcome, error_code, safe_error_summary, receipt_json, lease_owner, started_at,"
            " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ids.new_id(ATTEMPT_ID_PREFIX), job_id, attempt_number, phase, outcome,
             error_code, safe_error_summary,
             canonical_json(receipt) if receipt else None, owner, at, at),
        )

    # -- completion --------------------------------------------------------

    def complete(self, job: ClaimedJob, *, receipt: dict[str, Any], at: str | None = None) -> None:
        stamp = at or clock.now_rfc3339()
        with db.translate_lock_errors(), db.transaction(self._conn):
            # Before the item write: a stale worker must not leave a *successful*
            # engine state behind on a job somebody else now owns, even if the
            # job row update would later have been refused.
            self._assert_claim_held(job)
            self._store.note_retained(job.job["memory_id"], receipt)
            self._finish(job, receipt=receipt, at=stamp)
            self._attempt(job.job["job_id"], int(job.job["retry_count"]), "RETAINED", "SUCCESS",
                          owner=job.job["lease_owner"] or "", at=stamp, receipt=receipt)

    def fail(self, job: ClaimedJob, *, error_code: str, safe_error_summary: str,
             at: str | None = None, rng: random.Random | None = None) -> str:
        """Record a failure and decide PENDING-with-backoff or FAILED.

        Returns the resulting status so the caller can log one line without a
        second query. An exhausted attempt budget is `FAILED` even when the error
        was retryable: an indefinitely retried job is how a queue looks healthy
        while nothing is retained.

        Two refusals, both of which a stale worker can provoke:

        * The engine state may only be written from the states *this job kind*
          can fail from. A worker whose lease expired mid-retain and which comes
          back holding a failure must not rewrite a `RETAINED` item as
          `RETAIN_FAILED` while its job row says `DONE` — the remote document
          exists and the Inspector would deny it.
        * The job row update must actually match. If another worker finished the
          job in the meantime, its result is the truth and this failure is stale.
        """
        stamp = at or clock.now_rfc3339()
        summary = filtering.safe_summary(safe_error_summary)
        attempts = int(job.job["retry_count"])
        budget = int(job.job["max_attempts"])
        retry = is_retryable(error_code) and attempts < budget
        status = "PENDING" if retry else "FAILED"
        next_at = _next_attempt_at(backoff_seconds(attempts, rng=rng), at=stamp) if retry else stamp
        # A soft retire failing changes nothing about what the engine holds, so
        # the item's engine state stays whatever it already was.
        target_state = {"retain": "RETAIN_FAILED", "delete": "DELETE_FAILED"}.get(
            job.job["kind"])
        if error_code == "document_deleted_locally":
            # The item was obsoleted or deleted between the scan and the claim.
            # There is nothing to send and nothing to retry, and the item's
            # engine state is whatever the lifecycle command set it to. Writing
            # a retain failure over `DELETE_PENDING` would be a regression, and
            # refusing the write would surface as an unhandled error on every
            # tick. The job is simply permanently FAILED.
            target_state = None
        with db.translate_lock_errors(), db.transaction(self._conn):
            # Checked before `set_engine_state`, not after: a stale worker whose
            # job was reclaimed would otherwise rewrite the *item* first and only
            # then discover the job row was not its own to fail.
            self._assert_claim_held(job, error_code=error_code)
            if target_state is not None:
                self._store.set_engine_state(
                    job.job["memory_id"], engine_state=target_state,
                    expected=failure_predecessors(job.job["kind"]),
                    error_code=error_code, safe_error_summary=summary, at=stamp,
                )
            cursor = self._conn.execute(
                "UPDATE memory_jobs SET status=?, lease_owner=NULL, lease_expires_at=NULL,"
                " last_error_code=?, safe_error_summary=?, next_attempt_at=?, completed_at=?,"
                " updated_at=? WHERE job_id=? AND status='RUNNING' AND lease_owner=?"
                " AND retry_count=?",
                (status, error_code, summary, next_at, None if retry else stamp, stamp,
                 job.job["job_id"], job.job["lease_owner"], int(job.job["retry_count"])),
            )
            if cursor.rowcount != 1:
                raise errors.InvalidStateTransition(
                    "this Memory job is no longer running; another worker completed it",
                    job_id=job.job["job_id"], error_code=error_code,
                )
            self._attempt(job.job["job_id"], attempts, "FAILED",
                          "RETRY" if retry else "FAILED",
                          owner=job.job["lease_owner"] or "", at=stamp,
                          error_code=error_code, safe_error_summary=summary)
            self._store.note_engine_failure(error_code=error_code, safe_error_summary=summary,
                                            at=stamp)
        return status

    # -- manual retry ------------------------------------------------------

    def request_retry(self, job_id: str, *, actor_id: str, actor_kind: str, host_id: str,
                      event_id: str | None = None) -> dict[str, Any]:
        """An operator command: put a failed job back in the queue.

        Three properties the taskbook requires, and how each is met:

        * it may not change what is retained — the job's `document_id`,
          `content_sha256` and `bank_id` are immutable by trigger, and this method
          touches none of them;
        * it may not retry a `DONE` job — a done document is already in the bank
          and a second retain would replace it, which is an upgrade nobody asked
          for;
        * it is idempotent — a repeated `event_id` returns the original command
          Event and writes no second job transition.

        A `RUNNING` job is refused rather than stolen: its worker still holds a
        lease and will finish or fail on its own, and interrupting it would leave
        the engine in a state nobody is tracking.
        """
        from ..events import EventStore
        from ..models import NewEvent

        if actor_kind not in ("human", "system"):
            raise errors.ForbiddenActorKind(
                "only a human or trusted system actor may retry a Memory job"
            )
        if not ids.is_id(job_id, JOB_ID_PREFIX):
            raise errors.InvalidRequest("job_id must be a mjb_ id", field="job_id", value=job_id)
        events = EventStore(self._conn, schema_version=0)
        with db.translate_lock_errors(), db.transaction(self._conn):
            row = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise errors.NotFound("memory_job", job_id)
            job = job_row(row)
            payload = {
                "text": "",
                "job_id": job_id,
                "kind": job["kind"],
                "memory_id": job["memory_id"],
                "document_id": job["document_id"],
                "bank_id": job["bank_id"],
                "previous_status": job["status"],
                "previous_retry_count": job["retry_count"],
            }
            spec = NewEvent(
                event_type="memory.job.retried", source_system="core-api", source_event_id=None,
                occurred_at=clock.now(), actor_id=actor_id, actor_kind=actor_kind,
                host_id=host_id, project_id=job["project_id"], task_id=job["task_id"],
                payload=payload, event_id=event_id,
                # The idempotency hash covers the *command*, not the job's state
                # at the time. A retry of the same request re-sent after the first
                # one already succeeded must be recognised as the same command;
                # hashing `previous_status` would make the second delivery look
                # like a different request and raise a spurious conflict.
                hash_payload={k: v for k, v in payload.items()
                              if k not in ("previous_status", "previous_retry_count")},
            )
            stored, replayed = events.append(spec)
            if replayed:
                current = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?",
                                             (job_id,)).fetchone()
                return {"job": job_row(current), "event": stored, "replayed": True}
            if job["status"] == "DONE":
                raise errors.InvalidStateTransition(
                    "a DONE Memory job is not retried; the document is already retained",
                    job_id=job_id, status=job["status"],
                )
            if job["status"] == "RUNNING":
                raise errors.InvalidStateTransition(
                    "this Memory job is claimed by a worker; wait for its lease to expire",
                    job_id=job_id, status=job["status"],
                )
            if job["status"] == "PENDING":
                # It is already queued. Accepting the command would append an
                # audit Event that records a transition which did not happen.
                raise errors.InvalidStateTransition(
                    "this Memory job is already queued", job_id=job_id, status=job["status"],
                )
            stamp = stored["recorded_at"]
            cursor = self._conn.execute(
                "UPDATE memory_jobs SET status='PENDING', retry_count=0, next_attempt_at=?,"
                " lease_owner=NULL, lease_expires_at=NULL, updated_at=?"
                " WHERE job_id=? AND status='FAILED'",
                (stamp, stamp, job_id),
            )
            if cursor.rowcount != 1:
                raise errors.InvalidStateTransition(
                    "this Memory job is no longer FAILED", job_id=job_id,
                )
            updated = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?",
                                         (job_id,)).fetchone()
        return {"job": job_row(updated), "event": stored, "replayed": False}

    # -- lifecycle jobs ----------------------------------------------------

    def enqueue_invalidate(self, *, memory_id: str, source_event_id: str,
                           max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                           at: str | None = None) -> dict[str, Any]:
        """Queue the engine's soft retire for an obsoleted item.

        The local exclusion from Recall has already happened; this is the remote
        half, and it is queued rather than done inline because it is network
        work. A failure here leaves the item correctly excluded locally with the
        remote copy still recallable *in the engine* — which is why
        `lifecycle.describe_engine_claim` reports the two states separately
        rather than claiming the engine agrees.
        """
        stamp = at or clock.now_rfc3339()
        item = self._store.item(memory_id)
        if item is None:
            raise errors.NotFound("memory", memory_id)
        existing = self._conn.execute(
            "SELECT * FROM memory_jobs WHERE source_event_id=? AND kind='invalidate'",
            (source_event_id,),
        ).fetchone()
        if existing is not None:
            return job_row(existing)
        job_id = ids.new_id(JOB_ID_PREFIX)
        self._conn.execute(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, project_id,"
            " task_id, memory_type, bank_id, document_id, content_sha256, status, retry_count,"
            " max_attempts, next_attempt_at, created_at, updated_at)"
            " VALUES (?,'invalidate',?,?,?,?,?,?,?,?,'PENDING',0,?,?,?,?)",
            (job_id, memory_id, source_event_id, item["project_id"], item["task_id"],
             item["memory_type"], item["bank_id"], item["document_id"],
             item["content_sha256"], max_attempts, stamp, stamp, stamp),
        )
        row = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
        return job_row(row)

    def mark_invalidated(self, job: ClaimedJob, *, receipt: dict[str, Any],
                         at: str | None = None) -> None:
        """Record that the engine's memory units were soft-retired.

        `engine_state` is deliberately left alone: the item is still `RETAINED`
        in the engine, it is just excluded from recall there. The receipt on the
        job is what distinguishes the two, and the Inspector reads it from there.
        """
        stamp = at or clock.now_rfc3339()
        with db.translate_lock_errors(), db.transaction(self._conn):
            self._finish(job, receipt=receipt, at=stamp)
            self._attempt(job.job["job_id"], int(job.job["retry_count"]), "INVALIDATED",
                          "SUCCESS", owner=job.job["lease_owner"] or "", at=stamp, receipt=receipt)

    # -- delete jobs -------------------------------------------------------

    def enqueue_delete(self, *, memory_id: str, source_event_id: str,
                       max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                       at: str | None = None) -> dict[str, Any]:
        """Queue the remote document deletion for a tombstoned item.

        The tombstone and the exclusion from Recall happen locally and
        immediately, in `lifecycle.delete`. This job is the *physical* removal,
        and its failure leaves the item excluded with `DELETE_FAILED` — never a
        claim that the remote copy is gone when it is not.
        """
        stamp = at or clock.now_rfc3339()
        item = self._store.item(memory_id)
        if item is None:
            raise errors.NotFound("memory", memory_id)
        existing = self._conn.execute(
            "SELECT * FROM memory_jobs WHERE source_event_id=? AND kind='delete'",
            (source_event_id,),
        ).fetchone()
        if existing is not None:
            return job_row(existing)
        job_id = ids.new_id(JOB_ID_PREFIX)
        self._conn.execute(
            "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id, project_id,"
            " task_id, memory_type, bank_id, document_id, content_sha256, status, retry_count,"
            " max_attempts, next_attempt_at, created_at, updated_at)"
            " VALUES (?,'delete',?,?,?,?,?,?,?,?,'PENDING',0,?,?,?,?)",
            (job_id, memory_id, source_event_id, item["project_id"], item["task_id"],
             item["memory_type"], item["bank_id"], item["document_id"],
             item["content_sha256"], max_attempts, stamp, stamp, stamp),
        )
        row = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
        return job_row(row)

    def mark_document_deleted(self, job: ClaimedJob, *, receipt: dict[str, Any],
                              at: str | None = None) -> None:
        stamp = at or clock.now_rfc3339()
        with db.translate_lock_errors(), db.transaction(self._conn):
            self._assert_claim_held(job)
            # An operator may delete an item whose retain never ran, or whose
            # retain failed. The tombstone and the exclusion from Recall are
            # already correct in both cases, and the remote document is either
            # absent or about to be removed — so every of these predecessors is
            # legitimate, and refusing any of them would wedge the queue with a
            # crash rather than remove the document.
            self._store.set_engine_state(
                job.job["memory_id"], engine_state="DELETED",
                expected=("DELETE_PENDING", "DELETE_FAILED", "RETAINED", "PENDING_RETAIN",
                          "RETAIN_FAILED"),
                receipt=receipt, remote_deleted=True, at=stamp)
            self._finish(job, receipt=receipt, at=stamp)
            self._attempt(job.job["job_id"], int(job.job["retry_count"]), "DELETED", "SUCCESS",
                          owner=job.job["lease_owner"] or "", at=stamp, receipt=receipt)

    # -- reading -----------------------------------------------------------

    def job(self, job_id: str) -> dict[str, Any] | None:
        if not ids.is_id(job_id, JOB_ID_PREFIX):
            return None
        row = self._conn.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
        return None if row is None else job_row(row)

    def jobs(self, *, project_id: str | None = None, projects: tuple[str, ...] | None = None,
             status: str | None = None, kind: str | None = None,
             memory_type: str | None = None, limit: int = 100,
             after_job_id: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in JOB_STATUSES:
            raise errors.InvalidRequest(f"status must be one of {', '.join(JOB_STATUSES)}",
                                        field="status", value=status)
        if kind is not None and kind not in JOB_KINDS:
            raise errors.InvalidRequest(f"kind must be one of {', '.join(JOB_KINDS)}",
                                        field="kind", value=kind)
        clauses: list[str] = []
        params: list[Any] = []
        # Project jobs *plus* global-bank jobs: the global bank is shared by
        # every project in this Core, so its jobs belong in every project's
        # view. Project A's own jobs are never in project B's list.
        _scope_clause(clauses, params, project_id=project_id, projects=projects)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if after_job_id is not None:
            clauses.append("job_id > ?")
            params.append(after_job_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self._conn.execute(
            f"SELECT * FROM memory_jobs{where} ORDER BY job_id LIMIT ?", params
        ).fetchall()
        return [job_row(row) for row in rows]

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        return [
            dict(row, receipt=json.loads(row["receipt_json"]) if row["receipt_json"] else None)
            for row in self._conn.execute(
                "SELECT * FROM memory_job_attempts WHERE job_id=? ORDER BY attempt_number, phase",
                (job_id,),
            )
        ]

    def counts(self, *, project_id: str | None = None,
               projects: tuple[str, ...] | None = None) -> dict[str, int]:
        clauses: list[str] = []
        params: list[Any] = []
        _scope_clause(clauses, params, project_id=project_id, projects=projects)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        result: dict[str, int] = {}
        for status in JOB_STATUSES:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM memory_jobs{where}{' AND' if where else ' WHERE'} status=?",
                (*params, status),
            ).fetchone()
            result[status.lower()] = int(row["n"])
        return result


def job_row(row: sqlite3.Row | None) -> dict[str, Any]:
    """A `memory_jobs` row as a dict with its receipt decoded.

    Named publicly because `lifecycle.MemoryLifecycle` reads the same rows for
    the Inspector, and two readers of one table should not each hand-roll the
    JSON decoding.
    """
    if row is None:  # pragma: no cover - callers check first
        raise errors.NotFound("memory_job", "unknown")
    job = dict(row)
    receipt = job.get("receipt_json")
    job["receipt"] = json.loads(receipt) if receipt else None
    job.pop("receipt_json", None)
    return job


__all__ = [
    "ClaimedJob", "JobStore", "CURSOR_KEY", "DEFAULT_LEASE_SECONDS", "DEFAULT_MAX_ATTEMPTS",
    "PERMANENT_CODES", "RETRYABLE_CODES", "backoff_seconds", "is_retryable", "job_row",
]
