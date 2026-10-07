# P6-02 compliance (milestone A — read slice, milestone B — audited writes)

Scope: P6-02 as defined by `evidence/p6-rebase-handoff-20261007/NEXT-P6-02.md`.
Milestone **A** is the read slice; milestone **B** is the audited human writes.
Both are described below, and each says what is *not* delivered. Nothing here
claims P6, or even P6-02, is finished: manual reapply and the Task/Step/Evidence
writes are still open, and milestone B has not had an independent review.

Baseline: `850fbec` (P2-01/02/03 merged, schema v8). The P6-01 product baseline
`12fd0ba` is untouched: its bound bundle `index-Bn3SCuxd.js`
(`1047915010c1e2…9d6e58`) is still what that commit builds.

## What milestone A delivers

| Handoff item | Delivery |
| --- | --- |
| 1. Fix the 7 missing P2 event types, verified against the Core enum | `ConversationView.tsx` `EVENT_TYPES` is now all 30 `models.EVENT_TYPES` values; `tests/p6/test_dashboard_proxy_contract.py::test_conversation_event_filter_matches_the_core_enum` parses the TS list and fails on drift in either direction. Raw Events are not modified. |
| 2. Replace the stale "waiting for P2" reason | Conversation, Project and the Pending page no longer say the P2 contract is pending. Conversation links to the delivered reads; Project links its scope; the Pending entry now describes only what is genuinely missing (milestone B writes). |
| 3. Interpretation list/detail, Pending Review list/detail, Resolution preview/detail | `InterpretationsView`, `InterpretationView`, `ReviewsView`, `ReviewView`, `ResolutionView` with routes `#/interpretations`, `#/interpretation/{int}`, `#/reviews`, `#/review/{rvw}`, `#/resolution/{res}`. Every record links back to its source Event, Task, Project and lineage. |
| 4. Proxy stays GET-only and only real paths are added | Six patterns added to `web/server/core-paths.json`, each backed by a real route: `/v1/interpretations`, `/v1/interpretations/{id}`, `/v1/reviews/pending`, `/v1/reviews/{id}`, `/v1/resolutions/preview`, `/v1/resolutions/{id}`. No wildcard prefix, no `/v1/reviews` or `/v1/resolutions` collection, no auth/audit, no external target. |
| 5. Loaded range, status/revision/model/source, preview conflicts, error ≠ empty, scope cancellation | Each list states its read scope and the loaded count; `after_id` paging appends only what Core returned. A preview conflict is rendered as a conflict (`preview_error` on a review row, `409` on a preview) and never as an empty plan. Errors and empty results have separate states, and `useResource`/`usePagedResource` abort and discard the previous scope's responses. |

Required scopes for the slice: `objects:read`, `events:read`, `state:read`,
`authority:read`, `evidence:read`, `interpretations:read`, `reviews:read`,
`resolutions:read`. Core additionally requires `events:read` for the
interpretation endpoints and `interpretations:read` for the review/resolution
ones; the UI only reports what it is refused and never widens its own scopes.

## GET is not a no-side-effect guarantee (verified, not assumed)

The handoff warned that the interpretation/review reads involve
`maintenance.recover`. Two things are now established with a real Core:

1. **Code**: `InterpretationStore._recover` (`interpretations.py:58`) is called by
   `list()` and `get()`; it INSERTs an `INTERRUPTED` result row for any
   interpretation whose `deadline_at` passed without a result.
   `MaintenanceStore.recover` (`interpretation_maintenance.py:35`) is called by
   the interpretation detail, the review detail and the resolution preview; it
   completes a pending rerun and can write
   `interpretation_operation_completions`, `maintenance_heads` and
   `interpretation_changes`.
2. **Runtime**: `scripts/p6/p6_lazy_recovery_probe.py` copies a fixture, inserts
   the crashed-provider precondition (an interpretation whose deadline passed with
   no result row — the chain is append-only by trigger, so this is an INSERT),
   serves that copy itself, then issues a plain
   `GET /v1/interpretations?limit=50`. Observed:
   `interpretation_results` rows 8 → 9 with
   `status=INTERRUPTED, error_code=provider_interrupted`.

So P6-02 states plainly: **the dashboard sends only GET, but these endpoints can
write.** The UI shows the recovered status as what Core already decided; it never
presents the recovery as a user action and never treats a read as a promise that
nothing changed. The in-page notes say the same thing next to the data.

On an intact fixture with no expired or pending operation, the before/after
digests of every P2 table are identical; `audit_log` grows, because Core audits
every request including reads. The Gate reports these separately:
`p2_truth_writes` (must be empty) and `audit_log_grew` (expected).

## API shapes this UI depends on (frozen, schema v8)

* `GET /v1/interpretations` — filters `event_id`, `project_id`, `task_id`,
  `status`, `after_id`, `limit`; returns `{items, next_after_id}` with **no
  count**. `InterpretationStore.list` limits `limit` to 1..200 and orders by
  `(created_at, interpretation_id)`.
* `GET /v1/interpretations/{int}` — one record plus `maintenance_head`,
  `maintenance_history`, `operation_completions`.
* `GET /v1/reviews/pending` — filters `project_id`, `task_id`, `after_id`,
  `limit`; only `PENDING` rows; `{items, next_after_id}`, no count.
* `GET /v1/reviews/{rvw}` — one review with its resolution, its change history
  and either `preview` or `preview_error`.
* `GET /v1/resolutions/preview?interpretation_id={int}` — `plan`,
  `expected_revisions`, `expected_context_digest`, `maintenance_head`,
  `source_application`; refuses with a conflict when the interpretation is not
  current or not ready.
* `GET /v1/resolutions/{res}` — the persisted decision with `result.actions`,
  `result.reasons`, `result.disposition`, `source_bindings` and
  `manual_application_history`.

Enums rendered with a glyph plus their literal token, unknown values verbatim:
interpretation `PROCESSING|EXTRACTED|FAILED|INTERRUPTED`; disposition
`APPLIED|PENDING_REVIEW|NO_ACTION|REJECTED|SOURCE_REPLAYED`; review
`PENDING|APPROVED|REJECTED|SOURCE_REPLAYED`; plan action
`CREATE_RULE|ATTACH_TASK|CREATE_TASK|NO_ACTION`.

## Verification of this slice

`validation-runs/p6/` — the newest `*-p6-02a-read/` run (its `manifest.json` names
the commit it binds; earlier `*-p6-02-read/` directories were taken before the
commit and are superseded). Isolated P2 fixture
(`scripts/p6/p6_p2_fixture.py`: the P1 scenario plus real P2 rows produced by the
real pipeline with a component-simulation provider, clearly labelled as not a
model run), dashboard `:5183` over Core `:8795`, read token `p2_read`:

* real browser trace **32/32**, including: the event filter offers all 31 options
  (30 types + placeholder) with none missing; the interpretation list states its
  loaded range and no total; a stale interpretation renders the preview conflict
  rather than an empty plan; the resolution preview shows the server's
  `PENDING_REVIEW`/`CREATE_RULE` plan with the pending badge; the review queue
  lists Core's pending review and offers no approve button; the resolution detail
  shows disposition, reasons and its interpretation;
* `truth_changed_reads: []` after normalising the per-response `request_id`;
* `p2_truth_writes: []`, `audit_log_grew: true`;
* plus the separate lazy-recovery probe above, which deliberately produces the
  one write a read can perform.

Component and contract tests: web tests in 13 files (107 at the milestone-B tip,
up from 80 in 11 at the milestone-A verdict because milestone B added
`writeAction`, `writeBodies` and the Rule surface); 8 Python contract tests
(`tests/p6`), which also assert that the allowlist never covers `/v1/reviews` or
`/v1/resolutions` as collections, that it does cover exactly the four write
shapes, and that the Conversation event filter matches `models.EVENT_TYPES` in
both directions.

## Independent review and validation (milestone A)

- Review of `cef1cdd` raised two blockers, both fixed before this note: the
  `OperationCompletion` type (and two sibling sets) named fields that do not exist
  — the real columns are `processing_status`, `head_applied`,
  `expected_head_revision`, `observed_head_revision`, `result_json`, and
  `MaintenanceChange`/`ResolutionAction` use
  `parent_interpretation_id`/`child_interpretation_id` and `action`/`target_type`.
  The fixture now performs a **real rerun** so a genuine completion row exists to
  render and test, and `p6Views`-level tests pin it. The second blocker was this
  evidence generator's own metadata (P1 scopes, P6-01 templates, a citation to a
  pre-commit run); `scripts/p6/p6_verify.py` now derives the scopes and fixture
  name from the fixture descriptor and emits a P6-02 walkthrough and Gate mapping.
- Validation of `cef1cdd`/`daa618a` returned PASS on build/tests, the six read
  paths, under-scoped-token refusal (403 shown as `forbidden`, no fallback), the
  32-step browser trace, and the no-claim-falsified checks; it independently
  reproduced the lazy-recovery write on its own DB copy and re-verified the
  target-refusal fix on servers it started itself.
- Its one broader observation is now in this bundle's accounting: a plain
  authenticated GET also advances `api_keys.last_used_at`, so the read-slice
  digests are reported as `p2_truth_writes` (must be empty) versus
  `p2_bookkeeping_changes` (`audit_log`, `api_keys` — expected).

## Milestone B — audited human modification

Seven writes are shipped. The proxy forward list is what bounds them
(`web/server/core-paths.json` `write_methods: ["POST"]`), and Core still enforces
scope, actor kind, revision, idempotency and source per request — the browser can
only *ask*.

| Action | Method + path | Scope | Replay key | `reason` | `expected_revision` |
| --- | --- | --- | --- | --- | --- |
| Correct | `POST /v1/interpretations/{int}/correct` | `interpretations:manage` | `idempotency_key` | required | required |
| Reject | `POST /v1/interpretations/{int}/reject` | `interpretations:manage` | `idempotency_key` | required | required |
| Re-run | `POST /v1/interpretations/{int}/rerun` | `interpretations:manage` + `interpretations:process` | `idempotency_key` | required | required |
| Approve review | `POST /v1/reviews/{rvw}/approve` | `reviews:manage` | `idempotency_key` | required | required |
| Reject review | `POST /v1/reviews/{rvw}/reject` | `reviews:manage` | `idempotency_key` | required | required |
| Propose Rule | `POST /v1/rules/proposals` | `authority:propose` | `event_id` | **rejected** | **rejected** |
| Approve / Supersede / Retire Rule | `POST /v1/rules/{rul}/(approve\|supersede\|retire)` | `authority:manage` | `event_id` | **rejected** | required |

The two families are genuinely different, and Core enforces it with `_fields(body,
allowed)`: `AuthorityStore.propose`/`transition` answer an extra `reason` with
`invalid_request: unknown fields`, and their replay key is a client-minted
`event_id` (`_replay`) — `idempotency_key` is itself an unknown field. Creation
additionally refuses `expected_revision` (`UnexpectedExpectedRevision`). So
`useWriteAction` carries the key *kind* and `WritePanel` has a `reasonMode="none"`
that hides the reason input rather than offering a field Core would refuse.
`web/tests/writeBodies.test.tsx` asserts the exact key set of every write the UI
can send, so a blanket "add reason/idempotency_key everywhere" change fails there
instead of in Core.

`src/lib/ids.ts` mints the `evt_` id in Core's exact shape
(`<prefix>_<26 Crockford base32>`), which ADR 0001 sanctions for exactly this:
*"An `evt` body is opaque: clients may derive it deterministically for idempotent
retries."* The id is minted once per intent and reused on every retry, so a retry
after a timeout is a replay rather than a second command.

Four things the panels refuse to do:

* **invent a plan.** `Approve` is only offered when Core returns a `preview`; the
  actions, `expected_revisions` and `expected_context_digest` are read from the
  server and sent back verbatim. When the preview fails the panel says so
  (`data-testid="approve-unavailable"`) instead of opening a form;
* **retry a conflict.** A `409`/`revision_conflict`/`interpretation_not_current`/
  `idempotency_conflict`/`event_id_conflict` keeps the draft and shows Core's own
  current value; nothing re-submits with a new revision;
* **turn an unknown into a success.** A timeout or transport failure is its own
  state, with the request id and a read-back instruction, never a silent retry;
* **widen its own authority.** A refusal names the missing scope and offers no
  alternative credential, and no write panel can reach `/v1/auth/*`, `/v1/audit`
  or `/v1/evidence/confirm`.

## How milestone B was verified

`scripts/p6/p6_t03_gate.py`, run against the isolated fixture with the dashboard
proxy as the endpoint under test:

* all seven actions succeed through the proxy with the fixture's real human key,
  each recorded as method · path · scope · actor kind · host · source Event ·
  replay key · expected/observed revision · Core's answer · object readback;
* the negatives: a read-only token is refused `403 forbidden_scope` with no state
  change, an agent actor is refused, a stale `expected_revision` is `409
  revision_conflict` with nothing changed, the same replay key with the same body
  returns `replayed: true` and creates no second Rule, and the same key with a
  different body is `event_id_conflict`;
* `reject`, `rerun` and `review.reject` are measured against a per-table sha256
  digest of `projects`/`tasks`/`steps` (the state the Task board renders) taken
  before and after, so "leaves Truth alone" is a measurement, not an assertion;
  `review.approve` is checked the other way round — that it really did bind a plan
  to its source Event;
* `audit_log` grew for every attempt, including the refusals, carrying the actor,
  method, path, decision, status and error code.

Two limits are stated rather than glossed over. The audit readback is a
read-only SQLite read of the fixture's `audit_log`: no fixture token carries
`admin` and the proxy has no `/v1/audit` path, so **the browser cannot read audit
at all**, and the report says so instead of implying the UI displayed it. And the
`rerun` path exercises the fixture's `component_simulation` provider — it proves
the audited command path, not a real model call.

## Not delivered (still open in P6-02)

`POST /v1/resolutions/{res}/manual-reapply`, and the Task/Step/Evidence writes.
The first needs its own frozen payload/permission table and an explicit operator
decision; the second needs a Gate of its own, and `evidence/confirm` additionally
needs a human prompt the browser cannot mint (ADR 0007). `browser_trace.mjs`
asserts the absence of any Evidence-confirm control.

## A packaging defect found after the first milestone-B evidence run

`web/src/lib/` was never in a commit. The root `.gitignore` carries Python's
`lib/` pattern, and gitignore patterns are path-agnostic, so it also matched the
dashboard's own `web/src/lib/` — `classNames.ts`, `text.ts`, `useMaskedText.ts`
and (since milestone B) `ids.ts`, all imported by committed code.

This is worse than a missing file, because it made the *frozen-commit claim*
untrue without any check being able to notice: the tests, the browser trace and
the Gate all ran in a working tree that happened to contain those files, so
everything passed while a clean checkout of the same commit did not compile. It
was found by running `git worktree add --detach <commit>` — a checkout with no
untracked carry-over — and watching `tsc` fail with `Cannot find module
'../lib/text'`. The same was true of the milestone-A verdict commit (`693afe6`)
and of the P6-01 baseline, so the r2 and r3 verdicts are bound to commits that
are complete only together with a working tree.

The fix is a `!web/src/lib/` negation rather than a rename, so the layout the
reviewed commits used is preserved, and every evidence run from now on includes
`clean-checkout.txt`: `tsc`, `vitest` and `vite build` executed inside a fresh
worktree of the exact commit. The milestone-B bundle carries one.

## Status

`P6-02 milestone A: delivered, independently reviewed and validated` (the r2
verdict binds `693afe6`; see the packaging caveat above — that commit is complete
only together with its working tree). `P6-02 milestone B: implemented and gated
by the implementer` — the T03 Gate above passes all seven actions and every
negative on an isolated fixture, and the tip now builds from a clean checkout, but
milestone B **has not** had an independent reviewer or validator. Two review
attempts against `b834f53` were stopped once the packaging defect was found, since
a verdict on a commit that cannot be checked out is not worth having. `P6-02: PARTIAL` as a whole; manual reapply and the Task/Step/Evidence
writes are not delivered, and no Gate has run against the operator's own Core or a
real captured conversation. `P6` as a stage is not complete and nothing here
releases V1.
