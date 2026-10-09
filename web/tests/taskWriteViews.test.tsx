/**
 * P6-02b Task/Step write panels — behaviour the frozen spec requires, asserted against Core's
 * own strings rather than "something rendered".
 *
 * Each test pins one way the slice could lie:
 *
 * 1. **The exact body per route**, including `expected_revision` being the *right* object's
 *    revision (a Step transition sends the Step's, a Task action the Task's) and Task create
 *    never sending `expected_revision`.
 * 2. **One `event_id` per intent**, reused when the identical body is retried, so a retry is a
 *    replay and not a second write.
 * 3. **A replay is shown as the same write** (`replayed: true`), not as a fresh one.
 * 4. **A timeout/unreachable is unknown**, with the request id that was actually sent, and never
 *    a success or a failure banner.
 * 5. **Every refusal is its own condition**, with Core's code and the fields it named.
 * 6. **A `422 missing_evidence` is BLOCKED**, with Core's reason, and is not a failure.
 * 7. **The value shown after a write is Core's re-read value**, not the write response.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 8 }
const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const TASK_ID = `tsk_${ID}`
const STEP_ID = `stp_${ID}`
const PROJECT_ID = `prj_${ID}`
const EVENT_ID = `evt_${ID}`
const HOST = { host_id: `hst_${ID}`, display_name: 'local', name: 'local' }
const AT = '2026-10-07T12:00:00Z'

function task(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    task_id: TASK_ID,
    project_id: PROJECT_ID,
    title: 'Fixture Task',
    description: '',
    status: 'ACTIVE',
    revision: 12,
    source_event_id: EVENT_ID,
    created_at: AT,
    updated_at: AT,
    acceptance_criteria: { requirements: [] },
    ...overrides,
  }
}

function step(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    step_id: STEP_ID,
    task_id: TASK_ID,
    title: 'Fixture Step',
    description: '',
    status: 'PLANNED',
    revision: 3,
    acceptance_criteria: { requirements: [] },
    source_event_id: EVENT_ID,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  }
}

const WRITE_HASH = `#/task/${TASK_ID}/write`

/** Base routes for the Task write tab; `routes` overrides or adds POSTs. */
function baseRoutes(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    'GET /v1/health': { body: HEALTH },
    // Sign-in verifies the token with a projects read before the shell renders.
    'GET /v1/projects': { body: { projects: [], count: 0 } },
    'GET /v1/hosts': { body: { hosts: [HOST] } },
    [`GET /v1/tasks/${TASK_ID}`]: { body: { task: task() } },
    [`GET /v1/tasks/${TASK_ID}/steps`]: { body: { steps: [step()] } },
    [`GET /v1/tasks/${TASK_ID}/history`]: { body: { events: [], count: 0 } },
    ...overrides,
  }
}

async function enter(
  user: ReturnType<typeof userEvent.setup>,
  hash: string,
  routes: Record<string, unknown>,
  handler?: Parameters<typeof installFakeCore>[1],
): Promise<void> {
  fake = installFakeCore(routes as Parameters<typeof installFakeCore>[0], handler)
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

/** The write panel with this exact title. */
async function panel(name: string | RegExp): Promise<HTMLElement> {
  return screen.findByRole('region', { name })
}

async function clickSubmit(
  user: ReturnType<typeof userEvent.setup>,
  region: HTMLElement,
  reason?: string,
): Promise<void> {
  if (reason !== undefined) {
    const input = within(region).getByTestId('write-reason')
    await user.type(input, reason)
  }
  const submit = within(region).getByTestId('write-submit') as HTMLButtonElement
  await waitFor(() => expect(submit.disabled).toBe(false))
  await user.click(submit)
}

function postBody(path: string): Record<string, unknown> {
  const calls = fake!.callsTo('POST', path)
  expect(calls).toHaveLength(1)
  return calls[0]?.body ?? {}
}

function expectEventId(value: unknown): void {
  expect(String(value)).toMatch(/^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$/)
}

/* -------------------------------------------------------------------------- *
 * The seven routes, one success path each
 * -------------------------------------------------------------------------- */

describe('the seven Task/Step write panels send the frozen body and show Core the answer', () => {
  it('route 1: Task create sends title/project/host, never expected_revision', async () => {
    const user = userEvent.setup()
    await enter(user, `#/project/${PROJECT_ID}`, {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: { hosts: [HOST] } },
      [`GET /v1/projects/${PROJECT_ID}`]: {
        body: {
          project: {
            project_id: PROJECT_ID,
            name: 'p',
            description: '',
            status: 'ACTIVE',
            revision: 1,
            source_event_id: EVENT_ID,
            created_at: AT,
            updated_at: AT,
          },
        },
      },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/sessions': { body: { sessions: [], count: 0 } },
      'GET /v1/events': { body: { events: [], count: 0 } },
      'POST /v1/tasks': {
        status: 201,
        body: {
          object: task({ task_id: `tsk_${ID}` }),
          event: { event_id: EVENT_ID, event_type: 'task.created' },
          replayed: false,
        },
      },
    })
    const region = await panel('Task 创建 (objects:write)')
    await user.type(within(region).getByTestId('task-create-title'), 'Created from the dashboard')
    await clickSubmit(user, region)

    const sent = postBody('/v1/tasks')
    expect(Object.keys(sent).sort()).toEqual(['event_id', 'host_id', 'project_id', 'title'])
    // The route rejects expected_revision, so the panel must not send one.
    expect(sent).not.toHaveProperty('expected_revision')
    expect(sent['project_id']).toBe(PROJECT_ID)
    expect(sent['host_id']).toBe(HOST.host_id)
    expect(sent['title']).toBe('Created from the dashboard')
    expectEventId(sent['event_id'])
    // The response is shown verbatim and the Task list is re-read, not optimistically inserted.
    await waitFor(() => expect(within(region).getByText(/已提交/)).toBeTruthy())
    await waitFor(() => expect(fake!.callsTo('GET', '/v1/tasks').length).toBeGreaterThan(1))
  })

  it('route 2: Step create sends the Task revision and a title', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/steps`]: {
        status: 201,
        body: {
          step: step({ revision: 1 }),
          task_revision: 13,
          event: { event_id: EVENT_ID, event_type: 'step.created' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Step 创建 (state:write)')
    await user.type(within(region).getByTestId('step-create-title'), 'New Step')
    await clickSubmit(user, region)

    const sent = postBody(`/v1/tasks/${TASK_ID}/steps`)
    expect(Object.keys(sent).sort()).toEqual(
      ['acceptance_criteria', 'event_id', 'expected_revision', 'host_id', 'title'].sort(),
    )
    // Step creation compares against the Task's revision, not any Step's.
    expect(sent['expected_revision']).toBe(12)
    expect(sent['acceptance_criteria']).toEqual({ requirements: [] })
    await waitFor(() => expect(within(region).getByText(/已提交/)).toBeTruthy())
  })

  it('route 3: Step transition sends the Step revision, not the Task revision', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/steps/${STEP_ID}/transition`]: {
        body: {
          step: step({ status: 'IN_PROGRESS', revision: 4 }),
          task_revision: 13,
          evidence_ids: [],
          rule_versions: {},
          event: { event_id: EVENT_ID, event_type: 'step.transitioned' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Step 状态转换 (state:write)')
    await clickSubmit(user, region)

    const sent = postBody(`/v1/steps/${STEP_ID}/transition`)
    expect(Object.keys(sent).sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id', 'status'].sort(),
    )
    expect(sent['expected_revision']).toBe(3)
    expect(sent['status']).toBe('IN_PROGRESS')
    // The Step write moves the Task too, so the readback names both.
    await waitFor(() => expect(screen.getByTestId('write-readback')).toBeTruthy())
    const readback = screen.getByTestId('write-readback')
    expect(readback.textContent).toContain('step revision')
    expect(readback.textContent).toContain('task revision')
  })

  it('route 4: Task transition requires a reason and sends the Task revision', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/transition`]: {
        body: {
          task: task({ status: 'BLOCKED', revision: 13 }),
          event: { event_id: EVENT_ID, event_type: 'task.transitioned' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Task 状态转换 (state:write)')
    await clickSubmit(user, region, 'blocking on review')

    const sent = postBody(`/v1/tasks/${TASK_ID}/transition`)
    expect(Object.keys(sent).sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id', 'reason', 'status'].sort(),
    )
    expect(sent['expected_revision']).toBe(12)
    expect(sent['reason']).toBe('blocking on review')
  })

  it('route 5: Task criteria sends the parsed criteria and the Task revision', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/criteria`]: {
        body: {
          task: task({ revision: 13 }),
          event: { event_id: EVENT_ID, event_type: 'task.criteria_updated' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Task criteria 更新 (state:accept)')
    const textarea = within(region).getByTestId('task-criteria')
    await user.clear(textarea)
    await user.click(textarea)
    await user.paste('{"requirements":[{"key":"build","kind":"BUILD","required_result":"PASS"}]}')
    await clickSubmit(user, region)

    const sent = postBody(`/v1/tasks/${TASK_ID}/criteria`)
    expect(Object.keys(sent).sort()).toEqual(
      ['acceptance_criteria', 'event_id', 'expected_revision', 'host_id'].sort(),
    )
    expect(sent['expected_revision']).toBe(12)
    expect(sent['acceptance_criteria']).toEqual({
      requirements: [{ key: 'build', kind: 'BUILD', required_result: 'PASS' }],
    })
  })

  it('route 6: Task accept sends only the Task revision, host and replay key', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/accept`]: {
        body: {
          task: task({ status: 'ACCEPTED', revision: 13 }),
          evidence_ids: [`evd_${ID}`],
          rule_versions: { 'rul_1': 2 },
          event: { event_id: EVENT_ID, event_type: 'task.accepted' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
    await clickSubmit(user, region)

    const sent = postBody(`/v1/tasks/${TASK_ID}/accept`)
    expect(Object.keys(sent).sort()).toEqual(['event_id', 'expected_revision', 'host_id'].sort())
    expect(sent['expected_revision']).toBe(12)
    // Core's evidence decision is shown from its response, never recomputed.
    await waitFor(() => expect(within(region).getByText(/已提交/)).toBeTruthy())
    expect(within(region).getByText(/evd_/)).toBeTruthy()
  })

  it('route 7: Step criteria sends the parsed criteria and the Step revision', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/steps/${STEP_ID}/criteria`]: {
        body: {
          step: step({ revision: 4 }),
          task_revision: 13,
          event: { event_id: EVENT_ID, event_type: 'step.criteria_updated' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Step criteria 更新 (state:accept)')
    await clickSubmit(user, region)

    const sent = postBody(`/v1/steps/${STEP_ID}/criteria`)
    expect(Object.keys(sent).sort()).toEqual(
      ['acceptance_criteria', 'event_id', 'expected_revision', 'host_id'].sort(),
    )
    expect(sent['expected_revision']).toBe(3)
  })
})

/* -------------------------------------------------------------------------- *
 * Replay, retry and readback
 * -------------------------------------------------------------------------- */

describe('replay and recovery', () => {
  it('shows a replayed response as the same write, not a new one', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/steps/${STEP_ID}/transition`]: {
        body: {
          step: step({ status: 'IN_PROGRESS', revision: 4 }),
          task_revision: 13,
          evidence_ids: [],
          rule_versions: {},
          event: { event_id: EVENT_ID, event_type: 'step.transitioned' },
          replayed: true,
        },
      },
    }))
    const region = await panel('Step 状态转换 (state:write)')
    await clickSubmit(user, region)
    await waitFor(() =>
      expect(within(region).getByText(/replayed: Core 返回了原命令结果/)).toBeTruthy(),
    )
    expect(fake!.callsTo('POST', `/v1/steps/${STEP_ID}/transition`)).toHaveLength(1)
  })

  it('reuses one event_id when the identical body is retried after an unknown result', async () => {
    const user = userEvent.setup()
    let attempts = 0
    await enter(
      user,
      WRITE_HASH,
      baseRoutes(),
      (request) => {
        if (request.method === 'POST' && request.path === `/v1/steps/${STEP_ID}/transition`) {
          attempts += 1
          if (attempts === 1) return { networkError: 'connect ECONNREFUSED' }
          return {
            body: {
              step: step({ status: 'IN_PROGRESS', revision: 4 }),
              task_revision: 13,
              evidence_ids: [],
              rule_versions: {},
              event: { event_id: EVENT_ID, event_type: 'step.transitioned' },
              replayed: true,
            },
          }
        }
        if (request.method === 'GET' && request.path === '/v1/projects') {
          return { body: { projects: [], count: 0 } }
        }
        if (request.method === 'GET' && request.path === '/v1/health') return { body: HEALTH }
        if (request.method === 'GET' && request.path === '/v1/hosts') return { body: { hosts: [HOST] } }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}`) {
          return { body: { task: task() } }
        }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/steps`) {
          return { body: { steps: [step()] } }
        }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/history`) {
          return { body: { events: [], count: 0 } }
        }
        return undefined
      },
    )
    const region = await panel('Step 状态转换 (state:write)')
    await clickSubmit(user, region)
    await within(region).findByTestId('write-unknown')

    // The retry repeats the identical body, so Core replays rather than writes twice.
    await clickSubmit(user, region)
    await waitFor(() => expect(attempts).toBe(2))
    const calls = fake!.callsTo('POST', `/v1/steps/${STEP_ID}/transition`)
    expect(calls).toHaveLength(2)
    expect(calls[0]?.body?.['event_id']).toBe(calls[1]?.body?.['event_id'])
    expectEventId(calls[0]?.body?.['event_id'])
    await waitFor(() =>
      expect(within(region).getByText(/replayed: Core 返回了原命令结果/)).toBeTruthy(),
    )
  })

  it('shows Core re-read value after a write, not the write response', async () => {
    const user = userEvent.setup()
    let reads = 0
    await enter(
      user,
      WRITE_HASH,
      baseRoutes(),
      (request) => {
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}`) {
          reads += 1
          // The re-read really moved; the readback cannot be showing the response by accident.
          return { body: { task: task({ revision: reads === 1 ? 12 : 13, status: reads === 1 ? 'ACTIVE' : 'BLOCKED' }) } }
        }
        if (request.method === 'POST' && request.path === `/v1/tasks/${TASK_ID}/transition`) {
          return {
            body: {
              task: task({ revision: 13, status: 'BLOCKED' }),
              event: { event_id: EVENT_ID, event_type: 'task.transitioned' },
              replayed: false,
            },
          }
        }
        if (request.method === 'GET' && request.path === '/v1/projects') {
          return { body: { projects: [], count: 0 } }
        }
        if (request.method === 'GET' && request.path === '/v1/health') return { body: HEALTH }
        if (request.method === 'GET' && request.path === '/v1/hosts') return { body: { hosts: [HOST] } }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/steps`) {
          return { body: { steps: [step()] } }
        }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/history`) {
          return { body: { events: [], count: 0 } }
        }
        return undefined
      },
    )
    const region = await panel('Task 状态转换 (state:write)')
    await waitFor(() => expect(within(region).getByText(/revision r12/)).toBeTruthy())
    await clickSubmit(user, region, 'blocked by review')
    await waitFor(() => expect(reads).toBeGreaterThan(1))
    // Exactly one automatic re-read: a reload callback in the effect's dependency list re-ran
    // the effect on every render while the state stayed `succeeded`, reloading forever.
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(reads).toBe(2)
    const readback = await screen.findByTestId('write-readback')
    expect(readback.textContent).toContain('r13')
    expect(readback.textContent).toContain('BLOCKED')
    expect(readback.textContent).toContain('读回时间')
  })

  it('re-reads the command history after a write, not just the object row', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/transition`]: {
        body: {
          task: task({ status: 'BLOCKED', revision: 13 }),
          event: { event_id: EVENT_ID, event_type: 'task.transitioned' },
          replayed: false,
        },
      },
    }))
    const region = await panel('Task 状态转换 (state:write)')
    await waitFor(() =>
      expect(fake!.callsTo('GET', `/v1/tasks/${TASK_ID}/history`).length).toBe(1),
    )
    await clickSubmit(user, region, 'reason')
    // §8: the readback is the object row *and* the history, so the history is fetched again.
    await waitFor(() =>
      expect(fake!.callsTo('GET', `/v1/tasks/${TASK_ID}/history`).length).toBeGreaterThan(1),
    )
  })
})

/* -------------------------------------------------------------------------- *
 * Timeout / unreachable
 * -------------------------------------------------------------------------- */

describe('a timeout or an unreachable Core is unknown, never success or failure', () => {
  it('names the request id it sent and offers no verdict', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      baseRoutes(),
      (request) => {
        if (request.method === 'POST' && request.path === `/v1/tasks/${TASK_ID}/transition`) {
          return { networkError: 'connect ECONNREFUSED' }
        }
        if (request.method === 'GET' && request.path === '/v1/projects') {
          return { body: { projects: [], count: 0 } }
        }
        if (request.method === 'GET' && request.path === '/v1/health') return { body: HEALTH }
        if (request.method === 'GET' && request.path === '/v1/hosts') return { body: { hosts: [HOST] } }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}`) {
          return { body: { task: task() } }
        }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/steps`) {
          return { body: { steps: [step()] } }
        }
        if (request.method === 'GET' && request.path === `/v1/tasks/${TASK_ID}/history`) {
          return { body: { events: [], count: 0 } }
        }
        return undefined
      },
    )
    const region = await panel('Task 状态转换 (state:write)')
    await clickSubmit(user, region, 'reason')

    const unknown = await within(region).findByTestId('write-unknown')
    expect(unknown.textContent).toContain('结果未知')
    const sent = fake!.callsTo('POST', `/v1/tasks/${TASK_ID}/transition`)[0]?.requestId
    expect(sent).toMatch(/^ui-/)
    expect(within(region).getByTestId('write-request-id').textContent).toContain(sent ?? '')
    // No verdict either way.
    expect(within(region).queryByText(/已提交/)).toBeNull()
    expect(within(region).queryByText(/被拒绝/)).toBeNull()
    // The guidance is to retry with the same key, not a fresh one.
    expect(unknown.textContent).toContain('event_id')
    // And recovery option two is offered: read the truth back rather than guess.
    const reads = fake!.callsTo('GET', `/v1/tasks/${TASK_ID}`).length
    await user.click(within(region).getByRole('button', { name: '从 Core 重新读取' }))
    await waitFor(() =>
      expect(fake!.callsTo('GET', `/v1/tasks/${TASK_ID}`).length).toBeGreaterThan(reads),
    )
  })

  it('shows a proxy 502 with no id as unknown with an explicitly unknown request id', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/transition`]: {
        status: 502,
        body: { error: { code: 'proxy_core_unreachable', message: 'Core did not answer' } },
      },
    }))
    const region = await panel('Task 状态转换 (state:write)')
    await clickSubmit(user, region, 'reason')
    const unknown = await within(region).findByTestId('write-unknown')
    expect(unknown.textContent).toContain('proxy_core_unreachable')
    const idLine = within(region).getByTestId('write-request-id')
    expect(idLine.textContent).toContain('未知')
    expect(idLine.textContent).toContain('关联缺口')
    expect(within(region).queryByText(/已提交/)).toBeNull()
    expect(within(region).queryByText(/被拒绝/)).toBeNull()
  })

  it('names the request id on a refusal too, when the response carries one', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes({
      [`POST /v1/tasks/${TASK_ID}/transition`]: {
        status: 403,
        headers: { 'x-request-id': 'proxy-refusal-id' },
        body: {
          error: {
            code: 'forbidden_scope',
            message: 'this key does not carry state:write',
            details: { required: 'state:write', granted: ['state:read'] },
          },
        },
      },
    }))
    const region = await panel('Task 状态转换 (state:write)')
    await clickSubmit(user, region, 'reason')
    await within(region).findByText(/被拒绝/)
    // The id came from the response header: without §5 change 4 the browser could not name it.
    expect(within(region).getByTestId('write-request-id').textContent).toContain('proxy-refusal-id')
  })
})

/* -------------------------------------------------------------------------- *
 * Every refusal is its own condition
 * -------------------------------------------------------------------------- */

async function refusalCase(
  user: ReturnType<typeof userEvent.setup>,
  routePath: string,
  panelName: string,
  error: { status: number; code: string; message: string; details?: unknown },
  reason: string | undefined,
): Promise<HTMLElement> {
  await enter(user, WRITE_HASH, baseRoutes({
    [`POST ${routePath}`]: { status: error.status, body: { error } },
  }))
  const region = await panel(panelName)
  await clickSubmit(user, region, reason)
  return region
}

describe('every refusal is rendered as its own condition', () => {
  it('409 revision_conflict shows both revisions and keeps the draft', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/tasks/${TASK_ID}/transition`,
      'Task 状态转换 (state:write)',
      {
        status: 409,
        code: 'revision_conflict',
        message: 'expected_revision does not match current revision',
        details: { expected_revision: 12, current_revision: 15, kind: 'task', current_snapshot: {} },
      },
      'my reason',
    )
    const conflict = await within(region).findByText(/冲突,已保留草稿/)
    expect(conflict.textContent).toContain('revision_conflict')
    const fields = within(region).getByTestId('write-refusal-fields')
    expect(fields.textContent).toContain('expected_revision')
    expect(fields.textContent).toContain('12')
    expect(fields.textContent).toContain('current_revision')
    expect(fields.textContent).toContain('15')
    // The draft survives, and no automatic retry happened.
    expect((within(region).getByTestId('write-reason') as HTMLInputElement).value).toBe('my reason')
    expect(fake!.callsTo('POST', `/v1/tasks/${TASK_ID}/transition`)).toHaveLength(1)
  })

  it('409 invalid_state_transition shows from/to as a refusal, not a conflict', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/steps/${STEP_ID}/transition`,
      'Step 状态转换 (state:write)',
      {
        status: 409,
        code: 'invalid_state_transition',
        message: 'invalid Step transition',
        details: { from_status: 'PLANNED', to_status: 'ACCEPTED' },
      },
      undefined,
    )
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('invalid_state_transition')
    const fields = within(region).getByTestId('write-refusal-fields')
    expect(fields.textContent).toContain('PLANNED')
    expect(fields.textContent).toContain('ACCEPTED')
    // A state refusal is not the conflict block that invites a re-review retry.
    expect(within(region).queryByText(/冲突,已保留草稿/)).toBeNull()
  })

  it('403 forbidden_scope names the missing scope and offers no other credential', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/tasks/${TASK_ID}/criteria`,
      'Task criteria 更新 (state:accept)',
      {
        status: 403,
        code: 'forbidden_scope',
        message: 'this key does not carry state:accept',
        details: { required: 'state:accept', granted: ['state:read', 'state:write'] },
      },
      undefined,
    )
    await within(region).findByText(/被拒绝/)
    expect(within(region).getByTestId('write-refusal-fields').textContent).toContain('state:accept')
    expect(within(region).getByText(/不会换更高权限的凭据重试/)).toBeTruthy()
  })

  it('403 forbidden_actor_kind is its own refusal', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/tasks/${TASK_ID}/accept`,
      'Task 验收 (state:accept,Evidence 门槛)',
      {
        status: 403,
        code: 'forbidden_actor_kind',
        message: 'state:accept requires a human or system actor; this actor is agent',
        details: { actor_kind: 'agent', required: 'human|system' },
      },
      undefined,
    )
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('forbidden_actor_kind')
    expect(region.textContent).toContain('human or system actor')
    expect(within(region).queryByText(/已提交/)).toBeNull()
  })

  it('409 event_id_conflict is a conflict with Core detail, not a silent retry', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/steps/${STEP_ID}/criteria`,
      'Step criteria 更新 (state:accept)',
      {
        status: 409,
        code: 'event_id_conflict',
        message: 'this event_id already belongs to a different request',
        details: { event_id: EVENT_ID },
      },
      undefined,
    )
    const conflict = await within(region).findByText(/冲突,已保留草稿/)
    expect(conflict.textContent).toContain('event_id_conflict')
    expect(fake!.callsTo('POST', `/v1/steps/${STEP_ID}/criteria`)).toHaveLength(1)
  })

  it('409 source_event_duplicate is a conflict naming the source identity', async () => {
    const user = userEvent.setup()
    await enter(user, `#/project/${PROJECT_ID}`, {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/hosts': { body: { hosts: [HOST] } },
      [`GET /v1/projects/${PROJECT_ID}`]: {
        body: {
          project: {
            project_id: PROJECT_ID, name: 'p', description: '', status: 'ACTIVE', revision: 1,
            source_event_id: EVENT_ID, created_at: AT, updated_at: AT,
          },
        },
      },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/sessions': { body: { sessions: [], count: 0 } },
      'GET /v1/events': { body: { events: [], count: 0 } },
      'POST /v1/tasks': {
        status: 409,
        body: {
          error: {
            code: 'source_event_duplicate',
            message: 'source identity already created a Task',
            details: {
              source_system: 'probe-src',
              source_event_id: 'src-001',
              existing_event_id: EVENT_ID,
            },
          },
        },
      },
    })
    const region = await panel('Task 创建 (objects:write)')
    await user.type(within(region).getByTestId('task-create-title'), 'dup')
    await user.type(within(region).getByTestId('task-create-source-system'), 'probe-src')
    await user.type(within(region).getByTestId('task-create-source-event-id'), 'src-001')
    await clickSubmit(user, region)
    const conflict = await within(region).findByText(/冲突,已保留草稿/)
    expect(conflict.textContent).toContain('source_event_duplicate')
    const fields = within(region).getByTestId('write-refusal-fields')
    expect(fields.textContent).toContain('probe-src')
    expect(fields.textContent).toContain('src-001')
    expect(fields.textContent).toContain(EVENT_ID)
  })

  it('400 invalid_request names the offending field', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/steps/${STEP_ID}/transition`,
      'Step 状态转换 (state:write)',
      {
        status: 400,
        code: 'invalid_request',
        message: 'reason must be nonempty and at most 4096 characters',
        details: { field: 'reason' },
      },
      undefined,
    )
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('invalid_request')
    expect(within(region).getByTestId('write-refusal-fields').textContent).toContain('reason')
  })

  it('422 missing_evidence is recorded as BLOCKED with Core reason, not as a failure', async () => {
    const user = userEvent.setup()
    const region = await refusalCase(
      user,
      `/v1/tasks/${TASK_ID}/accept`,
      'Task 验收 (state:accept,Evidence 门槛)',
      {
        status: 422,
        code: 'missing_evidence',
        message: 'a requirement was not matched',
        details: { requirement_key: 'build', rule_id: 'rul_1', rule_version: 2 },
      },
      undefined,
    )
    const blocked = await within(region).findByTestId('write-evidence-blocked')
    expect(blocked.textContent).toContain('BLOCKED')
    expect(blocked.textContent).toContain('missing_evidence')
    expect(blocked.textContent).toContain('a requirement was not matched')
    expect(blocked.textContent).toContain('没有发生写入')
    expect(blocked.textContent).toContain('build')
    // It is neither a success nor the generic "被拒绝" failure banner.
    expect(within(region).queryByText(/已提交/)).toBeNull()
    expect(within(region).queryByText(/^被拒绝/)).toBeNull()
  })
})

/* -------------------------------------------------------------------------- *
 * The criteria textarea refuses what cannot be sent
 * -------------------------------------------------------------------------- */

describe('criteria input is validated locally only for what cannot be sent at all', () => {
  it('keeps submit disabled on non-JSON criteria and never posts', async () => {
    const user = userEvent.setup()
    await enter(user, WRITE_HASH, baseRoutes())
    const region = await panel('Task criteria 更新 (state:accept)')
    const textarea = within(region).getByTestId('task-criteria')
    await user.clear(textarea)
    await user.click(textarea)
    await user.paste('{not json')
    await waitFor(() => expect(within(region).getByText(/不是合法 JSON/)).toBeTruthy())
    expect((within(region).getByTestId('write-submit') as HTMLButtonElement).disabled).toBe(true)
    expect(fake!.callsTo('POST', `/v1/tasks/${TASK_ID}/criteria`)).toHaveLength(0)
  })
})
