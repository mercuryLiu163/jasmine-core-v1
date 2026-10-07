/**
 * P6-02 read-slice component tests.
 *
 * These use the real views against a fake Core, so they pin the behaviour the
 * handoff calls out: no implied totals for `after_id` collections, an empty
 * result distinguished from a refusal, a preview conflict shown as a conflict
 * (never as an empty plan), and the Conversation filter carrying every event type
 * the Core enum defines.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 8 }

function interpretation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    interpretation_id: 'int_1',
    event_id: 'evt_1',
    project_id: 'prj_1',
    task_id: 'tsk_1',
    session_id: null,
    host_id: 'hst_1',
    source_actor_id: 'act_1',
    source_actor_kind: 'human',
    source_type: 'USER_EXPLICIT',
    processor_actor_id: 'act_1',
    extractor_id: 'codex-local-v1',
    extractor_model: 'gpt-6.1-sol',
    extractor_version: 'test.v1',
    provider: 'component_simulation',
    prompt_version: 'jasmine.interpreter.v2',
    output_schema_version: 'jasmine.interpretation.v1',
    schema_digest: 'a'.repeat(64),
    config_digest: 'b'.repeat(64),
    input_hash: 'c'.repeat(64),
    parent_interpretation_id: null,
    created_at: '2026-10-07T12:00:00Z',
    deadline_at: '2026-10-07T12:02:00Z',
    config: {},
    status: 'EXTRACTED',
    raw_result_json: '{"ok":true}',
    candidates: [
      {
        kind: 'REQUIRED_CAPABILITY',
        content: 'playwright',
        impact: 'LOW',
        certainty: 'EXPLICIT',
        confidence: 0.95,
        rationale: '用户明确要求能力',
        scope: { kind: 'TASK', project_id: 'prj_1', task_id: 'tsk_1', path: null, tool: null },
        source_span: { start: 0, end: 3, quote: '你好' },
      },
    ],
    confidence: 0.95,
    error_code: null,
    completed_at: '2026-10-07T12:00:01Z',
    ...overrides,
  }
}

function review(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    review_id: 'rvw_1',
    resolution_id: 'res_1',
    status: 'PENDING',
    revision: 1,
    created_at: '2026-10-07T12:00:00Z',
    updated_at: '2026-10-07T12:00:00Z',
    history: [],
    resolution: {
      resolution_id: 'res_1',
      interpretation_id: 'int_1',
      source_event_id: 'evt_1',
      actor_id: 'act_1',
      command_event_id: 'evt_cmd',
      policy_version: 'jasmine.resolution.v1',
      context_digest: 'd'.repeat(64),
      expected_context: {},
      plan: { policy_version: 'jasmine.resolution.v1', disposition: 'PENDING_REVIEW', items: [] },
      request_hash: 'e'.repeat(64),
      created_at: '2026-10-07T12:00:00Z',
      result: {
        resolution_id: 'res_1',
        interpretation_id: 'int_1',
        source_event_id: 'evt_1',
        command_event_id: 'evt_cmd',
        policy_version: 'jasmine.resolution.v1',
        disposition: 'PENDING_REVIEW',
        reasons: ['scope_or_impact_requires_review'],
        actions: [],
        source_bindings: [],
        canonical_resolution_id: null,
        latest_application_id: null,
        execution_actor_id: null,
        review_id: 'rvw_1',
        review_actor_id: null,
        review_command_event_id: null,
      },
      manual_application_history: [],
    },
    ...overrides,
  }
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

describe('Interpretations list', () => {
  const page = {
    items: [interpretation()],
    next_after_id: 'int_1',
  }

  it('declares only the loaded count and follows the after_id cursor', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretations', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/interpretations': { body: page },
    })

    const panel = await screen.findByRole('region', { name: /Interpretations\(已载入 1 条\)/ })
    expect(panel.textContent).toContain('Core 不返回总数')
    expect(panel.textContent).toContain('int_1')

    // The next page is requested with the cursor Core returned.
    fake = fake // keep reference
    const before = fake!.callsTo('GET', '/v1/interpretations').length
    await user.click(within(panel).getByRole('button', { name: /继续加载/ }))
    await waitFor(() =>
      expect(fake!.callsTo('GET', '/v1/interpretations').length).toBeGreaterThan(before),
    )
    const last = fake!.callsTo('GET', '/v1/interpretations').at(-1)
    expect(last?.query.get('after_id')).toBe('int_1')
  })

  it('shows an empty range as empty, not as an error', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretations', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/interpretations': { body: { items: [], next_after_id: null } },
    })
    expect((await screen.findByText(/空:Interpretation/)).textContent).toContain('该范围没有解释记录')
  })

  it('shows a refused read as a refusal, not as an empty list', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretations', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/interpretations': coreError(403, 'forbidden_scope', 'interpretations:read is required'),
    })
    const panel = await screen.findByRole('region', { name: /Interpretations\(已载入 0 条\)/ })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('forbidden_scope'))
    expect(within(panel).queryByText(/空:Interpretation/)).toBeNull()
  })
})

describe('Interpretation detail and resolution preview', () => {
  const preview = {
    interpretation_id: 'int_1',
    source_event_id: 'evt_1',
    policy_version: 'jasmine.resolution.v1',
    plan: {
      policy_version: 'jasmine.resolution.v1',
      disposition: 'PENDING_REVIEW',
      items: [
        {
          candidate_index: 0,
          candidate: {
            kind: 'RULE',
            content: 'Always run the device smoke test.',
            impact: 'MEDIUM',
            certainty: 'EXPLICIT',
            confidence: 0.9,
            rationale: 'fixture',
            scope: { kind: 'PROJECT', project_id: 'prj_1', task_id: null, path: null, tool: null },
            source_span: { start: 0, end: 3, quote: '你好' },
          },
          action: 'CREATE_RULE',
          pending: true,
          reason: 'scope_or_impact_requires_review',
        },
      ],
    },
    expected_revisions: {},
    expected_context_digest: 'f'.repeat(64),
    maintenance_head: {
      root_interpretation_id: 'int_1',
      current_interpretation_id: 'int_1',
      revision: 1,
      status: 'NORMAL',
      pending_operation_event_id: null,
      pending_child_interpretation_id: null,
    },
    source_application: null,
  }

  it('renders the record, its candidates and the server plan', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretation() } },
      'GET /v1/resolutions/preview': { body: preview },
    })
    expect(await screen.findByText('Interpretation(逐字记录)')).toBeTruthy()
    expect((await screen.findByText(/Candidates\(1\)/)).textContent).toContain('Candidates(1)')
    const plan = await screen.findByRole('region', { name: /Resolution 预览/ })
    expect(plan.textContent).toContain('CREATE_RULE')
    expect(plan.textContent).toContain('需要人工审批')
    // The read-side side effect is stated, not hidden.
    expect(plan.textContent).toContain('不是已应用的决议')
  })

  it('renders a preview conflict as a conflict, never as an empty plan', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretation() } },
      'GET /v1/resolutions/preview': coreError(409, 'interpretation_not_current', 'stale'),
    })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('interpretation_not_current')
    expect(alert.textContent).toContain('不是空计划')
    expect(screen.queryByText(/空:计划项/)).toBeNull()
  })
})

describe('Pending reviews', () => {
  it('lists pending reviews and surfaces a preview conflict on the row', async () => {
    const user = userEvent.setup()
    await enter(user, '#/reviews', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/reviews/pending': {
        body: {
          items: [
            review({ preview: undefined, preview_error: { code: 'interpretation_not_current', details: {} } }),
          ],
          next_after_id: null,
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /待审批 Review\(已载入 1 条\)/ })
    expect(panel.textContent).toContain('rvw_1')
    expect(panel.textContent).toContain('PENDING')
    expect(panel.textContent).toContain('预览冲突')
    expect(panel.textContent).toContain('interpretation_not_current')
    expect(panel.textContent).toContain('不会把「没有更多待审批」写成「全部已处理」')
  })

  it('shows an empty queue as empty with the right caveat', async () => {
    const user = userEvent.setup()
    await enter(user, '#/reviews', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/reviews/pending': { body: { items: [], next_after_id: null } },
    })
    expect((await screen.findByText(/空:待审批 Review/)).textContent).toContain('不等于没有 Review 历史')
  })
})

describe('Conversation event filter', () => {
  it('offers every event type the Core enum defines, including the P2 additions', async () => {
    const user = userEvent.setup()
    await enter(user, '#/conversation', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/sessions': { body: { sessions: [], count: 0 } },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/events': { body: { events: [], count: 0 } },
    })
    const select = await screen.findByLabelText('event_type')
    const options = within(select as HTMLElement).getAllByRole('option').map((o) => o.getAttribute('value'))
    // 30 Core event types plus the "(全部)" placeholder.
    expect(options).toHaveLength(31)
    for (const added of [
      'interpretation.bound',
      'interpretation.corrected',
      'interpretation.rejected',
      'interpretation.rerun',
      'resolution.command',
      'review.approved',
      'review.rejected',
    ]) {
      expect(options).toContain(added)
    }
  })

  it('links the current scope to the delivered interpretation reads', async () => {
    const user = userEvent.setup()
    await enter(user, '#/conversation?project_id=prj_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/sessions': { body: { sessions: [], count: 0 } },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/events': { body: { events: [], count: 0 } },
    })
    const panel = await screen.findByRole('region', { name: /Interpretation \/ Resolver/ })
    expect(panel.textContent).toContain('读取视图已交付')
    expect(within(panel).getByRole('link', { name: /当前范围的全部解释/ })).toBeTruthy()
    expect(panel.textContent).toContain('里程碑 B')
  })
})
