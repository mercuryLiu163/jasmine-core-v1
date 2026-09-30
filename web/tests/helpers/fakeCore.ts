/**
 * Test double for the Core HTTP surface.
 *
 * It answers only the routes a test declares and records every request, so a
 * test can assert *what the UI asked for* (scope, paging cursor, absence of an
 * Authorization header after sign-out) rather than only what it rendered. An
 * undeclared route returns a Core-shaped 404, which is exactly what the real
 * server does for an unknown path.
 */

import { vi } from 'vitest'

export interface FakeReply {
  status?: number
  body?: unknown
  /** Simulate a proxy/Core that is not answering. */
  networkError?: string
  /** Delay before answering, for cancellation tests. */
  delayMs?: number
}

export interface RecordedRequest {
  method: string
  path: string
  query: URLSearchParams
  authorization: string | null
  requestId: string | null
}

export interface FakeCore {
  requests: RecordedRequest[]
  /** Requests grouped by `METHOD path`, ignoring the query string. */
  callsTo: (method: string, path: string) => RecordedRequest[]
  restore: () => void
}

type Handler = (request: RecordedRequest) => FakeReply | undefined

export function installFakeCore(
  routes: Record<string, FakeReply> = {},
  handler?: Handler,
): FakeCore {
  const requests: RecordedRequest[] = []
  const original = globalThis.fetch

  const fake = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const signal = init?.signal ?? null
    const abortError = (): DOMException => new DOMException('aborted', 'AbortError')
    if (signal?.aborted) throw abortError()
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(raw, 'http://dashboard.test')
    const headers = new Headers(init?.headers ?? {})
    const recorded: RecordedRequest = {
      method: (init?.method ?? 'GET').toUpperCase(),
      path: url.pathname,
      query: url.searchParams,
      authorization: headers.get('authorization'),
      requestId: headers.get('x-request-id'),
    }
    requests.push(recorded)

    const reply = handler?.(recorded) ?? routes[`${recorded.method} ${recorded.path}`]
    if (reply === undefined) {
      return new Response(
        JSON.stringify({
          error: { code: 'route_not_found', message: `no fake route for ${recorded.path}` },
        }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      )
    }
    if (reply.delayMs) {
      // Honour the caller's signal the way real `fetch` does, otherwise a
      // cancellation test would prove nothing.
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, reply.delayMs)
        signal?.addEventListener(
          'abort',
          () => {
            clearTimeout(timer)
            reject(abortError())
          },
          { once: true },
        )
      })
    }
    if (signal?.aborted) throw abortError()
    if (reply.networkError) throw new TypeError(reply.networkError)
    return new Response(JSON.stringify(reply.body ?? {}), {
      status: reply.status ?? 200,
      headers: { 'content-type': 'application/json' },
    })
  })

  globalThis.fetch = fake as unknown as typeof fetch
  return {
    requests,
    callsTo: (method, path) =>
      requests.filter((request) => request.method === method.toUpperCase() && request.path === path),
    restore: () => {
      globalThis.fetch = original
    },
  }
}

/** Core error envelope, shaped exactly like `errors.CoreError.to_payload`. */
export function coreError(status: number, code: string, message = code): FakeReply {
  return {
    status,
    body: { error: { code, message, request_id: 'aud_test_request', details: {} } },
  }
}
