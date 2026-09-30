import { describe, expect, it } from 'vitest'

import {
  ProxyConfigError,
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

describe('isAllowedCoreRequest', () => {
  it('allows the frozen read paths the dashboard uses', () => {
    for (const path of [
      '/v1/health',
      '/v1/meta/schema',
      '/v1/projects',
      '/v1/projects/prj_01ABC',
      '/v1/tasks',
      '/v1/tasks/tsk_01ABC',
      '/v1/tasks/tsk_01ABC/steps',
      '/v1/tasks/tsk_01ABC/history',
      '/v1/tasks/tsk_01ABC/evidence',
      '/v1/steps/stp_01ABC',
      '/v1/steps/stp_01ABC/history',
      '/v1/events',
      '/v1/events/evt_01ABC',
      '/v1/sessions',
      '/v1/sessions/ses_01ABC',
      '/v1/rules/active',
      '/v1/rules/rul_01ABC',
      '/v1/rules/rul_01ABC/history',
      '/v1/evidence/evd_01ABC',
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

  it('is P6-01 read-only: no method other than GET is forwarded', () => {
    expect(isAllowedCoreRequest('POST', '/v1/tasks')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/events')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/rules/proposals')).toBe(false)
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
