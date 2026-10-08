/**
 * `useResource` when the loader itself appears or disappears.
 *
 * This pins the defect the browser Gate hit. Views select between a list read and a by-id read
 * with the same hook: in by-id mode the list loader is `null`, and in list mode the by-id loader
 * is `null`. With `deps` alone the resource signature was identical in both modes, so switching
 * from a detail to its list left the effect unrun — the resource stayed `idle`, the panel
 * rendered with no data, no loading and no error, and **no request was ever made**.
 *
 * The view-level version of this test was abandoned: driving the hash inside jsdom did not
 * re-route reliably, and a test that cannot reproduce the transition is worse than none. This
 * exercises the hook directly, which is where the fix is.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { usePagedResource, type Cursor, type PageResult } from '../src/app/usePagedResource'
import { useResource } from '../src/app/useResource'

/** A component that switches between "no loader" and "a loader", like list <-> detail. */
function Switcher({
  loader,
  deps,
}: {
  loader: (signal: AbortSignal) => Promise<string>
  deps: readonly unknown[]
}): JSX.Element {
  const [enabled, setEnabled] = useState(false)
  const resource = useResource(enabled ? loader : null, deps)
  return (
    <div>
      <button type="button" onClick={() => setEnabled(true)}>enable</button>
      <span data-testid="status">{resource.status}</span>
      <span data-testid="data">{resource.data ?? ''}</span>
    </div>
  )
}

describe('a loader appearing is a state change', () => {
  it('fetches when the loader goes from null to a function, even with constant deps', async () => {
    const loader = vi.fn(async () => 'loaded')
    const user = userEvent.setup()
    render(<Switcher loader={loader} deps={[]} />)

    // Nothing to load yet: idle, and no request.
    expect(screen.getByTestId('status').textContent).toBe('idle')
    expect(loader).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'enable' }))
    // The defect: with `[]` deps the signature never changed, so this stayed `idle` forever.
    await waitFor(() => expect(screen.getByTestId('data').textContent).toBe('loaded'))
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('fetches with non-empty deps too', async () => {
    const loader = vi.fn(async () => 'loaded')
    const user = userEvent.setup()
    render(<Switcher loader={loader} deps={['a']} />)
    await user.click(screen.getByRole('button', { name: 'enable' }))
    await waitFor(() => expect(screen.getByTestId('data').textContent).toBe('loaded'))
    expect(loader).toHaveBeenCalledTimes(1)
  })
})

describe('a loader disappearing clears the state', () => {
  function Vanisher(): JSX.Element {
    const [enabled, setEnabled] = useState(true)
    const resource = useResource(enabled ? async () => 'loaded' : null, [])
    return (
      <div>
        <button type="button" onClick={() => setEnabled(false)}>disable</button>
        <span data-testid="status">{resource.status}</span>
        <span data-testid="data">{resource.data ?? ''}</span>
      </div>
    )
  }

  it('drops back to idle and keeps no stale data', async () => {
    const user = userEvent.setup()
    render(<Vanisher />)
    await waitFor(() => expect(screen.getByTestId('data').textContent).toBe('loaded'))
    await user.click(screen.getByRole('button', { name: 'disable' }))
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('idle'))
    // The previous read's data must not linger once the loader is gone.
    expect(screen.getByTestId('data').textContent).toBe('')
  })
})


describe('the paged hook carries the same term', () => {
  // Not reachable at any current call site — every paged view passes a non-null loader — but the
  // two hooks do the same job, and fixing one and not the other is how the next view inherits
  // the bug. Behavioural on purpose: asserting the source string would pass on a rewrite that
  // reintroduces the defect elsewhere.
  function PagedSwitcher({ loader }: { loader: (cursor: Cursor | null, signal: AbortSignal) => Promise<PageResult<string>> }): JSX.Element {
    const [enabled, setEnabled] = useState(false)
    const paged = usePagedResource(enabled ? loader : null, [])
    return (
      <div>
        <button type="button" onClick={() => setEnabled(true)}>enable</button>
        <span data-testid="items">{paged.items.join(',')}</span>
      </div>
    )
  }

  it('fetches when a paged loader appears, even with constant deps', async () => {
    const loader = vi.fn(async (): Promise<PageResult<string>> => ({ items: ['page-one'], count: 1, nextCursor: null, complete: true }))
    const user = userEvent.setup()
    render(<PagedSwitcher loader={loader} />)
    expect(loader).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'enable' }))
    await waitFor(() => expect(screen.getByTestId('items').textContent).toBe('page-one'))
    expect(loader).toHaveBeenCalledTimes(1)
  })
})
