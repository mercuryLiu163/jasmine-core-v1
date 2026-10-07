/**
 * Audited write behaviour (P6-02 milestone B).
 *
 * These pin the rules the handoff freezes, because they are the ones a UI can
 * quietly get wrong: reusing one idempotency key per intent, never auto-retrying
 * after a conflict, never turning an unknown result into a success, and never
 * inventing a disposition the server did not return.
 *
 * The last block covers the field *name*: Authority writes replay on a
 * client-minted `event_id` and refuse `idempotency_key` as an unknown field, so
 * sending the wrong one is a guaranteed 400.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CoreApiError } from '../src/api/errors'
import type { WriteResult } from '../src/api/endpoints'
import { classifyWriteError, useWriteAction } from '../src/app/useWriteAction'
import { isEventId, newEventId } from '../src/lib/ids'

describe('classifyWriteError', () => {
  it('treats revision and idempotency conflicts as conflicts, not failures', () => {
    for (const code of [
      'revision_conflict',
      'interpretation_not_current',
      'interpretation_not_ready',
      'idempotency_conflict',
      'application_conflict',
    ]) {
      expect(classifyWriteError(new CoreApiError({ kind: 'conflict', status: 409, code, message: code }))).toBe('conflict')
    }
  })

  it('separates refusal from an unknown result', () => {
    expect(classifyWriteError(new CoreApiError({ kind: 'forbidden', status: 403, code: 'forbidden_scope', message: 'x' }))).toBe('refused')
    expect(classifyWriteError(new CoreApiError({ kind: 'unauthenticated', status: 401, code: 'unauthenticated', message: 'x' }))).toBe('refused')
    expect(classifyWriteError(new CoreApiError({ kind: 'invalid', status: 400, code: 'invalid_request', message: 'x' }))).toBe('refused')
    expect(classifyWriteError(new CoreApiError({ kind: 'unreachable', status: null, code: 'ui_core_unreachable', message: 'x' }))).toBe('unknown')
    expect(classifyWriteError(new CoreApiError({ kind: 'degraded', status: 503, code: 'database_busy', message: 'x' }))).toBe('unknown')
  })
})

describe('useWriteAction', () => {
  const ok: WriteResult = { replayed: false, result: { review_id: 'rvw_1' } }

  it('reuses one idempotency key across retries of the same intent', async () => {
    const seen: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(async (body) => {
        seen.push(String(body['idempotency_key']))
        if (seen.length === 1) {
          throw new CoreApiError({ kind: 'conflict', status: 409, code: 'revision_conflict', message: 'moved' })
        }
        return ok
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('conflict')
    const afterConflict = result.current.writeKey
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(seen[0]).toBe(seen[1])
    expect(result.current.writeKey).toBe(afterConflict)
    expect(result.current.state).toBe('succeeded')
  })

  it('mints a new key only when the draft is reset', async () => {
    const { result } = renderHook(() => useWriteAction(async () => ok))
    const first = result.current.writeKey
    act(() => result.current.resetDraft())
    expect(result.current.writeKey).not.toBe(first)
    expect(result.current.state).toBe('idle')
    expect(result.current.result).toBeNull()
  })

  it('reports an unreachable Core as unknown with no fabricated result', async () => {
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        throw new CoreApiError({
          kind: 'unreachable',
          status: null,
          code: 'ui_core_unreachable',
          message: 'connection refused',
          requestId: 'ui-abc',
        })
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('unknown')
    expect(result.current.result).toBeNull()
    expect(result.current.requestId).toBe('ui-abc')
  })

  it('reports a missing scope as refused and keeps Core details', async () => {
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        throw new CoreApiError({
          kind: 'forbidden',
          status: 403,
          code: 'forbidden_scope',
          message: 'this key does not carry interpretations:manage',
          details: { required: 'interpretations:manage' },
        })
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('refused')
    expect(result.current.error?.details).toEqual({ required: 'interpretations:manage' })
  })

  it('ignores a second submit while one is in flight', async () => {
    let calls = 0
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        calls += 1
        await new Promise((resolve) => setTimeout(resolve, 20))
        return ok
      }),
    )
    await act(async () => {
      const first = result.current.submit({ reason: 'r', host_id: 'h1', expected_revision: 1 })
      const second = result.current.submit({ reason: 'r', host_id: 'h1', expected_revision: 1 })
      await Promise.all([first, second])
    })
    expect(calls).toBe(1)
  })

  it('passes Core\'s replayed flag through instead of claiming a new write', async () => {
    const { result } = renderHook(() => useWriteAction(async () => ({ replayed: true, result: {} })))
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    await waitFor(() => expect(result.current.state).toBe('succeeded'))
    expect(result.current.result?.replayed).toBe(true)
  })
})

describe('Authority writes key on event_id, not idempotency_key', () => {
  const ok: WriteResult = { replayed: false }

  it('sends a valid evt_ id as the replay key and never sends idempotency_key', async () => {
    const bodies: Array<Record<string, unknown>> = []
    const { result } = renderHook(() =>
      useWriteAction(
        async (body) => {
          bodies.push(body)
          return ok
        },
        { keyKind: 'event_id' },
      ),
    )
    expect(result.current.keyKind).toBe('event_id')
    await act(async () => {
      await result.current.submit({ host_id: 'hst_1', expected_revision: 2 })
    })
    expect(bodies).toHaveLength(1)
    const body = bodies[0] ?? {}
    // Core's `_fields` allow-list for approve/retire is exactly these three keys,
    // so an extra idempotency_key would be a 400 invalid_request.
    expect(Object.keys(body).sort()).toEqual(['event_id', 'expected_revision', 'host_id'])
    expect(isEventId(String(body['event_id']))).toBe(true)
    expect(body['idempotency_key']).toBeUndefined()
  })

  it('reuses the same event_id across a retry so Core can replay it', async () => {
    const keys: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(
        async (body) => {
          keys.push(String(body['event_id']))
          if (keys.length === 1) {
            throw new CoreApiError({
              kind: 'conflict',
              status: 409,
              code: 'revision_conflict',
              message: 'stale',
            })
          }
          return { replayed: true }
        },
        { keyKind: 'event_id' },
      ),
    )
    await act(async () => {
      await result.current.submit({ host_id: 'hst_1', expected_revision: 2 })
    })
    expect(result.current.state).toBe('conflict')
    await act(async () => {
      await result.current.submit({ host_id: 'hst_1', expected_revision: 3 })
    })
    expect(keys[0]).toBe(keys[1])
    expect(isEventId(keys[0] ?? '')).toBe(true)
  })

  it('mints a fresh event_id only on reset, so a new intent is a new command', async () => {
    const { result } = renderHook(() =>
      useWriteAction(async () => ok, { keyKind: 'event_id' }),
    )
    const first = result.current.writeKey
    act(() => result.current.resetDraft())
    expect(result.current.writeKey).not.toBe(first)
    expect(isEventId(result.current.writeKey)).toBe(true)
  })
})

describe('newEventId', () => {
  it('matches Core\'s `<prefix>_<26 Crockford base32>` id shape', () => {
    expect(newEventId()).toMatch(/^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$/)
  })

  it('does not repeat, so two intents cannot collide into one command', () => {
    const seen = new Set<string>()
    for (let index = 0; index < 200; index += 1) seen.add(newEventId())
    expect(seen.size).toBe(200)
  })

  it('refuses strings Core would reject as a malformed event id', () => {
    for (const bad of ['', 'evt_', 'evt_short', 'evt_' + 'I'.repeat(26), 'rul_' + 'A'.repeat(26)]) {
      expect(isEventId(bad)).toBe(false)
    }
  })
})
