# P6-03a compliance — read-only Checkpoint / Context Pack / Resume views

Status: **PARTIAL / DEPENDENCY_PENDING.** P6-03a is not complete, P6 is not complete, and
nothing here releases V1.

Scope: read-only views for the P3 continuity slice — a Checkpoint, a Task's latest
Checkpoint, a Context Pack, and a Resume. No P3 write, no P6-03b, and no P4 work of any
kind: the P4 benefit experiments remain stopped.

## The dependency this milestone is named after

P3 is **not merged**. The contract these views are built against comes from the unmerged
branch `p3-checkpoint-context-resume`, commit
`7016dabb8926d6bd6599f1f56b8f39cbda253ebf` (schema 10). The baseline this dashboard
serves, `850fbec` (schema 8), has no `context/`, no `continuity.py`, no
`task_snapshot.py`, and **no route for any of the four endpoints**:

| View | Method and path | Scopes required by P3 | Response key |
| --- | --- | --- | --- |
| Checkpoint (by id) | `GET /v1/checkpoints/{ckp}` | `checkpoint:read` + the five shared | `checkpoint` |
| Latest Checkpoint | `GET /v1/tasks/{tsk}/checkpoints/latest?session_id=` | `checkpoint:read` + the five shared | `checkpoint` |
| Context Pack | `GET /v1/context/{ctx}` | `context:read` **and** `checkpoint:read` + the five shared | `context` |
| Resume | `GET /v1/resumes/{rms}` | `resume:read` **and** `checkpoint:read` + the five shared | `resume` |

The **five shared** scopes are `objects:read`, `state:read`, `authority:read`,
`evidence:read`, `events:read` — every one of the four runs P3's `_continuity_read`. None of
this is guesswork: `scripts/p6/p3_contract.py` parses each handler's own
`principal.require(...)` calls, including the ones it reaches through helpers, and the
frozen contract records the result in `scope` + `extra_scopes`. The route table alone names
one scope per route and would have under-stated every one of them, which is precisely the
defect an independent review found in the first cut of this contract.

### The taskbook's scope list is a subset of this, deliberately

The P6-03a taskbook words the endpoint scopes as `context:read`, `checkpoint:read`, and
`resume:read + checkpoint:read`. The frozen contract records **more than that**, because the
taskbook list names only the *slice-specific* scope of each route while P3's handlers also
require the five shared scopes:

| Route | Taskbook wording | Contract (derived from P3) |
| --- | --- | --- |
| `GET /v1/context/{ctx}` | `context:read` | `context:read`, `checkpoint:read`, + five shared |
| `GET /v1/tasks/{tsk}/checkpoints/latest` | `checkpoint:read` | `checkpoint:read` + five shared |
| `GET /v1/checkpoints/{ckp}` | `checkpoint:read` | `checkpoint:read` + five shared |
| `GET /v1/resumes/{rms}` | `resume:read + checkpoint:read` | `resume:read`, `checkpoint:read`, + five shared |

This divergence is **intentional and settled**: the operator decided to keep the full derived
set, on the grounds that the UI must reflect what Core actually requires, not what a summary
of it omits. The taskbook's wording is recorded here so the omission is visible rather than
silently corrected — a future reader comparing the two should find this table, not a puzzle.

The practical consequence is that a token carrying exactly the taskbook's scopes is refused
by real P3 (and by the labelled fixture, which enforces the contract). Granting only
`context:read` for a Context Pack read does not work.

So the four views are delivered, wired, and exercised — against a **simulated fixture**,
with the missing dependency marked prominently in the product itself. That is the honest
maximum for this milestone without P3.

### How the contract is pinned

`scripts/p6/p3_contract.py` derives the contract **from the P3 branch**, not from
documentation: routes, handler names and route-table scopes come from P3's own route table;
every *other* scope a handler requires is parsed from that handler's own
`principal.require(...)` calls, following the helpers it calls (`_continuity_read` adds
five, and a context pack or a resume also reaches `checkpoint:read`) — the route table alone
under-states the requirement. The response field sets are read with `ast` from the dict
literals P3 builds, with the `**rendering` spread resolved against the renderer's
`render()`; the Checkpoint set is the exception, taken from the columns of the
`CREATE TABLE checkpoints` statement in P3's `m0009_continuity.py`, because a checkpoint is
a stored row rather than a dict literal. The result is frozen as
`contracts/p3-continuity-read.v1.json` with `source.commit 7016dabb8926` and
`baseline.these_routes_exist: false`.

`tests/p6/test_p3_contract_snapshot.py` re-derives the contract from the branch and fails
on any drift, and separately asserts that the TypeScript interfaces carry exactly the frozen
fields plus that each route records every scope its handler requires. Where the P3 ref is
absent that first guard **skips and the suite still exits 0** — a skip is reported, not a
failure, so a checkout without the branch passes with one test skipped and the rest still
running. That is a real limitation of the guard, not a design win: it means the branch
comparison is only as good as the checkout. Deriving the contract from prose would have let
the docs and the code drift together; deriving it from P3's own source does not.

Derived shapes: **19** Checkpoint fields, **17** Resume fields, **31** Context Pack fields
(23 of its own plus the 8 renderer keys that `**rendering` spreads in).

## What is proven, and what is not

Proven, in a real browser (`validation-runs/p6/<stamp>-p6-03a-clickthrough/`):

* a Checkpoint renders as a snapshot of an immutable revision, with its projection,
  context and workspace digests read as separate rows;
* a Task with no Checkpoint renders as **empty**, not as an error — P3 answers `200` with
  `{"checkpoint": null}`, and the view must not confuse "no checkpoint" with "not found";
* an **unknown** (valid-shaped) id is Core's `not_found`, and the view does **not** show
  the missing-dependency panel;
* a **malformed** id is refused locally, with the right reason, and **no request** is sent;
* a Context Pack shows what was rendered, and marks P4-provided memory as not authoritative;
* a Resume is a **comparison, not a restore**, and its `note` is historical with
  `authority = false`;
* switching objects leaves no previous content behind;
* a token missing a scope is refused by **Core** (`forbidden_scope`), which is a different
  state from the missing dependency.

Not proven, and not claimed:

* that P3 works, that P3 has been verified, or that P3's read slice is correct — no real
  P3 code answered any of this;
* that the views render every real-world P3 payload. The fixture serves fixed payloads
  whose field sets are asserted against the frozen contract before serving;
* anything about the four POST routes (`/v1/context/build`, `/v1/context/{id}/check-current`,
  `POST /v1/checkpoints`, `POST /v1/tasks/{id}/resume`). They are build/write operations
  and are outside this slice; the proxy refuses them.

## Why the missing dependency cannot be mistaken for an error

Three distinct states are kept apart, and each is separately tested:

| State | What the user sees | How it is detected |
| --- | --- | --- |
| P3 not available | `dependency-pending` panel naming the method, path, scope and source ref | proxy error code `proxy_path_not_allowed` **only** |
| Core refuses this caller | the real `forbidden_scope` error | Core's own error body |
| The object does not exist | `not_found` | HTTP 404 from Core |

`isDependencyPending` keys on `proxy_path_not_allowed` alone. If it also treated Core's
`not_found` as "P3 missing", a genuinely deleted checkpoint would be reported as an
unmerged dependency; if it treated `forbidden_scope` that way, a permission problem would be
blamed on a missing branch. Both are covered by tests, and the tests are mutually
falsifying: widening the check to Core's `not_found` fails the 404 cases, widening it to the
403 code fails the refusal case, and narrowing it breaks the pending cases. That
`not_found` direction was **missing at first** — only the 403 side was covered, so widening
the check passed the whole suite. The gap was found by review and is now closed; the
falsification was re-run and fails as it should.

A malformed id deserves its own note. The proxy's allowlist spells ids as
`<prefix>_<26 Crockford chars>`, so a typo would be refused **by the proxy** and reported as
`proxy_path_not_allowed` — i.e. as "P3 is not merged", which is a wrong explanation. The
views therefore validate the id shape themselves (`isCoreId`) and refuse it locally, naming
the bad field, before any request is made. A wrong explanation is worse than none.

## The fixture is simulated, and says so

`scripts/p6/p3_read_fixture.py` is **not** a Jasmine Core and **not** a P3 verification
harness. It exists because P3 is unmerged and there is nothing real to serve. It:

* asserts every payload's field set equals the frozen contract **before serving**, so a
  drifted fixture fails loudly at startup instead of producing misleading evidence;
* stamps `X-P6-Fixture: simulated-p3-reads` on every reply, and prints a banner on startup;
* keeps its ids recognisably fake and documents the constant that means "unknown".

The label is evidence, not decoration: `browser-requests.jsonl` records `fixtureLabel` for
every response, and one Gate check requires all continuity reads to carry it. That check
was **falsified before it was trusted** — with the header suppressed it is the only
failure (7 reads, 7 unlabelled) while the other eleven records still pass. The
falsification run is preserved in the bundle directory
`<stamp>-p6-03a-clickthrough-label-falsification/`.

## The proxy keeps the baseline unchanged

`server/core-paths.json` gains `p3_pending_paths` (the four strict-id patterns) and an
explanatory `p3_pending_note`, while the live `paths` list stays at **25** entries. The
pending paths are appended only when `P6_ENABLE_P3_READS=1` is set:

| `P6_ENABLE_P3_READS` | Behaviour |
| --- | --- |
| unset / `0` | baseline behaviour, unchanged: the four paths are refused by name, so the browser never reaches a Core that cannot answer |
| `1` | the four paths are forwarded; Core still enforces `context:read` / `checkpoint:read` / `resume:read` itself |

`tests/p6/test_p3_contract_snapshot.py` asserts the pending patterns are **absent** from the
live allowlist, so enabling them cannot happen by drift or by a careless edit to `paths`.

### The live entry stays closed until P3 lands — deliberately

The operator's decision is to keep this entrance shut, and it is **not** unlocked by the
existence of this switch. `P6_ENABLE_P3_READS` is a Gate-time affordance for driving the
views against the labelled fixture; it is not a production readiness flag.

The order for opening it is fixed, and each step is a precondition for the next:

1. P3 is merged into the target baseline this dashboard is served against;
2. the contract is **re-derived** from that merged code (`scripts/p6/p3_contract.py --write`)
   and the drift guard run against the merged baseline rather than the branch;
3. the four reads are independently validated against a **real Core** with a real token —
   not the fixture — including the scope refusals and the empty-checkpoint case;
4. only then is `P6_ENABLE_P3_READS` treated as a normal configuration option.

Until step 3 has actually happened, any claim that these views work against real P3 is
unverified. Today they are exercised against a fixture whose payloads are *shaped* like P3's
and whose values are not P3's, which is a different and much weaker statement.

## Historical failure: `faf6800` must not be used as an acceptance candidate

Commit `faf6800` is **broken** and is retained only as failure history. It was created by
accident when a `git add -A` swept in an independent reviewer's in-flight falsification edit —
`isDependencyPending` left as `error.code === PROXY_PATH_REFUSED && false` — so that commit's
tree fails four tests. It is reverted in `61a96af`.

Consequences, stated plainly so nobody has to rediscover them:

* do not validate, cite, or offer `faf6800` as an acceptance candidate;
* the verdicts recorded for P6-03a bind the current tip and the frozen bundle's commit, not
  that one;
* the process correction is to stage **explicit paths** rather than `git add -A` while an
  independent reviewer may be editing the same worktree. The commit that follows `faf6800`
  was made that way, and every commit since has been.

This is recorded in a *tracked* file on purpose: the evidence index and bundle READMEs that
also mention it live under `validation-runs/`, which is gitignored.

## Evidence

* Browser click-through Gate, **15/15**, three times with identical step sequences, run
  from an isolated `git archive` of the commit with its own fixture, proxy and ports:
  `validation-runs/p6/<stamp>-p6-03a-clickthrough/` (+ `-stability1`, `-stability2`), with
  `README.md`, `BUNDLE-FROZEN.json`, `click-trace.jsonl`, `browser-requests.jsonl` and
  screenshots `21`–`24`. The current binding and the superseded bundles are registered in
  `validation-runs/p6/P6-02B-INDEX.md`.
* The product asset was rebuilt from committed source and compared file for file against the
  tree the runs served, so the evidence comes from the commit and not from a stale build.
* Component tests: `web/tests/p3Views.test.tsx` (27), `web/tests/p3Reads.test.ts` (15),
  `web/tests/p3ReadsCoreProxy.test.ts` (9) — the last covers the Vite-plugin host, which had
  no test until review pointed out that only `serve.mjs` was gated by one.
* Contract tests: `tests/p6/test_p3_contract_snapshot.py` (16),
  `tests/p6/test_p3_read_fixture.py` (16) — 32 in total, including two that pin the scope
  *order* Core would report (not only the set) and one that reads P3's own source to pin the
  nested key sets.

## What independent review changed

The first cut of this milestone was reviewed adversarially, and the review was right about
four things worth recording rather than quietly fixing:

* **The contract under-stated the scope set.** It recorded only the route-table scope; a
  token carrying `context:read` alone was accepted by the fixture and refused by real P3. The
  full set is now derived from each handler's own `principal.require(...)` calls, and
  reverting it fails three independent tests. This was a real defect in the deliverable, not
  a documentation slip.
* **Then the order was wrong too.** A second pass found the derivation collected a helper's
  own literals *before* descending into the helpers it calls, which reversed Core's
  execution order for `_context_read` (it runs `_continuity_read` first, then requires
  `checkpoint:read`). The scope *sets* were right and the *reported* scope was not: a token
  missing several scopes was told to grant `checkpoint:read` where Core names
  `objects:read`. The traversal now descends into a helper where the call appears, two tests
  pin the order, and reverting the traversal fails three of them.
* **A guard that could not fail.** Widening `isDependencyPending` to treat Core's `not_found`
  as the missing dependency passed the entire suite. Per-view 404 tests plus a direct
  unit test now fail it.
* **Two documentation claims were stronger than the code.** The drift guard *does* exit 0
  when the P3 ref is absent (it skips), and the Checkpoint field set comes from a
  `CREATE TABLE`, not from a dict literal. Both statements are corrected above.
* **The fixture invented nested shapes.** `next_actions` was served as strings where P3
  returns objects, so the "lists next_actions" evidence exercised a branch real P3 never
  reaches. `section_metrics` and `omissions` were invented too. The fixture and the view now
  use P3's real key sets for everything the UI renders, and a test pins each mirrored key set
  to P3's source (`continuity.next_actions`, `continuity._compare_checkpoint`,
  `context.renderer.render`). That test reads P3's source through git rather than comparing
  to a hardcoded copy, and skips loudly where the P3 ref is absent — a claim the review
  correctly rejected when the test was only checking literals. A nested shape this build does
  not recognise renders as JSON rather than being dropped. Values remain the fixture's — it
  has no Truth store, and this document says so rather than letting the distinction blur.

A third pass found the pages said PARTIAL / DEPENDENCY_PENDING only through the panel shown
when the proxy *refuses* the path — so against a P3-enabled proxy they rendered a populated
record with no standing marker, and a screenshot of that state invited the "P6 passed" reading
this milestone forbids. Each view now carries a `slice-status` line stating the status and the
unmerged source commit whenever a record renders — so the status is never absent on a
populated read, and on a refused one the dependency panel states it instead.

That pass also found the view over-refusing: a malformed `task_id` beside a valid
`checkpoint_id` blocked the record, even though the by-id read never uses `task_id`. A
malformed id now blocks only when the read that will run actually needs it; the others are
reported as ignored rather than dropped in silence.

## Deliverables

| File | Role |
| --- | --- |
| `contracts/p3-continuity-read.v1.json` | the frozen P3 read contract, with its source commit |
| `src/views/ContinuityViews.tsx` | the four read-only views, the dependency-pending panel, and local id refusal |
| `src/api/types.ts` | the P3 response types, in a section that names the dependency |
| `src/api/endpoints.ts`, `src/api/errors.ts` | the four reads; `proxy_path_not_allowed` and `isDependencyPending` |
| `src/lib/ids.ts` | `isCoreId`, so a malformed id is not blamed on P3 |
| `server/core-paths.json`, `server/serve.mjs`, `server/core-proxy.ts` | the pending paths, and the flag that enables them |
| `scripts/p6/p3_contract.py`, `scripts/p6/p3_read_fixture.py`, `scripts/p6/browser_trace.mjs` | derive the contract; serve the simulated reads; drive the Gate |

## Not delivered

* P6-03b, and anything that depends on the P4 memory provider beyond rendering its
  non-authoritative output.
* Any P3 write, and any view for the four POST routes.
* **The Context Pack read against real P3 — this item was delivered later; see "Real-P3 verification"
  below.** As of the first real-P3 run (`validation-runs/p6/20261008T063605Z-p6-03a-realcore/`,
  23 passed / 0 failed / 3 blocked) the other three routes were verified against an isolated real P3
  Core, but P3's `ContextBuilder` requires its pinned tokenizer (`tiktoken` plus a BPE asset whose
  SHA-256 `context/tokens.py` fixes). Neither was present in that environment, so **no Context Pack
  row existed** and those three checks were recorded **BLOCKED** with that reason. Nothing was
  fabricated in its place. That bundle remains the record of what was true then; the current status
  is the 26/26 result below.
* Verification against a v8 database migrated to schema 10.
* Any change to `src/`, the schema, or the existing services. P6-03a touches `web/`,
  `tests/p6/` and `scripts/p6/` only.

## Real-P3 verification (added after this slice was delivered)

`scripts/p6/p3_realcore_env.py` starts an **isolated real P3 Core** at the pinned commit: its own
tree, database and loopback port, checkpoints and resumes created by P3's own `ContinuityStore`,
keys minted by P3's `auth.issue_key`. `scripts/p6/run_p3_realcore.py` runs a Core-level matrix and
then the browser pass (`browser_trace.mjs --p3-reads --real-core`) against it, and freezes a bundle.

Result, **first run — historical, retained as written**: **23 passed, 0 failed, 3 blocked**
(`20261008T063605Z-p6-03a-realcore/`), the three blocked rows being the Context Pack. Verified for
real then: checkpoint by id and `latest`, the empty Task, the resume by id, the resume note P3
builds from the checkpoint's own note, switching between two real checkpoints, Core's own
`not_found` and `invalid_request`, both scope refusals (the missing route scope, and the
`checkpoint:read` P3's handler adds on top of the route table), no simulation label in the traffic,
and `/v1/health` reporting P3's schema.

Result, **current**: **26 passed, 0 failed, 0 blocked** — 11 Core rows + 15 browser records —
(`20261008T072010Z-p6-03a-realcore/`, frozen at the P6-03 integration commit
`e7f322ae61ea6d95ef7d2057b31ad56c0cc93e6e`). The operator authorised installing the pinned tokenizer
into an **isolated** toolchain (`validation-runs/p6/realcore-toolchain/`) and the workspace scan was
fixed, so P3's own `ContextBuilder` now **builds the pack in this environment**: `tiktoken` installed
`0.14.0` = `context/tokens.py`'s `LIBRARY_VERSION`, and the fixed BPE asset
`fb374d419588a4632f3f557e76b4b70aebbca790` (3,613,922 bytes) hashes to
`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` = its `ASSET_SHA256`; encoding
`o200k_base`. **No global Python and no tokenizer contract was changed**, and the comparison is
recorded as row `C9.tokenizer_identity` in the bundle's own `evidence.json`. The workspace scan in
these rows is P3's own `scan_workspace` over an isolated git workspace
(`mode.workspace_scan: real (isolated git workspace)`), not the earlier component stand-in. The
browser record asserts a literal slice of *that pack's* `rendered_content` is on screen — the
earlier record asserted the fixture's task title, which a real Core never renders — and that its
Resume renders `next_actions: []` for a comparison that needs no reconciliation.

What that still does not establish: anything about the P3 Gate's own acceptance (its own runner
needs a real deployment of the native lifecycle), and P4 phase acceptance — the P6-03 integration
commit merges the pinned P4 code, but the engine behind the P4 Core in those runs is a wire double
(`mode: boundary-fake`, i.e. real Core, simulated engine boundary), so no real extraction or
reflection is claimed. The integration re-derivation of the contracts has since been executed; see
`web/REAL-CORE-ACCEPTANCE-PLAN.md` §11 and `validation-runs/p6/P6-03-INTEGRATION-REPORT.md`.

## Standing status

`P6-03a: PARTIAL / DEPENDENCY_PENDING` — the views are delivered and exercised against real P3 code
for **all four routes** (26/26 at the P6-03 integration commit). The Context Pack is no longer
blocked on its tokenizer; what remains pending is the dependency's own acceptance, not this slice's
coverage. The missing dependency is still unmistakable in the product, the proxy, the
contract and this document. **P3 phase acceptance is not claimed, P4 phase acceptance is not
claimed, and P6 is not complete.** P6-02 remains PARTIAL for its own reasons; see
`P6-02-COMPLIANCE.md`.

## Documentation sync (this section's commit)

This document and `web/REAL-CORE-ACCEPTANCE-PLAN.md` were synced to the current real-P3 result in a
**documentation-only** commit on top of the acceptance commit `e7f322ae61ea6d95ef7d2057b31ad56c0cc93e6e`.
The code those acceptance bundles bind is unchanged: the only paths the sync commit touches are
these two Markdown files. That is checkable with `git diff --name-only e7f322ae… <sync tip>`, which
lists `web/P6-03A-COMPLIANCE.md` and `web/REAL-CORE-ACCEPTANCE-PLAN.md` and nothing else, so the
Gate results above still describe the same code. The earlier `23/0/3` text is kept above as the
record of what was true when that bundle was written, rather than edited away.
