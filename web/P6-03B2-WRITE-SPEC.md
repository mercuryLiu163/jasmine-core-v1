# P6-03b2 — write payload, permission and acceptance specification

**Status: SPECIFICATION ONLY. Nothing here is implemented, and nothing here may be implemented
until §4 has been met.** This document freezes what P4 offers and what would have to be true before
a dashboard may offer it. It is the step the operator asked for before any b2 work starts.

**Dependency: P4 is not merged.** The specification is derived from the pinned commit
`0c4f8f0e2118711ac9aa2a7006fddc6982962248` (schema 11) by `scripts/p6/p4_write_spec.py` and frozen
in `contracts/p4-memory-write.v1.json`. **P4 phase acceptance has not passed**: none of this has
been exercised against a real P4 Core.

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

### Field constraints, from P4's validators

| Field | Rule |
| --- | --- |
| `expected_revision` | present, an `int`, `>= 1`; absent → `MissingExpectedRevision`; stale → `RevisionConflict` |
| `host_id` | a `hst_` id |
| `event_id` | an `evt_` id when present; **this is the idempotency key** |
| `reason` | text, at most 500 characters |
| `project_id` (reflect) | a `prj_` id, and the actor must be permitted for it |
| `query` (reflect) | non-empty text, at most 4000 characters |
| `max_tokens` (reflect) | an integer when present |
| unknown fields | any field outside the allow-list is refused, naming them |

## 2. Permissions

Two scopes, split deliberately so that reflect can be withheld independently of lifecycle
management:

| Scope | Covers | Who may hold it |
| --- | --- | --- |
| `memory:manage` | retry, obsolete, delete, pin, unpin | a **human or system** actor only — `ForbiddenActorKind` otherwise |
| `memory:reflect` | reflect | key issuance will not grant it to an agent; the adapter also refuses unless the request is explicitly a user's |

An agent key can therefore read Memory (P6-03b1) and cannot change it. That is P4's decision, made
deliberately, and the UI must not work around it: **a button that Core would refuse is not a
feature, it is a lie about what the operator can do.**

## 3. Semantics that constrain any implementation

Recorded from the pinned commit; each names its source. They are not auto-derived.

* **Retry never changes what is retained.** `document_id`, `content_sha256` and `bank_id` are
  immutable by trigger, and a `DONE` job is refused rather than retained twice — a second retain
  would silently replace the document. A `RUNNING` job is refused rather than stolen from a worker
  that still holds a lease.
* **Both retry and the lifecycle commands are idempotent on `event_id`.** A repeat returns the
  original command Event and writes no second transition. A UI must surface a replay as *the same
  command*, not as a second one.
* **A lifecycle command is guarded by `expected_revision`.** The item's state is checked before its
  revision, so "you cannot obsolete a tombstone" is answered as such rather than as a stale
  revision, and an illegal command appends no Event at all.
* **Reflect is the only path to the engine's generator**, is reachable only on an explicit user
  request, and is unreachable from a Context build. A complicated ordinary question belongs to
  recall.
* **Memory is never Authority.** No write here changes Rule or State, and every lifecycle response
  says `non_authoritative: true`.
* **Engine free text is withheld per actor**, inside P4's `_respond`, so it applies to write
  responses as well as reads.
* **Nothing here is built.** There is no b2 route in the dashboard, no b2 path in the proxy
  allowlist, and no b2 endpoint in the client. Tests assert all three.

## 4. Acceptance criteria, before any of it ships

Each is falsifiable, and each exists because one of them was skipped somewhere in this project's
history:

1. **A frozen payload per action.** Every write has a recorded request body in
   `contracts/p4-memory-write.v1.json`'s terms, and a test asserts the client sends exactly it —
   no extra key, no missing required key.
2. **A permission Gate.** The action is exercised with a token that has the scope and with one that
   does not, and with a non-human actor; the refusal is Core's own (`forbidden_scope`,
   `ForbiddenActorKind`), never a UI-enforced pretence.
3. **Audit readback per action.** After each write, the command Event is read back and the object's
   own row is re-read after a forced reload — not a success banner. The `replayed` projection can
   be refreshed; the audit event must be the first thing checked.
4. **Revision conflict is exercised.** A stale `expected_revision` must produce `409` and a UI that
   shows the current value beside the operator's draft, with no automatic retry of the original
   intent.
5. **A replay is shown as a replay.** Repeating the same `event_id` must render as the same
   command, and must not double-count or double-report.
6. **Retry's invariants are checked, not assumed.** `DONE` refused, `RUNNING` refused, and the
   retained document unchanged after a retry — the last one by comparing the content digest before
   and after.
7. **Reflect requires an explicit user request** in the UI's own terms, and its output is marked
   non-authoritative from Core's field.
8. **No `before`/`after` assumption.** The client handles both lifecycle response shapes, tested
   against each, because P4 sends both.
9. **Status.** Until all of the above pass against a real P4 Core, the write surface does not
   appear in the product, and nothing may describe Memory as writable.

## 5. Non-goals

* No write is implemented by this document, and P4's own acceptance is not established by it.
* No retain, no bank selection, no direct engine access, no credential in the browser.
* No write may change Rule or State, and Memory is never presented as Truth.
* No b2 route, path or control enters the product before criterion 9 is met.

## 6. What is frozen, and how it is checked

| Artefact | What it pins |
| --- | --- |
| `contracts/p4-memory-write.v1.json` | the six routes, scopes, allowed and required body fields, id prefixes, both lifecycle response shapes, the reflect response, and the recorded semantics — all derived from the pinned SHA |
| `tests/p6/test_p4_write_spec.py` | the snapshot still matches the pinned commit; the six writes are exactly the ones the read slice excluded; none of them is reachable in the proxy today; and no write exists in the client |
| this document | the acceptance criteria above, which are what a future b2 Gate must satisfy |

`scripts/p6/p4_write_spec.py --check` exits **2** where the pinned commit is absent, rather than
passing because it could not look — the same rule the read contract follows.
