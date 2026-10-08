# P6-03b — scope, dependency check, and acceptance criteria

**Status: P6-03b1 DELIVERED as PARTIAL / DEPENDENCY_PENDING; P6-03b2 IMPLEMENTED behind a
default-off switch, also PARTIAL / DEPENDENCY_PENDING** — the six writes exist and are exercised
only against a simulated P4 fixture, never a real one. Per-criterion state:
[`P6-03B2-COMPLIANCE.md`](P6-03B2-COMPLIANCE.md); specification:
[`P6-03B2-WRITE-SPEC.md`](P6-03B2-WRITE-SPEC.md).

This began as the task breakdown and dependency check the operator asked for before
implementation. The operator then authorised b1, and E1–E5 below are built, tested and
evidenced: contract pinned by full SHA, pending paths behind a default-off switch, a labelled
simulated fixture, the seven read views, and 124 guards across three layers. The Gate evidence
is registered under the P6-03b1 section of `validation-runs/p6/P6-02B-INDEX.md`, which is where
the current bundle and the superseded ones are listed — the directory is gitignored, so a
specific timestamp here would rot the moment the Gate is re-run.

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
| **P6-03b1** | **7 read endpoints** — health, jobs ×2, items ×2, traces ×2 | **delivered**, PARTIAL / DEPENDENCY_PENDING (this file) |
| **P6-03b2** | **5 `memory:manage` writes + `reflect`** | **implemented, default-off, verified against the simulated fixture only**; PARTIAL / DEPENDENCY_PENDING ([`P6-03B2-COMPLIANCE.md`](P6-03B2-COMPLIANCE.md)) |
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

### Enabling the switch against a Core that has no P4 is not a harmless experiment

`P6_ENABLE_P4_READS=1` tells the *proxy* to forward the seven paths. If the Core behind it has no
P4, the request arrives and Core answers `not_found` — the same answer it gives for a route it
does not serve — so the view renders "does not exist, or you may not see it" rather than the
dependency-pending panel. The proxy did not refuse it, so nothing marks the dependency.

That is why the flag is a Gate-time affordance and not a readiness switch: pointed at a baseline
Core it produces a misleading answer instead of an honest refusal. Set it only when the Core is
genuinely a P4 build.

## 3. What was built before P4 merges, and what stays blocked

**Built — P6-03b1**, every piece of it dependency-marked. None of it makes the feature
available: the paths are refused by default, the only Core that has answered them is the
labelled fixture, and every page says PARTIAL / DEPENDENCY_PENDING.

| # | Item | Status |
| --- | --- | --- |
| E1 | `scripts/p6/p4_contract.py` pinned to the full SHA above, deriving the seven read routes' scopes, response field sets **and** the semantics in §2, frozen as `web/contracts/p4-memory-read.v1.json` | **built.** The drift check re-derives from the pinned commit and **exits 2** where it is absent — P6-03a skipped to 0, and review was right that this passes for the wrong reason |
| E2 | `p4_pending_paths` in `web/server/core-paths.json` (strict-id patterns) behind its own **default-off** switch, with the live `paths` list untouched at 25 | **built.** Both hosts accept the same value set and are tested on it — including `' yes '` and mixed case, because the helper is *duplicated* rather than shared and dropping `.trim()` from one host left both suites green until those cases existed. Tests also assert P3 and P4 gate independently, and that enabling the reads does not open the six b2 writes |
| E3 | A simulated P4 fixture, labelled on every reply and asserting each payload against the frozen contract before serving | **built.** `X-P6-Fixture: simulated-p4-reads`; the fixture also serves the shell routes in their documented shapes after an earlier version crashed the overview |
| E4 | Types, endpoints and views behind the dependency-pending panel | **built.** Seven views; no write control exists, asserted structurally rather than by text |
| E5 | Component tests plus a Gate section | **built, 124 guards** (25 contract, 21 fixture, 44 view, 16 static-host, 14 Vite-host, 4 shared-hook). Gate: 11/11, twice with identical step sequences, in a single run |

**Still blocked until P4 is merged and independently validated:**

* acceptance against a real Core with a real `memory:read` token;
* anything in P6-03b2. The five lifecycle/retry writes and `reflect` are state-changing, and
  P6-02's precedent is that writes need their own frozen payload/permission table and an
  independent permission-and-audit Gate before any UI ships them;
* any statement that the Memory inspector works, or that Memory is wired.

### Reachability, and why there is no navigation entry

The seven Memory views have **no nav entry**, and nothing in the app links to them. That is
deliberate and matches P6-03a, whose Checkpoint / Context Pack / Resume views are also reached
only by their `#/…` hash: a nav item is an advertisement of availability, and these views are
`PARTIAL / DEPENDENCY_PENDING` against an unmerged dependency. The nav's `领域(未接入)` menu
describes what is missing instead.

They are reachable, and the Gate uses these URLs:

| View | Hash |
| --- | --- |
| health | `#/memory/health` |
| jobs list / detail | `#/memory/jobs`, `#/memory/jobs/{mjb}` |
| items list / detail | `#/memory/items`, `#/memory/items/{mem}` |
| traces list / detail | `#/memory/traces`, `#/memory/traces/{mtr}` |

Each one states the slice status on arrival, so a deep link cannot be mistaken for a working
capability. If the operator later wants them in the nav, that is a product decision to take when
P4 lands — not something to slip in while the dependency is pending.

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
5. **Empty vs error vs refused vs not-found vs pending are five distinct states.** No
   grants → an empty list, not an error; `not_found` → its own state saying the object does
   not exist *or* the actor may not see it, with **no error banner and no retry prompt**
   (P4 refuses an unauthorised row with the same `not_found` it uses for an absent one,
   deliberately, so the UI must not invent the distinction back and must not invite a retry
   that cannot succeed); a genuine failure (5xx) → the error banner with a retry; missing
   `memory:read` → Core's `forbidden_scope`; P4 absent → the dependency-pending panel. The
   standing re-read control every view carries is unaffected — it is not an error retry.
6. **Malformed ids are refused locally** with the right field named and **zero** requests sent.
7. **Redaction is respected, at the layer this slice controls.** P4 withholds engine free
   text (`safe_error_summary`) from any actor that is not human or system, by nulling the
   value and keeping the key (`_withhold_engine_text`, applied inside `_respond`, so it covers
   every handler). The fixture does the same on all seven reads, and tests assert both
   directions: a human key sees the text, an agent key does not, the key is still present, and
   the view renders the sentinel Core sent rather than inventing text where it was withheld.
   **Two limits, stated rather than implied:** the fixture keys agent-ness on an
   `actor:agent` scope, whereas the real Core decides from the actor record via
   `core.actor_kind(...)` — the fixture has no actor registry, so it stands in rather than
   reproduces that call; and P4's own decision is server-side, so what is verified here is
   that the UI does not undo the withholding.
8. **No forbidden affordance and no engine client exists.** No retain, no bank selector, no
   direct-engine call, no `reflect` control, no lifecycle button. Asserted structurally rather
   than by word search, because the first draft of this criterion said "a grep over the bundle
   comes back empty" and that is false: the bundle contains the word *Hindsight* in
   disclaimers saying the browser does **not** touch it. What is asserted instead: the built
   bundle carries no origin other than XML namespace URIs and React's error-decoder link; no
   engine credential identifier (`api_key`, `access_token`, `refresh_token`, `password=`,
   `client_secret`); every memory call is a same-origin `/memory/...` path; and no memory
   module names an engine client. **The two bundle-level tests skip when `web/dist` is absent**
   (a fresh checkout has none), so those two pass vacuously there; the same-origin-path and
   no-engine-client tests read source and always run. Build before relying on the bundle pair.
9. **Status.** The pages and the report say **PARTIAL / DEPENDENCY_PENDING** and state that
   the contract source is pinned while **P4 phase acceptance has not passed**; nothing claims
   P6 passed, and Memory is never presented as Truth.

**P6-03b2 — additional, before any of it ships:**

Criterion 10 is discharged against the **simulated** P4 write fixture and against nothing else.
The frozen payload/permission table is [`P6-03B2-WRITE-SPEC.md`](P6-03B2-WRITE-SPEC.md) with
snapshot `contracts/p4-memory-write.v1.json`, derived by `scripts/p6/p4_write_spec.py`; the
per-write permission Gate, payload checks, conflict/replay cases and per-action audit readback
are in the P6-03b2 Gate and in the component tests. What is **not** discharged is the last clause
of the frozen criterion 9: no part of this has been exercised against a real P4 Core. Status is
**PARTIAL / DEPENDENCY_PENDING**, and `web/P6-03B2-COMPLIANCE.md` records exactly which criterion
is met by which artefact.

10. A frozen payload/permission table per write, an explicit permission Gate, and per-action
    audit readback, mirroring P6-02 milestone B.
11. `reflect` is human/system only, requires an explicit user request, and is unreachable from
    a Context build.
12. A retry does not duplicate a retain. Core refuses `DONE`, `RUNNING` **and already-`PENDING`**
    jobs with `invalid_state_transition`, and an accepted retry resets the budget to 0.

### Every criterion names the test that checks it

Two criteria (7 and 8) were found false as written precisely because nothing verified them. So
each one names its checker, and a reviewer can follow any row and see whether the test does what
the criterion claims. `R#` is a Gate record in `scripts/p6/browser_trace.mjs`.

| Criterion | Checked by |
| --- | --- |
| 1 contract + drift + loud absence | `test_snapshot_still_matches_the_pinned_commit`, `test_check_exits_two_when_the_commit_is_absent`, `test_source_is_a_pinned_full_sha_not_a_branch`, `test_snapshot_says_the_dependency_is_missing_and_acceptance_unpassed`, `test_every_payload_matches_the_frozen_field_set`, `test_row_keys_come_from_the_p4_tables`, `test_a_rename_in_p4_would_break_the_guard`, `test_interfaces_carry_exactly_the_contract_fields` |
| 2 baseline untouched | `test_the_pinned_baseline_lacks_the_dependency_and_this_core_agrees_with_its_own_tree`, `test_pending_paths_are_not_in_the_live_read_allowlist`, `test_the_live_allowlist_is_unchanged_by_this_slice`, `test_every_live_path_is_a_real_baseline_route`; `refuses all seven on a baseline proxy`, `forwards all seven when the operator states the Core has P4`, `leaves the P6-03a switch independent`, `defaults to off…`; the Vite-host equivalents |
| 3 the seven reads render | Gate records 1–4 — health; jobs list+detail; items list+detail; traces list+detail — screenshots 25–28. The traces list was missing until the criterion was audited, which is why the record now names both halves |
| 4 `non_authoritative` from Core | `shows the marker when Core sends non_authoritative: true`, `does NOT claim it when Core does not send the field`, `shows the marker on an item detail`, `test_non_authoritative_is_present_where_p4_sends_it`; Gate R1 |
| 5 five states distinct | `renders a grantless actor as an empty result, not an error`, `shows an unauthorised project as not_found, not as an error and not as pending`, `renders an unknown id as the same not-found state…`, `still shows a genuine failure as an error banner with a retry`, `shows a missing scope as Core refusal…`; the `%s marks the pending dependency instead of blaming Core` cases also assert that a proxy refusal — which is also HTTP 404 — renders the **pending** panel and not the not-found state, so the code decides and not the status; `test_a_grantless_token_gets_an_empty_list_not_an_error`, `test_an_unauthorised_project_is_not_found_rather_than_an_error`, `test_a_missing_scope_is_403_and_names_itself`, `test_an_unknown_id_is_not_found`; Gate R5 |
| 6 malformed id refused locally | the three `does not blame the missing P4 for a bad … id` cases; `test_a_malformed_id_is_400_not_a_silent_404`; Gate R6 |
| 7 redaction respected | `test_a_human_key_sees_the_engine_text`, `test_every_read_withholds_the_engine_text_from_an_agent` (all seven routes, each with a per-route control so an unobservable route fails rather than passing), `test_withholding_does_not_drop_the_field`, `renders nothing where Core withheld the text`, `renders the sentinel Core actually sent` — the last pair runs on the **detail** view, because the list table never renders the field and the first version of it was vacuous |
| 8 no engine client | `test_the_bundle_can_only_talk_to_its_own_origin`, `test_the_bundle_carries_no_engine_credential_identifier`, `test_the_memory_endpoints_stay_under_the_same_origin_client`, `test_no_engine_client_exists_in_the_slice`, `test_the_memory_read_section_issues_get_only`, `test_absent_retain_and_bank_selector_routes`; `test_every_write_is_refused_because_writes_are_p6_03b2` |
| 9 status | `test_snapshot_says_the_dependency_is_missing_and_acceptance_unpassed`; `states PARTIAL / DEPENDENCY_PENDING on the view itself`; the `%s offers only read controls` cases; Gate R7 |
| b2 implemented behind a default-off switch (not a b1 criterion) | the b2 write-spec guards — the frozen snapshot still matches the pinned SHA, the six writes are exactly the ones the read slice excluded, `--check` exits 2 when the commit is absent, they are reachable only through the gated `p4_write_paths` list, and the client issues exactly the six. The view/Gate half is listed in `P6-03B2-COMPLIANCE.md` |

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
E1–E5 are authorised and built; P6-03b2 is built behind its own default-off switch
(`P6_ENABLE_P4_WRITES`), deep-link only, and verified against the simulated fixture alone.

Still open, for the operator:

* **P6-03b2** is built and has its own payload/permission Gate, but that Gate ran against a
  simulated Core: real-P4 verification is the outstanding item, and the switch stays off by
  default until it is done.
* **Independent review: done, three pairs of passes.** Two adversarial passes (source review +
  clean-archive validation) ran first, each returning PASS-WITH-CAVEATS; their 12 findings are fixed
  at `c1113c5`, `108f699` and `bbdbf7f`. A further pair then reviewed the fix rounds and found a
  same-class latent bug in `usePagedResource`, a stale Gate count in this file, a stale
  "review has not run" line, a wrong labelled-response count in the bundle README, an
  unbackticked-citation hole in the mapping self-check, and a web test fixture carrying
  detail-only fields — all fixed after that. Validation reproduced every suite, the contract
  check, the build digests and 11/11 on the Gate from a clean archive, and confirmed the
  `useResource` fix is load-bearing by reverting it and watching the Gate fail at record 8.
  **Model caveat, stated rather than glossed:** the operator asked for these passes on
  `gpt-6.1-sol`. That override was unreachable in this environment — it returned no result while
  the default model did, and the `opencode-go` provider has no API key set — so every pass ran on
  the session's default model (`deepseek-flash`) after the operator chose that fallback. The
  independence is between separate agent contexts, not between model families.
* **RESOLVED — the "route anomaly" was a real defect, and my first framing of it was wrong.**
  The Gate used to time out after 退出 and re-signing in with a narrower token, when navigating
  from a Memory *detail* to its *list*. I first recorded it as unexplained, not reproduced in
  jsdom, and probably not a product defect. The operator refused that reasoning, correctly, and a
  real-browser probe found the cause: `useResource` depends on `[nonce, signature]`, and the list
  resources passed `[]` as `deps`, so the signature was `''` in both modes. Switching from a
  detail to a list changed the loader from `null` to a function **without changing the signature**,
  so the effect never re-ran, the resource stayed `idle`, and the panel rendered with no data, no
  loading, no error and **no request**. It is fixed at the hook — a loader appearing or
  disappearing is now part of the signature — and the same shape existed latently in
  `CheckpointsView`, whose `latest` resource depends on `[taskId, sessionId]`.
  The probe also answered the question that mattered more than the routing: **no data fetched with
  the previous token was ever displayed** after 退出 or after signing in with a narrower token, so
  this was staleness, not a leak. Evidence, including before/after request logs, is in
  `<bundle>-route-investigation/`. The Gate workaround (splitting the scope check into its own
  invocation) is removed and the check now covers the transition in one run.

## 7. Standing status

`P6-03b1: DELIVERED, PARTIAL / DEPENDENCY_PENDING` — the seven read views, the pinned
contract, the gated paths and the Gate are built and evidenced; contract pinned to
`0c4f8f0e2118711ac9aa2a7006fddc6982962248`, **P4 phase acceptance not passed**, live proxy
closed by default. This header said "NOT STARTED" for five commits after the implementation
landed, which a reader could have taken either as "nothing to review" or as a reason to skip
the acceptance caveats. `P6-03b2: IMPLEMENTED, DEFAULT-OFF, PARTIAL / DEPENDENCY_PENDING` — the six writes are built and
gated behind their own switch, and every one of them was verified against a simulated P4 fixture,
never a real one.
The baseline serves none of the thirteen routes, so no part of this slice is available, and
none of it may be described as such until §4 has been executed against a real Core.
