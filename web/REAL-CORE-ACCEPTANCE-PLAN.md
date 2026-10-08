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
