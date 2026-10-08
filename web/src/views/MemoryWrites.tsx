/**
 * P6-03b2 — the Memory **write** panels: retry, the four lifecycle commands, and reflect.
 *
 * **DEPENDENCY PENDING, P4 phase acceptance has not passed, and the switch is off by
 * default.** P4 is not merged; the six routes are real at the pinned commit and absent from
 * this baseline's Core. The proxy forwards them only when the operator starts it with
 * `P6_ENABLE_P4_WRITES=1` — a *different* switch from the reads — so on a default dashboard
 * every submit is refused by the dashboard's own proxy and the panel says which switch is
 * missing rather than blaming the operator's key.
 *
 * Why the controls exist while the entry is closed, rather than being hidden: hiding them
 * would make the refusal state untestable and would leave the operator unable to see what the
 * slice would offer. What must not happen is a *pretence* — so the panels never decide a
 * permission, a revision or a state themselves. Every submit goes to Core, and the panel
 * renders Core's own answer: `forbidden_scope` when the key lacks the scope,
 * `forbidden_actor_kind` when the actor is not human or system, `RevisionConflict` with Core's
 * current revision, `invalid_state` when the state forbids the command. The retry panel
 * deliberately does **not** disable its button for a `DONE` job: P4 refuses that, and the
 * refusal is the evidence that the invariant is Core's rather than the UI's.
 *
 * The payloads come from `api/memoryWrites.ts`, which cannot emit a field the frozen contract
 * does not list. `reason` is offered on lifecycle commands because P4 allows it, and omitted
 * when blank because P4 allows that too; retry and reflect offer no reason field at all,
 * because their contract does not accept one.
 */

import { useId, useState } from 'react'
import type { ReactNode } from 'react'

import type { CoreApiError } from '../api/errors'
import { isDependencyPending } from '../api/errors'
import { useWriteAction } from '../app/useWriteAction'
import type { WriteAction } from '../app/useWriteAction'
import { lifecycleBody, reflectBody, retryBody } from '../api/memoryWrites'
import type {
  MemoryItem,
  MemoryJob,
  MemoryLifecycleCommand,
  MemoryReflectResult,
} from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { WritePanel } from '../components/WritePanel'
import { KeyValue } from '../components/Trace'
import { RawText } from '../components/RawText'
import { Panel } from '../components/States'
import { hrefFor } from '../app/router'
import { formatTimestamp } from '../lib/text'
import {
  MemorySliceStatus,
  NonAuthoritative,
} from './MemoryDependency'

/** The scope each write needs, and the switch that decides whether it is reachable at all. */
const MANAGE_SCOPE = ['memory:manage'] as const
const REFLECT_SCOPE = ['memory:reflect'] as const
export const P4_WRITES_SWITCH = 'P6_ENABLE_P4_WRITES'

/**
 * The one place that says a Memory write is not reachable yet.
 *
 * It names the switch rather than "Core refused", because a `proxy_path_not_allowed` means the
 * request never left this origin: the operator's Core may well be a P4 build that would have
 * accepted the command.
 */
export function MemoryWriteDependency({
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
    <>
      <p>
        <strong>{what}</strong> 的 <code>{method}</code> <code>{path}</code> 只存在于固定的 P4
        提交,且由<strong>独立于读取的开关</strong>控制。<code>paths</code> 与 milestone B 的
        <code> write_paths</code> 都<strong>不含</strong>它:只有以{' '}
        <code>{P4_WRITES_SWITCH}=1</code> 启动的代理才会转发;否则代理以{' '}
        <code>proxy_path_not_allowed</code> 拒绝,而不是把请求送给一个必然 404 的 Core。
      </p>
      <ul className="plain">
        <li>
          需要的 scope:<code>{scopes.join(' + ')}</code>(由 Core 校验;P4 的 retry 与四个
          lifecycle 命令还要求 actor 是 human 或 system,agent key 会被 Core 以{' '}
          <code>ForbiddenActorKind</code> 拒绝)
        </li>
        <li>
          写入开关:<code>{P4_WRITES_SWITCH}=1</code>(与 <code>P6_ENABLE_P4_READS</code> 相互独立:
          能读 Memory 不等于能改它)
        </li>
        <li>
          契约来源:提交 <code>0c4f8f0e2118711ac9aa2a7006fddc6982962248</code>,冻结在{' '}
          <code>web/contracts/p4-memory-write.v1.json</code>
        </li>
        <li>
          验收状态:<strong>P4 阶段验收未通过</strong> —— 这些写入只对模拟 fixture 验证过,
          尚未在真实 P4 Core 上执行
        </li>
      </ul>
    </>
  )
}

/**
 * The readback that makes "写完读回" visible: the displayed row is Core's **re-read** value,
 * tied to the browser-side read time, not the write response.
 *
 * A write response can describe a projection the engine has since changed; the audit question
 * is what the object's own row says now. The timestamp is the resource's `loadedAt`, so a
 * reader can tell a refreshed row from a stale one.
 */
function MemoryReadback({
  title,
  loadedAt,
  rows,
}: {
  title: string
  loadedAt: string | null
  rows: Array<{ label: string; value: ReactNode }>
}): JSX.Element {
  return (
    <div className="state state--meta" data-testid="write-readback">
      <p className="state__headline">{title}</p>
      <KeyValue rows={rows} />
      <p className="state__meta">
        读回时间 <code>{loadedAt ?? '—'}</code> —— 这段值来自对 Core 的重新读取,不是写入响应。
      </p>
    </div>
  )
}

const LIFECYCLE_COMMANDS: Array<{ value: MemoryLifecycleCommand; label: string }> = [
  { value: 'pin', label: 'pin(固定,不再被淘汰)' },
  { value: 'unpin', label: 'unpin(取消固定)' },
  { value: 'obsolete', label: 'obsolete(标记过期)' },
  { value: 'delete', label: 'delete(删除,留下 tombstone)' },
]

/* -------------------------------------------------------------------------- *
 * Lifecycle: obsolete / delete / pin / unpin on one item
 * -------------------------------------------------------------------------- */

export function MemoryLifecyclePanel({
  item,
  loadedAt,
  onWritten,
}: {
  item: MemoryItem
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [command, setCommand] = useState<MemoryLifecycleCommand>('pin')
  const selectId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.memoryLifecycle(item.memory_id, command, body, signal),
    // The command is part of the identity: switching from `pin` to `delete` is a different
    // command and must not reuse the same `event_id`, or Core answers `event_id_conflict`.
    { keyKind: 'event_id', targetKey: `${item.memory_id}:${command}` },
  )

  return (
    <>
      <MemoryWriteDependency
        what="Memory lifecycle"
        method="POST"
        path="/v1/memory/items/{mem}/{obsolete|delete|pin|unpin}"
        scopes={MANAGE_SCOPE}
      />
      <WritePanel
        title="Memory 生命周期写入 (memory:manage)"
        basis={`memory ${item.memory_id} @ revision r${String(item.revision)} —— status ${item.status}, pinned ${String(item.pinned)}`}
        action={action}
        submitLabel={`提交 ${command}`}
        reasonMode="optional"
        targetKey={`${item.memory_id}:${command}`}
        dependencyNote={
          <MemoryWriteDependency
            what="Memory lifecycle"
            method="POST"
            path="/v1/memory/items/{mem}/{obsolete|delete|pin|unpin}"
            scopes={MANAGE_SCOPE}
          />
        }
        ready
        build={({ reason, hostId }) =>
          lifecycleBody(command, {
            // The revision the operator actually read. Core compares it; the UI never
            // refreshes it silently, because that would turn "I read r7" into a blind write.
            expected_revision: item.revision,
            host_id: hostId,
            reason,
          })
        }
        fields={
          <div className="filters">
            <label htmlFor={selectId}>
              命令(Core 各自有独立路由)
              <select
                id={selectId}
                value={command}
                onChange={(event) => setCommand(event.target.value as MemoryLifecycleCommand)}
                data-testid="lifecycle-command"
              >
                {LIFECYCLE_COMMANDS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="state state--meta" data-testid="lifecycle-note">
              Core <strong>先判状态、再判 revision</strong>:对已删除的 item 发任何命令都会得到{' '}
              <code>invalid_state_transition</code>(“不能 obsolete 一个 tombstone”),而不是{' '}
              <code>RevisionConflict</code>。本面板不替 Core 预判这两者。
            </p>
          </div>
        }
        onSucceeded={onWritten}
      />
      {action.state === 'succeeded' ? (
        <MemoryReadback
          title="写后读回:item 自己的行"
          loadedAt={loadedAt}
          rows={[
            { label: 'memory_id', value: <code>{item.memory_id}</code> },
            { label: 'revision', value: <code>r{item.revision}</code> },
            { label: 'status', value: <code>{item.status}</code> },
            { label: 'engine_state', value: <code>{item.engine_state}</code> },
            { label: 'pinned', value: String(item.pinned) },
            { label: 'remote_deleted_at', value: formatTimestamp(item.remote_deleted_at) },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Retry on one job
 * -------------------------------------------------------------------------- */

export function MemoryRetryPanel({
  job,
  loadedAt,
  onWritten,
}: {
  job: MemoryJob
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const action = useWriteAction(
    (body, signal) => endpoints.retryMemoryJob(job.job_id, body, signal),
    { keyKind: 'event_id', targetKey: job.job_id },
  )

  return (
    <>
      <MemoryWriteDependency
        what="Memory retry"
        method="POST"
        path="/v1/memory/jobs/{mjb}/retry"
        scopes={MANAGE_SCOPE}
      />
      <WritePanel
        title="Memory retry (memory:manage)"
        basis={`job ${job.job_id} —— status ${job.status}, retry ${String(job.retry_count)}/${String(job.max_attempts)}`}
        action={action}
        submitLabel="retry 这个 job"
        // P4's retry body allows only `event_id` and `host_id`; a `reason` would be an
        // unknown field, so the panel does not offer one.
        reasonMode="none"
        targetKey={job.job_id}
        dependencyNote={
          <MemoryWriteDependency
            what="Memory retry"
            method="POST"
            path="/v1/memory/jobs/{mjb}/retry"
            scopes={MANAGE_SCOPE}
          />
        }
        ready
        build={({ hostId }) => retryBody({ host_id: hostId })}
        fields={
          <p className="state state--meta" data-testid="retry-note">
            重试<strong>不改变被保留的内容</strong>:<code>document_id</code>、
            <code>content_sha256</code>、<code>bank_id</code> 不变。Core 会拒绝{' '}
            <code>DONE</code>(再 retain 一次会覆盖文档)和 <code>RUNNING</code>
            (从 worker 手里抢走)。本面板不替你禁用按钮 —— 拒绝由 Core 给出,那才是证据。
          </p>
        }
        onSucceeded={onWritten}
      />
      {action.state === 'succeeded' ? (
        <MemoryReadback
          title="写后读回:job 自己的行"
          loadedAt={loadedAt}
          rows={[
            { label: 'job_id', value: <code>{job.job_id}</code> },
            { label: 'status', value: <code>{job.status}</code> },
            { label: 'retry', value: <code>{job.retry_count} / {job.max_attempts}</code> },
            { label: 'claim_generation', value: <code>{job.claim_generation}</code> },
            { label: 'updated_at', value: formatTimestamp(job.updated_at) },
            { label: 'last_error_code', value: <code>{job.last_error_code ?? '—'}</code> },
            { label: 'content_sha256', value: <code>{job.content_sha256}</code> },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * reflect — the generator, on its own route
 * -------------------------------------------------------------------------- */

/** What Core's reflect response says, rendered from Core's fields rather than inferred. */
function ReflectOutcome({ result }: { result: MemoryReflectResult }): JSX.Element {
  return (
    <div className="state state--meta" data-testid="reflect-outcome">
      <NonAuthoritative payload={result} />
      <p className="state__headline">
        reflect status <code>{result.status}</code>
        {result.error_code ? <> · error_code <code>{result.error_code}</code></> : null}
      </p>
      {result.text !== null && result.text !== '' ? (
        <RawText value={result.text} label="reflect text(Core 原文,非权威)" machine={false} />
      ) : (
        <p className="muted" data-testid="reflect-no-text">
          这次 reflect 没有给出文本。
        </p>
      )}
      {/* P4 nulls `safe_error_summary` for a non-human/system actor while keeping the key, so
          "the key is present and null" is what withholding looks like. It is also what a
          human sees when the engine genuinely had no error text, so the page states both
          readings instead of claiming one — the actor kind is Core's to know, not the UI's. */}
      {result.safe_error_summary === null ? (
        <p className="state state--meta" data-testid="reflect-withheld">
          Core 没有给出引擎错误文本(<code>safe_error_summary</code> 存在但为 null)。P4 对非
          human/system 的 actor 会扣留这段自由文本;如果当前 key 是 human,那表示这次没有错误文本。
          本页不替你判断是哪一种。
        </p>
      ) : (
        <RawText value={result.safe_error_summary} label="safe_error_summary" machine={false} />
      )}
      <KeyValue
        rows={[
          { label: 'banks', value: <code>{result.banks.length} 个</code> },
          { label: 'based_on_memory_ids', value: <code>{result.based_on_memory_ids.length} 条</code> },
          {
            label: 'trace',
            value: result.trace_id ? (
              <a href={hrefFor({ name: 'memoryTrace', traceId: result.trace_id })}>
                <code>{result.trace_id}</code>
              </a>
            ) : (
              '—'
            ),
          },
        ]}
      />
      {/* reflect's contract has no command Event among its eight keys, so its audit record is
          the trace it wrote. Saying so is better than showing an empty "command Event" row. */}
      <p className="state__meta">
        reflect 的响应契约里没有 command Event(八个键里没有 <code>event</code>);它的审计记录是
        上面这条 <code>memory_query_traces</code>,可以按 trace_id 读回。
      </p>
    </div>
  )
}

export function MemoryReflectView(): JSX.Element {
  const { endpoints } = useSession()
  const projects = useResource((signal) => endpoints.listProjects({ limit: 50, offset: 0 }, signal), [])
  const [projectId, setProjectId] = useState('')
  const [query, setQuery] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const queryId = useId()
  const projectFieldId = useId()
  const tokensId = useId()

  const effectiveProject = projectId || projects.data?.projects[0]?.project_id || ''
  const parsedTokens = maxTokens.trim() === '' ? undefined : Number.parseInt(maxTokens, 10)
  const tokensValid = parsedTokens === undefined || Number.isInteger(parsedTokens)
  const action = useWriteAction(
    (body, signal) => endpoints.reflectMemory(body, signal),
    // A reflect is a request about a project, not about a row; the project is its identity.
    { keyKind: 'event_id', targetKey: effectiveProject },
  )

  return (
    <Panel title="Memory reflect (memory:reflect)">
      <div className="stack">
        <MemorySliceStatus slice="P6-03b2(写入)" />
        <MemoryWriteDependency
          what="Memory reflect"
          method="POST"
          path="/v1/memory/reflect"
          scopes={REFLECT_SCOPE}
        />
        <p className="state state--meta" data-testid="reflect-explicit">
          reflect 是<strong>显式的用户请求</strong>:它只在这个表单被提交时发生,不会由页面渲染、
          读取或 Context 构建触发。P4 只把 <code>memory:reflect</code> 发给人类用户,并拒绝
          <code> explicit_user_request=false</code> 的调用;agent key 读得到 Memory,但读不到
          reflect。
        </p>
        <WritePanel
          title="reflect 写入 (memory:reflect)"
          basis={`project ${effectiveProject || '(未选择)'}`}
          action={action}
          submitLabel="提交 reflect"
          reasonMode="none"
          targetKey={effectiveProject}
          dependencyNote={
            <MemoryWriteDependency
              what="Memory reflect"
              method="POST"
              path="/v1/memory/reflect"
              scopes={REFLECT_SCOPE}
            />
          }
          ready={effectiveProject !== '' && query.trim() !== '' && tokensValid}
          build={({ hostId }) =>
            reflectBody({
              project_id: effectiveProject,
              query: query.trim(),
              max_tokens: parsedTokens,
              host_id: hostId,
            })
          }
          fields={
            <div className="filters">
              <label htmlFor={projectFieldId}>
                project_id(Core 校验 actor 是否有权使用)
                <select
                  id={projectFieldId}
                  value={effectiveProject}
                  onChange={(event) => setProjectId(event.target.value)}
                  data-testid="reflect-project"
                >
                  {(projects.data?.projects ?? []).map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name} ({project.project_id})
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={queryId}>
                query(Core 必填,≤4000 字符)
                <textarea
                  id={queryId}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="你想让引擎综合反思的问题"
                  data-testid="reflect-query"
                />
              </label>
              <label htmlFor={tokensId}>
                max_tokens(可选)
                <input
                  id={tokensId}
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value)}
                  inputMode="numeric"
                  data-testid="reflect-max-tokens"
                />
              </label>
              {tokensValid ? null : (
                <span className="muted" data-testid="reflect-tokens-invalid">
                  max_tokens 必须是整数
                </span>
              )}
            </div>
          }
        />
        {action.state === 'succeeded' && action.result ? (
          <ReflectOutcome result={action.result as unknown as MemoryReflectResult} />
        ) : null}
      </div>
    </Panel>
  )
}

/* -------------------------------------------------------------------------- *
 * Small helpers shared with the views
 * -------------------------------------------------------------------------- */

/**
 * A write refusal that is Core's own — exported so a view can label it without re-deriving the
 * rule, and so the "is this the missing dependency?" question has one answer.
 */
export function isWriteDependencyRefusal(error: CoreApiError | null): boolean {
  return error !== null && isDependencyPending(error)
}

export type { WriteAction }
