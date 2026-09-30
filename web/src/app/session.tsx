/**
 * In-memory session state (P6-A01).
 *
 * The token is held in a React ref/state pair for the lifetime of the tab and
 * nothing else. There is deliberately no `localStorage`, `sessionStorage`,
 * `IndexedDB`, service worker or cache write anywhere in this bundle: a refresh
 * or "退出" clears the token, and a screenshot of the page shows only bullets.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { CoreClient } from '../api/client'
import { makeEndpoints, type Endpoints } from '../api/endpoints'

/** Scopes P6-01 asks the operator to grant a read-only dashboard token. */
export const REQUIRED_READ_SCOPES = [
  'objects:read',
  'events:read',
  'state:read',
  'authority:read',
  'evidence:read',
] as const

interface SessionValue {
  token: string | null
  signedIn: boolean
  signIn: (token: string) => void
  signOut: () => void
  endpoints: Endpoints
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }): JSX.Element {
  const [token, setToken] = useState<string | null>(null)
  // A ref keeps `getToken` stable so the client is not rebuilt on every render,
  // which would restart every in-flight request.
  const tokenRef = useRef<string | null>(null)

  const signIn = useCallback((next: string) => {
    const trimmed = next.trim()
    if (!trimmed) return
    tokenRef.current = trimmed
    setToken(trimmed)
  }, [])

  const signOut = useCallback(() => {
    tokenRef.current = null
    setToken(null)
  }, [])

  const endpoints = useMemo(() => {
    const client = new CoreClient({ getToken: () => tokenRef.current })
    return makeEndpoints(client)
  }, [])

  const value = useMemo<SessionValue>(
    () => ({ token, signedIn: token !== null, signIn, signOut, endpoints }),
    [token, signIn, signOut, endpoints],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (value === null) throw new Error('useSession must be used inside SessionProvider')
  return value
}
