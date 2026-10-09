/**
 * Test double for the Core HTTP surface.
 *
 * It answers only the routes a test declares and records every request, so a
 * test can assert *what the UI asked for* (scope, paging cursor, absence of an
 * Authorization header after sign-out, and — for writes — the exact body) rather
 * than only what it rendered. An undeclared route returns a Core-shaped 404,
 * which is exactly what the real server does for an unknown path.
 *
 * The body is recorded because Core validates write fields with an exact
 * allow-list: an extra `reason` or `idempotency_key` is a 400, so a test has to
 * be able to see the body the UI actually sent, not just that it sent one.
 */

import { vi } from 'vitest'

export interface FakeReply {
  status?: number
  body?: unknown
  /** Extra response headers, e.g. a proxy-generated `x-request-id` with no Core envelope. */
  headers?: Record<string, string>
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
  /** The parsed JSON body, or null for a body-less request. */
  body: Record<string, unknown> | null
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
      body: parseBody(init?.body),
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
      headers: { 'content-type': 'application/json', ...(reply.headers ?? {}) },
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

function parseBody(body: BodyInit | null | undefined): Record<string, unknown> | null {
  if (typeof body !== 'string' || body === '') return null
  try {
    const parsed: unknown = JSON.parse(body)
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/** Core error envelope, shaped exactly like `errors.CoreError.to_payload`. */
export function coreError(status: number, code: string, message = code): FakeReply {
  return {
    status,
    body: { error: { code, message, request_id: 'aud_test_request', details: {} } },
  }
}
