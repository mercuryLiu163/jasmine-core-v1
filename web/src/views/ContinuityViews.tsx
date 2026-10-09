/**
 * P6-03a: the P3 continuity reads — Checkpoint, Context Pack, Resume (read-only).
 *
 * **DEPENDENCY PENDING.** The four endpoints these views call exist on branch
 * `p3-checkpoint-context-resume` and **not** in this baseline: `850fbec` is schema 8 and
 * has no `context/`, `continuity.py` or `task_snapshot.py`. The contract was derived
 * from that branch by `scripts/p6/p3_contract.py` and frozen in
 * `web/contracts/p3-continuity-read.v1.json` (source commit recorded, with a drift
 * guard), so these views render a *real* P3 shape rather than a plausible one.
 *
 * Because the routes are absent here, they are kept out of the proxy's live allowlist:
 * a baseline dashboard refuses them with `proxy_path_not_allowed`, and the views render
 * a dependency-pending panel saying so. Core's own `not_found` means something else —
 * that the object does not exist — and is never conflated with it.
 *
 * Three habits the views keep, because each is a way a projection gets mistaken for
 * Truth:
 *
 * 1. a checkpoint's `projection` is an **immutable snapshot at `task_revision`**, shown
 *    with the three digests that let a reader check it. It is never labelled current
 *    state;
 * 2. `checkpoints/latest` answering `null` is an **empty result** ("no checkpoint yet"),
 *    rendered as empty rather than as an error;
 * 3. a resume is a **comparison, not a restore**. Its `note` is shown with Core's own
 *    `authority: false, historical: true` next to it, and nothing here implies an
 *    application happened.
 *
 * Nothing in this file writes, and no P4 experiment runs: `memory` inside a Context Pack
 * is displayed as what the P4 provider recorded, marked non-authoritative.
 */

import { useId, useState } from 'react'

import { isDependencyPending, type CoreApiError } from '../api/errors'
import type { Checkpoint, ContextPack, Resume } from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { RawText } from '../components/RawText'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { isCoreId } from '../lib/ids'
import { formatTimestamp } from '../lib/text'

/** A malformed id is refused here, with the right reason, before any request is made. */
function InvalidId({ prefix, value, field }: { prefix: string; value: string; field: string }): JSX.Element {
  return (
    <div className="state state--error" role="alert" data-testid="invalid-id">
      <p className="state__headline">非法 id:{field}</p>
      <p>
        <code>{value}</code> 不是 Core 的 id 形状(<code>{prefix}_</code> + 26 位 Crockford
        base32)。请求没有被发出 —— 否则代理会以 <code>proxy_path_not_allowed</code> 拒绝它,
        那会被误报成「P3 未合并」。
      </p>
    </div>
  )
}

/**
 * A malformed id in the URL that this view's read does not use.
 *
 * Reported rather than dropped — silently ignoring what someone typed is how a typo
 * becomes a mystery — but it does not block the record that *is* readable, because
 * refusing a usable URL over an unused field would be its own kind of wrong.
 */
function UnusedInvalidId({ prefix, value, field }: { prefix: string; value: string; field: string }): JSX.Element {
  return (
    <div className="state state--meta" role="status" data-testid="invalid-id-unused">
      <p>
        忽略无法使用的 <code>{field}</code>:<code>{value}</code> 不是 Core 的 id 形状
        (<code>{prefix}_</code> + 26 位 Crockford base32)。当前 URL 选择的是按 ID 读取,
        不使用这个字段,所以记录仍然显示。
      </p>
    </div>
  )
}

/** A resource error is nullable; only a real proxy refusal counts as pending. */
function pending(error: CoreApiError | null): boolean {
  return error !== null && isDependencyPending(error)
}

/**
 * The scopes each read requires, copied from the frozen contract's `scope` +
 * `extra_scopes`. The full set matters: P3's handlers also reach `_continuity_read`, so a
 * token carrying only the route-table scope is refused by real P3. A test asserts each list
 * against `contracts/p3-continuity-read.v1.json`, so a contract change that is not mirrored
 * here fails rather than quietly showing an incomplete requirement.
 */
const CHECKPOINT_READ_SCOPES = [
  'checkpoint:read',
  'objects:read',
  'state:read',
  'authority:read',
  'evidence:read',
  'events:read',
] as const
const CONTEXT_READ_SCOPES = [
  'context:read',
  'objects:read',
  'state:read',
  'authority:read',
  'evidence:read',
  'events:read',
  'checkpoint:read',
] as const
const RESUME_READ_SCOPES = [
  'resume:read',
  'objects:read',
  'state:read',
  'authority:read',
  'evidence:read',
  'events:read',
  'checkpoint:read',
] as const

/** The one place that decides how "this path is not being forwarded" looks. */
function DependencyPending({
  what,
  method,
  path,
  scopes,
}: {
  what: string
  method: string
  path: string
  scopes: readonly string[]
}): JSX.Element {
  return (
    <div className="state state--pending" role="status" data-testid="dependency-pending">
      <p className="state__headline">
        依赖未满足:{what} 属于 P3(Context / Checkpoint / Resume),仪表盘当前不转发这条路径
      </p>
      <p>
        仪表盘当前没有转发 <code>{method}</code> <code>{path}</code>，代理返回
        <code> proxy_path_not_allowed</code>。读取这组路径需要启动配置
        <code> P6_ENABLE_P3_READS=1</code> 以及支持这些路由的 Core。
        这个拒绝本身不能证明 Core 缺少该记录，也不能证明 Core 的版本或服务状态。
      </p>
      <ul className="plain">
        <li>
          需要的 scope:<code>{scopes.join(' + ')}</code>(由 Core 校验,前端从不代持)
        </li>
        <li>
          契约来源:分支 <code>p3-checkpoint-context-resume</code>,派生并冻结在
          <code> web/contracts/p3-continuity-read.v1.json</code>
        </li>
        <li>
          状态:<strong>PARTIAL / DEPENDENCY_PENDING</strong> —— 本页不是已通过的 P6 能力
        </li>
      </ul>
      <p className="state__meta">
        要在真实 Core 上打开它,需要一个 P3 已合并的 Core,并以显式开关启动代理转发这几条
        路径(见 <code>web/server/core-paths.json</code> 的 <code>p3_pending_paths</code>)。
      </p>
    </div>
  )
}

/**
 * The standing status of this slice, shown on every continuity view whether or not the
 * read succeeded.
 *
 * P3 is not merged into this baseline, so these views are P6-03a *partial* work whatever
 * answered them. Without this line a screenshot taken against a P3-enabled proxy would show
 * a populated record and nothing else — exactly the "P6 passed" reading this milestone must
 * not invite. It states the status, not a cause: the contract comes from an unmerged branch
 * either way.
 */
function SliceStatus(): JSX.Element {
  return (
    <p className="state state--meta" data-testid="slice-status">
      <strong>状态:PARTIAL / DEPENDENCY_PENDING</strong> —— 本页契约派生自尚未合并的分支{' '}
      <code>p3-checkpoint-context-resume</code>(<code>7016dab</code>),不是已通过的 P6 能力。
    </p>
  )
}

/** Digests are the point of these records, so they render whole, wrapped. */
function Digest({ label, value }: { label: string; value: string | null | undefined }): JSX.Element {
  return (
    <span className="kv__digest">
      <span className="muted">{label}</span> <code className="wrap">{value ?? '—'}</code>
    </span>
  )
}

/** Shared scope/host/actor rows so the three views cannot drift apart. */
function identityRows(item: {
  project_id: string
  task_id: string
  session_id: string | null
  actor_id: string
  host_id: string
  created_at: string
  source_event_id: string
  command_event_id: string
}): Array<{ label: string; value: React.ReactNode }> {
  return [
    {
      label: 'scope',
      value: (
        <>
          <a href={sourceHref.project(item.project_id)}>
            <code>{item.project_id}</code>
          </a>{' '}
          ·{' '}
          <a href={sourceHref.task(item.task_id)}>
            <code>{item.task_id}</code>
          </a>
          {item.session_id ? (
            <>
              {' '}
              · session <code>{item.session_id}</code>
            </>
          ) : (
            ' · 无 session'
          )}
        </>
      ),
    },
    { label: 'actor / host', value: <><code>{item.actor_id}</code> · <code>{item.host_id}</code></> },
    {
      label: 'source Event',
      value: <SourceLink kind="event" id={item.source_event_id} label="source Event" />,
    },
    {
      label: 'command Event',
      value: <SourceLink kind="event" id={item.command_event_id} label="command Event" />,
    },
    { label: 'created_at', value: formatTimestamp(item.created_at) },
  ]
}

function StatRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <p className="state state--meta">
      <strong>{label}</strong> {value}
    </p>
  )
}

/* -------------------------------------------------------------------------- */

function CheckpointBody({
  checkpoint,
  onReadback,
}: {
  checkpoint: Checkpoint
  onReadback?: () => void
}): JSX.Element {
  return (
    <>
      <SliceStatus />
      <StatRow
        label="不可变快照:"
        value={`这是 task_revision ${checkpoint.task_revision} 时的快照(projection_version ${checkpoint.projection_version}),是记录,不是当前 Truth。`}
      />
      <p className="muted" data-testid="checkpoint-read-boundary">
        读取历史记录不执行完整性比较，也不证明它与当前 Truth 或工作区匹配。
      </p>
      <KeyValue
        rows={[
          { label: 'checkpoint_id', value: <code>{checkpoint.checkpoint_id}</code> },
          { label: 'reason', value: <code>{checkpoint.reason}</code> },
          {
            label: 'required scope',
            value: <code>{CHECKPOINT_READ_SCOPES.join(' + ')}</code>,
          },
          ...identityRows(checkpoint),
          {
            label: 'task_revision',
            value: <code>r{checkpoint.task_revision}</code>,
          },
          {
            label: 'trigger_provenance',
            value: <code>{String(checkpoint.trigger_provenance?.level ?? '未知')}</code>,
          },
        ]}
      />
      <KeyValue
        rows={[
          {
            label: 'digests',
            value: (
              <span className="stack">
                <Digest label="projection_digest" value={checkpoint.projection_digest} />
                <Digest label="full_context_digest" value={checkpoint.full_context_digest} />
                <Digest label="workspace_digest" value={checkpoint.workspace_digest} />
              </span>
            ),
          },
        ]}
      />
      {checkpoint.note ? (
        <RawText
          value={checkpoint.note.text}
          label="note(记录在创建时的备注,非权威)"
          machine={false}
        />
      ) : (
        <p className="muted">没有 note。</p>
      )}
      <JsonBlock value={checkpoint.trigger_provenance} label="trigger_provenance" />
      <JsonBlock value={checkpoint.projection} label={`projection(task_revision ${checkpoint.task_revision})`} />
      <JsonBlock value={checkpoint.workspace} label="workspace" />
      {onReadback ? (
        <button type="button" onClick={onReadback}>
          从 Core 重新读取
        </button>
      ) : null}
    </>
  )
}

/** `latest` is addressed by Task (with an optional session), a specific one by id. */
export function CheckpointsView({
  taskId,
  checkpointId,
  sessionId,
}: {
  taskId: string | null
  checkpointId: string | null
  sessionId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const taskInputId = useId()
  const checkpointInputId = useId()
  const sessionInputId = useId()
  const [taskDraft, setTaskDraft] = useState(taskId ?? '')
  const [checkpointDraft, setCheckpointDraft] = useState(checkpointId ?? '')
  const [sessionDraft, setSessionDraft] = useState(sessionId ?? '')

  const checkpointIdValid = checkpointId === null || isCoreId(checkpointId, 'ckp')
  const taskIdValid = taskId === null || isCoreId(taskId, 'tsk')
  const sessionIdValid = sessionId === null || isCoreId(sessionId, 'ses')
  // A malformed id blocks the view only when the read that will actually run needs it.
  // `checkpoint_id` selects the by-id read; `task_id` and `session_id` select the latest
  // read, which runs only when there is no `checkpoint_id`. Refusing a *usable* URL over a
  // field the read never touches is its own kind of wrong — so those are reported below
  // without blocking, and nothing is dropped in silence either way.
  const badId =
    checkpointId !== null
      ? checkpointIdValid
        ? null
        : { prefix: 'ckp', field: 'checkpoint_id', value: checkpointId }
      : taskId !== null && !taskIdValid
        ? { prefix: 'tsk', field: 'task_id', value: taskId }
        : sessionId !== null && !sessionIdValid
          ? { prefix: 'ses', field: 'session_id', value: sessionId }
          : null
  // Malformed, but not used by the read this URL selects. Reported, not blocking.
  const unusedBadIds =
    checkpointId !== null
      ? [
          ...(taskId !== null && !taskIdValid
            ? [{ prefix: 'tsk', field: 'task_id', value: taskId }]
            : []),
          ...(sessionId !== null && !sessionIdValid
            ? [{ prefix: 'ses', field: 'session_id', value: sessionId }]
            : []),
        ]
      : []
  const byId =
    badId !== null || checkpointId === null
      ? null
      : (signal: AbortSignal) => endpoints.getCheckpoint(checkpointId, signal)
  const latest =
    badId === null && checkpointId === null && taskId !== null
      ? (signal: AbortSignal) => endpoints.latestCheckpoint(taskId, sessionId, signal)
      : null

  const detail = useResource(byId, [checkpointId])
  const latestResource = useResource(latest, [taskId, sessionId])

  const applyTask = (): void => {
    globalThis.location.hash = hrefFor({
      name: 'checkpoints',
      taskId: taskDraft.trim() || null,
      checkpointId: null,
      sessionId: sessionDraft.trim() || null,
    })
  }
  const applyCheckpoint = (): void => {
    const value = checkpointDraft.trim()
    if (value) {
      globalThis.location.hash = hrefFor({
        name: 'checkpoints',
        taskId,
        checkpointId: value,
        sessionId: null,
      })
    }
  }

  const isPending = pending(detail.error) || pending(latestResource.error)

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Checkpoint(P3 只读)</span>
      </nav>

      <Panel title="读取 Checkpoint(最新 / 按 ID)">
        <div className="filters">
          <label htmlFor={taskInputId}>
            task_id(读最新)
            <input
              id={taskInputId}
              value={taskDraft}
              onChange={(event) => setTaskDraft(event.target.value)}
              placeholder="tsk_…"
              autoComplete="off"
            />
          </label>
          <label htmlFor={sessionInputId}>
            session_id(可空)
            <input
              id={sessionInputId}
              value={sessionDraft}
              onChange={(event) => setSessionDraft(event.target.value)}
              placeholder="ses_…"
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={applyTask}>
            读取最新
          </button>
          <label htmlFor={checkpointInputId}>
            checkpoint_id
            <input
              id={checkpointInputId}
              value={checkpointDraft}
              onChange={(event) => setCheckpointDraft(event.target.value)}
              placeholder="ckp_…"
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={applyCheckpoint}>
            按 ID 读取
          </button>
        </div>
        <p className="state state--meta">
          <code>GET /v1/tasks/&#123;tsk&#125;/checkpoints/latest</code> 需要一个 Task;
          <code>GET /v1/checkpoints/&#123;ckp&#125;</code> 按 ID 读。两者都需要 scope{' '}
          <code>checkpoint:read</code>。「没有 checkpoint」是空集,不是错误,也不是记录不存在。
        </p>

        {badId ? <InvalidId {...badId} /> : null}
        {badId === null
          ? unusedBadIds.map((unused) => (
              <UnusedInvalidId key={unused.field} {...unused} />
            ))
          : null}

        {!badId && isPending ? (
          <DependencyPending
            what="Checkpoint"
            method="GET"
            path={checkpointId ? `/v1/checkpoints/${checkpointId}` : `/v1/tasks/${taskId ?? '{tsk}'}/checkpoints/latest`}
            scopes={CHECKPOINT_READ_SCOPES}
          />
        ) : null}

        {!badId && !isPending && (detail.status === 'loading' || latestResource.status === 'loading') &&
        detail.data === null && latestResource.data === null ? (
          <Loading what="Checkpoint" />
        ) : null}

        {!badId && !isPending && detail.error ? (
          <ErrorPanel error={detail.error} what={`Checkpoint ${checkpointId}`} onRetry={detail.reload} />
        ) : null}
        {!badId && !isPending && latestResource.error ? (
          <ErrorPanel
            error={latestResource.error}
            what={`Task ${taskId} 的最新 Checkpoint`}
            onRetry={latestResource.reload}
          />
        ) : null}

        {!badId && !isPending && detail.data ? (
          <CheckpointBody checkpoint={detail.data.checkpoint} onReadback={detail.reload} />
        ) : null}

        {!badId && !isPending && latestResource.data ? (
          latestResource.data.checkpoint === null ? (
            <Empty
              what="Checkpoint"
              hint="该 Task 目前没有 checkpoint;这是空集,不是错误,也不代表 Task 不存在。"
            />
          ) : (
            <CheckpointBody checkpoint={latestResource.data.checkpoint} onReadback={latestResource.reload} />
          )
        ) : null}

        {checkpointId === null && taskId === null ? (
          <p className="muted">尚未给出 task_id 或 checkpoint_id。</p>
        ) : null}

        {detail.loadedAt ? <StaleBanner loadedAt={detail.loadedAt} scope="Checkpoint(按 ID)" /> : null}
        {latestResource.loadedAt ? (
          <StaleBanner loadedAt={latestResource.loadedAt} scope="Checkpoint(最新)" />
        ) : null}
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- */

function ContextPackBody({
  pack,
  onReadback,
}: {
  pack: ContextPack
  onReadback: () => void
}): JSX.Element {
  return (
    <>
      <SliceStatus />
      <StatRow
        label="这是实际渲染记录:"
        value={`rendered_content 是当时真正渲染的内容(renderer ${pack.renderer_version}),token ${pack.token_count} / ${pack.rendered_utf8_bytes} 字节。`}
      />
      <KeyValue
        rows={[
          { label: 'context_pack_id', value: <code>{pack.context_pack_id}</code> },
          { label: 'reason', value: <code>{pack.reason}</code> },
          {
            label: 'required scope',
            value: <code>{CONTEXT_READ_SCOPES.join(' + ')}</code>,
          },
          ...identityRows(pack),
          { label: 'current_step_id', value: <code>{pack.current_step_id ?? '—'}</code> },
          {
            label: 'trigger_provenance',
            value: <code>{String(pack.trigger_provenance?.level ?? '未知')}</code>,
          },
          {
            label: 'digests',
            value: (
              <span className="stack">
                <Digest label="truth_digest" value={pack.truth_digest} />
                <Digest label="rendered_sha256" value={pack.rendered_sha256} />
                <Digest label="config_digest" value={pack.config_digest} />
                <Digest label="workspace_digest" value={pack.workspace_digest} />
              </span>
            ),
          },
        ]}
      />
      <RawText value={pack.rendered_content} label="rendered_content(实际渲染内容)" />
      <JsonBlock value={pack.section_metrics} label="section_metrics(每段 token)" />
      <JsonBlock value={pack.omissions} label="omissions(被省略的内容)" />
      <JsonBlock value={pack.selected_optional_ids} label="selected_optional_ids" />
      <JsonBlock value={pack.selector} label="selector" />
      <JsonBlock value={pack.resume_comparison} label="resume_comparison" />
      <JsonBlock value={pack.configuration} label="configuration" />
      <JsonBlock value={pack.tokenizer} label="tokenizer" />
      <p className="state state--meta">
        <strong>memory 非权威:</strong>下面记录的是 P4 provider 当时返回的内容;它不是 Truth,
        本页不运行任何 P4 实验,也不据此改变任何状态。
      </p>
      <JsonBlock value={pack.memory} label="memory(P4 提供,非权威)" />
      <JsonBlock value={pack.projection} label="projection" />
      <JsonBlock value={pack.workspace} label="workspace" />
      <JsonBlock value={pack.sample_window} label="sample_window" />
      <button type="button" onClick={onReadback}>
        从 Core 重新读取
      </button>
    </>
  )
}

export function ContextPackView({ contextPackId }: { contextPackId: string }): JSX.Element {
  const { endpoints } = useSession()
  const inputId = useId()
  const [draft, setDraft] = useState(contextPackId)
  const idValid = isCoreId(contextPackId, 'ctx')
  const pack = useResource(
    idValid ? (signal: AbortSignal) => endpoints.getContextPack(contextPackId, signal) : null,
    [contextPackId],
  )

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Context Pack(P3 只读)</span>
      </nav>
      <Panel title="按 ID 读取 Context Pack">
        <div className="filters">
          <label htmlFor={inputId}>
            context_pack_id
            <input
              id={inputId}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="ctx_…"
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              const value = draft.trim()
              if (value) globalThis.location.hash = sourceHref.contextPack(value)
            }}
          >
            读取这个 pack
          </button>
        </div>
        <p className="state state--meta">
          <code>GET /v1/context/&#123;ctx&#125;</code> · scope <code>context:read</code>。上下文包是
          <strong>构建时</strong>的产物;本页只读它,不会重新构建,也不会把 memory 当作 Truth。
        </p>

        {!idValid ? (
          <InvalidId prefix="ctx" field="context_pack_id" value={contextPackId} />
        ) : null}
        {idValid && pending(pack.error) ? (
          <DependencyPending
            what="Context Pack"
            method="GET"
            path={`/v1/context/${contextPackId}`}
            scopes={CONTEXT_READ_SCOPES}
          />
        ) : null}
        {idValid && !pending(pack.error) && pack.status === 'loading' && pack.data === null ? (
          <Loading what="Context Pack" />
        ) : null}
        {idValid && pack.error && !pending(pack.error) ? (
          <ErrorPanel error={pack.error} what={`Context Pack ${contextPackId}`} onRetry={pack.reload} />
        ) : null}
        {idValid && pack.data ? <ContextPackBody pack={pack.data.context} onReadback={pack.reload} /> : null}
        {pack.loadedAt ? <StaleBanner loadedAt={pack.loadedAt} scope="Context Pack" /> : null}
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- */

/**
 * One entry of P3's `next_actions`.
 *
 * P3 returns objects: `{"kind": "RECONCILE_CURRENT_TRUTH"}`, or
 * `{"kind": "VERIFY_EVIDENCE" | "INSPECT_STEP", "step_id": ..., "status": ...}`. The
 * `kind` is the action, so it leads; `step_id`/`status` are the subject. An entry of a
 * shape this build does not know is still shown, as its JSON, rather than dropped — an
 * unrecognised action must not read as no action.
 */
function NextAction({ action }: { action: unknown }): JSX.Element {
  if (action !== null && typeof action === 'object' && !Array.isArray(action)) {
    const record = action as Record<string, unknown>
    const kind = typeof record.kind === 'string' ? record.kind : null
    const rest = Object.entries(record).filter(([key]) => key !== 'kind')
    if (kind !== null) {
      return (
        <>
          <code>{kind}</code>
          {rest.length ? (
            <span className="muted">
              {' '}
              {rest
                .map(([key, value]) => `${key} = ${typeof value === 'string' ? value : JSON.stringify(value)}`)
                .join(', ')}
            </span>
          ) : null}
        </>
      )
    }
  }
  return <code>{typeof action === 'string' ? action : JSON.stringify(action)}</code>
}

function ResumeBody({ resume, onReadback }: { resume: Resume; onReadback: () => void }): JSX.Element {
  const nextActions = Array.isArray(resume.next_actions) ? resume.next_actions : []
  return (
    <>
      <SliceStatus />
      <StatRow
        label="这是比较,不是恢复:"
        value="comparison / next_actions 描述 checkpoint 与当前投影的差异;本页不会应用任何东西,Truth 未被改动。"
      />
      <div className="state state--meta" data-testid="resume-comparison-status">
        <p>这是收据生成时的比较结果；重新读取不会重新比较。</p>
        <p>
          checkpoint_freshness: <code>{String(resume.comparison?.checkpoint_freshness ?? '未知')}</code>
          {' · '}workspace: <code>{String(resume.comparison?.workspace ?? '未知')}</code>
          {' · '}reconciliation_required: <code>{String(resume.comparison?.reconciliation_required ?? '未知')}</code>
        </p>
        {resume.comparison?.workspace === 'UNKNOWN' ? <p>工作区未确认；相同的 Truth 投影不能证明工作区匹配。</p> : null}
        {resume.comparison?.workspace === 'MISMATCH' ? <p>工作区存在差异；请按本条历史收据中的建议对账。</p> : null}
      </div>
      <KeyValue
        rows={[
          { label: 'resume_id', value: <code>{resume.resume_id}</code> },
          {
            label: 'required scope',
            value: <code>{RESUME_READ_SCOPES.join(' + ')}</code>,
          },
          ...identityRows(resume),
          {
            label: 'checkpoint',
            value: resume.checkpoint_id ? (
              <a href={sourceHref.checkpoints(null, resume.checkpoint_id)}>
                <code>{resume.checkpoint_id}</code>
              </a>
            ) : (
              '—(该 Task 当时没有 checkpoint)'
            ),
          },
          {
            label: 'digests',
            value: <Digest label="truth_digest" value={resume.truth_digest} />,
          },
        ]}
      />
      {resume.note ? (
        <div className="state state--meta" data-testid="resume-note">
          <p className="state__headline">
            note:historical,authority = false(Core 明确否认其权威性)
          </p>
          {resume.note.value ? <RawText value={resume.note.value.text} label="note.value" /> : null}
        </div>
      ) : (
        <p className="muted">没有 note。</p>
      )}
      <JsonBlock value={resume.comparison} label="comparison(checkpoint 与当前投影的差异)" />
      {/* `next_actions` is what a reader opens this page for, and P3 returns it as a list of
          *objects* — `{"kind": "RECONCILE_CURRENT_TRUTH"}`, or `{"kind", "step_id",
          "status"}`. Rendering it as a collapsed JSON block would hide the one actionable
          part behind a click, so each entry is rendered as its own row. Anything this
          build does not recognise still renders (as JSON on that row) rather than being
          dropped: an unknown action must not look like no action. */}
      {nextActions.length ? (
        <div className="stack" data-testid="next-actions">
          <span className="muted">next_actions({nextActions.length} 条,建议而非已执行)</span>
          <ul className="plain">
            {nextActions.map((action, index) => (
              <li key={index} data-testid="next-action">
                <NextAction action={action} />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <JsonBlock value={resume.next_actions} label="next_actions" startsOpen />
      )}
      <JsonBlock value={resume.projection} label="projection" />
      <JsonBlock value={resume.workspace} label="workspace" />
      <JsonBlock value={resume.sample_window} label="sample_window" />
      <button type="button" onClick={onReadback}>
        从 Core 重新读取
      </button>
    </>
  )
}

export function ResumeView({ resumeId }: { resumeId: string }): JSX.Element {
  const { endpoints } = useSession()
  const inputId = useId()
  const [draft, setDraft] = useState(resumeId)
  const idValid = isCoreId(resumeId, 'rms')
  const resume = useResource(
    idValid ? (signal: AbortSignal) => endpoints.getResume(resumeId, signal) : null,
    [resumeId],
  )

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Resume(P3 只读)</span>
      </nav>
      <Panel title="按 ID 读取 Resume">
        <div className="filters">
          <label htmlFor={inputId}>
            resume_id
            <input
              id={inputId}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="rms_…"
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              const value = draft.trim()
              if (value) globalThis.location.hash = sourceHref.resume(value)
            }}
          >
            读取这个 resume
          </button>
        </div>
        <p className="state state--meta">
          <code>GET /v1/resumes/&#123;rms&#125;</code> · scope <code>resume:read</code> +
          <code>checkpoint:read</code>。Resume 是<strong>精确的历史 receipts</strong>,不是恢复操作。
        </p>

        {!idValid ? <InvalidId prefix="rms" field="resume_id" value={resumeId} /> : null}
        {idValid && pending(resume.error) ? (
          <DependencyPending
            what="Resume"
            method="GET"
            path={`/v1/resumes/${resumeId}`}
            scopes={RESUME_READ_SCOPES}
          />
        ) : null}
        {idValid && !pending(resume.error) && resume.status === 'loading' && resume.data === null ? (
          <Loading what="Resume" />
        ) : null}
        {idValid && resume.error && !pending(resume.error) ? (
          <ErrorPanel error={resume.error} what={`Resume ${resumeId}`} onRetry={resume.reload} />
        ) : null}
        {idValid && resume.data ? <ResumeBody resume={resume.data.resume} onReadback={resume.reload} /> : null}
        {resume.loadedAt ? <StaleBanner loadedAt={resume.loadedAt} scope="Resume" /> : null}
      </Panel>
    </div>
  )
}
