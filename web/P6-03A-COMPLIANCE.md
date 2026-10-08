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

## Evidence

* Browser click-through Gate, **12/12**, three times with identical step sequences:
  `validation-runs/p6/<stamp>-p6-03a-clickthrough/` (+ `-stability1`, `-stability2`), with
  `README.md`, `BUNDLE-FROZEN.json`, `click-trace.jsonl`, `browser-requests.jsonl` and
  screenshots `21`–`24`.
* The product asset was rebuilt from committed source and hash-compared to the tree the
  runs served, so the evidence comes from the commit and not from a stale build.
* Component tests: `web/tests/p3Views.test.tsx` (11), `web/tests/p3Reads.test.ts` (5).
* Contract tests: `tests/p6/test_p3_contract_snapshot.py` (14),
  `tests/p6/test_p3_read_fixture.py` (10).

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
* Verification against real P3, or against a v8 database migrated to schema 10.
* Any change to `src/`, the schema, or the existing services. P6-03a touches `web/`,
  `tests/p6/` and `scripts/p6/` only.

## Standing status

`P6-03a: PARTIAL / DEPENDENCY_PENDING` — the views are delivered and exercised against a
labelled simulated fixture, and the missing dependency is unmistakable in the product, the
proxy, the contract and this document. **This is not P3 verification and not P6
completion.** P6-02 remains PARTIAL for its own reasons; see `P6-02-COMPLIANCE.md`.
