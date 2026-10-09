/**
 * Audited write actions (P6-02 milestone B).
 *
 * The rules the handoff freezes are encoded here once, so every write panel
 * behaves the same way:
 *
 * 1. **One replay key per command, for as long as that command is unresolved.** It
 *    is created before the first submit and reused on every retry — including a
 *    retry after a conflict, where the operator has corrected the basis — because a
 *    retry must never look like a second command. It is replaced when the command
 *    *completes*, when the target changes, or when the operator resets the draft.
 *
 *    Both halves matter, and each was learned from a failure. Keeping the key after
 *    a success means the next command carries it too, and Core keys replay on the
 *    value alone, so it refuses the second command as a conflict with the first
 *    (`event_id_conflict` for Authority, `idempotency_conflict` for the rest) — an
 *    operator could not retire two Rules in one sitting. Minting a key per *submit*
 *    is the opposite error: a retry after a timeout becomes a second command.
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CoreApiError, isAbortedRequest, isAbortError } from '../api/errors'
import type { WriteResult } from '../api/endpoints'
import { newEventId } from '../lib/ids'

export type WriteState = 'idle' | 'submitting' | 'succeeded' | 'conflict' | 'refused' | 'unknown'

/** The body field Core replays on: its name is fixed per endpoint family. */
export type WriteKeyKind = 'idempotency_key' | 'event_id'

export interface WriteAction<T = WriteResult> {
  state: WriteState
  /** Core's response on success; null otherwise. */
  result: T | null
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

/** Codes that mean "Core refuses this command in this state".
 *
 * A `409` is not automatically a conflict to re-review. P4 answers `invalid_state_transition`
 * for a `DONE`/`RUNNING`/already-`PENDING` retry and for a lifecycle command the item's state
 * forbids: re-reading the object will not make it legal, so showing the conflict block ("Core's
 * current value is beside your draft, review and decide") would invite exactly the retry that
 * cannot succeed. It is a refusal, and it says so.
 */
const STATE_REFUSAL_CODES = new Set(['invalid_state_transition'])

export function classifyWriteError(error: CoreApiError): WriteState {
  if (CONFLICT_CODES.has(error.code)) return 'conflict'
  if (STATE_REFUSAL_CODES.has(error.code)) return 'refused'
  if (error.kind === 'unreachable') return 'unknown'
  if (error.kind === 'conflict') return 'conflict'
  if (error.kind === 'forbidden' || error.kind === 'unauthenticated') return 'refused'
  if (error.kind === 'invalid' || error.kind === 'missing') return 'refused'
  return 'unknown'
}

export interface WriteActionOptions {
  keyKind?: WriteKeyKind
  /**
   * Identity of the object being written — a rule id, an interpretation id, and so
   * on. It is part of the intent, so a key is never carried from one target to
   * another. Without it, two panels that share a hook instance (the Rule panels do,
   * while the operator moves between Rules) would send one key for two commands,
   * and Core would refuse the second as a conflict.
   */
  targetKey?: string
}

// The result type is a parameter because not every family's response is shaped like an
// Authority write: P4's reflect answers eight named keys and no command Event. Typing it here
// keeps each panel's `result` exact instead of forcing a cast that would hide a mismatch.
export function useWriteAction<T = WriteResult>(
  send: (body: Record<string, unknown>, signal: AbortSignal) => Promise<T>,
  options: WriteActionOptions = {},
): WriteAction<T> {
  const keyKind = options.keyKind ?? 'idempotency_key'
  const targetKey = options.targetKey ?? ''
  const [state, setState] = useState<WriteState>('idle')
  const [result, setResult] = useState<T | null>(null)
  const [error, setError] = useState<CoreApiError | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  // The key is state, not a ref: the panel displays it, and a ref mutated without a
  // render would leave the displayed key describing a previous intent. The lazy
  // initialiser runs once, so no randomness is spent on re-render.
  const [writeKey, setWriteKey] = useState<string>(() => newKey(keyKind))
  const inFlight = useRef(false)
  // Counted in state, not derived from `state`: a target change resets `state` to
  // 'idle' while a request is still outstanding, which left the button enabled and
  // then silently dropped the click. `busy` must describe the request, not the panel.
  const [pending, setPending] = useState(0)
  const sendRef = useRef(send)
  sendRef.current = send
  // Latest target, readable from an async continuation. A response belongs to the
  // target that issued it: if the operator has moved on, applying it here would show
  // one object's outcome under another.
  const targetRef = useRef(targetKey)
  targetRef.current = targetKey

  // A different target is a different command: it gets its own key, and the previous
  // target's result must not sit next to it. The condition compares the *previous*
  // target rather than a "first run" flag: `StrictMode` invokes effects twice on
  // mount, which would defeat a flag and re-mint the displayed key, while a comparison
  // is idempotent. Skipping the initial run is what keeps the key shown before any
  // submit identical to the key that is sent.
  const previousTarget = useRef(targetKey)
  useEffect(() => {
    if (previousTarget.current === targetKey) return
    previousTarget.current = targetKey
    setWriteKey(newKey(keyKind))
    setResult(null)
    setError(null)
    setRequestId(null)
    setState('idle')
  }, [targetKey, keyKind])

  const submit = useCallback(async (body: Record<string, unknown>) => {
    if (inFlight.current) return
    inFlight.current = true
    setPending((value) => value + 1)
    const issuedFor = targetKey
    setState('submitting')
    setError(null)
    const controller = new AbortController()
    try {
      const response = await sendRef.current(
        // `keyKind` is fixed for the lifetime of this hook instance, so the field
        // name cannot drift from the key that was minted for it.
        { ...body, [keyKind]: writeKey },
        controller.signal,
      )
      // The operator moved to another object while this was in flight. The command
      // did happen, but its outcome is not this object's, so it is dropped rather
      // than shown against the wrong target — the panel would otherwise read
      // "已提交" for a write the operator did not make here.
      if (targetRef.current !== issuedFor) return
      setResult(response)
      setState('succeeded')
      // The command is resolved. A further submit is a NEW command, so it must not
      // carry this key: Core would refuse it as a conflict with the body just sent.
      setWriteKey(newKey(keyKind))
    } catch (caught) {
      // Same reasoning on the failure path, and it matters more here: a timeout is
      // genuinely unresolved, and reporting it under the *new* target would attach an
      // unknown outcome to an object that was never written. The command's fate is in
      // Core's audit log and in the previous object's state, which is where the
      // operator can read it back; inventing a banner on this object would be worse
      // than silence.
      if (targetRef.current !== issuedFor) return
      // Both an abort surfaced by the caller's signal and the `CoreApiError` the client now
      // wraps one in (`ui_request_aborted`, spec §5 change 1) are the same unresolved state.
      // The request id is set here as well as in the `CoreApiError` branch below, because a
      // timeout is exactly the case where the operator cannot correlate the outcome and the
      // id is the only handle they have (spec §5 change 2).
      if (isAbortError(caught) || isAbortedRequest(caught)) {
        setState('unknown')
        setRequestId(caught instanceof CoreApiError ? caught.requestId : null)
        setError(
          caught instanceof CoreApiError
            ? caught
            : new CoreApiError({
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
        // No id is available for a failure that never came from the HTTP client. Clearing it
        // (rather than leaving a previous attempt's id) keeps the panel from naming the wrong
        // request; `WritePanel` renders the missing id as unknown.
        setRequestId(null)
        setState('unknown')
      }
    } finally {
      inFlight.current = false
      setPending((value) => value - 1)
    }
  }, [keyKind, writeKey])

  const resetDraft = useCallback(() => {
    setWriteKey(newKey(keyKind))
    setResult(null)
    setError(null)
    setRequestId(null)
    setState('idle')
  }, [keyKind])

  return useMemo(
    () => ({
      state,
      result,
      error,
      requestId,
      keyKind,
      writeKey,
      busy: pending > 0 || state === 'submitting',
      submit,
      resetDraft,
    }),
    [state, result, error, requestId, keyKind, writeKey, pending, submit, resetDraft],
  )
}
