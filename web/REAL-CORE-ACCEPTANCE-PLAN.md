# Real-Core acceptance plan — isolated P3/P4 integration for P6-03a / b1 / b2

**Status: PLAN AND ENVIRONMENT DESIGN. Nothing in this file has been executed.** It exists because
the operator asked for the acceptance plan and the isolated-environment design *before* any real
Core is started, and because P6-03b1/b2 currently rest on a **simulated** P4 fixture: the
specification's criterion **9b (verified against a real P4 Core) is unmet**, and the b1 slice has
never been pointed at real P4 either.

This document says what will be verified, how the environment is kept away from everything the
operator owns, what each check can and cannot prove, and what would make a result `BLOCKED` rather
than `PASS`. It does not start a server.

## 1. What this must settle

1. **P6-03b1 (seven Memory reads)** against real P4 code: the contract derived from the pinned
   commit, the real response shapes, the real `not_found`/`forbidden_scope` behaviour, and the real
   per-actor withholding rule.
2. **P6-03b2 (six writes)** against real P4 code: real payload acceptance, real permission
   decisions (**including key issuance**, which the fixture could not model), real
   `RevisionConflict`, real `event_id` idempotency, real command Events and traces, and a real
   post-write re-read of the object's own row.
3. **The divergences the fixture admitted**, which only a real Core can settle: P4's idempotency
   hash covers the command payload (the fixture keyed on `(object_id, command)` only); the nested
   command `event` shape is P4's, not the fixture's; a lifecycle reply's `job` is a real queued job
   rather than `null`; and the read **list** routes reflect mutations.
4. **P6-03a (P3 continuity reads)** on a P3 build, by the same method, once the P4 pass is done.

It does **not** settle P4's own phase acceptance, and it cannot: see §5.

## 2. Isolation, stated as promises that can be checked

| Promise | How it is kept |
| --- | --- |
| The operator's Core, database, hooks, global config and accounts are **never touched** | The real Core is started from a `git archive` of the pinned commit into a fresh temp directory with its **own** `core.db`, workspace, evidence-producer file and ports. It is never started from the worktree, never given `JASMINE_CORE_DB` pointing at anything the operator owns, and the plan's scripts take the directory as an argument they create |
| Nothing listens off-host | P4's `serve()` refuses any non-loopback bind (`api/server.py`); the dashboard proxy already requires a loopback `P6_CORE_ORIGIN` |
| No credential of the operator's is read | Every key is minted inside the isolated Core by P4's own `auth.issue_key`, written to the temp tree (mode 600), and never printed in full (P4's own `p4-memory-acl.py` sets that precedent: "nothing here prints or returns a token value") |
| The product's live entry stays closed | The dashboard is served from an isolated archive with `P6_ENABLE_P4_READS=1` **and** `P6_ENABLE_P4_WRITES=1` on a throwaway port; `web/server/core-paths.json` is not edited, `paths` stays at 25 and the milestone-B write list at 4 |
| The run can be deleted | Everything lives under one temp root; teardown kills the processes and removes it, and the bundle keeps only evidence, never the database |
| The worktree stays clean | Scripts write into `validation-runs/p6/<stamp>-p6-03-realcore/` (gitignored) and the temp root; commits, if any, stage explicit paths |

## 3. Environment design

Three layers, each real or doubled, and each labelled.

### Layer A — the engine (a **wire double**, and the label is not negotiable)

There is no Hindsight deployment here. P4's own Gate says so itself: `scripts/p4-memory-gate.py`
checks preconditions first and reports **`BLOCKED`**, never a fake `PASS`, and its docstring states
"A case is `PASS` only with `mode: "real"` … Component coverage lives in `tests/test_memory_*.py`
and is labelled as such". So this plan does **not** run P4's G-Gate and does not claim P4 phase
acceptance.

What it does instead: P4's own `tests/memory_support.py` ships two engine doubles, and the plan uses
the **wire** one, `StubHindsightServer` — a real `http.server` on loopback speaking Hindsight
0.10.2's real paths, bodies and response shapes, driven through P4's real `HindsightTransport` over
a real socket. Its own docstring states what that buys and what it does not: it catches a wrong URL,
a wrong field name, a wrong method or a body the engine would reject; "it still proves nothing about
a real Hindsight process".

Every result from this layer is labelled **`mode: boundary-fake (engine)`**. In particular the
*content* of a reflect answer and the *quality* of extraction are not evidence about anything real.

### Layer B — the Core (**real P4 code**)

`git archive 0c4f8f0e2118711ac9aa2a7006fddc6982962248 | tar -x -C <temp>/p4`, then P4's own CLI
(`src/jasmine_core/cli.py`):

1. `migrate --db <temp>/core.db` → schema 11;
2. `bootstrap` (host + human actor + key) and `register` for an **agent** actor and a **system**
   actor;
3. keys with explicit scopes minted in-process through `auth.issue_key` — the real issuance rules
   apply, including the ones §4 relies on;
4. **real Truth, then a real retain**: create a project, a Task and a `PASS` Evidence row with a
   usable artifact, then `memory scan` (P4's `eligibility.py` requires exactly that provenance for
   `VERIFIED_OUTCOME`: "at least one `PASS` Evidence row for the same task whose reference is
   currently usable"), then `memory-worker` against the wire-double engine → a real item, a real
   command Event and a real recall/retain trace;
5. `serve --db <temp>/core.db --host 127.0.0.1 --port <free>` with `JASMINE_CORE_WORKSPACE_ROOT`
   and `JASMINE_CORE_EVIDENCE_PRODUCERS` pointing into the temp tree.

### Layer C — the dashboard (a **pinned archive**, as the b2 Gate already does)

`git archive <pinned commit>` → borrow `web/node_modules` read-only → `vite build` →
`serve.mjs --root dist --port <free>` with `P6_CORE_ORIGIN=http://127.0.0.1:<core>` and both P4
switches on. The same launcher discipline as `run_p4_write_gate.sh`: free ports, the host's own
startup line required in its log, tokens only in the temp tree, and a credential check before
freezing.

### What is deliberately **not** done

* No direct `INSERT` into P4's tables to create a memory item or job. The item comes from P4's own
  scan → worker → transport path. If that path cannot run, the affected checks are `BLOCKED`, not
  faked.
* No modification of `src/`, the schema, the migrations, or the dashboard's live allowlist.
* No use of the operator's Hindsight configuration, even if one exists in the environment.

## 4. Acceptance matrix

Each row names what is exercised, the observation that decides it, and where the evidence goes.
`mode` is recorded per check: `real-core` (real P4 code, wire-double engine) or `real-core+wire`.

### Reads (P6-03b1)

| # | Check | Observation | Evidence |
| --- | --- | --- | --- |
| R1 | The seven routes answer **real** P4 with the contract's shapes | every top-level/nested key set equals what `scripts/p6/p4_contract.py` derives from the same commit; compare against `web/contracts/p4-memory-read.v1.json` | `reads/<route>.json`, plus the derived contract re-checked at the same SHA |
| R2 | `memory:read` is required and sufficient | a key without it → `forbidden_scope` naming the scope; an **agent** key with only `memory:read` → 200 (P4 grants agents this scope deliberately) | per-route status + error code |
| R3 | An unauthorised project is `not_found`, not an error | the same `not_found` Core returns for an absent row, as `_may_see` documents | paired status codes |
| R4 | **The real withholding rule** | the same route with a **human** key vs an **agent** key: `safe_error_summary` is text for one and `null` (key present) for the other. This is the rule the fixture simulated with a scope stand-in; here the actor kind comes from P4's `core.actor_kind` | both response bodies, recorded verbatim |
| R5 | Mutations are visible where they should be | after a write, the by-id read **and the list** reflect it — the fixture's list routes did not, and this is one of its admitted divergences | before/after list + detail bodies |
| R6 | No writes are reachable read-only | `GET` on every write path, and `OPTIONS`/`HEAD` probes, refused at the proxy and (with the proxy bypassed, directly on the Core) by Core's router | status + code for each probe |

### Writes (P6-03b2) — the six routes

| # | Check | Observation | Evidence |
| --- | --- | --- | --- |
| W1 | Payloads are accepted **by real P4** | each of the six, submitted from the dashboard, gets a real 200 with the contract's response shape; unknown fields still refused, `event_id` still the replay key, `idempotency_key` still absent | `writes/<route>.json` with request and response bodies |
| W2 | **Real permission, decided by Core** | (a) a `memory:read`-only key → `forbidden_scope` on all six; (b) a human key with the scope → allowed; (c) **issuance**: `auth.issue_key` must *refuse* to mint an agent key carrying `memory:manage` or `memory:reflect` (`forbidden_actor_kind`) — the fixture's "artificial agent token with manage" therefore **cannot exist on real P4**, which is exactly what the b2 compliance record says about it; (d) a revoked key → `unauthenticated` | issuance attempts + per-route decisions |
| W3 | `RevisionConflict` is real, with Core's current revision | a second writer (the real API from the harness, not the dashboard) moves the item; the dashboard's next lifecycle command carries the stale revision it read and receives 409 with `details.current_revision`; the page keeps the draft and issues exactly one POST | request/response pair + DOM snapshot + POST count |
| W4 | `event_id` idempotency, **including the case the fixture could not model** | (a) the same `event_id` repeated returns the original Event and writes no second transition (command log/trace count unchanged); (b) the same `event_id` with a **changed** `expected_revision`/`reason` — the fixture replayed here, P4's `hash_payload` should not; record which happens and treat the fixture's divergence as settled either way | both pairs, with the item's revision before/after |
| W5 | Real command Events and traces | each returned `event.event_id` is read back through the real audit surface and matches; `reflect` writes a real `memory_query_traces` row and the page links it | event readback + trace readback |
| W6 | Write-then-read of the object's own row | after each write the dashboard forces a reload; the row's real `revision`/`status`/`pinned`/`engine_state` must match the write's `after` and differ from `before` | before/after detail + list |
| W7 | Retry invariants against real jobs | a real `FAILED` job is retried (budget reset to 0, document untouched); a real `DONE` job and a `RUNNING` job are refused with `invalid_state_transition`. Reaching those states uses P4's own CLI/API, not a fixture knob | job bodies + fixture-free state |
| W8 | The unknown/malformed/absent cases | unknown ids → `memory_not_found`/`memory_job_not_found`; malformed ids refused locally by the dashboard and by Core when sent directly | paired statuses |
| W9 | `reflect` is an explicit human request end to end | a human key with `memory:reflect` gets the eight-key reply; the page shows the non-authoritative marker from Core's field; a `memory:reflect`-less human key is refused; an agent cannot hold the scope at all (W2c) | response + page |
| W10 | Nothing reaches Rule or State | before/after snapshots of the Authority/State surfaces the Core exposes show no change attributable to a memory write | before/after readback |

### Divergences the fixture admitted (settled here)

| # | Fixture said | What the real Core must show |
| --- | --- | --- |
| D1 | replay identity `(object_id, command)` is looser than P4's command hash | see W4b |
| D2 | the nested command `event` shape is fixture-invented | R1/W5 compare the real `event` keys against the pinned `events.py` shape |
| D3 | a lifecycle reply's `job` is always `null` | W5/W7 show a real queued job (P4's `_queue_remote`) and its by-id read |
| D4 | read **list** routes do not reflect mutations | R5 |

### What no row in this matrix can prove

* That a real Hindsight deployment works, that extraction produces usable units, or that reflect's
  text is any good: the engine is a **wire double** (§3).
* P4's own phase acceptance (its G-Gate needs `mode: real` and a real deployment).
* Anything about P3's continuity reads until the P3 environment is built the same way.
* That the dashboard is free of defects beyond what these checks exercise. This is a
  behaviour-verification plan, not a source audit.

## 5. Judgment rules

Borrowed from P4's own Gate, because they are the right ones:

1. **A precondition that is missing is `BLOCKED`, never `PASS` and never `FAIL`**, and the report
   names the missing item. The first such precondition is the engine: without a real deployment the
   affected rows stay `BLOCKED`, and the run says `mode: boundary-fake (engine)`.
2. **HTTP 200 is not success.** Success is the stored artifact: a real Event id read back, a real
   revision advanced, a real trace row, a real list entry.
3. **Nothing fabricated.** No row is inserted to make a check runnable; if the real pipeline cannot
   produce an item, the write checks are `BLOCKED`.
4. **A refusal is paired with a success by the same key on something it does own**, so a 403/404
   from a broken route cannot masquerade as an authorization decision (P4's `p4-memory-acl.py`
   states this rule; it is adopted verbatim).
5. **The run's manifest records `mode` per check** and never averages the two.

## 6. Evidence bundle and reporting

`validation-runs/p6/<stamp>-p6-03-realcore/`, frozen by `scripts/p6/freeze_bundle.py` (the same
tool the b2 Gate uses, including its exit-2 rule and its refusal to freeze a bundle containing a
generated token), containing:

* `README.md` written from the run's own result files (counts, modes, `BLOCKED` reasons);
* `plan.md` — a copy of this plan as it was at run time, so a reader can see what was promised;
* `reads/`, `writes/`, `issuance/` — request/response bodies, never tokens;
* `core-schema.json`, `core-version.json` — the isolated Core's own report of its schema version;
* `mode.json` — the engine label and the reason the run is not `mode: real`;
* `BUNDLE-FROZEN.json`.

The evidence index (`validation-runs/p6/P6-02B-INDEX.md`) then gains a **P6-03 real-Core** section
that records: the fixed SHA, the Core's schema version, which checks passed, which were `BLOCKED`
and why, and — plainly — that 9b is satisfied only to the extent the engine layer allows. It does
not overwrite the simulated-slice records; those stay as they are.

## 7. Preconditions and operator decisions

| # | Precondition | State |
| --- | --- | --- |
| P1 | The pinned P4 commit is present locally | yes (`git cat-file -e 0c4f8f0e…` verified) |
| P2 | P4's Core runs standalone from an archive | **probed, not assumed.** `git archive 0c4f8f0e… \| tar -x -C <temp>` then `PYTHONPATH=src python3.13 -m jasmine_core.cli migrate --db <temp>/core.db` applied `m0001_baseline` … `m0011_memory` and reported `schema_version: 11`, `drift: []`; `schema` agreed. `PYTHONPATH=src:tests python3.13 -c "import memory_support"` worked with no extra dependencies, and `StubHindsightServer()` exposes `base_url` and `stop` (it binds `127.0.0.1:0`) |
| P3 | An **engine** | **absent.** Plan uses P4's `StubHindsightServer` wire double and labels every result. A real deployment would upgrade the affected rows from `BLOCKED`/`boundary-fake` to `real`; that is the operator's call |
| P4 | Free loopback ports | chosen free at start-up |
| P5 | Whether the real-Core pass should also re-cut the contracts at the *final integration* commit (the operator's step 2) | proposed after the fixed-SHA pass, so a contract change is attributable to the commit rather than the environment |
| P6 | P3 environment for P6-03a | same design against the P3 branch; separate run, not in this plan's first pass |

## 8. Why this is safe to start without a `main` merge

Nothing here depends on `main`: the Core comes from a fixed SHA of the unmerged branch, the
dashboard from a pinned commit, and the two meet on loopback inside a temp directory. What *does*
depend on the merge is the `live` default: `web/server/core-paths.json` stays as it is, the live
entry stays closed, and no operator service is started or reconfigured until the real target
baseline has passed this plan and the operator says so.

## 10. First probe — exploratory, since re-derived by the harness

**Superseded by the executed run** in `validation-runs/p6/20261008T061309Z-p6-03-realcore/`
(**35/35**, no failures, no blockers, `mode: real-core+wire`), which re-derived every observation
below from a clean environment and added the withholding pair, the retry invariants on real jobs,
and the four-divergence settlement. The probe is kept because it is what the design was checked
against, and because two of its observations changed the harness: the redaction row needs a row that
really carries engine text, and a healthy row's `safe_error_summary` is `None` for the human too.

The environment was built and driven by hand to find out whether the plan's design works before a
harness is written around it. **These observations are real but exploratory**: they come from one
ad-hoc session, they are not yet a bundle with `BUNDLE-FROZEN.json`, and the harness is expected to
re-derive every one of them. They are recorded because three of the fixture's admitted divergences
are already settled by them, and a settled divergence changes what the harness must assert.

Environment: real P4 Core at the pinned commit (`schema_version: 11`), its own tree under
`/tmp`, wire-double engine, real Truth created through P4's own stores, and the Memory rows produced
by P4's own recovery scan and worker (`2 DONE retain jobs`, `3 items`).

| Observation | Result |
| --- | --- |
| The seven reads | 200 with the frozen contract's key sets: health 18, jobs `{count,counts,jobs}`, job `{job,attempts}`, items `{count,items,non_authoritative}`, item `{memory,sources,jobs,recallable,lifecycle,engine_claim,non_authoritative}`, traces `{count,traces}`; the nested `lifecycle` has 9 keys |
| `memory:read` on a read-only key | reads 200; a `pin` with the same key → **403 `forbidden_scope`** `{required: memory:manage, granted: [...]}` |
| **Issuance, which the fixture could not model** | `auth.issue_key` for an **agent** actor with `memory:manage` → **403 `forbidden_actor_kind`**; with `memory:reflect` → the same. The fixture's "artificial agent token carrying manage" therefore **cannot exist on real P4**, exactly as the b2 compliance record says |
| A fresh lifecycle transition | `pin` → 200, **7 keys** with `before`/`after`, `replayed: false`, `revision` 1 → 2, and `job: null` (correct: a pin queues no remote work) |
| A replay | the same `event_id` and body again → 200, **5 keys**, `replayed: true` |
| **D1 — the replay identity** | the same `event_id` with a **changed** `reason` → **409 `event_id_conflict`** carrying `existing_body_sha256`. P4 hashes the command payload; the fixture keyed on `(object_id, command)` and replayed here, so the fixture **was** looser and the divergence is now settled with evidence |
| A stale revision | → 409 `revision_conflict` with `current_revision` and `expected_revision` |
| `reflect` | → 200, **8 keys**, a real `trace_id`, `non_authoritative: true` |
| A retry on a `DONE` job | → 409 `invalid_state_transition` |
| The audit readback | the command Event read back through `/v1/events/{event_id}` → 200, same id and type |
| **D3 — a lifecycle reply's job** | `obsolete` returns a **real queued job** (`kind: invalidate`), readable by id; the fixture always returned `job: null` |
| **D4 — the list after a write** | the items **list** reflects the mutation (`status: OBSOLETE`, `revision: 2`); the fixture's list routes did not |
| **The withholding rule needs a row that carries engine text** | a healthy item's `safe_error_summary` is `None` **for the human too**, so the fixture's "text for the human" was an artifact of the fixture. The environment now produces a genuinely failed retain (a transport pointed at a port nothing listens on): the job ends `FAILED`/`engine_unreachable` and the item ends `RETAIN_FAILED`, both carrying `'could not reach the engine: [Errno 61] Connection refused'`. The human/agent pair must be checked on **those** rows |

Still to be settled by the harness: **D2** (the nested command `event` shape against the derivation),
the human/agent withholding pair on the failed rows, the retry invariants on real jobs, and the
dashboard-level pass (the six writes driven through the UI, not by hand).

## 11. Integration rehearsal (synthetic merge; step 3's groundwork)

Step 3 above needs a "final integration commit" that does not exist yet. `scripts/p6/
integration_rehearsal.py` rehearses it in a throwaway clone — the source repository's refs are
never touched, and its `HEAD` and branch list are recorded before and after with a difference
aborting the run. The current rehearsal is
`validation-runs/p6/20261008T062435Z-p6-03-integration-rehearsal/` (source `7016dab`, unchanged).

What it found on the current tips (`p4-integration` `0c4f8f0`, `p6-01` `e570f6b`, onto `main`):

* **P3 is an ancestor of P4**, so the integration set is `main + P4 + P6`.
* **Both merges are conflict-free.** A clean synthetic merge promises nothing about the real one.
* **All three frozen contracts are content-identical at the merged ref.** Only provenance stamps
  differ: `source.ref` for the two P4 contracts, and `source.ref` + `source.commit` +
  `source.schema_version_in_p3` for P3 (which recorded schema 10; the merged baseline is 11). Step 3
  is therefore a **provenance decision**, not a contract re-derivation: record the integration ref,
  or keep the pinned dependency ref beside it.
* **The dashboard suites pass at the merged ref** — `tsc --noEmit` clean, vitest 328/328 — which is
  the strongest signal available here that the P6 slice holds against a baseline that *contains* P3
  and P4, not merely against their pinned commits.
* **Python at the merged ref: 21 failing/erroring assertions across 12 unique tests.** Nine of those
  unique tests fail identically at P4's own tip inside a clone (they need the real repository's
  worktree and native-hook machinery — environmental). **Three appear only when merged**, and they
  are step 3's work list: `test_baseline_core_serves_none_of_the_seven_endpoints` and
  `test_baseline_core_serves_none_of_the_four_endpoints` (whose premise — "the baseline serves none
  of these routes" — inverts once the slices are in), and
  `test_conversation_event_filter_matches_the_core_enum` (the merged baseline's event enum has
  values the P6 comparison did not know). None is a product defect.

**Status of those three, updated.** All three were made ref-relative in the round after this
rehearsal, and the rehearsal was re-run to prove it: the two route tests now assert the fact about
the *pinned baseline* from Git and require the running Core to serve the routes **iff** the
dependency module is in the tree (so they hold before and after integration, and assert more than
before); the enum test keeps the "never name a type Core cannot emit" direction in full and measures
completeness against the **pinned baseline's** enum read from Git, so a later baseline adding types
is an improvement rather than a failure. The re-run's result is in the newer rehearsal bundle.

**The real-Core acceptances run at the merged ref too.** The rehearsal now executes both isolated
acceptances from inside the clone — P4's Core matrix, and P3's Core matrix plus browser pass — with
the Core extracted from the merged commit and the dashboard built from the same tree. Results:
P4 **35/35, 0 failed, 0 blocked**; P3 **23 passed, 0 failed, 3 blocked** (the Context Pack, for the
pinned-tokenizer reason above — that figure is the rehearsal's, before the isolated tokenizer was
installed; §12 has the current 26/26). So at a synthetic integration baseline every claim this plan
makes about behaviour holds, not only the suites.

A wording correction, recorded rather than silently amended: the commit that added the rehearsal
said "12 appear only when merged", conflating failing *assertions* with unique tests. The precise
figures are the ones above and the ones in the bundle's `README.md` and `evidence.json`, which are
generated from the run. The earlier commit is left as written because amending it would change a
ref.

## 12. The real integration branch (step 3, executed)

§11 rehearsed step 3. It has since been executed, on the operator's authorisation, in the branch's
own worktree — the source repository's `main` was never committed to, the original branches are
untouched, and nothing was pushed, no PR was opened and no CI was run.

* **Branch** `integration/p6-03-realcore`, worktree `_worktrees/p6-03-integration`, created from the
  local `main` at `b9985e21f7971079e1a0b3d4efa87ee9b1aede3d` (schema 8).
* **`git merge-base --is-ancestor` confirms P3 is an ancestor of the pinned P4 commit**
  `0c4f8f0e2118711ac9aa2a7006fddc6982962248`, so P3 was not merged a second time.
* **Merged, all conflict-free**: the pinned P4 commit, the P6 tip, and the harness fixes the
  integration itself required (workspace-scan/tokenizer fix; integration-baseline fixes; the two
  fixture-value browser records made Core-relative; the provenance stamp; the tokenizer identity
  row; the re-derived contracts; the P4 UI runner fix).
* **First acceptance commit** `e7f322ae61ea6d95ef7d2057b31ad56c0cc93e6e`; after the two
  independent reviews below, the hardening commit `f323c779bc91893b5908218d8c5c148619159180`
  re-ran the same battery with the same results and is the **current acceptance commit**.

**Contracts re-derived at that commit, with the original source kept.** `source.ref` /
`source.commit` still name the dependency each contract came from (the P4 SHA; P3's branch and commit),
and `source.derived_at_ref` / `derived_at_identical` / `derived_at_differences` record where the
re-derivation ran and whether it changed anything. At the acceptance commit all three are
content-identical to their dependency. That is shown by `--print --derived-at <commit>`
(`identical: true`, `differences: []`, non-stamp content equal) — **not** by
`--check --derived-at <ref>`, which strips the stamp from both sides before comparing and so takes
no account of `<ref>`; an independent review caught that the first wording counted those exit-0
commands as evidence about the ref, and this text no longer does. Plain `--check` (exit 0) is a
dependency-content check. The stamp names the commit *before* the stamp commit because a commit
cannot contain its own SHA; the acceptance commit differs from it only by those three stamp lines.

**Results at that commit**, each frozen in a bundle with `BUNDLE-FROZEN.json` bound to the full SHA:

| Run | Result |
| --- | --- |
| P4 Core matrix (real Core) | **35/35** — the seven reads and the six writes |
| P4 UI pass (same real Core) | **10/10** browser records |
| P3 Core + browser (real Core) | **26/26** (11 Core rows + 15 records), Context Pack built |
| P3 continuity reads vs. the simulated fixture | **15/15** |
| P4 memory reads vs. the simulated fixture (b1) | **11/11** |
| P4 memory writes vs. the simulated fixture (b2) | **16/16** twice, identical steps, plus **4/4** with the switch off |
| Python suite | `Ran 1338 tests` — **not green**: 5 failures + 4 errors, all nine also failing at P4's own tip inside a clone; **0 integration-only** |
| Dashboard | `tsc --noEmit` exit 0; vitest **328/328** |

**`mode: boundary-fake` means real Core, simulated engine boundary.** The Core in the two real-Core
acceptances is real P4 and real P3 code; the engine behind the P4 Core is P4's own
`tests/memory_support.StubHindsightServer`, a wire double on loopback. P4's own Gate requires
`mode: real` **and a real deployment**, so **P4 phase acceptance is not passed**, engine-dependent
facts (extraction quality, `reflect` content) remain unverified, and no real extraction or reflection
is claimed. No Hindsight deployment was added. Restarting P4 phase acceptance is a separate decision,
and a real Hindsight deployment alone would not reopen it: the G01 benefit-verification line was
stopped for its own reasons.

**The nine Python failures are listed, not summarised away**, with each test's name, kind and
message, in
`validation-runs/p6/20261008T075553Z-p6-03-failure-attribution-post-review/` (the earlier
`20261008T074301Z-…` and `20261008T074622Z-…` bundles stay as written). The comparison is not a
clone's word for it: the same nine — compared **class-qualified**, which is stronger than the
method-name comparison the first bundle recorded — were reproduced by running P4's **own** suite in
P4's **own** worktree at `0c4f8f0e…` (`Ran 1155 tests`, 5 failures + 4 errors; set differences empty
in both directions). The cause is how the interpreter is invoked here, not the repository layout:
`scripts/p4-codex-hook-install.py` refuses when the interpreter's parent is not its resolved self,
and `sys.executable` resolves through `/opt/homebrew/opt/python@3.13/bin`. They remain **unfixed and
not passing**; "0 integration-only failures" is a statement about attribution, not a green suite.
See `validation-runs/p6/P6-03-INTEGRATION-REPORT.md`.

**Independent reviews and what they changed.** Two independent subagents reviewed the candidate
after the first battery: evidence binding (bundle manifests, contract provenance, the failure set)
and the harness fixes plus the failure attribution. Their findings and the point-by-point
dispositions are frozen in
`validation-runs/p6/20261008T075620Z-p6-03-independent-review/`. What was acted on in `caeb947`:

* the Context Pack record's marker now prefers the `task_id` slice of the rendered header JSON, so it
  identifies *this* pack rather than any pack from the same renderer (the old rule could degenerate
  to the renderer's constant header), and the rule that ran is recorded;
* the Resume record now asserts the rendered action **count** against the Core's own number, requires
  the JSON block's `<pre>` body to be exactly `[]` for the empty case, records no action kind when
  none is asserted, and requires the Core's own note text on the page;
* the generated evidence no longer contradicts itself: the environment banner describes the
  procedure while a log line and the descriptor state the outcome, the README's BLOCKED branch quotes
  the run's own refusal instead of asserting the tokenizer, and the manifest's dependency sentence is
  derived from the run;
* `freeze_bundle.py` no longer writes a boilerplate `ran_from` that was false for runs made in a
  worktree.

Recorded as open follow-ups rather than fixed here: no unit test covers the P4 UI runner's
`build_dashboard` or encodes the out-of-scope-`args` sweep; no unit test covers the marker
derivation, and its `480`-character window is still a hand-copied duplicate of `RawText.tsx`.

**Documentation sync, and what changed after the acceptance commit.** The status text in this file
and in `web/P6-03A-COMPLIANCE.md`, plus the two follow-up documents `web/P6-REMAINING-TASKS.md` (the
whole-P6 remaining list, including two historical limitations that must not be merged away) and
`web/TASK-p4-interpreter-symlink.md` (the nine failing P4 tests registered as an independent fix
task), are documentation-only. The tree also gained `scripts/p6/bundle_conformance.py` and
`tests/p6/test_bundle_conformance.py` — tooling that renders the taskbook-§8 file names out of
already-frozen bundles into a separate derived directory; it re-runs nothing, writes into no bundle
and changes no verdict, and a complete file set is not acceptance. The exact diff against the
acceptance commit is `git diff --name-only f323c779bc91893b5908218d8c5c148619159180… HEAD`:
documentation and harness paths only, with **no `src/`, schema or `web/` product path** among them, so
the batteries above still bind the same product code and the dashboard results (tsc, 328/328) still
apply unchanged. This text names the acceptance commit rather than the tip, and
states the command rather than the list, on purpose: a commit that named its own tip or its own file
list would go stale the moment it was committed. The earlier `e7f322ae…` battery is a separate
binding with its own bundles, and the harness commit `caeb947` sits between it and the current code —
its battery was re-run, not assumed.

## 13. T05 degraded slice — Memory unavailable, Core and the P1 views still up

The remaining-task list's next behavioural slice, delivered after §12. T05's PASS condition has a half
no earlier run covered — *"Memory 故障不挡 P1 视图,degraded 明确"* — and
`scripts/p6/run_t05_degraded.py` covers it against the same isolated real P4 Core the other
acceptances use, put into P4's own `unreachable` state.

* `p4_realcore_env.py --engine-state unavailable` (the default `wire-double` is unchanged, so every
  earlier run is unaffected): the deliberate retain failure is skipped, the wire double is **stopped**
  after seeding, and **P4's own probe** (`record_engine_probe`) records the refusal. The engine row
  then reports `unreachable` / `degraded: true` / `core_impact: memory_paused` /
  `last_error_code: engine_unreachable` — the state a real outage produces, not an edited status.
* Result, frozen at `0b6f737681eb9a6e7ad0c76b8b4be54409e12ac4` in
  `validation-runs/p6/20261008T123611Z-p6-t05-degraded/`: **12/12** (6 Core checks + 6 browser
  records), 0 blocked. Core health stays `ok`; every P1 read still answers with rows; the Memory job
  and trace lists are not blanked; and the P1 views (Overview, Task, Conversation, Rules, Evidence)
  render with no dependency-pending panel and no dependency-failure alert.
* The honesty case this slice exists to record: `POST /v1/memory/reflect` answers **HTTP 200 with a
  `degraded` payload** (`engine_unreachable`, `non_authoritative: true`, empty basis, every bank
  `failed`). The check calls that a degradation, not a success, and a revision comparison shows no
  Task or Rule change.
* What it does **not** establish: the engine is a stopped loopback wire double, not a real Hindsight
  outage, so `mode: boundary-fake` still means real Core with a simulated engine boundary — no claim
  about Hindsight, extraction or reflection content, and **not** a P4 Gate verdict. P4 phase
  acceptance is not claimed. The retry half of T05 remains covered by the real-Core matrix.
