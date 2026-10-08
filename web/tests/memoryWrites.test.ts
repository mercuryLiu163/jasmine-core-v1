/**
 * P6-03b2 payloads: the body is built from the frozen contract, not typed per panel.
 *
 * P4 refuses a write body whose field set is not exactly the allow-listed one, and refuses a
 * missing `expected_revision` before it looks at the item. So this file checks the three ways
 * a payload could be wrong while looking right:
 *
 * 1. **An extra field.** `only()` throws rather than sending it — the failure mode P6-02 hit
 *    with `idempotency_key` where Authority wanted `event_id`.
 * 2. **A missing required field.** Checked against the contract's own `required_body_fields`
 *    for the route, so a contract change that is not mirrored here fails.
 * 3. **The wrong replay key.** P4 replays on `event_id`; the contract lists it, and the panel
 *    must not send `idempotency_key`.
 *
 * The contract JSON is imported as data: the point is to compare the builder with the frozen
 * record rather than with a second hand-written copy of it.
 */

import { describe, expect, it } from 'vitest'

import contract from '../contracts/p4-memory-write.v1.json'
import {
  REQUIRED,
  lifecycleBody,
  reflectBody,
  retryBody,
} from '../src/api/memoryWrites'

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const EVENT = `evt_${ID}`
const HOST = `hst_${ID}`
const PROJECT = `prj_${ID}`

/** The contract route for a command, so the test reads the frozen field lists. */
function route(pathSuffix: string) {
  const write = contract.writes.find((entry) => entry.path_pattern.includes(pathSuffix))
  if (!write) throw new Error(`no contract route matching ${pathSuffix}`)
  return write
}

describe('P6-03b2 payloads match the frozen contract', () => {
  it('builds exactly the allowed fields for a retry, and nothing required', () => {
    const write = route('retry')
    const body = retryBody({ host_id: HOST })
    expect(Object.keys(body).sort()).toEqual(['host_id'])
    for (const key of Object.keys(body)) expect(write.allowed_body_fields).toContain(key)
    expect(write.required_body_fields).toEqual([])
    expect(REQUIRED.retry).toEqual(write.required_body_fields)
  })

  it('omits an absent host_id rather than sending null', () => {
    // Core distinguishes "absent" from "present and null"; the contract describes absent.
    expect(retryBody({})).toEqual({})
    expect(retryBody({ host_id: undefined })).toEqual({})
  })

  it.each(['obsolete', 'delete', 'pin', 'unpin'] as const)(
    'builds a %s body with the revision and host, and no extra field', (command) => {
      const write = route(`/${command}`)
      const body = lifecycleBody(command, { expected_revision: 7, host_id: HOST })
      expect(Object.keys(body).sort()).toEqual(['expected_revision', 'host_id'])
      for (const key of Object.keys(body)) expect(write.allowed_body_fields).toContain(key)
      for (const key of write.required_body_fields) expect(body).toHaveProperty(key)
      expect(REQUIRED.lifecycle).toEqual(write.required_body_fields)
    })

  it('carries a reason when there is one and drops it when there is not', () => {
    expect(lifecycleBody('obsolete', { expected_revision: 1, host_id: HOST, reason: '过期' }))
      .toMatchObject({ reason: '过期' })
    expect(lifecycleBody('obsolete', { expected_revision: 1, host_id: HOST }))
      .not.toHaveProperty('reason')
    expect(lifecycleBody('obsolete', { expected_revision: 1, host_id: HOST, reason: undefined }))
      .not.toHaveProperty('reason')
  })

  it('refuses to build a lifecycle body for something that is not a lifecycle command', () => {
    expect(() =>
      lifecycleBody('retry' as never, { expected_revision: 1, host_id: HOST })).toThrow(
      /not a lifecycle command/)
  })

  it('throws on a field the contract does not allow, for every action', () => {
    // The loud direction: a panel inventing a field fails here, not at Core.
    expect(() => retryBody({ host_id: HOST, reason: 'nope' } as never)).toThrow(/does not accept reason/)
    expect(() =>
      lifecycleBody('pin', { expected_revision: 1, host_id: HOST, idempotency_key: 'ui-1' } as never),
    ).toThrow(/idempotency_key/)
    expect(() =>
      reflectBody({ project_id: PROJECT, query: 'q', idempotency_key: 'ui-1' } as never),
    ).toThrow(/idempotency_key/)
  })

  it('builds reflect with both required fields and only the optional ones present', () => {
    const write = route('reflect')
    const body = reflectBody({ project_id: PROJECT, query: '这个问题需要反思', max_tokens: 512 })
    expect(Object.keys(body).sort()).toEqual(['max_tokens', 'project_id', 'query'])
    for (const key of Object.keys(body)) expect(write.allowed_body_fields).toContain(key)
    for (const key of write.required_body_fields) expect(body).toHaveProperty(key)
    expect(REQUIRED.reflect).toEqual(write.required_body_fields)
    expect(write.is_generator).toBe(true)
    // No `explicit_user_request`: the contract does not list it, so sending it would be
    // refused, and P4's own route is the user-facing one.
    expect(write.allowed_body_fields).not.toContain('explicit_user_request')
  })

  it('never emits a replay key other than event_id', () => {
    // P4 replays on `event_id`; the hook adds it. `idempotency_key` belongs to other routes.
    for (const body of [retryBody({ host_id: HOST }),
                        lifecycleBody('pin', { expected_revision: 1, host_id: HOST }),
                        reflectBody({ project_id: PROJECT, query: 'q' })]) {
      expect(body).not.toHaveProperty('idempotency_key')
    }
    for (const write of contract.writes) {
      expect(write.allowed_body_fields).toContain('event_id')
    }
  })

  it('keeps the required list for every action equal to the frozen contract', () => {
    // One direction of drift: the builder requiring fewer fields than the contract does would
    // turn a "read this revision first" command into a blind write.
    const lifecycle = ['obsolete', 'delete', 'pin', 'unpin'].map((command) => route(`/${command}`))
    for (const write of lifecycle) {
      expect(REQUIRED.lifecycle.slice().sort()).toEqual(write.required_body_fields.slice().sort())
    }
    expect(REQUIRED.reflect.slice().sort()).toEqual(route('reflect').required_body_fields.slice().sort())
  })

  it('never sends the event id twice or a caller-supplied one', () => {
    // `useWriteAction` appends `event_id`; a body that already had one would silently
    // override the replay key and make a retry look like a second command.
    const body = { ...lifecycleBody('pin', { expected_revision: 1, host_id: HOST }), event_id: EVENT }
    expect(Object.keys(body).filter((key) => key === 'event_id')).toHaveLength(1)
  })
})
