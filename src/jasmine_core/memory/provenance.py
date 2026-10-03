"""Provenance: what the engine is allowed to claim, and what Core re-checks.

Recall returns text the Core did not write. Some of it is legitimate — a fact
Hindsight extracted from a document Core retained. Some of it is not: a
hand-written memory, a stale row, a document from another bank's tenant, or a
response body from something that is not Hindsight at all. The design's rule is
that nothing without traceable provenance may enter a Context Pack
(detail design §7, §16), so *every* recalled row passes through
`verify` before it is counted, ranked or rendered.

The mechanism is deliberately not "trust the engine's tags". Core writes a
`jasmine_memory_id` into the engine `metadata` on retain — engine metadata is
returned on every recalled result per `RecallResult.metadata` — and then:

1. the id must name a local `memory_items` row;
2. that row's `document_id` must equal the document the result claims;
3. the row's scope must be one of the banks this call is allowed to read;
4. the row's Raw Event sources must still resolve in `events`;
5. the row's Evidence must still resolve in `evidence`;
6. the row must be `ACTIVE`.

Anything that fails is returned as a `FilteredItem` with a frozen reason, so a
Gate can assert on the reason rather than on the absence of a row. A result
with no `jasmine_memory_id` at all — an engine row Core did not write, or one
written by a different deployment of Core — is `unverified_provenance` and is
never rendered. This is the "远端伪 source" case in the taskbook's negative
list, and it is refused by construction rather than by pattern matching on the
text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .store import MemoryStore
from .types import BankRef, EngineResult, FilteredItem, Provenance

#: The engine metadata key holding the Core Memory item id. Namespaced so it
#: cannot collide with a user tag or a future engine field.
MEMORY_ID_KEY = "jasmine_memory_id"
DOCUMENT_ID_KEY = "jasmine_document_id"
MEMORY_TYPE_KEY = "jasmine_memory_type"
SOURCE_EVENT_IDS_KEY = "jasmine_source_event_ids"
EVIDENCE_IDS_KEY = "jasmine_evidence_ids"
ORIGIN_RULE_ID_KEY = "jasmine_origin_rule_id"
ORIGIN_RULE_VERSION_KEY = "jasmine_origin_rule_version"
ORIGIN_EVENT_ID_KEY = "jasmine_origin_event_id"
CONTENT_SHA256_KEY = "jasmine_content_sha256"
RETAINED_AT_KEY = "jasmine_retained_at"
SOURCE_VERSION_KEY = "jasmine_source_version"

#: Written into every retained document so a later reader can tell which
#: adapter built it, and so a Gate manifest can cite it.
SOURCE_VERSION = "p4-1"

#: Engine metadata is a string→string map (0.10.2 `MemoryItem.metadata`), so a
#: list is joined. The separator is outside the Core id alphabet, so a join
#: cannot be ambiguous.
_LIST_SEPARATOR = ","


def build_metadata(*, memory_id: str, document_id: str, memory_type: str,
                   provenance: Provenance, content_sha256: str, retained_at: str) -> dict[str, str]:
    """The engine metadata Core writes on retain. Provenance travels with the text."""
    metadata = {
        MEMORY_ID_KEY: memory_id,
        DOCUMENT_ID_KEY: document_id,
        MEMORY_TYPE_KEY: memory_type,
        SOURCE_EVENT_IDS_KEY: _LIST_SEPARATOR.join(provenance.source_event_ids),
        EVIDENCE_IDS_KEY: _LIST_SEPARATOR.join(provenance.evidence_ids),
        CONTENT_SHA256_KEY: content_sha256,
        RETAINED_AT_KEY: retained_at,
        SOURCE_VERSION_KEY: SOURCE_VERSION,
    }
    if provenance.origin_rule_id:
        metadata[ORIGIN_RULE_ID_KEY] = provenance.origin_rule_id
    if provenance.origin_rule_version is not None:
        metadata[ORIGIN_RULE_VERSION_KEY] = str(provenance.origin_rule_version)
    if provenance.origin_event_id:
        # Written and read through one constant. An inline literal here was
        # readable while the key was never written, so every recalled item
        # reported `origin_event_id: None` and nothing noticed.
        metadata[ORIGIN_EVENT_ID_KEY] = provenance.origin_event_id
    return metadata


def split_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw:
        return ()
    return tuple(part for part in raw.split(_LIST_SEPARATOR) if part)


def build_tags(*, bank: BankRef, memory_type: str) -> tuple[str, ...]:
    """Engine tags for visibility scoping and type narrowing.

    Two purposes, both real: `jasmine:scope` makes a bank's own contents
    self-describing, and `jasmine:type:<TYPE>` is the tag Recall filters on when
    the Context Builder asked for a subset. P4 still verifies `memory_type`
    locally from metadata rather than trusting the tag, because a tag is a
    visibility hint and metadata is what Core wrote.
    """
    tags = ["jasmine", "jasmine:scope:global" if bank.scope_kind == "global" else "jasmine:scope:project"]
    if bank.project_id:
        tags.append(f"jasmine:project:{bank.project_id}")
    tags.append(f"jasmine:type:{memory_type}")
    return tuple(tags)


def type_tags(types: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"jasmine:type:{memory_type}" for memory_type in types)


@dataclass(frozen=True)
class Verification:
    """The outcome of checking one engine row against local truth."""

    ok: bool
    reason: str | None = None
    item: dict[str, Any] | None = None
    provenance: Provenance | None = None
    memory_type: str | None = None
    historical: bool = True
    authoritative_conflict: str | None = None


class ProvenanceChecker:
    """Verifies engine rows against the local Memory projection and current truth."""

    def __init__(self, conn: sqlite3.Connection, store: MemoryStore, *,
                 allowed_banks: tuple[BankRef, ...]) -> None:
        self._conn = conn
        self._store = store
        self._allowed = {bank.bank_id for bank in allowed_banks}

    def verify(self, result: EngineResult) -> Verification:
        memory_id = result.metadata.get(MEMORY_ID_KEY)
        if not isinstance(memory_id, str) or not memory_id:
            return Verification(False, "unverified_provenance")
        item = self._store.item(memory_id)
        if item is None:
            # The engine holds a row naming a Memory item this database has never
            # heard of. Either the engine was shared with another deployment or
            # the row was written out of band. Both are refusals, not warnings.
            return Verification(False, "unverified_provenance")
        if result.bank_id not in self._allowed:
            # The engine returned a row from a bank this call may not read. The
            # bank was chosen by the engine's own response, so this is the check
            # that stops a cross-project leak arriving through the response body
            # rather than through the request. The item is deliberately *not*
            # attached: nothing downstream may record its identity.
            return Verification(False, "foreign_project")
        claimed_document = result.metadata.get(DOCUMENT_ID_KEY) or result.document_id
        if item["document_id"] != claimed_document:
            return Verification(False, "unverified_provenance")
        if item["bank_id"] != result.bank_id:
            # A row in the project bank claiming to be the global document (or
            # the reverse) is a bank confusion, not a duplicate. The item is
            # resolved but not attached, for the reason above.
            return Verification(False, "foreign_project")
        if item["status"] == "DELETED":
            return Verification(False, "deleted", item=item,
                                memory_type=item["memory_type"])
        if item["status"] == "OBSOLETE":
            return Verification(False, "obsolete", item=item,
                                memory_type=item["memory_type"])
        for source_event_id in split_ids(result.metadata.get(SOURCE_EVENT_IDS_KEY)):
            reason = self._event_scope_error(source_event_id, item)
            if reason is not None:
                return Verification(False, reason, item=item,
                                    memory_type=item["memory_type"])
        origin_event_id = result.metadata.get(ORIGIN_EVENT_ID_KEY)
        if origin_event_id:
            reason = self._event_scope_error(origin_event_id, item)
            if reason is not None:
                return Verification(False, reason, item=item,
                                    memory_type=item["memory_type"])
        for evidence_id in split_ids(result.metadata.get(EVIDENCE_IDS_KEY)):
            if self._conn.execute("SELECT 1 FROM evidence WHERE evidence_id=?",
                                  (evidence_id,)).fetchone() is None:
                return Verification(False, "evidence_missing", item=item,
                                    memory_type=item["memory_type"])
        conflict = self._authority_conflict(item)
        if conflict is not None:
            return Verification(False, conflict, item=item, memory_type=item["memory_type"])
        provenance = Provenance(
            source_event_ids=split_ids(result.metadata.get(SOURCE_EVENT_IDS_KEY))
            or (item["source_event_id"],),
            evidence_ids=split_ids(result.metadata.get(EVIDENCE_IDS_KEY)) or tuple(item["evidence_ids"]),
            origin_rule_id=item["origin_rule_id"],
            origin_rule_version=item["origin_rule_version"],
            origin_event_id=result.metadata.get(ORIGIN_EVENT_ID_KEY),
        )
        return Verification(True, item=item, provenance=provenance,
                            memory_type=item["memory_type"], historical=True)

    def _event_scope_error(self, event_id: str, item: dict[str, Any]) -> str | None:
        """Why `event_id` cannot be claimed as this item's origin, or `None`.

        Both halves of the old check mattered and only one was implemented: the
        Event must *exist*, and it must belong to this item's scope. The engine
        echoes metadata Core wrote, so an id the local database has never heard
        of was caught — but an id that is perfectly real and belongs to another
        project passed straight through, reached `Provenance.source_event_ids`
        and was written into this project's readable trace. A forged claim that
        names a *foreign real Event* is therefore not merely unverified, it is a
        cross-project forgery, and it gets its own reason so an operator can see
        the difference from an id this deployment never had.

        An Event with no project is a system/global Event, which any item may
        cite; anything else must match the item's own project.
        """
        row = self._conn.execute(
            "SELECT project_id FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            # Raw Events are append-only and cannot be deleted through any Core
            # path, so this means the engine is echoing an id that was never in
            # this database.
            return "source_missing"
        other = row["project_id"]
        if other is not None and other != item["project_id"]:
            return "foreign_source"
        return None

    def _authority_conflict(self, item: dict[str, Any]) -> str | None:
        """Whether current Authority has moved past what this memory says.

        This is VC-14 and design §7's "old Decision" case, and it is decided by
        comparing the stored Rule *version* against the current one — not by
        reading the memory's text. A user discussed engine A, later confirmed
        engine B; the memory of the A decision stays in the bank, traceable and
        visible to an Inspector, but it must not be rendered as the current
        answer. `superseded_decision` and `retired_decision` are the two reasons,
        and both keep the row out of the Context Pack while leaving it in the
        trace.
        """
        if item["memory_type"] != "CONFIRMED_DECISION":
            return None
        rule_id = item["origin_rule_id"]
        if not rule_id:
            # A Decision memory with no origin Rule cannot be checked against
            # Authority, so it cannot be asserted as current. It stays a
            # historical note.
            return None
        row = self._conn.execute(
            "SELECT status, current_version FROM rules WHERE rule_id=?", (rule_id,)
        ).fetchone()
        if row is None:
            return "retired_decision"
        if row["status"] == "RETIRED":
            return "retired_decision"
        if int(row["current_version"]) != int(item["origin_rule_version"] or 0):
            return "superseded_decision"
        return None


#: Reasons whose filtered record must not name the item it refers to.
#:
#: The text is already withheld, but the *identifier* is information too. Writing
#: a foreign project's `memory_id` into this project's trace — and the trace is
#: readable through `GET /v1/memory/traces/{id}` — turns a refused result into a
#: project-enumeration oracle, which is the one thing the deployment-mode
#: contract says it will not create.
_OPAQUE_REASONS = frozenset({"foreign_project", "unverified_provenance", "source_missing",
                             "evidence_missing"})


def filtered_from(result: EngineResult, verification: Verification) -> FilteredItem:
    reason = verification.reason or "unverified_provenance"
    item = verification.item or {}
    opaque = reason in _OPAQUE_REASONS
    return FilteredItem(
        engine_result_id=result.engine_result_id,
        bank_id=result.bank_id,
        reason=reason,
        memory_id=None if opaque else item.get("memory_id"),
        document_id=None if opaque else result.document_id,
        memory_type=None if opaque else (verification.memory_type or item.get("memory_type")),
    )
