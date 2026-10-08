/**
 * Frozen Core API response types.
 *
 * Every field here was copied from a real response body captured from the
 * P1-scope Core build (schema v6, `docs/api/core-api-v1.md` + ADR 0001–0007).
 * Nothing is guessed and nothing is widened to `any`: when the contract gains a
 * field, this file is updated by the API owner's change, not by a cast.
 *
 * Two deliberate habits:
 * - Status vocabularies are `string`, not closed unions. A Core that adds a
 *   status must render as itself, so views switch on known values and print
 *   anything else verbatim.
 * - `payload` stays `unknown`-shaped. Raw Event text is displayed, never
 *   interpreted as HTML or as a trusted object.
 */

export type JsonObject = { [key: string]: unknown }

export interface AcceptanceRequirement {
  key: string
  kind: string
  required_result: string
  tool_name?: string
  command_sha256?: string
  artifact_sha256?: string
}

export interface AcceptanceCriteria {
  requirements: AcceptanceRequirement[]
}

export interface Project {
  project_id: string
  name: string
  description: string
  status: string
  revision: number
  source_event_id: string
  created_at: string
  updated_at: string
}

export interface Task {
  task_id: string
  project_id: string
  title: string
  description: string
  status: string
  revision: number
  source_event_id: string
  created_at: string
  updated_at: string
  acceptance_criteria: AcceptanceCriteria
}

export interface Step {
  step_id: string
  task_id: string
  title: string
  description: string
  status: string
  revision: number
  source_event_id: string
  created_at: string
  updated_at: string
  acceptance_criteria: AcceptanceCriteria
}

export interface Session {
  session_id: string
  project_id: string | null
  task_id: string | null
  host_id: string
  actor_id: string
  status: string
  revision: number
  source_event_id: string
  started_at: string
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface RawEvent {
  event_id: string
  seq: number
  schema_version: number
  event_type: string
  source_system: string
  source_event_id: string | null
  occurred_at: string
  recorded_at: string
  actor_id: string
  actor_kind: string
  host_id: string
  session_id: string | null
  project_id: string | null
  task_id: string | null
  payload: JsonObject
  body_sha256: string
}

/** One member of `Resolution.preview.expected_revisions`. */
export interface ExpectedRevision {
  object_type: string
  object_id: string
  revision: number
}

export interface RuleScope {
  kind: string
  project_id: string | null
  task_id: string | null
}

export interface Rule {
  rule_id: string
  rule_key: string
  version: number
  revision: number
  status: string
  scope: RuleScope
  kind: string
  severity: string
  enforcement: string
  content: string
  matcher: JsonObject
  verification_requirements: AcceptanceCriteria | null
  origin_event_id: string
  // `authority.get` returns null when the origin/status Event is absent, so
  // these are typed nullable and rendered defensively rather than printed as
  // the literal string "null".
  origin_actor_kind: string | null
  origin_event_type: string | null
  change_event_id: string | null
  status_event_id: string | null
  status_actor_kind: string | null
}

export interface RuleHistoryEntry {
  rule: Rule
  change_event_id: string
  changed_at: string
}

export interface Evidence {
  evidence_id: string
  task_id: string
  step_id: string | null
  kind: string
  status: string
  source_event_id: string
  change_event_id: string
  fingerprint_sha256: string | null
  artifact_uri: string | null
  artifact_sha256: string | null
  actor_id: string
  host_id: string
  session_id: string | null
  turn_id: string | null
  tool_use_id: string | null
  tool_name: string | null
  command_sha256: string | null
  producer_config_sha256: string | null
  confirmed_rule_id: string | null
  confirmed_rule_version: number | null
  task_revision: number
  step_revision: number | null
  created_at: string
  related_posttool_event_id: string | null
  result: unknown
  /** `NONE` | `OK` | `BROKEN_REFERENCE`, rechecked by Core on every read. */
  reference_status: string
}

export interface Host {
  host_id: string
  display_name: string
  kind: string
  first_seen_at: string
  last_seen_at: string
}

export interface Actor {
  actor_id: string
  kind: string
  display_name: string
  home_host_id: string
  created_at: string
}

export interface Health {
  status: string
  core_version: string
  schema_version: number
}

export interface AppliedMigration {
  name: string
  version: number
  checksum: string
  applied_at: string
}

export interface MetaSchema {
  core_version: string
  schema_version: number
  expected_schema_version: number
  migrations: AppliedMigration[]
}

export interface EventPage {
  events: RawEvent[]
  count: number
}

export interface ProjectPage {
  projects: Project[]
  count: number
}

export interface TaskPage {
  tasks: Task[]
  count: number
}

export interface SessionPage {
  sessions: Session[]
  count: number
}

export interface RulePage {
  rules: Rule[]
  count: number
}

export interface EvidencePage {
  evidence: Evidence[]
  count: number
}

/** `GET /v1/tasks/{id}/steps` and the `/history` endpoints carry no `count`. */
export interface StepList {
  steps: Step[]
}

export interface EventList {
  events: RawEvent[]
}

/* ------------------------------------------------------------------ *
 * P2 interpretation / resolution / review (read slice).
 *
 * Field sets were copied from real responses of the frozen P2 Core
 * (schema v8): `GET /v1/interpretations[/{id}]`, `GET /v1/reviews/pending[/{id}]`,
 * `GET /v1/resolutions/preview`, `GET /v1/resolutions/{id}`. The three
 * collections page with `after_id` and return `next_after_id`; none of them
 * returns a total count, so the UI must not imply one.
 * ------------------------------------------------------------------ */

/** `PROCESSING` | `EXTRACTED` | `FAILED` | `INTERRUPTED`; unknown values render verbatim. */
export interface InterpretationCandidate {
  kind: string
  content: string
  impact: string
  certainty: string
  confidence: number
  rationale: string
  scope: {
    kind: string
    project_id: string | null
    task_id: string | null
    path: string | null
    tool: string | null
  }
  source_span: { start: number; end: number; quote: string }
}

export interface Interpretation {
  interpretation_id: string
  event_id: string
  project_id: string | null
  task_id: string | null
  session_id: string | null
  host_id: string
  source_actor_id: string
  source_actor_kind: string
  source_type: string
  processor_actor_id: string
  extractor_id: string
  extractor_model: string
  extractor_version: string
  provider: string
  prompt_version: string
  output_schema_version: string
  schema_digest: string
  config_digest: string
  input_hash: string
  parent_interpretation_id: string | null
  created_at: string
  deadline_at: string
  config: JsonObject
  status: string
  raw_result_json: string | null
  candidates: InterpretationCandidate[] | null
  confidence: number | null
  error_code: string | null
  completed_at: string | null
  /** Detail responses add the maintenance view (lineage / rerun bookkeeping). */
  maintenance_head?: MaintenanceHead
  maintenance_history?: MaintenanceChange[]
  operation_completions?: OperationCompletion[]
}

export interface MaintenanceHead {
  root_interpretation_id: string
  current_interpretation_id: string
  revision: number
  status: string
  pending_operation_event_id: string | null
  pending_child_interpretation_id: string | null
}

export interface MaintenanceChange {
  operation_event_id: string
  root_interpretation_id: string
  /** The interpretation the change applied to (nullable on RERUN_COMPLETE). */
  parent_interpretation_id: string
  /** The child a `correct`/`rerun` produced; null for `reject`. */
  child_interpretation_id: string | null
  /** `CORRECT` | `REJECT` | `RERUN` | `RERUN_COMPLETE`. */
  action: string
  actor_id: string
  reason: string
  from_revision: number
  to_revision: number
  created_at: string
}

export interface OperationCompletion {
  operation_event_id: string
  child_interpretation_id: string
  /** `EXTRACTED` | `FAILED` | `INTERRUPTED` — column `processing_status`. */
  processing_status: string
  /** 0/1 column `head_applied`: whether this completion advanced the head. */
  head_applied: number
  expected_head_revision: number
  observed_head_revision: number
  completed_at: string
  /** Column `result_json` is returned as the stored JSON **string**. */
  result_json: string
}

export interface InterpretationPage {
  items: Interpretation[]
  next_after_id: string | null
}

export interface ResolutionAction {
  /** `CREATE_RULE` | `ATTACH_TASK` | `CREATE_TASK` | `NO_ACTION` | unknown. */
  action: string
  candidate_index: number
  target_id: string | null
  /** `rule` | `task` | unknown. */
  target_type: string | null
  before_revision: number | null
  after_revision: number | null
  rule_version: number | null
  binding_event_id: string | null
  change_event_ids: string[]
  [key: string]: unknown
}

export interface ResolutionResult {
  resolution_id: string
  interpretation_id: string
  source_event_id: string
  command_event_id: string
  policy_version: string
  disposition: string
  /** Applicable/disposition reasons; unknown values are rendered verbatim. */
  reasons: string[]
  actions: ResolutionAction[]
  source_bindings: unknown[]
  canonical_resolution_id: string | null
  latest_application_id: string | null
  execution_actor_id: string | null
  review_id: string | null
  review_actor_id: string | null
  review_command_event_id: string | null
}

export interface Resolution {
  resolution_id: string
  interpretation_id: string
  source_event_id: string
  actor_id: string
  command_event_id: string
  policy_version: string
  context_digest: string
  expected_context: JsonObject
  plan: ResolutionPlan
  request_hash: string
  created_at: string
  result: ResolutionResult
  manual_application_history: JsonObject[]
}

export interface PlanItem {
  candidate_index: number
  candidate: InterpretationCandidate
  /** `CREATE_RULE` | `ATTACH_TASK` | `CREATE_TASK` | `NO_ACTION` | unknown. */
  action: string
  pending: boolean
  reason: string
}

export interface ResolutionPlan {
  policy_version: string
  disposition: string
  items: PlanItem[]
}

export interface ResolutionPreview {
  interpretation_id: string
  source_event_id: string
  policy_version: string
  plan: ResolutionPlan
  /**
   * A **list**, not a map: `ResolverStore._validate_revisions` requires an array of
   * `{object_type, object_id, revision}` (`object_type` in project/task/step/rule)
   * and rejects anything else with `expected_revisions must be bounded array`. It is
   * returned verbatim by the preview and must be sent back verbatim.
   */
  expected_revisions: ExpectedRevision[]
  expected_context_digest: string
  maintenance_head: MaintenanceHead
  source_application: JsonObject | null
}

export interface Review {
  review_id: string
  resolution_id: string
  /** `PENDING` | `APPROVED` | `REJECTED` | `SOURCE_REPLAYED`; unknown renders verbatim. */
  status: string
  revision: number
  created_at: string
  updated_at: string
  resolution: Resolution
  history: Array<JsonObject & { result: unknown }>
  /** Present unless the current preview conflicts (then `preview_error` is). */
  preview?: ResolutionPreview
  preview_error?: { code: string; details: JsonObject }
}

export interface ReviewPage {
  items: Review[]
  next_after_id: string | null
}

/* -------------------------------------------------------------------------- *
 * P6-03a: P3 continuity reads — Checkpoint / Context Pack / Resume
 *
 * **DEPENDENCY PENDING.** These routes exist on branch
 * `p3-checkpoint-context-resume` (P3) and **not** in this baseline (`850fbec`, schema
 * 8, no `context/`, `continuity.py` or `task_snapshot.py`). The field sets below were
 * derived from that branch by `scripts/p6/p3_contract.py` and frozen in
 * `web/contracts/p3-continuity-read.v1.json`, with the source commit recorded; the
 * drift guard re-derives and diffs them.
 *
 * Two habits here, both deliberate:
 * - scalar identity, scope, digest and revision fields are typed exactly as P3 returns
 *   them (`/v1/checkpoints/{id}.projection` is a string, `task_revision` a number);
 * - nested documents (`projection`, `workspace`, `comparison`, `configuration`,
 *   `memory`, `section_metrics`, …) stay `JsonObject`/`unknown`. Their inner shape is
 *   P3's to define, and typing it from memory is how an invented contract starts. The
 *   views render them verbatim through `JsonBlock`.
 * -------------------------------------------------------------------------- */

/** A checkpoint's note: the DDL constrains `note_json` to an object with a text. */
export interface CheckpointNote {
  text: string
}

/** Where a checkpoint's creation came from (`continuity._persist`). */
export interface TriggerProvenance {
  level: string
  source_event_id: string
  [key: string]: unknown
}

/**
 * `GET /v1/checkpoints/{ckp}` and `GET /v1/tasks/{tsk}/checkpoints/latest`.
 *
 * `projection` is an **immutable snapshot of what the Task looked like at
 * `task_revision`** (`projection_version` `jasmine.task-snapshot.v1`), carried with
 * three digests. It is a record of a moment, never a claim about current Truth.
 */
export interface Checkpoint {
  checkpoint_id: string
  command_event_id: string
  source_event_id: string
  project_id: string
  task_id: string
  host_id: string
  actor_id: string
  session_id: string | null
  reason: string
  trigger_provenance: TriggerProvenance
  projection_version: string
  projection: JsonObject
  projection_digest: string
  task_revision: number
  full_context_digest: string
  workspace: JsonObject
  workspace_digest: string
  note: CheckpointNote | null
  created_at: string
}

/**
 * `GET /v1/resumes/{rms}`.
 *
 * A resume is a **comparison, not a restore**: `comparison` and `next_actions` describe
 * how the checkpoint differs from the current projection, and nothing is applied.
 * `truth_digest` is the digest of current Truth at build time, not a restore point.
 * `note` is the resumed-from checkpoint's note, explicitly
 * `{authority: false, historical: true}` — it must never be presented as Truth.
 */
export interface Resume {
  resume_id: string
  command_event_id: string
  source_event_id: string
  actor_id: string
  host_id: string
  project_id: string
  task_id: string
  session_id: string | null
  checkpoint_id: string | null
  created_at: string
  projection: JsonObject
  truth_digest: string
  workspace: JsonObject
  sample_window: JsonObject
  comparison: JsonObject
  note: HistoricalNote | null
  next_actions: unknown[]
}

/** A note that is carried forward with its authority explicitly denied. */
export interface HistoricalNote {
  value: CheckpointNote | null
  authority: false
  historical: true
}

/**
 * `GET /v1/context/{ctx}` — the persisted result of a Context Pack build.
 *
 * The pack records what was *actually rendered* (`rendered_content`, its digest, byte
 * and token counts, per-section metrics and omissions) alongside the projection and
 * workspace it was built from. `memory` is produced by the P4 provider and is
 * non-authoritative; P6-03a only displays what Core recorded and runs no experiment.
 */
export interface ContextPack {
  context_pack_id: string
  command_event_id: string
  source_event_id: string
  actor_id: string
  host_id: string
  project_id: string
  task_id: string
  session_id: string | null
  current_step_id: string | null
  reason: string
  trigger_provenance: TriggerProvenance
  created_at: string
  projection: JsonObject
  truth_digest: string
  selector: JsonObject
  resume_comparison: JsonObject
  workspace: JsonObject
  workspace_digest: string
  sample_window: JsonObject
  configuration: JsonObject
  config_digest: string
  tokenizer: JsonObject
  memory: JsonObject
  rendered_content: string
  rendered_sha256: string
  rendered_utf8_bytes: number
  renderer_version: string
  token_count: number
  section_metrics: unknown
  omissions: unknown
  selected_optional_ids: unknown
}

/* -------------------------------------------------------------------------- *
 * P6-03b1: P4 memory reads — health / jobs / items / traces
 *
 * **DEPENDENCY PENDING.** These routes exist at the pinned P4 commit
 * `0c4f8f0e2118711ac9aa2a7006fddc6982962248` and **not** in this baseline
 * (`850fbec`, schema 8, no `src/jasmine_core/memory/`). The field sets below were
 * derived from that commit by `scripts/p6/p4_contract.py` and frozen in
 * `web/contracts/p4-memory-read.v1.json`, with the source commit recorded. The
 * drift check re-derives and diffs them, and exits non-zero when the pinned
 * commit is absent rather than skipping.
 *
 * P4 phase acceptance has NOT passed: nothing here was validated against a real
 * P4 Core, and the seven routes are served only by the simulated fixture.
 *
 * The same two habits as the P3 section above, for the same reasons: scalar
 * identity/revision/digest fields are typed as P4 returns them (nullability read
 * from the `m0011_memory` table definitions, not guessed), and nested documents
 * stay `JsonObject`/`unknown` because their inner shape is P4's to define.
 *
 * `non_authoritative` is Core's own field, not an editorial note: P4 emits it on
 * the item list, the item detail and health. The views read it rather than
 * printing a hardcoded claim.
 * -------------------------------------------------------------------------- */

/** `memory_jobs` row, with `receipt_json` decoded to `receipt` (memory/jobs.py). */
export interface MemoryJob {
  job_id: string
  kind: string
  memory_id: string
  source_event_id: string
  project_id: string | null
  task_id: string | null
  memory_type: string
  bank_id: string
  document_id: string
  content_sha256: string
  status: string
  retry_count: number
  max_attempts: number
  next_attempt_at: string
  lease_owner: string | null
  lease_expires_at: string | null
  claim_generation: number
  last_error_code: string | null
  safe_error_summary: string | null
  receipt: JsonObject | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

/** `memory_job_attempts` row, with `receipt_json` decoded to `receipt`. */
export interface MemoryJobAttempt {
  attempt_id: string
  job_id: string
  attempt_number: number
  phase: string
  outcome: string
  error_code: string | null
  safe_error_summary: string | null
  receipt: JsonObject | null
  lease_owner: string
  started_at: string
  finished_at: string | null
}

/** `JobStore.counts()` — one key per `JOB_STATUSES` entry, lowercased. */
export interface MemoryJobCounts {
  pending: number
  running: number
  done: number
  failed: number
}

/** `MemoryStore.health_counts()` — three status counts plus three engine-state counts. */
export interface MemoryHealthCounts {
  items_active: number
  items_obsolete: number
  items_deleted: number
  retain_failed: number
  delete_pending: number
  delete_failed: number
}

/** `lifecycle.lifecycle_summary` — what the Inspector shows for one item. */
export interface MemoryLifecycle {
  status: string
  recallable: boolean
  pinned: boolean
  pin_affects: string
  engine_state: string
  remote_document_deleted: boolean
  last_recalled_at: string | null
  recall_count: number
  lifecycle_reason: string | null
}

/** `memory_items` row, with `evidence_ids_json`/`receipt_json` decoded. */
export interface MemoryItem {
  memory_id: string
  bank_id: string
  scope_kind: string
  project_id: string | null
  task_id: string | null
  memory_type: string
  document_id: string
  source_event_id: string
  evidence_ids: unknown[]
  origin_rule_id: string | null
  origin_rule_version: number | null
  origin_event_id: string | null
  content_sha256: string
  sanitized_text: string
  status: string
  pinned: boolean
  revision: number
  engine_state: string
  receipt: JsonObject | null
  last_error_code: string | null
  safe_error_summary: string | null
  remote_deleted_at: string | null
  lifecycle_reason: string | null
  last_recalled_at: string | null
  recall_count: number
  created_at: string
  updated_at: string
}

/** The list endpoint adds `lifecycle` to each row; the detail endpoint does not. */
export interface MemoryItemListRow extends MemoryItem {
  lifecycle: MemoryLifecycle
}

/** `memory_query_traces` row, with the four `_json` columns decoded. */
export interface MemoryTrace {
  trace_id: string
  mode: string
  status: string
  project_id: string | null
  task_id: string | null
  session_id: string | null
  actor_id: string
  query_sha256: string
  query_length: number
  requested_types: unknown[]
  banks: unknown[]
  filtered: unknown[]
  budget_tokens: number | null
  used_tokens: number
  authority_refs: unknown[]
  state_revision: number | null
  error_code: string | null
  safe_error_summary: string | null
  created_at: string
}

/** The trace detail read adds `items`; the list row does not. */
export interface MemoryTraceDetail extends MemoryTrace {
  items: unknown[]
}

/** `health.report()` plus the handler's `non_authoritative`. */
export interface MemoryHealth {
  status: string
  engine_id: string | null
  configured: boolean
  reachable: boolean | null
  api_version: string | null
  origin_host: string | null
  features: JsonObject
  last_check_at: string | null
  last_success_at: string | null
  last_error_code: string | null
  safe_error_summary: string | null
  jobs: MemoryJobCounts
  items: MemoryHealthCounts
  degraded: boolean
  core_impact: string
  deployment: JsonObject
  checked_at: string
  non_authoritative: boolean
}
