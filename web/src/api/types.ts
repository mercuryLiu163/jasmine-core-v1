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
