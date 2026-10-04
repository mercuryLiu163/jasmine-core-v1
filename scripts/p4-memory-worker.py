#!/usr/bin/env python3
"""P4's Memory worker: the process P3's Memory provider spawns for one query.

This is the executable half of P3's `jasmine.memory-slot.v1` contract. P3 owns
the contract (`context/memory_provider.py`) and this repository owns the worker
that speaks it, which is why it is a script an operator points `argv` at rather
than something the provider embeds.

The protocol, exactly:

* **stdin** carries one canonical-JSON query and is then closed. The provider
  writes it non-blocking and caps what it will read back, so this reads a bounded
  request rather than trusting the writer.
* **stdout** carries one JSON result. The provider refuses more than 64 KiB, so
  an answer that would not fit is degraded instead of truncated.
* **the acting identity** is not in P3's query, so it comes from
  `JASMINE_MEMORY_ACTOR_ID` in the operator's environment — part of the pinned
  deployment configuration — or the answer is degraded with
  `memory_actor_not_supplied`. Every recall leaves a trace that records who
  asked, and inventing an actor either breaks the foreign key or claims a
  permission nobody granted.
* **identity** arrives in `JASMINE_MEMORY_PROVIDER_ID` and
  `JASMINE_MEMORY_CONFIG_DIGEST`, and is echoed back verbatim: a worker that
  answers under a different identity is refused, which is what stops one
  deployment's worker answering for another's.
* **the result's `banks` must be exactly the requested set** — each with a
  status — and P3 compares them as `(scope, bank_id, project_id)` triples. A
  missing or extra bank degrades the whole answer, so they are echoed from the
  request rather than derived.
* at most **8 items**, and every item is re-verified against the Core database by
  P3 before it can reach a Context Pack.

Exit non-zero only when the request itself cannot be honoured — unreadable input,
no identity. Everything else is reported as `degraded` with a code: a degraded
answer is information the caller can render, and a dead process is a hole.

## What this worker will and will not send today

P3's `filter_memory` accepts only `VERIFIED_OUTCOME` and `FAILURE_LESSON`, and it
re-derives every claim from the local database: the item's `fact` must name a
real Evidence row, `fact.subject_event_id` must be that row's `change_event_id`,
`fact.value` must equal `evidence[fact.key]`, and both of the Evidence's own
events must appear in `source_event_ids`. So the worker does not forward an
engine hit — it re-states it in terms P3 can check, reading the Evidence row
itself.

`FAILURE_LESSON` is therefore emitted when the item has a resolving Evidence row
whose status is `FAIL`; that is the whole of its requirement.

`VERIFIED_OUTCOME` additionally needs P1's `verified_transition` proof, which
lives in P3's branch and is not merged, so it is deliberately **not** emitted
yet: sending it without the proof would produce an item P3 filters as
`unverified_outcome` and would be guessing at a contract this worker does not own.

Two fields are written to satisfy the contract's shape and are flagged for P3's
review rather than treated as settled: `confidence` is `1.0` for every item
(locally re-verified provenance; P3 validates the range and never reads the
value), and `source_task_id` is the Evidence row's task.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

#: P3 writes a bounded request and reads at most this much back.
MAX_BYTES = 65536
#: P3's own item cap, and its rule that an empty result carries none.
MAX_ITEMS = 8
#: P3's `_text` bound on item text.
MAX_TEXT = 2000
#: P3's `MemoryUnit` type names are P4's own; the wire uses the same five.
MEMORY_TYPES = ("CONFIRMED_DECISION", "STABLE_PROJECT_FACT", "RESOLVED_ISSUE",
                "VERIFIED_OUTCOME", "FAILURE_LESSON")
#: Only these two survive P3's `filter_memory`.
_P3_ELIGIBLE = ("FAILURE_LESSON", "VERIFIED_OUTCOME")


def _fail(message: str) -> int:
    """The request could not be honoured; the provider reports a dead worker."""
    sys.stderr.write(f"p4-memory-worker: {message}\n")
    return 2


def _bank_triple(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scope = raw.get("scope")
    bank_id = raw.get("bank_id")
    project_id = raw.get("project_id")
    if scope not in ("GLOBAL", "PROJECT") or not isinstance(bank_id, str):
        return None
    if scope == "GLOBAL":
        if bank_id != "jasmine-global" or project_id is not None:
            return None
    elif not isinstance(project_id, str) or bank_id != f"jasmine-project-{project_id}":
        return None
    return {"scope": scope, "bank_id": bank_id, "project_id": project_id}


def _envelope(banks: list[dict[str, Any]], *, status: str, items: list[dict[str, Any]],
              provider_id: str, config_digest: str, trace_id: str | None,
              error_code: str | None) -> dict[str, Any]:
    """Exactly the seven keys P3 validates — it rejects unknown ones."""
    return {
        "status": status,
        "provider_id": provider_id,
        "provider_config_digest": config_digest,
        "banks": banks,
        "items": items[:MAX_ITEMS],
        "query_trace_id": trace_id,
        "error_code": error_code,
    }


def _bank_status(outcome: Any) -> str:
    """P3's vocabulary from P4's, per bank.

    P4 reports `ok` / `failed` / `skipped` and says how many results it kept;
    P3 wants `ok` / `empty` / `degraded`. A bank that answered and had nothing
    is `empty`, which is a different fact from a bank that failed, and both are
    different from a bank that was never asked.
    """
    if outcome is None or outcome.status in ("failed", "skipped"):
        return "degraded"
    return "ok" if outcome.result_count else "empty"


def _evidence_rows(conn: Any, evidence_ids: tuple[str, ...]) -> list[Any]:
    rows = []
    for evidence_id in evidence_ids:
        row = conn.execute(
            "SELECT evidence_id, task_id, status, source_event_id, change_event_id"
            " FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is not None and row["change_event_id"]:
            rows.append(row)
    return rows


def _fact_for(conn: Any, row: Any) -> dict[str, Any] | None:
    """P3's fact shape over a real Evidence row.

    `EVIDENCE_RESULT` with `key='status'` is the one mapping P4 can state
    truthfully for every Evidence row it keeps: `subject_event_id` is the row's
    own change event, `value` is the column P3 will compare against, and the
    artifacts P4 records separately are not what a FAILURE_LESSON recalls.
    """
    status = row["status"]
    if not isinstance(status, str) or not status:
        return None
    return {"fact_type": "EVIDENCE_RESULT", "subject_event_id": row["change_event_id"],
            "evidence_id": row["evidence_id"], "key": "status", "value": status}


def _item_for(conn: Any, item: Any) -> dict[str, Any] | None:
    """One P4 recall item in the shape P3 re-verifies, or `None` to leave it out."""
    if item.memory_type not in _P3_ELIGIBLE:
        return None
    recorded_at = None
    for row in _evidence_rows(conn, tuple(item.provenance.evidence_ids)):
        fact = _fact_for(conn, row)
        if fact is None:
            continue
        if item.memory_type == "FAILURE_LESSON":
            # P3 requires `not_failure_evidence` otherwise; it does not read the
            # engine's claim, it reads this row's status.
            if row["status"] != "FAIL":
                continue
            verified_transition = None
        else:
            # Needs P1's `verified_transition` proof, which lives in P3's branch.
            continue
        event = conn.execute("SELECT occurred_at FROM events WHERE event_id=?",
                             (row["change_event_id"],)).fetchone()
        if event is None or not isinstance(event["occurred_at"], str):
            continue
        recorded_at = event["occurred_at"]
        scope = "GLOBAL" if item.scope_kind == "global" else "PROJECT"
        bank = {"scope": scope, "bank_id": item.bank_id, "project_id": item.project_id}
        mapped = {
            "memory_id": item.memory_id,
            "memory_type": item.memory_type,
            "bank": bank,
            # P3 re-checks both against the database and against the Evidence's
            # task; citing the Evidence's own events is what makes that check
            # pass, and it is what the memory is actually about.
            "source_event_ids": [row["source_event_id"], row["change_event_id"]],
            "source_task_id": row["task_id"],
            "origin_rule_ref": None,
            "evidence_ids": [row["evidence_id"]],
            "backend_classification": None,
            "text": item.text[:MAX_TEXT],
            "confidence": 1.0,
            "verified_transition_event_id": verified_transition,
            "recorded_at": recorded_at,
            "fact": fact,
        }
        mapped["provenance_digest"] = _provenance_digest(mapped)
        return mapped
    return None


def _provenance_digest(item: dict[str, Any]) -> str:
    """P3's digest over the exact key tuple it hashes.

    Recomputed here rather than delegated, because P3 recomputes it from the
    item it receives and refuses a mismatch. It uses this same helper
    (`canonical.sha256_hex`) over this same tuple, so the two agree by
    construction rather than by convention.
    """
    from jasmine_core.canonical import sha256_hex

    return sha256_hex({key: item[key] for key in (
        "memory_type", "bank", "source_event_ids", "source_task_id", "origin_rule_ref",
        "evidence_ids", "verified_transition_event_id", "fact")})


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return _fail("the query exceeds the bounded request size")
    try:
        query = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _fail("the query was not JSON")
    if not isinstance(query, dict):
        return _fail("the query was not an object")

    provider_id = os.environ.get("JASMINE_MEMORY_PROVIDER_ID")
    config_digest = os.environ.get("JASMINE_MEMORY_CONFIG_DIGEST")
    if not provider_id or not config_digest:
        return _fail("no Memory provider identity in the environment")

    raw_banks = query.get("banks")
    if not isinstance(raw_banks, list) or not 1 <= len(raw_banks) <= 2:
        return _fail("the query did not carry one or two banks")
    triples = [_bank_triple(entry) for entry in raw_banks]
    if any(triple is None for triple in triples):
        return _fail("the query carried an invalid bank")

    db_path = os.environ.get("JASMINE_CORE_DB")
    if not db_path:
        return _fail("no Core database configured for the Memory worker")

    repo = Path(__file__).resolve().parents[1]
    src = str(repo / "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from jasmine_core import db as db_module
    from jasmine_core import errors
    from jasmine_core.memory.adapter import MemoryAdapter
    from jasmine_core.memory.hindsight import HindsightTransport, config_from_env
    from jasmine_core.memory.store import MemoryStore
    from jasmine_core.memory.types import RecallRequest

    def answer(items: list[dict[str, Any]], trace_id: str | None,
               error_code: str | None, outcomes: dict[str, Any]) -> int:
        banks = []
        for triple in triples:
            outcome = outcomes.get(triple["bank_id"])
            bank = dict(triple)
            # A bank P4 never reported on cannot be called `ok`: the answer would
            # be claiming something about a bank nobody asked.
            bank["status"] = _bank_status(outcome)
            bank["error_code"] = getattr(outcome, "error_code", None) or error_code
            banks.append(bank)
        # P3 refuses `empty` with items, and refuses `ok` when a bank degraded.
        if any(bank["status"] == "degraded" for bank in banks):
            status = "degraded"
        elif not items:
            status = "empty"
        else:
            status = "ok"
        envelope = _envelope(banks, status=status, items=items, provider_id=provider_id,
                             config_digest=config_digest, trace_id=trace_id,
                             error_code=error_code)
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            # The provider refuses an oversized answer outright. An empty
            # degraded answer with a code is the honest way to say the same.
            envelope = _envelope(banks, status="degraded", items=[], provider_id=provider_id,
                                 config_digest=config_digest, trace_id=trace_id,
                                 error_code="memory_result_too_large")
            encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0

    try:
        conn = db_module.connect(db_path)
    except Exception:
        # Not even the database: every requested bank is degraded, because none
        # of them can be answered for.
        from jasmine_core.memory.types import BankOutcome

        return answer([], None, "memory_unavailable", {
            triple["bank_id"]: BankOutcome(triple["bank_id"], triple["scope"].lower(),
                                           "failed", error_code="memory_unavailable")
            for triple in triples})
    try:
        project_id = query.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return _fail("the query did not name a project")
        # The acting identity. P3's query does not carry one, so it comes from
        # the operator's environment — a real, configured actor whose id is part
        # of the pinned deployment digest — or the answer is degraded. It is
        # never invented: the trace records who asked, and a made-up `actor_id`
        # either fails the foreign key or, worse, is one that happens to be
        # granted something.
        actor_id = query.get("actor_id") if isinstance(query.get("actor_id"), str) else None
        actor_id = actor_id or os.environ.get("JASMINE_MEMORY_ACTOR_ID")
        if not actor_id:
            return answer([], None, "memory_actor_not_supplied", {})
        if conn.execute("SELECT 1 FROM actors WHERE actor_id=?",
                        (actor_id,)).fetchone() is None:
            return answer([], None, "memory_actor_unknown", {})
        engine = config_from_env()
        transport = HindsightTransport(engine) if engine is not None else None
        adapter = MemoryAdapter(conn, MemoryStore(conn, schema_version=0), transport)
        outcome = adapter.recall(RecallRequest(
            project_id=project_id,
            # `single_user` does not read this: the mode has no second user to
            # check against, which is why a placeholder is not a security claim
            # here and why `acl` above refuses instead of using one.
            actor_id=actor_id or "act_" + "0" * 26,
            query=query.get("query_text") if isinstance(query.get("query_text"), str) else "",
            task_id=query.get("task_id") if isinstance(query.get("task_id"), str) else None,
            session_id=query.get("session_id") if isinstance(query.get("session_id"), str)
            else None,
            types=tuple(name for name in (query.get("types") or ())
                        if name in MEMORY_TYPES) or tuple(MEMORY_TYPES),
            max_tokens=query.get("max_tokens") if isinstance(query.get("max_tokens"), int)
            else None,
        ))
    except errors.CoreError as exc:
        # A refusal by Core is an answer, not a crash: the project is not visible
        # to this actor, the deployment is not ready, the request is malformed.
        # The provider renders `degraded` with the code and carries on; exiting
        # non-zero would turn "you may not read this" into "memory is broken".
        return answer([], None, getattr(exc, "code", None) or "memory_not_available", {})
    except Exception as exc:
        # The adapter does not raise for engine failure, so reaching here is a
        # defect in this worker's own handling: it must read as "failed to
        # answer", never as "memory had nothing to say". The detail goes to
        # stderr, which the provider sends to /dev/null but an operator running
        # the worker by hand needs.
        return _fail(f"the Memory adapter could not be driven: {type(exc).__name__}: "
                     f"{str(exc)[:300]}")

    items = []
    for item in outcome.items:
        mapped = _item_for(conn, item)
        if mapped is not None:
            items.append(mapped)
        if len(items) >= MAX_ITEMS:
            break
    outcomes = {entry.bank_id: entry for entry in outcome.banks}
    return answer(items, outcome.trace_id, outcome.error_code, outcomes)


if __name__ == "__main__":
    raise SystemExit(main())
