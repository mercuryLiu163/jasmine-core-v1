/**
 * One async read, with cancellation and stale-response rejection (P6-A07).
 *
 * A resource is either loading, ready, or failed. There is no fourth state that
 * quietly renders an empty list: an aborted request resolves to nothing, and a
 * response that arrives after the effect was cleaned up (project switched, row
 * closed) is dropped instead of being written into the current view.
 *
 * When the caller's `deps` change — which is exactly "the operator changed the
 * scope" — the previous body is discarded immediately, so project A's rows can
 * never appear for even one frame under project B's heading.
 *
 * `loadedAt` records when the browser received the body, which is what the UI
 * must state next to any derived claim about "current" data.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { CoreApiError, isAbortError } from '../api/errors'

export type ResourceStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface Resource<T> {
  status: ResourceStatus
  data: T | null
  error: CoreApiError | null
  /** Browser-side read time of the currently displayed body. */
  loadedAt: string | null
  reload: () => void
}

interface ResourceState<T> {
  status: ResourceStatus
  data: T | null
  error: CoreApiError | null
  loadedAt: string | null
}

function signatureOf(deps: readonly unknown[]): string {
  return deps
    .map((value) =>
      value === null || value === undefined
        ? '∅'
        : typeof value === 'object'
          ? JSON.stringify(value)
          : String(value),
    )
    .join('\u0000')
}

export function useResource<T>(
  load: ((signal: AbortSignal) => Promise<T>) | null,
  deps: readonly unknown[],
): Resource<T> {
  const loadRef = useRef(load)
  useEffect(() => {
    loadRef.current = load
  })

  // A loader appearing or disappearing IS a state change, even when `deps` do not move.
  //
  // Views select between a list read and a by-id read with the same hook: in list mode the
  // by-id loader is `null`, and in by-id mode the list loader is `null`. With deps alone
  // (`[]` for a list), the signature was identical in both modes, so switching from a detail
  // to its list left the effect unrun: the resource stayed `idle`, the panel rendered with no
  // data and no error, and **no request was ever made**. Recorded here rather than patched in
  // each view, because the next view would forget.
  const signature = `${load === null ? 'none' : 'loader'}\u0000${signatureOf(deps)}`
  const [nonce, setNonce] = useState(0)
  const previousSignature = useRef(signature)
  const [state, setState] = useState<ResourceState<T>>({
    status: load === null ? 'idle' : 'loading',
    data: null,
    error: null,
    loadedAt: null,
  })

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    const current = loadRef.current
    if (current === null) {
      setState({ status: 'idle', data: null, error: null, loadedAt: null })
      previousSignature.current = signature
      return undefined
    }
    const scopeChanged = previousSignature.current !== signature
    previousSignature.current = signature
    const controller = new AbortController()
    let live = true
    setState((previous) => ({
      status: 'loading',
      data: scopeChanged ? null : previous.data,
      error: null,
      loadedAt: scopeChanged ? null : previous.loadedAt,
    }))
    current(controller.signal)
      .then((data) => {
        if (!live || controller.signal.aborted) return
        setState({ status: 'ready', data, error: null, loadedAt: new Date().toISOString() })
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted || isAbortError(error)) return
        setState({
          status: 'error',
          data: null,
          error:
            error instanceof CoreApiError
              ? error
              : new CoreApiError({
                  kind: 'unknown',
                  status: null,
                  code: 'ui_unexpected_error',
                  message: error instanceof Error ? error.message : String(error),
                }),
          loadedAt: null,
        })
      })
    return () => {
      live = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, signature])

  return { ...state, reload }
}
