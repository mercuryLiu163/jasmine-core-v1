/**
 * P6-03b2 write payloads, built to the frozen contract rather than typed per panel.
 *
 * P4 validates write bodies with an **exact allow-list**: a field outside it is a `400`
 * naming the offender, and a missing required field is refused before anything is written.
 * Two failure modes are therefore worth designing out rather than testing for:
 *
 * * a panel inventing a field (`reason` on a retry, `idempotency_key` where P4 wants
 *   `event_id`) — the mistake P6-02 made once and had to fix;
 * * a panel forgetting `expected_revision`, which turns "I read this revision" into a
 *   blind overwrite.
 *
 * So every body goes through `only()`, which throws on any key the contract does not list
 * for that action. A programming error is then loud in the test suite instead of arriving at
 * Core as a request that looks plausible.
 *
 * The event id is **not** minted here: `useWriteAction` owns the replay key so that a retry
 * after a timeout reuses it. It adds `event_id`, which every one of the six allows.
 */

import type { MemoryLifecycleCommand } from './types'

/** Fields P4 accepts per action, from `web/contracts/p4-memory-write.v1.json`. */
const ALLOWED: Record<'retry' | 'lifecycle' | 'reflect', readonly string[]> = {
  retry: ['event_id', 'host_id'],
  lifecycle: ['event_id', 'expected_revision', 'host_id', 'reason'],
  reflect: ['event_id', 'host_id', 'max_tokens', 'project_id', 'query', 'task_id'],
}

/** Fields P4 refuses to proceed without. `retry` requires none. */
export const REQUIRED: Record<'retry' | 'lifecycle' | 'reflect', readonly string[]> = {
  retry: [],
  lifecycle: ['expected_revision', 'host_id'],
  reflect: ['project_id', 'query'],
}

export type MemoryWriteBody = Record<string, unknown>

/**
 * Keep only the fields the contract lists, and only those that carry a value.
 *
 * `undefined` and `null` are dropped: Core distinguishes "absent" from "present and empty",
 * and an absent optional field is what the contract describes. A field the contract does not
 * list is a bug, not a value to drop silently.
 */
function only(action: keyof typeof ALLOWED, values: MemoryWriteBody): MemoryWriteBody {
  const allowed = ALLOWED[action]
  const unknown = Object.keys(values).filter((key) => !allowed.includes(key))
  if (unknown.length > 0) {
    throw new Error(
      `${action} does not accept ${unknown.join(', ')}; P4 refuses unknown fields and the ` +
        `frozen contract lists only ${allowed.join(', ')}`,
    )
  }
  const body: MemoryWriteBody = {}
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) continue
    body[key] = value
  }
  return body
}

/**
 * `POST /v1/memory/jobs/{mjb}/retry` — no body field is required.
 *
 * The caller's whole object is passed to `only()`, not a hand-copied subset, so a caller that
 * supplies an unlisted field gets the loud error instead of having it dropped quietly.
 */
export function retryBody(values: { host_id?: string } = {}): MemoryWriteBody {
  return only('retry', values)
}

/**
 * `POST /v1/memory/items/{mem}/{command}`.
 *
 * `expected_revision` is the revision the operator actually read. Passing it is the point of
 * the command: Core answers `409 RevisionConflict` when the item moved, and the panel shows
 * the current value beside the operator's draft rather than retrying the original intent.
 */
export function lifecycleBody(
  command: MemoryLifecycleCommand,
  values: { expected_revision: number; host_id: string; reason?: string },
): MemoryWriteBody {
  // The command is part of the path, not the body; it is named here so a caller cannot build a
  // lifecycle body for something that is not a lifecycle command.
  if (!['obsolete', 'delete', 'pin', 'unpin'].includes(command)) {
    throw new Error(`${String(command)} is not a lifecycle command`)
  }
  return only('lifecycle', values)
}

/**
 * `POST /v1/memory/reflect` — the generator, and the only write that needs
 * `memory:reflect` rather than `memory:manage`.
 *
 * No `explicit_user_request` field: P4's API route is the user-facing one and sets it, and
 * the contract does not list it, so sending it would be refused as an unknown field. What the
 * UI owes is an explicit user gesture, which is why the panel is a form the operator submits
 * and never a side effect of rendering.
 */
export function reflectBody(values: {
  project_id: string
  query: string
  task_id?: string
  max_tokens?: number
  host_id?: string
}): MemoryWriteBody {
  return only('reflect', values)
}
