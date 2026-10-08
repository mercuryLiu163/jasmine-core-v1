import { describe, expect, it } from 'vitest'

import {
  ProxyConfigError,
  classifyRequestTarget,
  isAllowedCoreRequest,
  resolveCoreOrigin,
} from '../server/core-proxy'

describe('resolveCoreOrigin', () => {
  it('defaults to the documented loopback Core', () => {
    expect(resolveCoreOrigin(undefined).origin).toBe('http://127.0.0.1:8787')
    expect(resolveCoreOrigin('  ').origin).toBe('http://127.0.0.1:8787')
    expect(resolveCoreOrigin('http://localhost:8789').port).toBe('8789')
  })

  it('refuses a non-loopback target so the proxy cannot be an SSRF gadget', () => {
    expect(() => resolveCoreOrigin('http://10.0.0.5:8787')).toThrow(ProxyConfigError)
    expect(() => resolveCoreOrigin('http://example.invalid')).toThrow(ProxyConfigError)
  })

  it('refuses TLS, embedded credentials and a path', () => {
    expect(() => resolveCoreOrigin('https://127.0.0.1:8787')).toThrow(ProxyConfigError)
    expect(() => resolveCoreOrigin('http://user:pass@127.0.0.1:8787')).toThrow(ProxyConfigError)
    expect(() => resolveCoreOrigin('http://127.0.0.1:8787/v1')).toThrow(ProxyConfigError)
    expect(() => resolveCoreOrigin('not a url')).toThrow(ProxyConfigError)
  })
})

describe('classifyRequestTarget (dev proxy)', () => {
  // The dev proxy and the static host must agree: an absolute-form or
  // protocol-relative target is refused before any allowlist logic runs, so it
  // can never fall through to Vite's static handling and receive the SPA shell.
  it('marks origin-form API paths as api and other paths as static', () => {
    expect(classifyRequestTarget('/v1/interpretations').kind).toBe('api')
    expect(classifyRequestTarget('/src/main.tsx').kind).toBe('static')
    expect(classifyRequestTarget('/').kind).toBe('static')
  })

  it('marks malformed targets as bad', () => {
    for (const hostile of ['//v1/auth/keys', 'http://127.0.0.1:8795/v1/auth/keys', '*', 'v1/x']) {
      expect(classifyRequestTarget(hostile).kind, hostile).toBe('bad')
    }
  })

  it('agrees with the static host on every case that matters', async () => {
    const serve = await import('../server/serve.mjs')
    for (const target of ['/v1/health', '/', '//v1/auth/keys', 'http://x/v1/health', '*']) {
      expect(serve.classifyRequestTarget(target).kind, target).toBe(
        classifyRequestTarget(target).kind,
      )
    }
  })
})

describe('isAllowedCoreRequest', () => {
  // The allowlist names Core's real id shape (`[a-z]{3}_` + 26 Crockford base32
  // characters, ADR 0001), so these fixtures are ids Core would accept rather than
  // the `prj_01ABC` / `int_1` placeholders a looser pattern used to tolerate.
  const ID_BODY = '01M4B6VGC29ESRDRQR62DX8AK8'
  const id = (prefix: string): string => `${prefix}_${ID_BODY}`

  it('allows the frozen read paths the dashboard uses', () => {
    for (const path of [
      '/v1/health',
      '/v1/meta/schema',
      '/v1/projects',
      `/v1/projects/${id('prj')}`,
      '/v1/tasks',
      `/v1/tasks/${id('tsk')}`,
      `/v1/tasks/${id('tsk')}/steps`,
      `/v1/tasks/${id('tsk')}/history`,
      `/v1/tasks/${id('tsk')}/evidence`,
      `/v1/steps/${id('stp')}`,
      `/v1/steps/${id('stp')}/history`,
      '/v1/events',
      `/v1/events/${id('evt')}`,
      '/v1/sessions',
      `/v1/sessions/${id('ses')}`,
      '/v1/rules/active',
      `/v1/rules/${id('rul')}`,
      `/v1/rules/${id('rul')}/history`,
      `/v1/evidence/${id('evd')}`,
      '/v1/hosts',
      '/v1/actors',
    ]) {
      expect(isAllowedCoreRequest('GET', path), path).toBe(true)
    }
  })

  it('never exposes admin surfaces through the browser proxy', () => {
    expect(isAllowedCoreRequest('GET', '/v1/auth/keys')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/auth/keys')).toBe(false)
    expect(isAllowedCoreRequest('GET', '/v1/audit')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/auth/keys/key_1/revoke')).toBe(false)
  })

  it('milestone B opens exactly the four frozen POST shapes', () => {
    for (const path of [
      `/v1/interpretations/${id('int')}/correct`,
      `/v1/interpretations/${id('int')}/reject`,
      `/v1/interpretations/${id('int')}/rerun`,
      `/v1/reviews/${id('rvw')}/approve`,
      `/v1/reviews/${id('rvw')}/reject`,
      '/v1/rules/proposals',
      `/v1/rules/${id('rul')}/approve`,
      `/v1/rules/${id('rul')}/supersede`,
      `/v1/rules/${id('rul')}/retire`,
    ]) {
      expect(isAllowedCoreRequest('POST', path), path).toBe(true)
    }
    // Deliberately not opened in this slice.
    for (const path of [
      '/v1/evidence/confirm',
      `/v1/resolutions/${id('res')}/manual-reapply`,
      `/v1/resolve/${id('int')}`,
      '/v1/tool-results',
      '/v1/workspace/fingerprint',
      '/v1/events',
      '/v1/tasks',
      '/v1/auth/keys',
      '/v1/audit',
    ]) {
      expect(isAllowedCoreRequest('POST', path), path).toBe(false)
    }
    // A write path must not be reachable with another verb, and a collection
    // must not become writable through the write section.
    expect(isAllowedCoreRequest('PUT', `/v1/interpretations/${id('int')}/correct`)).toBe(false)
    expect(isAllowedCoreRequest('GET', `/v1/interpretations/${id('int')}/correct`)).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/interpretations')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/reviews/pending')).toBe(false)
  })

  it('keeps the read surface GET-only even though writes exist', () => {
    expect(isAllowedCoreRequest('POST', '/v1/tasks')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/events')).toBe(false)
    // `rules/proposals` is open for milestone B; the state machine is not.
    expect(isAllowedCoreRequest('POST', '/v1/rules/proposals')).toBe(true)
    expect(isAllowedCoreRequest('POST', '/v1/tasks/tsk_1/transition')).toBe(false)
    expect(isAllowedCoreRequest('DELETE', '/v1/projects/prj_1')).toBe(false)
    // The browser is same-origin, so no preflight is needed; forwarding OPTIONS
    // only widened the surface (it reached Core and produced a real audit row).
    expect(isAllowedCoreRequest('OPTIONS', '/v1/health')).toBe(false)
    expect(isAllowedCoreRequest('OPTIONS', '/v1/tasks/tsk_1/transition')).toBe(false)
    for (const method of ['PUT', 'PATCH', 'HEAD', 'TRACE']) {
      expect(isAllowedCoreRequest(method, '/v1/health'), method).toBe(false)
    }
  })

  it('refuses traversal, absolute and non-Core targets', () => {
    expect(isAllowedCoreRequest('GET', '/v1/../../etc/passwd')).toBe(false)
    expect(isAllowedCoreRequest('GET', '//evil.invalid/v1/health')).toBe(false)
    expect(isAllowedCoreRequest('GET', 'http://evil.invalid/v1/health')).toBe(false)
    expect(isAllowedCoreRequest('GET', '/v1/projects/prj_1/../tasks')).toBe(false)
    expect(isAllowedCoreRequest('GET', '/v1/')).toBe(false)
    expect(isAllowedCoreRequest('GET', '/health')).toBe(false)
  })
})
