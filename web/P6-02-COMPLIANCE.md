# P6-02 compliance (milestone A — read slice)

Scope: the read half of P6-02 as defined by
`evidence/p6-rebase-handoff-20261007/NEXT-P6-02.md`. Milestone **B** (audited
human writes) is **not** delivered and nothing here claims it.

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

Component and contract tests: 80 web tests in 11 files; 7 Python contract tests
(`tests/p6`), which also assert that the allowlist never covers `/v1/reviews` or
`/v1/resolutions` as collections, and that the Conversation event filter matches
`models.EVENT_TYPES` in both directions.

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

## Not delivered (milestone B)

Correct / Reject / Re-run, review approve/reject, manual reapply, and Rule
management. Each needs a frozen per-endpoint payload/permission table, a real
human actor and provenance chain, `expected_revision` conflict handling,
idempotent command Events with audit readback, and an independent permission Gate.
No write control exists in the UI today, and the panels that mention these actions
say they are not delivered.

## Status

`P6-02 milestone A: delivered and verified on an isolated fixture` (implementer
evidence). `P6-02: PARTIAL` as a whole — milestone B is not started, and an
independent reviewer/validator has yet to confirm this slice. `P6` as a stage is
not complete and nothing here releases V1.
