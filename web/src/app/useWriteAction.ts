/**
 * Audited write actions (P6-02 milestone B).
 *
 * The rules the handoff freezes are encoded here once, so every write panel
 * behaves the same way:
 *
 * 1. **One replay key per intent.** It is created on the first submit and
 *    reused on every retry of that same intent; `resetDraft` starts a new intent
 *    and only then gets a new key. A retry after a timeout must never mint a new
 *    key, because Core would treat it as a second command.
 *
 *    Which key that is depends on the endpoint family, and the difference is
 *    Core's, not a preference: interpretation and review writes take an
 *    `idempotency_key`, while Authority writes take a client-minted `event_id`
 *    and *reject* `idempotency_key` as an unknown field. `keyKind` carries that
 *    distinction so a panel cannot send the wrong one.
 * 2. **The basis is read, not guessed.** The caller passes the revision it read;
 *    Core compares it and answers `409` when the object moved.
 * 3. **A conflict keeps the draft.** `revision_conflict`, `interpretation_not_current`,
 *    `idempotency_conflict` and `application_conflict` all land in `conflict`
 *    state with Core's details, so the operator can compare and re-review rather
 *    than resubmit blindly.
 * 4. **Unknown is its own state.** A timeout or a network failure becomes
 *    `unknown`, with the request id and a readback action — never a silent
 *    retry and never a success.
 * 5. **Only Core's answer counts.** Success is whatever Core returned
 *    (including `replayed: true`); the hook never fabricates a result.
 */

import { useCallback, useMemo, useRef, useState } from 'react'

import { CoreApiError, isAbortError } from '../api/errors'
import type { WriteResult } from '../api/endpoints'
import { newEventId } from '../lib/ids'

export type WriteState = 'idle' | 'submitting' | 'succeeded' | 'conflict' | 'refused' | 'unknown'

/** The body field Core replays on: its name is fixed per endpoint family. */
export type WriteKeyKind = 'idempotency_key' | 'event_id'

export interface WriteAction {
  state: WriteState
  /** Core's response on success; null otherwise. */
  result: WriteResult | null
  /** The refusal or failure, with kind/code/details; null when none. */
  error: CoreApiError | null
  /** The request id of the last attempt, for correlation and readback. */
  requestId: string | null
  /** The field name the replay key is sent in: `idempotency_key` or `event_id`. */
  keyKind: WriteKeyKind
  /** The replay key this intent will reuse; its field name is `keyKind`. */
  writeKey: string
  /** True while a request is in flight, so a double click cannot double-send. */
  busy: boolean
  submit: (body: Record<string, unknown>) => Promise<void>
  /** Start a new intent: new key, cleared result and error. */
  resetDraft: () => void
}

function newKey(kind: WriteKeyKind): string {
  return kind === 'event_id' ? newEventId() : newIdempotencyKey()
}

function newIdempotencyKey(): string {
  const globalCrypto = globalThis.crypto
  if (globalCrypto && typeof globalCrypto.randomUUID === 'function') {
    return `ui-${globalCrypto.randomUUID()}`
  }
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/** Conflicts are the states where Core asks the operator to look again. */
const CONFLICT_CODES = new Set([
  'revision_conflict',
  'interpretation_not_current',
  'interpretation_not_ready',
  'idempotency_conflict',
  'application_conflict',
  'rule_key_conflict',
  'source_event_duplicate',
  'event_id_conflict',
])

export function classifyWriteError(error: CoreApiError): WriteState {
  if (CONFLICT_CODES.has(error.code)) return 'conflict'
  if (error.kind === 'unreachable') return 'unknown'
  if (error.kind === 'conflict') return 'conflict'
  if (error.kind === 'forbidden' || error.kind === 'unauthenticated') return 'refused'
  if (error.kind === 'invalid' || error.kind === 'missing') return 'refused'
  return 'unknown'
}

export function useWriteAction(
  send: (body: Record<string, unknown>, signal: AbortSignal) => Promise<WriteResult>,
  options: { keyKind?: WriteKeyKind } = {},
): WriteAction {
  const keyKind = options.keyKind ?? 'idempotency_key'
  const [state, setState] = useState<WriteState>('idle')
  const [result, setResult] = useState<WriteResult | null>(null)
  const [error, setError] = useState<CoreApiError | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  // Stable per intent: regenerated only when the draft is reset or submitted
  // successfully (a new intent after a refusal keeps the same key so a retry of
  // the *same* intent is a replay, not a second command).
  const keyRef = useRef<string>(newKey(keyKind))
  const inFlight = useRef(false)
  const sendRef = useRef(send)
  sendRef.current = send

  const submit = useCallback(async (body: Record<string, unknown>) => {
    if (inFlight.current) return
    inFlight.current = true
    setState('submitting')
    setError(null)
    const controller = new AbortController()
    try {
      const response = await sendRef.current(
        // `keyKind` is fixed for the lifetime of this hook instance, so the field
        // name cannot drift from the key that was minted for it.
        { ...body, [keyKind]: keyRef.current },
        controller.signal,
      )
      setResult(response)
      setState('succeeded')
    } catch (caught) {
      if (isAbortError(caught)) {
        setState('unknown')
        setError(
          new CoreApiError({
            kind: 'unreachable',
            status: null,
            code: 'ui_write_aborted',
            message: '请求被中断,结果未知;请读回对象或 command Event 再决定',
          }),
        )
      } else if (caught instanceof CoreApiError) {
        setError(caught)
        setRequestId(caught.requestId)
        setState(classifyWriteError(caught))
      } else {
        setError(
          new CoreApiError({
            kind: 'unknown',
            status: null,
            code: 'ui_write_failed',
            message: caught instanceof Error ? caught.message : String(caught),
          }),
        )
        setState('unknown')
      }
    } finally {
      inFlight.current = false
    }
  }, [])

  const resetDraft = useCallback(() => {
    keyRef.current = newKey(keyKind)
    setResult(null)
    setError(null)
    setRequestId(null)
    setState('idle')
    setNonce((value) => value + 1)
  }, [keyKind])

  return useMemo(
    () => ({
      state,
      result,
      error,
      requestId,
      keyKind,
      writeKey: keyRef.current,
      busy: state === 'submitting',
      submit,
      resetDraft,
    }),
    // `nonce` is what makes a reset visible to consumers that read the key.
    [state, result, error, requestId, keyKind, nonce, submit, resetDraft],
  )
}
