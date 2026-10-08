/**
 * P6-03b2 write behaviour in the Memory views.
 *
 * Every test here pins one way the write surface could lie about what happened:
 *
 * 1. **The payload is the frozen contract's, key for key.** `event_id` is the replay key; a
 *    `reason` on a retry would be an unknown field Core refuses; the lifecycle commands must
 *    carry the revision the operator read.
 * 2. **A refusal is Core's, not the UI's.** A missing scope, a non-human actor
 *    (`ForbiddenActorKind`) and a `DONE` job (`invalid_state`) are rendered from Core's answer;
 *    the panel offers no alternative credential and no UI-side pretence that it decided.
 * 3. **A conflict keeps the draft and shows Core's current value**, with no automatic retry of
 *    the original intent — asserted by watching that no second POST is issued.
 * 4. **A replay is shown as a replay** and a fresh transition is not: `replayed: true` and the
 *    5-key shape say so, and `before`/`after` are only rendered when Core sent them.
 * 5. **The write is read back, not assumed.** After a success the object is re-read, and the
 *    readback block shows the freshly read revision with its read time.
 * 6. **A default-off proxy refusal says so**, naming the switch — it is not Core refusing the
 *    key, and the generic "缺 scope" banner would be a lie about which hop refused.
 * 7. **reflect is an explicit request with Core's own non-authoritative marker**, and a
 *    withheld engine text is not silently rendered as an empty success.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import contract from '../contracts/p4-memory-read.v1.json'
import writeContract from '../contracts/p4-memory-write.v1.json'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

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
const PROJECT = `prj_${ID}`
const TASK = `tsk_${ID}`
const EVENT = `evt_${ID}`
const HOST = { host_id: `hst_${ID}`, display_name: 'local', name: 'local' }

function withKeys<T extends object>(keys: readonly string[], overrides: Partial<T> = {}): T {
  return { ...Object.fromEntries(keys.map((key) => [key, null])), ...overrides } as T
}

function job(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.job, {
    job_id: JOB, kind: 'retain', memory_id: ITEM, source_event_id: EVENT,
    project_id: PROJECT, task_id: TASK, memory_type: 'VERIFIED_OUTCOME',
    bank_id: 'bank_x', document_id: 'doc_x', content_sha256: 'a'.repeat(64),
    status: 'FAILED', retry_count: 1, max_attempts: 3, next_attempt_at: '2026-10-07T12:00:00Z',
    claim_generation: 1, created_at: '2026-10-07T12:00:00Z', updated_at: '2026-10-07T12:01:00Z',
    last_error_code: 'engine_unavailable',
    safe_error_summary: 'fixture: the engine refused (simulated text)',
    ...overrides,
  })
}

function item(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.item, {
    memory_id: ITEM, bank_id: 'bank_x', scope_kind: 'project', project_id: PROJECT,
    task_id: TASK, memory_type: 'VERIFIED_OUTCOME', document_id: 'doc_x',
    source_event_id: EVENT, evidence_ids: [], content_sha256: 'b'.repeat(64),
    sanitized_text: 'fixture memory text', status: 'ACTIVE', pinned: false, revision: 7,
    engine_state: 'RETAINED', recall_count: 3, created_at: '2026-10-07T12:00:00Z',
    updated_at: '2026-10-07T12:02:00Z',
    ...overrides,
  })
}

function lifecycle(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return withKeys(contract.nested_fields.lifecycle, {
    status: 'ACTIVE', recallable: true, pinned: false, pin_affects: 'inspector_ordering_only',
    engine_state: 'RETAINED', remote_document_deleted: false, recall_count: 3, ...overrides,
  })
}

const ITEM_DETAIL = {
  memory: item(), sources: [], jobs: [], recallable: true, lifecycle: lifecycle(),
  engine_claim: 'x', non_authoritative: true,
}
const JOB_DETAIL = { job: job(), attempts: [] }

/** A 7-key fresh lifecycle reply, as P4 records it. */
function freshLifecycle(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    memory: item({ revision: 8, pinned: true }),
    event: { event_id: EVENT, event_type: 'memory.pinned' },
    replayed: false,
    job: job(),
    before: { revision: 7, pinned: false },
    after: { revision: 8, pinned: true },
    non_authoritative: true,
    ...overrides,
  }
}

/** The 5-key replay: the same command, minus `before`/`after`. */
function replayedLifecycle(): Record<string, unknown> {
  const full = freshLifecycle()
  return {
    memory: full.memory, event: full.event, replayed: true, job: full.job,
    non_authoritative: full.non_authoritative,
  }
}

const REFLECT = {
  status: 'ok',
  text: '模拟引擎的综合反思文本',
  banks: ['bank_x'],
  based_on_memory_ids: [ITEM],
  trace_id: `mtr_${ID}`,
  error_code: null,
  safe_error_summary: null,
  non_authoritative: true,
}

async function enter(
  user: ReturnType<typeof userEvent.setup>,
  hash: string,
  routes: Parameters<typeof installFakeCore>[0],
  handler?: Parameters<typeof installFakeCore>[1],
): Promise<void> {
  fake = installFakeCore(
    {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/hosts': { body: { hosts: [HOST] } },
      'GET /v1/projects': { body: { projects: [{ project_id: PROJECT, name: 'p', host_id: HOST.host_id }], count: 1 } },
      ...routes,
    },
    handler,
  )
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

/** The one write path a test expects, from the frozen contract rather than a literal. */
function contractPath(suffix: string): string {
  const write = writeContract.writes.find((entry) => entry.path_pattern.includes(suffix))
  if (!write) throw new Error(`no frozen write route matching ${suffix}`)
  // The route's own `id_prefix`, not a bare id: without it the path is
  // `/v1/memory/items/<id>/pin` instead of `.../mem_<id>/pin`, no fake route matches, and the
  // assertion fails against a panel that is behaving correctly. The previous version of this
  // helper had no caller that checked, so the mistake was invisible.
  const prefix = write.id_prefix ?? ''
  return write.path_pattern
    .replace(/^\^/, '')
    .replace(/\$$/, '')
    .replace(/\(\[\^\/\]\+\)/g, prefix ? `${prefix}_${ID}` : ID)
}

describe('the lifecycle panel sends the contract body and reads the object back', () => {
  it('submits the read revision, the host, an optional reason and the replay event_id', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/pin`]: { body: freshLifecycle() },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(within(panel).getByTestId('write-submit')).toBeTruthy())

    await user.selectOptions(within(panel).getByTestId('lifecycle-command'), 'pin')
    await user.type(within(panel).getByTestId('write-reason'), '固定这条结论')
    await user.click(within(panel).getByTestId('write-submit'))

    await waitFor(() => expect(fake!.callsTo('POST', `/v1/memory/items/${ITEM}/pin`)).toHaveLength(1))
    const post = fake!.callsTo('POST', `/v1/memory/items/${ITEM}/pin`)[0]!
    // Exactly the frozen allow-list for a lifecycle command: nothing more, nothing less.
    expect(Object.keys(post.body ?? {}).sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id', 'reason'])
    expect(post.body?.expected_revision).toBe(7)
    expect(post.body?.host_id).toBe(HOST.host_id)
    expect(post.body?.reason).toBe('固定这条结论')
    // P4 replays on `event_id`, and it is a client-minted `evt_` id — never `idempotency_key`.
    expect(String(post.body?.event_id)).toMatch(/^evt_/)
    expect(post.body).not.toHaveProperty('idempotency_key')
  })

  it('renders a fresh transition with before/after from Core own response', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/obsolete`]: {
        body: freshLifecycle({ event: { event_id: EVENT, event_type: 'memory.obsoleted' } }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.selectOptions(await within(panel).findByTestId('lifecycle-command'), 'obsolete')
    await user.click(within(panel).getByTestId('write-submit'))
    await waitFor(() => expect(within(panel).getByText(/已提交/)).toBeTruthy())
    // The 7-key shape: Core's `before`/`after` are rendered because Core sent them.
    expect(panel.textContent).toContain('before')
    expect(panel.textContent).toContain('after')
    expect(panel.textContent).not.toContain('replayed: Core 返回了原命令结果')
  })

  it('renders a replay as a replay, with Core 5-key shape and no invented before/after', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/obsolete`]: { body: replayedLifecycle() },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.selectOptions(await within(panel).findByTestId('lifecycle-command'), 'obsolete')
    await user.click(within(panel).getByTestId('write-submit'))
    await waitFor(() => expect(within(panel).getByText(/replayed: Core 返回了原命令结果/)).toBeTruthy())
    // A 5-key response must not make the panel claim a fresh transition happened.
    expect(panel.textContent).not.toContain('"before"')
    expect(panel.textContent).not.toContain('"after"')
  })

  it('keeps the draft on a revision conflict, shows Core current value, and does not retry', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/pin`]: {
        status: 409,
        body: {
          error: {
            code: 'revision_conflict',
            message: 'expected_revision 7 does not match the current revision 9',
            details: { current_revision: 9, expected_revision: 7 },
          },
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.type(await within(panel).findByTestId('write-reason'), '固定')
    await user.click(within(panel).getByTestId('write-submit'))

    const conflict = await within(panel).findByText(/冲突,已保留草稿/)
    expect(conflict.textContent).toContain('revision_conflict')
    // Core's current value is shown beside the draft, and the draft (the reason) survives.
    expect(panel.textContent).toContain('current_revision')
    expect(panel.textContent).toContain('9')
    expect((within(panel).getByTestId('write-reason') as HTMLInputElement).value).toBe('固定')
    expect(panel.textContent).toContain('不会用新 revision 自动重试')

    // The load-bearing half: no automatic retry of the original intent.
    await new Promise((resolve) => setTimeout(resolve, 250))
    expect(fake!.callsTo('POST', `/v1/memory/items/${ITEM}/pin`)).toHaveLength(1)
  })

  it('renders Core forbidding the actor kind, without offering another credential', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/pin`]: {
        status: 403,
        body: {
          error: {
            code: 'forbidden_actor_kind',
            message: 'memory:manage requires a human or system actor; this actor is agent',
            details: { actor_kind: 'agent', required: 'human|system' },
          },
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.click(await within(panel).findByTestId('write-submit'))
    const refused = await within(panel).findByText(/被拒绝/)
    expect(refused.textContent).toContain('forbidden_actor_kind')
    expect(panel.textContent).toContain('human or system actor')
    // No success, no alternative credential.
    expect(panel.textContent).not.toContain('已提交')
    expect(panel.textContent).toContain('不会换更高权限的凭据重试')
  })

  it('renders a missing scope as Core forbidding_scope, naming the scope', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/pin`]: {
        status: 403,
        body: {
          error: {
            code: 'forbidden_scope',
            message: 'this key does not carry the memory:manage scope',
            details: { required: 'memory:manage', granted: ['memory:read'] },
          },
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.click(await within(panel).findByTestId('write-submit'))
    await within(panel).findByText(/被拒绝/)
    expect(panel.textContent).toContain('forbidden_scope')
    expect(panel.textContent).toContain('memory:manage')
    expect(panel.textContent).not.toContain('已提交')
  })

  it('shows the default-off proxy refusal as a missing write dependency, not as a scope problem', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/items/${ITEM}`, {
      [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
      [`POST /v1/memory/items/${ITEM}/pin`]: {
        status: 404,
        body: {
          error: {
            code: 'proxy_path_not_allowed',
            message: 'the dashboard proxy does not forward POST /v1/memory/items/x/pin',
          },
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await user.click(await within(panel).findByTestId('write-submit'))
    const pending = await within(panel).findByTestId('write-dependency-pending')
    expect(pending.textContent).toContain('proxy_path_not_allowed')
    expect(pending.textContent).toContain('P6_ENABLE_P4_WRITES')
    // The generic refusal banner claims the operator's key lacks a scope. That would be a
    // different, false statement here: the proxy never forwarded the request.
    expect(within(panel).queryByText(/不会换更高权限的凭据重试/)).toBeNull()
  })

  it('re-reads the object after a successful write and shows the fresh revision with its read time', async () => {
    const user = userEvent.setup()
    let reads = 0
    await enter(
      user,
      `#/memory/items/${ITEM}`,
      {},
      (request) => {
        if (request.method === 'GET' && request.path === `/v1/memory/items/${ITEM}`) {
          reads += 1
          // The second read is the readback: the item really moved, so the block cannot be
          // showing the write response's copy by accident.
          return { body: { ...ITEM_DETAIL, memory: item({ revision: reads === 1 ? 7 : 8, pinned: reads > 1 }) } }
        }
        if (request.method === 'POST' && request.path === `/v1/memory/items/${ITEM}/pin`) {
          return { body: freshLifecycle() }
        }
        if (request.method === 'GET' && request.path === '/v1/health') return { body: HEALTH }
        if (request.method === 'GET' && request.path === '/v1/hosts') return { body: { hosts: [HOST] } }
        if (request.method === 'GET' && request.path === '/v1/projects') {
          return { body: { projects: [{ project_id: PROJECT, name: 'p', host_id: HOST.host_id }], count: 1 } }
        }
        return undefined
      },
    )
    const panel = await screen.findByRole('region', { name: /Memory Item/ })
    await waitFor(() => expect(panel.textContent).toContain('r7'))
    await user.click(await within(panel).findByTestId('write-submit'))
    await waitFor(() => expect(within(panel).getByText(/已提交/)).toBeTruthy())

    // Two reload buttons are in scope: the write panel's own readback button and the
    // Memory panel's. Both call the same resource reload, so either is the real thing.
    const reloadButtons = within(panel).getAllByRole('button', { name: '从 Core 重新读取' })
    expect(reloadButtons.length).toBeGreaterThan(0)
    await user.click(reloadButtons[0]!)
    await waitFor(() => expect(reads).toBeGreaterThan(1))
    const readback = await within(panel).findByTestId('write-readback')
    expect(readback.textContent).toContain('r8')
    expect(readback.textContent).toContain('读回时间')
    expect(reads).toBeGreaterThan(1)
  })
})

describe('the retry panel', () => {
  it('sends only the fields the retry contract allows and offers no reason input', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/jobs/${JOB}`, {
      [`GET /v1/memory/jobs/${JOB}`]: { body: JOB_DETAIL },
      [`POST /v1/memory/jobs/${JOB}/retry`]: {
        body: { job: job({ status: 'PENDING', retry_count: 2 }), event: { event_id: EVENT }, replayed: false },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Job/ })
    // P4's retry body has no `reason`, so the panel must not offer one.
    expect(within(panel).queryByTestId('write-reason')).toBeNull()
    await user.click(await within(panel).findByTestId('write-submit'))
    await waitFor(() => expect(fake!.callsTo('POST', `/v1/memory/jobs/${JOB}/retry`)).toHaveLength(1))
    const post = fake!.callsTo('POST', `/v1/memory/jobs/${JOB}/retry`)[0]!
    expect(Object.keys(post.body ?? {}).sort()).toEqual(['event_id', 'host_id'])
    expect(post.body?.host_id).toBe(HOST.host_id)
  })

  it('offers retry on a DONE job rather than pretending to know P4 invariant, and shows Core refusal', async () => {
    const user = userEvent.setup()
    await enter(user, `#/memory/jobs/${JOB}`, {
      [`GET /v1/memory/jobs/${JOB}`]: { body: { job: job({ status: 'DONE' }), attempts: [] } },
      [`POST /v1/memory/jobs/${JOB}/retry`]: {
        status: 409,
        body: {
          error: {
            code: 'invalid_state_transition',
            message: 'a DONE job is refused rather than retained twice',
            details: { status: 'DONE' },
          },
        },
      },
    })
    const panel = await screen.findByRole('region', { name: /Memory Job/ })
    // The button is offered: the invariant is Core's, and the refusal is the evidence.
    const submit = await within(panel).findByTestId('write-submit')
    expect(panel.textContent).toContain('DONE')
    await user.click(submit)
    // A state refusal is a *refusal*, not a conflict to re-review: re-reading a DONE job will
    // not make it retryable, so the conflict block ("Core's current value is beside your draft,
    // review and decide") would invite a retry that cannot succeed.
    const refused = await within(panel).findByText(/被拒绝/)
    expect(refused.textContent).toContain('invalid_state_transition')
    expect(panel.textContent).not.toContain('已提交')
    expect(panel.textContent).not.toContain('冲突,已保留草稿')
  })
})

describe('the reflect view', () => {
  it('cannot submit without a query and sends exactly the reflect contract body', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/reflect', {
      'POST /v1/memory/reflect': { body: REFLECT },
    })
    const region = await screen.findByRole('region', { name: /Memory reflect/ })
    expect(region.textContent).toContain('显式的用户请求')

    // No query, no submit: reflect is an explicit request, not a default.
    const submit = within(region).getByTestId('write-submit')
    expect((submit as HTMLButtonElement).disabled).toBe(true)

    await user.type(within(region).getByTestId('reflect-query'), '这个项目接下来该做什么?')
    await user.type(within(region).getByTestId('reflect-max-tokens'), '512')
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false))
    await user.click(submit)

    await waitFor(() => expect(fake!.callsTo('POST', '/v1/memory/reflect')).toHaveLength(1))
    const post = fake!.callsTo('POST', '/v1/memory/reflect')[0]!
    expect(Object.keys(post.body ?? {}).sort()).toEqual(
      ['event_id', 'host_id', 'max_tokens', 'project_id', 'query'])
    expect(post.body?.project_id).toBe(PROJECT)
    expect(post.body?.query).toBe('这个项目接下来该做什么?')
    expect(post.body?.max_tokens).toBe(512)
    // Not an unknown field: the contract does not list it, and P4 refuses extras.
    expect(post.body).not.toHaveProperty('explicit_user_request')
    expect(post.body).not.toHaveProperty('reason')
  })

  it('marks the output non-authoritative from Core own field and shows the trace', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/reflect', { 'POST /v1/memory/reflect': { body: REFLECT } })
    const region = await screen.findByRole('region', { name: /Memory reflect/ })
    await user.type(within(region).getByTestId('reflect-query'), '反思一下')
    await user.click(within(region).getByTestId('write-submit'))
    const outcome = await within(region).findByTestId('reflect-outcome')
    expect(within(outcome).getByTestId('non-authoritative')).toBeTruthy()
    expect(outcome.textContent).toContain(REFLECT.text)
    expect(outcome.textContent).toContain(REFLECT.trace_id)
  })

  it('does not render a withheld engine text as an empty success', async () => {
    const user = userEvent.setup()
    // P4 nulls `safe_error_summary` for a non-human actor while keeping the key.
    await enter(user, '#/memory/reflect', {
      'POST /v1/memory/reflect': {
        body: { ...REFLECT, text: null, status: 'failed', error_code: 'engine_error' },
      },
    })
    const region = await screen.findByRole('region', { name: /Memory reflect/ })
    await user.type(within(region).getByTestId('reflect-query'), '反思一下')
    await user.click(within(region).getByTestId('write-submit'))
    const outcome = await within(region).findByTestId('reflect-outcome')
    expect(outcome.textContent).toContain('engine_error')
    // The withheld state is stated, not papered over with a blank.
    expect(within(outcome).getByTestId('reflect-withheld')).toBeTruthy()
    expect(within(outcome).getByTestId('reflect-no-text')).toBeTruthy()
    expect(within(outcome).queryByTestId('non-authoritative')).toBeTruthy()
  })
})

describe('the write surface is a deep link, not a navigation entry', () => {
  it('offers no navigation link to the reflect route', async () => {
    const user = userEvent.setup()
    await enter(user, '#/memory/reflect', { 'POST /v1/memory/reflect': { body: REFLECT } })
    await screen.findByRole('region', { name: /Memory reflect/ })
    const nav = document.querySelector('nav')
    expect(nav?.textContent ?? '').not.toContain('reflect')
  })

  /**
   * The old version of this test rewrote each contract pattern into a literal and asserted that
   * literal matched the contract again — the contract checking itself. Deleting every panel left
   * it green, which review caught. This version drives the **panels**: each of the four lifecycle
   * commands is submitted from the item view, and the retry and reflect panels are submitted
   * from theirs, so every one of the six frozen routes is shown being reached by the UI.
   *
   * The paths are taken from the frozen contract (a literal for each route), not from the
   * panels' own strings, so a panel pointing somewhere else fails here.
   */
  it.each(['pin', 'unpin', 'obsolete', 'delete'] as const)(
    'the lifecycle panel reaches the frozen %s route', async (command) => {
      const user = userEvent.setup()
      const path = contractPath(`/${command}`)
      await enter(user, `#/memory/items/${ITEM}`, {
        [`GET /v1/memory/items/${ITEM}`]: { body: ITEM_DETAIL },
        [`POST ${path}`]: { body: freshLifecycle() },
      })
      const panel = await screen.findByRole('region', { name: /Memory Item/ })
      await user.selectOptions(await within(panel).findByTestId('lifecycle-command'), command)
      await user.click(within(panel).getByTestId('write-submit'))
      await waitFor(() => expect(fake!.callsTo('POST', path)).toHaveLength(1))
    })

  it('the retry panel reaches the frozen retry route', async () => {
    const user = userEvent.setup()
    const retry = contractPath('retry')
    await enter(user, `#/memory/jobs/${JOB}`, {
      [`GET /v1/memory/jobs/${JOB}`]: { body: JOB_DETAIL },
      [`POST ${retry}`]: { body: { job: job(), event: { event_id: EVENT }, replayed: false } },
    })
    const jobPanel = await screen.findByRole('region', { name: /Memory Job/ })
    await user.click(await within(jobPanel).findByTestId('write-submit'))
    await waitFor(() => expect(fake!.callsTo('POST', retry)).toHaveLength(1))
  })

  it('the reflect panel reaches the frozen reflect route', async () => {
    const user = userEvent.setup()
    const reflect = contractPath('reflect')
    await enter(user, '#/memory/reflect', { [`POST ${reflect}`]: { body: REFLECT } })
    const reflectPanel = await screen.findByRole('region', { name: /Memory reflect/ })
    await user.type(within(reflectPanel).getByTestId('reflect-query'), '反思一下')
    await user.click(within(reflectPanel).getByTestId('write-submit'))
    await waitFor(() => expect(fake!.callsTo('POST', reflect)).toHaveLength(1))
  })

  it('has a panel for every frozen write route and no seventh route in the contract', () => {
    // The reverse direction: the four lifecycle routes share one panel, so the contract must
    // still record exactly six routes and three proxy patterns — a seventh write in the frozen
    // contract would need a panel and a proxy pattern this suite does not cover.
    expect(writeContract.writes).toHaveLength(6)
    expect(new Set(writeContract.writes.map((write) => write.method))).toEqual(new Set(['POST']))
  })
})
