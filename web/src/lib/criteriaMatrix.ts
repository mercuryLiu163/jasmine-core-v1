/**
 * The criteria↔Evidence **candidate** predicate (P6-02 item 5, frozen contract
 * `web/P6-02-ITEM5-VERIFIED-ACCEPTED-SPEC.md` rev 2 §3.2).
 *
 * This module copies Core's readable half exactly and never decides sufficiency:
 *
 * * the candidate predicate is the conjunction of `_fresh` parts **F3–F7 and F9a–F9c**
 *   (`src/jasmine_core/evidence.py:914-947`, `:846-855`) plus `_match`'s exact
 *   field equality (`:949-958`), nothing else;
 * * **F1, F2, F8, F9d and F9e are unknowable in the browser** — no read route exposes the
 *   stored `workspace_fingerprints` row, the current fingerprint, the producer-config mapping
 *   or the source-payload re-derivation. They are carried as an explicit `UNREADABLE_FRESHNESS`
 *   residual and never rendered as a pass;
 * * the only readable display states are the contract's: candidate, missing, stale-proven,
 *   field-mismatch (a `kind`/`status` row whose requirement fields differ) and the wrong-kind
 *   set. "Stale — unprovable here" is a candidate plus the residual, not a separate verdict.
 *
 * Everything here is a read aid. Core's `CurrentEvidenceValidator` remains the only judge:
 * a request the browser cannot falsify still answers `422 missing_evidence` (§3.5).
 */

import type { AcceptanceRequirement, Evidence, RawEvent } from '../api/types'

/**
 * The five `_fresh` conditions with no read route (§3.2, F1/F2/F8/F9d/F9e). They are the
 * residual a candidate row must carry, never a silent assumption of freshness.
 */
export const UNREADABLE_FRESHNESS = ['F1', 'F2', 'F8', 'F9d', 'F9e'] as const

export interface ReadableFailure {
  /** The frozen `_fresh` condition number the readable fact falsifies. */
  condition: string
  detail: string
}

export interface MatrixContext {
  /** The owning Task's `project_id` — `_source_valid` compares the source Event to it. */
  projectId: string
  /** The Task row's revision (R-T). */
  taskRevision: number
  /** The revision a submission would send: the Step's for a Step form, the Task's otherwise. */
  expectedRevision: number
  /** `null` for a Task-level requirement; the Step id for a Step-level one. */
  stepId: string | null
  /** R-TH/R-SH command Events, used for Core's cycle cutoff. */
  commandEvents: readonly RawEvent[]
  /** R-EV Events by id, for `change_event_id` `seq` and `source_event_id` validation. */
  eventsById: ReadonlyMap<string, RawEvent>
  /** Set when the requirement comes from an applicable Rule (`_rules`). */
  ruleId?: string | null
  ruleVersion?: number | null
  /** The Rule's activation `seq`; matching rows must be newer (`satisfy(..., after_seq=…)`). */
  afterSeq?: number | null
}

/**
 * Core's cycle cutoff (`evidence.py:1003-1021`).
 *
 * The cutoff's owner is `step_id or row["step_id"]`: a Step form — and a Step-scoped row on a
 * Task form — uses that Step's own `step.criteria_updated`/`→IN_PROGRESS` cycle; only a
 * Task-scoped row on a Task form uses the task-level query.
 */
export function cycleCutoff(
  events: readonly RawEvent[],
  formStepId: string | null,
  rowStepId: string | null,
): number {
  const owner = formStepId ?? rowStepId
  let cutoff = 0
  for (const event of events) {
    const payload = event.payload ?? {}
    if (owner !== null) {
      if (payload['step_id'] !== owner) continue
      if (event.event_type === 'step.criteria_updated') cutoff = Math.max(cutoff, event.seq)
      else if (event.event_type === 'step.transitioned' && payload['to'] === 'IN_PROGRESS') {
        cutoff = Math.max(cutoff, event.seq)
      }
    } else {
      if (event.event_type === 'task.criteria_updated') cutoff = Math.max(cutoff, event.seq)
      else if (event.event_type === 'step.transitioned' && payload['to'] === 'IN_PROGRESS') {
        cutoff = Math.max(cutoff, event.seq)
      }
    }
  }
  return cutoff
}

/** `_match`'s field half (`evidence.py:949-958`): only the keys the requirement actually states. */
export function matchesRequirementFields(
  requirement: AcceptanceRequirement,
  row: Evidence,
  ruleId: string | null | undefined,
  ruleVersion: number | null | undefined,
): boolean {
  for (const key of ['tool_name', 'command_sha256', 'artifact_sha256'] as const) {
    if (Object.prototype.hasOwnProperty.call(requirement, key) && row[key] !== requirement[key]) {
      return false
    }
  }
  if (ruleId !== null && ruleId !== undefined && row.kind === 'USER_CONFIRMATION') {
    return row.confirmed_rule_id === ruleId && row.confirmed_rule_version === ruleVersion
  }
  return true
}

/**
 * The readable half of `_fresh` for one row: every failure here is a positive falsification
 * (the row cannot satisfy the gate). An empty list is **not** freshness — the residual applies.
 */
export function readableFreshnessFailures(
  row: Evidence,
  context: MatrixContext,
): ReadableFailure[] {
  const failures: ReadableFailure[] = []
  const changeSeq = context.eventsById.get(row.change_event_id)?.seq
  const source = context.eventsById.get(row.source_event_id)

  // F3: task_revision <= current task revision (Step form) or expected_revision (Task form).
  const taskLimit = context.stepId !== null ? context.taskRevision : context.expectedRevision
  if (row.task_revision > taskLimit) {
    failures.push({ condition: 'F3', detail: `task_revision ${row.task_revision} > ${taskLimit}` })
  }

  // F4 (Step form only): the row belongs to the Step and predates its current revision.
  if (context.stepId !== null) {
    if (row.step_id !== context.stepId) {
      failures.push({
        condition: 'F4',
        detail: `step_id ${row.step_id ?? '—'} ≠ ${context.stepId}`,
      })
    }
    if (row.step_revision !== null && row.step_revision > context.expectedRevision) {
      failures.push({
        condition: 'F4',
        detail: `step_revision ${row.step_revision} > ${context.expectedRevision}`,
      })
    }
  }

  // F5: the change Event is newer than the cycle cutoff.
  const cutoff = cycleCutoff(context.commandEvents, context.stepId, row.step_id)
  if (changeSeq === undefined) {
    failures.push({
      condition: 'F5',
      detail: `change Event ${row.change_event_id} 不可读,无法证明晚于 cycle cutoff ${cutoff}`,
    })
  } else if (changeSeq <= cutoff) {
    failures.push({ condition: 'F5', detail: `change seq ${changeSeq} ≤ cycle cutoff ${cutoff}` })
  }
  if (context.afterSeq !== null && context.afterSeq !== undefined) {
    if (changeSeq === undefined || changeSeq <= context.afterSeq) {
      failures.push({
        condition: 'F5(规则激活)',
        detail: `change seq ${changeSeq ?? '—'} ≤ 规则激活 seq ${context.afterSeq}`,
      })
    }
  }

  // F6: a USER_CONFIRMATION binds the exact revision.
  if (row.kind === 'USER_CONFIRMATION') {
    if (context.stepId !== null && row.step_revision !== context.expectedRevision) {
      failures.push({
        condition: 'F6',
        detail: `confirmation step_revision ${row.step_revision ?? '—'} ≠ ${context.expectedRevision}`,
      })
    }
    if (context.stepId === null && row.task_revision !== context.expectedRevision) {
      failures.push({
        condition: 'F6',
        detail: `confirmation task_revision ${row.task_revision} ≠ ${context.expectedRevision}`,
      })
    }
  }

  // F7: reference_status is recomputed by Core on every read (R-TE/R-E).
  if (row.reference_status === 'BROKEN_REFERENCE') {
    failures.push({ condition: 'F7', detail: 'reference_status BROKEN_REFERENCE' })
  }

  // F9a: the source Event exists and belongs to this Task and project.
  if (source === undefined) {
    failures.push({
      condition: 'F9a',
      detail: `source Event ${row.source_event_id} 不可读`,
    })
  } else {
    if (source.task_id !== row.task_id || source.project_id !== context.projectId) {
      failures.push({ condition: 'F9a', detail: 'source Event 的 task_id/project_id 与该行不符' })
    }
    // F9b/F9c: the source Event is the kind of producer that may mint this row.
    if (row.kind === 'USER_CONFIRMATION') {
      if (
        !(
          (source.event_type === 'user.prompt' ||
            (source.event_type === 'human.confirmation_submitted' && source.source_system === 'core-web-confirmation-v1')) &&
          source.actor_kind === 'human' &&
          source.actor_id === row.actor_id
        )
      ) {
        failures.push({ condition: 'F9b', detail: 'source 不是该 actor 的 human prompt 或专用 Web source（完整链仅 Core 重验）' })
      }
    } else if (
      !(
        source.event_type === 'tool.result' &&
        source.actor_kind === 'system' &&
        source.actor_id === row.actor_id
      )
    ) {
      failures.push({ condition: 'F9c', detail: 'source 不是该 actor 的 system tool.result' })
    }
  }

  return failures
}

/** The contract's display states (§3.2). "Stale — unprovable" is a candidate + the residual. */
export type MatrixState = 'candidate' | 'missing' | 'stale-proven' | 'field-mismatch'

export interface RequirementEvaluation {
  requirement: AcceptanceRequirement
  state: MatrixState
  /** Matching rows that pass every readable condition — still only candidates. */
  candidates: Evidence[]
  /** Matching rows falsified by a readable fact, with the facts. */
  stale: Array<{ row: Evidence; failures: ReadableFailure[] }>
  /** Rows with the right `kind`/`status` whose requirement fields or Rule binding differ. */
  fieldMismatch: Evidence[]
  /** Rows whose `kind` or `status` differs — never a match, never a PASS. */
  wrongKind: Evidence[]
  /** `UNREADABLE_FRESHNESS` when a candidate exists; empty otherwise. */
  residualUnknown: readonly string[]
}

/**
 * Evaluate one requirement against the Evidence rows the form may read.
 *
 * `rows` must already be scoped the way Core scopes them: only the Step's rows for a Step form
 * (`_rows(task_id, step_id)`), every Task row otherwise.
 */
export function evaluateRequirement(
  requirement: AcceptanceRequirement,
  rows: readonly Evidence[],
  context: MatrixContext,
): RequirementEvaluation {
  const shapeRows = rows.filter(
    (row) => row.kind === requirement.kind && row.status === requirement.required_result,
  )
  const wrongKind = rows.filter(
    (row) => !(row.kind === requirement.kind && row.status === requirement.required_result),
  )
  const evaluated = shapeRows
    .filter((row) => matchesRequirementFields(requirement, row, context.ruleId, context.ruleVersion))
    .map((row) => ({ row, failures: readableFreshnessFailures(row, context) }))
  const fieldMismatch = shapeRows.filter(
    (row) => !matchesRequirementFields(requirement, row, context.ruleId, context.ruleVersion),
  )
  const candidates = evaluated.filter((item) => item.failures.length === 0).map((item) => item.row)
  const stale = evaluated.filter((item) => item.failures.length > 0)

  let state: MatrixState
  if (shapeRows.length === 0) state = 'missing'
  else if (candidates.length > 0) state = 'candidate'
  else if (stale.length > 0) state = 'stale-proven'
  else state = 'field-mismatch'

  return {
    requirement,
    state,
    candidates,
    stale,
    fieldMismatch,
    wrongKind,
    residualUnknown: state === 'candidate' ? UNREADABLE_FRESHNESS : [],
  }
}

/**
 * Core's applicable-Rule WHERE clause, copied (`evidence.py:960-970`): ACTIVE and the current
 * version is `kind='ACCEPTANCE'` or `enforcement='VERIFY'`, scoped global / this project /
 * this task. R-RA returns every ACTIVE rule, so the form must present this as a copy of Core's
 * filter, not as Core's answer (§3.3).
 */
export function ruleApplies(rule: {
  scope: { kind: string; project_id: string | null; task_id: string | null }
  kind: string
  enforcement: string
}, taskId: string, projectId: string): boolean {
  const scoped =
    rule.scope.kind === 'global' ||
    (rule.scope.kind === 'project' && rule.scope.project_id === projectId) ||
    (rule.scope.kind === 'task' && rule.scope.task_id === taskId)
  return scoped && (rule.kind === 'ACCEPTANCE' || rule.enforcement === 'VERIFY')
}
