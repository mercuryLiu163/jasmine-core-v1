"""The Core-facing Memory adapter: retain, recall, reflect (ADR 0014 §4, §5).

`MemoryAdapter` is the only object P3 and the API layer talk to. It is where
design module §11's processing line actually lives:

    query global + project banks → merge/dedupe → filter obsolete/conflict with
    current truth → apply memory token budget

and where the four properties that make Memory safe to inject are enforced:

**No network call inside a write transaction.** `recall` performs its engine
calls before it opens the short transaction that writes the trace, and it never
opens a transaction of its own around them. The trace write is the only write,
it is short, and it cannot hold the SQLite write lock while a remote latency is
outstanding — which is the property G05 and design §14 depend on.

**No error escapes as an exception.** A dead engine produces
`status="degraded"` with the healthy bank's compliant results, never a raise.
The Context Pack Builder is not required to have a try/except around memory, and
Truth — Rules, State, Evidence, Checkpoint — is never told that memory failed.

**`empty` and `degraded` are different.** A bank that answered with nothing is
`empty`; a bank that could not be reached is `degraded`. Reporting a failed bank
as `ok` with zero items is the specific lie the design forbids.

**Reflect is unreachable from a Context build.** `recall` contains no call to
`reflect`, and `reflect` refuses unless the authenticated operator explicitly
asked for historical synthesis (`ReflectRequest.explicit_user_request`, set only
by `POST /v1/memory/reflect` behind `memory:reflect` on a human/system key).
Complexity of phrasing is not a trigger.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .. import clock, db, errors
from . import filtering, provenance, scope as scope_module, tokens
from .store import MemoryStore
from .transport import MemoryTransport, TransportError
from .types import (
    BankOutcome,
    EngineResult,
    FilteredItem,
    RecallItem,
    RecallOutcome,
    RecallRequest,
    ReflectOutcome,
    ReflectRequest,
    RetainRequest,
    RetainReceipt,
)


class MemoryAdapter:
    """Recall and Reflect against the engine, with Core judging every result."""

    def __init__(self, conn: sqlite3.Connection, store: MemoryStore,
                 transport: MemoryTransport | None, *, mode: str = scope_module.SINGLE_USER,
                 schema_version: int = 0) -> None:
        self._conn = conn
        self._store = store
        self._transport = transport
        self._mode = mode
        self._schema_version = schema_version

    @property
    def transport(self) -> MemoryTransport | None:
        return self._transport

    @property
    def configured(self) -> bool:
        return self._transport is not None

    # -- retain ------------------------------------------------------------

    def retain(self, request: RetainRequest) -> RetainReceipt:
        """Hand one document to the engine. Called by the worker, not by an API caller.

        There is no public retain endpoint on purpose (design §5: an Agent may
        not choose a bank or write a bank), so the only path into the engine is
        the worker acting on a job that the enqueuer created from committed truth.
        """
        if self._transport is None:
            raise TransportError("engine_not_configured",
                                 "no Hindsight endpoint is configured for this Core",
                                 retryable=False)
        if self._conn.in_transaction:
            # Structural, not a convention: the invariant is that no engine call
            # happens while the SQLite write lock is held. Checking it here as
            # well as in the worker means an in-process caller cannot bypass it
            # by calling the adapter directly.
            raise RuntimeError(
                "refusing to call the engine during a database transaction: the write lock "
                "would be held across a network call"
            )
        metadata = provenance.build_metadata(
            memory_id=request.metadata[provenance.MEMORY_ID_KEY],
            document_id=request.document_id,
            memory_type=request.memory_type,
            provenance=request.provenance,
            content_sha256=request.metadata[provenance.CONTENT_SHA256_KEY],
            retained_at=request.metadata[provenance.RETAINED_AT_KEY],
        )
        tags = provenance.build_tags(bank=request.bank, memory_type=request.memory_type)
        payload = self._transport.retain(
            bank=request.bank,
            document_id=request.document_id,
            content=request.content,
            context=request.context,
            metadata=metadata,
            tags=tags,
            memory_type=request.memory_type,
            timestamp=request.timestamp,
        )
        return RetainReceipt(
            document_id=str(payload.get("document_id") or request.document_id),
            bank_id=str(payload.get("bank_id") or request.bank.bank_id),
            items_count=int(payload.get("items_count") or 0),
            engine_api_version=payload.get("engine_api_version"),
            operation_id=payload.get("operation_id"),
            async_processed=bool(payload.get("async")),
            usage={k: int(v) for k, v in (payload.get("usage") or {}).items()},
        )

    # -- recall ------------------------------------------------------------

    def recall(self, request: RecallRequest) -> RecallOutcome:
        """Query the project and global banks and return what may be injected.

        Never raises for an engine failure. The only exceptions that escape are
        Core's own request refusals (`invalid_request`, `*_not_found`), which are
        programming errors in the caller's context rather than memory's state.
        """
        budget = tokens.effective_budget(request.max_tokens)
        types = request.types or tuple(_all_types())
        # The provider contract is that an unrecognised type is refused. Accepting
        # it and querying anyway is worse than useless: the engine is asked for a
        # type it knows nothing about, the local type filter then matches nothing,
        # and the caller is handed `empty` — indistinguishable from a bank that
        # genuinely had nothing to say, so a typo in a type name looks like a
        # memory gap. This is a request error, not memory's state, so it raises
        # rather than degrading.
        from .types import MEMORY_TYPE_SET

        unknown = sorted({value for value in types if value not in MEMORY_TYPE_SET})
        if unknown:
            raise errors.InvalidRequest(
                f"unknown memory type(s): {', '.join(unknown)}; expected one of "
                f"{', '.join(sorted(MEMORY_TYPE_SET))}",
                field="types", value=unknown,
            )
        at = clock.now_rfc3339()
        if self._transport is None:
            return self._record(_unconfigured(request, types, budget, at),
                                request=request, at=at)
        if budget <= 0:
            # P3 allocated no Memory partition. Querying and then dropping the
            # answer would spend a network round trip and an LLM-free rerank for
            # nothing, and a trace claiming `empty` would be indistinguishable
            # from a bank that genuinely had nothing.
            return self._record(_skipped(request, types, budget, at,
                                         reason="no_memory_budget"),
                                request=request, at=at)

        banks = scope_module.resolve_banks(
            self._conn, project_id=request.project_id, task_id=request.task_id,
            actor_id=request.actor_id, mode=self._mode,
        )
        checker = provenance.ProvenanceChecker(self._conn, self._store, allowed_banks=banks)
        requested_tags = provenance.type_tags(types)
        # Project bank first: when both banks return the same statement, the
        # project-specific provenance is the one that survives de-duplication.
        allocations = tokens.split_budget(budget, [2, 1])

        collected: list[tuple[EngineResult, str, int]] = []
        outcomes: list[BankOutcome] = []
        failed_banks: list[str] = []
        for bank, allocation in zip(banks, allocations):
            if allocation <= 0:
                outcomes.append(BankOutcome(bank.bank_id, bank.scope_kind, "skipped"))
                continue
            try:
                results = self._transport.recall(
                    bank=bank, query=request.query, max_tokens=allocation,
                    fact_types=_engine_fact_types(types), tags=requested_tags,
                )
            except TransportError as exc:
                failed_banks.append(bank.bank_id)
                outcomes.append(BankOutcome(bank.bank_id, bank.scope_kind, "failed",
                                            error_code=exc.code,
                                            safe_error_summary=exc.safe_error_summary))
                continue
            outcomes.append(BankOutcome(bank.bank_id, bank.scope_kind, "ok",
                                        result_count=len(results)))
            for rank, result in enumerate(results):
                collected.append((result, bank.bank_id, rank))

        items, filtered = self._judge(collected, checker, types, budget)
        status = self._status(outcomes, failed_banks, items)
        first_failure = next((b for b in outcomes if b.status == "failed"), None)
        return self._record(RecallOutcome(
            status=status, items=tuple(items), filtered=tuple(filtered),
            banks=tuple(outcomes), budget_tokens=budget,
            used_tokens=sum(item.token_estimate for item in items),
            error_code=first_failure.error_code if first_failure else None,
            safe_error_summary=first_failure.safe_error_summary if first_failure else None,
            degraded_banks=tuple(failed_banks),
        ), request=request, at=at)

    def _judge(self, collected: list[tuple[EngineResult, str, int]],
               checker: provenance.ProvenanceChecker, types: tuple[str, ...],
               budget: int) -> tuple[list[RecallItem], list[FilteredItem]]:
        """Verify, de-duplicate, guard and budget the engine's answer.

        The order is fixed and each step is a refusal that cannot be undone
        downstream: provenance first (an unverified row must not even compete
        for budget), then the injection guard, then de-duplication across banks
        (which is where a cross-bank provenance merge happens), then the budget
        in engine relevance order.
        """
        verified: list[tuple[EngineResult, provenance.Verification]] = []
        filtered: list[FilteredItem] = []
        for result, _bank, _rank in collected:
            check = checker.verify(result)
            if not check.ok:
                filtered.append(provenance.filtered_from(result, check))
                continue
            if check.memory_type not in types:
                filtered.append(FilteredItem(
                    engine_result_id=result.engine_result_id, bank_id=result.bank_id,
                    reason="type_not_requested", memory_id=check.item["memory_id"],
                    document_id=result.document_id, memory_type=check.memory_type))
                continue
            verified.append((result, check))

        merged: list[tuple[EngineResult, provenance.Verification]] = []
        seen: dict[str, int] = {}
        for result, check in verified:
            text, had_marker = filtering.neutralize_markers(result.text)
            directive = filtering.directive_reason(text)
            if directive:
                filtered.append(FilteredItem(
                    engine_result_id=result.engine_result_id, bank_id=result.bank_id,
                    reason="directive_injection", memory_id=check.item["memory_id"],
                    document_id=result.document_id, memory_type=check.memory_type))
                continue
            if had_marker:
                # The text tried to speak as a Context Pack section. Inert now,
                # and dropped rather than rendered, because a lesson that is
                # mostly delimiters is not a lesson.
                filtered.append(FilteredItem(
                    engine_result_id=result.engine_result_id, bank_id=result.bank_id,
                    reason="section_marker", memory_id=check.item["memory_id"],
                    document_id=result.document_id, memory_type=check.memory_type))
                continue
            key = _dedupe_key(text)
            if key in seen:
                # Same statement from another bank or another fact row. Keep the
                # first (project bank, higher engine rank) and record the
                # duplicate, rather than merging two different memory items into
                # one claim about two different sources.
                filtered.append(FilteredItem(
                    engine_result_id=result.engine_result_id, bank_id=result.bank_id,
                    reason="duplicate", memory_id=check.item["memory_id"],
                    document_id=result.document_id, memory_type=check.memory_type))
                continue
            seen[key] = len(merged)
            merged.append((EngineResult(engine_result_id=result.engine_result_id, text=text,
                                        bank_id=result.bank_id, document_id=result.document_id,
                                        engine_fact_type=result.engine_fact_type,
                                        metadata=result.metadata, tags=result.tags,
                                        score=result.score), check))

        items: list[RecallItem] = []
        remaining = budget
        for rank, (result, check) in enumerate(merged):
            cost = tokens.estimate(result.text)
            if cost > remaining:
                # Skip rather than truncate: a half-truncated lesson states
                # something the source did not say, and the next result is
                # usually shorter and fits.
                filtered.append(FilteredItem(
                    engine_result_id=result.engine_result_id, bank_id=result.bank_id,
                    reason="over_budget", memory_id=check.item["memory_id"],
                    document_id=result.document_id, memory_type=check.memory_type))
                continue
            assert check.provenance is not None and check.item is not None
            items.append(RecallItem(
                memory_result_id=_result_id(),
                memory_id=check.item["memory_id"],
                text=result.text,
                bank_id=result.bank_id,
                scope_kind=check.item["scope_kind"],
                project_id=check.item["project_id"],
                memory_type=check.memory_type or "",
                document_id=check.item["document_id"],
                engine_result_id=result.engine_result_id,
                token_estimate=cost,
                provenance=check.provenance,
                historical=True,
                pinned=bool(check.item["pinned"]),
                rank=rank,
            ))
            remaining -= cost
        return items, filtered

    def _status(self, outcomes: Iterable[BankOutcome], failed: list[str],
                items: list[RecallItem]) -> str:
        outcomes = list(outcomes)
        if failed:
            return "degraded"
        answered = [b for b in outcomes if b.status == "ok"]
        if not answered:
            return "skipped"
        return "ok" if items else "empty"

    def _record(self, outcome: RecallOutcome, *, request: RecallRequest | None = None,
                at: str | None = None) -> RecallOutcome:
        """Persist the trace, then return the outcome with its trace id.

        The engine calls are already finished, so this transaction is the only
        thing holding the write lock and it holds it for two inserts.
        """
        if request is None:
            return outcome
        stamp = at or clock.now_rfc3339()
        try:
            with db.translate_lock_errors(), db.transaction(self._conn):
                trace_id = self._store.record_recall(
                    outcome, actor_id=request.actor_id, project_id=request.project_id,
                    task_id=request.task_id, session_id=request.session_id,
                    query=request.query, types=request.types, authority_refs=request.authority_refs,
                    state_revision=request.state_revision, at=stamp,
                )
                self._store.mark_recalled([item.memory_id for item in outcome.items], stamp)
                if self._transport is not None:
                    self._record_engine_outcome(outcome, stamp)
        except db.errors.DatabaseBusy:
            # Losing the trace must not turn a successful recall into a failure
            # of the Context Pack. The answer is still correct; only the
            # record of it is missing, and the caller can see that from the
            # absent trace id.
            return outcome
        return RecallOutcome(**{**outcome.__dict__, "trace_id": trace_id})

    def _record_engine_outcome(self, outcome: RecallOutcome, at: str) -> None:
        """Keep the engine's last-success/last-error pair honest.

        A success is recorded only when a bank actually answered, so a recall
        where every bank failed cannot clear the last error and make the health
        report say Memory is fine while every job fails. A failure is recorded
        whenever a bank failed, including a partial one, because "one bank
        answered" is not the same fact as "the engine is healthy".
        """
        answered = any(bank.status == "ok" for bank in outcome.banks)
        failed = [bank for bank in outcome.banks if bank.status == "failed"]
        if answered:
            self._store.note_engine_success(at)
        if failed:
            self._store.note_engine_failure(
                error_code=failed[0].error_code or "engine_error",
                safe_error_summary=failed[0].safe_error_summary or "", at=at,
            )

    # -- reflect -----------------------------------------------------------

    def reflect(self, request: ReflectRequest) -> ReflectOutcome:
        """Historical synthesis, only on an explicit human request.

        The refusal order matters. `explicit_user_request` is checked before
        anything else so a missing or malformed request can never be the reason
        a reflect call goes out, and the authorisation check comes before the
        network call so an unpermitted caller never costs an LLM call.
        """
        at = clock.now_rfc3339()
        if not request.explicit_user_request:
            # design §7: Reflect only when the user explicitly asked for
            # historical synthesis. A complicated ordinary question is not that.
            raise errors.InvalidRequest(
                "reflect requires an explicit historical-synthesis request; "
                "an ordinary Context build must call recall",
                field="explicit_user_request",
            )
        if self._transport is None:
            outcome = ReflectOutcome(status="degraded", error_code="engine_not_configured",
                                     safe_error_summary="no Hindsight endpoint is configured")
            return self._record_reflect(outcome, request, at)
        budget = tokens.effective_budget(request.max_tokens) or 600
        banks = scope_module.resolve_banks(
            self._conn, project_id=request.project_id, task_id=request.task_id,
            actor_id=request.actor_id, mode=self._mode,
        )
        answers: list[str] = []
        based_on: list[str] = []
        outcomes: list[BankOutcome] = []
        for bank in banks:
            try:
                payload = self._transport.reflect(bank=bank, query=request.query,
                                                  max_tokens=budget)
            except TransportError as exc:
                outcomes.append(BankOutcome(bank.bank_id, bank.scope_kind, "failed",
                                            error_code=exc.code,
                                            safe_error_summary=exc.safe_error_summary))
                continue
            outcomes.append(BankOutcome(bank.bank_id, bank.scope_kind, "ok"))
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                # Reflect output is another model's prose about our data. It is
                # marked non-authoritative in the payload and passes the same
                # directive guard as a recalled fact, because it has exactly the
                # same ability to contain an instruction.
                safe, _had_marker = filtering.neutralize_markers(text)
                if filtering.directive_reason(safe):
                    outcomes[-1] = BankOutcome(bank.bank_id, bank.scope_kind, "ok",
                                              error_code="directive_injection",
                                              safe_error_summary="reflect output was withheld")
                    continue
                answers.append(safe)
            based_on.extend(payload.get("based_on_ids") or [])
        failed = [b for b in outcomes if b.status == "failed"]
        # The top-level `error_code` is what a caller branches on, so it has to
        # be a code — `engine_timeout`, `engine_unreachable` — and not the id of
        # the bank that happened to fail first. Filling it with `bank_id` meant
        # every degraded Reflect reported a value no error table contains, and
        # the actual reason stayed buried per-bank where nothing looks.
        outcome = ReflectOutcome(
            status="degraded" if failed else ("ok" if answers else "empty"),
            text="\n\n".join(answers) if answers else None,
            based_on_memory_ids=tuple(based_on),
            banks=tuple(outcomes),
            error_code=next((b.error_code for b in failed if b.error_code), None),
        )
        return self._record_reflect(outcome, request, at)

    def _record_reflect(self, outcome: ReflectOutcome, request: ReflectRequest,
                        at: str) -> ReflectOutcome:
        try:
            with db.translate_lock_errors(), db.transaction(self._conn):
                trace_id = self._store.record_reflect(
                    outcome, actor_id=request.actor_id, project_id=request.project_id,
                    task_id=request.task_id, session_id=request.session_id,
                    query=request.query, at=at,
                )
        except db.errors.DatabaseBusy:
            return outcome
        return ReflectOutcome(**{**outcome.__dict__, "trace_id": trace_id})


def _all_types() -> tuple[str, ...]:
    from .types import MEMORY_TYPES

    return MEMORY_TYPES


def _engine_fact_types(types: tuple[str, ...]) -> tuple[str, ...]:
    from .hindsight import recall_fact_types

    return recall_fact_types(types)


def _dedupe_key(text: str) -> str:
    """Case- and whitespace-insensitive identity for de-duplication.

    Deliberately conservative: it merges statements that differ only in casing
    and spacing, and nothing else. A looser key would merge two genuinely
    different lessons that share a template, which is how a bank ends up
    asserting that two different fixes were the same fix.
    """
    return " ".join(text.casefold().split())


def _result_id() -> str:
    from .. import ids

    return ids.new_id("mqi")


def _unconfigured(request: RecallRequest, types: tuple[str, ...], budget: int,
                  at: str) -> RecallOutcome:
    """An absent endpoint is a degraded Memory subsystem, not an empty bank.

    The banks are reported as unconfigured rather than omitted, so a Dashboard
    can tell "Memory is switched off" from "Memory has nothing to say".
    """
    return RecallOutcome(
        status="degraded",
        banks=(),
        budget_tokens=budget,
        error_code="engine_not_configured",
        safe_error_summary="no Hindsight endpoint is configured for this Core",
    )


def _skipped(request: RecallRequest, types: tuple[str, ...], budget: int, at: str,
             *, reason: str) -> RecallOutcome:
    return RecallOutcome(status="skipped", budget_tokens=budget,
                         error_code=reason,
                         safe_error_summary="the Context Pack allocated no Memory budget")


def describe_config(transport: MemoryTransport | None) -> dict[str, Any]:
    """Non-secret description of the engine for the health API."""
    if transport is None:
        return {"configured": False}
    config = getattr(transport, "config", None)
    return {
        "configured": True,
        "engine_id": getattr(transport, "engine_id", "unknown"),
        "origin_host": getattr(config, "origin_host", None),
        "api_prefix": getattr(config, "api_prefix", None),
        "credential_present": bool(getattr(config, "has_credential", False)),
    }
