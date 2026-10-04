# ADR 0014: P4 long-term Memory, durable jobs and the Memory lifecycle

Status: implementation candidate. **No P4 Gate is claimed.** `P4-G01`…`P4-G07`
require a real Hindsight process, a real LLM extractor, a real Agent session and
a real P3 Context Pack; none of that is in this repository. What this ADR freezes
is the contract. §12 lists what a reviewer still has to check against a live
deployment, and §13 the shared files whose renumbering and integration the
integration owner owns.

Source requirements: detail design §7 (Hindsight), §8 (Context Pack budget),
§9 (Memory Inspector), §12 (secret filter and audit), §14 (degradation); module
design §11 (Hindsight adapter), §12 (Memory Job Worker), §10 (Context Builder
boundary); implementation and validation taskbook VC-14…VC-19 and VC-22; the P4
taskbook §§2–8.

## 1. The one invariant

Memory is history. Nothing in P4 may activate, supersede or retire a Rule, move
State, mint Evidence, overwrite a Checkpoint, or raise a Task revision. A HARD
Rule is loaded deterministically by P3 and is never substituted by a top-k recall
(detail design §7, §3). Every design decision below is subordinate to that.

The second invariant is about authority: the Core chooses the bank, and the
provenance decides what may be shown. An Agent cannot select a bank, write a
bank, or turn a recall result into a rule.

## 2. Public identifiers, frozen vocabulary

Five prefixes are registered: `mem` (Memory item), `mjb` (durable Memory job),
`mja` (job attempt), `mtr` (Recall/Reflect trace), `mqi` (one result in a trace).
`del` is deliberately not used: `evt_` already names every command Event, and a
second identifier for the same command would give clients two ways to name one
thing.

| Frozen | Value |
| --- | --- |
| `MEMORY_TYPES` | `CONFIRMED_DECISION`, `STABLE_PROJECT_FACT`, `RESOLVED_ISSUE`, `VERIFIED_OUTCOME`, `FAILURE_LESSON` |
| `GLOBAL_BANK_ID` | `jasmine-global` |
| `PROJECT_BANK_PREFIX` | `jasmine-project-{project_id}` (a Core `prj_` id; the character class is closed) |
| `JOB_STATUSES` | `PENDING`, `RUNNING`, `DONE`, `FAILED` (the design's own words) |
| `JOB_KINDS` | `retain`, `delete`, `invalidate` |
| `ITEM_STATUSES` | `ACTIVE`, `OBSOLETE`, `DELETED` |
| `ENGINE_STATES` | `PENDING_RETAIN`, `RETAINED`, `RETAIN_FAILED`, `DELETE_PENDING`, `DELETED`, `DELETE_FAILED` |
| `RECALL_STATUSES` | `ok`, `empty`, `degraded`, `skipped` |
| `FILTER_REASONS` | see `memory/types.py`; 15 machine-readable reasons |

**An optional namespace on every bank id.**
`JASMINE_MEMORY_BANK_NAMESPACE`, defaulting to empty, prefixes whatever
`project_bank` and `global_bank` return, so a Core that never sets it produces
exactly the ids it produced before this existed. It exists because project banks
were already unique per run — they embed a fresh `prj_` ULID — while
`jasmine-global` was shared by every run *and* by the production deployment, so
a Gate reading it was reading somebody else's memory while reporting its own
namespace. A Gate sets it from the operator's `bank_suffix`, which is what makes
that suffix shape addresses rather than sit in a config being checked for
non-emptiness. `scope_of` and `is_valid_bank_id` strip the prefix before
matching, so in a namespaced deployment a bare `jasmine-global` is no longer
ours — somebody else's address, and refused. A malformed value is refused rather
than dropped, because a silently empty namespace falls back to exactly the
un-namespaced ids this exists to prevent.

A type *name* is not evidence. `REQUIRED_PROVENANCE` binds each type to the
committed fact it needs, and §4 implements those checks.

## 3. Schema

`m0009_memory` adds eight tables and touches nothing that exists. It creates no
P3 Checkpoint/Context table and no P6 Dashboard table, so the P3 contract stays
single-sourced.

| Table | Purpose | Guarantee |
| --- | --- | --- |
| `memory_items` | one retained source, its sanitised text, provenance, lifecycle | never deleted; identity columns immutable; `scope_kind`/`project_id` CHECK stops a project source reaching the global bank |
| `memory_item_sources` | additional committed Events behind one item | append-only |
| `memory_jobs` | the durable queue | `(source_event_id, kind)` UNIQUE; never deleted; identity immutable; a lease always has an expiry |
| `memory_job_attempts` | every claim, retain, invalidate, delete and failure | append-only; `last_error_code` is a summary, never the only record |
| `memory_query_traces` | one Recall/Reflect, its banks, budget and outcome | append-only; stores `query_sha256` and `query_length`, **not** the query |
| `memory_query_trace_items` | every result, included or filtered, with its reason | append-only |
| `memory_engine_status` | one row: origin host, API version, last probe, last success, last error | no column can hold a credential |
| `memory_project_actors` | project grants, consulted only in `acl` mode | — |

The uniqueness on `(source_event_id, kind)` is what makes the recovery scan
idempotent by *schema* rather than by convention, and the immutability triggers
are what make "a retry may not change what is retained" true for an out-of-band
writer as well as for P4's own code.

The query text is deliberately not stored. A Context Pack query derives from a
user's turn, and duplicating raw conversation into a second table would create a
copy no retention policy covers. `query_sha256` plus `query_length` make the
trace joinable to the P7 Validation Recorder, which already holds the raw turn
under its own permissions.

## 4. Eligibility: a type name is not evidence

`memory/eligibility.py` checks committed P1 truth for each source Event:

| Memory type | Required committed proof |
| --- | --- |
| `CONFIRMED_DECISION` | an ACTIVE Authority Rule of kind `DECISION`, whose origin Event resolves |
| `STABLE_PROJECT_FACT` | an ACTIVE project-scoped Rule of kind `RULE` |
| `VERIFIED_OUTCOME` | ≥1 `PASS` Evidence for the same task whose `reference_status` is not `BROKEN_REFERENCE` |
| `FAILURE_LESSON` | a `FAIL` Evidence, or a committed step/task transition to `FAILED` |
| `RESOLVED_ISSUE` | a committed step transition to `STALE` |

A PROPOSED Rule, a task-scoped Rule, `INFO` Evidence, a missing artifact, an
unverifiable workspace reference and a source Event with project scope presented
as global are all refused, each with a distinct reason. Content that is mostly a
credential is refused rather than half-redacted, and the Raw Event keeps the
original text under `events:read`.

**P2 boundary.** P2 owns Interpretation, Resolution and review; its approved
*candidates* are a second, wider source. `SourceRegistry` is the extension point
and P2 registers a candidate source on it. Until then the registry holds exactly
`AuthorityEvidenceSource`, which reads P1 truth only. P4 does not reach into P2's
tables, guess at its schema, or treat an unreviewed candidate as eligible.

## 5. The adapter

`memory/adapter.py` is the only object P3 and the API talk to.

**Recall.** Resolve banks from a validated `(project_id, task_id)` — project bank
first, then global. Ask the engine for both banks with a local budget split 2:1
in the project's favour. Verify, de-duplicate, guard, budget, and report.

**Provenance, re-checked locally.** Core writes `jasmine_memory_id` and the rest
of the provenance into the engine `metadata` on retain, and *re-checks* it on
every recall: the id must name a local item, the item's `document_id` must equal
the one claimed, the item's bank must be one this call derived, every referenced
Raw Event and Evidence must still resolve, and the item must be `ACTIVE`. A row
without Core metadata is `unverified_provenance` and is never rendered. This is
the answer to a forged remote source: refused by construction, not by pattern
matching on text.

**The record is the authority, not the tables the ids point at.** Every earlier
form of this check asked whether an id *resolves*, and an id that resolves is
not evidence that it belongs to this item. Two review rounds found the
consequences, and they are the same shape each time:

* a Raw Event from **another** project, real and resolvable, reached
  `Provenance.source_event_ids` and this project's readable trace — a
  cross-project forgery with an identifier attached;
* an Evidence id from another project passed the same way, and Evidence is worse
  placed for that check than Event: it carries no project of its own, reaching
  one only through its task, so `SELECT 1 FROM evidence` had nothing to compare
  against;
* and within one project, *any* unrelated Event could replace the item's real
  source, so a forged metadata claim rewrote the provenance an Inspector walks
  back through.

The check is now against what Core itself wrote at retain time —
`memory_item_sources` and `evidence_ids_json`. Every id the engine declares must
be a member of that record, and the `Provenance` handed to a caller is built
*from* the record rather than from the echo, so an engine that drops a
supporting source cannot shorten the trace either. Refusals are `foreign_source`
and `foreign_evidence`; `source_missing` and `evidence_missing` remain for an id
that is recorded but no longer resolves. `jasmine_origin_event_id` is compared to
the item's persisted origin instead of being trusted outright. An Event with no
project is a system or global Event and may be cited by anything.

**Where an item's origin came from.** `memory_items.origin_event_id` is the Event
the *content* came from, which for a Decision is not `source_event_id`: the
latter is the `rule.approved` Event that created the row, the former is the Event
the Rule version was written from. Only `source_event_id` used to be persisted
and the worker substituted it, so every recalled Decision reported an approval as
its origin and the real one was recorded nowhere. `Candidate.provenance` already
carried it; it is now stored and carried back out unchanged.

**Old decisions.** For a `CONFIRMED_DECISION` item, the stored Rule *version* is
compared with the current one. A newer version is `superseded_decision`, a
retired Rule is `retired_decision`; both keep the row out of the Context Pack and
keep it visible in the trace and the Inspector. This is VC-14, decided by
comparing Authority, not by reading the memory's prose.

The stored version is read from `rule_changes` at the source Event's `seq`, never
from `rules.current_version` at scan time. The current column answers a different
question — "which version is active *now*" — and reading it during a scan of
history made a Decision approved at version 1 and later superseded record itself
as version 2. Both memories then claimed to be current, neither was ever filtered,
and the superseded Decision was rendered as the live answer. Because a scan runs
after the fact, the version that was current for an Event can only be recovered
from that Event's own change record. Consequently a `rule.approved` Event is
eligible even when the Rule is now `SUPERSEDED` or `RETIRED`: the approval is
committed history, and the two filters above are what stop it being served.

**Injection guard.** Engine text has its Context Pack section delimiters
neutralised, and an item whose text tries to *instruct* — ignore previous rules,
the rule no longer binds, bypass the guard, this memory supersedes the authority,
treat this as a hard rule, no need to verify — is dropped as
`directive_injection`. A memory is not a rule, and this module cannot make one.

**Budget.** `ceil(len/4)` locally, with a 4% margin. The count is ours on purpose:
Hindsight returns no per-fact token counts, and a budget adjudicated by the
engine's tokenizer would let a Core release silently change how much memory fits
a pack. An over-budget item is *skipped*, not truncated, because a
half-truncated lesson states something the source did not say. `None`/0 means P3
allocated no Memory partition, and then the engine is not called at all.

**Status.** `degraded` when any bank failed, `empty` when every bank answered with
nothing, `ok` otherwise, `skipped` when there was no budget. A failed bank is
never reported as `ok` with zero items. The healthy bank's compliant results are
still returned, and `degraded_banks` says which bank did not answer.

**Reflect** is unreachable from a Context build: `recall` has no call site for it,
and `reflect` refuses unless `ReflectRequest.explicit_user_request` is set, which
only `POST /v1/memory/reflect` does, behind `memory:reflect` on a human/system
key. The output is generated prose about the bank and passes the same directive
guard, because it has the same ability to contain an instruction.

## 6. Transport, and what the real Hindsight API forced

`memory/hindsight.py` is a real HTTP client over the standard library — the
package still has no third-party dependency (ADR 0001 §2.1). Every claim below
was read from Hindsight **0.10.2**'s published OpenAPI document
(`https://hindsight.vectorize.io/openapi.json`, `info.version = 0.10.2`,
retrieved 2026-09-29) and is cited so a reviewer can re-check it.

| Operation | Request | Response |
| --- | --- | --- |
| version | `GET /version` (root, **not** under the API prefix) | `{api_version, features{…}}` |
| retain | `POST /v1/default/banks/{bank_id}/memories` | `{success, bank_id, items_count, async, operation_id, usage}` |
| recall | `POST /v1/default/banks/{bank_id}/memories/recall` | `{results[], chunks, source_facts}` |
| reflect | `POST /v1/default/banks/{bank_id}/reflect` | `{text, based_on, usage}` |
| list units | `GET /v1/default/banks/{bank_id}/memories/list?document_id=` | `{items[], total, limit, offset}` |
| invalidate unit | `PATCH …/memories/{memory_id}` `{"state":"invalidated"}` | memory unit |
| document read-back | `GET …/documents/{document_id}` | document |
| delete document | `DELETE …/documents/{document_id}` | `{success, message, document_id, memory_units_deleted}` |

Five consequences, each a decision:

1. **`document_id` is the idempotency key and `update_mode="replace"` is its
   upsert.** A re-send deletes the previous document *and all its memories* and
   re-extracts. So a re-send is safe to repeat *and* it renumbers the memory
   units: a remote memory unit id is **not** a stable handle. Receipts record the
   document id, never a unit id, and every lifecycle and read-back path addresses
   units by listing the document. The transport sends `update_mode` explicitly so
   a future engine default cannot turn a retry into a second copy.
2. **Retain is synchronous, and the receipt is read, not assumed.** `usage` and
   `items_count` exist only for `async=false`; `operation_id` only for
   `async=true`. A durable worker needs a *completion* receipt, not an
   acknowledgement that work was queued, so the transport always sends
   `async=false` — and then holds the response to the same standard: the
   official schema makes `success` and `async` **required** members of
   `RetainResponse`, and `success` a required member of `DeleteDocumentResponse`.
   The check used to be `if payload.get("success") is False`, which means an
   absent field reads as success, so an empty `{}`, a `"success": null`, and an
   `async=true` queued operation were all recorded as a completed retain: job
   `DONE`, item `RETAINED`, an Inspector reporting a document that was never
   stored. Delete was worse — `bool(payload.get("success", True))` turned an
   explicit `false` into `true`. A missing or non-boolean `success` is now
**Reflect reports a reason, not an address.** `ReflectOutcome.error_code` is what
a caller branches on, and it was filled with the *bank id* of the first failed
bank — a value no error table contains, while the actual reason sat per-bank
where nothing looked. It now carries that bank's `error_code`.

**A reason without its explanation is half an answer.**
`ReflectOutcome.safe_error_summary` was never assigned, so a degraded Reflect
carried `error_code` at the top level while the engine's own words sat only in
the per-bank outcomes. Both now travel together, as they do on `RecallOutcome`.

**A type we do not have is a refusal.** `recall` accepted an unrecognised
`types` entry, queried the engine for it, and the local type filter then matched
nothing — so a typo returned `empty`, indistinguishable from a bank with nothing
to say. `InvalidRequest` names the offender instead.

   `engine_protocol_error` (the response does not follow the schema), an explicit
   `false` is `engine_retain_failed` / `engine_delete_failed`, and a queued
   `async=true` is `engine_retain_unconfirmed`. None of them can reach `DONE`.

   The second pass checked the *rest* of the contract rather than `success`
   alone, because a receipt is evidence only if it describes the request that
   was made. `RetainResponse` requires `success`, `bank_id`, `items_count` and
   `async`; `DeleteDocumentResponse` requires `success`, `message`,
   `document_id` and `memory_units_deleted`. A missing or `null` field is
   `engine_protocol_error`. A non-boolean `async` (`"true"`, `1`) slipped through
   because the test was `is True` and read as synchronous. A `bank_id` naming a
   different bank, and a `document_id` naming a different document, were echoed
   into the receipt unexamined — an acknowledgement about somebody else's data,
   recorded as this one's result. Both are now refusals. The worker also refuses
   to mark a document `DELETED` from any receipt whose `success` is falsy, so the
   guarantee does not rest on every future `MemoryTransport` being this strict.
3. **Recall's `types` is the engine's vocabulary, not ours.** Core's five types
   have no remote equivalent, so they travel as `metadata` (returned on every
   recalled result) and as `tags` (used for the visibility filter). `observation`
   is **never** requested: an observation is an LLM-consolidated belief with no
   path back to a Raw Event, and design §16 forbids a high-authority fact that
   cannot be traced. Core filters by type locally, where the provenance can be
   checked.
4. **Soft retire is a first-class engine operation.** `state="invalidated"` is
   documented as excluded from recall and consolidation, kept for audit,
   reversible. That is exactly design §9's `obsolete`, so obsolete is local-first
   plus a queued `invalidate` job, and only `DELETE /documents/{id}` is physical.
5. **Authentication is not part of the base API.** The 0.10.2 document declares
   no `securitySchemes`; each path takes an optional `Authorization` header that a
   tenant extension validates. A single-user deployment needs no credential and a
   multi-tenant one needs an operator-provided key, and the header is sent only
   when one is configured.

Transport safety, each item a real failure mode:

* credentials come from the environment only — never argv, a body or a row — and
  the configured URL is reduced to `scheme://host:port`, dropping userinfo, query
  and fragment, before it can reach a status row or a response;
* **redirects are refused.** A 3xx would replay the `Authorization` header and
  every byte of retained content at a host the operator did not name. This needs
  a `_StrictErrorProcessor`, because `urllib`'s `HTTPErrorProcessor` is itself a
  `HTTPRedirectHandler` subclass that follows 3xx in `http_response`, *before* any
  `http_error_30x` handler — replacing `HTTPRedirectHandler` alone looks like it
  works and does not;
* a response over 4 MiB is refused rather than truncated, and a body that is not
  JSON becomes `engine_protocol_error` with the body **not** quoted;
* **absence is proven, never inferred.** `get_document` used to return `None`
  for three different answers — a 404 whatever caused it, a 200 whose body was
  not an object (the collection endpoint's `{items: []}` is what a path
  resolved one level short returns), and a 200 with `null` — and `None` means
  "not there" to every caller, including the worker recording `DELETED`. Three
  separate requirements now stand between a404 and that conclusion:

  * **the read-back must be this document.** `DocumentResponse` requires `id`,
    and an id that is missing or that names a *different* document says nothing
    about the one asked for — yet it was accepted, and every caller reads a
    returned document as "the target is present";
  * **the route must be reachable.** `GET .../documents`, same prefix one level
    up, has to answer with an items array that does not list this document. A
    404 from it means the path itself does not exist here; a collection that
    *does* list the id means the two reads contradict each other;
  * **every page must be read, and every entry on it must be readable.**
    `limit` defaults to 100, so one page proves nothing — a document on page 2
    is present while a first-page-only check reports it gone.
    `ListDocumentsResponse` requires `items`, `total`, `limit` and `offset`,
    which is what makes full coverage *checkable* rather than hoped for: the
    loop advances until `offset >= total`, and refuses to conclude anything when
    the server ignores the requested offset, repeats an id across pages, omits
    `total`, stops before its stated total, or exceeds a hard page cap. Every one
    of those means "coverage not proved", which is an error and not an absence.
    An entry that cannot be read counts the same way: `null`, an object with no
    `id`, or a non-string `id` is refused rather than skipped, because skipping
    still advances `offset` — a server answering `items: [null]` would have every
    page "read", conclude the document is absent, and let the worker record
    `DELETED` over one that is still there.

  A non-object body and a missing `id` are `engine_protocol_error` as well.
  Together with the unit listing the worker already checks, a wrong endpoint,
  an unrecognised bank, a paginated bank, a junk-filled collection, or an answer
  about the wrong document can no longer be turned into a successful delete over
  a document that is still in the bank;

  **and a failed check keeps its own error.** The confirmation reads a paginated
  collection, so it can fail partway — a timeout on the second page is ordinary.
  `_document_is_absent` used to swallow every `TransportError` and return `False`,
  which made the caller re-raise the **original** 404; `engine_not_found` is
  permanent, so a retryable timeout became a terminal `FAILED` with no automatic
  retry and an operator as the only way forward. The probe's error is now
  re-raised, and it — not the 404 that started the exchange — decides whether the
  job retries.
* every failure becomes a stable `error_code` plus a redacted, clipped
  `safe_error_summary`. A remote error never becomes a Core 500 and is never
  mapped onto "Truth is unavailable".

Configuration: `JASMINE_HINDSIGHT_URL` (required to be an absolute http(s) URL),
`JASMINE_HINDSIGHT_API_KEY` (optional), `JASMINE_HINDSIGHT_TIMEOUT_SECONDS`
(default 20, malformed values fall back rather than crash), `JASMINE_HINDSIGHT_API_PREFIX`
(default `/v1/default`).

## 7. Jobs, durability and the crash windows

Design module §12 draws `commit → enqueue PENDING → worker retain → DONE`. The
interesting part is the window it does not draw.

**Commit-to-enqueue.** "Asynchronous" is not durability: a process that dies after
the State transaction commits and before the job row exists has lost the content
with no record of the loss. P4 makes the job a *projection of committed Events*
rather than a side effect of a write path. `enqueue_from_sources` walks the
immutable Event stream above a persisted cursor and creates a PENDING job for
every eligible source that has none; the job inserts and the cursor update commit
in the same short transaction, and the unique index makes a re-scan, a second
worker or a rewound cursor a no-op. No P4 code path enqueues from inside another
domain's transaction, and none is needed: the scan reads *committed* Events, so a
rolled-back write is invisible to it.

**Claim.** `BEGIN IMMEDIATE` covers one `SELECT` and one `UPDATE` and nothing
else. Two workers therefore cannot both claim a job, and the write lock is never
held across a network call. The claim carries a lease; an expired `RUNNING` job is
claimable again, so a worker killed mid-retain loses nothing.

Two further guards, both found in review and both about a claim that *stopped*
being yours:

* **One in-flight job per `memory_id`.** A `retain` and the `delete` an operator
  ordered later are two jobs for the same document and nothing ordered them
  relative to each other. Claimed concurrently, the delete removes the remote
  document and the slow retain — already past its `item is ACTIVE` check —
  writes it back, leaving a local `DELETED` tombstone whose remote copy no job
  will ever remove again, because the only job that would have removed it already
  succeeded. `claim_due` therefore only offers a `PENDING` job when no job for
  that `memory_id` is `RUNNING`. The expired-lease reclaim branch deliberately
  carries no such guard: a stranded `RUNNING` job must always be claimable, or a
  blocked sibling picked first would wedge it forever.
* **`status='RUNNING'` does not identify a claim.** An expired lease leaves the
  status exactly as it was, so worker A whose lease lapsed can still match
  `WHERE job_id=? AND status='RUNNING'` after worker B has claimed the same job —
  and write `FAILED` over B's in-flight retain or `DONE` over B's result, with
  the rowcount check passing because the row really is `RUNNING`. `_finish`,
  `fail` and `mark_document_deleted` match the claim's identity in the same
  transaction, and `_assert_claim_held` runs before the item write as well, so a
  stale worker cannot leave a changed engine state behind even when the job row
  update would later have been refused.

  The identity is **`claim_generation`, not `retry_count`**. The first version
  fenced on `lease_owner` *and* `retry_count`, which looked strict and was
  wrong in one specific way: `retry_count` is the *budget*, and `request_retry`
  zeroes it so an exhausted job gets another allowance. After a manual retry the
  same worker claims again with the same `retry_count` it had the first time, so
  its **older** claim passes the fence — and can commit the newer attempt's job
  and write its own older receipt over the newer work. `claim_generation` is
  bumped on every claim and never reset; it comes from the monotonic attempt
  number of that claim, which is also the number recorded under
  `memory_job_attempts.attempt_number`, so fencing and the attempt history name
  the same claim. The budget stays a budget, and the sequence stays a sequence —
  two questions, two answers, and neither may be asked of the other.

**Remote success before DONE.** The document id is deterministic
(`sha256(source_event_id)`, scoped by engine and type) and the engine upserts on
it, so the re-send replaces rather than duplicates. **P4 claims at-least-once
delivery with an idempotent sink, not end-to-end exactly-once**, which cannot be
proven against an LLM-backed extractor and is not claimed anywhere.

**The write that outlives the claim.** Fencing constrains the *database*: a
stale worker cannot touch the job row or the item. It cannot constrain an HTTP
request already on the wire. The sequence is real: A claims a `retain` and its
engine call is in flight when the lease expires; B reclaims the job, finds the
item tombstoned and fails it; the `delete` job then runs and completes against a
document that does not exist yet; and only afterwards does A's write land. Both
jobs are terminal, the item says `DELETED`, and the document sits in the bank
with nothing queued to remove it — a permanent orphan, surfacing only when the
next recall returns something an operator deleted.

A is the only participant that knows it just wrote, so A takes it back. After
`retain` returns, `_compensate_revoked_write` re-reads the item and, if it has
left `ACTIVE`, deletes the document (`DELETED`) or soft-retires its units
(`OBSOLETE`, which keeps the document by design). A receipt whose `success` is
falsy is a failed compensation, not a finished one — the undo must not be
believed on the same terms the delete path refuses to believe it.

**The obligation is a job row, because the participant that owes it cannot be
trusted to record it.** The first version of this took the write back inline and
then called `fail()` on the retain job. That is wrong twice over, and a single
transient timeout is enough to prove it: `fail()` matches `lease_owner` and
`retry_count`, so once the claim has been reclaimed it is refused outright and
nothing about the failed undo is recorded — local `DELETED`, document in the
bank, both jobs terminal, no retry. And even when `fail()` *is* accepted, the
retain job is the wrong owner: `_process_retain` returns early for an item that
is no longer `ACTIVE`, so the next claim would record `document_deleted_locally`
and go terminal without ever retrying the undo.

So the take-back is recorded where it can survive either outcome:

* `_ensure_engine_work` (a `delete` job for a tombstoned item, an `invalidate`
  job for an obsoleted one) is called **before** anything fencing can refuse,
  from both the failed-compensation path and the early-exit path;
* an existing job that is `PENDING` is left alone — it is queued and will run;
* a `RUNNING`, `DONE` or `FAILED` one is re-armed, in a single statement against
  every non-`PENDING` state so a job that flips between the read and the write is
  still caught. `RUNNING` is the case that matters, because *running* is not
  *current*: a delete can be mid-flight, its remote removal already done and
  `DONE` not yet committed, when a stale retain writes the document back.
  Returning it unchanged would let it commit against the older view — `DONE`,
  document present, nothing queued. Clearing `lease_owner` is what fences that
  commit; `_assert_claim_held` refuses it and the report says `claim_lost`
  rather than raising, because losing a lease is an expected outcome and not a
  defect in the delete path. Re-arming touches only status, lease and timing
  columns, none of which `trg_memory_jobs_identity_immutable` guards;
* a re-armed delete that finds the document already absent records success, but
  only after **proving** absence. A 404 means *a* resource was missing, not
  *which* one: a wrong endpoint, a bank this Core never derived, or a proxy
  rewriting the path all answer 404 while the document is still in the bank.
  `_document_is_absent` reads it back twice, on two paths — the unit listing
  answers `200 {items: []}` for an absent document when the endpoint and the
  bank are right, and the document read-back returns null only on a genuine
  miss — and any error or surviving unit keeps the failure. Status codes are
  not evidence about which resource they describe;
* `retry_count` and the attempt sequence are **two different numbers**, and the
  distinction is what makes both safe to do their own job. `retry_count` is the
  budget counter, and `request_retry` resets it to 0 so an exhausted job gets a
  fresh allowance — that reset was previously harmless-looking and wasn't: the
  claim derived `attempt_number` from it, so the reset made the next claim
  collide with `UNIQUE (job_id, attempt_number, phase)`, abort the claim and
  strand the job `PENDING` forever with a worker that could never take it.
  `attempt_number` now comes from `MAX(attempt_number) + 1` over
  `memory_job_attempts`, which only moves forward, while `retry_count` continues
  to count claims. Neither writer has to know which one it needs:
  `_attempt_number_for` picks the sequence and `fail()` picks the budget.

The residue this still does not close is narrower than before: a process killed
**between** the write and `_ensure_engine_work`. There is no transaction that
can cover a remote write and a local insert, so that gap is real. It needs an
operator-side read-back sweep and is named as not covered rather than assumed
away.

**Every field that decides coverage is checked, not just read.** `total` that is
negative satisfies the stopping rule `offset >= total` on page one and reports
the whole collection covered without reading a row; a `total` that *changes*
between pages means the pages do not describe one set; a page larger than its own
stated `limit`, a mismatched `offset` echo, or more rows than the `total` allows
each break the partition. The first three are schema violations; a changing total
is concurrency, and that is `engine_conflict` — retryable, re-read from the start
once the bank settles.

**One page is not the document, and a short page is not the last one.**
`list_document_memories` takes a `limit`, and obsoleting "every unit" against the
default page left every unit past it `valid` while the job recorded
`INVALIDATED`; the same truncation is how a document with units on a later page
gets called absent. The first fix still ended the read when a page came back
shorter than we asked — but a server is free to cap the page below the `limit`,
and *that* short page looks exactly like the last one, so the read stopped early
and reported work done on units it never loaded.

**The engine names its own address.** The documents collection echoes
`bank_id` as the *engine's* bank — the namespaced one — while Core's `bank_id`
stays canonical. Comparing the echo to the Core id refused every correct row the
moment a namespace was configured, so the read-back the absence proof depends on
answered `engine_protocol_error` for a bank that was in fact ours. It compares
`engine_bank_id(bank)`, the address the request was actually made to.

**An entry we cannot read is not an entry we may skip.** Completeness is not
only "every page was fetched" but "every row on it was understood": a caller that
filters the list for the entries it recognises turns a malformed, duplicated or
foreign entry into a silent omission and then records the job `DONE` having
handled the subset it happened to parse. Obsoleting "all units" would leave the
rest `valid` while the receipt said otherwise. So every entry must be an object
with a non-empty string `id`; duplicates are refused across pages as well as
within one, because the same unit arriving twice is what makes `total` a count of
rows rather than of units; a unit naming a `document_id` must name this one; and
a document naming a `bank_id` must name the bank the collection was queried
under. The document read-back is the only place that knows what was asked for, so
it is the only place that can refuse.

Completeness is therefore driven by `total`, never by page length, and both
collections share one validator (`validated_page`) checking `total`, the `offset`
asked for, the `limit` the server claims, and that the rows fit inside both —
because two implementations is how they drift, which is precisely how the
documents collection was hardened first while the unit listing still ended on a
short page. It lives in the transport: only the transport can read the paging
envelope. `list_all_document_memories` is the completeness call, the worker's
`_all_document_units` is a thin wrapper over it so no call site can quietly
revert to the single-page method, and the fake and the wire stub both paginate
for real — a double that always answered with every row could never catch a
reader that stopped after one page, because there would be no second page to
miss.

**A delete that provably failed is retryable.** When the read-back shows the
document is still there, the 404 that started the exchange did not mean "the
document is missing" — it meant the delete did not do what it said. Re-raising it
recorded `engine_not_found`, a *permanent* code, so a delete that failed
conclusively ended `FAILED` with no automatic retry. It is now
`engine_delete_failed`, retryable, because what went wrong is that the document
is *present*.

**The worker's report list is bounded.** `run()` collects a `WorkerReport` per
tick so a test and `jasmine-core memory drain` can assert on what happened — but
a service runs with `max_ticks=None` until it is stopped, and appending every
tick, including every `idle`, grows without limit for a value nobody can read
until the process exits. A bounded run's history is capped by `max_ticks`
already; an unbounded one keeps the most recent `_REPORT_HISTORY`, and the `log`
callback remains the durable record.

**Retry.** A retryable failure returns to `PENDING` with exponential backoff
(30 s, doubling, clamped to 3600 s, ±25% jitter, clamped *after* the jitter) and a
`next_attempt_at`; a permanent failure or an exhausted budget goes to `FAILED`.
`engine_not_configured` is **permanent**: retrying cannot configure anything, and
a silent retry loop against an endpoint the operator never set is worse than a
FAILED job an operator can see and requeue.

**Manual retry** is a human/system command Event, takes `expected_revision` and
an optional `event_id`, and may not change the document, the content hash or the
bank (immutable by trigger). `DONE` is refused; `RUNNING` is refused rather than
stolen from its worker. Repeating an `event_id` replays. The idempotency hash
covers the *command*, not the job's state at the time, so a re-sent identical
request is a replay rather than a spurious conflict.

**No task revision** changes anywhere in this lifecycle.

## 8. Lifecycle: obsolete, delete, pin

All three are authenticated, audited Core API operations with `expected_revision`
and an optional `event_id`; the Dashboard consumes them and is not a second
implementation.

* **obsolete** — logical and reversible. The item keeps its metadata, its
  provenance and every Raw Event and Evidence it points at, and stops appearing in
  Recall and in a Context Pack immediately. A queued `invalidate` job soft-retires
  the engine's memory units; a remote failure never un-obsoletes the item, and the
  Inspector reports the two halves separately.
* **delete** — a tombstone locally, a physical removal remotely. The item is
  excluded from Recall *immediately* and the row survives with its provenance; a
  queued `delete` job removes the remote document. `engine_state` reaches
  `DELETED` only on an engine receipt; a failure leaves `DELETE_FAILED` with the
  item still excluded. The Core source is untouched: the Rule, the State, the
  Evidence and the Raw Event all remain. A tombstone is terminal; re-retaining the
  same source is a new item.
* **pin** — Inspector ordering and nothing else. It cannot resurrect an obsolete
  or deleted item, cannot widen the budget, and cannot make an item load as
  Authority. `last_recalled_at`/`recall_count` change on every query and are
  deliberately *not* part of `revision`, so asking a question cannot invalidate an
  operator's `expected_revision`.

## 9. Deployment mode: what a reviewer must read

`jasmine-global` is global *within one trusted usage domain*. It is not shared
between users. `JASMINE_MEMORY_DEPLOYMENT_MODE` is recorded, not assumed:

* `single_user` (default) — one trusted operator owns the Core, so an
  authenticated actor may read every project in it. Cross-*project* isolation
  still holds absolutely: a recall for project B never returns a project A item,
  and A's jobs are never in B's list. This mode provides **no user-level ACL**,
  and no Gate may claim one from it. `P4-G04` in this mode proves scope isolation
  and bank-injection refusal only.
* `acl` — `memory_project_actors` is authoritative and an actor without a grant is
  refused as `project_not_found`. An unpopulated table refuses to serve at all
  (`503 memory_deployment_not_ready`), because a table that is empty because
  nobody was granted access and a table that is empty because it was never
  populated are indistinguishable at runtime, and the second must not behave like
  the first.

A UI filter, a `project_id` in a body, or an API scope is never treated as proof
of authorisation. A project the actor may not use is reported as **not found**,
not as forbidden, so the Inspector and the job list are not a project-enumeration
oracle.

## 10. API

The two design paths keep their exact spelling. No public retain endpoint and no
bank selector exist, so an agent cannot retain arbitrary text, choose
`jasmine-global`, or promote project content into it.

| Method | Path | Scope | Notes |
| --- | --- | --- | --- |
| GET | `/v1/memory/jobs` | `memory:read` | `project_id`, `status`, `kind`, `memory_type`, `limit`, `after_job_id`; returns `counts` |
| GET | `/v1/memory/jobs/{id}` | `memory:read` | job plus its full attempt history |
| POST | `/v1/memory/jobs/{id}/retry` | `memory:manage` + human/system | `host_id`, optional `event_id` |
| GET | `/v1/memory/items` | `memory:read` | Inspector list, labelled non-authoritative |
| GET | `/v1/memory/items/{id}` | `memory:read` | provenance, jobs, honest `engine_claim` |
| POST | `/v1/memory/items/{id}/obsolete` | `memory:manage` + human/system | `expected_revision`, `host_id`, optional `event_id`, `reason` |
| POST | `/v1/memory/items/{id}/delete` | `memory:manage` + human/system | as above; queues the remote removal |
| POST | `/v1/memory/items/{id}/pin`, `/unpin` | `memory:manage` + human/system | ordering only |
| GET | `/v1/memory/traces`, `/traces/{id}` | `memory:read` | query hash and length, never the query |
| GET | `/v1/memory/health` | `memory:read` | no probe; `core_impact` states what Memory costs the Core |
| POST | `/v1/memory/reflect` | `memory:reflect` + human/system | explicit historical synthesis only |

`memory:read` is available to an agent key: the Inspector is history.
`memory:manage` and `memory:reflect` are human/system only, refused at key
issuance, because an agent must not curate what it will be shown and must not be
the thing that decides a history question is worth an LLM call.

**Engine free text is withheld from an Agent key on every path.**
`safe_error_summary` is redacted and clipped, but it is still text the engine
produced about Core's own request, so an Agent key does not get it. Withholding
it on the job *list* while the job detail, the item list and the item detail
returned it is not a policy anyone can rely on: the same job appears at the top
level of `GET /jobs/{id}`, inside `attempts` there, and inside `jobs` on an item
detail. Responses therefore pass through one walker (`_respond`) at each return,
so no nesting can be forgotten — and that includes the trace endpoints, whose
rows carry `safe_error_summary` beside `error_code` and which had been the one
path still building a `Response` by hand.

**A row never carries a raw JSON twin of a field it also carries parsed.** The
walker redacts *fields*; it cannot see into an opaque string. `traces()` added
the parsed `banks` list while leaving `banks_json` in the same row, so every
trace in the list still contained the bank outcomes — engine text included — as
one string the walker had no reason to look at. `trace()` pops the raw column and
always did; `traces()` now does too, `attempts()` pops `receipt_json` for the
same reason, and the rule is that a caller receives the parsed form or the raw
one and never both, on every row-returning path. `error_code` beside it is a stable enumerable
value and is deliberately *not* withheld — an Agent needs to know a retain
failed, just not what the engine said about it.

New error: `503 memory_deployment_not_ready`.

`GET /v1/health` says nothing about Memory. A dead Hindsight must not make the
Core report itself dead (design §14), and P4 now *asserts* that.

## 11. CLI

`jasmine-core memory scan | reset-cursor | jobs | attempts | health` and
`jasmine-core memory-worker`. The worker is a separate service in the design's
topology (design §13) and is the only process that writes to the engine. All of
these call the same service layer the API uses; none writes a job or a Memory row
directly.

## 12. Shared files, and what the integration owner owns

P4 changed these files outside `src/jasmine_core/memory/` and
`src/jasmine_core/migrations/m0009_memory.py`. They are all resolved **in this
branch** as of the rebase onto `850fbec`; none was applied by pushing to
`main`, and the integration owner still has to take them in the merge.

| File | Change | Action needed |
| --- | --- | --- |
| `__init__.py` | `SCHEMA_VERSION` 8 → **9** (`main` is at 8) | renumber with the migration |
| `migrations/m0009_memory.py` | new | **renumbered three times**: cut as `m0007_memory`, then `m0008_memory`, then `m0009_memory` as P2 claimed `m0007_interpretations` and `m0008_resolutions`. Ships at `VERSION = 9`, `SCHEMA_VERSION = 9`. **P3 PR #11 also claims migration 9** (`m0009_continuity`), so whoever merges second renumbers. P4 cannot pre-empt by moving to `m0011`: `discover()` requires versions contiguous from 1, and with 9 and 10 absent `m0011` fails at import with `MigrationConflict: migration versions must be contiguous from 1: position 9 holds version 11`. The move to `m0011`/`SCHEMA_VERSION = 11` happens in the rebase onto P3, which P4 needs anyway for its Context Pack. |
| `ids.py` | five prefixes | register |
| `models.py` | five `EVENT_TYPES` | register |
| `auth.py` | three scopes + two actor-kind refusals | register |
| `errors.py` | `MemoryDeploymentNotReady` | register |
| `api/handlers.py` | one import, one line: `+ memory_api.routes(Route)` | apply as a patch |
| `api/dispatch.py` | a 500 with code `internal_error` no longer echoes its message | **review separately**: this is a pre-existing ADR 0003 gap that P4 surfaced, and it changes P0 behaviour |
| `cli.py` | `memory` and `memory-worker` | apply as a patch |

### The Memory worker P3 spawns

P3 owns the `jasmine.memory-slot.v1` contract — `context/memory_provider.py`
starts an operator-configured `argv` as a subprocess, writes one JSON query to
its stdin and reads one JSON result from its stdout — and P4 owns the process:
`scripts/p4-memory-worker.py`. P3's own tests use inline one-liners as the worker
and ship only a failing one, so there is nothing else to point `argv` at.

What the contract costs, each learned from P3's validator rather than assumed:

* the result has **exactly** seven keys and each bank exactly five; P3 rejects
  unknown ones, so this is not "at least";
* the banks are named by **Core's** canonical ids, because that is what the
  contract compares; the engine's namespaced address is a wire detail and never
  appears in an answer;
* the `banks` are echoed from the request rather than derived, because P3
  compares the declared set to the requested one and degrades the whole answer
  on a mismatch;
* identity is echoed from `JASMINE_MEMORY_PROVIDER_ID` /
  `JASMINE_MEMORY_CONFIG_DIGEST`, so a worker cannot answer for another
  deployment;
* at most 8 items and 64 KiB, and an answer that would not fit is degraded with
  a code rather than truncated;
* nothing exits non-zero except an unreadable request or a missing identity. A
  degraded answer is information the caller can render; a dead process is a
  hole. Core's own refusals (`CoreError`) come back as `degraded` with their
  code for the same reason.

**The worker does not forward an engine hit, it re-states it.** P3's
`filter_memory` re-derives every claim from the local database: only
`VERIFIED_OUTCOME` and `FAILURE_LESSON` survive, the item's `fact` must name a
real Evidence row, `fact.subject_event_id` must be that row's `change_event_id`,
`fact.value` must equal `evidence[fact.key]`, and **both** of the Evidence's own
events must appear in `source_event_ids`. So the worker reads the Evidence row
itself and cites that row's two events — which is what the memory is about, and
what makes P3's re-check pass. This is why an item with no Evidence behind it is
not sent at all: a failure nobody recorded a result for is not a lesson P3 will
accept, and it should not be.

**The environment is the whole configuration interface.** P3 hands the child
five fixed values, the two identity variables and whatever the operator
configured — allowlisted to `PYTHONPATH`/`LANG`/`LC_ALL`. The worker needs
`JASMINE_CORE_DB` and `JASMINE_MEMORY_ACTOR_ID` to answer at all, and
`JASMINE_HINDSIGHT_URL`/`JASMINE_HINDSIGHT_API_KEY` for an engine, so **those
names have to be on that allowlist**; until they are, every answer is `degraded`
however healthy the deployment is. Environment is the right channel rather than a
config file: P3 folds the values into `config_digest`, so a change moves the
pinned deployment identity instead of quietly changing behaviour under it. The
worker declares the names (`REQUIRED_ENV`, `ENGINE_ENV`) so the allowlist can be
extended to exactly them, and both halves are verified against P3's own provider:
uncoordinated it returns `degraded / memory_invalid_or_unavailable / 0 items`,
and with the allowlist extended the same run returns `ok` with the
Evidence-backed lesson.

**Identity comes from the deployment, and only from there.** Every recall writes
a trace naming who asked, so the actor is `JASMINE_MEMORY_ACTOR_ID` — part of the
pinned deployment digest — and a query carrying an `actor_id` is **refused** with
`memory_actor_not_trusted` rather than obeyed. The query is built from an HTTP
request body upstream, so anything in it is caller-influenced, and a worker that
honoured it would let a caller name an actor with a grant it does not have, with
the trace recording the lie. A missing variable is `memory_actor_not_supplied`
and an unknown actor is `memory_actor_unknown`; none of the three invents an
identity.

**The deployment's authorisation mode is passed through.** `MemoryAdapter`
defaults to `single_user`, so a worker that does not hand it
`scope.deployment_mode()` answers with every project in the database while an
`acl` deployment believes it is enforcing grants. In `acl` mode an actor with no
row in `memory_project_actors` reads nothing, which is what the mode is for.

**Still open:** `VERIFIED_OUTCOME` needs P1's `verified_transition` proof, which
lives in P3's branch; sending one without it produces an item P3 filters as
`unverified_outcome`, so only `FAILURE_LESSON` is emitted today.

Worker claim/retain/completion is *not* an Event: those rows live in the
append-only `memory_job_attempts`, because a background worker holds a lease owner
rather than a registered device actor, and a job row is not Truth another
projection must agree with. Human/system lifecycle commands *are* Events, because
they are authorised actions on Memory that a reviewer must be able to read.

## 13. What is not claimed

* No `P4-G01`…`P4-G07` verdict. Every one needs a real Hindsight process, a real
  extractor, a real Agent session, a real P3 Context Pack and a real SessionStart
  injection. This repository has none of them.
* No end-to-end exactly-once (§7).
* No user-level ACL in `single_user` mode (§9).
* No statistical memory benefit. One demonstrated recall that changed an agent's
  actions is a demonstration, not a measurement.
* No `VERIFIED_OUTCOME` without a `PASS` Evidence whose artifact still verifies,
  and no Memory at all from a P2 candidate until P2 registers its source.

Test evidence is two-layered and deliberately labelled: `FakeTransport` exercises
Core's own decisions, and `StubHindsightServer` drives the *real* transport over a
real socket against Hindsight 0.10.2's paths, methods and shapes — which is how the
`/version`-is-not-under-the-prefix and the redirect-credential bugs were found.
Neither is Gate evidence.

**How a Gate verdict is earned.** `scripts/p4-memory-gate.py` checks preconditions
first and reports `BLOCKED` naming the exact missing item, and a case that is
recorded as `PASS` is one whose function *returned*. Three of the seven could
previously return having produced only part of what their taskbook entry
mandates, so two shapes are now the only ones allowed, and a test asserts it on
the parsed source:

* **G05 and G07 end in `raise Blocked(...)`.** Both prove something about an
  actual Context Pack — G05 that it is marked degraded with an exact pack
  containing no Memory, G07 that a recalled memory reads non-authoritative and an
  obsolete one is not injected — and both therefore join `NEEDS_P3_PACK`, so they
  do not even start without P3. The terminal `Blocked` names what still
  separates a complete run from a partial one (Context pack, real Tool Evidence,
  real session, management audit) so a future operator knows exactly what to
  supply rather than finding a green verdict that means less than it appears to.
* **`PASS` is a statement about a stage, so only a full clean run may say it.**
  It used to mean "every case I happened to select passed", which let
  `--case P4-G06` produce a stage PASS with six cases never looked at, and let
  `--allow-dirty` — the flag that exists precisely so a development run cannot be
  mistaken for the frozen candidate — do the same. The manifest now carries
  `scope` (what was selected, what was not run) and `stage_acceptance` (whether
  this run could mean acceptance at all, and why not), and the verdict is `PASS`
  only when a full case set and a clean checkout are both in play; otherwise the
  run is `BLOCKED`, which is what a partial evaluation is.
* **The Gate's own numbers come from complete reads.** Its assertions are
  counts — "exactly one document", "every unit is `invalidated`" — and a count
  taken from one page is a count of one page: G07 would report every unit retired
  from the first 200 while the rest were still `valid`, and `retained_item`
  reported "nothing was retained" whenever the item it wanted sat past the
  200th. `engine_units` reads the document to completion and `all_items` keys
  past the page boundary.
* **G06 returns, and earns it.** Its two windows are crossed by real OS
  processes: one commits the Truth and dies before ever scanning, another runs
  the *real* `MemoryWorker` whose transport wrapper calls `os._exit(7)` after
  `transport.retain` has returned and before `jobs.complete` — so the process is
  killed inside the retain window with the remote write done and `DONE`
  unwritten — and a third process claims the expired lease and finishes. A
  reconnecting in-process connection would have proved nothing: the state a crash
  discards only ever lived in the dead process. The exit code, the held lease,
  the single `RETAINED` attempt, the remote read-back and the untouched Rule are
  all asserted on the parent's side.
