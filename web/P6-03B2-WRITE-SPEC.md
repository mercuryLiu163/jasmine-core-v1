# P6-03b2 — write payload, permission and acceptance specification

**Status: IMPLEMENTED, behind a default-off switch, and NOT verified against a real P4 Core.**
The six writes are built in `web/` and exercised end to end against a **simulated** P4 fixture.
P4 is not merged, **P4 phase acceptance has not passed**, and this document is not evidence that
it has. Implementation and validation state, per criterion, is tracked in
[`P6-03B2-COMPLIANCE.md`](P6-03B2-COMPLIANCE.md) — this file is the specification that state is
measured against.

**Dependency: P4 is not merged.** The specification is derived from the pinned commit
`0c4f8f0e2118711ac9aa2a7006fddc6982962248` (schema 11) by `scripts/p6/p4_write_spec.py` and
frozen in `contracts/p4-memory-write.v1.json`. Nothing in this slice has been served by real P4
code.

Why a frozen specification at all: P6-02's audited writes needed a permission-and-audit Gate that
was discovered late, and P6-03b1's contract needed a scope defect found by review. Both are cheaper
to settle on paper than in code.

## 1. The six writes

Derived from P4's own route table and body validators. `*` marks a field the code refuses to
proceed without — for the lifecycle commands that includes `host_id`, which must be a `hst_` id;
for retry neither body field is required, since `request_retry` never validates them.

| Method and path | Scope | Body fields | Response keys |
| --- | --- | --- | --- |
| `POST /v1/memory/jobs/{mjb}/retry` | `memory:manage` | `host_id`, `event_id` | 3: `job`, `event`, `replayed` |
| `POST /v1/memory/items/{mem}/obsolete` | `memory:manage` | `expected_revision`\*, `host_id`\*, `event_id`, `reason` | **7 or 5** — see below |
| `POST /v1/memory/items/{mem}/delete` | `memory:manage` | same | **7 or 5** |
| `POST /v1/memory/items/{mem}/pin` | `memory:manage` | same | **7 or 5** |
| `POST /v1/memory/items/{mem}/unpin` | `memory:manage` | same | **7 or 5** |
| `POST /v1/memory/reflect` | `memory:reflect` | `project_id`\*, `query`\*, `task_id`, `max_tokens`, `host_id`, `event_id` | 8 |

**A lifecycle command has two response shapes, and a client that assumes one will break on the
other.** A fresh transition returns `memory`, `event`, `replayed`, `job`, `before`, `after` plus
`non_authoritative` (7 keys). A replay of the same `event_id` returns the same minus `before` and
`after` (5 keys). The read contract learned this the hard way: one shape recorded for two real
shapes was wrong in both directions.

Response key sets are in the frozen snapshot, per write, as `response_shapes`.

`reflect` has **no command Event** among its eight keys, so its audit record is the
`memory_query_traces` row it writes; the UI links that trace instead of showing an empty "command
Event" row.

### Field constraints, from P4's validators

| Field | Rule |
| --- | --- |
| `expected_revision` | present, an `int`, `>= 1`; absent → `missing_expected_revision` (400); stale → `revision_conflict` (409) |
| `host_id` | a `hst_` id |
| `event_id` | an `evt_` id when present; **this is the idempotency key** |
| `reason` | text, at most 500 characters; allowed but not required on lifecycle commands, and not accepted at all on retry or reflect |
| `project_id` (reflect) | a `prj_` id, and the actor must be permitted for it |
| `query` (reflect) | non-empty text, at most 4000 characters |
| `max_tokens` (reflect) | an integer when present |
| unknown fields | any field outside the allow-list is refused, naming them |

> **A correction, recorded rather than quietly made.** The first version of this section named the
> Python exception *classes* (`ForbiddenActorKind`, `MissingExpectedRevision`, `RevisionConflict`)
> where Core puts a **snake_case wire code** on the response, and said `422` where
> `errors.MissingExpectedRevision` carries `status = 400`. Both were wrong, and review found them
> by reading `src/jasmine_core/errors.py` at the pinned commit. The wire codes are
> `forbidden_actor_kind` (403), `missing_expected_revision` (400), `revision_conflict` (409),
> `invalid_state_transition` (409) and `event_id_conflict` (409); the class names above appear only
> where a Python class is meant. The fixture and the panels were corrected with it.

The client cannot emit an unlisted field: every body is built by `web/src/api/memoryWrites.ts`,
which throws on a key the frozen contract does not carry for that action.

## 2. Permissions

Two scopes, split deliberately so that reflect can be withheld independently of lifecycle
management:

| Scope | Covers | Who may hold it |
| --- | --- | --- |
| `memory:manage` | retry, obsolete, delete, pin, unpin | a **human or system** actor only — Core answers `forbidden_actor_kind` (403) otherwise |
| `memory:reflect` | reflect | key issuance will not grant it to an agent; the adapter also refuses unless the request is explicitly a user's |

An agent key can therefore read Memory (P6-03b1) and cannot change it. That is P4's decision, made
deliberately, and the UI must not work around it: **a button that Core would refuse is not a
feature, it is a lie about what the operator can do.** The panels never decide a permission: every
submit goes to Core and the refusal is rendered from Core's own code.

Reachability is a *second*, independent decision from permission. The six paths are not in the
dashboard's live allowlist; they are in the gated `p4_write_paths` list, forwarded only when the
proxy is started with `P6_ENABLE_P4_WRITES=1` — a switch separate from the reads' switch, because
reading Memory and changing it are different decisions. With the switch off the proxy refuses by
name (`proxy_path_not_allowed`) and the panel shows the missing write dependency, naming the
switch; that refusal is *not* shown as a scope problem, because the request never reached Core.

## 3. Semantics that constrain any implementation

Recorded from the pinned commit; each names its source. They are not auto-derived.

* **Retry never changes what is retained.** `document_id`, `content_sha256` and `bank_id` are
  immutable by trigger, and a `DONE` job is refused rather than retained twice — a second retain
  would silently replace the document. A `RUNNING` job is refused rather than stolen from a worker
  that still holds a lease, and a `PENDING` job is refused because it is already queued: accepting
  it would append an audit Event for a transition that did not happen. All three answer
  `invalid_state_transition` (409), and only a `FAILED` job is retried, with `retry_count`
  **reset to 0** rather than incremented.
* **Both retry and the lifecycle commands are idempotent on `event_id`.** A repeat returns the
  original command Event and writes no second transition. A UI must surface a replay as *the same
  command*, not as a second one.
* **A lifecycle command is guarded by `expected_revision`.** The item's state is checked before its
  revision, so "you cannot obsolete a tombstone" is answered as `invalid_state_transition` rather
  than as a stale revision, and an illegal command appends no Event at all.
* **Reflect is the only path to the engine's generator**, is reachable only on an explicit user
  request, and is unreachable from a Context build. A complicated ordinary question belongs to
  recall.
* **Memory is never Authority.** No write here changes Rule or State, and every lifecycle response
  says `non_authoritative: true`.
* **Engine free text is withheld per actor**, inside P4's `_respond`, so it applies to write
  responses as well as reads. On this surface the rule is observable on a write reply through
  `reflect`; the five `memory:manage` writes are human-or-system only, so an agent never gets a
  response to withhold from.

## 4. Acceptance criteria, before any of it ships

Each is falsifiable, and each exists because one of them was skipped somewhere in this project's
history:

1. **A frozen payload per action.** Every write has a recorded request body in
   `contracts/p4-memory-write.v1.json`'s terms, and a test asserts the client sends exactly it —
   no extra key, no missing required key.
2. **A permission Gate.** The action is exercised with a token that has the scope and with one that
   does not, and with a non-human actor; the refusal is Core's own (`forbidden_scope`,
   `forbidden_actor_kind`), never a UI-enforced pretence.
3. **Audit readback per action.** After each write, the command Event is read back and the object's
   own row is re-read after a forced reload — not a success banner. The `replayed` projection can
   be refreshed; the audit event must be the first thing checked. For `reflect`, whose contract has no
   command Event, the audit record is the trace.
4. **Revision conflict is exercised.** A stale `expected_revision` must produce `409` and a UI that
   shows the current value beside the operator's draft, with no automatic retry of the original
   intent.
5. **A replay is shown as a replay.** Repeating the same `event_id` must render as the same
   command, and must not double-count or double-report.
6. **Retry's invariants are checked, not assumed.** `DONE`, `RUNNING` and `PENDING` refused, and
   the retained document unchanged after a retry — the last one by comparing the content digest
   before and after.
7. **Reflect requires an explicit user request** in the UI's own terms, and its output is marked
   non-authoritative from Core's field.
8. **No `before`/`after` assumption.** The client handles both lifecycle response shapes, tested
   against each, because P4 sends both.
9. **Status.** Until all of the above pass against a real P4 Core, the write surface does not
   appear in the product, and nothing may describe Memory as writable.

### Criterion 9, stated honestly rather than reinterpreted away

Criterion 9 has two halves and they are no longer in the same state.

* **9a — not reachable by default.** Met, and checked: the six paths are absent from the live
  allowlist and from the always-on milestone B write list; they sit in the gated
  `p4_write_paths` list behind `P6_ENABLE_P4_WRITES` (default off); Memory has no navigation entry
  and the write routes are deep links; and with the switch off the proxy refuses every one of them.
* **9b — verified against a real P4 Core.** **Not met.** All six are exercised only against the
  simulated fixture `scripts/p6/p4_write_fixture.py`, which stamps `X-P6-Fixture:
  simulated-p4-writes` on every reply. No real P4 code has ever served one of these writes.

The operator authorised building the surface under this narrowing — default-off, isolated,
simulation-only verification — and keeping real-P4 verification as a separate outstanding item.
So 9a is claimed and 9b is not, and nothing in this slice may be described as "P4 verified",
"P4 accepted" or "writable in production".

## 5. Non-goals

* No retain, no bank selection, no direct engine access, no credential in the browser.
* No write may change Rule or State, and Memory is never presented as Truth.
* No write is verified against real P4 by anything in this slice.
* No navigation entry, no default-on path and no admin surface for any of the six writes.

## 6. What is frozen, and how it is checked

| Artefact | What it pins |
| --- | --- |
| `contracts/p4-memory-write.v1.json` | the six routes, scopes, allowed and required body fields, id prefixes, both lifecycle response shapes, the reflect response, and the recorded semantics — all derived from the pinned SHA |
| `tests/p6/test_p4_write_spec.py` | the snapshot still matches the pinned commit; the six writes are exactly the ones the read slice excluded; they are reachable only through the gated list and are in neither the live nor the milestone B list; and the client issues exactly the six |
| `web/tests/memoryWrites.test.ts` | the payload builder matches the frozen field lists, throws on an unlisted field, and never emits `idempotency_key` |
| `web/tests/memoryWriteViews.test.tsx` | the panels: body sent, Core's refusals shown, conflict kept, replay rendered, readback performed, reflect explicit and marked |
| the P6-03b2 Gate (`scripts/p6/browser_trace.mjs --p4-writes` / `--p4-writes-off`) | the same properties in a real browser, against the simulated fixture, with response bodies recorded |
| this document | the acceptance criteria above, and the 9a/9b split |

`scripts/p6/p4_write_spec.py --check` exits **2** where the pinned commit is absent, rather than
passing because it could not look — the same rule the read contract follows.
