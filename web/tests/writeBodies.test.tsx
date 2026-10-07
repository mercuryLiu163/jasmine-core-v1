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
const PROJECT_ID = 'prj_1'
const TASK_ID = 'tsk_1'

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
    expect(within(panel).getByTestId('no-reason-field').textContent).toContain('不接受 reason 字段')
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

function interpretationDetail(): Record<string, unknown> {
  return {
    ...interpretationRow(),
    candidates: [],
    raw_result_json: '{"ok":true}',
    confidence: 0.9,
    error_code: null,
    config: {},
    maintenance_history: [],
    operation_completions: [],
  }
}
