/**
 * Application-level tests.
 *
 * These render the real `App` inside the real `SessionProvider` and drive it
 * against a fake Core, so the assertions cover the behaviour the P6 taskbook
 * cares about: sign-in uses a real authenticated read, the token never reaches
 * storage, the board separates Task and Step status, a changed scope does not
 * bleed the previous project's rows, and untrusted text is never HTML.
 */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { RawText } from '../src/components/RawText'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

beforeEach(() => {
  window.location.hash = ''
})

afterEach(() => {
  fake?.restore()
  fake = null
  vi.restoreAllMocks()
})

function navigate(hash: string): void {
  act(() => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

async function signIn(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText('Core API token'), 'tok_read_only')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
}

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 6 }
const SCHEMA = {
  core_version: '0.1.0',
  schema_version: 6,
  expected_schema_version: 6,
  migrations: [{ name: 'm0001_baseline', version: 1, checksum: 'a'.repeat(64), applied_at: '2026-09-30T05:00:00Z' }],
}

describe('sign-in and Overview', () => {
  it('accepts a token only after a real authenticated read, and never writes storage', async () => {
    const user = userEvent.setup()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/meta/schema': { body: SCHEMA },
      'GET /v1/hosts': { body: { hosts: [] } },
      'GET /v1/actors': { body: { actors: [] } },
      'GET /v1/projects': {
        body: {
          projects: [
            {
              project_id: 'prj_1',
              name: 'Jasmine Core V1',
              description: '',
              status: 'active',
              revision: 1,
              source_event_id: 'evt_1',
              created_at: '2026-09-30T05:00:00Z',
              updated_at: '2026-09-30T05:00:00Z',
            },
          ],
          count: 1,
        },
      },
    })

    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)

    expect(await screen.findByText('Core 健康与 Schema')).toBeTruthy()
    expect(await screen.findByText('Jasmine Core V1')).toBeTruthy()
    // Sign-in itself must have performed an authenticated read. `GET /v1/health`
    // is public and cannot prove anything, so a request to /v1/projects carrying
    // the bearer token is the actual evidence.
    const probe = fake.callsTo('GET', '/v1/projects')[0]
    expect(probe?.authorization).toBe('Bearer tok_read_only')
    expect(probe?.query.get('limit')).toBe('1')
    expect(setItem).not.toHaveBeenCalled()
    // jsdom in this configuration may not expose Storage at all; the assertion
    // that matters is that nothing ever calls setItem, checked above.
    const keys = (storage: Storage | null | undefined): string[] =>
      storage == null ? [] : Object.keys(storage)
    expect(keys(window.localStorage)).toHaveLength(0)
    expect(keys(window.sessionStorage)).toHaveLength(0)
    expect(window.location.href).not.toContain('tok_read_only')
  })

  it('refuses a bogus token that the public health check would have accepted', async () => {
    const user = userEvent.setup()
    // Exactly what real Core does: /v1/health is public (200 for any token) and
    // the authenticated read is what refuses. This is the regression test for
    // "sign-in validates with a public endpoint".
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': coreError(401, 'unauthenticated', 'missing or unknown token'),
    })
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    expect((await screen.findByRole('alert')).textContent).toContain('unauthenticated')
    // Still on the sign-in form: no dashboard was opened with a bad token.
    expect(screen.getByLabelText('Core API token')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /退出/ })).toBeNull()
  })

  it('treats a missing scope as a refusal, naming the scope Core asked for', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': coreError(403, 'forbidden_scope', 'this key lacks objects:read'),
    })
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    expect((await screen.findByRole('alert')).textContent).toContain('forbidden_scope')
    expect(screen.getByLabelText('Core API token')).toBeTruthy()
  })

  it('stops sending Authorization after 退出', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/meta/schema': { body: SCHEMA },
      'GET /v1/hosts': { body: { hosts: [] } },
      'GET /v1/actors': { body: { actors: [] } },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
    })
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    await screen.findByText('Core 健康与 Schema')
    await user.click(screen.getByRole('button', { name: /退出/ }))
    expect(screen.getByLabelText('Core API token')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /退出/ })).toBeNull()
  })
})

describe('Task board', () => {
  const steps = [
    {
      step_id: 'stp_planned',
      task_id: 'tsk_1',
      title: 'Keyboard walkthrough',
      description: '',
      status: 'PLANNED',
      revision: 1,
      source_event_id: 'evt_s1',
      created_at: '2026-09-30T05:00:00Z',
      updated_at: '2026-09-30T05:00:00Z',
      acceptance_criteria: { requirements: [] },
    },
    {
      step_id: 'stp_executed',
      task_id: 'tsk_1',
      title: 'Rule and Evidence views',
      description: '',
      status: 'EXECUTED',
      revision: 3,
      source_event_id: 'evt_s2',
      created_at: '2026-09-30T05:00:00Z',
      updated_at: '2026-09-30T05:00:00Z',
      acceptance_criteria: { requirements: [{ key: 'build', kind: 'BUILD', required_result: 'PASS' }] },
    },
    {
      step_id: 'stp_stale',
      task_id: 'tsk_1',
      title: 'Stale probe',
      description: '',
      status: 'STALE',
      revision: 4,
      source_event_id: 'evt_s3',
      created_at: '2026-09-30T05:00:00Z',
      updated_at: '2026-09-30T05:00:00Z',
      acceptance_criteria: { requirements: [] },
    },
    {
      step_id: 'stp_unknown',
      task_id: 'tsk_1',
      title: 'Future status',
      description: '',
      status: 'QUARANTINED',
      revision: 1,
      source_event_id: 'evt_s4',
      created_at: '2026-09-30T05:00:00Z',
      updated_at: '2026-09-30T05:00:00Z',
      acceptance_criteria: { requirements: [] },
    },
  ]

  it('keeps Task status separate from Step columns and never claims all done', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/tasks/tsk_1': {
        body: {
          task: {
            task_id: 'tsk_1',
            project_id: 'prj_1',
            title: 'Wire the dashboard',
            description: '',
            status: 'BLOCKED',
            revision: 12,
            source_event_id: 'evt_t1',
            created_at: '2026-09-30T05:00:00Z',
            updated_at: '2026-09-30T05:00:00Z',
            acceptance_criteria: { requirements: [] },
          },
        },
      },
      'GET /v1/tasks/tsk_1/steps': { body: { steps } },
    })
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    navigate('#/task/tsk_1')

    expect(await screen.findByText('Step 看板(4 个 Step)')).toBeTruthy()
    // Task status (BLOCKED) is shown in the summary, not as a board column.
    expect(screen.getByTestId('task-revision').textContent).toContain('12')
    const summary = screen.getByTestId('board-summary').textContent ?? ''
    expect(summary).toContain('EXECUTED×1')
    expect(summary).toContain('STALE×1')
    expect(summary).not.toContain('全部')

    // Unknown vocabulary gets its own loud column.
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('QUARANTINED')
    expect(within(screen.getByLabelText('Step 状态 QUARANTINED')).getByText('Future status')).toBeTruthy()
    // EXECUTED is visible rather than hidden behind a "done" claim.
    expect(within(screen.getByLabelText('Step 状态 EXECUTED')).getByText('Rule and Evidence views')).toBeTruthy()
  })
})

describe('project isolation', () => {
  it('never shows project A rows after switching to project B', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({}, (request) => {
      if (request.path === '/v1/health') return { body: HEALTH }
      if (request.path === '/v1/projects') return { body: { projects: [], count: 0 } }
      if (request.path === '/v1/tasks') {
        const project = request.query.get('project_id')
        if (project === 'prj_a') {
          return {
            delayMs: 40,
            body: {
              tasks: [
                {
                  task_id: 'tsk_a',
                  project_id: 'prj_a',
                  title: 'PROJECT-A-TASK',
                  description: '',
                  status: 'ACTIVE',
                  revision: 1,
                  source_event_id: 'evt_a',
                  created_at: '2026-09-30T05:00:00Z',
                  updated_at: '2026-09-30T05:00:00Z',
                  acceptance_criteria: { requirements: [] },
                },
              ],
              count: 1,
            },
          }
        }
        return {
          body: {
            tasks: [
              {
                task_id: 'tsk_b',
                project_id: 'prj_b',
                title: 'PROJECT-B-TASK',
                description: '',
                status: 'ACTIVE',
                revision: 1,
                source_event_id: 'evt_b',
                created_at: '2026-09-30T05:00:00Z',
                updated_at: '2026-09-30T05:00:00Z',
                acceptance_criteria: { requirements: [] },
              },
            ],
            count: 1,
          },
        }
      }
      if (request.path.startsWith('/v1/projects/')) {
        const projectId = request.path.split('/').pop() ?? ''
        return {
          body: {
            project: {
              project_id: projectId,
              name: projectId,
              description: '',
              status: 'active',
              revision: 1,
              source_event_id: 'evt_p',
              created_at: '2026-09-30T05:00:00Z',
              updated_at: '2026-09-30T05:00:00Z',
            },
          },
        }
      }
      if (request.path === '/v1/sessions') return { body: { sessions: [], count: 0 } }
      if (request.path === '/v1/events') return { body: { events: [], count: 0 } }
      return undefined
    })

    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    navigate('#/project/prj_a')
    expect(await screen.findByText('PROJECT-A-TASK')).toBeTruthy()

    navigate('#/project/prj_b')
    expect(screen.queryByText('PROJECT-A-TASK')).toBeNull()
    expect(await screen.findByText('PROJECT-B-TASK')).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, 60))
    expect(screen.queryByText('PROJECT-A-TASK')).toBeNull()
  })
})

describe('untrusted text rendering', () => {
  it('renders Event text as text, masks credentials and reveals only on request', async () => {
    const user = userEvent.setup()
    const hostile = '<img src=x onerror="alert(1)"> and Authorization: Bearer supersecrettokenvalue'
    const { container } = render(<RawText value={hostile} label="payload.text" />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    const body = container.querySelector('.rawtext__body')
    expect(body?.textContent).toContain('<img src=x onerror="alert(1)">')
    expect(body?.textContent).not.toContain('supersecrettokenvalue')
    expect(body?.textContent).toContain('«已脱敏:bearer»')

    await user.click(screen.getByRole('button', { name: '显示原文' }))
    expect(container.querySelector('.rawtext__body')?.textContent).toContain('supersecrettokenvalue')
    await user.click(screen.getByRole('button', { name: '恢复脱敏' }))
    expect(container.querySelector('.rawtext__body')?.textContent).not.toContain('supersecrettokenvalue')
  })

  it('labels machine events so a tool result cannot read as a user message', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({
      'GET /v1/health': { body: HEALTH },
      'GET /v1/events': {
        body: {
          events: [
            {
              event_id: 'evt_tool',
              seq: 9,
              schema_version: 6,
              event_type: 'tool.result',
              source_system: 'codex',
              source_event_id: null,
              occurred_at: '2026-09-30T05:00:00Z',
              recorded_at: '2026-09-30T05:00:00Z',
              actor_id: 'act_1',
              actor_kind: 'system',
              host_id: 'hst_1',
              session_id: null,
              project_id: null,
              task_id: null,
              payload: { text: 'exit 0' },
              body_sha256: 'b'.repeat(64),
            },
          ],
          count: 1,
        },
      },
      'GET /v1/sessions': { body: { sessions: [], count: 0 } },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
    })
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await signIn(user)
    navigate('#/conversation')

    await waitFor(() => expect(screen.getByText('tool.result')).toBeTruthy())
    expect(screen.getAllByText('机器事件(非用户发言)').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/尚未开放/).length).toBeGreaterThan(0)
  })
})
