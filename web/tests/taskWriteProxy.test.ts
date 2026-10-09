/**
 * P6-02b proxy surface: the seven always-on write patterns, and the request id a proxy refusal
 * must carry (spec §5 change 3, §10).
 *
 * The Python contract test (`tests/p6/test_dashboard_proxy_contract.py`) pins the allowlist
 * exactly and checks every write pattern against Core's real route table; this file pins the
 * JS-side behaviour those patterns produce: which concrete paths are forwarded, which
 * out-of-scope POSTs are still refused, and that a refusal this hop generates names a request.
 */

import { createServer } from 'node:http'
import type { IncomingMessage } from 'node:http'
import { describe, expect, it } from 'vitest'

import allowlist from '../server/core-paths.json'
import { isAllowedCoreRequest, proxyCoreRequest, requestIdFor } from '../server/core-proxy'
import { proxyRequestId } from '../server/serve.mjs'

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const id = (prefix: string): string => `${prefix}_${ID}`

const B_FAMILIES = [
  `^/v1/interpretations/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/(correct|reject|rerun)$`,
  `^/v1/reviews/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/(approve|reject)$`,
  '^/v1/rules/proposals$',
  `^/v1/rules/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/(approve|supersede|retire)$`,
]

const TASK_STEP_WRITES = [
  '^/v1/tasks$',
  `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/steps$`,
  `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$`,
  `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$`,
  `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/accept$`,
  `^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$`,
  `^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$`,
]

const TASK_STEP_PATHS = [
  '/v1/tasks',
  `/v1/tasks/${id('tsk')}/steps`,
  `/v1/tasks/${id('tsk')}/transition`,
  `/v1/tasks/${id('tsk')}/criteria`,
  `/v1/tasks/${id('tsk')}/accept`,
  `/v1/steps/${id('stp')}/transition`,
  `/v1/steps/${id('stp')}/criteria`,
]

describe('the always-on write allowlist', () => {
  it('pins the exact write_paths list, seven Task/Step patterns plus the four B families', () => {
    expect(allowlist.write_paths).toEqual([...B_FAMILIES, ...TASK_STEP_WRITES])
    expect(allowlist.write_methods).toEqual(['POST'])
  })

  it('leaves the GET list and methods untouched by the write slice', () => {
    expect(allowlist.methods).toEqual(['GET'])
    expect(allowlist.paths).toHaveLength(25)
    // The GET list is what it was: it carries the two collection/detail reads the seven reuse
    // (`/v1/tasks`, `/v1/tasks/{id}/steps`) and none of the mutation-only shapes.
    expect(allowlist.paths).toContain('^/v1/tasks$')
    expect(allowlist.paths).toContain(
      `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/(steps|history|evidence)$`,
    )
    for (const mutationOnly of [
      `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$`,
      `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$`,
      `^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/accept$`,
      `^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$`,
      `^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$`,
    ]) {
      expect(allowlist.paths, mutationOnly).not.toContain(mutationOnly)
    }
  })

  it('forwards each of the seven POSTs and still refuses the out-of-scope ones', () => {
    for (const path of TASK_STEP_PATHS) {
      expect(isAllowedCoreRequest('POST', path), path).toBe(true)
    }
    // The write list is POST-only even where the path is also a GET read: a mutation-only
    // shape is not reachable with GET, and a shared read path is not a write with another verb.
    expect(isAllowedCoreRequest('GET', `/v1/tasks/${id('tsk')}/transition`)).toBe(false)
    expect(isAllowedCoreRequest('GET', `/v1/tasks/${id('tsk')}/accept`)).toBe(false)
    expect(isAllowedCoreRequest('PUT', `/v1/tasks/${id('tsk')}/transition`)).toBe(false)
    expect(isAllowedCoreRequest('DELETE', `/v1/tasks/${id('tsk')}/criteria`)).toBe(false)
    for (const path of [
      '/v1/evidence/confirm',
      '/v1/workspaces/fingerprint',
      '/v1/workspace/fingerprint',
      '/v1/tool-results',
      `/v1/resolve/${id('int')}`,
      `/v1/resolutions/${id('res')}/manual-reapply`,
      '/v1/audit',
      '/v1/auth/keys',
    ]) {
      expect(isAllowedCoreRequest('POST', path), path).toBe(false)
    }
    // A malformed id is not one of the seven.
    expect(isAllowedCoreRequest('POST', '/v1/tasks/tsk_short/transition')).toBe(false)
    expect(isAllowedCoreRequest('POST', '/v1/tasks/tsk_1/accept')).toBe(false)
  })
})

describe('a proxy refusal names the request', () => {
  it('echoes an incoming X-Request-Id and mints one when there is none', () => {
    const withId = { headers: { 'x-request-id': 'ui-abc-123' } } as unknown as IncomingMessage
    expect(requestIdFor(withId)).toBe('ui-abc-123')
    expect(proxyRequestId(withId)).toBe('ui-abc-123')
    const empty = { headers: {} } as unknown as IncomingMessage
    expect(requestIdFor(empty)).toMatch(/^proxy-/)
    expect(proxyRequestId(empty)).toMatch(/^proxy-/)
    // A blank header is not an id.
    const blank = { headers: { 'x-request-id': '   ' } } as unknown as IncomingMessage
    expect(requestIdFor(blank)).toMatch(/^proxy-/)
  })

  it('sets the header on the proxy refusal and on the 502 core-unreachable', async () => {
    // A real server using the exported Vite-plugin handler, with nothing listening upstream.
    const server = createServer((req, res) => proxyCoreRequest(req, res, new URL('http://127.0.0.1:1')))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    const port = (server.address() as { port: number }).port
    try {
      const refused = await fetch(`http://127.0.0.1:${port}/v1/evidence/confirm`, {
        method: 'POST',
        headers: { 'x-request-id': 'ui-refused-1', 'content-type': 'application/json' },
        body: '{}',
      })
      expect(refused.status).toBe(404)
      expect(await refused.text()).toContain('proxy_path_not_allowed')
      expect(refused.headers.get('x-request-id')).toBe('ui-refused-1')

      const unreachable = await fetch(`http://127.0.0.1:${port}/v1/tasks`, {
        method: 'POST',
        headers: { 'x-request-id': 'ui-unreachable-1', 'content-type': 'application/json' },
        body: '{}',
      })
      expect(unreachable.status).toBe(502)
      expect(await unreachable.text()).toContain('proxy_core_unreachable')
      expect(unreachable.headers.get('x-request-id')).toBe('ui-unreachable-1')

      // No incoming id: this hop still answers with one rather than with none.
      const minted = await fetch(`http://127.0.0.1:${port}/v1/evidence/confirm`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      })
      expect(minted.headers.get('x-request-id')).toMatch(/^proxy-/)
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })
})
