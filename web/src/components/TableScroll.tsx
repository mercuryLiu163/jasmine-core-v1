/**
 * Horizontal scroll container for wide tables.
 *
 * At a narrow window a six-column table cannot fit; without this wrapper it
 * widens the whole document, which pushes source-trace controls around and makes
 * the page scroll sideways. Scrolling the table itself keeps the layout stable
 * and the buttons reachable (P6-A08).
 */

import type { ReactNode } from 'react'

export function TableScroll({ children, label }: { children: ReactNode; label: string }): JSX.Element {
  return (
    <div className="tablescroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  )
}
