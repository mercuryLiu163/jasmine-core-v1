import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { offsetLoader, usePagedResource } from '../src/app/usePagedResource'
import type { Cursor, PageResult } from '../src/app/usePagedResource'

interface Row {
  id: string
  scope: string
}

function pageLoader(
  pages: Record<string, PageResult<Row>[]>,
  options: { delayMs?: number } = {},
): (scope: string) => (cursor: Cursor | null, signal: AbortSignal) => Promise<PageResult<Row>> {
  return (scope) => async (cursor, signal) => {
    const index = cursor === null ? 0 : Number(cursor)
    if (options.delayMs) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, options.delayMs)
        signal.addEventListener(
          'abort',
          () => {
            clearTimeout(timer)
            reject(new DOMException('aborted', 'AbortError'))
          },
          { once: true },
        )
      })
    }
    const list = pages[scope] ?? []
    return list[index] ?? { items: [], count: 0, complete: true, nextCursor: null }
  }
}

describe('usePagedResource', () => {
  it('appends pages and only claims completeness from a short page', async () => {
    const pages: Record<string, PageResult<Row>[]> = {
      a: [
        { items: [{ id: '1', scope: 'a' }, { id: '2', scope: 'a' }], count: 2, complete: false, nextCursor: 1 },
        { items: [{ id: '3', scope: 'a' }], count: 1, complete: true, nextCursor: null },
      ],
    }
    const loader = pageLoader(pages)
    const { result } = renderHook(() => usePagedResource(loader('a'), ['a']))

    await waitFor(() => expect(result.current.items).toHaveLength(2))
    expect(result.current.complete).toBe(false)
    expect(result.current.hasMore).toBe(true)

    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.items).toHaveLength(3))
    expect(result.current.complete).toBe(true)
    expect(result.current.hasMore).toBe(false)
    expect(result.current.items.map((row) => row.id)).toEqual(['1', '2', '3'])
  })

  it('clears the previous scope instead of showing project A rows under project B', async () => {
    const pages: Record<string, PageResult<Row>[]> = {
      a: [{ items: [{ id: 'a1', scope: 'a' }], count: 1, complete: true, nextCursor: null }],
      b: [{ items: [{ id: 'b1', scope: 'b' }], count: 1, complete: true, nextCursor: null }],
    }
    const loader = pageLoader(pages)
    const { result, rerender } = renderHook(
      ({ scope }: { scope: string }) => usePagedResource(loader(scope), [scope]),
      { initialProps: { scope: 'a' } },
    )
    await waitFor(() => expect(result.current.items.map((row) => row.id)).toEqual(['a1']))

    rerender({ scope: 'b' })
    expect(result.current.items).toEqual([])
    await waitFor(() => expect(result.current.items.map((row) => row.id)).toEqual(['b1']))
  })

  it('drops a superseded response that arrives after the scope changed', async () => {
    const pages: Record<string, PageResult<Row>[]> = {
      slow: [{ items: [{ id: 'slow', scope: 'slow' }], count: 1, complete: true, nextCursor: null }],
      fast: [{ items: [{ id: 'fast', scope: 'fast' }], count: 1, complete: true, nextCursor: null }],
    }
    const slow = pageLoader(pages, { delayMs: 40 })
    const fast = pageLoader(pages)
    const { result, rerender } = renderHook(
      ({ scope }: { scope: string }) => usePagedResource(scope === 'slow' ? slow(scope) : fast(scope), [scope]),
      { initialProps: { scope: 'slow' } },
    )
    rerender({ scope: 'fast' })
    await waitFor(() => expect(result.current.items.map((row) => row.id)).toEqual(['fast']))
    // Give the aborted request time to (not) land.
    await new Promise((resolve) => setTimeout(resolve, 60))
    expect(result.current.items.map((row) => row.id)).toEqual(['fast'])
    expect(result.current.error).toBeNull()
  })

  it('surfaces a Core refusal as an error, not as an empty list', async () => {
    const loader = async (): Promise<PageResult<Row>> => {
      throw new Error('boom')
    }
    const { result } = renderHook(() => usePagedResource<Row>(loader, []))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.error?.code).toBe('ui_unexpected_error')
    expect(result.current.loading).toBe(false)
  })
})

describe('offsetLoader', () => {
  it('derives the next cursor from the page length, never from a total', async () => {
    const seen: Array<{ offset: number; limit: number }> = []
    const loader = offsetLoader<Row>({
      limit: 2,
      fetch: async (offset, limit) => {
        seen.push({ offset, limit })
        const all: Row[] = [
          { id: '1', scope: 'x' },
          { id: '2', scope: 'x' },
          { id: '3', scope: 'x' },
        ]
        const items = all.slice(offset, offset + limit)
        return { items, count: items.length }
      },
    })
    const first = await loader(null, new AbortController().signal)
    expect(first.nextCursor).toBe(2)
    expect(first.complete).toBe(false)
    const second = await loader(first.nextCursor, new AbortController().signal)
    expect(second.items.map((row) => row.id)).toEqual(['3'])
    expect(second.complete).toBe(true)
    expect(second.nextCursor).toBeNull()
    expect(seen).toEqual([
      { offset: 0, limit: 2 },
      { offset: 2, limit: 2 },
    ])
  })
})
