/**
 * Endpoint ↔ proxy allowlist contract.
 *
 * The dashboard can only reach Core through its own origin, and that origin
 * forwards a fixed allowlist. This test calls *every* endpoint the UI has and
 * asserts that each resulting request is one the proxy would forward, with the
 * read-only method set. It fails if someone adds a client call the proxy does
 * not cover (which would look like a mysterious 404 in the browser) or if the
 * allowlist gains a path nothing uses.
 */

import { afterEach, describe, expect, it } from 'vitest'

import allowlist from '../server/core-paths.json'
import { CoreClient } from '../src/api/client'
import { makeEndpoints } from '../src/api/endpoints'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

const ALLOWED_METHODS = new Set(allowlist.methods.map((method) => method.toUpperCase()))
const ALLOWED_PATHS = allowlist.paths.map((pattern) => new RegExp(pattern))

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
})

function isAllowed(method: string, path: string): boolean {
  return ALLOWED_METHODS.has(method.toUpperCase()) && ALLOWED_PATHS.some((p) => p.test(path))
}

describe('every dashboard endpoint call is forwarded by the proxy', () => {
  it('issues only GET requests on allowlisted paths', async () => {
    // Answer any declared Core path so the endpoint functions all resolve.
    fake = installFakeCore(
      {
        'GET /v1/projects': { body: { projects: [], count: 0 } },
        'GET /v1/projects/prj_x': { body: { project: {} } },
      },
      () => ({ body: {} }),
    )
    const client = new CoreClient({ getToken: () => 'tok' })
    const endpoints = makeEndpoints(client)
    const signal = new AbortController().signal

    await Promise.all([
      endpoints.health(signal),
      endpoints.metaSchema(signal),
      endpoints.listProjects({ limit: 50, offset: 0 }, signal),
      endpoints.getProject('prj_x', signal),
      endpoints.listTasks('prj_x', { limit: 50, offset: 0 }, signal),
      endpoints.listTasks(null, { limit: 50, offset: 0 }, signal),
      endpoints.getTask('tsk_x', signal),
      endpoints.listSteps('tsk_x', signal),
      endpoints.getStep('stp_x', signal),
      endpoints.taskHistory('tsk_x', signal),
      endpoints.stepHistory('stp_x', signal),
      endpoints.listSessions(null, { limit: 50, offset: 0 }, signal),
      endpoints.listSessions('prj_x', { limit: 50, offset: 0 }, signal),
      endpoints.listEvents({ project_id: 'prj_x', limit: 100 }, signal),
      endpoints.listEvents({ session_id: 'ses_x', after_seq: 7, limit: 100 }, signal),
      endpoints.getEvent('evt_x', signal),
      endpoints.listActiveRules({ project_id: 'prj_x' }, signal),
      endpoints.getRule('rul_x', signal),
      endpoints.getRuleHistory('rul_x', signal),
      endpoints.listTaskEvidence('tsk_x', signal),
      endpoints.getEvidence('evd_x', signal),
      endpoints.listHosts(signal),
      endpoints.listActors(signal),
    ])

    const paths = fake.requests.map((request) => `${request.method} ${request.path}`)
    const refused = fake.requests.filter((request) => !isAllowed(request.method, request.path))
    expect(refused.map((request) => `${request.method} ${request.path}`)).toEqual([])
    expect(paths.length).toBe(23)

    // The sessions filter must be server-side, not a client-side slice.
    const filtered = fake.requests.find(
      (request) => request.path === '/v1/sessions' && request.query.get('project_id') === 'prj_x',
    )
    expect(filtered).toBeDefined()
    expect(filtered?.query.get('limit')).toBe('50')

    // The UI must never ask for an admin surface, even by accident.
    for (const request of fake.requests) {
      expect(request.path.startsWith('/v1/auth/')).toBe(false)
      expect(request.path.startsWith('/v1/audit')).toBe(false)
    }
  })

  it('never sends an absolute URL, cookies or a body on a read', async () => {
    fake = installFakeCore({}, () => ({ body: {} }))
    const client = new CoreClient({ getToken: () => 'tok' })
    await client.get('/health')
    const [request] = fake.requests
    expect(request?.path.startsWith('/v1/')).toBe(true)
    await expect(client.get('https://evil.invalid/v1/health')).rejects.toMatchObject({
      code: 'ui_absolute_url_refused',
    })
  })
})
