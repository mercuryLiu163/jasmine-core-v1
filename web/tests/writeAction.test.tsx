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
import { ABORTED_REQUEST_CODE } from '../src/api/errors'
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

  it('treats P4 state refusals as refusals, though Core sends them as 409', () => {
    // A `DONE`/`RUNNING`/already-`PENDING` retry and a lifecycle command the item's state
    // forbids are 409s, but they are not conflicts to re-review: re-reading changes nothing.
    // Classifying them as conflicts would show "Core's current value is beside your draft —
    // review and retry", which is a retry that cannot succeed.
    expect(classifyWriteError(new CoreApiError({
      kind: 'conflict', status: 409, code: 'invalid_state_transition', message: 'done',
    }))).toBe('refused')
    expect(classifyWriteError(new CoreApiError({
      kind: 'forbidden', status: 403, code: 'forbidden_actor_kind', message: 'agent',
    }))).toBe('refused')
    expect(classifyWriteError(new CoreApiError({
      kind: 'invalid', status: 400, code: 'missing_expected_revision', message: 'guard',
    }))).toBe('refused')
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
    expect(result.current.state).toBe('succeeded')
    // The command is resolved, so the key shown now belongs to the *next* intent.
    expect(result.current.writeKey).not.toBe(afterConflict)
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

describe('the request id reaches the panel on every failure branch', () => {
  it('keeps the id the client attached to a wrapped abort', async () => {
    // Spec §5 change 2: CoreClient now wraps an abort in a CoreApiError carrying the id it
    // generated, and the abort branch must put that id on the action rather than drop it.
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        throw new CoreApiError({
          kind: 'unreachable',
          status: null,
          code: ABORTED_REQUEST_CODE,
          message: 'the request was aborted before an answer arrived; the result is unknown',
          requestId: 'ui-abort-9',
        })
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('unknown')
    expect(result.current.requestId).toBe('ui-abort-9')
    expect(result.current.result).toBeNull()
  })

  it('keeps unknown for a raw abort and leaves the id explicitly absent', async () => {
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        throw new DOMException('aborted', 'AbortError')
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('unknown')
    expect(result.current.requestId).toBeNull()
  })

  it('clears a stale id on a non-HTTP generic failure instead of naming the wrong request', async () => {
    let calls = 0
    const { result } = renderHook(() =>
      useWriteAction(async () => {
        calls += 1
        if (calls === 1) {
          throw new CoreApiError({
            kind: 'unreachable',
            status: null,
            code: 'ui_core_unreachable',
            message: 'refused',
            requestId: 'ui-first',
          })
        }
        throw new Error('a programming error, not an HTTP response')
      }),
    )
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.requestId).toBe('ui-first')
    await act(async () => {
      await result.current.submit({ reason: 'r', host_id: 'hst_1', expected_revision: 1 })
    })
    expect(result.current.state).toBe('unknown')
    expect(result.current.requestId).toBeNull()
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

describe('a replay key belongs to one intent, not one hook instance', () => {
  const ok: WriteResult = { replayed: false }

  it('does not carry the key to a different target', async () => {
    // The Rule panels keep one hook instance while the operator moves between Rules.
    // Reusing the key would make Core refuse the second retire as event_id_conflict
    // ("belongs to a different rule") — a legitimate action reported as a conflict.
    const keys: string[] = []
    const { result, rerender } = renderHook(
      ({ target }: { target: string }) =>
        useWriteAction(
          async (body) => {
            keys.push(`${target}:${String(body['event_id'])}`)
            return ok
          },
          { keyKind: 'event_id', targetKey: target },
        ),
      { initialProps: { target: 'rul_A' } },
    )
    await act(async () => {
      await result.current.submit({ expected_revision: 5, host_id: 'hst_1' })
    })
    rerender({ target: 'rul_B' })
    await act(async () => {
      await result.current.submit({ expected_revision: 5, host_id: 'hst_1' })
    })
    expect(keys).toHaveLength(2)
    const [first, second] = keys
    expect(first?.split(':')[1]).not.toBe(second?.split(':')[1])
    expect(isEventId(first?.split(':')[1] ?? '')).toBe(true)
    expect(isEventId(second?.split(':')[1] ?? '')).toBe(true)
  })

  it('keeps the key when a conflicted command is corrected and retried', async () => {
    // Nothing was written when Core answered 409, so the corrected retry is still the
    // same command and must stay a replay rather than become a second one.
    const keys: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(
        async (body) => {
          keys.push(String(body['idempotency_key']))
          if (keys.length === 1) {
            throw new CoreApiError({ kind: 'conflict', status: 409, code: 'rule_key_conflict', message: 'taken' })
          }
          return ok
        },
        { targetKey: 'rules/proposals' },
      ),
    )
    await act(async () => {
      await result.current.submit({ rule_key: 'ui.first' })
    })
    expect(result.current.state).toBe('conflict')
    await act(async () => {
      await result.current.submit({ rule_key: 'ui.second' })
    })
    expect(keys[0]).toBe(keys[1])
  })

  it('retires two different Rules with two different keys', async () => {
    // The failure this guards: one hook instance stays mounted while the operator
    // moves between Rules, so a key kept after success would be sent for the second
    // Rule and Core would answer event_id_conflict ("belongs to a different rule").
    const keys: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(
        async (body) => {
          keys.push(String(body['event_id']))
          return ok
        },
        { keyKind: 'event_id', targetKey: 'rul_A' },
      ),
    )
    await act(async () => {
      await result.current.submit({ expected_revision: 5, host_id: 'hst_1' })
    })
    expect(result.current.state).toBe('succeeded')
    act(() => result.current.resetDraft())
    await act(async () => {
      await result.current.submit({ expected_revision: 5, host_id: 'hst_1' })
    })
    expect(keys).toHaveLength(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('mints a new key after a successful command, so the next one is not a replay', async () => {
    const keys: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(async (body) => {
        keys.push(String(body['idempotency_key']))
        return ok
      }),
    )
    await act(async () => {
      await result.current.submit({ rule_key: 'ui.first' })
    })
    await act(async () => {
      await result.current.submit({ rule_key: 'ui.second' })
    })
    expect(keys).toHaveLength(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('still reuses the key for a retry of the identical intent', async () => {
    const keys: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(
        async (body) => {
          keys.push(String(body['idempotency_key']))
          if (keys.length === 1) {
            throw new CoreApiError({ kind: 'unreachable', status: null, code: 'ui_core_unreachable', message: 'timeout' })
          }
          return ok
        },
        { targetKey: 'int_1' },
      ),
    )
    const body = { reason: 'r', host_id: 'hst_1', expected_revision: 2 }
    await act(async () => {
      await result.current.submit(body)
    })
    await act(async () => {
      await result.current.submit({ ...body })
    })
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])
  })

  it('shows the key it will actually send on the first submit', async () => {
    // The panel displays the key before anything is sent; that key must be the one
    // used, or the disclosure is untrue.
    const sent: string[] = []
    const { result } = renderHook(() =>
      useWriteAction(async (body) => {
        sent.push(String(body['idempotency_key']))
        return ok
      }),
    )
    const displayed = result.current.writeKey
    await act(async () => {
      await result.current.submit({ reason: 'r' })
    })
    expect(sent[0]).toBe(displayed)
  })

  it('clears the previous target\'s result so it is not shown next to another', async () => {
    const { result, rerender } = renderHook(
      ({ target }: { target: string }) =>
        useWriteAction(async () => ok, { targetKey: target }),
      { initialProps: { target: 'rul_A' } },
    )
    await act(async () => {
      await result.current.submit({ expected_revision: 5 })
    })
    expect(result.current.state).toBe('succeeded')
    rerender({ target: 'rul_B' })
    await waitFor(() => expect(result.current.state).toBe('idle'))
    expect(result.current.result).toBeNull()
  })
})

describe('a response belongs to the target that issued it', () => {
  it('does not show a previous target\'s result under the new target', async () => {
    // The operator can move between Rules while a write is in flight. The command
    // still happened, but its outcome is not the new object's: showing "已提交" there
    // would claim a write the operator did not make on that object.
    let release: (() => void) | null = null
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const { result, rerender } = renderHook(
      ({ target }: { target: string }) =>
        useWriteAction(
          async () => {
            await gate
            return { replayed: false, result: { from: target } }
          },
          { keyKind: 'event_id', targetKey: target },
        ),
      { initialProps: { target: 'rul_A' } },
    )
    let pending: Promise<void> = Promise.resolve()
    await act(async () => {
      pending = result.current.submit({ expected_revision: 1 })
    })
    expect(result.current.state).toBe('submitting')
    rerender({ target: 'rul_B' })
    await waitFor(() => expect(result.current.state).toBe('idle'))
    await act(async () => {
      release?.()
      await pending
    })
    expect(result.current.state).toBe('idle')
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('does not surface a previous target\'s error under the new target', async () => {
    let release: (() => void) | null = null
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const { result, rerender } = renderHook(
      ({ target }: { target: string }) =>
        useWriteAction(
          async () => {
            await gate
            throw new CoreApiError({ kind: 'conflict', status: 409, code: 'revision_conflict', message: 'moved' })
          },
          { keyKind: 'event_id', targetKey: target },
        ),
      { initialProps: { target: 'rul_A' } },
    )
    let pending: Promise<void> = Promise.resolve()
    await act(async () => {
      pending = result.current.submit({ expected_revision: 1 })
    })
    rerender({ target: 'rul_B' })
    await waitFor(() => expect(result.current.state).toBe('idle'))
    await act(async () => {
      release?.()
      await pending
    })
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
  })
})

describe('the panel must not look idle while a request is outstanding', () => {
  it('keeps busy true across a target change until the request settles', async () => {
    // Without this, a target change reset `state` to idle while the previous request
    // was still in flight: the button re-enabled and a click was silently dropped.
    let release: (() => void) | null = null
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const { result, rerender } = renderHook(
      ({ target }: { target: string }) =>
        useWriteAction(
          async () => {
            await gate
            return { replayed: false }
          },
          { keyKind: 'event_id', targetKey: target },
        ),
      { initialProps: { target: 'rul_A' } },
    )
    let pending: Promise<void> = Promise.resolve()
    await act(async () => {
      pending = result.current.submit({ expected_revision: 1 })
    })
    expect(result.current.busy).toBe(true)
    rerender({ target: 'rul_B' })
    // The panel state resets, but the request has not settled, so it is still busy.
    expect(result.current.busy).toBe(true)
    await act(async () => {
      release?.()
      await pending
    })
    await waitFor(() => expect(result.current.busy).toBe(false))
  })
})
