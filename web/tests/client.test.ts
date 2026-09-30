import { afterEach, describe, expect, it } from 'vitest'

import { CoreClient, buildQuery, parseErrorBody } from '../src/api/client'
import { CoreApiError, describeError } from '../src/api/errors'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
})

describe('buildQuery', () => {
  it('drops empty values so Core sees no filter rather than an empty one', () => {
    expect(buildQuery({ a: 1, b: null, c: undefined, d: '', e: 'x' })).toBe('?a=1&e=x')
    expect(buildQuery(undefined)).toBe('')
    expect(buildQuery({})).toBe('')
  })
})

describe('parseErrorBody', () => {
  it('reads the frozen Core envelope', () => {
    const error = parseErrorBody(
      403,
      JSON.stringify({
        error: { code: 'forbidden_scope', message: 'nope', request_id: 'aud_1', details: { required: 'x' } },
      }),
    )
    expect(error.kind).toBe('forbidden')
    expect(error.code).toBe('forbidden_scope')
    expect(error.requestId).toBe('aud_1')
    expect(error.details).toEqual({ required: 'x' })
  })

  it('does not crash on a non-JSON body from the proxy', () => {
    const error = parseErrorBody(502, '<html>bad gateway</html>')
    expect(error.code).toBe('http_502')
    // A gateway status means the proxy answered and Core did not: that is the
    // same condition as "the dashboard could not reach Core", not "unknown".
    expect(error.kind).toBe('unreachable')
    expect(describeError(error)).toContain('无法连接 Core')
  })

  it('classifies 401/404/409/422/503 distinctly', () => {
    expect(parseErrorBody(401, '{}').kind).toBe('unauthenticated')
    expect(parseErrorBody(404, '{}').kind).toBe('missing')
    expect(parseErrorBody(409, '{}').kind).toBe('conflict')
    expect(parseErrorBody(422, '{}').kind).toBe('invalid')
    expect(parseErrorBody(503, '{}').kind).toBe('degraded')
  })

  it('treats a stopped Core behind the proxy as unreachable, not unclassified', () => {
    const error = parseErrorBody(
      502,
      JSON.stringify({
        error: { code: 'proxy_core_unreachable', message: '127.0.0.1:8789: ECONNREFUSED' },
      }),
    )
    expect(error.kind).toBe('unreachable')
    expect(describeError(error)).toContain('无法连接 Core')
    // A plain 502 without the proxy code is still a gateway failure.
    expect(parseErrorBody(504, '{}').kind).toBe('unreachable')
    expect(parseErrorBody(502, '{}').kind).toBe('unreachable')
  })
})

describe('CoreClient', () => {
  it('only talks to its own origin and refuses an absolute URL', async () => {
    fake = installFakeCore({ 'GET /v1/health': { body: { status: 'ok' } } })
    const client = new CoreClient({ getToken: () => null })
    await expect(client.get('https://evil.invalid/v1/health')).rejects.toMatchObject({
      code: 'ui_absolute_url_refused',
    })
    expect(fake.requests).toHaveLength(0)
  })

  it('sends the bearer token in memory and a correlation id', async () => {
    fake = installFakeCore({ 'GET /v1/projects': { body: { projects: [], count: 0 } } })
    const client = new CoreClient({ getToken: () => 'tok_read_only' })
    await client.get('/projects', { query: { limit: 50, offset: 0 } })
    const [request] = fake.requests
    expect(request?.authorization).toBe('Bearer tok_read_only')
    expect(request?.requestId).toMatch(/^ui-/)
    expect(request?.path).toBe('/v1/projects')
    expect(request?.query.get('limit')).toBe('50')
  })

  it('sends no Authorization header when the token was cleared', async () => {
    fake = installFakeCore({ 'GET /v1/health': { body: { status: 'ok' } } })
    let token: string | null = 'tok'
    const client = new CoreClient({ getToken: () => token })
    await client.get('/health')
    token = null
    await client.get('/health')
    expect(fake.requests[0]?.authorization).toBe('Bearer tok')
    expect(fake.requests[1]?.authorization).toBeNull()
  })

  it('maps a 401 to an unauthenticated error carrying Core request_id', async () => {
    fake = installFakeCore({ 'GET /v1/projects': coreError(401, 'unauthenticated', 'no token') })
    const client = new CoreClient({ getToken: () => null })
    await expect(client.get('/projects')).rejects.toMatchObject({
      kind: 'unauthenticated',
      code: 'unauthenticated',
      status: 401,
      requestId: 'aud_test_request',
    })
  })

  it('maps an unroutable Core to unreachable rather than an empty result', async () => {
    fake = installFakeCore({}, () => ({ networkError: 'connect ECONNREFUSED' }))
    const client = new CoreClient({ getToken: () => 'tok' })
    await expect(client.get('/projects')).rejects.toMatchObject({
      kind: 'unreachable',
      code: 'ui_core_unreachable',
    })
  })

  it('propagates an abort instead of turning it into an error state', async () => {
    fake = installFakeCore({ 'GET /v1/projects': { body: { projects: [], count: 0 }, delayMs: 50 } })
    const client = new CoreClient({ getToken: () => 'tok' })
    const controller = new AbortController()
    const pending = client.get('/projects', { signal: controller.signal })
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('treats a 2xx non-JSON body as an explicit failure', async () => {
    const client = new CoreClient({
      getToken: () => 'tok',
      fetchImpl: async () =>
        new Response('<html>proxy landing page</html>', {
          status: 200,
          headers: { 'content-type': 'text/html' },
        }),
    })
    const error = await client.get('/health').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(CoreApiError)
    expect((error as CoreApiError).code).toBe('ui_unparsable_response')
  })
})
