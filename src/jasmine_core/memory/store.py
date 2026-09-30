"""The local Memory projection: items, traces, engine status, project grants.

Every write here assumes the caller already holds the write transaction opened
by `db.transaction()`, for the same reason `events.EventStore` does (ADR 0002):
the item and the receipt that describes it, or the trace and every result row
inside it, are one fact and must be visible together or not at all.

The lifecycle rules that are not expressible as a CHECK live in
`migrations/m0008_memory.py` as triggers: no `DELETE` on an item, no `UPDATE` of
an identity column, and append-only traces. This module therefore never issues a
`DELETE` and never updates `document_id`, `content_sha256` or `source_event_id`.

`items(projects=...)` is the *only* filter a caller may pass and it is not
optional in the acl lifecycle mode. A grant is not a preference: an actor that
can see one project must not be able to omit the filter and read the others, so
`scope.visible_projects` is consulted first and its tuple is what reaches the
`IN (...)` here. Under the `single_user` lifecycle mode the filter is omitted and
every project is visible by design.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .. import clock, errors, ids
from ..canonical import canonical_json, sha256_hex
from .types import (
    ENGINE_STATES,
    GLOBAL_BANK_ID,
    ITEM_STATUSES,
    RECALL_STATUSES,
    BankOutcome,
    BankRef,
    FilteredItem,
    Provenance,
    RecallItem,
    RecallOutcome,
    ReflectOutcome,
)

MEMORY_ID_PREFIX = "mem"
TRACE_ID_PREFIX = "mtr"
RESULT_ID_PREFIX = "mqi"
JOB_ID_PREFIX = "mjb"
ATTEMPT_ID_PREFIX = "mja"

_ITEM_COLUMNS = (
    "memory_id", "bank_id", "scope_kind", "project_id", "task_id", "memory_type",
    "document_id", "source_event_id", "evidence_ids_json", "origin_rule_id",
    "origin_rule_version", "content_sha256", "sanitized_text", "status", "pinned",
    "revision", "engine_state", "receipt_json", "last_error_code", "safe_error_summary",
    "remote_deleted_at", "lifecycle_reason", "last_recalled_at", "recall_count",
    "created_at", "updated_at",
)


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    item = {key: row[key] for key in _ITEM_COLUMNS}
    item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
    item["pinned"] = bool(item["pinned"])
    receipt = item.get("receipt_json")
    item["receipt"] = json.loads(receipt) if receipt else None
    item.pop("receipt_json", None)
    return item


class MemoryStore:
    """Reads and writes for the P4 tables. Never opens its own transaction."""

    def __init__(self, conn: sqlite3.Connection, *, schema_version: int) -> None:
        self._conn = conn
        self._schema_version = schema_version

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # -- items: reading ----------------------------------------------------

    def item(self, memory_id: str) -> dict[str, Any] | None:
        if not ids.is_id(memory_id, MEMORY_ID_PREFIX):
            return None
        row = self._conn.execute(
            "SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return None if row is None else _row_to_item(row)

    def item_by_document(self, document_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memory_items WHERE document_id=?", (document_id,)
        ).fetchone()
        return None if row is None else _row_to_item(row)

    def items(self, *, project_id: str | None = None, projects: tuple[str, ...] | None = None,
              status: str | None = None, memory_type: str | None = None,
              bank_id: str | None = None, task_id: str | None = None, limit: int = 100,
              after_memory_id: str | None = None) -> list[dict[str, Any]]:
        """Keyset pagination on `memory_id`.

        Offset pagination would let a row inserted during a Dashboard scroll
        shift the window, which for an Inspector reading a mutating lifecycle is
        how a duplicate or a skip appears. `after_memory_id` is the last id the
        caller saw.
        """
        if status is not None and status not in ITEM_STATUSES:
            raise errors.InvalidRequest(f"status must be one of {', '.join(ITEM_STATUSES)}",
                                        field="status", value=status)
        clauses: list[str] = []
        params: list[Any] = []
        _scope_clause(clauses, params, project_id=project_id, projects=projects)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if bank_id is not None:
            clauses.append("bank_id = ?")
            params.append(bank_id)
        if after_memory_id is not None:
            clauses.append("memory_id > ?")
            params.append(after_memory_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self._conn.execute(
            f"SELECT * FROM memory_items{where} ORDER BY memory_id LIMIT ?", params
        ).fetchall()
        return [_row_to_item(row) for row in rows]

    def sources_of(self, memory_id: str) -> list[dict[str, Any]]:
        return [
            {"source_event_id": row["source_event_id"], "role": row["role"],
             "created_at": row["created_at"]}
            for row in self._conn.execute(
                "SELECT source_event_id, role, created_at FROM memory_item_sources"
                " WHERE memory_id=? ORDER BY role, source_event_id", (memory_id,)
            )
        ]

    def count_items(self, *, project_id: str | None = None,
                    status: str | None = None) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("(project_id = ? OR bank_id = ?)")
            params.extend([project_id, GLOBAL_BANK_ID])
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM memory_items{where}", params
        ).fetchone()
        return int(row["n"])

    # -- items: writing ----------------------------------------------------

    def insert_item(self, *, bank: BankRef, memory_type: str, document_id: str,
                    source_event_id: str, provenance: Provenance, sanitized_text: str,
                    content_sha256: str, task_id: str | None, at: str) -> str:
        """Create the item and its source rows. Caller holds the transaction.

        `bank` is the *only* admissible destination: it was produced by
        `scope.resolve_scope_for_item` from the source's own scope, so there is
        no path here by which a project-scoped source reaches the global bank.
        """
        memory_id = ids.new_id(MEMORY_ID_PREFIX, now_ms=_now_ms(at))
        self._conn.execute(
            "INSERT INTO memory_items (memory_id, bank_id, scope_kind, project_id, task_id,"
            " memory_type, document_id, source_event_id, evidence_ids_json, origin_rule_id,"
            " origin_rule_version, content_sha256, sanitized_text, status, pinned, revision,"
            " engine_state, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',0,1,'PENDING_RETAIN',?,?)",
            (memory_id, bank.bank_id, bank.scope_kind, bank.project_id, task_id,
             memory_type, document_id, source_event_id,
             canonical_json(list(provenance.evidence_ids)),
             provenance.origin_rule_id, provenance.origin_rule_version,
             content_sha256, sanitized_text, at, at),
        )
        self._conn.execute(
            "INSERT INTO memory_item_sources (memory_id, source_event_id, role, created_at)"
            " VALUES (?,?,'PRIMARY',?)",
            (memory_id, source_event_id, at),
        )
        for extra in provenance.source_event_ids[1:]:
            self.add_source(memory_id, extra, role="SUPPORTING", at=at)
        return memory_id

    def add_source(self, memory_id: str, source_event_id: str, *, role: str, at: str) -> None:
        """Attach a supporting source. Ignores a duplicate rather than failing.

        A duplicate means the same Event was already recorded for this item,
        which is not an error worth rolling a transaction back for.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO memory_item_sources (memory_id, source_event_id, role, created_at)"
            " VALUES (?,?,?,?)",
            (memory_id, source_event_id, role, at),
        )

    def set_engine_state(self, memory_id: str, *, engine_state: str, expected: Iterable[str],
                         receipt: dict[str, Any] | None = None,
                         error_code: str | None = None,
                         safe_error_summary: str | None = None,
                         remote_deleted: bool = False, at: str | None = None) -> None:
        """Advance the engine-facing state, refusing a transition that is not allowed.

        The caller states what it believes the current state is (`expected`), so
        two workers racing on the same item cannot interleave a "retained" and a
        "retain failed" into a row that claims both.
        """
        if engine_state not in ENGINE_STATES:
            raise errors.InvalidRequest(f"engine_state must be one of {', '.join(ENGINE_STATES)}",
                                        field="engine_state", value=engine_state)
        allowed = tuple(expected)
        marks = ", ".join("?" for _ in allowed)
        params: list[Any] = [engine_state]
        if receipt is not None:
            params.append(canonical_json(receipt))
        params.extend([error_code, safe_error_summary])
        if remote_deleted:
            params.append(at or clock.now_rfc3339())
        params.append(at or clock.now_rfc3339())
        params.append(memory_id)
        params.extend(allowed)
        cursor = self._conn.execute(
            "UPDATE memory_items SET engine_state=?,"
            + (" receipt_json=?," if receipt is not None else "")
            + " last_error_code=?, safe_error_summary=?"
            + (", remote_deleted_at=?" if remote_deleted else "")
            + ", updated_at=?"
            + f" WHERE memory_id=? AND engine_state IN ({marks})",
            params,
        )
        if cursor.rowcount != 1:
            current = self._conn.execute(
                "SELECT engine_state FROM memory_items WHERE memory_id=?", (memory_id,)
            ).fetchone()
            raise errors.InvalidStateTransition(
                "the memory item is not in an expected engine state",
                memory_id=memory_id,
                expected=list(allowed),
                current=current["engine_state"] if current else None,
            )

    def note_retained(self, memory_id: str, receipt: dict[str, Any]) -> None:
        self.set_engine_state(memory_id, engine_state="RETAINED",
                              expected=("PENDING_RETAIN", "RETAIN_FAILED", "DELETE_PENDING"),
                              receipt=receipt, at=clock.now_rfc3339())

    def mark_recalled(self, memory_ids: Iterable[str], at: str) -> None:
        """Bump the Inspector's "last recalled" without touching revision.

        `last_recalled_at` and `recall_count` are display metadata. They are
        deliberately not part of `revision`, because they change on every query
        and an operator's `expected_revision` must not be invalidated by an Agent
        asking a question.
        """
        ids_list = [m for m in memory_ids if ids.is_id(m, MEMORY_ID_PREFIX)]
        if not ids_list:
            return
        marks = ", ".join("?" for _ in ids_list)
        self._conn.execute(
            f"UPDATE memory_items SET last_recalled_at=?, recall_count=recall_count+1"
            f" WHERE memory_id IN ({marks})",
            [at, *ids_list],
        )

    def health_counts(self) -> dict[str, int]:
        counts = {
            "items_active": self.count_items(status="ACTIVE"),
            "items_obsolete": self.count_items(status="OBSOLETE"),
            "items_deleted": self.count_items(status="DELETED"),
        }
        for state in ("RETAIN_FAILED", "DELETE_PENDING", "DELETE_FAILED"):
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memory_items WHERE engine_state=?", (state,)
            ).fetchone()
            counts[state.lower()] = int(row["n"])
        return counts

    # -- engine status -----------------------------------------------------

    def record_engine_status(self, *, origin_host: str, configured: bool,
                             api_version: str | None, features: dict[str, Any] | None,
                             reachable: bool, error_code: str | None,
                             safe_error_summary: str | None, at: str) -> None:
        """Upsert the single engine row. No credential field exists in the table.

        `reachable` is a claim about the *last probe*, and `last_success_at` is
        only advanced by a successful retain or recall, not by a probe that
        merely got a 200 from `/version`. Conflating the two is how a Core ends up
        reporting Memory healthy while every job is failing.
        """
        from . import ENGINE_ID

        self._conn.execute(
            "INSERT INTO memory_engine_status (engine_id, origin_host, configured, api_version,"
            " features_json, last_check_ok, last_attempt_at, last_error_code, safe_error_summary,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(engine_id) DO UPDATE SET origin_host=excluded.origin_host,"
            " configured=excluded.configured, api_version=excluded.api_version,"
            " features_json=excluded.features_json, last_check_ok=excluded.last_check_ok,"
            " last_attempt_at=excluded.last_attempt_at, last_error_code=excluded.last_error_code,"
            " safe_error_summary=excluded.safe_error_summary, updated_at=excluded.updated_at",
            (ENGINE_ID, origin_host, 1 if configured else 0, api_version,
             canonical_json(features) if features else None,
             1 if reachable else 0, at, error_code, safe_error_summary, at),
        )

    def note_engine_success(self, at: str) -> None:
        from . import ENGINE_ID

        self._conn.execute(
            "UPDATE memory_engine_status SET last_success_at=?, last_error_code=NULL,"
            " safe_error_summary=NULL, updated_at=? WHERE engine_id=?",
            (at, at, ENGINE_ID),
        )

    def note_engine_failure(self, *, error_code: str, safe_error_summary: str,
                            at: str) -> None:
        from . import ENGINE_ID

        self._conn.execute(
            "UPDATE memory_engine_status SET last_error_code=?, safe_error_summary=?,"
            " updated_at=? WHERE engine_id=?",
            (error_code, safe_error_summary, at, ENGINE_ID),
        )

    def engine_status(self) -> dict[str, Any] | None:
        from . import ENGINE_ID

        row = self._conn.execute(
            "SELECT * FROM memory_engine_status WHERE engine_id=?", (ENGINE_ID,)
        ).fetchone()
        if row is None:
            return None
        return {
            "engine_id": row["engine_id"],
            "origin_host": row["origin_host"],
            "configured": bool(row["configured"]),
            "api_version": row["api_version"],
            "features": json.loads(row["features_json"]) if row["features_json"] else {},
            "last_check_ok": None if row["last_check_ok"] is None else bool(row["last_check_ok"]),
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "last_error_code": row["last_error_code"],
            "safe_error_summary": row["safe_error_summary"],
            "updated_at": row["updated_at"],
        }

    # -- traces ------------------------------------------------------------

    _TRACE_COLUMNS = (
        "trace_id", "mode", "status", "project_id", "task_id", "session_id", "actor_id",
        "query_sha256", "query_length", "requested_types_json", "banks_json",
        "filtered_json", "budget_tokens", "used_tokens", "authority_refs_json",
        "state_revision", "error_code", "safe_error_summary", "created_at",
    )

    def _insert_trace(self, values: dict[str, Any]) -> None:
        """One named-column insert for both trace kinds.

        Written this way on purpose: the recall and reflect rows have the same
        shape with different defaults, and a positional `VALUES` list is where a
        column silently ends up holding the next column's value.
        """
        columns = ", ".join(self._TRACE_COLUMNS)
        marks = ", ".join("?" for _ in self._TRACE_COLUMNS)
        self._conn.execute(
            f"INSERT INTO memory_query_traces ({columns}) VALUES ({marks})",
            [values[column] for column in self._TRACE_COLUMNS],
        )

    def record_recall(self, outcome: RecallOutcome, *, actor_id: str, project_id: str,
                      task_id: str | None, session_id: str | None, query: str,
                      types: tuple[str, ...], authority_refs: tuple[str, ...],
                      state_revision: int | None, at: str) -> str:
        """Persist a Recall trace and every result row in one transaction.

        The query text itself is *not* stored. A Context Pack query is derived
        from a user's turn, and duplicating raw conversation into a second table
        would create a copy that no retention policy covers. `query_sha256` plus
        `query_length` make the trace joinable to the P7 Validation Recorder,
        which already holds the raw turn under its own permissions.
        """
        if outcome.status not in RECALL_STATUSES:
            raise errors.InvalidRequest("trace status is not a known recall status",
                                        field="status", value=outcome.status)
        trace_id = outcome.trace_id or ids.new_id(TRACE_ID_PREFIX, now_ms=_now_ms(at))
        self._insert_trace({
            "trace_id": trace_id, "mode": "recall", "status": outcome.status,
            "project_id": project_id, "task_id": task_id, "session_id": session_id,
            "actor_id": actor_id, "query_sha256": sha256_hex(query), "query_length": len(query),
            "requested_types_json": canonical_json(list(types)),
            "banks_json": canonical_json([bank.as_dict() for bank in outcome.banks]),
            "filtered_json": canonical_json([item.as_dict() for item in outcome.filtered]),
            "budget_tokens": outcome.budget_tokens, "used_tokens": outcome.used_tokens,
            "authority_refs_json": canonical_json(list(authority_refs)),
            "state_revision": state_revision, "error_code": outcome.error_code,
            "safe_error_summary": outcome.safe_error_summary, "created_at": at,
        })
        self._write_trace_items(trace_id, outcome.items, outcome.filtered, at=at)
        return trace_id

    def record_reflect(self, outcome: ReflectOutcome, *, actor_id: str, project_id: str,
                       task_id: str | None, session_id: str | None, query: str,
                       at: str) -> str:
        """A Reflect trace. `mode` distinguishes it from a Recall for the Inspector."""
        trace_id = outcome.trace_id or ids.new_id(TRACE_ID_PREFIX, now_ms=_now_ms(at))
        self._insert_trace({
            "trace_id": trace_id, "mode": "reflect", "status": outcome.status,
            "project_id": project_id, "task_id": task_id, "session_id": session_id,
            "actor_id": actor_id, "query_sha256": sha256_hex(query), "query_length": len(query),
            "requested_types_json": "[]",
            "banks_json": canonical_json([bank.as_dict() for bank in outcome.banks]),
            "filtered_json": "[]", "budget_tokens": None, "used_tokens": 0,
            "authority_refs_json": "[]", "state_revision": None,
            "error_code": outcome.error_code, "safe_error_summary": outcome.safe_error_summary,
            "created_at": at,
        })
        return trace_id

    def _write_trace_items(self, trace_id: str, items: tuple[RecallItem, ...],
                           filtered: tuple[FilteredItem, ...], *, at: str) -> None:
        for item in items:
            self._conn.execute(
                "INSERT INTO memory_query_trace_items (result_id, trace_id, memory_id, bank_id,"
                " engine_result_id, document_id, memory_type, scope_kind, project_id,"
                " source_event_ids_json, evidence_ids_json, origin_rule_id, origin_rule_version,"
                " rank, token_estimate, text_sha256, included, historical, obsolete,"
                " filtered_reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,0,NULL,?)",
                (item.memory_result_id, trace_id, item.memory_id, item.bank_id,
                 item.engine_result_id, item.document_id, item.memory_type, item.scope_kind,
                 item.project_id, canonical_json(list(item.provenance.source_event_ids)),
                 canonical_json(list(item.provenance.evidence_ids)),
                 item.provenance.origin_rule_id, item.provenance.origin_rule_version,
                 item.rank, item.token_estimate, sha256_hex(item.text),
                 1 if item.historical else 0, at),
            )
        for row in filtered:
            self._conn.execute(
                "INSERT INTO memory_query_trace_items (result_id, trace_id, memory_id, bank_id,"
                " engine_result_id, document_id, memory_type, scope_kind, project_id,"
                " source_event_ids_json, evidence_ids_json, origin_rule_id, origin_rule_version,"
                " rank, token_estimate, text_sha256, included, historical, obsolete,"
                " filtered_reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,NULL,'[]','[]',NULL,NULL,0,0,?,0,0,0,?,?)",
                (ids.new_id(RESULT_ID_PREFIX, now_ms=_now_ms(at)), trace_id, row.memory_id,
                 row.bank_id, row.engine_result_id, row.document_id, row.memory_type,
                 sha256_hex(""), row.reason, at),
            )

    def trace(self, trace_id: str) -> dict[str, Any] | None:
        if not ids.is_id(trace_id, TRACE_ID_PREFIX):
            return None
        row = self._conn.execute(
            "SELECT * FROM memory_query_traces WHERE trace_id=?", (trace_id,)
        ).fetchone()
        if row is None:
            return None
        trace = dict(row)
        for column in ("requested_types_json", "banks_json", "filtered_json",
                       "authority_refs_json"):
            trace[column.removesuffix("_json")] = json.loads(trace.pop(column))
        trace["items"] = [
            dict(item, source_event_ids=json.loads(item["source_event_ids_json"]),
                 evidence_ids=json.loads(item["evidence_ids_json"]))
            for item in self._conn.execute(
                "SELECT * FROM memory_query_trace_items WHERE trace_id=? ORDER BY rank, result_id",
                (trace_id,),
            )
        ]
        for item in trace["items"]:
            item.pop("source_event_ids_json", None)
            item.pop("evidence_ids_json", None)
        return trace

    def traces(self, *, project_id: str | None = None, projects: tuple[str, ...] | None = None,
               task_id: str | None = None, limit: int = 50,
               after_trace_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        # A trace belongs to exactly one project and has no bank column, so the
        # global-bank arm of the shared scope clause does not apply to it.
        _scope_clause(clauses, params, project_id=project_id, projects=projects,
                      include_global_bank=False)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if after_trace_id is not None:
            clauses.append("trace_id > ?")
            params.append(after_trace_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        rows = self._conn.execute(
            f"SELECT trace_id, mode, status, project_id, task_id, session_id, actor_id,"
            f" query_sha256, query_length, requested_types_json, banks_json, budget_tokens,"
            f" used_tokens, error_code, safe_error_summary, created_at"
            f" FROM memory_query_traces{where} ORDER BY trace_id LIMIT ?",
            params,
        ).fetchall()
        return [dict(row, requested_types=json.loads(row["requested_types_json"]),
                     banks=json.loads(row["banks_json"])) for row in rows]


def _scope_clause(clauses: list[str], params: list[Any], *, project_id: str | None,
                  projects: tuple[str, ...] | None,
                  include_global_bank: bool = True) -> None:
    """Restrict a list query to the projects the caller may read.

    A named project is an exact match, plus the shared global bank where the
    table has a `bank_id` column at all. A *set* of projects — what `acl` mode
    resolves an omitted filter to — is an `IN` over the same shape, so the global
    bank stays visible in both and no project's own rows appear in another's list.
    `projects=None` means "no restriction", which only `single_user` mode ever
    produces.
    """
    if project_id is not None:
        if include_global_bank:
            clauses.append("(project_id = ? OR bank_id = ?)")
            params.extend([project_id, GLOBAL_BANK_ID])
        else:
            clauses.append("project_id = ?")
            params.append(project_id)
        return
    if projects is None:
        return
    if not projects:
        clauses.append("0 = 1")
        return
    # Parameter order must follow the clause text: the `IN` list comes first,
    # and the global-bank comparison last.
    marks = ", ".join("?" for _ in projects)
    if include_global_bank:
        clauses.append(f"(project_id IN ({marks}) OR bank_id = ?)")
        params.extend(list(projects))
        params.append(GLOBAL_BANK_ID)
    else:
        clauses.append(f"project_id IN ({marks})")
        params.extend(list(projects))


def _now_ms(at: str | None) -> int | None:
    """Reuse the row's timestamp for the id, so ids sort with the rows."""
    if not at:
        return None
    try:
        parsed = clock.parse_rfc3339(at)
    except ValueError:  # pragma: no cover - defensive
        return None
    return int(parsed.timestamp() * 1000)


__all__ = [
    "MemoryStore", "ATTEMPT_ID_PREFIX", "JOB_ID_PREFIX", "MEMORY_ID_PREFIX",
    "RESULT_ID_PREFIX", "TRACE_ID_PREFIX", "BankOutcome",
]
