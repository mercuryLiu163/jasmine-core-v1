/**
 * Task Evidence regressions found by the independent review.
 *
 * Two defects are pinned here:
 *
 * 1. Core's task acceptance is the `task.accepted` event (`state.py: accept_task`),
 *    not `task.transitioned`. The "判定依据" panel must surface the acceptance and
 *    its `evidence_ids`, otherwise a real `POST /v1/tasks/{id}/accept` leaves the
 *    panel empty.
 * 2. The criteria matrix reads `/v1/tasks/{id}/steps`; a refused steps read must
 *    render the refusal, never "没有 requirements 就没有矩阵".
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

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 6 }

function event(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    event_id: 'evt_x',
    seq: 1,
    schema_version: 6,
    event_type: 'step.transitioned',
    source_system: 'core-api',
    source_event_id: null,
    occurred_at: '2026-09-30T05:00:00Z',
    recorded_at: '2026-09-30T05:00:00Z',
    actor_id: 'act_1',
    actor_kind: 'human',
    host_id: 'hst_1',
    session_id: null,
    project_id: 'prj_1',
    task_id: 'tsk_1',
    payload: {},
    body_sha256: 'a'.repeat(64),
    ...overrides,
  }
}

async function openTaskEvidence(
  user: ReturnType<typeof userEvent.setup>,
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
  window.location.hash = '#/task/tsk_1/evidence'
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

describe('TaskEvidence', () => {
  it('shows the evidence_ids Core recorded on a task.accepted acceptance', async () => {
    const user = userEvent.setup()
    await openTaskEvidence(user, {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/tasks/tsk_1/evidence': {
        body: {
          evidence: [
            {
              evidence_id: 'evd_accept',
              task_id: 'tsk_1',
              step_id: null,
              kind: 'USER_CONFIRMATION',
              status: 'PASS',
              source_event_id: 'evt_src',
              change_event_id: 'evt_chg',
              fingerprint_sha256: null,
              artifact_uri: null,
              artifact_sha256: null,
              actor_id: 'act_1',
              host_id: 'hst_1',
              session_id: null,
              turn_id: null,
              tool_use_id: null,
              tool_name: null,
              command_sha256: null,
              producer_config_sha256: null,
              confirmed_rule_id: null,
              confirmed_rule_version: null,
              task_revision: 9,
              step_revision: null,
              created_at: '2026-09-30T05:00:00Z',
              related_posttool_event_id: null,
              result: {},
              reference_status: 'NONE',
            },
          ],
          count: 1,
        },
      },
      'GET /v1/tasks/tsk_1/history': {
        body: {
          events: [
            event({
              event_id: 'evt_accept',
              event_type: 'task.accepted',
              payload: {
                expected_revision: 8,
                evidence_ids: ['evd_accept'],
                rule_versions: [],
                result: {
                  task: { status: 'ACCEPTED' },
                  evidence_ids: ['evd_accept'],
                  rule_versions: [],
                },
              },
            }),
          ],
        },
      },
      'GET /v1/tasks/tsk_1/steps': { body: { steps: [] } },
    })

    const panel = await screen.findByRole('region', { name: /Core 记录的判定依据/ })
    expect(panel.textContent).toContain('1 次 VERIFIED/ACCEPTED')
    expect(within(panel).getByText('evd_accept')).toBeTruthy()
    expect(panel.textContent).toContain('ACCEPTED')
    // The empty-state wording must not be used when a record exists.
    expect(panel.textContent).not.toContain('没有 VERIFIED/ACCEPTED 记录')
  })

  it('renders a refused steps read instead of "no requirements"', async () => {
    const user = userEvent.setup()
    await openTaskEvidence(user, {
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/tasks/tsk_1/evidence': { body: { evidence: [], count: 0 } },
      'GET /v1/tasks/tsk_1/history': { body: { events: [] } },
      'GET /v1/tasks/tsk_1/steps': coreError(403, 'forbidden_scope', 'state:read is required'),
    })

    const matrix = await screen.findByRole('region', { name: /criteria → Evidence 参考矩阵/ })
    await waitFor(() => expect(matrix.textContent).toContain('forbidden_scope'))
    expect(matrix.textContent).toContain('缺少 scope')
    expect(matrix.textContent).not.toContain('没有 requirements 就没有矩阵')
  })
})
