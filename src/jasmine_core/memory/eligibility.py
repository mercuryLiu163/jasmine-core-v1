"""Which committed Core truth may become long-term Memory, and what it may claim.

This is the module design §5's `Memory candidate` branch and the reason P4 can
exist at all before P2 does. Design §7 is explicit that the *name* of a memory
type is not evidence:

    允许进入 Memory: CONFIRMED_DECISION, STABLE_PROJECT_FACT, RESOLVED_ISSUE,
    VERIFIED_OUTCOME, FAILURE_LESSON
    不允许作为 Memory 真源: 当前 step 状态, task revision, 未验证猜测, 临时 plan,
    Agent 自报"应该没问题"

So nothing is enqueued on the strength of a label. Every candidate is checked
against committed P1 truth, and the checks are the five in
`types.REQUIRED_PROVENANCE`:

* `CONFIRMED_DECISION` — there is an ACTIVE Authority Rule of kind `DECISION`
  whose *origin Event* is this source. An agent proposal, an unapproved
  proposal, or a plain `user.prompt` cannot produce one.
* `STABLE_PROJECT_FACT` — an ACTIVE `RULE` at project scope whose origin Event
  is this source.
* `VERIFIED_OUTCOME` — at least one `PASS` Evidence row for the same task whose
  reference is currently usable, i.e. the artifact still exists and the
  fingerprint still matches (P1-03's `reference_status`). INFO Evidence, and
  anything the Core itself has already marked `BROKEN_REFERENCE`, is refused.
* `RESOLVED_ISSUE` and `FAILURE_LESSON` — a real failure source: a `FAIL`
  Evidence row, or a committed step/task transition into `FAILED`/`STALE`.
  "The agent said it was fine" is not a failure source and neither is an
  absence of records.

## P2 boundary

P2 owns Interpretation, Resolution and review. Its approved *candidates* are a
second, wider source of eligible content, and that source is not implemented
here: `SourceRegistry` is the extension point, and P2 registers
`CandidateSource` on it. Until P2 does, the registry holds exactly the
`AuthorityEvidenceSource` below, which is derived entirely from P1 truth and is
therefore fully testable today. Nothing in this file reaches into P2's tables,
guesses at its schema, or treats an unreviewed candidate as eligible.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .. import errors
from . import filtering, scope
from .types import MEMORY_TYPES, MEMORY_TYPE_SET, Provenance

#: Events a Memory source may be. A candidate must be one of these; anything
#: else in the Event stream is never scanned, so a future Event type cannot
#: start producing memories by accident.
SOURCE_EVENT_TYPES: frozenset[str] = frozenset({
    "rule.approved",       # a confirmed Decision or a stable project fact
    "rule.superseded",     # the new version of a Decision
    "evidence.recorded",   # PASS for a verified outcome, FAIL for a lesson
    "evidence.confirmed",  # a human confirmation of a real result
    "step.transitioned",   # FAILED / STALE for an issue or a lesson
    "task.transitioned",
})

#: The label the source Event proposes. The Event's own payload says which kind
#: of Authority it activated; a `rule.approved` for a `RULE` yields a project
#: fact, and for a `DECISION` a confirmed decision.
_DECISION_KINDS = frozenset({"DECISION"})
_FACT_KINDS = frozenset({"RULE"})


@dataclass(frozen=True)
class Candidate:
    """A committed source that is eligible to become one kind of Memory."""

    source_event_id: str
    memory_type: str
    project_id: str
    task_id: str | None
    scope_kind: str
    title: str
    body: str
    occurred_at: str
    provenance: Provenance
    document_id: str
    context: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "memory_type": self.memory_type,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "scope_kind": self.scope_kind,
            "title": self.title,
            "occurred_at": self.occurred_at,
            "document_id": self.document_id,
            "context": self.context,
            **self.provenance.as_dict(),
        }


class Ineligible(Exception):
    """Why a committed Event is not a Memory source. Not an error condition.

    This is raised per Event during a scan and collected into the scan report.
    A scan over a healthy Event stream produces many of these; treating one as a
    failure would make the recovery scan useless.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@runtime_checkable
class Source(Protocol):
    """A place eligible Memory content comes from."""

    name: str

    def scan(self, conn: sqlite3.Connection, *, after_seq: int, limit: int) -> list[Candidate]:
        """Eligible candidates with `seq > after_seq`, in Event order."""


def evidence_reference_status(conn: sqlite3.Connection, evidence_id: str) -> str:
    """P1's own view of whether an Evidence artifact still demonstrates anything.

    Delegates to `EvidenceStore.get`, so the answer cannot drift from the one
    `GET /v1/evidence/{id}` and the acceptance validator give. `OK` and `NONE`
    are both acceptable: `NONE` means the result cited no artifact, and there is
    then nothing that can have decayed. `BROKEN_REFERENCE` — the artifact is
    gone, or hashes to something else — is the only refusal.
    """
    from ..db import get_meta
    from ..evidence import EvidenceStore

    store = EvidenceStore(conn, schema_version=int(get_meta(conn, "schema_version", 0) or 0))
    try:
        return store.get(evidence_id)["reference_status"]
    except (errors.NotFound, OSError, ValueError, RuntimeError):
        # `reference_status` touches the configured workspace root, which is
        # absent in a test or a read-only replica. A reference we cannot check is
        # not a reference we can call broken, and it is not one we promote to a
        # verified outcome either; callers treat an exception as ineligible.
        raise Ineligible("evidence_reference_unverifiable", evidence_id)


def document_id_for(source_event_id: str, memory_type: str) -> str:
    """The stable engine document id for one source.

    Deterministic, so a crash between "Truth committed" and "job inserted", and
    a re-scan of the same Event, both land on the same remote document instead
    of creating a second one. It is scoped by engine id so two engines pointed at
    one deployment could not collide.
    """
    from . import ENGINE_ID
    from ..canonical import sha256_hex

    return f"{ENGINE_ID}:{memory_type.lower()}:{sha256_hex(source_event_id)[:32]}"


class AuthorityEvidenceSource:
    """Eligible content derived from committed P1 Authority, Evidence and State.

    P2 registers a second source for approved candidates; this one stands alone
    so the P4 pipeline is exercisable, and so the durable-job machinery is
    tested against real committed truth rather than fixtures.
    """

    name = "p1-authority-evidence"

    def scan(self, conn: sqlite3.Connection, *, after_seq: int, limit: int,
             reasons: list[dict[str, Any]] | None = None) -> list[Candidate]:
        """Eligible candidates above `after_seq`, in Event order.

        `reasons`, when given, collects why each rejected Event was rejected.
        Most Events in a healthy stream are not memory sources, so the count of
        refusals is a health signal and the reasons are the only way to tell a
        correctly-refused candidate from a broken query.
        """
        import json

        # Parameter order must follow the SQL text: `seq > ?`, then the event
        # types, then the limit.
        rows = conn.execute(
            "SELECT event_id, seq, event_type, occurred_at, recorded_at, project_id, task_id,"
            " payload_json FROM events WHERE seq > ? AND event_type IN (%s) ORDER BY seq LIMIT ?"
            % ",".join("?" for _ in SOURCE_EVENT_TYPES),
            (after_seq, *sorted(SOURCE_EVENT_TYPES), max(1, min(limit, 500))),
        ).fetchall()
        candidates: list[Candidate] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            candidate, refusal = self._evaluate(conn, row, payload)
            if candidate is not None:
                candidates.append(candidate)
            elif reasons is not None:
                reasons.append({"event_id": row["event_id"], "event_type": row["event_type"],
                                "reason": refusal[0] if refusal else "unknown",
                                "detail": refusal[1] if refusal else ""})
        return candidates

    # -- per Event ---------------------------------------------------------

    def _evaluate(self, conn: sqlite3.Connection, row: sqlite3.Row,
                  payload: dict[str, Any]) -> tuple[Candidate | None, tuple[str, str] | None]:
        """Return ``(candidate, None)`` or ``(None, (reason, detail))``.

        Not an exception: a scan over a healthy Event stream rejects most Events
        by design, and raising would make the recovery scan useless.
        """
        try:
            handler = {
                "rule.approved": self._from_rule_approved,
                "rule.superseded": self._from_rule_superseded,
                "evidence.recorded": self._from_evidence_recorded,
                "evidence.confirmed": self._from_evidence_recorded,
                "step.transitioned": self._from_step_transitioned,
                "task.transitioned": self._from_task_transitioned,
            }[row["event_type"]]
        except KeyError:  # pragma: no cover - guarded by the IN clause
            return None, ("unknown_event_type", row["event_type"])
        try:
            return handler(conn, row, payload), None
        except Ineligible as refusal:
            return None, (refusal.reason, refusal.detail)

    def _from_rule_approved(self, conn: sqlite3.Connection, row: sqlite3.Row,
                            payload: dict[str, Any]) -> Candidate:
        rule_id = payload.get("rule_id")
        if not isinstance(rule_id, str):
            raise Ineligible("no_rule", "the approve Event names no rule")
        rule = conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
        if rule is None:
            raise Ineligible("rule_missing", rule_id)
        if rule["status"] != "ACTIVE":
            # A rule that was approved and then retired before the scan reached
            # it is not "current"; the lifecycle is P1's answer, not P4's.
            raise Ineligible("rule_not_active", f"{rule_id} is {rule['status']}")
        version = conn.execute(
            "SELECT * FROM rule_versions WHERE rule_id=? AND version=?",
            (rule_id, rule["current_version"]),
        ).fetchone()
        if version is None:
            raise Ineligible("rule_version_missing", f"{rule_id}:{rule['current_version']}")
        if not version["origin_event_id"] or conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (version["origin_event_id"],)
        ).fetchone() is None:
            # The Rule's own origin Event must resolve, or the memory would point
            # at a conversation that is not in this database. The memory's
            # *source* is this confirmation Event; the origin travels as
            # `origin_event_id` so an Inspector can walk back to the words that
            # produced the decision.
            raise Ineligible("origin_missing", version["origin_event_id"] or rule_id)
        if version["kind"] in _DECISION_KINDS:
            memory_type = "CONFIRMED_DECISION"
        elif version["kind"] in _FACT_KINDS and rule["scope_kind"] == "project":
            memory_type = "STABLE_PROJECT_FACT"
        else:
            raise Ineligible("kind_not_memory_eligible", version["kind"])
        if rule["scope_kind"] not in ("global", "project"):
            # A task-scoped Rule is a constraint on one task, not a stable fact
            # about the project. Design §7 forbids current step state as truth.
            raise Ineligible("scope_not_memory_eligible", rule["scope_kind"])
        return self._candidate(
            conn, row,
            memory_type=memory_type,
            scope_kind=rule["scope_kind"],
            title=f"{version['kind']} {payload.get('rule_key', rule['rule_key'])}",
            body=version["content"],
            origin_rule_id=rule_id,
            origin_rule_version=int(rule["current_version"]),
            origin_event_id=version["origin_event_id"],
            context=f"jasmine {memory_type.lower().replace('_', ' ')}",
        )

    def _from_rule_superseded(self, conn: sqlite3.Connection, row: sqlite3.Row,
                              payload: dict[str, Any]) -> Candidate:
        rule_id = payload.get("rule_id")
        if not isinstance(rule_id, str):
            raise Ineligible("no_rule", "the supersede Event names no rule")
        rule = conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
        if rule is None or rule["status"] != "ACTIVE":
            raise Ineligible("rule_not_active", rule_id)
        version = conn.execute(
            "SELECT * FROM rule_versions WHERE rule_id=? AND version=?", (rule_id, rule["current_version"])
        ).fetchone()
        if version is None:
            raise Ineligible("rule_version_missing", rule_id)
        if version["kind"] not in _DECISION_KINDS:
            raise Ineligible("kind_not_memory_eligible", version["kind"])
        return self._candidate(
            conn, row,
            memory_type="CONFIRMED_DECISION",
            scope_kind=rule["scope_kind"],
            title=f"DECISION {payload.get('rule_key', rule['rule_key'])}",
            body=version["content"],
            origin_rule_id=rule_id,
            origin_rule_version=int(rule["current_version"]),
            origin_event_id=version["origin_event_id"],
            context="jasmine confirmed decision",
        )

    def _from_evidence_recorded(self, conn: sqlite3.Connection, row: sqlite3.Row,
                                payload: dict[str, Any]) -> Candidate:
        evidence_id = payload.get("evidence_id")
        if not isinstance(evidence_id, str):
            raise Ineligible("no_evidence", "the Event names no evidence")
        record = conn.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        if record is None:
            raise Ineligible("evidence_missing", evidence_id)
        if evidence_reference_status(conn, evidence_id) == "BROKEN_REFERENCE":
            # The artifact this result cited is gone or no longer hashes to what
            # was recorded, so the result no longer demonstrates anything.
            # Recording it as a verified outcome would be exactly the
            # "self-reported PASS" design §7 forbids.
            raise Ineligible("evidence_broken_reference", evidence_id)
        if record["status"] == "PASS":
            memory_type = "VERIFIED_OUTCOME"
        elif record["status"] == "FAIL":
            memory_type = "FAILURE_LESSON"
        else:
            raise Ineligible("evidence_status_not_memory_eligible", record["status"])
        detail = payload.get("summary") or payload.get("tool_name") or "recorded result"
        return self._candidate(
            conn, row,
            memory_type=memory_type,
            scope_kind="project",
            title=f"{record['status']} {record['kind']} {detail}",
            body=self._evidence_body(record, payload, detail),
            evidence_ids=(evidence_id,),
            context=f"jasmine {memory_type.lower().replace('_', ' ')}",
        )

    def _from_step_transitioned(self, conn: sqlite3.Connection, row: sqlite3.Row,
                                payload: dict[str, Any]) -> Candidate | None:
        # P1's transition payload names the target in `to`; reading `status` here
        # would silently match nothing and look like "no failures were ever
        # detected", which is the worst possible direction for this check.
        target = payload.get("to")
        if target not in ("FAILED", "STALE"):
            return None
        step_id = payload.get("step_id")
        return self._failure_candidate(
            conn, row,
            memory_type="FAILURE_LESSON" if target == "FAILED" else "RESOLVED_ISSUE",
            title=f"step {step_id or ''} {target}".strip(),
            body=payload.get("reason") or f"the step reached {target}",
        )

    def _from_task_transitioned(self, conn: sqlite3.Connection, row: sqlite3.Row,
                                payload: dict[str, Any]) -> Candidate | None:
        if payload.get("to") != "FAILED":
            return None
        return self._failure_candidate(
            conn, row, memory_type="FAILURE_LESSON",
            title=f"task {row['task_id']} FAILED",
            body=payload.get("reason") or "the task reached FAILED",
        )

    # -- assembly ----------------------------------------------------------

    def _failure_candidate(self, conn: sqlite3.Connection, row: sqlite3.Row, *,
                           memory_type: str, title: str, body: str) -> Candidate:
        if row["project_id"] is None or row["task_id"] is None:
            raise Ineligible("no_project_or_task", row["event_id"])
        return self._candidate(conn, row, memory_type=memory_type, scope_kind="project",
                               title=title, body=body,
                               context=f"jasmine {memory_type.lower().replace('_', ' ')}")

    def _evidence_body(self, record: sqlite3.Row, payload: dict[str, Any], detail: str) -> str:
        parts = [
            f"{record['kind']} {record['status']} evidence for task {record['task_id']}",
            f"command: {record['command_sha256']}" if record["command_sha256"] else None,
            f"tool: {record['tool_name']}" if record["tool_name"] else None,
            f"workspace fingerprint: {record['fingerprint_sha256']}",
            f"detail: {detail}",
        ]
        return "\n".join(part for part in parts if part)

    def _candidate(self, conn: sqlite3.Connection, row: sqlite3.Row, *, memory_type: str,
                   scope_kind: str, title: str, body: str, context: str,
                   evidence_ids: tuple[str, ...] = (),
                   origin_rule_id: str | None = None, origin_rule_version: int | None = None,
                   origin_event_id: str | None = None) -> Candidate:
        if memory_type not in MEMORY_TYPE_SET:
            raise Ineligible("unknown_memory_type", memory_type)
        project_id = row["project_id"]
        if scope_kind == "project" and project_id is None:
            raise Ineligible("no_project", row["event_id"])
        if scope_kind == "global" and project_id is not None:
            # Elevating a project-scoped source into the global bank is the exact
            # leak design §7 rules out. P4 never does it implicitly; a genuinely
            # global source must arrive with global scope from its own Event.
            raise Ineligible("global_source_has_project", row["event_id"])
        if project_id is not None and conn.execute(
            "SELECT 1 FROM tasks WHERE task_id=?", (row["task_id"],)
        ).fetchone() is None and row["task_id"] is not None:
            raise Ineligible("task_missing", row["task_id"] or "")
        sanitized = filtering.sanitize(body)
        if sanitized.rejected:
            # A lesson that is mostly a credential is not retained. The source
            # Event keeps the original text under `events:read`.
            raise Ineligible("secret_content_rejected", ",".join(f.rule for f in sanitized.findings))
        return Candidate(
            source_event_id=row["event_id"],
            memory_type=memory_type,
            project_id=project_id or "",
            task_id=row["task_id"],
            scope_kind=scope_kind,
            title=title[:200],
            body=sanitized.text,
            occurred_at=row["occurred_at"],
            provenance=Provenance(
                source_event_ids=(row["event_id"],),
                evidence_ids=evidence_ids,
                origin_rule_id=origin_rule_id,
                origin_rule_version=origin_rule_version,
                origin_event_id=origin_event_id or row["event_id"],
            ),
            document_id=document_id_for(row["event_id"], memory_type),
            context=context,
        )


class SourceRegistry:
    """The ordered set of eligible-content sources.

    P2 adds a candidate source by registering it here; nothing else in P4
    changes. The registry is ordered and the sources are independent, so a source
    that raises is skipped and reported rather than aborting the scan: one
    broken source must not stop the others from producing work.
    """

    def __init__(self, sources: list[Source] | None = None) -> None:
        self._sources: list[Source] = list(sources or [AuthorityEvidenceSource()])

    def register(self, source: Source) -> None:
        self._sources.append(source)

    @property
    def names(self) -> list[str]:
        return [source.name for source in self._sources]

    def scan(self, conn: sqlite3.Connection, *, after_seq: int, limit: int,
             reasons: list[dict[str, Any]] | None = None) -> list[Candidate]:
        candidates: list[Candidate] = []
        for source in self._sources:
            try:
                candidates.extend(source.scan(conn, after_seq=after_seq, limit=limit,
                                              reasons=reasons))
            except Exception as exc:  # noqa: BLE001 - one source must not stop the others
                # A source that raises is reported, not hidden: a P2 candidate
                # source that is broken should show up in the scan report instead
                # of silently contributing nothing.
                if reasons is not None:
                    reasons.append({"source": source.name, "reason": "source_failed",
                                    "detail": type(exc).__name__})
                continue
        # Deterministic order by Event, then document id, so two scans over the
        # same window produce the same sequence and the cursor is meaningful.
        candidates.sort(key=lambda c: (c.source_event_id, c.document_id))
        return candidates[:limit]


def resolve_destination(candidate: Candidate) -> Any:
    """The one bank this candidate may be written to.

    Deliberately not a parameter. A caller cannot choose a bank; the candidate's
    own committed scope decides, and `scope.resolve_scope_for_item` is the only
    thing that constructs the result.
    """
    if candidate.scope_kind == "global":
        return scope.global_bank()
    return scope.project_bank(candidate.project_id)


def validate_types(types: tuple[str, ...]) -> tuple[str, ...]:
    """Reject an unknown type instead of silently returning nothing for it."""
    unknown = sorted(set(types) - MEMORY_TYPE_SET)
    if unknown:
        raise errors.InvalidRequest(
            f"unknown memory types: {', '.join(unknown)}",
            field="types", unknown_types=unknown, allowed=list(MEMORY_TYPES),
        )
    return types or MEMORY_TYPES
