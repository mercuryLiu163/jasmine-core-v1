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
  expected_revisions: JsonObject
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
