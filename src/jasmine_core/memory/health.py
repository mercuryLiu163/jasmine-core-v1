"""Memory health, reported independently of Core health (ADR 0009 §10; design §14).

Design §14's first row is the whole point of this file: *Hindsight unavailable →
long-term Memory pauses; Rules/State/Evidence/Checkpoint continue normally*.
`GET /v1/health` therefore says nothing about Memory, and this report is what a
Dashboard reads for the Memory panel.

Three reporting rules, each of which is a way a health endpoint usually lies:

* **A probe is not a success.** `last_success_at` advances only when a retain,
  recall or delete actually completed against the engine. `/version` answering
  200 means the process is up; it does not mean the extractor is working. A Core
  whose jobs are all failing still reports a reachable engine here, and the
  `failed_jobs` count is the number that matters.

* **Unconfigured is not unhealthy.** A Core with no `JASMINE_HINDSIGHT_URL` is a
  working Core. The report says `configured: false` and `status: "unconfigured"`,
  and `/v1/health` still says `ok`.

* **No credential, no endpoint detail, no raw exception.** The table has no
  column that could hold one; the report exposes the origin host only
  (`scheme://host:port`, userinfo and query stripped at configuration time), the
  stable error code, and a redacted summary.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock
from . import jobs as jobs_module
from . import scope as scope_module
from .adapter import describe_config
from .store import MemoryStore
from .transport import MemoryTransport

#: Ordered worst-first. A reader of the JSON takes the first match.
STATUS_ORDER = ("unreachable", "failing", "degraded", "idle", "unconfigured", "ok")


def report(conn: sqlite3.Connection, store: MemoryStore, transport: MemoryTransport | None,
           job_store: jobs_module.JobStore, *, mode: str = scope_module.SINGLE_USER,
           probe: bool = False) -> dict[str, Any]:
    """Build the Memory health report.

    `probe=True` performs a live engine call and is only reachable from the CLI
    and the worker, never from a request handler: design §5 forbids network calls
    in a Truth write transaction, and a health endpoint that blocks on a dead
    engine is the second way to make a Core look down.
    """
    engine = store.engine_status() or {}
    if probe:
        from .worker import record_engine_probe

        probed = record_engine_probe(conn, store, transport)
        engine = store.engine_status() or {}
    else:
        probed = {}

    counts = job_store.counts()
    items = store.health_counts()
    configured = bool(describe_config(transport).get("configured")) or bool(
        engine.get("configured")
    )
    last_error = engine.get("last_error_code")
    reachable = engine.get("last_check_ok")

    if not configured:
        status = "unconfigured"
    elif counts["failed"] or items.get("retain_failed", 0) or items.get("delete_failed", 0):
        status = "failing"
    elif reachable is False or last_error:
        status = "unreachable"
    elif counts["pending"] or counts["running"]:
        status = "degraded"
    elif not engine.get("last_success_at"):
        status = "idle"
    else:
        status = "ok"

    payload: dict[str, Any] = {
        "status": status,
        "engine_id": engine.get("engine_id"),
        "configured": configured,
        "reachable": reachable,
        "api_version": engine.get("api_version"),
        "origin_host": engine.get("origin_host"),
        "features": engine.get("features") or {},
        "last_check_at": engine.get("last_attempt_at"),
        "last_success_at": engine.get("last_success_at"),
        "last_error_code": last_error,
        "safe_error_summary": engine.get("safe_error_summary"),
        "jobs": counts,
        "items": items,
        "degraded": status in ("unreachable", "failing", "degraded"),
        "core_impact": "none" if status in ("ok", "idle", "unconfigured") else "memory_paused",
        "deployment": scope_module.ProjectAccess(conn, mode=mode).describe(),
        "checked_at": clock.now_rfc3339(),
    }
    if probe:
        payload["probe"] = {k: v for k, v in probed.items()
                            if k in ("reachable", "api_version", "error_code",
                                     "safe_error_summary", "credential_present")}
    return payload


def persist_probe(conn: sqlite3.Connection, store: MemoryStore,
                  transport: MemoryTransport | None) -> None:
    """Record a probe without building a report. Used by the worker each tick."""
    from .worker import record_engine_probe

    record_engine_probe(conn, store, transport)
