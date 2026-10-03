"""Frozen P4 vocabulary and the values that cross the adapter boundary (ADR 0011 §2).

These are the words the rest of P4, P6 and the validation runner share. They
are declared here rather than repeated per module so that "the five memory
types" and "the four job states" have exactly one definition in the codebase,
and so the engine's own vocabulary cannot leak in by accident: Hindsight speaks
`world`/`experience`/`observation`, and `memory/transport.py` is the only place
allowed to translate between the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# -- design §7: the only five things allowed to become long-term memory -------
MEMORY_TYPES: tuple[str, ...] = (
    "CONFIRMED_DECISION",
    "STABLE_PROJECT_FACT",
    "RESOLVED_ISSUE",
    "VERIFIED_OUTCOME",
    "FAILURE_LESSON",
)
MEMORY_TYPE_SET = frozenset(MEMORY_TYPES)

#: A type name is a label, never a proof. These are the provenance facts the
#: eligibility check must find in Core before a type may be used at all
#: (design §7; ADR 0011 §4). Anything less and the item is not enqueued.
REQUIRED_PROVENANCE: dict[str, tuple[str, ...]] = {
    # A Decision memory is only legitimate if a human/system ACTIVE Authority
    # Decision exists. There is no "the agent said so" path.
    "CONFIRMED_DECISION": ("authority_decision",),
    "STABLE_PROJECT_FACT": ("authority_rule",),
    "RESOLVED_ISSUE": ("failure_source",),
    "VERIFIED_OUTCOME": ("evidence_pass",),
    "FAILURE_LESSON": ("failure_source",),
}

# -- banks (design §7) ---------------------------------------------------------
GLOBAL_BANK_ID = "jasmine-global"
PROJECT_BANK_PREFIX = "jasmine-project-"

#: `jasmine-project-<project_id>`. The project id is a Core public id, so the
#: character class is closed rather than "anything but a slash": a bank id is
#: interpolated into a remote URL path.
PROJECT_BANK_RE = re.compile(r"^jasmine-project-(prj_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26})$")
GLOBAL_BANK_RE = re.compile(r"^jasmine-global$")

# -- job lifecycle (design module §12; state names are the original text) -----
JOB_STATUSES: tuple[str, ...] = ("PENDING", "RUNNING", "DONE", "FAILED")
#: One table, one queue, three kinds. `invalidate` is the engine's documented
#: soft retire (`PATCH /memories/{id}` with `state="invalidated"`: excluded from
#: recall and consolidation, kept for audit, reversible), which is exactly what
#: design §9's `obsolete` means. A second table for it would be a parallel queue
#: for the same engine.
JOB_KINDS: tuple[str, ...] = ("retain", "delete", "invalidate")

# -- memory item lifecycle (design §9) ----------------------------------------
ITEM_STATUSES: tuple[str, ...] = ("ACTIVE", "OBSOLETE", "DELETED")
ENGINE_STATES: tuple[str, ...] = (
    "PENDING_RETAIN", "RETAINED", "RETAIN_FAILED",
    "DELETE_PENDING", "DELETED", "DELETE_FAILED",
)

# -- recall outcome (ADR 0011 §5) ---------------------------------------------
#: `degraded` is separate from `empty` on purpose: a bank that failed is not a
#: bank with nothing to say, and a Dashboard that cannot tell them apart will
#: report a healthy Core with no memory.
RECALL_STATUSES: tuple[str, ...] = ("ok", "empty", "degraded", "skipped")

#: Reasons a result was withheld. Frozen so P6 can offer them as filter facets
#: and a reviewer can assert on them without parsing prose.
FILTER_REASONS: tuple[str, ...] = (
    "unverified_provenance",     # no `jasmine_memory_id`, or unknown locally
    "foreign_project",           # the item belongs to a project not in scope
    "obsolete",                  # locally obsoleted by an operator
    "deleted",                   # tombstoned; remote document delete pending or done
    "superseded_decision",       # a newer ACTIVE Decision version exists
    "retired_decision",          # the origin Rule was retired
    "source_missing",            # a referenced Raw Event no longer resolves
    "foreign_source",            # a real Raw Event, but one from another project
                                 # claimed as this item's source or origin
    "evidence_missing",          # a referenced Evidence no longer resolves
    "type_not_requested",        # outside the requested memory types
    "duplicate",                 # the same statement, already kept from this or
                                 # the other bank; provenance merged, not lost
    "directive_injection",       # the text tries to act as an instruction
    "section_marker",            # the text contains a Context Pack section marker
    "over_budget",               # did not fit the Memory partition
)

#: A HARD Rule is loaded deterministically by P3, never by top-k Recall, and a
#: recalled item may never claim otherwise. These are the marker strings of the
#: Context Pack sections; engine text containing one is neutralised before it
#: can be rendered, and an item that only *is* a section is dropped.
CONTEXT_SECTION_MARKERS: tuple[str, ...] = (
    "[JASMINE AUTHORITY]",
    "[JASMINE CURRENT STATE]",
    "[JASMINE CHECKPOINT]",
    "[JASMINE RELEVANT MEMORY - NON AUTHORITATIVE]",
    "[JASMINE EVIDENCE HINTS]",
)


@dataclass(frozen=True)
class BankRef:
    """A bank the Core is allowed to touch, and why it is allowed to."""

    bank_id: str
    scope_kind: str            # "global" | "project"
    project_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"bank_id": self.bank_id, "scope_kind": self.scope_kind,
                "project_id": self.project_id}


@dataclass(frozen=True)
class Provenance:
    """Where a Memory item came from, and what must still resolve for it to count.

    `source_event_ids` always contains at least one committed Raw Event, and
    every id in it must still resolve in `events`. That is the whole of the
    "nothing becomes a high-authority fact unless it is traceable" rule
    (design §16) applied to Memory.
    """

    source_event_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    origin_rule_id: str | None = None
    origin_rule_version: int | None = None
    origin_event_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_event_ids": list(self.source_event_ids),
            "evidence_ids": list(self.evidence_ids),
            "origin_rule_id": self.origin_rule_id,
            "origin_rule_version": self.origin_rule_version,
            "origin_event_id": self.origin_event_id,
        }


@dataclass(frozen=True)
class RetainRequest:
    """One document to hand to the engine. Built by the worker, never by a caller."""

    bank: BankRef
    document_id: str
    memory_type: str
    content: str
    provenance: Provenance
    context: str
    timestamp: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RetainReceipt:
    """Evidence that the engine accepted the document.

    `remote_memory_unit_ids` is deliberately *not* part of this: with
    `update_mode="replace"` Hindsight deletes and re-extracts a document's
    memories on every re-send, so a memory unit id is not stable across a
    crash-and-retry. The document id is the stable handle (ADR 0011 §5).
    """

    document_id: str
    bank_id: str
    items_count: int
    engine_api_version: str | None = None
    operation_id: str | None = None
    async_processed: bool = False
    usage: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "bank_id": self.bank_id,
            "items_count": self.items_count,
            "engine_api_version": self.engine_api_version,
            "operation_id": self.operation_id,
            "async": self.async_processed,
            "usage": dict(self.usage),
        }


@dataclass(frozen=True)
class EngineResult:
    """One row of an engine recall response, before any Core judgement."""

    engine_result_id: str
    text: str
    bank_id: str
    document_id: str | None = None
    engine_fact_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = field(default_factory=tuple)
    score: float | None = None


@dataclass(frozen=True)
class RecallRequest:
    """What P3 asks for. Identity comes from the Core call context, not a body.

    `max_tokens` is the Memory partition P3 allocated out of the total Context
    Pack budget (design §8: Memory 400-600 of 1500-2200). `None` means P3 gave
    Memory no budget at all, and the adapter then does not query the engine at
    all rather than querying and throwing the answer away.
    """

    project_id: str
    actor_id: str
    query: str
    task_id: str | None = None
    session_id: str | None = None
    types: tuple[str, ...] = ()
    max_tokens: int | None = None
    authority_refs: tuple[str, ...] = ()
    state_revision: int | None = None


@dataclass(frozen=True)
class RecallItem:
    """A result that survived provenance, authority and budget checks."""

    memory_result_id: str
    memory_id: str
    text: str
    bank_id: str
    scope_kind: str
    project_id: str | None
    memory_type: str
    document_id: str | None
    engine_result_id: str
    token_estimate: int
    provenance: Provenance
    historical: bool = True
    pinned: bool = False
    rank: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_result_id": self.memory_result_id,
            "memory_id": self.memory_id,
            "text": self.text,
            "bank_id": self.bank_id,
            "scope_kind": self.scope_kind,
            "project_id": self.project_id,
            "memory_type": self.memory_type,
            "document_id": self.document_id,
            "engine_result_id": self.engine_result_id,
            "token_estimate": self.token_estimate,
            "historical": self.historical,
            "pinned": self.pinned,
            "rank": self.rank,
            **self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class FilteredItem:
    """A result the Core refused to hand over, with the machine-readable reason."""

    engine_result_id: str
    bank_id: str
    reason: str
    memory_id: str | None = None
    document_id: str | None = None
    memory_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_result_id": self.engine_result_id,
            "bank_id": self.bank_id,
            "reason": self.reason,
            "memory_id": self.memory_id,
            "document_id": self.document_id,
            "memory_type": self.memory_type,
        }


@dataclass(frozen=True)
class BankOutcome:
    """Per-bank result of a recall, so `degraded` is never guessed at the top."""

    bank_id: str
    scope_kind: str
    status: str                     # "ok" | "failed" | "skipped"
    error_code: str | None = None
    safe_error_summary: str | None = None
    result_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"bank_id": self.bank_id, "scope_kind": self.scope_kind,
                "status": self.status, "error_code": self.error_code,
                "safe_error_summary": self.safe_error_summary,
                "result_count": self.result_count}


@dataclass(frozen=True)
class RecallOutcome:
    """What the Memory provider hands back to P3. Never raises for engine failure."""

    status: str                     # "ok" | "empty" | "degraded" | "skipped"
    items: tuple[RecallItem, ...] = ()
    filtered: tuple[FilteredItem, ...] = ()
    banks: tuple[BankOutcome, ...] = ()
    budget_tokens: int | None = None
    used_tokens: int = 0
    error_code: str | None = None
    safe_error_summary: str | None = None
    trace_id: str | None = None
    degraded_banks: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "items": [item.as_dict() for item in self.items],
            "filtered": [item.as_dict() for item in self.filtered],
            "banks": [bank.as_dict() for bank in self.banks],
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "error_code": self.error_code,
            "safe_error_summary": self.safe_error_summary,
            "trace_id": self.trace_id,
            "degraded_banks": list(self.degraded_banks),
        }


@dataclass(frozen=True)
class ReflectRequest:
    """An explicit request for historical synthesis.

    `explicit_user_request` is not a convenience flag. Reflect is a second LLM
    call over the whole bank and costs real money and real time; design §7 allows
    it only when the *user* asked for historical synthesis. The Core sets this
    from the authenticated operator's command, never from an ordinary Context
    build and never from an agent's phrasing of a complicated question.
    """

    project_id: str
    actor_id: str
    query: str
    task_id: str | None = None
    session_id: str | None = None
    max_tokens: int | None = None
    explicit_user_request: bool = False
    requested_by: str | None = None      # audit detail: the operator's words


@dataclass(frozen=True)
class ReflectOutcome:
    """Historical analysis. Explicitly not authoritative, and says so in the payload."""

    status: str
    text: str | None = None
    based_on_memory_ids: tuple[str, ...] = ()
    banks: tuple[BankOutcome, ...] = ()
    error_code: str | None = None
    safe_error_summary: str | None = None
    trace_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "non_authoritative": True,
            "based_on_memory_ids": list(self.based_on_memory_ids),
            "banks": [bank.as_dict() for bank in self.banks],
            "error_code": self.error_code,
            "safe_error_summary": self.safe_error_summary,
            "trace_id": self.trace_id,
        }
