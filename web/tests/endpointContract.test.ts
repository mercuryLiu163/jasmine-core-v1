/**
 * Endpoint ↔ proxy allowlist contract.
 *
 * The dashboard can only reach Core through its own origin, and that origin
 * forwards a fixed allowlist. This test calls *every* endpoint the UI has and
 * asserts that each resulting request is one the proxy would forward, with the
 * read-only method set. It fails if someone adds a client call the proxy does
 * not cover (which would look like a mysterious 404 in the browser). The reverse
 * direction — an allowlist entry no client uses — is not checked here; the Python
 * contract test covers allowlist ⊆ Core routes instead.
 */

import { afterEach, describe, expect, it } from 'vitest'

import allowlist from '../server/core-paths.json'
import { CoreClient } from '../src/api/client'
import { makeEndpoints } from '../src/api/endpoints'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

const ALLOWED_METHODS = new Set(allowlist.methods.map((method) => method.toUpperCase()))
const ALLOWED_PATHS = allowlist.paths.map((pattern) => new RegExp(pattern))
const ALLOWED_WRITE_PATHS = (allowlist.write_paths ?? []).map((pattern) => new RegExp(pattern))
// The P6-03b2 memory writes are a *gated* list, off by default, so they are checked against
// their own patterns rather than against the always-on milestone B list. A memory write
// appearing in `write_paths` would be forwarded to a baseline Core that cannot answer it.
const ALLOWED_P4_WRITE_PATHS = (allowlist.p4_write_paths ?? []).map((pattern) => new RegExp(pattern))
const ID_BODY = '01M4B6VGC29ESRDRQR62DX8AK8'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
})

function isAllowed(method: string, path: string): boolean {
  return ALLOWED_METHODS.has(method.toUpperCase()) && ALLOWED_PATHS.some((p) => p.test(path))
}

describe('every dashboard endpoint call is forwarded by the proxy', () => {
  it('issues only POSTs that the write allowlist carries', async () => {
    // GET-only coverage was a gap: a tenth write added to `endpoints.ts` but not to
    // `core-paths.json` `write_paths` would have gone unnoticed until runtime.
    fake = installFakeCore({}, () => ({ body: {} }))
    const client = new CoreClient({ getToken: () => 'tok' })
    const endpoints = makeEndpoints(client)
    const signal = new AbortController().signal
    const body = { host_id: ID_BODY, reason: 'r', expected_revision: 1 }
    const writes: Array<[string, Promise<unknown>]> = [
      ['/v1/interpretations/int_' + ID_BODY + '/correct', endpoints.correctInterpretation('int_' + ID_BODY, body, signal)],
      ['/v1/interpretations/int_' + ID_BODY + '/reject', endpoints.rejectInterpretation('int_' + ID_BODY, body, signal)],
      ['/v1/interpretations/int_' + ID_BODY + '/rerun', endpoints.rerunInterpretation('int_' + ID_BODY, body, signal)],
      ['/v1/reviews/rvw_' + ID_BODY + '/approve', endpoints.approveReview('rvw_' + ID_BODY, body, signal)],
      ['/v1/reviews/rvw_' + ID_BODY + '/reject', endpoints.rejectReview('rvw_' + ID_BODY, body, signal)],
      ['/v1/rules/proposals', endpoints.proposeRule(body, signal)],
      ['/v1/rules/rul_' + ID_BODY + '/approve', endpoints.approveRule('rul_' + ID_BODY, body, signal)],
      ['/v1/rules/rul_' + ID_BODY + '/supersede', endpoints.supersedeRule('rul_' + ID_BODY, body, signal)],
      ['/v1/rules/rul_' + ID_BODY + '/retire', endpoints.retireRule('rul_' + ID_BODY, body, signal)],
      // P6-03b2. Every one of these is in the gated `p4_write_paths` list, never in
      // `write_paths`: an unmerged dependency must not ride along in the always-on surface.
      [`/v1/memory/jobs/mjb_${ID_BODY}/retry`, endpoints.retryMemoryJob(`mjb_${ID_BODY}`, body, signal)],
      [`/v1/memory/items/mem_${ID_BODY}/obsolete`, endpoints.memoryLifecycle(`mem_${ID_BODY}`, 'obsolete', body, signal)],
      [`/v1/memory/items/mem_${ID_BODY}/delete`, endpoints.memoryLifecycle(`mem_${ID_BODY}`, 'delete', body, signal)],
      [`/v1/memory/items/mem_${ID_BODY}/pin`, endpoints.memoryLifecycle(`mem_${ID_BODY}`, 'pin', body, signal)],
      [`/v1/memory/items/mem_${ID_BODY}/unpin`, endpoints.memoryLifecycle(`mem_${ID_BODY}`, 'unpin', body, signal)],
      ['/v1/memory/reflect', endpoints.reflectMemory(body, signal)],
      // P6-02b: the seven Task/Step state writes. All seven are in the always-on
      // `write_paths` list because this baseline Core serves them (`handlers.py:115-124`),
      // unlike the gated P4 writes above.
      ['/v1/tasks', endpoints.createTask(body, signal)],
      [`/v1/tasks/tsk_${ID_BODY}/steps`, endpoints.createStep(`tsk_${ID_BODY}`, body, signal)],
      [`/v1/steps/stp_${ID_BODY}/transition`, endpoints.transitionStep(`stp_${ID_BODY}`, body, signal)],
      [`/v1/tasks/tsk_${ID_BODY}/transition`, endpoints.transitionTask(`tsk_${ID_BODY}`, body, signal)],
      [`/v1/tasks/tsk_${ID_BODY}/criteria`, endpoints.setTaskCriteria(`tsk_${ID_BODY}`, body, signal)],
      [`/v1/tasks/tsk_${ID_BODY}/accept`, endpoints.acceptTask(`tsk_${ID_BODY}`, body, signal)],
      [`/v1/steps/stp_${ID_BODY}/criteria`, endpoints.setStepCriteria(`stp_${ID_BODY}`, body, signal)],
    ]
    await Promise.allSettled(writes.map(([, promise]) => promise))
    const posts = fake!.requests.filter((request) => request.method === 'POST')
    const notForwarded = posts
      .filter((request) =>
        !ALLOWED_WRITE_PATHS.some((pattern) => pattern.test(request.path)) &&
        !ALLOWED_P4_WRITE_PATHS.some((pattern) => pattern.test(request.path)))
      .map((request) => `POST ${request.path}`)
    expect(notForwarded).toEqual([])
    expect(posts).toHaveLength(writes.length)
    // The two lists must stay disjoint, or "gated" and "always on" stop meaning anything.
    const alwaysOn = ALLOWED_WRITE_PATHS.map((pattern) => pattern.source)
    for (const pattern of ALLOWED_P4_WRITE_PATHS.map((p) => p.source)) {
      expect(alwaysOn).not.toContain(pattern)
    }
  })


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
      endpoints.getProject('prj_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listTasks('prj_01M4B6VGC29ESRDRQR62DX8AK8', { limit: 50, offset: 0 }, signal),
      endpoints.listTasks(null, { limit: 50, offset: 0 }, signal),
      endpoints.getTask('tsk_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listSteps('tsk_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.getStep('stp_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.taskHistory('tsk_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.stepHistory('stp_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listSessions(null, { limit: 50, offset: 0 }, signal),
      endpoints.listSessions('prj_01M4B6VGC29ESRDRQR62DX8AK8', { limit: 50, offset: 0 }, signal),
      endpoints.listEvents({ project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8', limit: 100 }, signal),
      endpoints.listEvents({ session_id: 'ses_01M4B6VGC29ESRDRQR62DX8AK8', after_seq: 7, limit: 100 }, signal),
      endpoints.getEvent('evt_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listActiveRules({ project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8' }, signal),
      endpoints.getRule('rul_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.getRuleHistory('rul_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listTaskEvidence('tsk_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.getEvidence('evd_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listInterpretations({ project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8', limit: 50 }, signal),
      endpoints.listInterpretations({ event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AK8', status: 'EXTRACTED', after_id: 'int_01M4B6VGC29ESRDRQR62DX8AK8', limit: 50 }, signal),
      endpoints.getInterpretation('int_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listPendingReviews({ project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8', after_id: 'rvw_01M4B6VGC29ESRDRQR62DX8AK8', limit: 50 }, signal),
      endpoints.getReview('rvw_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.previewResolution('int_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.getResolution('res_01M4B6VGC29ESRDRQR62DX8AK8', signal),
      endpoints.listHosts(signal),
      endpoints.listActors(signal),
    ])

    const paths = fake.requests.map((request) => `${request.method} ${request.path}`)
    const refused = fake.requests.filter((request) => !isAllowed(request.method, request.path))
    expect(refused.map((request) => `${request.method} ${request.path}`)).toEqual([])
    expect(paths.length).toBe(30)

    // The P2 collections page by an opaque `after_id` cursor, and the preview
    // requires an explicit interpretation_id - neither is a wildcard read.
    const preview = fake.requests.find((request) => request.path === '/v1/resolutions/preview')
    expect(preview?.query.get('interpretation_id')).toBe('int_01M4B6VGC29ESRDRQR62DX8AK8')
    expect(fake.requests.some((request) => request.path === '/v1/reviews/pending')).toBe(true)
    expect(fake.requests.some((request) => request.path === '/v1/reviews')).toBe(false)
    expect(fake.requests.some((request) => request.path === '/v1/resolutions')).toBe(false)

    // The sessions filter must be server-side, not a client-side slice.
    const filtered = fake.requests.find(
      (request) => request.path === '/v1/sessions' && request.query.get('project_id') === 'prj_01M4B6VGC29ESRDRQR62DX8AK8',
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
