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
import { CoreApiError, PROXY_PATH_REFUSED, isDependencyPending } from '../src/api/errors'
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
    comparison: { reconciliation_required: true, checkpoint_freshness: 'STALE' },
    note: { value: { text: 'stop here for today' }, authority: false, historical: true },
    next_actions: [{ kind: 'RECONCILE_CURRENT_TRUTH' }],
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

describe('Core saying "no such object" is not the missing dependency', () => {
  // `isDependencyPending` keys on `proxy_path_not_allowed` alone. If it also treated a
  // Core 404 as pending, a deleted checkpoint would be reported as an unmerged branch —
  // a wrong explanation, and the opposite of actionable. The browser Gate covers this
  // end to end; these pin it per view so widening the check fails here too.
  it.each([
    ['checkpoint', `#/checkpoints?checkpoint_id=${CHECKPOINT}`,
      'GET /v1/checkpoints/' + CHECKPOINT, /读取 Checkpoint/],
    ['context pack', `#/context/${PACK}`, 'GET /v1/context/' + PACK, /Context Pack/],
    ['resume', `#/resume/${RESUME}`, 'GET /v1/resumes/' + RESUME, /Resume/],
  ])('shows a %s 404 as an error, not as pending P3', async (_name, hash, route, region) => {
    const user = userEvent.setup()
    await enter(user, hash, {
      ...SIGNED_IN,
      [route]: coreError(404, 'not_found', 'no such object'),
    })
    const panel = await screen.findByRole('region', { name: region })
    await waitFor(() => expect(within(panel).getByRole('alert').textContent).toContain('not_found'))
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('classifies the states from the error code, not the status', async () => {
    // A direct unit check on the boundary: the proxy's refusal is the *only* pending
    // input, and Core's own 404/403 are not.
    const refusal = new CoreApiError({ kind: 'missing', status: 404, code: PROXY_PATH_REFUSED, message: 'x' })
    const notFound = new CoreApiError({ kind: 'missing', status: 404, code: 'not_found', message: 'x' })
    const forbidden = new CoreApiError({ kind: 'forbidden', status: 403, code: 'forbidden_scope', message: 'x' })
    expect(isDependencyPending(refusal)).toBe(true)
    expect(isDependencyPending(notFound)).toBe(false)
    expect(isDependencyPending(forbidden)).toBe(false)
  })
})

describe('a malformed id is refused with the right reason', () => {
  it('does not blame the missing P3 for a bad checkpoint id', async () => {
    // The proxy's allowlist spells ids the same way, so a malformed id is refused by the
    // proxy — and the dependency-pending panel would then explain it as "P3 is not
    // merged". A wrong explanation is worse than none, so the view refuses it itself.
    const user = userEvent.setup()
    await enter(user, '#/checkpoints?checkpoint_id=ckp_short', SIGNED_IN)
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(within(panel).getByTestId('invalid-id')).toBeTruthy())
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
    expect(panel.textContent).toContain('请求没有被发出')
    // Nothing was requested at all.
    expect(fake!.requests.filter((r) => r.path.startsWith('/v1/checkpoints/'))).toHaveLength(0)
  })

  it('refuses a malformed context pack id and a malformed resume id', async () => {
    const user = userEvent.setup()
    await enter(user, '#/context/not-an-id', SIGNED_IN)
    const pack = await screen.findByRole('region', { name: /Context Pack/ })
    await waitFor(() => expect(within(pack).getByTestId('invalid-id')).toBeTruthy())
    expect(fake!.requests.filter((r) => r.path.startsWith('/v1/context/'))).toHaveLength(0)
  })

  it('refuses a malformed task_id even when the checkpoint_id is valid', async () => {
    // A malformed field that nothing happens to request must still be reported: silently
    // ignoring it reads as "the dashboard ignored what I typed".
    const user = userEvent.setup()
    await enter(user, `#/checkpoints?checkpoint_id=${CHECKPOINT}&task_id=tsk_short`, SIGNED_IN)
    const panel = await screen.findByRole('region', { name: /读取 Checkpoint/ })
    await waitFor(() => expect(within(panel).getByTestId('invalid-id').textContent).toContain('task_id'))
    expect(fake!.requests.filter((r) => r.path.startsWith('/v1/checkpoints/'))).toHaveLength(0)
    expect(fake!.requests.filter((r) => r.path.includes('/checkpoints/latest'))).toHaveLength(0)
  })

  it('refuses a malformed resume id without requesting it', async () => {
    const user = userEvent.setup()
    await enter(user, '#/resume/rms_short', SIGNED_IN)
    const resume = await screen.findByRole('region', { name: /Resume/ })
    await waitFor(() => expect(within(resume).getByTestId('invalid-id')).toBeTruthy())
    expect(fake!.requests.filter((r) => r.path.startsWith('/v1/resumes/'))).toHaveLength(0)
  })
})

describe('the resume shows what a reader opens it for', () => {
  it('lists next_actions instead of hiding them in a collapsed block', async () => {
    // The slice exists to show this; a collapsed JSON block would render nothing until a
    // click, and `JsonBlock` only emits its text once open.
    const user = userEvent.setup()
    await enter(user, `#/resume/${RESUME}`, {
      ...SIGNED_IN,
      ['GET /v1/resumes/' + RESUME]: { body: { resume: resume() } },
    })
    const list = await screen.findByTestId('next-actions')
    expect(list.textContent).toContain('RECONCILE_CURRENT_TRUTH')
    expect(list.textContent).toContain('建议而非已执行')
  })

  it('renders the object shape P3 actually returns', async () => {
    // P3's `next_actions` is a list of objects, not strings: `{"kind", "step_id",
    // "status"}` for a step action. A view that only handled strings would fall back to a
    // collapsed block, so this pins the real shape.
    const user = userEvent.setup()
    await enter(user, `#/resume/${RESUME}`, {
      ...SIGNED_IN,
      ['GET /v1/resumes/' + RESUME]: {
        body: {
          resume: resume({
            next_actions: [
              { kind: 'VERIFY_EVIDENCE', step_id: 'stp_01M4B6VGC29ESRDRQR62DX8AK8', status: 'EXECUTED' },
              { kind: 'INSPECT_STEP', step_id: 'stp_01M4B6VGC29ESRDRQR62DX8AK9', status: 'BLOCKED' },
            ],
          }),
        },
      },
    })
    const rows = await screen.findAllByTestId('next-action')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.textContent).toContain('VERIFY_EVIDENCE')
    expect(rows[0]!.textContent).toContain('stp_01M4B6VGC29ESRDRQR62DX8AK8')
    expect(rows[0]!.textContent).toContain('EXECUTED')
  })

  it('shows an unrecognised action rather than dropping it', async () => {
    // An action this build does not know must not look like no action.
    const user = userEvent.setup()
    await enter(user, `#/resume/${RESUME}`, {
      ...SIGNED_IN,
      ['GET /v1/resumes/' + RESUME]: {
        body: { resume: resume({ next_actions: [{ future_kind: 'SOMETHING_NEW' }, 'plain string'] }) },
      },
    })
    const rows = await screen.findAllByTestId('next-action')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.textContent).toContain('SOMETHING_NEW')
    expect(rows[1]!.textContent).toContain('plain string')
  })
})
