/**
 * Subscribe to the hash route.
 *
 * `hashchange` fires for in-page anchor navigation, so every source-trace link
 * re-renders without a full page load while still being a real URL that survives
 * a refresh.
 */

import { useEffect, useState } from 'react'

import { parseHash, type Route } from './router'

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(globalThis.location?.hash ?? ''))
  useEffect(() => {
    const onChange = (): void => setRoute(parseHash(globalThis.location?.hash ?? ''))
    globalThis.addEventListener('hashchange', onChange)
    return () => globalThis.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
