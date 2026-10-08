# P6-03b — scope, dependency check, and acceptance criteria

**Status: NOT IMPLEMENTED. Nothing in this document has been built.** It is the task
breakdown and dependency check the operator asked for *before* implementation, so that a
capability whose backend is not merged cannot be presented as available.

P6-03b is the **P4 (Memory)** inspector in the P6-03 family:

| | Slice | Owner | This document |
| --- | --- | --- | --- |
| P6-03a | Checkpoint / Context Pack / Resume | P3 | delivered, PARTIAL / DEPENDENCY_PENDING |
| **P6-03b** | **Memory Jobs / Recall / Inspector** | **P4** | **planned only — this file** |
| P6-03c | Validation inspector | P7-01 | not started |

## 1. Scope, as the taskbook defines it

The P6 taskbook (`docs/Jasmine-Core-V1-P6-阶段任务书.md` §6) splits P6-03 by domain. The two
P6-03b rows are the Memory Jobs/Recall view and the Memory Inspector management view:

| View | Frozen prerequisite and display | Stated failure modes / non-goals |
| --- | --- | --- |
| Memory Jobs/Recall | P4 jobs/detail/retry, bank mapping, source Event/Evidence, query/result trace/degraded, management permission | Memory is NON_AUTHORITATIVE; cannot change Rule/State; Reflect only on explicit user request; retry must not duplicate retain; Hindsight credentials must not enter the browser |
| Memory Inspector management | P4's explicit obsolete/delete/pin Core API, authorisation/audit, soft-delete / retained provenance / idempotency policy | With no interface, read-only with actions disabled; must not connect to the engine directly or bypass the secret filter; historical Memory conflicting with current Authority is displayed prominently |

The taskbook also fixes the boundary: the P4 line owns the Memory adapter, jobs, recall and
management API; the UI must not talk to Hindsight directly, must not choose a bank, and must
not write Memory into Truth. And, generally for P6-03: **"不能将未接入部分写成已通过 P6"** — an
unconnected part must not be written up as passing P6.

## 2. Dependency check — the decisive part

**The baseline this dashboard is served against has no Memory API at all.**

| Check | Result |
| --- | --- |
| Baseline `850fbec` (schema 8) memory routes | **0** |
| Baseline `src/jasmine_core/memory/` | does not exist |
| P4 source of truth | branch `p4-integration` @ `0c4f8f0e2118711ac9aa2a7006fddc6982962248` (2026-10-06), schema **11**, 128 commits ahead of the baseline |
| Is `p4-integration` merged into `main`? | **No** |
| Other P4 refs | `p4-memory` @ `b88efc5` (an **ancestor** of `p4-integration`); `p4-memory-checkpoint` @ `a0e8539` (diverges — **not** an ancestor) |
| Route surface across the three P4 refs | all three declare the same 13 routes; the shapes and semantics still need pinning to one ref |

So P6-03b is in the *same class* as P6-03a: a real contract exists on an unmerged branch, the
baseline cannot answer it, and the honest delivery is a dependency-marked scaffold. The
difference worth noting is that P4 is a **separate line** from P3 — P6-03b does not become
available just because P3 lands.

### The P4 read surface, from `src/jasmine_core/memory/api.py`

Thirteen routes, all under `/v1/memory/*`. Seven are reads and belong to a read-only slice:

| # | Method and path | Handler | Scope | Response |
| --- | --- | --- | --- | --- |
| 1 | `GET /v1/memory/health` | `memory_health` | `memory:read` | `{**health, non_authoritative: true}` |
| 2 | `GET /v1/memory/jobs` | `list_jobs` | `memory:read` | `{jobs, count, counts}` — query `project_id`, `status`, `kind`, `memory_type`, `limit`, `after_job_id` |
| 3 | `GET /v1/memory/jobs/{mjb}` | `get_job` | `memory:read` | `{job, attempts}` |
| 4 | `GET /v1/memory/items` | `list_items` | `memory:read` | `{items, count, non_authoritative: true}` — query `project_id`, `bank_id` |
| 5 | `GET /v1/memory/items/{mem}` | `get_item` | `memory:read` | `{**detail, non_authoritative: true}` |
| 6 | `GET /v1/memory/traces` | `list_traces` | `memory:read` | `{traces, count}` |
| 7 | `GET /v1/memory/traces/{mtr}` | `get_trace` | `memory:read` | `{trace}` |

The other six are writes or generator actions and are **out of the read slice**:

| Method and path | Handler | Scope |
| --- | --- | --- |
| `POST /v1/memory/jobs/{mjb}/retry` | `retry_job` | `memory:manage` |
| `POST /v1/memory/items/{mem}/obsolete` | `_lifecycle_command` | `memory:manage` |
| `POST /v1/memory/items/{mem}/delete` | `_lifecycle_command` | `memory:manage` |
| `POST /v1/memory/items/{mem}/pin` | `_lifecycle_command` | `memory:manage` |
| `POST /v1/memory/items/{mem}/unpin` | `_lifecycle_command` | `memory:manage` |
| `POST /v1/memory/reflect` | `reflect` | `memory:reflect` |

Id prefixes: `mjb` (job), `mem` (item), `mtr` (trace).

### Semantics the UI must not contradict

These come from P4's own module docstring and handler bodies, and each one is a testable
constraint rather than a style preference:

* **`non_authoritative: true` is emitted by Core itself** on item, item-detail and health
  payloads. The inspector must surface it; Memory is not Authority and must never be
  rendered as if it were.
* **There is no public retain endpoint and no public bank selector.** Content reaches the
  engine only through a worker acting on a job derived from committed truth, and the bank is
  derived from the item's own scope. A UI affordance to retain text or choose a bank would be
  inventing an API that deliberately does not exist.
* **Reflect is human/system only, behind `memory:reflect`**, is the only path that runs the
  engine's generator, and is unreachable from a Context build. The taskbook adds: only on an
  explicit user request.
* **Project authorisation comes from the authenticated actor**, never a body field or query
  parameter, and an unauthorised project is reported as `not_found` — so the inspector cannot
  be used to enumerate another project's work. The UI must render that as *not found*, and in
  particular must not turn it into an error banner or a retry prompt.
* **An actor with no grants gets an empty list, not the whole database**, and agent keys get
  job payloads redacted because error summaries can carry engine text. Empty and redacted are
  both normal states, not failures.
* **Hindsight credentials never enter the browser.** The engine lives behind Core.

## 3. Executable before P4 merges, and blocked until it does

**Executable now** — all of it dependency-marked scaffolding, mirroring what P6-03a did for
P3. None of it makes the feature available:

| # | Item | Notes |
| --- | --- | --- |
| E1 | `scripts/p6/p4_contract.py`, pinned to one P4 ref, deriving the seven read routes' scopes and response field sets and freezing `web/contracts/p4-memory-read.v1.json` with its source commit | `p3_contract.py` is the template; the same drift guard applies |
| E2 | `p4_pending_paths` in `web/server/core-paths.json` (strict-id patterns) behind its own default-off switch, with the live `paths` list untouched | the existing test that pending paths never enter the live allowlist extends to these |
| E3 | A simulated P4 fixture, labelled on every reply and asserting each payload against the frozen contract before serving | values are the fixture's; shapes are P4's |
| E4 | Types, endpoints and views behind the dependency-pending panel; the `未接入` row becomes a P4-dependency row | no action buttons |
| E5 | Component tests plus a Gate section with the negatives listed in §4 | same shape as P6-03a's record set |

**Blocked until P4 is merged and independently validated:**

* acceptance against a real Core with a real `memory:read` token;
* the management flows (`memory:manage`) and `reflect` (`memory:reflect`) — these are writes,
  and P6-02's precedent is that writes need their own frozen payload/permission table and an
  independent permission-and-audit Gate before any UI ships them;
* any statement that the Memory inspector works, or that Memory is wired.

## 4. Acceptance criteria

Falsifiable, and each one is a test or a Gate record rather than a sentence:

1. **Contract.** `p4_contract.py --check` reproduces the frozen snapshot from the pinned P4
   ref; changing a scope or a field in the snapshot fails the drift guard.
2. **Baseline untouched.** The live allowlist is still exactly 25 paths and contains none of
   the seven memory reads; the default-off switch is asserted, and no default path reaches
   Core for a memory route.
3. **Gate, per read.** All seven reads render from the labelled fixture, with network
   readback; screenshots for the jobs list/detail, the items list/detail and a trace.
4. **Non-authoritative is visible.** The rendered item, item-detail and health views state
   that Memory is non-authoritative, read from Core's own `non_authoritative` field rather
   than from a hardcoded string.
5. **Empty vs error vs refused are three distinct states.** No grants → empty list, not an
   error; unauthorised project → `not_found`, not an error banner and not the
   dependency-pending panel; missing `memory:read` → Core's `forbidden_scope`.
6. **Malformed ids are refused locally** with the right field named and **zero** requests sent.
7. **Redaction is respected.** With an agent key, engine-derived text is absent from the
   rendered job payloads.
8. **No forbidden affordance exists.** No retain, no bank selector, no direct-engine call, no
   `reflect` control in the read slice; a grep over the bundle for engine credentials and
   endpoints comes back empty.
9. **Status.** The pages and the report say **PARTIAL / DEPENDENCY_PENDING**; nothing claims
   P6 passed, and Memory is never presented as Truth.

## 5. Non-goals

* No retain, no bank selection, no direct Hindsight access, no engine credential in the
  browser.
* No Memory write into Truth, and no Memory shown as Authority.
* No `reflect` in the read slice.
* No P4 benefit experiments — they remain stopped, and this document neither runs nor plans
  one.
* No changes to `src/`, the schema, or the existing services.

## 6. Open questions for the operator

These change the deliverable, so they are asked rather than assumed:

* **Q1 — which ref is the contract source?** `p4-integration` @ `0c4f8f0` is the newest and
  `p4-memory` is an ancestor of it, so pinning to `p4-integration` is the defensible default;
  `p4-memory-checkpoint` diverges and would be a deliberate choice.
* **Q2 — split, or one milestone?** The taskbook's P6-03b mixes seven reads with six
  manage/reflect actions. Proposal: **P6-03b1** = the seven reads (this plan), and
  **P6-03b2** = the five lifecycle/retry writes plus `reflect`, each needing the P6-02-style
  audited-write Gate. Shipping them together would put write authority behind a read-only
  review.
* **Q3 — the placeholder.** Should `未接入` stay until P4 merges, or should P6-03b1's
  dependency-pending views land first, as P6-03a did for P3? Landing them earlier makes the
  pending state demonstrable but adds a second set of pending paths to maintain.
* **Q4 — is any of E1–E5 wanted now?** The operator's instruction was to plan before
  implementing. This document assumes **nothing** is built until that is confirmed.

## 7. Standing status

`P6-03b: NOT STARTED, DEPENDENCY UNMERGED` (P4 on `p4-integration`, schema 11, not in `main`).
The baseline serves none of these routes, so no part of this slice is available, and none of
it may be described as such until §4 has been executed against a real Core.
