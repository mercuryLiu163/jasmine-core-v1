/**
 * Write bodies, asserted against Core's real field contracts (P6-02 milestone B).
 *
 * The dashboard proxy only decides *whether* a `METHOD path` may pass; what is
 * inside the body is Core's business, and Core validates it strictly:
 *
 * - `InterpretationMaintenance` allows exactly
 *   `{idempotency_key, host_id, reason, expected_revision}` (`+ result` for
 *   `correct`) and raises `invalid_request: unknown fields` for anything else.
 * - `AuthorityStore.propose` allows exactly
 *   `{rule_key, kind, severity, enforcement, content, matcher, scope,
 *   origin_event_id, host_id, event_id, verification_requirements}` and
 *   *rejects* `expected_revision` outright.
 * - `AuthorityStore.transition` allows exactly `{expected_revision, host_id,
 *   event_id}` for approve/retire, plus the content keys for supersede.
 *
 * So the tests below assert the exact key set of every write the UI can send.
 * A well-meaning "just add reason/idempotency_key everywhere" refactor would
 * break Authority writes, and these are the tests that fail when it does.
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
const HOSTS = { hosts: [{ host_id: 'hst_1', display_name: 'local', name: 'local' }] }
// Core-shaped ids: the panels validate scope ids against Core's own id shape, so a
// `prj_1` fixture would be refused locally and never reach the assertion.
const PROJECT_ID = 'prj_01M4B6VGC29ESRDRQR62DX8AK8'
const TASK_ID = 'tsk_01M4B6VGC29ESRDRQR62DX8AK8'

function rule(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    rule_id: 'rul_1',
    rule_key: 'ui.smoke.test',
    version: 2,
    revision: 5,
    status: 'ACTIVE',
    scope: { kind: 'task', project_id: PROJECT_ID, task_id: TASK_ID },
    kind: 'RULE',
    severity: 'NORMAL',
    enforcement: 'CONTEXT',
    content: 'Always run the device smoke test.',
    matcher: { tool: 'shell' },
    verification_requirements: null,
    origin_event_id: 'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    origin_actor_kind: 'human',
    origin_event_type: 'task.created',
    change_event_id: 'evt_01M4B63ERBRK844P596VWM2ZYD',
    status_event_id: 'evt_01M4B63ERD980YKMMC12A4Y4Q7',
    status_actor_kind: 'human',
    ...overrides,
  }
}

const EVENTS = {
  events: [
    {
      event_id: 'evt_01M4B63ER9AR3M3V55FHRWZ66X',
      seq: 1,
      schema_version: 8,
      event_type: 'task.created',
      source_system: 'p6-fixture',
      source_event_id: null,
      occurred_at: '2026-10-07T12:00:00Z',
      recorded_at: '2026-10-07T12:00:00Z',
      actor_id: 'act_1',
      actor_kind: 'human',
      host_id: 'hst_1',
      session_id: null,
      project_id: PROJECT_ID,
      task_id: TASK_ID,
      payload: {},
      body_sha256: 'a'.repeat(64),
    },
  ],
  count: 1,
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

const RULES_HASH = `#/rules?project_id=${PROJECT_ID}&task_id=${TASK_ID}&rule_id=rul_1`

const rulesRoutes = {
  'GET /v1/projects': { body: { projects: [], count: 0 } },
  'GET /v1/hosts': { body: HOSTS },
  'GET /v1/rules/active': { body: { rules: [] } },
  'GET /v1/rules/rul_1': { body: { rule: rule() } },
  'GET /v1/rules/rul_1/history': { body: { history: [] } },
  'GET /v1/events': { body: EVENTS },
}

describe('Authority write bodies', () => {
  it('proposes a rule with Core\'s exact creation fields and no reason/expected_revision', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'POST /v1/rules/proposals': { status: 201, body: { rule: rule({ status: 'PROPOSED' }), replayed: false } },
    })

    const panel = await screen.findByRole('region', { name: 'Propose(提出新的 Rule)' })
    await user.type(within(panel).getByLabelText(/rule_key/), 'ui.new.rule')
    await user.selectOptions(
      within(panel).getByLabelText(/候选 origin Event/),
      'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    )
    await user.type(within(panel).getByLabelText(/^content\(Core 必填/), 'Prefer the smoke test.')
    await user.click(within(panel).getByRole('button', { name: '提交提案' }))

    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/proposals')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/rules/proposals')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      [
        'content',
        'enforcement',
        'event_id',
        'host_id',
        'kind',
        'matcher',
        'origin_event_id',
        'rule_key',
        'scope',
        'severity',
      ].sort(),
    )
    expect(sent['reason']).toBeUndefined()
    expect(sent['idempotency_key']).toBeUndefined()
    expect(sent['expected_revision']).toBeUndefined()
    expect(String(sent['event_id'])).toMatch(/^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$/)
    expect(sent['scope']).toEqual({ kind: 'task', project_id: PROJECT_ID, task_id: TASK_ID })
  })

  it('never offers a reason input on an Authority panel, because Core would refuse it', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, rulesRoutes)

    const panel = await screen.findByRole('region', { name: 'Propose(提出新的 Rule)' })
    expect(within(panel).queryByLabelText(/reason/)).toBeNull()
    const note = within(panel).getByTestId('no-reason-field').textContent ?? ''
    expect(note).toContain('不接受 reason 字段')
    // The note is shared by every `reasonMode="none"` panel, including the Task/Step ones whose
    // command Events differ and whose create routes REFUSE expected_revision. It must therefore
    // not name P2's `rule.proposed`/`rule.*` events as the audit trail (review M1).
    expect(note).not.toContain('rule.proposed')
    expect(note).not.toContain('expected_revision')
    // The reason input is genuinely elsewhere on a write that does take one.
    expect(within(panel).getByLabelText(/host_id/)).toBeTruthy()
  })

  it('keeps the reason input on interpretation writes, which do require it', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/interpretations': { body: { items: [interpretationRow()], next_after_id: null } },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretationDetail() } },
    })

    const panel = await screen.findByRole('region', { name: 'Reject(判定该解释不可用)' })
    // Submit stays disabled until both the required reason and a host are present.
    const submit = within(panel).getByRole('button', { name: '提交拒绝' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    await user.type(within(panel).getByLabelText(/reason/), '来源不可用')
    await waitFor(() => expect(submit.disabled).toBe(false))

    await user.click(submit)
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/interpretations/int_1/reject')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/interpretations/int_1/reject')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      ['expected_revision', 'host_id', 'idempotency_key', 'reason'].sort(),
    )
    expect(sent['reason']).toBe('来源不可用')
    expect(sent['event_id']).toBeUndefined()
  })

  it('approves a PROPOSED rule with the transition field set and the rule\'s own revision', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'GET /v1/rules/rul_1': { body: { rule: rule({ status: 'PROPOSED', revision: 3 }) } },
      'POST /v1/rules/rul_1/approve': {
        status: 200,
        body: { rule: rule({ status: 'ACTIVE', revision: 4 }), replayed: false },
      },
    })

    const panel = await screen.findByRole('region', { name: 'Approve(让 PROPOSED Rule 生效)' })
    await user.click(within(panel).getByRole('button', { name: '提交批准' }))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_1/approve')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/rules/rul_1/approve')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(['event_id', 'expected_revision', 'host_id'].sort())
    // The revision is the one Core returned for this Rule, not a guess.
    expect(sent['expected_revision']).toBe(3)
    expect(sent['reason']).toBeUndefined()
    expect(sent['idempotency_key']).toBeUndefined()
  })

  it('refuses to approve a rule that is not PROPOSED rather than letting Core reject it', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, rulesRoutes) // fixture rule is ACTIVE

    const panel = await screen.findByRole('region', { name: 'Approve(让 PROPOSED Rule 生效)' })
    const submit = within(panel).getByRole('button', { name: '提交批准' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    expect(panel.textContent).toContain('PROPOSED')
    expect(fake!.callsTo('POST', '/v1/rules/rul_1/approve')).toHaveLength(0)
  })

  it('requires an origin Event before supersede, and sends the Rule\'s own locked scope', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'POST /v1/rules/rul_1/supersede': {
        status: 200,
        body: { rule: rule({ version: 3, revision: 6 }), replayed: false },
      },
    })

    const panel = await screen.findByRole('region', { name: 'Supersede(用新版本替换 ACTIVE Rule)' })
    const submit = within(panel).getByRole('button', { name: '提交替换' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    expect(panel.textContent).toContain('不支持跨 scope 替换')

    // A malformed id is refused locally, so Core never sees a bad event_id.
    const originInput = within(panel).getByLabelText(/origin_event_id\(supersede 必填/)
    await user.type(originInput, 'not-an-id')
    expect(submit.disabled).toBe(true)

    await user.clear(originInput)
    await user.type(originInput, 'evt_01M4B63ER9AR3M3V55FHRWZ66X')
    await waitFor(() => expect(submit.disabled).toBe(false))
    await user.click(submit)

    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_1/supersede')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/rules/rul_1/supersede')[0]?.body ?? {}
    expect(sent['scope']).toEqual({ kind: 'task', project_id: PROJECT_ID, task_id: TASK_ID })
    expect(sent['expected_revision']).toBe(5)
    expect(sent['origin_event_id']).toBe('evt_01M4B63ER9AR3M3V55FHRWZ66X')
    expect(sent['reason']).toBeUndefined()
    expect(sent['idempotency_key']).toBeUndefined()
  })

  it('shows a conflict as a conflict, keeps the draft, and does not auto-retry', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'POST /v1/rules/rul_1/retire': coreError(409, 'revision_conflict', 'rule revision is stale'),
    })

    const panel = await screen.findByRole('region', { name: 'Retire(停用 ACTIVE Rule)' })
    await user.click(within(panel).getByRole('button', { name: '提交停用' }))

    await waitFor(() =>
      expect(within(panel).getByRole('alert').textContent).toContain('revision_conflict'),
    )
    expect(within(panel).getByRole('alert').textContent).toContain('已保留草稿')
    // Exactly one attempt: a conflict must not be replayed automatically.
    expect(fake!.callsTo('POST', '/v1/rules/rul_1/retire')).toHaveLength(1)
    // The key shown is stable, so a manual retry replays rather than duplicates.
    expect(panel.textContent).toContain('event_id')
  })

  it('names the missing scope on a refusal and offers no alternative credential', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'POST /v1/rules/rul_1/retire': coreError(403, 'forbidden_scope', 'authority:manage is required'),
    })

    const panel = await screen.findByRole('region', { name: 'Retire(停用 ACTIVE Rule)' })
    await user.click(within(panel).getByRole('button', { name: '提交停用' }))
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('forbidden_scope'))
    expect(panel.textContent).toContain('不会换更高权限的凭据重试')
  })

  it('does not open transition panels until a Rule has been read by ID', async () => {
    const user = userEvent.setup()
    await enter(user, `#/rules?project_id=${PROJECT_ID}&task_id=${TASK_ID}`, {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/rules/active': { body: { rules: [] } },
      'GET /v1/events': { body: EVENTS },
    })

    await screen.findByTestId('rule-transition-unavailable')
    expect(screen.queryByRole('region', { name: /Approve/ })).toBeNull()
    expect(screen.queryByRole('region', { name: /Retire/ })).toBeNull()
    // Propose is still offered: creation takes no revision and needs no open Rule.
    expect(screen.getByRole('region', { name: 'Propose(提出新的 Rule)' })).toBeTruthy()
  })
})

function interpretationRow(): Record<string, unknown> {
  return {
    interpretation_id: 'int_1',
    event_id: 'evt_1',
    project_id: PROJECT_ID,
    task_id: TASK_ID,
    session_id: null,
    host_id: 'hst_1',
    source_actor_kind: 'human',
    source_type: 'USER_EXPLICIT',
    extractor_id: 'codex-local-v1',
    provider: 'component_simulation',
    status: 'EXTRACTED',
    disposition: 'PENDING_REVIEW',
    created_at: '2026-10-07T12:00:00Z',
    completed_at: '2026-10-07T12:00:01Z',
    maintenance_head: {
      root_interpretation_id: 'int_1',
      current_interpretation_id: 'int_1',
      pending_child_interpretation_id: null,
      pending_operation_event_id: null,
      revision: 4,
      status: 'NORMAL',
      updated_at: '2026-10-07T12:00:02Z',
    },
  }
}

function interpretationDetail(interpretationId = 'int_1'): Record<string, unknown> {
  return {
    ...interpretationRow(),
    interpretation_id: interpretationId,
    candidates: [],
    raw_result_json: '{"ok":true}',
    confidence: 0.9,
    error_code: null,
    config: {},
    maintenance_history: [],
    operation_completions: [],
  }
}

/**
 * The remaining five writes, so "every write the UI can send" is literally true.
 *
 * The gaps were real: `review/{id}/approve` is the only body carrying
 * `expected_revisions`, `expected_context_digest` and `actions`, and it had no body
 * test at all, while `correct`, `rerun`, `review.reject` and `rule.retire` were only
 * asserted to have *been called*.
 */
describe('every remaining write body is pinned too', () => {
  function reviewFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      review_id: 'rvw_1',
      resolution_id: 'res_1',
      status: 'PENDING',
      revision: 2,
      history: [],
      preview: {
        interpretation_id: 'int_1',
        source_event_id: 'evt_01M4B63ER9AR3M3V55FHRWZ66X',
        policy_version: 'jasmine.resolution.v1',
        plan: {
          policy_version: 'jasmine.resolution.v1',
          disposition: 'PENDING_REVIEW',
          items: [
            {
              candidate_index: 0,
              action: 'NO_ACTION',
              pending: false,
              reason: 'scope_or_impact_requires_review',
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
            },
          ],
        },
        expected_revisions: [
          { object_type: 'project', object_id: 'prj_1', revision: 1 },
        ],
        expected_context_digest: 'f'.repeat(64),
      },
      resolution: {
        resolution_id: 'res_1',
        interpretation_id: 'int_1',
        source_event_id: 'evt_01M4B63ER9AR3M3V55FHRWZ66X',
        result: { disposition: 'PENDING_REVIEW', reasons: ['scope_or_impact_requires_review'], actions: [] },
        plan: { policy_version: 'jasmine.resolution.v1', disposition: 'PENDING_REVIEW', items: [] },
      },
      ...overrides,
    }
  }

  it('sends Core\'s exact field set for interpretation correct', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretationDetail() } },
      'POST /v1/interpretations/int_1/correct': { status: 201, body: { replayed: false } },
    })
    const panel = await screen.findByRole('region', { name: 'Correct(提出新的解释内容)' })
    await user.type(within(panel).getByTestId('write-reason'), 'r')
    await waitFor(() => expect(buttonFor(panel, '提交修正').disabled).toBe(false))
    await user.click(buttonFor(panel, '提交修正'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/interpretations/int_1/correct')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/interpretations/int_1/correct')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      ['expected_revision', 'host_id', 'idempotency_key', 'reason', 'result'].sort(),
    )
    expect(sent['result']).toBeTruthy()
    expect(sent['event_id']).toBeUndefined()
  })

  it('sends Core\'s exact field set for interpretation rerun', async () => {
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretationDetail() } },
      'POST /v1/interpretations/int_1/rerun': { status: 201, body: { replayed: false } },
    })
    const panel = await screen.findByRole('region', { name: 'Re-run(用当前配置重新解释同一来源)' })
    await user.type(within(panel).getByTestId('write-reason'), 'r')
    await waitFor(() => expect(buttonFor(panel, '提交重跑').disabled).toBe(false))
    await user.click(buttonFor(panel, '提交重跑'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/interpretations/int_1/rerun')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/interpretations/int_1/rerun')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      ['expected_revision', 'host_id', 'idempotency_key', 'reason'].sort(),
    )
  })

  it('sends the server plan back verbatim on review approve', async () => {
    const user = userEvent.setup()
    await enter(user, '#/review/rvw_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/reviews/rvw_1': { body: { review: reviewFixture() } },
      'POST /v1/reviews/rvw_1/approve': { status: 201, body: { replayed: false, result: { disposition: 'APPLIED' } } },
    })
    const panel = await screen.findByRole('region', { name: 'Approve(按服务器计划批准)' })
    await user.type(within(panel).getByTestId('write-reason'), 'r')
    await waitFor(() => expect(buttonFor(panel, '提交批准').disabled).toBe(false))
    await user.click(buttonFor(panel, '提交批准'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/approve')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/reviews/rvw_1/approve')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      [
        'actions',
        'expected_context_digest',
        'expected_revision',
        'expected_revisions',
        'host_id',
        'idempotency_key',
        'reason',
      ].sort(),
    )
    // `expected_revisions` is a LIST of {object_type, object_id, revision}; an object
    // would be refused by Core's `_validate_revisions`.
    expect(Array.isArray(sent['expected_revisions'])).toBe(true)
    expect(sent['expected_revisions']).toEqual([
      { object_type: 'project', object_id: 'prj_1', revision: 1 },
    ])
    expect(sent['expected_context_digest']).toBe('f'.repeat(64))
    expect(sent['actions']).toEqual([{ candidate_index: 0, action: 'NO_ACTION', payload: {} }])
  })

  it('sends Core\'s exact field set for review reject', async () => {
    const user = userEvent.setup()
    await enter(user, '#/review/rvw_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/reviews/rvw_1': { body: { review: reviewFixture() } },
      'POST /v1/reviews/rvw_1/reject': { status: 201, body: { replayed: false, result: { disposition: 'REJECTED' } } },
    })
    const panel = await screen.findByRole('region', { name: 'Reject(拒绝该计划)' })
    await user.type(within(panel).getByTestId('write-reason'), 'r')
    await waitFor(() => expect(buttonFor(panel, '提交拒绝').disabled).toBe(false))
    await user.click(buttonFor(panel, '提交拒绝'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')).toHaveLength(1))
    const sent = fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')[0]?.body ?? {}
    expect(Object.keys(sent).sort()).toEqual(
      ['expected_revision', 'host_id', 'idempotency_key', 'reason'].sort(),
    )
  })

  it('sends Core\'s exact field set for rule retire and rule supersede', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      ...rulesRoutes,
      'POST /v1/rules/rul_1/retire': { status: 200, body: { rule: rule({ status: 'RETIRED' }), replayed: false } },
      'POST /v1/rules/rul_1/supersede': { status: 200, body: { rule: rule({ version: 3 }), replayed: false } },
    })

    const retirePanel = await screen.findByRole('region', { name: 'Retire(停用 ACTIVE Rule)' })
    await user.click(buttonFor(retirePanel, '提交停用'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_1/retire')).toHaveLength(1))
    const retired = fake!.callsTo('POST', '/v1/rules/rul_1/retire')[0]?.body ?? {}
    expect(Object.keys(retired).sort()).toEqual(['event_id', 'expected_revision', 'host_id'].sort())

    const supersedePanel = await screen.findByRole('region', { name: 'Supersede(用新版本替换 ACTIVE Rule)' })
    await user.type(
      within(supersedePanel).getByLabelText(/origin_event_id\(supersede 必填/),
      'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    )
    await waitFor(() => expect(buttonFor(supersedePanel, '提交替换').disabled).toBe(false))
    await user.click(buttonFor(supersedePanel, '提交替换'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_1/supersede')).toHaveLength(1))
    const superseded = fake!.callsTo('POST', '/v1/rules/rul_1/supersede')[0]?.body ?? {}
    expect(Object.keys(superseded).sort()).toEqual(
      [
        'content',
        'enforcement',
        'event_id',
        'expected_revision',
        'host_id',
        'kind',
        'matcher',
        'origin_event_id',
        'scope',
        'severity',
      ].sort(),
    )
    expect(superseded['reason']).toBeUndefined()
    expect(superseded['idempotency_key']).toBeUndefined()
  })

  it('does not reuse one replay key for two different targets', async () => {
    // The defect an independent review reproduced: retire Rule A, then retire Rule B,
    // and the second command carried A's event_id, which Core refuses as
    // event_id_conflict. The key must belong to a target, not to a component.
    const user = userEvent.setup()
    await enter(user, RULES_HASH, {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/rules/active': { body: { rules: [] } },
      'GET /v1/events': { body: EVENTS },
      'GET /v1/rules/rul_1': { body: { rule: rule({ rule_id: 'rul_1' }) } },
      'GET /v1/rules/rul_1/history': { body: { history: [] } },
      'GET /v1/rules/rul_2': { body: { rule: rule({ rule_id: 'rul_2' }) } },
      'GET /v1/rules/rul_2/history': { body: { history: [] } },
      'POST /v1/rules/rul_1/retire': { status: 200, body: { rule: rule({ status: 'RETIRED' }), replayed: false } },
      'POST /v1/rules/rul_2/retire': { status: 200, body: { rule: rule({ status: 'RETIRED' }), replayed: false } },
    })

    const first = await screen.findByRole('region', { name: 'Retire(停用 ACTIVE Rule)' })
    await user.click(buttonFor(first, '提交停用'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_1/retire')).toHaveLength(1))

    const go = (hash: string): void => {
      window.location.hash = hash
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    }
    go(`#/rules?project_id=${PROJECT_ID}&task_id=${TASK_ID}&rule_id=rul_2`)
    await waitFor(() => expect(document.body.textContent).toContain('rul_2'))
    const second = await screen.findByRole('region', { name: 'Retire(停用 ACTIVE Rule)' })
    await waitFor(() => expect(buttonFor(second, '提交停用').disabled).toBe(false))
    await user.click(buttonFor(second, '提交停用'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/rules/rul_2/retire')).toHaveLength(1))

    const firstKey = fake!.callsTo('POST', '/v1/rules/rul_1/retire')[0]?.body?.['event_id']
    const secondKey = fake!.callsTo('POST', '/v1/rules/rul_2/retire')[0]?.body?.['event_id']
    expect(typeof firstKey).toBe('string')
    expect(typeof secondKey).toBe('string')
    expect(secondKey).not.toBe(firstKey)
  })

  it('does not carry one interpretation\'s reason into the next', async () => {
    // Same family as the key reuse: the panel instance can survive a target change,
    // and A's reason must not become B's.
    const user = userEvent.setup()
    await enter(user, '#/interpretation/int_1', {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: HOSTS },
      'GET /v1/interpretations/int_1': { body: { interpretation: interpretationDetail('int_1') } },
      'GET /v1/interpretations/int_2': { body: { interpretation: interpretationDetail('int_2') } },
      'POST /v1/interpretations/int_1/reject': { status: 201, body: { replayed: false } },
      'POST /v1/interpretations/int_2/reject': { status: 201, body: { replayed: false } },
    })
    const first = await screen.findByRole('region', { name: 'Reject(判定该解释不可用)' })
    await user.type(within(first).getByTestId('write-reason'), 'A no good')
    await waitFor(() => expect(buttonFor(first, '提交拒绝').disabled).toBe(false))
    await user.click(buttonFor(first, '提交拒绝'))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/interpretations/int_1/reject')).toHaveLength(1))

    window.location.hash = '#/interpretation/int_2'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await waitFor(() => expect(document.body.textContent).toContain('int_2'))
    const second = await screen.findByRole('region', { name: 'Reject(判定该解释不可用)' })
    const reasonInput = within(second).getByTestId('write-reason') as HTMLInputElement
    await waitFor(() => expect(reasonInput.value).toBe(''))
    expect(reasonInput.value).not.toContain('A no good')
  })
})

function buttonFor(panel: HTMLElement, name: string): HTMLButtonElement {
  return within(panel).getByRole('button', { name }) as HTMLButtonElement
}

describe('local validation names the right prefix and the right keys', () => {
  it('refuses a task id offered as a project scope id', async () => {
    // A prefix-agnostic check let a `tsk_` id pass as `project_id` and Core answered
    // 404, while the message claimed a prefix the check did not enforce. The panel
    // reports the first failing field, so the other fields are filled first.
    const user = userEvent.setup()
    await enter(user, `#/rules?rule_id=rul_1&project_id=${PROJECT_ID}&task_id=${TASK_ID}`, {
      ...rulesRoutes,
    })
    const panel = await screen.findByRole('region', { name: 'Propose(提出新的 Rule)' })
    await user.type(within(panel).getByLabelText(/rule_key/), 'ui.badscope')
    await user.selectOptions(
      within(panel).getByLabelText(/候选 origin Event/),
      'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    )
    const projectField = within(panel).getByLabelText(/scope.project_id/)
    await user.clear(projectField)
    await user.type(projectField, TASK_ID)
    await waitFor(() =>
      expect(panel.textContent).toContain('project scope 需要 Core 形状的 project_id'),
    )
    expect(buttonFor(panel, '提交提案').disabled).toBe(true)
    expect(fake!.callsTo('POST', '/v1/rules/proposals')).toHaveLength(0)
  })

  it('refuses unknown keys in verification_requirements instead of sending them', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, { ...rulesRoutes })
    const panel = await screen.findByRole('region', { name: 'Propose(提出新的 Rule)' })
    await user.type(within(panel).getByLabelText(/rule_key/), 'ui.keys')
    await user.selectOptions(
      within(panel).getByLabelText(/候选 origin Event/),
      'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    )
    await user.type(within(panel).getByLabelText(/^content\(Core 必填/), 'content')
    await user.selectOptions(within(panel).getByLabelText('enforcement'), 'VERIFY')
    const requirements = within(panel).getByLabelText(/verification_requirements/) as HTMLTextAreaElement
    // `user.type` treats `{` as an escape sequence, so paste the JSON literally.
    await user.clear(requirements)
    await user.click(requirements)
    await user.paste('{"requirements":[{"key":"k","kind":"BUILD","required_result":"PASS","surprise":1}]}')
    await waitFor(() => expect(panel.textContent).toContain('requirement 不认识的键'))
    expect(buttonFor(panel, '提交提案').disabled).toBe(true)
    expect(fake!.callsTo('POST', '/v1/rules/proposals')).toHaveLength(0)
  })

  it('refuses an unknown top-level key in verification_requirements', async () => {
    const user = userEvent.setup()
    await enter(user, RULES_HASH, { ...rulesRoutes })
    const panel = await screen.findByRole('region', { name: 'Propose(提出新的 Rule)' })
    await user.type(within(panel).getByLabelText(/rule_key/), 'ui.top')
    await user.selectOptions(
      within(panel).getByLabelText(/候选 origin Event/),
      'evt_01M4B63ER9AR3M3V55FHRWZ66X',
    )
    await user.type(within(panel).getByLabelText(/^content\(Core 必填/), 'content')
    await user.selectOptions(within(panel).getByLabelText('enforcement'), 'VERIFY')
    const requirements = within(panel).getByLabelText(/verification_requirements/) as HTMLTextAreaElement
    await user.clear(requirements)
    await user.click(requirements)
    await user.paste('{"requirements":[{"key":"k","kind":"BUILD","required_result":"PASS"}],"extra":1}')
    await waitFor(() => expect(panel.textContent).toContain('Core 只接受 requirements'))
    expect(buttonFor(panel, '提交提案').disabled).toBe(true)
  })
})
