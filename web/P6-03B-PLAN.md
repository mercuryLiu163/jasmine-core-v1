# P6-03b — scope, dependency check, and acceptance criteria

**Status: P6-03b1 DELIVERED as PARTIAL / DEPENDENCY_PENDING; P6-03b2 NOT STARTED.**

This began as the task breakdown and dependency check the operator asked for before
implementation. The operator then authorised b1, and E1–E5 below are built, tested and
evidenced: contract pinned by full SHA, pending paths behind a default-off switch, a labelled
simulated fixture, the seven read views, and 89 guards across three layers. The Gate bundle is
`validation-runs/p6/20261008T020743Z-p6-03b1-clickthrough/`.

**P4 phase acceptance still has NOT passed**, and the live proxy entry is still closed: nothing
here was validated against a real P4 Core. Section §6 records what remains open.

**Contract source: pinned.** `p4-integration` @
`0c4f8f0e2118711ac9aa2a7006fddc6982962248` — the full SHA, not the branch name, so the
contract cannot move when that branch does.

**P4 phase acceptance: NOT PASSED.** Nothing here has been validated against a real P4 Core.
The pinned commit is an unmerged feature branch, not an accepted phase.

**Dependency status: DEPENDENCY_PENDING.** The target Core does not serve these routes, so the
live proxy stays closed by default.

P6-03b is the **P4 (Memory)** inspector, split by operator decision:

| Slice | Contents | Status |
| --- | --- | --- |
| P6-03a | Checkpoint / Context Pack / Resume (P3) | delivered, PARTIAL / DEPENDENCY_PENDING |
| **P6-03b1** | **7 read endpoints** — health, jobs ×2, items ×2, traces ×2 | **planned only — this file** |
| **P6-03b2** | **5 `memory:manage` writes + `reflect`** | planned, separate milestone, needs an audited-write Gate |
| P6-03c | Validation inspector (P7-01) | not started |

## 1. Scope, as the taskbook defines it

The P6 taskbook (`docs/Jasmine-Core-V1-P6-阶段任务书.md` §6) splits P6-03 by domain. Its two
P6-03b rows are the Memory Jobs/Recall view and the Memory Inspector management view:

| View | Frozen prerequisite and display | Stated failure modes / non-goals |
| --- | --- | --- |
| Memory Jobs/Recall | P4 jobs/detail/retry, bank mapping, source Event/Evidence, query/result trace/degraded, management permission | Memory is NON_AUTHORITATIVE; cannot change Rule/State; Reflect only on explicit user request; retry must not duplicate retain; Hindsight credentials must not enter the browser |
| Memory Inspector management | P4's explicit obsolete/delete/pin Core API, authorisation/audit, soft-delete / retained provenance / idempotency policy | With no interface, read-only with actions disabled; must not connect to the engine directly or bypass the secret filter; historical Memory conflicting with current Authority is displayed prominently |

The taskbook also fixes the boundary: the P4 line owns the Memory adapter, jobs, recall and
management API; the UI must not talk to Hindsight directly, must not choose a bank, and must
not write Memory into Truth. And, generally for P6-03: **"不能将未接入部分写成已通过 P6"**.

**The operator's split puts the first row's reads in b1 and every write in b2.** "Retry" and
"management permission" belong to b2; b1 renders and links, and issues no state-changing
request.

## 2. Dependency check — the decisive part

**The baseline this dashboard is served against has no Memory API at all.**

| Check | Result |
| --- | --- |
| Baseline `850fbec` (schema 8) memory routes | **0** |
| Baseline `src/jasmine_core/memory/` | does not exist |
| Pinned contract source | `p4-integration` @ `0c4f8f0e2118711ac9aa2a7006fddc6982962248` (2026-10-06), schema **11**, 128 commits ahead of the baseline |
| Is that commit in `main`? | **No** |
| Other P4 refs | `p4-memory` @ `b88efc5` (an **ancestor** of the pinned commit); `p4-memory-checkpoint` @ `a0e8539` (diverges — **not** an ancestor) |
| Route surface across the three P4 refs | all three declare the same 13 routes; the shapes and semantics are pinned to the commit above |

P6-03b is in the *same class* as P6-03a: a real contract exists on an unmerged branch, the
baseline cannot answer it, and the honest delivery is a dependency-marked scaffold. The
difference is that P4 is a **separate line** from P3 — P6-03b does not become available just
because P3 lands.

### Pinning rule (operator requirement)

The contract is pinned to the **full commit SHA**, never to a branch name. Every derived
artefact records that SHA, so:

* the contract does not drift when `p4-integration` moves;
* a drift check can compare the frozen snapshot against that exact commit, and fails if the
  commit is absent rather than silently passing;
* re-pinning to a newer P4 commit is an explicit, reviewable change — not a side effect of a
  `git pull`.

The same rule already applies to P6-03a, which pins `7016dabb8926…`, and both are recorded the
same way in their frozen snapshots.

### The P4 read surface — `src/jasmine_core/memory/api.py` at the pinned SHA

Thirteen routes, all under `/v1/memory/*`. Seven are reads and make up **P6-03b1**:

| # | Method and path | Handler | Scope | Response |
| --- | --- | --- | --- | --- |
| 1 | `GET /v1/memory/health` | `memory_health` | `memory:read` | `{**health, non_authoritative: true}` |
| 2 | `GET /v1/memory/jobs` | `list_jobs` | `memory:read` | `{jobs, count, counts}` — query `project_id`, `status`, `kind`, `memory_type`, `limit`, `after_job_id` |
| 3 | `GET /v1/memory/jobs/{mjb}` | `get_job` | `memory:read` | `{job, attempts}` |
| 4 | `GET /v1/memory/items` | `list_items` | `memory:read` | `{items, count, non_authoritative: true}` — query `project_id`, `bank_id` |
| 5 | `GET /v1/memory/items/{mem}` | `get_item` | `memory:read` | `{**detail, non_authoritative: true}` |
| 6 | `GET /v1/memory/traces` | `list_traces` | `memory:read` | `{traces, count}` |
| 7 | `GET /v1/memory/traces/{mtr}` | `get_trace` | `memory:read` | `{trace}` |

The other six are writes or generator actions and are **P6-03b2**:

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

Derived from the pinned commit's module docstring and handler bodies. Each is a testable
constraint, and per the operator's requirement the derivation covers **semantics** as well as
routes, scopes and shapes:

* **`non_authoritative: true` is emitted by Core itself** on item, item-detail and health
  payloads. The inspector must surface it; Memory is not Authority and must never be rendered
  as if it were.
* **There is no public retain endpoint and no public bank selector.** Content reaches the
  engine only through a worker acting on a job derived from committed truth, and the bank is
  derived from the item's own scope. A UI affordance to retain text or choose a bank would be
  inventing an API that deliberately does not exist.
* **Reflect is human/system only, behind `memory:reflect`**, is the only path that runs the
  engine's generator, and is unreachable from a Context build. The taskbook adds: only on an
  explicit user request. It is b2.
* **Project authorisation comes from the authenticated actor**, never a body field or query
  parameter, and an unauthorised project is reported as `not_found` — so the inspector cannot
  be used to enumerate another project's work. The UI must render that as *not found*, and in
  particular must not turn it into an error banner or a retry prompt.
* **An actor with no grants gets an empty list, not the whole database**, and agent keys get
  job payloads redacted because error summaries can carry engine text. Empty and redacted are
  both normal states, not failures.
* **Hindsight credentials never enter the browser.** The engine lives behind Core.

## 3. What was built before P4 merges, and what stays blocked

**Built — P6-03b1**, every piece of it dependency-marked. None of it makes the feature
available: the paths are refused by default, the only Core that has answered them is the
labelled fixture, and every page says PARTIAL / DEPENDENCY_PENDING.

| # | Item | Status |
| --- | --- | --- |
| E1 | `scripts/p6/p4_contract.py` pinned to the full SHA above, deriving the seven read routes' scopes, response field sets **and** the semantics in §2, frozen as `web/contracts/p4-memory-read.v1.json` | **built.** The drift check re-derives from the pinned commit and **exits 2** where it is absent — P6-03a skipped to 0, and review was right that this passes for the wrong reason |
| E2 | `p4_pending_paths` in `web/server/core-paths.json` (strict-id patterns) behind its own **default-off** switch, with the live `paths` list untouched at 25 | **built.** Both hosts share one `flagEnabled` spelling; tests assert they agree, that P3 and P4 gate independently, and that enabling the reads does not open the six b2 writes |
| E3 | A simulated P4 fixture, labelled on every reply and asserting each payload against the frozen contract before serving | **built.** `X-P6-Fixture: simulated-p4-reads`; the fixture also serves the shell routes in their documented shapes after an earlier version crashed the overview |
| E4 | Types, endpoints and views behind the dependency-pending panel | **built.** Seven views; no write control exists, asserted structurally rather than by text |
| E5 | Component tests plus a Gate section | **built, 89 guards.** Gate: 10/10 full twice, 4/4 scope |

**Still blocked until P4 is merged and independently validated:**

* acceptance against a real Core with a real `memory:read` token;
* anything in P6-03b2. The five lifecycle/retry writes and `reflect` are state-changing, and
  P6-02's precedent is that writes need their own frozen payload/permission table and an
  independent permission-and-audit Gate before any UI ships them;
* any statement that the Memory inspector works, or that Memory is wired.

## 4. Acceptance criteria

Falsifiable, each a test or a Gate record rather than a sentence.

**P6-03b1 — the read slice:**

1. **Contract.** `p4_contract.py --check` reproduces the frozen snapshot from the pinned SHA;
   changing a scope, a field or a recorded semantic fails the drift check, and an absent
   commit fails loudly rather than passing.
2. **Baseline untouched.** The live allowlist is still exactly 25 paths and contains none of
   the seven memory reads; the switch defaults off; with it off no memory path reaches Core.
3. **Gate, per read.** All seven reads render from the labelled fixture, with network
   readback; screenshots for the jobs list/detail, the items list/detail and a trace.
4. **Non-authoritative is visible.** Item, item-detail and health views state that Memory is
   non-authoritative, read from Core's own `non_authoritative` field rather than a hardcoded
   string.
5. **Empty vs error vs refused vs not-found are four distinct states.** No grants → empty
   list, not an error; unauthorised project → `not_found`, not an error banner and not the
   dependency-pending panel; missing `memory:read` → Core's `forbidden_scope`; P4 absent →
   the dependency-pending panel.
6. **Malformed ids are refused locally** with the right field named and **zero** requests sent.
7. **Redaction is respected.** With an agent key, engine-derived text is absent from the
   rendered job payloads.
8. **No forbidden affordance exists.** No retain, no bank selector, no direct-engine call, no
   `reflect` control, no lifecycle button; a grep over the bundle for engine credentials and
   endpoints comes back empty.
9. **Status.** The pages and the report say **PARTIAL / DEPENDENCY_PENDING** and state that
   the contract source is pinned while **P4 phase acceptance has not passed**; nothing claims
   P6 passed, and Memory is never presented as Truth.

**P6-03b2 — additional, before any of it ships:**

10. A frozen payload/permission table per write, an explicit permission Gate, and per-action
    audit readback, mirroring P6-02 milestone B.
11. `reflect` is human/system only, requires an explicit user request, and is unreachable from
    a Context build.
12. A retry does not duplicate a retain.

## 5. Non-goals

* No retain, no bank selection, no direct Hindsight access, no engine credential in the
  browser.
* No Memory write into Truth, and no Memory shown as Authority.
* No `reflect` and no lifecycle writes in b1.
* No P4 benefit experiments — they remain stopped, and this document neither runs nor plans
  one.
* No changes to `src/`, the schema, or the existing services.

## 6. Decisions and remaining question

Settled by the operator:

* **Contract source** — pinned to `p4-integration` @
  `0c4f8f0e2118711ac9aa2a7006fddc6982962248`, by full SHA; routes, permissions, response
  shapes and semantics are derived from that commit with a drift check; the contract's
  provenance is marked as pinned while **P4 phase acceptance remains unpassed**; and every
  interface not yet integrated with the target Core stays DEPENDENCY_PENDING with the live
  proxy closed by default.
* **Split** — P6-03b1 is the seven reads; P6-03b2 is the five `memory:manage` writes plus
  `reflect`, behind its own audited-write Gate.

Still open:

Both are settled: **Q3** — the dependency-pending views land now, with simulation labelled,
the pending paths isolated from the live allowlist and forwarding off by default. **Q4** —
E1–E5 are authorised and built; everything in P6-03b2 remains plan-only.

Still open, for the operator:

* **P6-03b2** is not started and needs its own frozen payload/permission table and an
  audited-write Gate before any of it ships.
* **Independent review of the b1 implementation** has not run at the time of writing; the
  bundle is frozen and the slices are green, but no third party has yet reviewed the b1 code
  or reproduced the Gate.
* **One unexplained behaviour.** Signing out and back in mid-Gate left the app rendering the
  previous route while the URL already carried the target hash; assigning the hash and
  dispatching `hashchange` did not fix it, so the scope check moved to its own run. The root
  cause was not established. It is not recorded as a product defect because the component
  tests drive the same transition through `render` and pass — but it is unexplained, and that
  is stated rather than buried.

## 7. Standing status

`P6-03b1: NOT STARTED, DEPENDENCY_PENDING` — contract pinned to
`0c4f8f0e2118711ac9aa2a7006fddc6982962248`, P4 phase acceptance **not passed**, live proxy
closed by default. `P6-03b2: NOT STARTED` and gated behind its own audited-write Gate. The
baseline serves none of the thirteen routes, so no part of this slice is available, and none
of it may be described as such until §4 has been executed against a real Core.
