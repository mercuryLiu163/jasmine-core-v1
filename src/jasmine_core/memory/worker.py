"""The Memory Job Worker: an explicit process that owns every engine write (ADR 0009 §8).

The worker is started deliberately — `jasmine-core memory-worker` — and never as
a side effect of an API request. Two reasons, both from the design:

* A dead Hindsight must pause Memory and nothing else (design §14). If the worker
  were a thread inside the API server, a wedged engine call would compete with
  request handling for the same process. Out here, the API server never makes an
  engine call at all except a Recall issued by the Context Builder.
* The deployment topology in design §13 lists `jasmine-memory-worker` as its own
  service next to `jasmine-core-api` and `hindsight-server`.

## The transaction discipline this module exists to keep

    claim  →  BEGIN IMMEDIATE … COMMIT        (short; no remote call inside)
    retain →  no transaction at all
    finish →  BEGIN IMMEDIATE … COMMIT        (short)

The SQLite write lock is never held across a network call, and a Truth write
transaction is never opened at all. `sqlite3.Connection.in_transaction` is
asserted false immediately before every engine call, so a future edit that wraps
one in a transaction fails a test instead of quietly serialising every Core
write behind the engine's latency. That is the property the taskbook's
"无网络调用跨 SQLite write transaction、无锁占用随远端时延增长" check measures.

## Delivery semantics, stated honestly

At-least-once delivery with an idempotent sink. A crash between a successful
`retain` and the local `DONE` causes the document to be sent again; because the
document id is deterministic and the engine's `update_mode` is `replace`, that
re-send replaces the document rather than duplicating the memory. End-to-end
exactly-once cannot be proven against an LLM-backed extractor and is not
claimed.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable

from .. import clock
from .. import db
from . import filtering
from . import jobs as jobs_module
from . import provenance
from .adapter import MemoryAdapter
from .eligibility import SourceRegistry
from .store import MemoryStore
from .transport import MemoryTransport, TransportError
from .types import RetainRequest, RetainReceipt

#: A worker identifies itself so a stuck lease is identifiable in `GET
#: /v1/memory/jobs`. Host plus pid is enough to be useful and carries nothing
#: sensitive.
DEFAULT_IDLE_SLEEP_SECONDS = 2.0


@dataclass
class WorkerReport:
    """What one pass did. Returned so a test and the CLI can both assert on it."""

    events_examined: int = 0
    candidates: int = 0
    created: list[str] | None = None
    claimed: str | None = None
    outcome: str | None = None       # "retained" | "deleted" | "retry" | "failed" | "idle"
    error_code: str | None = None
    receipt: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "events_examined": self.events_examined, "candidates": self.candidates,
            "created": self.created or [],
            "claimed": self.claimed, "outcome": self.outcome, "error_code": self.error_code,
            "receipt": self.receipt,
        }


class MemoryWorker:
    """One process, one lease owner, one job at a time."""

    def __init__(self, conn: sqlite3.Connection, store: MemoryStore,
                 transport: MemoryTransport | None, *,
                 registry: SourceRegistry | None = None,
                 lease_owner: str | None = None,
                 lease_seconds: int = jobs_module.DEFAULT_LEASE_SECONDS,
                 max_attempts: int = jobs_module.DEFAULT_MAX_ATTEMPTS,
                 scan_limit: int = 200,
                 log: Callable[[str], None] | None = None) -> None:
        self._conn = conn
        self._log = log
        self._store = store
        self._transport = transport
        self._registry = registry or SourceRegistry()
        self._jobs = jobs_module.JobStore(conn, store)
        self._adapter = MemoryAdapter(conn, store, transport)
        self.lease_owner = lease_owner or default_lease_owner()
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.scan_limit = scan_limit

    # -- one pass ----------------------------------------------------------

    def tick(self, *, scan: bool = True) -> WorkerReport:
        """Scan for new work, then claim and process at most one job.

        At most one, deliberately: the interesting concurrency property is that
        *two workers* do not double-claim, which is tested with two real
        connections rather than simulated inside one.
        """
        report = WorkerReport()
        if scan:
            try:
                scan_report = self._jobs.enqueue_from_sources(
                    self._registry, limit=self.scan_limit, max_attempts=self.max_attempts
                )
            except db.errors.DatabaseBusy:
                # A concurrent writer is normal for a background worker against a
                # live Core. It is not a reason to exit: the next tick retries.
                report.outcome = "busy"
                report.error_code = "database_busy"
                return report
            report.events_examined = int(scan_report["events_examined"])
            report.candidates = int(scan_report["candidates"])
            report.created = list(scan_report["created"])
        try:
            claimed = self._jobs.claim_due(owner=self.lease_owner,
                                            lease_seconds=self.lease_seconds)
        except db.errors.DatabaseBusy:
            report.outcome = "busy"
            report.error_code = "database_busy"
            return report
        if claimed is None:
            report.outcome = "idle"
            return report
        report.claimed = claimed.job["job_id"]
        kind = claimed.job["kind"]
        try:
            if kind == "delete":
                self._process_delete(claimed, report)
            elif kind == "invalidate":
                self._process_invalidate(claimed, report)
            else:
                self._process_retain(claimed, report)
        except db.errors.DatabaseBusy:
            # The engine call already happened; the local write did not. The job
            # stays RUNNING and its lease expires, so the next worker re-sends
            # the same document — which is the idempotent path, not a lost job.
            report.outcome = "busy"
            report.error_code = "database_busy"
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the service
            # A defect in the handling of one job is not a reason to exit: the
            # remaining queue still has to drain, and a crash loop on a single
            # job is the worst possible outcome for a background service.
            report.outcome = "error"
            report.error_code = f"unhandled_{type(exc).__name__}"
            if self._log is not None:
                self._log(f"memory-worker {self.lease_owner}: job {report.claimed} raised "
                          f"{type(exc).__name__}: {filtering.safe_summary(str(exc))}")
        return report

    def _process_retain(self, claimed: jobs_module.ClaimedJob, report: WorkerReport) -> None:
        item = claimed.item
        if item["status"] != "ACTIVE":
            # Obsoleted or deleted between the scan and the claim. There is
            # nothing to send, and sending it would put back content an operator
            # removed.
            self._jobs.fail(claimed, error_code="document_deleted_locally",
                            safe_error_summary="the memory item is no longer active")
            report.outcome = "failed"
            report.error_code = "document_deleted_locally"
            return
        request = RetainRequest(
            bank=claimed.bank,
            document_id=item["document_id"],
            memory_type=item["memory_type"],
            content=item["sanitized_text"],
            provenance=provenance.Provenance(
                source_event_ids=tuple(
                    row["source_event_id"] for row in self._store.sources_of(item["memory_id"])
                ) or (item["source_event_id"],),
                evidence_ids=tuple(item["evidence_ids"]),
                origin_rule_id=item["origin_rule_id"],
                origin_rule_version=item["origin_rule_version"],
                # The Event this item was actually created from, which is the one
                # an Inspector walks back to. Carrying the Rule version without
                # it produced an item whose `origin_event_id` was always None.
                origin_event_id=item["source_event_id"],
            ),
            context=_retain_context(item),
            timestamp=item["created_at"],
            metadata={
                provenance.MEMORY_ID_KEY: item["memory_id"],
                provenance.CONTENT_SHA256_KEY: item["content_sha256"],
                provenance.RETAINED_AT_KEY: item["created_at"],
            },
        )
        self._assert_no_transaction("retain")
        try:
            receipt = self._adapter.retain(request)
        except TransportError as exc:
            status = self._jobs.fail(claimed, error_code=exc.code,
                                     safe_error_summary=exc.safe_error_summary)
            report.outcome = "retry" if status == "PENDING" else "failed"
            report.error_code = exc.code
            return
        self._jobs.complete(claimed, receipt=receipt.as_dict())
        report.outcome = "retained"
        report.receipt = receipt.as_dict()

    def _process_delete(self, claimed: jobs_module.ClaimedJob, report: WorkerReport) -> None:
        """Physically remove the remote document.

        Only reached for a tombstoned item, so there is no question of deleting
        something still in use. The remote memory unit ids are read from the
        document first so the attempt record can name what was removed; the
        *delete* itself is by document, which is the only operation the engine
        offers that removes every unit at once.
        """
        self._assert_no_transaction("delete")
        try:
            transport = self._require_transport("delete")
            units = transport.list_document_memories(
                bank=claimed.bank, document_id=claimed.job["document_id"]
            )
            receipt = transport.delete_document(
                bank=claimed.bank, document_id=claimed.job["document_id"]
            )
        except TransportError as exc:
            status = self._jobs.fail(claimed, error_code=exc.code,
                                     safe_error_summary=exc.safe_error_summary)
            report.outcome = "retry" if status == "PENDING" else "failed"
            report.error_code = exc.code
            return
        receipt = {**receipt, "memory_units_seen": len(units),
                   "memory_unit_ids": [u.get("id") for u in units if isinstance(u.get("id"), str)]}
        self._jobs.mark_document_deleted(claimed, receipt=receipt)
        report.outcome = "deleted"
        report.receipt = receipt

    def _require_transport(self, phase: str) -> MemoryTransport:
        """The engine, or a `TransportError` that the retry machinery understands.

        Every engine path goes through here. Without it, `self._transport` being
        `None` produces an `AttributeError` on `NoneType`, which is not a
        `TransportError`, so it escapes the failure classification and takes the
        whole worker service down on its second tick.
        """
        if self._transport is None:
            raise TransportError(
                "engine_not_configured",
                "no Hindsight endpoint is configured for this Core",
                retryable=False,
            )
        return self._transport

    def _process_invalidate(self, claimed: jobs_module.ClaimedJob,
                            report: WorkerReport) -> None:
        """Soft-retire every remote memory unit of an obsoleted document.

        Per-unit, because that is the operation the engine offers. The item is
        already excluded from Recall locally, so a failure here is a stale remote
        copy rather than a correctness problem, and it is reported as such.
        """
        self._assert_no_transaction("invalidate")
        try:
            units = self._transport.list_document_memories(  # type: ignore[union-attr]
                bank=claimed.bank, document_id=claimed.job["document_id"]
            )
            unit_ids = [u["id"] for u in units
                        if isinstance(u.get("id"), str) and u.get("state") != "invalidated"]
            for unit_id in unit_ids:
                self._transport.invalidate_memory(  # type: ignore[union-attr]
                    bank=claimed.bank, memory_id=unit_id,
                    reason=f"jasmine memory {claimed.item['memory_id']} was obsoleted")
        except TransportError as exc:
            status = self._jobs.fail(claimed, error_code=exc.code,
                                     safe_error_summary=exc.safe_error_summary)
            report.outcome = "retry" if status == "PENDING" else "failed"
            report.error_code = exc.code
            return
        receipt = {"document_id": claimed.job["document_id"],
                   "memory_units_seen": len(units), "memory_units_invalidated": len(unit_ids),
                   "memory_unit_ids": unit_ids}
        self._jobs.mark_invalidated(claimed, receipt=receipt)
        report.outcome = "invalidated"
        report.receipt = receipt

    def _assert_no_transaction(self, phase: str) -> None:
        """Fail loudly rather than hold the write lock across the engine.

        This is a testable invariant, not a comment: `db.transaction` is the only
        way P4 opens a write transaction, and every P4 write closes it before
        returning.
        """
        if self._conn.in_transaction:
            raise RuntimeError(
                f"refusing to call the engine during the {phase} phase: a SQLite write "
                "transaction is open and the write lock would be held across a network call"
            )

    # -- loop --------------------------------------------------------------

    def run(self, *, max_ticks: int | None = None, idle_sleep: float = DEFAULT_IDLE_SLEEP_SECONDS,
            should_stop: Callable[[], bool] | None = None,
            log: Callable[[str], None] | None = None) -> list[WorkerReport]:
        """Process until stopped, or for `max_ticks` passes.

        An unbounded run (a service) backs off while idle; a bounded run (a test
        or `jasmine-core memory drain`) does not sleep, so it returns promptly.
        """
        reports: list[WorkerReport] = []
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            if should_stop is not None and should_stop():
                break
            report = self.tick()
            reports.append(report)
            if log:
                log(f"memory-worker {self.lease_owner}: {report.outcome}"
                    + (f" job={report.claimed}" if report.claimed else ""))
            ticks += 1
            if report.outcome == "idle" and max_ticks is None:
                time.sleep(idle_sleep)
        return reports


def default_lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _retain_context(item: dict[str, Any]) -> str:
    """The `context` label Hindsight injects into its extraction prompt.

    It is not decoration. Per the engine's own documentation, `context` is
    injected directly into the fact-extraction prompt and changes what is
    extracted, so it states the Jasmine memory type rather than a generic
    "jasmine" — the type is the difference between a lesson and a stray
    observation, and it is also the text an Inspector shows.
    """
    return f"jasmine {item['memory_type'].lower().replace('_', ' ')}"


def record_engine_probe(conn: sqlite3.Connection, store: MemoryStore,
                        transport: MemoryTransport | None) -> dict[str, Any]:
    """Record what the engine says about itself, for `GET /v1/memory/health`.

    Called by the worker and the CLI, never by a request handler: a health
    probe is a network call, and design §5 forbids those in a Truth write
    transaction. A Core with no worker still reports whatever the last probe
    recorded.
    """
    from . import ENGINE_ID
    from .adapter import describe_config
    from .hindsight import check_supported
    from .. import db as db_module

    at = clock.now_rfc3339()
    described = describe_config(transport)
    payload: dict[str, Any] = {
        "reachable": False, "api_version": None, "features": {},
        "error_code": "engine_not_configured", "safe_error_summary": "",
    }
    if transport is not None:
        probed = transport.version()
        payload = {
            "reachable": bool(probed.get("reachable")),
            "api_version": probed.get("api_version"),
            "features": probed.get("features") or {},
            "error_code": probed.get("error_code"),
            "safe_error_summary": probed.get("safe_error_summary") or "",
        }
        if payload["reachable"]:
            ok, reason = check_supported(probed)
            if not ok:
                payload["error_code"] = "engine_version_unsupported"
                payload["safe_error_summary"] = reason or ""
    with db_module.translate_lock_errors(), db_module.transaction(conn):
        store.record_engine_status(
            origin_host=str(described.get("origin_host") or ""),
            configured=bool(described.get("configured")),
            api_version=payload["api_version"], features=payload["features"],
            reachable=payload["reachable"], error_code=payload["error_code"],
            safe_error_summary=payload["safe_error_summary"], at=at,
        )
    return {"engine_id": ENGINE_ID, **described, **payload}


__all__ = ["MemoryWorker", "WorkerReport", "default_lease_owner", "record_engine_probe",
           "RetainReceipt"]
