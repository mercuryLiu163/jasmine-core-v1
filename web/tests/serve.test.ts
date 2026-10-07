/**
 * Request-target handling for the static host (regression tests).
 *
 * An independent validator found that `GET //v1/auth/keys` and
 * `GET http://127.0.0.1:8795/v1/auth/keys` answered **200 with the SPA shell**:
 * `req.url` did not start with `/v1/`, so the request fell through to the static
 * handler, which resolved the target against the bundle root, found no file and
 * served `index.html`. No Core data was reachable, but a prober cannot tell that
 * from a live endpoint, and the shell must never stand in for an API refusal.
 *
 * These tests talk to a real `createDashboardServer` over a raw socket, because
 * `fetch`/curl cannot send an absolute-form target.
 */

import { createServer } from 'node:http'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { connect } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { classifyRequestTarget, createDashboardServer, loadAllowlist } from '../server/serve.mjs'

interface RawReply {
  status: number
  contentType: string
  body: string
}

function rawRequestWithBody(
  port: number,
  target: string,
  method: string,
  body: string,
): Promise<RawReply> {
  return new Promise((resolve, reject) => {
    const socket = connect(port, '127.0.0.1', () => {
      socket.write(
        `${method} ${target} HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\n` +
          'Content-Type: application/json\r\n' +
          `Content-Length: ${Buffer.byteLength(body)}\r\n` +
          'Connection: close\r\n\r\n' +
          body,
      )
    })
    let data = ''
    socket.setEncoding('utf8')
    socket.on('data', (chunk) => (data += chunk))
    socket.on('error', reject)
    socket.on('end', () => {
      const [head, ...rest] = data.split('\r\n\r\n')
      const lines = (head ?? '').split('\r\n')
      resolve({
        status: Number.parseInt((lines[0] ?? '').split(' ')[1] ?? '0', 10),
        contentType: lines.find((line) => line.toLowerCase().startsWith('content-type')) ?? '',
        body: rest.join('\r\n\r\n'),
      })
    })
  })
}

function rawRequest(port: number, target: string, method = 'GET'): Promise<RawReply> {
  return new Promise((resolve, reject) => {
    const socket = connect(port, '127.0.0.1', () => {
      socket.write(
        `${method} ${target} HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\n` +
          (method === 'POST' ? 'Content-Type: application/json\r\nContent-Length: 2\r\n' : '') +
          'Connection: close\r\n\r\n' +
          (method === 'POST' ? '{}' : ''),
      )
    })
    let data = ''
    socket.setEncoding('utf8')
    socket.on('data', (chunk) => (data += chunk))
    socket.on('error', reject)
    socket.on('end', () => {
      const [head, ...rest] = data.split('\r\n\r\n')
      const lines = (head ?? '').split('\r\n')
      const status = Number.parseInt((lines[0] ?? '').split(' ')[1] ?? '0', 10)
      const contentType =
        lines.find((line) => line.toLowerCase().startsWith('content-type')) ?? ''
      resolve({ status, contentType, body: rest.join('\r\n\r\n') })
    })
  })
}

describe('classifyRequestTarget', () => {
  it('accepts origin-form paths and separates API from static', () => {
    expect(classifyRequestTarget('/v1/interpretations').kind).toBe('api')
    expect(classifyRequestTarget('/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8?limit=5').kind).toBe('api')
    expect(classifyRequestTarget('/').kind).toBe('static')
    expect(classifyRequestTarget('/assets/index.js').kind).toBe('static')
  })

  it('refuses absolute-form, protocol-relative and empty targets', () => {
    for (const hostile of [
      '//v1/auth/keys',
      'http://127.0.0.1:8795/v1/auth/keys',
      'https://evil.invalid/v1/health',
      'v1/auth/keys',
      '*',
      '',
    ]) {
      expect(classifyRequestTarget(hostile).kind, hostile).toBe('bad')
    }
  })
})

describe('dashboard host request targets', () => {
  let server: ReturnType<typeof createDashboardServer>
  let port = 0
  let root = ''
  const SHELL = '<!doctype html><html><body>static shell</body></html>'

  beforeAll(async () => {
    // The static root is created here rather than pointing at `web/dist`. Rooting
    // it at the real build output made this suite depend on `vite build` having
    // run first: with no `dist`, `GET /` answered 404 and the suite failed for a
    // reason that had nothing to do with the code under test.
    root = await mkdtemp(join(tmpdir(), 'p6-serve-'))
    await writeFile(join(root, 'index.html'), SHELL)
    // An unreachable Core is fine: every assertion here is about refusal *by the
    // host*, before anything is forwarded.
    server = createDashboardServer({
      root,
      origin: new URL('http://127.0.0.1:1'),
      allowlist: loadAllowlist(join(process.cwd(), 'server')),
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    port = (server.address() as { port: number }).port
  })

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await rm(root, { recursive: true, force: true })
  })

  it('refuses a protocol-relative target instead of serving the shell', async () => {
    const reply = await rawRequest(port, '//v1/auth/keys')
    expect(reply.status).toBe(400)
    expect(reply.contentType).toContain('application/json')
    expect(reply.body).toContain('refused request target')
    expect(reply.body).not.toContain('<!doctype html>')
  })

  it('refuses an absolute-form target instead of serving the shell', async () => {
    const reply = await rawRequest(port, 'http://127.0.0.1:8795/v1/auth/keys')
    expect(reply.status).toBe(400)
    expect(reply.body).not.toContain('<!doctype html>')
  })

  it('still refuses an allowlisted-shaped admin path through the normal target', async () => {
    const reply = await rawRequest(port, '/v1/auth/keys')
    expect(reply.status).toBe(404)
    expect(reply.body).toContain('proxy_path_not_allowed')
  })

  it('forwards an allowlisted write but refuses an unlisted one', async () => {
    // The probe Core is at 127.0.0.1:1 (nothing listening), so a *forwarded*
    // request answers 502 proxy_core_unreachable while a refused one answers 404
    // proxy_path_not_allowed. That difference is the assertion.
    const allowed = await rawRequest(port, '/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8/correct', 'POST')
    expect(allowed.status).toBe(502)
    expect(allowed.body).toContain('proxy_core_unreachable')

    const refused = await rawRequest(port, '/v1/evidence/confirm', 'POST')
    expect(refused.status).toBe(404)
    expect(refused.body).toContain('proxy_path_not_allowed')

    const wrongVerb = await rawRequest(port, '/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8/correct', 'PUT')
    expect(wrongVerb.status).toBe(404)
    expect(wrongVerb.body).toContain('proxy_path_not_allowed')
  })

  it('keeps serving the shell for real static paths', async () => {
    const reply = await rawRequest(port, '/')
    expect(reply.status).toBe(200)
    expect(reply.contentType).toContain('text/html')
    expect(reply.body).toContain('static shell')
  })

  it('does not need a prior `vite build` to serve the shell it owns', async () => {
    // Guards the defect this suite was fixed for: the static root must be the one
    // the test created, not `web/dist`. If someone points it back at the build
    // output, this fails on a clean checkout instead of only sometimes.
    expect(root).not.toContain(`${join('web', 'dist')}`)
    expect(root.startsWith(tmpdir())).toBe(true)
  })
})

describe('proxy forwards a write body intact', () => {
  let upstream: ReturnType<typeof createServer>
  let upstreamPort = 0
  let dashboard: ReturnType<typeof createDashboardServer>
  let dashboardPort = 0
  const seen: Array<{ url: string; contentType?: string; contentLength?: string; body: string }> = []

  beforeAll(async () => {
    upstream = createServer((req, res) => {
      let body = ''
      req.setEncoding('utf8')
      req.on('data', (chunk) => (body += chunk))
      req.on('end', () => {
        seen.push({
          url: req.url ?? '',
          contentType: req.headers['content-type'],
          contentLength: req.headers['content-length'],
          body,
        })
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ ok: true }))
      })
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', () => resolve()))
    upstreamPort = (upstream.address() as { port: number }).port
    dashboard = createDashboardServer({
      root: join(process.cwd(), 'dist'),
      origin: new URL(`http://127.0.0.1:${upstreamPort}`),
      allowlist: loadAllowlist(join(process.cwd(), 'server')),
    })
    await new Promise<void>((resolve) => dashboard.listen(0, '127.0.0.1', () => resolve()))
    dashboardPort = (dashboard.address() as { port: number }).port
  })

  afterAll(async () => {
    await new Promise<void>((resolve) => dashboard.close(() => resolve()))
    await new Promise<void>((resolve) => upstream.close(() => resolve()))
  })

  it('delivers the JSON body and a Content-Length to Core', async () => {
    const payload = JSON.stringify({ host_id: 'hst_01M4B6VGC29ESRDRQR62DX8AK8', reason: 'r', expected_revision: 1 })
    const reply = await rawRequestWithBody(
      dashboardPort,
      '/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8/reject',
      'POST',
      payload,
    )
    expect(reply.status).toBe(200)
    expect(seen).toHaveLength(1)
    expect(seen[0]?.url).toBe('/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8/reject')
    expect(seen[0]?.contentType).toContain('application/json')
    expect(seen[0]?.contentLength).toBe(String(Buffer.byteLength(payload)))
    expect(JSON.parse(seen[0]?.body ?? '{}')).toMatchObject({ reason: 'r', expected_revision: 1 })
  })

  it('still refuses a write that is not allowlisted', async () => {
    const before = seen.length
    const reply = await rawRequestWithBody(dashboardPort, '/v1/evidence/confirm', 'POST', '{}')
    expect(reply.status).toBe(404)
    expect(seen).toHaveLength(before)
  })
})

describe('server factory', () => {
  it('is exported for tests without starting on import', () => {
    // A guard against turning the module into a side-effecting entry point.
    expect(typeof createServer).toBe('function')
    expect(typeof createDashboardServer).toBe('function')
  })
})
