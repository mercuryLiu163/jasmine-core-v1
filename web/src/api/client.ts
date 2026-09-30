/**
 * Core HTTP client.
 *
 * Rules this file exists to keep (P6-A01/A02/A07):
 *
 * - The token lives in memory and is attached per request. It is never written
 *   to localStorage/sessionStorage/IndexedDB, never put in a URL, never logged,
 *   and never echoed into an error message.
 * - Requests go to the same origin only (`/v1/...`); the dev proxy or the static
 *   host decides what that means. No absolute URL can be passed in.
 * - Every request carries a generated `X-Request-Id` so a UI action can be
 *   correlated with Core's audit row without reading audit.
 * - Callers pass an `AbortSignal`; `useResource` aborts the previous request
 *   when the scope changes, so a slow project-A response cannot land after the
 *   operator switched to project B.
 */

import { CoreApiError, isAbortError, kindForStatus, type CoreErrorEnvelope } from './errors'

const JSON_HEADERS = { 'content-type': 'application/json' }

export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  signal?: AbortSignal
  query?: Record<string, QueryValue>
  /** Test seam only: replaced in unit tests, never in the browser. */
  fetchImpl?: typeof fetch
}

export function buildQuery(query: Record<string, QueryValue> | undefined): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

function newRequestId(): string {
  const globalCrypto = globalThis.crypto
  if (globalCrypto && typeof globalCrypto.randomUUID === 'function') {
    return `ui-${globalCrypto.randomUUID()}`
  }
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Read Core's error envelope, tolerating a non-JSON body (proxy 502, HTML). */
export function parseErrorBody(status: number, body: string): CoreApiError {
  let parsed: unknown
  try {
    parsed = body ? JSON.parse(body) : null
  } catch {
    parsed = null
  }
  const envelope =
    isRecord(parsed) && isRecord(parsed['error'])
      ? (parsed['error'] as unknown as CoreErrorEnvelope)
      : null
  if (envelope && typeof envelope.code === 'string') {
    return new CoreApiError({
      kind: kindForStatus(status),
      status,
      code: envelope.code,
      message: typeof envelope.message === 'string' ? envelope.message : envelope.code,
      requestId: typeof envelope.request_id === 'string' ? envelope.request_id : null,
      details: envelope.details ?? null,
    })
  }
  return new CoreApiError({
    kind: kindForStatus(status),
    status,
    code: `http_${status}`,
    message: `Core (or the proxy) answered ${status} without a Core error envelope.`,
    details: body.slice(0, 400),
  })
}

export interface CoreClientOptions {
  /** Returns the in-memory token, or null before the operator pastes one. */
  getToken: () => string | null
  basePath?: string
  fetchImpl?: typeof fetch
}

export class CoreClient {
  private readonly getToken: () => string | null
  private readonly basePath: string
  private readonly fetchImpl: typeof fetch

  constructor(options: CoreClientOptions) {
    this.getToken = options.getToken
    this.basePath = options.basePath ?? '/v1'
    this.fetchImpl = options.fetchImpl ?? ((...args) => globalThis.fetch(...args))
  }

  private url(path: string, query: Record<string, QueryValue> | undefined): string {
    if (/^[a-z]+:/i.test(path) || path.startsWith('//')) {
      throw new CoreApiError({
        kind: 'unknown',
        status: null,
        code: 'ui_absolute_url_refused',
        message: 'the dashboard only talks to its own origin',
      })
    }
    const suffix = path.startsWith('/') ? path : `/${path}`
    return `${this.basePath}${suffix}${buildQuery(query)}`
  }

  async request<T>(
    method: 'GET' | 'POST',
    path: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<T> {
    const token = this.getToken()
    const headers: Record<string, string> = {
      accept: 'application/json',
      'x-request-id': newRequestId(),
    }
    if (token) headers['authorization'] = `Bearer ${token}`
    if (body !== undefined) Object.assign(headers, JSON_HEADERS)

    const fetchImpl = options.fetchImpl ?? this.fetchImpl
    let response: Response
    try {
      response = await fetchImpl(this.url(path, options.query), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: options.signal,
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
      })
    } catch (error) {
      if (error instanceof CoreApiError) throw error
      // `isAbortError` checks the name rather than `instanceof Error`: a
      // `DOMException` from `AbortController` is not an `Error` in every runtime,
      // and turning a cancellation into "Core unreachable" would invent a
      // failure the operator never had.
      if (isAbortError(error)) throw error
      throw new CoreApiError({
        kind: 'unreachable',
        status: null,
        code: 'ui_core_unreachable',
        message: 'the dashboard could not reach Core through its own origin',
        details: error instanceof Error ? error.message : String(error),
      })
    }

    const text = await response.text()
    if (!response.ok) throw parseErrorBody(response.status, text)
    if (!text) return undefined as T
    try {
      return JSON.parse(text) as T
    } catch {
      throw new CoreApiError({
        kind: 'unknown',
        status: response.status,
        code: 'ui_unparsable_response',
        message: 'Core answered 2xx with a body that is not JSON',
      })
    }
  }

  get<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.request<T>('GET', path, undefined, options)
  }

  post<T>(path: string, body: unknown, options: RequestOptions = {}): Promise<T> {
    return this.request<T>('POST', path, body, options)
  }
}
