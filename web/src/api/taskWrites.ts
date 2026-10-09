/**
 * P6-02b Task/Step write payloads, built to the frozen route contract.
 *
 * Core validates these seven routes with an exact allow-list (`_state_body`, spec §3): a field
 * outside the set is a `400 invalid_request` naming it, and there is no "ignore extras" path.
 * Two mistakes are therefore worth designing out rather than testing for:
 *
 * * sending `expected_revision` on Task create, which Core refuses up front as
 *   `unexpected_expected_revision` (it is in the allowed set but explicitly rejected); and
 * * forgetting `expected_revision` on the six state routes, which turns "I read this revision"
 *   into a blind overwrite.
 *
 * Every body goes through `only()`, which throws on a key the route does not list, so a
 * programming error is loud in the suite instead of arriving at Core looking plausible. The
 * `event_id` replay key is **not** built here: `useWriteAction` owns it so a retry after a
 * timeout reuses the same key (§5).
 */

/** A Criteria value as Core accepts it: `{requirements: [...]}`. */
export type AcceptanceCriteriaValue = { requirements: unknown[] }

export type TaskWriteBody = Record<string, unknown>

/** Fields each route accepts, transcribed from spec §2 (`handlers.py`/`models.py`). */
export const ALLOWED = {
  task_create: [
    'title',
    'project_id',
    'host_id',
    'description',
    'acceptance_criteria',
    'source_system',
    'source_event_id',
    'occurred_at',
    'event_id',
    'actor_id',
  ],
  step_create: [
    'title',
    'description',
    'acceptance_criteria',
    'expected_revision',
    'host_id',
    'event_id',
  ],
  step_transition: ['status', 'reason', 'expected_revision', 'host_id', 'event_id'],
  task_transition: ['status', 'reason', 'expected_revision', 'host_id', 'event_id'],
  task_criteria: ['acceptance_criteria', 'expected_revision', 'host_id', 'event_id'],
  task_accept: ['expected_revision', 'host_id', 'event_id'],
  step_criteria: ['acceptance_criteria', 'expected_revision', 'host_id', 'event_id'],
} as const satisfies Record<string, readonly string[]>

/** The route keys, so a caller cannot pass a name that is not one of the seven. */
export type TaskWriteRoute = keyof typeof ALLOWED

/**
 * Keep only fields the route lists, and only those that carry a value.
 *
 * `undefined` and `null` are dropped: Core distinguishes "absent" from "present and empty", and
 * the contract describes absent optionals. A field the route does not list is a bug, not a value
 * to drop silently.
 */
export function only(route: TaskWriteRoute, values: TaskWriteBody): TaskWriteBody {
  const allowed: readonly string[] | undefined = ALLOWED[route]
  if (allowed === undefined) {
    throw new Error(`${String(route)} is not one of the seven frozen Task/Step write routes`)
  }
  const unknown = Object.keys(values).filter((key) => !allowed.includes(key))
  if (unknown.length > 0) {
    throw new Error(
      `${route} does not accept ${unknown.join(', ')}; Core refuses unknown fields and the ` +
        `frozen route lists only ${allowed.join(', ')}`,
    )
  }
  const body: TaskWriteBody = {}
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) continue
    body[key] = value
  }
  return body
}

/**
 * `POST /v1/tasks` — Task create. **No `expected_revision`**: the route rejects it, so it is not
 * in the allowed set and cannot be sent by a caller of this builder.
 */
export function taskCreateBody(values: {
  title: string
  project_id: string
  host_id: string
  description?: string
  acceptance_criteria?: AcceptanceCriteriaValue
  source_system?: string
  source_event_id?: string
  occurred_at?: string
}): TaskWriteBody {
  return only('task_create', values)
}

/**
 * `POST /v1/tasks/{tsk}/steps` — Step create. `expected_revision` is the **Task's** revision
 * (spec §4), so the caller passes the Task row it read.
 */
export function stepCreateBody(values: {
  title: string
  expected_revision: number
  host_id: string
  description?: string
  acceptance_criteria?: AcceptanceCriteriaValue
}): TaskWriteBody {
  return only('step_create', values)
}

/**
 * `POST /v1/steps/{stp}/transition` and `POST /v1/tasks/{tsk}/transition`.
 *
 * `reason` is conditional on route 3 (required only for a `SKIPPED` target or a
 * `BLOCKED`/`SKIPPED` source) and unconditional on route 4, so it is passed through when the
 * caller has one and omitted when blank. Omitting it where Core requires it is a `400` the panel
 * renders from Core's own answer, which is the honest outcome.
 */
export function transitionBody(values: {
  status: string
  expected_revision: number
  host_id: string
  reason?: string
}): TaskWriteBody {
  return only('task_transition', values)
}

/** `POST /v1/steps/{stp}/criteria` — the same four fields as the Task criteria route. */
export function criteriaBody(values: {
  acceptance_criteria: AcceptanceCriteriaValue
  expected_revision: number
  host_id: string
}): TaskWriteBody {
  return only('task_criteria', values)
}

/**
 * `POST /v1/tasks/{tsk}/accept` — no body field beyond the revision, host and replay key.
 * Evidence validation always runs, so this route's only fresh-200 outcome is a passing one.
 */
export function acceptBody(values: {
  expected_revision: number
  host_id: string
}): TaskWriteBody {
  return only('task_accept', values)
}

/**
 * The Step transition's own allowed set differs from the Task transition's only in which object
 * the revision belongs to; the field names are identical. This alias exists so a caller cannot
 * accidentally build a Step body against the Task route's name and vice versa.
 */
export function stepTransitionBody(values: {
  status: string
  expected_revision: number
  host_id: string
  reason?: string
}): TaskWriteBody {
  return only('step_transition', values)
}

export function stepCriteriaBody(values: {
  acceptance_criteria: AcceptanceCriteriaValue
  expected_revision: number
  host_id: string
}): TaskWriteBody {
  return only('step_criteria', values)
}
