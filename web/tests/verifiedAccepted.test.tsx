/**
 * P6-02 item 5 — the VERIFIED/ACCEPTED slice, browser side.
 *
 * Frozen contract: `web/P6-02-ITEM5-VERIFIED-ACCEPTED-SPEC.md` rev 2. The write panels and their
 * error vocabulary are item 4's (`web/P6-02B-TASK-STEP-WRITE-SPEC.md`); this file adds the item-5
 * matrix and re-asserts the refusal contract the forms must not break.
 *
 * §8 negative coverage, and what cannot be driven from a browser:
 *
 * * **Browser-drivable and covered here:** 4(c) →VERIFIED is decided by the Evidence validator
 *   (a 422, not a 403) and the UI never claims an actor-kind refusal on that edge; 5 a stale
 *   `expected_revision` is 409 with the draft kept and exactly one POST; 6 (the refusal side) the
 *   `422 missing_evidence` renders BLOCKED with Core's message/details and the no-write copy;
 *   8 `INFO` is never rendered as `PASS`; 9 no confirm control exists (the proxy refusal itself
 *   is Core/proxy-side and lives in `taskWriteProxy.test.ts` / the p6 proxy contract suite).
 * * **Core-side / not browser-drivable, marked where the test touches them:** 1 and 2 (the
 *   `422` with an unchanged Step/Task, history `event_id` set and `max(seq)`), 3 (no ACCEPTED
 *   Step), 4(a) (issuing `state:accept` for an `agent` needs `POST /v1/auth/keys`, absent from
 *   the proxy), 6's SQLite half, and 7 (a Rule with no mapping). They need a real Core / SQLite.
 *
 * The unknown/request-id and replay panels are also already pinned in `taskWriteViews.test.tsx`;
 * two accept-specific cases are repeated here because §8 frames them per form.
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
const RULE_ID = `rul_${ID}`
const EVENT_ID = `evt_${ID}`
const HOST = { host_id: `hst_${ID}`, display_name: 'local', name: 'local' }
const AT = '2026-10-07T12:00:00Z'
const EVIDENCE_HASH = `#/task/${TASK_ID}/evidence`
const WRITE_HASH = `#/task/${TASK_ID}/write`

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
    status: 'EXECUTED',
    revision: 3,
    acceptance_criteria: { requirements: [] },
    source_event_id: EVENT_ID,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  }
}

function rawEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event_id: EVENT_ID,
    seq: 20,
    schema_version: 8,
    event_type: 'tool.result',
    source_system: 'codex-posttool',
    source_event_id: null,
    occurred_at: AT,
    recorded_at: AT,
    actor_id: 'act_1',
    actor_kind: 'system',
    host_id: HOST.host_id,
    session_id: null,
    project_id: PROJECT_ID,
    task_id: TASK_ID,
    payload: {},
    body_sha256: 'a'.repeat(64),
    ...overrides,
  }
}

function evidence(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    evidence_id: `evd_${ID}`,
    task_id: TASK_ID,
    step_id: STEP_ID,
    kind: 'TEST',
    status: 'PASS',
    source_event_id: 'evt_source',
    change_event_id: 'evt_change',
    fingerprint_sha256: 'b'.repeat(64),
    artifact_uri: null,
    artifact_sha256: null,
    actor_id: 'act_1',
    host_id: HOST.host_id,
    session_id: null,
    turn_id: null,
    tool_use_id: null,
    tool_name: null,
    command_sha256: null,
    producer_config_sha256: null,
    confirmed_rule_id: null,
    confirmed_rule_version: null,
    task_revision: 12,
    step_revision: 3,
    created_at: AT,
    related_posttool_event_id: null,
    result: {},
    reference_status: 'NONE',
    ...overrides,
  }
}

function rule(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    rule_id: RULE_ID,
    rule_key: 'fixture-rule',
    version: 2,
    revision: 2,
    status: 'ACTIVE',
    scope: { kind: 'project', project_id: PROJECT_ID, task_id: null },
    kind: 'ACCEPTANCE',
    severity: 'high',
    enforcement: 'VERIFY',
    content: '',
    matcher: {},
    verification_requirements: null,
    origin_event_id: EVENT_ID,
    origin_actor_kind: 'human',
    origin_event_type: 'rule.proposed',
    change_event_id: EVENT_ID,
    status_event_id: 'evt_activation',
    status_actor_kind: 'human',
    ...overrides,
  }
}

/** The source/change Events the matrix reads with R-EV. */
function eventRoutes(): Record<string, unknown> {
  return {
    'GET /v1/events/evt_source': { body: { event: rawEvent({ event_id: 'evt_source' }) } },
    'GET /v1/events/evt_change': {
      body: {
        event: rawEvent({ event_id: 'evt_change', event_type: 'evidence.recorded', seq: 20 }),
      },
    },
    'GET /v1/events/evt_activation': {
      body: {
        event: rawEvent({
          event_id: 'evt_activation',
          event_type: 'rule.approved',
          seq: 15,
          actor_kind: 'human',
        }),
      },
    },
  }
}

/** Routes for the Evidence tab's matrix. */
function matrixRoutes(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    'GET /v1/health': { body: HEALTH },
    'GET /v1/projects': { body: { projects: [], count: 0 } },
    [`GET /v1/tasks/${TASK_ID}`]: { body: { task: task() } },
    [`GET /v1/tasks/${TASK_ID}/steps`]: {
      body: {
        steps: [
          step({
            acceptance_criteria: {
              requirements: [{ key: 'build', kind: 'TEST', required_result: 'PASS' }],
            },
          }),
        ],
      },
    },
    [`GET /v1/tasks/${TASK_ID}/evidence`]: { body: { evidence: [evidence()], count: 1 } },
    [`GET /v1/tasks/${TASK_ID}/history`]: {
      body: {
        events: [
          rawEvent({
            event_id: 'evt_cycle',
            seq: 5,
            event_type: 'step.transitioned',
            actor_kind: 'human',
            payload: { step_id: STEP_ID, to: 'IN_PROGRESS' },
          }),
        ],
      },
    },
    'GET /v1/rules/active': { body: { rules: [], count: 0 } },
    ...eventRoutes(),
    ...overrides,
  }
}

/** Routes for the write tab (§8's write-side cases). */
function writeRoutes(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    'GET /v1/health': { body: HEALTH },
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
): Promise<void> {
  fake = installFakeCore(routes as Parameters<typeof installFakeCore>[0])
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

async function panel(name: string | RegExp): Promise<HTMLElement> {
  return screen.findByRole('region', { name })
}

async function clickSubmit(
  user: ReturnType<typeof userEvent.setup>,
  region: HTMLElement,
  reason?: string,
): Promise<void> {
  if (reason !== undefined) {
    await user.type(within(region).getByTestId('write-reason'), reason)
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

/* ========================================================================== *
 * §8.8 and §3.2 — the matrix display states, INFO, and the residual
 * ========================================================================== */

describe('the criteria↔Evidence matrix is a read aid, never a verdict', () => {
  it('shows a candidate with the Core 未判定 residual (F1/F2/F8/F9d/F9e), never "通过"', async () => {
    const user = userEvent.setup()
    await enter(user, EVIDENCE_HASH, matrixRoutes())

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const state = await within(matrix).findByTestId('matrix-state-build')
    expect(state.textContent).toContain('候选匹配')
    expect(state.textContent).toContain('Core 未判定')
    // The residual names the five unreadable `_fresh` conditions.
    const residual = within(matrix).getByTestId('matrix-residual')
    for (const condition of ['F1', 'F2', 'F8', 'F9d', 'F9e']) {
      expect(residual.textContent).toContain(condition)
    }
    expect(within(matrix).getByTestId('matrix-candidates-build').textContent).toContain(`evd_${ID}`)
    // The strongest claim is "candidate"; no sufficiency verdict is asserted. The residual's
    // "不等于通过" is the negation, so the check is for an affirmative verdict, not the word.
    expect(state.textContent).not.toContain('已通过')
    expect(state.textContent).not.toContain('可提交')
    expect(within(matrix).getByTestId('matrix-non-verdict').textContent).toContain('不是')
  })

  it('shows a stale row only when a readable fact proves it, with the fact named', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      EVIDENCE_HASH,
      matrixRoutes({
        [`GET /v1/tasks/${TASK_ID}/evidence`]: {
          body: { evidence: [evidence({ task_revision: 99 })], count: 1 },
        },
      }),
    )

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const state = await within(matrix).findByTestId('matrix-state-build')
    expect(state.textContent).toContain('不可用')
    const stale = within(matrix).getByTestId('matrix-stale-build')
    expect(stale.textContent).toContain('F3')
    expect(within(matrix).queryByTestId('matrix-candidates-build')).toBeNull()
  })

  it('shows missing and the wrong-kind rows separately', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      EVIDENCE_HASH,
      matrixRoutes({
        [`GET /v1/tasks/${TASK_ID}/evidence`]: {
          body: { evidence: [evidence({ kind: 'BUILD', status: 'PASS' })], count: 1 },
        },
      }),
    )

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const state = await within(matrix).findByTestId('matrix-state-build')
    expect(state.textContent).toContain('缺项')
    expect(state.textContent).toContain('requirement_key')
    const wrongKind = within(matrix).getByTestId('matrix-wrong-kind-build')
    expect(wrongKind.textContent).toContain(`evd_${ID}`)
    expect(within(matrix).queryByTestId('matrix-candidates-build')).toBeNull()
  })

  it('renders INFO as INFO and never uses it for a PASS requirement (§8.8)', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      EVIDENCE_HASH,
      matrixRoutes({
        [`GET /v1/tasks/${TASK_ID}/steps`]: {
          body: {
            steps: [
              step({
                acceptance_criteria: {
                  requirements: [
                    { key: 'confirm', kind: 'USER_CONFIRMATION', required_result: 'PASS' },
                  ],
                },
              }),
            ],
          },
        },
        [`GET /v1/tasks/${TASK_ID}/evidence`]: {
          body: {
            evidence: [
              evidence({
                kind: 'USER_CONFIRMATION',
                status: 'INFO',
                confirmed_rule_id: null,
                step_revision: 3,
              }),
            ],
            count: 1,
          },
        },
        'GET /v1/events/evt_source': {
          body: {
            event: rawEvent({
              event_id: 'evt_source',
              event_type: 'user.prompt',
              actor_kind: 'human',
              actor_id: 'act_1',
            }),
          },
        },
      }),
    )

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const state = await within(matrix).findByTestId('matrix-state-confirm')
    // `USER_CONFIRMATION` + `PASS` can never be satisfied (`record_confirmation` writes INFO).
    expect(state.textContent).toContain('缺项')
    // The only place the INFO row appears is the wrong-kind set, carrying its own badge.
    const wrongKind = within(matrix).getByTestId('matrix-wrong-kind-confirm')
    expect(within(wrongKind).getByText('INFO')).toBeTruthy()
    expect(within(wrongKind).getByTitle('INFO 不是 PASS')).toBeTruthy()
    expect(within(matrix).queryByTestId('matrix-candidates-confirm')).toBeNull()
  })

  it('lists the applicable Rule half and the reachable no-mapping branch (§3.3)', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      EVIDENCE_HASH,
      matrixRoutes({
        'GET /v1/rules/active': {
          body: {
            rules: [
              rule({ verification_requirements: null }),
              rule({
                rule_id: `rul_${'B'.repeat(26)}`,
                version: 1,
                verification_requirements: {
                  requirements: [{ key: 'lint', kind: 'BUILD', required_result: 'PASS' }],
                },
              }),
              // Not applicable: neither ACCEPTANCE nor VERIFY.
              rule({ rule_id: `rul_${'C'.repeat(26)}`, kind: 'POLICY', enforcement: 'ADVISORY' }),
            ],
            count: 3,
          },
        },
      }),
    )

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const rules = await within(matrix).findByTestId('matrix-rules')
    // The copy-of-WHERE-clause caveat is stated, not implied.
    expect(rules.textContent).toContain('复制')
    expect(rules.textContent).toContain('不是 Core 的回答')
    // Branch 5 (reachable, `{rule_id, version}`) is named with the exact key spelling.
    const noMapping = within(matrix).getByTestId(`matrix-rule-no-mapping-${RULE_ID}`)
    expect(noMapping.textContent).toContain(RULE_ID)
    expect(noMapping.textContent).toContain('version')
    // The mapped Rule's requirement is shown as a candidate row.
    expect(within(matrix).getByTestId('matrix-state-lint')).toBeTruthy()
    // The inapplicable Rule is filtered out by the copied predicate.
    expect(within(matrix).queryByTestId(`matrix-rule-rul_${'C'.repeat(26)}`)).toBeNull()
  })

  it('never claims the current fingerprint is current/一致 (§3.5 item 2)', async () => {
    const user = userEvent.setup()
    await enter(user, EVIDENCE_HASH, matrixRoutes())

    const matrix = await panel(/criteria → Evidence 参考矩阵/)
    const note = await within(matrix).findByTestId('matrix-fingerprint-note')
    expect(note.textContent).toContain('永不')
    expect(note.textContent).toContain('一致')
    expect(note.textContent).toContain('F1/F2')
    const rowNote = within(matrix).getAllByTestId('matrix-fingerprint')[0]
    expect(rowNote?.textContent).toContain('当前工作区 fingerprint')
  })
})

/* ========================================================================== *
 * §8.9 — no confirm affordance anywhere
 * ========================================================================== */

describe('the item-5 read forms offer no confirmation affordance', () => {
  it('has no control that could mint a USER_CONFIRMATION, and never POSTs confirm', async () => {
    const user = userEvent.setup()
    await enter(user, EVIDENCE_HASH, matrixRoutes())

    await panel(/criteria → Evidence 参考矩阵/)
    // The existing "未接入" panel is preserved (the browser cannot produce ADR 0007 provenance).
    expect(screen.getByTestId('unavailable')).toBeTruthy()
    expect(screen.getByText(/人工 Evidence confirmation/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /confirm|确认/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /confirm|确认/i })).toBeNull()
    expect(fake!.callsTo('POST', '/v1/evidence/confirm')).toHaveLength(0)
  })
})

/* ========================================================================== *
 * §8.6 — the Evidence-insufficient refusal, every readable `details` shape
 * ========================================================================== */

describe('a 422 missing_evidence renders BLOCKED with Core details and the no-write copy', () => {
  const cases: Array<{ label: string; details: Record<string, unknown>; wants: string[] }> = [
    {
      label: 'a direct requirement {requirement_key}',
      details: { requirement_key: 'build' },
      wants: ['requirement_key = build'],
    },
    {
      label: 'a rule requirement {requirement_key, rule_id, rule_version}',
      details: { requirement_key: 'lint', rule_id: RULE_ID, rule_version: 2 },
      wants: ['requirement_key = lint', `rule_id = ${RULE_ID}`, 'rule_version = 2'],
    },
    {
      label: 'a Rule with no mapping {rule_id, version}',
      details: { rule_id: RULE_ID, version: 4 },
      wants: [`rule_id = ${RULE_ID}`, 'version = 4'],
    },
    // §8.2's `{step_id}` / `{task_id}` branches (Core-side to reach; the rendering is here).
    {
      label: 'a Step with no requirements {step_id}',
      details: { step_id: STEP_ID },
      wants: [`step_id = ${STEP_ID}`],
    },
    {
      label: 'a Task with no requirements {task_id}',
      details: { task_id: TASK_ID },
      wants: [`task_id = ${TASK_ID}`],
    },
  ]

  for (const { label, details, wants } of cases) {
    it(`names the branch from ${label}`, async () => {
      const user = userEvent.setup()
      await enter(
        user,
        WRITE_HASH,
        writeRoutes({
          [`POST /v1/tasks/${TASK_ID}/accept`]: {
            status: 422,
            body: {
              error: {
                code: 'missing_evidence',
                message: 'Evidence requirement is not satisfied',
                details,
              },
            },
          },
        }),
      )
      const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
      await clickSubmit(user, region)

      const blocked = await within(region).findByTestId('write-evidence-blocked')
      expect(blocked.textContent).toContain('BLOCKED')
      expect(blocked.textContent).toContain('missing_evidence')
      // The branch is named from Core's own details.
      for (const want of wants) {
        expect(within(blocked).getByTestId('write-evidence-branch').textContent).toContain(want)
      }
      // The frozen no-write copy, and not the conflict block (a re-read cannot fix it).
      expect(blocked.textContent).toContain('没有发生写入')
      expect(within(region).queryByText(/冲突,已保留草稿/)).toBeNull()
      expect(within(region).queryByText(/^被拒绝/)).toBeNull()
      expect(within(region).queryByText(/已提交/)).toBeNull()
    })
  }

  it('says Core did not give a requirement_key when the branch has no details', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/tasks/${TASK_ID}/accept`]: {
          status: 422,
          body: {
            error: {
              code: 'missing_evidence',
              message: 'current human USER_CONFIRMATION is required',
              details: {},
            },
          },
        },
      }),
    )
    const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
    await clickSubmit(user, region)
    const blocked = await within(region).findByTestId('write-evidence-blocked')
    expect(within(blocked).getByTestId('write-evidence-branch').textContent).toContain(
      'Core 未给出 requirement_key',
    )
    expect(blocked.textContent).toContain('没有发生写入')
  })
})

/* ========================================================================== *
 * §8.5 / §8.4 / §4.1 — revision, replay, unknown and refusal on the forms
 * ========================================================================== */

describe('the VERIFIED/ACCEPTED submissions reuse the frozen item-4 panels', () => {
  it('a stale expected_revision on →VERIFIED is 409, keeps the draft and issues one POST', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/steps/${STEP_ID}/transition`]: {
          status: 409,
          body: {
            error: {
              code: 'revision_conflict',
              message: 'expected_revision does not match current revision',
              details: {
                expected_revision: 3,
                current_revision: 4,
                kind: 'step',
                current_snapshot: { step_id: STEP_ID, revision: 4 },
              },
            },
          },
        },
      }),
    )
    const region = await panel('Step 状态转换 (state:write)')
    await user.selectOptions(within(region).getByTestId('step-transition-status'), 'VERIFIED')
    await clickSubmit(user, region, 'STALE-DRAFT-MARKER')

    const conflict = await within(region).findByText(/冲突,已保留草稿/)
    expect(conflict.textContent).toContain('revision_conflict')
    expect(within(region).getByTestId('write-refusal-fields').textContent).toContain('current_revision')
    // The draft survives and the panel did not retry with the new revision.
    expect((within(region).getByTestId('write-reason') as HTMLInputElement).value).toBe(
      'STALE-DRAFT-MARKER',
    )
    const sent = postBody(`/v1/steps/${STEP_ID}/transition`)
    expect(sent['expected_revision']).toBe(3)
    expect(sent['status']).toBe('VERIFIED')
  })

  it('shows a replayed →VERIFIED response as the same write, not a new one', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/steps/${STEP_ID}/transition`]: {
          body: {
            step: step({ status: 'VERIFIED', revision: 4 }),
            task_revision: 13,
            evidence_ids: [`evd_${ID}`],
            rule_versions: [],
            event: { event_id: EVENT_ID, event_type: 'step.transitioned' },
            replayed: true,
          },
        },
      }),
    )
    const region = await panel('Step 状态转换 (state:write)')
    await user.selectOptions(within(region).getByTestId('step-transition-status'), 'VERIFIED')
    await clickSubmit(user, region)
    await waitFor(() =>
      expect(within(region).getByText(/replayed: Core 返回了原命令结果/)).toBeTruthy(),
    )
    expect(fake!.callsTo('POST', `/v1/steps/${STEP_ID}/transition`)).toHaveLength(1)
  })

  it('a timeout on Task accept is unknown with the request id, never success or failure', async () => {
    const user = userEvent.setup()
    fake = installFakeCore(
      writeRoutes() as Parameters<typeof installFakeCore>[0],
      (request) => {
        if (request.method === 'POST' && request.path === `/v1/tasks/${TASK_ID}/accept`) {
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
    render(
      <SessionProvider>
        <App />
      </SessionProvider>,
    )
    await user.type(screen.getByLabelText('Core API token'), 'tok')
    await user.click(screen.getByRole('button', { name: '使用此 token' }))
    await screen.findByText('Core 健康与 Schema')
    window.location.hash = WRITE_HASH
    window.dispatchEvent(new HashChangeEvent('hashchange'))

    const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
    await clickSubmit(user, region)
    const unknown = await within(region).findByTestId('write-unknown')
    expect(unknown.textContent).toContain('结果未知')
    const sent = fake!.callsTo('POST', `/v1/tasks/${TASK_ID}/accept`)[0]?.requestId
    expect(sent).toMatch(/^ui-/)
    expect(within(region).getByTestId('write-request-id').textContent).toContain(sent ?? '')
    expect(within(region).queryByText(/已提交/)).toBeNull()
    expect(within(region).queryByText(/被拒绝/)).toBeNull()
  })

  it('403 forbidden_scope on accept is its own refusal and offers no other credential', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/tasks/${TASK_ID}/accept`]: {
          status: 403,
          body: {
            error: {
              code: 'forbidden_scope',
              message: 'state:accept scope is required',
              details: { required: 'state:accept', granted: ['state:read', 'state:write'] },
            },
          },
        },
      }),
    )
    const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
    await clickSubmit(user, region)
    await within(region).findByText(/被拒绝/)
    expect(within(region).getByTestId('write-refusal-fields').textContent).toContain('state:accept')
    expect(within(region).getByText(/不会换更高权限的凭据重试/)).toBeTruthy()
    expect(within(region).queryByText(/已提交/)).toBeNull()
  })

  it('409 invalid_state_transition on →VERIFIED shows Core from/to as a refusal, not a conflict', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/steps/${STEP_ID}/transition`]: {
          status: 409,
          body: {
            error: {
              code: 'invalid_state_transition',
              message: 'invalid Step transition',
              details: { from_status: 'EXECUTED', to_status: 'VERIFIED' },
            },
          },
        },
      }),
    )
    const region = await panel('Step 状态转换 (state:write)')
    await user.selectOptions(within(region).getByTestId('step-transition-status'), 'VERIFIED')
    await clickSubmit(user, region)
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('invalid_state_transition')
    expect(within(region).queryByText(/冲突,已保留草稿/)).toBeNull()
  })

  /**
   * §8.4(b) — an agent key holding `state:write` that submits route 3→ACCEPTED is refused
   * `403 forbidden_scope` at `can_accept` (`state.py:334-335`). Browser-drivable: only the
   * rendering is asserted here; the "no Event, revision unchanged" equality half is Core-side.
   */
  it('route 3 →ACCEPTED without state:accept is a 403 forbidden_scope, not a conflict', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/steps/${STEP_ID}/transition`]: {
          status: 403,
          body: {
            error: {
              code: 'forbidden_scope',
              message: 'state:accept scope is required',
              details: { required: 'state:accept', granted: ['state:read', 'state:write'] },
            },
          },
        },
      }),
    )
    const region = await panel('Step 状态转换 (state:write)')
    await user.selectOptions(within(region).getByTestId('step-transition-status'), 'ACCEPTED')
    await clickSubmit(user, region)
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('forbidden_scope')
    expect(within(region).getByTestId('write-refusal-fields').textContent).toContain('state:accept')
    expect(within(region).queryByText(/已提交/)).toBeNull()
    expect(fake!.callsTo('POST', `/v1/steps/${STEP_ID}/transition`)).toHaveLength(1)
  })

  /**
   * §8.3 — a Task accept with no ACCEPTED Step answers `409 invalid_state_transition` whose
   * message is Core's and whose `details` are empty. The *rendering* is browser-drivable; the
   * "(status, revision) unchanged and no task.accepted Event" half needs a real Core.
   */
  it('route 6 with no ACCEPTED Step renders Core\'s 409 precondition, with no details', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/tasks/${TASK_ID}/accept`]: {
          status: 409,
          body: {
            error: {
              code: 'invalid_state_transition',
              message: 'Task needs an accepted Step and no unfinished Steps',
              details: {},
            },
          },
        },
      }),
    )
    const region = await panel('Task 验收 (state:accept,Evidence 门槛)')
    await clickSubmit(user, region)
    const refused = await within(region).findByText(/被拒绝/)
    expect(refused.textContent).toContain('invalid_state_transition')
    expect(region.textContent).toContain('Task needs an accepted Step and no unfinished Steps')
    // Not the Evidence branch, and no invented precondition fields: Core sent no details.
    expect(within(region).queryByTestId('write-evidence-blocked')).toBeNull()
    expect(within(region).queryByTestId('write-refusal-fields')).toBeNull()
  })

  /**
   * §8.4(c) — stated as what it is. Core gates →VERIFIED through the Evidence validator, not an
   * actor-kind check: an agent key holding `state:write` reaches it and gets `422`, not `403`.
   * The UI must not claim a server-side actor-kind refusal on this edge, and it must not hide the
   * action from a session it cannot identify (the dashboard cannot see its own actor kind, CR-7).
   * §8.4(a) — issuing `state:accept` for an agent — is Core-side and not browser-drivable.
   */
  it('does not claim an actor-kind refusal on →VERIFIED: it renders Core 422 as BLOCKED', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      WRITE_HASH,
      writeRoutes({
        [`POST /v1/steps/${STEP_ID}/transition`]: {
          status: 422,
          body: {
            error: {
              code: 'missing_evidence',
              message: 'Evidence requirement is not satisfied',
              details: { requirement_key: 'build' },
            },
          },
        },
      }),
    )
    const region = await panel('Step 状态转换 (state:write)')
    await user.selectOptions(within(region).getByTestId('step-transition-status'), 'VERIFIED')
    await clickSubmit(user, region)

    const blocked = await within(region).findByTestId('write-evidence-blocked')
    expect(blocked.textContent).toContain('missing_evidence')
    expect(blocked.textContent).not.toContain('forbidden_actor_kind')
    // The action is still offered; it is not hidden behind an actor-kind rule the UI cannot know.
    expect((within(region).getByTestId('step-transition-status') as HTMLSelectElement).value).toBe(
      'VERIFIED',
    )
    expect(region.textContent).not.toContain('forbidden_actor_kind')
    expect(region.textContent).not.toContain('agent credential 被拒绝')
  })
})
