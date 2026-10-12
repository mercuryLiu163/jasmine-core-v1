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

/**
 * A trace **list** row: exactly the contract's 16 keys.
 *
 * This used to also carry `filtered` and `authority_refs`, which the list row does not have —
 * so the fixture described an 18-key row while the contract and the fixture said 16, and the
 * 16-vs-20 split was guarded only at the Python layer.
 */
function trace(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.trace, {
    trace_id: TRACE, mode: 'recall', status: 'ok', project_id: PROJECT, task_id: TASK,
    actor_id: `act_${ID}`, query_sha256: 'c'.repeat(64), query_length: 42,
    requested_types: [], banks: [], used_tokens: 128,
    created_at: '2026-10-07T12:03:00Z', ...overrides,
  })
}

/** A trace **detail**: the list row plus the four fields only `SELECT *` + the join carries. */
function traceDetail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...trace(),
    filtered: [], authority_refs: [], state_revision: 7,
    items: [{ result_id: `mqr_${ID}`, rank: 1, memory_id: ITEM, score: 0.9 }],
    ...overrides,
  }
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
    // The proxy refuses with HTTP 404 *and* `proxy_path_not_allowed`. That must render as
    // the missing dependency, never as Core's not-found state: the code decides, not the
    // status, and a 404 that means "not forwarded" read as "does not exist" would be wrong.
    expect(within(panel).queryByTestId('not-found')).toBeNull()
    // The panel must name the real contract source and the unpassed acceptance, not just
    // "not available".
    expect(panel.textContent).toContain('memory:read')
    expect(panel.textContent).toContain(contract.source.ref)
    expect(panel.textContent).toContain('P4 阶段验收未通过')
  })

  it('states the incomplete phase boundary on the view itself', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/jobs', { ...SIGNED_IN, 'GET /v1/memory/jobs': PROXY_REFUSAL })
    const panel = await screen.findByRole('region', { name: /Memory Jobs/ })
    expect(within(panel).getByTestId('slice-status').textContent).toContain('Memory 功能读取 / 管理')
  })

  it.each(VIEWS)('%s states the status even when the read succeeds', async (
    _name, hash, region, route,
  ) => {
    // The status is a property of the milestone, not of the response. Rendering it only when
    // the dependency is pending passes every other test in this file — which is exactly the
    // gap P6-03a also had, so it is pinned here too: a populated read must still say it.
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
      [`/v1/memory/traces/${TRACE}`]: { trace: traceDetail() },
    }
    const user = userEvent.setup()
    await enter(user, hash, { ...SIGNED_IN, ['GET ' + route]: { body: bodies[route] } })
    const panel = await screen.findByRole('region', { name: region })
    const status = await within(panel).findByTestId('slice-status')
    expect(status.textContent).toContain('Memory 功能读取 / 管理')
    // And it must name the pin and the unpassed acceptance, not just the status word.
    expect(status.textContent).toContain('验收未通过')
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
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

  it('shows an unauthorised project as not_found, not as an error and not as pending', async () => {
    // P4 answers `not_found` for a project the actor may not use, deliberately
    // indistinguishable from "does not exist" so the Inspector cannot enumerate another
    // project's work. So it must NOT be an error banner with a retry — that implies "try
    // again and you might get it" and recreates the hint P4 removed — and not the
    // dependency panel either, because Core answered.
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': coreError(404, 'not_found', 'project not found'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    const notFound = await within(panel).findByTestId('not-found')
    expect(notFound.textContent).toContain('无权访问')
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
    // No error-styled retry: the *standing* re-read control every Memory view carries is
    // not the same thing as a failure banner inviting a retry, which is what was wrong.
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(panel.textContent).not.toContain('失败')
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
    // A refusal is not an absence: 403 must never be dressed as not-found either.
    expect(within(panel).queryByTestId('not-found')).toBeNull()
  })

  it('renders an unknown id as the same not-found state, because P4 does not distinguish', async () => {
    // The UI must not invent a distinction Core deliberately withholds — but it must still
    // differ from the refused and pending states.
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${UNKNOWN_ITEM}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/items/' + UNKNOWN_ITEM]: coreError(404, 'not_found', 'memory not found'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(within(panel).getByTestId('not-found')).toBeTruthy())
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('still shows a genuine failure as an error banner with a retry', async () => {
    // The control for the two tests above: a 500 is a real failure and must keep the
    // banner, or "not an error banner" would have been achieved by removing error handling.
    const user = userEvent.setup()
    await enter(user, '#/memory/items', {
      ...SIGNED_IN,
      'GET /v1/memory/items': coreError(500, 'internal_error', 'boom'),
    })
    const panel = await screen.findByRole('region', { name: /Memory Items/ })
    await waitFor(() => expect(within(panel).getByRole('alert')).toBeTruthy())
    expect(within(panel).queryByTestId('not-found')).toBeNull()
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

  it('lists traces with their mode, status and token spend', async () => {
    // The traces *list* is a separate read from the detail; without this the list could
    // render nothing and every other test would still pass.
    const user = userEvent.setup()
    await enter(user, '#/memory/traces', {
      ...SIGNED_IN,
      'GET /v1/memory/traces': {
        body: { traces: [trace(), trace({ trace_id: `mtr_${'01M4B6VGC29ESRDRQR62DX8AK9'}`, mode: 'reflect' })], count: 2 },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Traces/ })
    const table = await within(panel).findByTestId('memory-traces')
    const rows = table.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(table.textContent).toContain(TRACE)
    expect(table.textContent).toContain('recall')
    expect(table.textContent).toContain('reflect')
    expect(table.textContent).toContain('128')
  })

  it('shows a trace query hash and length, and never invents the query text', async () => {
    // P4 stores the hash and the length, not the query. Rendering a "query" field would be
    // inventing one.
    const user = userEvent.setup()
    await enter(user, `#/memory/traces/${TRACE}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/traces/' + TRACE]: { body: { trace: traceDetail() } },
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

describe('the view renders the engine text exactly as Core sent it', () => {
  // These run on the job *detail*, which is where the field is rendered. The first version
  // of this pair ran on the list, whose table never shows `safe_error_summary` at all, so
  // changing the mocked value to non-null still passed — a vacuous test.
  it('renders nothing where Core withheld the text', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/jobs/${JOB}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/jobs/' + JOB]: {
        body: {
          job: job({ status: 'FAILED', safe_error_summary: null, last_error_code: 'engine_unavailable' }),
          attempts: [],
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Job/ })
    await waitFor(() => expect(panel.textContent).toContain('engine_unavailable'))
    // `RawText` renders its label only when it is mounted, so the label's absence is the
    // signal that no engine text was invented to fill the withheld value.
    expect(within(panel).queryByText('safe_error_summary')).toBeNull()
    expect(panel.textContent).not.toContain('simulated text')
  })

  it('renders an item engine summary when Core sent one', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/items/' + ITEM]: {
        body: {
          memory: item({ safe_error_summary: 'the engine rejected this item',
                         last_error_code: 'engine_rejected' }),
          sources: [], jobs: [], recallable: true, lifecycle: lifecycle(),
          engine_claim: 'x', non_authoritative: true,
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(panel.textContent).toContain('the engine rejected this item'))
    expect(within(panel).getByText('safe_error_summary')).toBeTruthy()
  })

  it('renders nothing where an item engine summary was withheld', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      ...SIGNED_IN,
      ['GET /v1/memory/items/' + ITEM]: {
        body: {
          memory: item({ safe_error_summary: null, last_error_code: 'engine_rejected' }),
          sources: [], jobs: [], recallable: true, lifecycle: lifecycle(),
          engine_claim: 'x', non_authoritative: true,
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(panel.textContent).toContain('engine_rejected'))
    expect(within(panel).queryByText('safe_error_summary')).toBeNull()
  })

  it('renders the sentinel Core actually sent', async () => {
    // The control: if this passed with the text withheld, the test above would prove nothing.
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
    expect(within(panel).getByText('safe_error_summary')).toBeTruthy()
  })
})

describe('the test fixtures match the contract they exercise', () => {
  it('builds a trace list row of 16 keys and a detail of 20', async () => {
    // Without this the view fixtures can drift from the contract while every other test still
    // passes — a list row carrying detail-only fields describes a shape P4 never sends.
    expect(Object.keys(trace()).sort()).toEqual([...contract.nested_fields.trace].sort())
    expect(Object.keys(traceDetail()).sort()).toEqual([...contract.nested_fields.trace_detail].sort())
    expect(Object.keys(traceDetail()).length - Object.keys(trace()).length).toBe(4)
  })

  it('builds job, item and lifecycle fixtures of the contract size', () => {
    expect(Object.keys(job()).sort()).toEqual([...contract.nested_fields.job].sort())
    expect(Object.keys(item()).sort()).toEqual([...contract.nested_fields.item].sort())
    expect(Object.keys(lifecycle()).sort()).toEqual([...contract.nested_fields.lifecycle].sort())
  })
})

describe('the read views stay read-only, and the write controls are gated', () => {
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

  /** The views that read and never write. The two details are covered below. */
  const READ_ONLY_VIEWS = VIEWS.filter(([name]) => name !== 'job detail' && name !== 'item detail')

  const BODIES = (): Record<string, unknown> => ({
    '/v1/memory/health': health(),
    '/v1/memory/jobs': { jobs: [job()], count: 1, counts: withKeys(contract.nested_fields.job_counts, {}) },
    [`/v1/memory/jobs/${JOB}`]: { job: job(), attempts: [] },
    '/v1/memory/items': { items: [{ ...item(), lifecycle: lifecycle() }], count: 1, non_authoritative: true },
    [`/v1/memory/items/${ITEM}`]: {
      memory: item(), sources: [], jobs: [], recallable: true, lifecycle: lifecycle(),
      engine_claim: 'x', non_authoritative: true,
    },
    '/v1/memory/traces': { traces: [trace()], count: 1 },
    [`/v1/memory/traces/${TRACE}`]: { trace: traceDetail() },
  })

  it.each(READ_ONLY_VIEWS)('%s offers only read controls, never a write action', async (
    _name, hash, region, route,
  ) => {
    const user = userEvent.setup()
    await enter(user, hash, { ...SIGNED_IN, ['GET ' + route]: { body: BODIES()[route] } })
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

  /**
   * P6-03b2 changed this file's old claim. The item and job details now carry the write
   * controls, so the invariant is no longer "no write affordance anywhere" — it is:
   *
   * * the write surface appears **only** on the two detail views, where the revision and
   *   status it must send are in hand;
   * * rendering it issues no write: it takes an explicit submit, which is what makes
   *   reflect an "explicit user request" rather than a side effect;
   * * it states which switch and which scope it needs, so a refusal cannot be read as a
   *   successful integration.
   */
  it.each([['job detail', `#/memory/jobs/${JOB}`, /Memory Job/, `/v1/memory/jobs/${JOB}`],
           ['item detail', `#/memory/items/${ITEM}`, /Memory Item/, `/v1/memory/items/${ITEM}`]] as const)(
    '%s carries the gated write panel and still sends no write on render', async (
      _name, hash, region, route,
    ) => {
      const user = userEvent.setup()
      await enter(user, hash, { ...SIGNED_IN, ['GET ' + route]: { body: BODIES()[route] } })
      const panel = await screen.findByRole('region', { name: region })
      await waitFor(() => expect(panel.textContent).not.toContain('读取失败'))

      // The write section is present, and it names the dependency rather than pretending.
      expect(panel.textContent).toContain('P6_ENABLE_P4_WRITES')
      expect(panel.textContent).toContain('P4 阶段验收未通过')
      expect(panel.textContent).toMatch(/memory:(manage|reflect)/)

      // Nothing was written by rendering the page.
      expect(fake!.requests.every((request) => request.method === 'GET')).toBe(true)
      expect(fake!.requests.some((request) => request.path === '/v1/memory/reflect')).toBe(false)
    })
})
