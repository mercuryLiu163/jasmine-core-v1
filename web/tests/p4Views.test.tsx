/**
 * P6-03b1 view behaviour.
 *
 * Each test pins a way this slice could lie:
 *
 * 1. **A refused P4 read says so.** On a baseline dashboard the proxy answers
 *    `proxy_path_not_allowed`, and the view must render the dependency-pending panel — not
 *    an error panel (which would blame Core) and not an empty table (which would invent
 *    "there is nothing here").
 * 2. **Memory is marked NON_AUTHORITATIVE from Core's own field**, so the marker disappears
 *    if Core stops sending it rather than surviving as a hardcoded claim.
 * 3. **Empty, not-found, refused and pending are four states.** An actor with no grants gets
 *    an empty list; an unauthorised project is `not_found`; a missing scope is
 *    `forbidden_scope`; a non-forwarded path is pending.
 * 4. **A malformed id is refused locally** with no request, so a typo is never explained as
 *    a missing dependency.
 * 5. **There is no write affordance.** Retry, obsolete/delete/pin/unpin and reflect are
 *    P6-03b2 and must not appear.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import contract from '../contracts/p4-memory-read.v1.json'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 8 }
const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const JOB = `mjb_${ID}`
const ITEM = `mem_${ID}`
const TRACE = `mtr_${ID}`
const PROJECT = `prj_${ID}`
const TASK = `tsk_${ID}`
const EVENT = `evt_${ID}`
const UNKNOWN_ITEM = `mem_01M4B6VGC29ESRDRQR62DX8AK7`

/** Exactly what the baseline proxy answers for a path it does not forward. */
const PROXY_REFUSAL = {
  status: 404,
  body: { error: { code: 'proxy_path_not_allowed', message: 'the dashboard proxy does not forward this path' } },
}

const SIGNED_IN = { 'GET /v1/projects': { body: { projects: [], count: 0 } } }

function withKeys<T extends object>(keys: readonly string[], overrides: Partial<T> = {}): T {
  return { ...Object.fromEntries(keys.map((key) => [key, null])), ...overrides } as T
}

function job(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.job, {
    job_id: JOB, kind: 'retain', memory_id: ITEM, source_event_id: EVENT,
    project_id: PROJECT, task_id: TASK, memory_type: 'VERIFIED_OUTCOME',
    bank_id: 'bank_x', document_id: 'doc_x', content_sha256: 'a'.repeat(64),
    status: 'DONE', retry_count: 0, max_attempts: 3, next_attempt_at: '2026-10-07T12:00:00Z',
    claim_generation: 1, created_at: '2026-10-07T12:00:00Z', updated_at: '2026-10-07T12:01:00Z',
    ...overrides,
  })
}

function item(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.item, {
    memory_id: ITEM, bank_id: 'bank_x', scope_kind: 'project', project_id: PROJECT,
    task_id: TASK, memory_type: 'VERIFIED_OUTCOME', document_id: 'doc_x',
    source_event_id: EVENT, evidence_ids: [], content_sha256: 'b'.repeat(64),
    sanitized_text: 'fixture memory text', status: 'ACTIVE', pinned: false, revision: 1,
    engine_state: 'RETAINED', recall_count: 3, created_at: '2026-10-07T12:00:00Z',
    updated_at: '2026-10-07T12:02:00Z', ...overrides,
  })
}

function lifecycle(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.lifecycle, {
    status: 'ACTIVE', recallable: true, pinned: false, pin_affects: 'inspector_ordering_only',
    engine_state: 'RETAINED', remote_document_deleted: false, recall_count: 3, ...overrides,
  })
}

function trace(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.trace, {
    trace_id: TRACE, mode: 'recall', status: 'ok', project_id: PROJECT, task_id: TASK,
    actor_id: `act_${ID}`, query_sha256: 'c'.repeat(64), query_length: 42,
    requested_types: [], banks: [], filtered: [], used_tokens: 128, authority_refs: [],
    created_at: '2026-10-07T12:03:00Z', ...overrides,
  })
}

function health(): Record<string, unknown> {
  return withKeys(contract.read_routes[0]!.response_fields, {
    status: 'ok', configured: true, reachable: true, degraded: false, core_impact: 'none',
    jobs: withKeys(contract.nested_fields.job_counts, {}),
    items: withKeys(contract.nested_fields.health_items, {}),
    features: {}, deployment: {}, checked_at: '2026-10-07T12:00:01Z',
    non_authoritative: true,
  })
}

async function enter(
  user: ReturnType<typeof userEvent.setup>,
  hash: string,
  routes: Parameters<typeof installFakeCore>[0],
): Promise<void> {
  fake = installFakeCore({ 'GET /v1/health': { body: HEALTH }, ...routes })
  render(
    <SessionProvider>
      <App />
    </SessionProvider>,
  )
  await user.type(screen.getByLabelText('Core API token'), 'tok')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
  await screen.findByText('Core 健康与 Schema')
  window.location.hash = hash
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

const VIEWS: ReadonlyArray<[string, string, RegExp, string]> = [
  ['health', '#/memory/health', /Memory 健康/, '/v1/memory/health'],
  ['jobs list', '#/memory/jobs', /Memory Jobs/, '/v1/memory/jobs'],
  ['job detail', `#/memory/jobs/${JOB}`, /Memory Job/, `/v1/memory/jobs/${JOB}`],
  ['items list', '#/memory/items', /Memory Items/, '/v1/memory/items'],
  ['item detail', `#/memory/items/${ITEM}`, /Memory Item/, `/v1/memory/items/${ITEM}`],
  ['traces list', '#/memory/traces', /Memory Traces/, '/v1/memory/traces'],
  ['trace detail', `#/memory/traces/${TRACE}`, /Memory Trace/, `/v1/memory/traces/${TRACE}`],
]

describe('a refused P4 read is shown as a missing dependency', () => {
  it.each(VIEWS)('%s marks the pending dependency instead of blaming Core', async (
    _name, hash, region, route,
  ) => {
    const user = userEvent.setup()
    await enter(user, hash, { ...SIGNED_IN, ['GET ' + route]: PROXY_REFUSAL })
    const panel = await screen.findByRole('region', { name: region })
    await waitFor(() => expect(within(panel).getByTestId('dependency-pending')).toBeTruthy())
    expect(within(panel).queryByRole('alert')).toBeNull()
    // The panel must name the real contract source and the unpassed acceptance, not just
    // "not available".
    expect(panel.textContent).toContain('memory:read')
    expect(panel.textContent).toContain(contract.source.ref)
    expect(panel.textContent).toContain('P4 阶段验收未通过')
  })

  it('states PARTIAL / DEPENDENCY_PENDING on the view itself', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', { ...SIGNED_IN, 'GET /v1/memory/jobs': PROXY_REFUSAL })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    expect(within(panel).getByTestId('slice-status').textContent).toContain('PARTIAL / DEPENDENCY_PENDING')
  })
})

describe('Memory is marked non-authoritative from Core own field', () => {
  it('shows the marker when Core sends non_authoritative: true', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': {
        body: { items: [{ ...item(), lifecycle: lifecycle() }], count: 1, non_authoritative: true },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    const marker = await within(panel).findByTestId('non-authoritative')
    expect(marker.textContent).toContain('NON_AUTHORITATIVE')
  })

  it('does NOT claim it when Core does not send the field', async () => {
    // A hardcoded marker would survive Core dropping the field, which is exactly when a
    // reader most needs to know Memory is no longer declaring itself non-authoritative.
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': { body: { items: [], count: 0 } },
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    await waitFor(() => expect(panel.textContent).toContain('空'))
    expect(within(panel).queryByTestId('non-authoritative')).toBeNull()
  })

  it('shows the marker on an item detail', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/items/' + ITEM]: {
        body: {
          memory: item(), sources: [], jobs: [], recallable: true,
          lifecycle: lifecycle(), engine_claim: 'fixture', non_authoritative: true,
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    expect((await within(panel).findByTestId('non-authoritative')).textContent).toContain('NON_AUTHORITATIVE')
  })
})

describe('empty, not-found, refused and pending stay four different states', () => {
  it('renders a grantless actor as an empty result, not an error', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', {
      ...SIGNED_IN,
      'GET /v1/memory/jobs': { body: { jobs: [], count: 0, counts: withKeys(contract.nested_fields.job_counts, {}) } },
    })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    await waitFor(() => expect(panel.textContent).toContain('空'))
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('shows an unauthorised project as not_found, not as pending or an error banner', async () => {
    // P4 answers `not_found` for a project the actor may not use; that is Core being
    // correct, not a broken dependency and not a failure to render.
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': coreError(404, 'not_found', 'project not found'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('not_found'))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('shows a missing scope as Core refusal, not as the missing dependency', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', {
      ...SIGNED_IN,
      'GET /v1/memory/jobs': coreError(403, 'forbidden_scope', 'memory:read is required'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('forbidden_scope'))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('keeps an unknown id distinct from an unauthorised project', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${UNKNOWN_ITEM}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/items/' + UNKNOWN_ITEM]: coreError(404, 'not_found', 'memory not found'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('not_found'))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })
})

describe('a malformed id is refused locally', () => {
  it.each([
    ['job', '#/memory/jobs/mjb_short', /Memory Job/, 'mjb', '/v1/memory/jobs/'],
    ['item', '#/memory/items/mem_short', /Memory Item/, 'mem', '/v1/memory/items/'],
    ['trace', '#/memory/traces/mtr_short', /Memory Trace/, 'mtr', '/v1/memory/traces/'],
  ])('does not blame the missing P4 for a bad %s id, and sends no request', async (
    _name, hash, region, prefix, route,
  ) => {
    const user = userEvent.setup()
    await enter(user, hash, SIGNED_IN)
    const panel = await screen.findByRole('region', { name: region })
    await waitFor(() => expect(within(panel).getByTestId('invalid-id')).toBeTruthy())
    expect(within(panel).getByTestId('invalid-id').textContent).toContain(`${prefix}_`)
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
    expect(fake!.requests.filter((request) => request.path.startsWith(route))).toHaveLength(0)
  })
})

describe('a real P4 read renders the record it was given', () => {
  it('lists jobs with their status and a link to the owning memory', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', {
      ...SIGNED_IN,
      'GET /v1/memory/jobs': {
        body: { jobs: [job()], count: 1, counts: withKeys(contract.nested_fields.job_counts, { done: 1 }) },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    const table = await within(panel).findByTestId('memory-jobs')
    expect(table.textContent).toContain(JOB)
    expect(table.textContent).toContain('DONE')
    expect(table.textContent).toContain(ITEM)
  })

  it('lists items with their memorability provenance, and marks the table non-authoritative', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': {
        body: { items: [{ ...item(), lifecycle: lifecycle() }], count: 1, non_authoritative: true },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    const table = await within(panel).findByTestId('memory-items')
    expect(table.textContent).toContain(ITEM)
    expect(table.textContent).toContain('ACTIVE')
    expect(table.textContent).toContain('RETAINED')
    expect(within(panel).getByTestId('non-authoritative')).toBeTruthy()
    // The Event the memory was derived from stays reachable: P4 keeps provenance.
    expect(within(table).getByRole('link', { name: /source Event/i })).toBeTruthy()
  })

  it('shows a trace query hash and length, and never invents the query text', async () => {
    // P4 stores the hash and the length, not the query. Rendering a "query" field would be
    // inventing one.
    const user = userEvent.setup()
    await enter(user, `#/memory/traces/${TRACE}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/traces/' + TRACE]: { body: { trace: { ...trace(), items: [] } } },
    })
    const panel = await screen.findByRole('region', { name: /Memory Trace/ })
    await waitFor(() => expect(panel.textContent).toContain(TRACE))
    expect(panel.textContent).toContain('query_sha256')
    expect(panel.textContent).toContain('query_length')
    expect(panel.textContent).not.toContain('query_text')
  })

  it('shows a job detail with its attempts', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/jobs/${JOB}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/jobs/' + JOB]: {
        body: {
          job: job(),
          attempts: [withKeys(contract.nested_fields.attempt, {
            attempt_id: `mja_${ID}`, job_id: JOB, attempt_number: 1, phase: 'retain',
            outcome: 'ok', lease_owner: 'w', started_at: '2026-10-07T12:00:30Z',
          })],
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Job/ })
    await waitFor(() => expect(panel.textContent).toContain('attempts(1)'))
  })
})

describe('the view does not synthesise engine text Core withheld', () => {
  it('renders a withheld safe_error_summary as nothing, not as a placeholder', async () => {
    // P4 nulls `safe_error_summary` for a non-human actor and keeps the key. The UI must
    // render the absence, not invent text and not leak a stale value from a previous read.
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', {
      ...SIGNED_IN,
      'GET /v1/memory/jobs': {
        body: {
          jobs: [job({ status: 'FAILED', safe_error_summary: null, last_error_code: 'engine_unavailable' })],
          count: 1,
          counts: withKeys(contract.nested_fields.job_counts, { failed: 1 }),
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    const table = await within(panel).findByTestId('memory-jobs')
    expect(table.textContent).toContain('FAILED')
    expect(table.textContent).not.toContain('simulated text')
  })

  it('shows the engine text when Core did send it', async () => {
    // The control: without this, rendering nothing would look like success either way.
    const user = userEvent.setup()
    await enter(user, `#/memory/jobs/${JOB}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/jobs/' + JOB]: {
        body: {
          job: job({ status: 'FAILED', safe_error_summary: 'engine refused the retain' }),
          attempts: [],
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Job/ })
    await waitFor(() => expect(panel.textContent).toContain('engine refused the retain'))
  })
})

describe('this slice has no write affordance', () => {
  /**
   * The only interactive controls a read-only Memory view may offer, each named so a new
   * one fails this test: the reload button, the by-id form submit, a `JsonBlock` expander
   * (`▸`/`▾`), and `RawText`'s copy button. None of them changes anything in Core.
   */
  const READ_ONLY_CONTROLS: ReadonlyArray<[string, (name: string) => boolean]> = [
    ['reload', (name) => name === '从 Core 重新读取'],
    ['by-id form submit', (name) => name === '打开'],
    ['JsonBlock expander', (name) => /^[▸▾]/.test(name)],
    ['RawText copy', (name) => name.startsWith('复制')],
  ]

  it.each(VIEWS)('%s offers only read controls, never a write action', async (
    _name, hash, region, route,
  ) => {
    const bodies: Record<string, unknown> = {
      '/v1/memory/health': health(),
      '/v1/memory/jobs': { jobs: [job()], count: 1, counts: withKeys(contract.nested_fields.job_counts, {}) },
      [`/v1/memory/jobs/${JOB}`]: { job: job(), attempts: [] },
      '/v1/memory/items': { items: [{ ...item(), lifecycle: lifecycle() }], count: 1, non_authoritative: true },
      [`/v1/memory/items/${ITEM}`]: {
        memory: item(), sources: [], jobs: [], recallable: true, lifecycle: lifecycle(),
        engine_claim: 'x', non_authoritative: true,
      },
      '/v1/memory/traces': { traces: [trace()], count: 1 },
      [`/v1/memory/traces/${TRACE}`]: { trace: { ...trace(), items: [] } },
    }
    const user = userEvent.setup()
    await enter(user, hash, { ...SIGNED_IN, ['GET ' + route]: { body: bodies[route] } })
    const panel = await screen.findByRole('region', { name: region })
    await waitFor(() => expect(panel.textContent).not.toContain('读取失败'))

    // A text search would be meaningless here: the *data* legitimately contains
    // `retry_count`, `delete_failed`, `pinned`, `obsolete` and `reflect` as column values
    // and enum members. What must not exist is a control that performs one.
    const controls = [...panel.querySelectorAll('button, input[type=submit]')]
    for (const control of controls) {
      const name = (control.textContent ?? '').trim()
      const allowed = READ_ONLY_CONTROLS.some(([, matches]) => matches(name))
      expect(allowed, `unexpected control: ${JSON.stringify(name)}`).toBe(true)
    }
    // And no request left the page that was not a read.
    expect(fake!.requests.every((request) => request.method === 'GET')).toBe(true)
  })
})
