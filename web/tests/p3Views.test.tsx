/**
 * P6-03a view behaviour.
 *
 * Three things these pin, because each is a way this slice could lie:
 *
 * 1. **A refused P3 read says so.** On a baseline dashboard the proxy answers
 *    `proxy_path_not_allowed`, and the view must render the dependency-pending panel —
 *    not an error panel (which would blame Core) and not an empty list (which would
 *    invent "there is nothing here").
 * 2. **`checkpoint: null` is empty, not an error.** Core answers 200 with a null
 *    checkpoint when a Task has none; that is an empty result.
 * 3. **A projection is a record.** The checkpoint view must say the projection is an
 *    immutable snapshot at a revision, and a resume's note must be shown as historical
 *    with `authority: false`, never as Truth.
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
const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const TASK = `tsk_${ID}`
const CHECKPOINT = `ckp_${ID}`
const PACK = `ctx_${ID}`
const RESUME = `rms_${ID}`

/** Exactly what the baseline proxy answers for a path it does not forward. */
const PROXY_REFUSAL = {
  status: 404,
  body: { error: { code: 'proxy_path_not_allowed', message: 'the dashboard proxy does not forward this path' } },
}

function checkpoint(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    checkpoint_id: CHECKPOINT,
    command_event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AK8',
    source_event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AK9',
    project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8',
    task_id: TASK,
    host_id: 'hst_01M4B6VGC29ESRDRQR62DX8AK8',
    actor_id: 'act_01M4B6VGC29ESRDRQR62DX8AK8',
    session_id: null,
    reason: 'SESSION_STOP',
    trigger_provenance: { level: 'UNVERIFIED_REQUEST', source_event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AK9' },
    projection_version: 'jasmine.task-snapshot.v1',
    projection: { task: { title: 'fixture task' } },
    projection_digest: 'a'.repeat(64),
    task_revision: 7,
    full_context_digest: 'b'.repeat(64),
    workspace: { files: [] },
    workspace_digest: 'c'.repeat(64),
    note: { text: 'stop here for today' },
    created_at: '2026-10-07T12:00:00Z',
    ...overrides,
  }
}

function resume(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    resume_id: RESUME,
    command_event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AKA',
    source_event_id: 'evt_01M4B6VGC29ESRDRQR62DX8AKB',
    actor_id: 'act_01M4B6VGC29ESRDRQR62DX8AK8',
    host_id: 'hst_01M4B6VGC29ESRDRQR62DX8AK8',
    project_id: 'prj_01M4B6VGC29ESRDRQR62DX8AK8',
    task_id: TASK,
    session_id: null,
    checkpoint_id: CHECKPOINT,
    created_at: '2026-10-07T12:00:00Z',
    projection: { task: { title: 'fixture task' } },
    truth_digest: 'd'.repeat(64),
    workspace: { files: [] },
    sample_window: {},
    comparison: { changed: true },
    note: { value: { text: 'stop here for today' }, authority: false, historical: true },
    next_actions: ['re-read the task'],
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

const SIGNED_IN = { 'GET /v1/projects': { body: { projects: [], count: 0 } } }

describe('a refused P3 read is shown as a missing dependency', () => {
  it('checkpoint (latest) marks the pending dependency instead of blaming Core', async () => {
    const user = userEvent.setup()
    await enter(user, `#/checkpoints?task_id=${TASK}`, {
      ...SIGNED_IN,
      ['GET /v1/tasks/' + TASK + '/checkpoints/latest']: PROXY_REFUSAL,
    })
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(within(panel).getByTestId('dependency-pending')).toBeTruthy())
    const text = panel.textContent ?? ''
    expect(text).toContain('P3')
    expect(text).toContain('DEPENDENCY_PENDING')
    expect(text).toContain('checkpoint:read')
    // Not an error panel (that would blame Core), and not an empty result (that would
    // invent "there is nothing here").
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByText(/空:Checkpoint/)).toBeNull()
  })

  it('context pack marks the pending dependency', async () => {
    const user = userEvent.setup()
    await enter(user, `#/context/${PACK}`, {
      ...SIGNED_IN,
      ['GET /v1/context/' + PACK]: PROXY_REFUSAL,
    })
    const panel = await screen.findByRole('region', { name: /Context Pack/ })
    await waitFor(() => expect(within(panel).getByTestId('dependency-pending')).toBeTruthy())
    expect(panel.textContent).toContain('context:read')
    expect(within(panel).queryByRole('alert')).toBeNull()
  })

  it('resume marks the pending dependency and names both scopes', async () => {
    const user = userEvent.setup()
    await enter(user, `#/resume/${RESUME}`, {
      ...SIGNED_IN,
      ['GET /v1/resumes/' + RESUME]: PROXY_REFUSAL,
    })
    const panel = await screen.findByRole('region', { name: /Resume/ })
    await waitFor(() => expect(within(panel).getByTestId('dependency-pending')).toBeTruthy())
    expect(panel.textContent).toContain('resume:read + checkpoint:read')
    expect(within(panel).queryByRole('alert')).toBeNull()
  })
})

describe('a real P3 read renders the record it was given', () => {
  it('shows a checkpoint as an immutable snapshot with its three digests', async () => {
    const user = userEvent.setup()
    await enter(user, `#/checkpoints?checkpoint_id=${CHECKPOINT}`, {
      ...SIGNED_IN,
      ['GET /v1/checkpoints/' + CHECKPOINT]: { body: { checkpoint: checkpoint() } },
    })
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(panel.textContent).toContain(CHECKPOINT))
    const text = panel.textContent ?? ''
    expect(text).toContain('不可变快照')
    expect(text).toContain('task_revision 7')
    // The digests are the point of the record, so they must be shown in full.
    expect(text).toContain('a'.repeat(64))
    expect(text).toContain('b'.repeat(64))
    expect(text).toContain('c'.repeat(64))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('renders "no checkpoint yet" as an empty result, not an error', async () => {
    const user = userEvent.setup()
    await enter(user, `#/checkpoints?task_id=${TASK}`, {
      ...SIGNED_IN,
      ['GET /v1/tasks/' + TASK + '/checkpoints/latest']: { body: { checkpoint: null } },
    })
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(panel.textContent).toContain('该 Task 目前没有 checkpoint'))
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('shows a resume note as historical and non-authoritative', async () => {
    const user = userEvent.setup()
    await enter(user, `#/resume/${RESUME}`, {
      ...SIGNED_IN,
      ['GET /v1/resumes/' + RESUME]: { body: { resume: resume() } },
    })
    const panel = await screen.findByRole('region', { name: /Resume/ })
    await waitFor(() => expect(within(panel).getByTestId('resume-note')).toBeTruthy())
    const note = within(panel).getByTestId('resume-note')
    expect(note.textContent).toContain('historical')
    expect(note.textContent).toContain('authority = false')
    // A resume is a comparison; it must not read as an applied restore.
    expect(panel.textContent).toContain('这是比较,不是恢复')
  })

  it('separates a genuine Core refusal from the missing dependency', async () => {
    // Core's own 403 is a permission refusal and must NOT be dressed up as "P3 is
    // missing" — the two mean completely different things to the operator.
    const user = userEvent.setup()
    await enter(user, `#/checkpoints?checkpoint_id=${CHECKPOINT}`, {
      ...SIGNED_IN,
      ['GET /v1/checkpoints/' + CHECKPOINT]: coreError(403, 'forbidden_scope', 'checkpoint:read is required'),
    })
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('forbidden_scope'))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })
})
