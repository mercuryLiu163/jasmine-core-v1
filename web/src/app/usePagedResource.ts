/**
 * Paged reads (P6-A07).
 *
 * Two properties are non-negotiable and are why this is a hook rather than a
 * `useEffect` in each view:
 *
 * 1. Every page request is cancellable, and a response from a superseded scope
 *    is discarded — switching projects mid-flight cannot append project A's
 *    rows to project B's list.
 * 2. `count` from Core is *this page's* length, never a total. The hook only
 *    claims "complete" when a short page proves it; otherwise the UI says how
 *    many rows have been loaded, not how many exist.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { CoreApiError, isAbortError } from '../api/errors'

/**
 * Some Core collections page by `offset`, some by `after_seq`, and the P2 ones
 * by an opaque `after_id` string; the hook must not assume numbers.
 */
export type Cursor = string | number

export interface PageResult<T> {
  items: T[]
  /** Rows in this page, as returned by Core. */
  count: number
  /** Cursor for the next page, or null when the loader knows it is done. */
  nextCursor: Cursor | null
  /** True only when the loader proved there is nothing further. */
  complete: boolean
}

export interface Paged<T> {
  items: T[]
  pages: number
  loading: boolean
  error: CoreApiError | null
  hasMore: boolean
  complete: boolean
  loadedAt: string | null
  loadMore: () => void
  reload: () => void
}

type Loader<T> = (cursor: Cursor | null, signal: AbortSignal) => Promise<PageResult<T>>

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

interface Internal<T> {
  items: T[]
  pages: number
  loading: boolean
  error: CoreApiError | null
  complete: boolean
  loadedAt: string | null
}

const EMPTY = <T,>(): Internal<T> => ({
  items: [],
  pages: 0,
  loading: true,
  error: null,
  complete: false,
  loadedAt: null,
})

export function usePagedResource<T>(
  loader: Loader<T> | null,
  deps: readonly unknown[],
): Paged<T> {
  const loaderRef = useRef(loader)
  useEffect(() => {
    loaderRef.current = loader
  })

  // Same term as `useResource`: a loader appearing or disappearing is a state change even when
  // `deps` do not move. No call site reaches that shape today — every paged view passes a
  // non-null loader — but the two hooks exist for the same job, and fixing one and not the
  // other is how the next view inherits the bug.
  const signature = `${loader === null ? 'none' : 'loader'}\u0000${signatureOf(deps)}`
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState<Internal<T>>(EMPTY<T>)

  const cursorRef = useRef<Cursor | null>(null)
  const generationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  const run = useCallback(
    (mode: 'reset' | 'more') => {
      const current = loaderRef.current
      if (current === null) {
        setState({ ...EMPTY<T>(), loading: false })
        return
      }
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller
      const generation = generationRef.current + 1
      generationRef.current = generation
      if (mode === 'reset') {
        cursorRef.current = null
        setState(EMPTY<T>())
      } else {
        setState((previous) => ({ ...previous, loading: true, error: null }))
      }
      const cursor = mode === 'reset' ? null : cursorRef.current
      current(cursor, controller.signal)
        .then((page) => {
          if (generation !== generationRef.current || controller.signal.aborted) return
          cursorRef.current = page.nextCursor
          setState((previous) => {
            const items = mode === 'reset' ? page.items : [...previous.items, ...page.items]
            return {
              items,
              pages: (mode === 'reset' ? 0 : previous.pages) + 1,
              loading: false,
              error: null,
              complete: page.complete,
              loadedAt: new Date().toISOString(),
            }
          })
        })
        .catch((error: unknown) => {
          if (generation !== generationRef.current || controller.signal.aborted || isAbortError(error))
            return
          setState((previous) => ({
            ...previous,
            loading: false,
            error:
              error instanceof CoreApiError
                ? error
                : new CoreApiError({
                    kind: 'unknown',
                    status: null,
                    code: 'ui_unexpected_error',
                    message: error instanceof Error ? error.message : String(error),
                  }),
          }))
        })
    },
    [],
  )

  useEffect(() => {
    run('reset')
    return () => {
      controllerRef.current?.abort()
      generationRef.current += 1
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, nonce, run])

  const loadMore = useCallback(() => {
    if (cursorRef.current === null) return
    run('more')
  }, [run])

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  return {
    items: state.items,
    pages: state.pages,
    loading: state.loading,
    error: state.error,
    hasMore: !state.complete && cursorRef.current !== null,
    complete: state.complete,
    loadedAt: state.loadedAt,
    loadMore,
    reload,
  }
}

/** The offset/after_seq loaders own numeric cursors; fail loudly otherwise. */
function numericCursor(cursor: Cursor | null): number | null {
  if (cursor === null) return null
  if (typeof cursor !== 'number') {
    throw new Error(`this loader expects a numeric cursor, received ${typeof cursor}`)
  }
  return cursor
}

/** Offset paging for the object collections (`limit`/`offset`). */
export function offsetLoader<T>(options: {
  limit: number
  fetch: (offset: number, limit: number, signal: AbortSignal) => Promise<{ items: T[]; count: number }>
}): Loader<T> {
  return async (cursor, signal) => {
    const offset = numericCursor(cursor) ?? 0
    const page = await options.fetch(offset, options.limit, signal)
    const complete = page.count < options.limit
    return {
      items: page.items,
      count: page.count,
      complete,
      nextCursor: complete ? null : offset + page.count,
    }
  }
}

/** `after_seq` paging for `GET /v1/events`, which has no `offset`. */
export function afterSeqLoader<T extends { seq: number }>(options: {
  limit: number
  fetch: (afterSeq: number | null, limit: number, signal: AbortSignal) => Promise<{ items: T[]; count: number }>
}): Loader<T> {
  return async (cursor, signal) => {
    const page = await options.fetch(numericCursor(cursor), options.limit, signal)
    const last = page.items.length > 0 ? page.items[page.items.length - 1] : undefined
    const complete = page.count < options.limit
    return {
      items: page.items,
      count: page.count,
      complete,
      nextCursor: complete || last === undefined ? null : last.seq,
    }
  }
}

/** `after_id` paging for the P2 collections, which have no `offset`/`after_seq`. */
export function afterIdLoader<T>(options: {
  limit: number
  /** Returns the page plus the cursor Core reported (`next_after_id`). */
  fetch: (
    afterId: string | null,
    limit: number,
    signal: AbortSignal,
  ) => Promise<{ items: T[]; nextAfterId: string | null }>
}): Loader<T> {
  return async (cursor, signal) => {
    const afterId = cursor === null || cursor === undefined ? null : String(cursor)
    const page = await options.fetch(afterId, options.limit, signal)
    return {
      items: page.items,
      count: page.items.length,
      // Core says there is no further page by returning a null cursor; a full
      // page alone is not proof, which is why this trusts `next_after_id`.
      complete: page.nextAfterId === null,
      nextCursor: page.nextAfterId,
    }
  }
}
