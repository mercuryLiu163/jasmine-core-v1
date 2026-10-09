/**
 * Shared shell for every audited write (P6-02 milestone B).
 *
 * One place decides what the operator sees, so no panel can quietly invent a
 * different rule:
 *
 * - the **basis** (revision Core returned) is displayed and sent verbatim;
 * - the **host** is chosen from `GET /v1/hosts`, because Core validates that the
 *   host exists and the browser has no Registry identity of its own — the
 *   dashboard never claims to be a host it did not read;
 * - a **reason** is required when Core's endpoint takes one. Authority writes
 *   validate their fields exactly and do not accept `reason`, so their panels
 *   say so instead of sending a field Core would refuse;
 * - the **replay key** is labelled for what it is (`idempotency_key` or the
 *   `event_id` Authority replays on), never a generic "key";
 * - a **conflict** shows Core's own details next to the draft and keeps the draft;
 * - a **refusal** names the missing scope/role and offers no alternative
 *   credential;
 * - an **unknown** result shows the request id and a readback instruction;
 * - a **success** shows Core's response verbatim, including `replayed`.
 */

import { useEffect, useId, useState } from 'react'
import type { ReactNode } from 'react'

import type { WriteAction, WriteKeyKind } from '../app/useWriteAction'
import type { WriteResult } from '../api/endpoints'
import type { CoreApiError } from '../api/errors'
import { isDependencyPending } from '../api/errors'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from './JsonBlock'
import { KeyValue, SourceLink } from './Trace'
import { ErrorPanel, Loading } from './States'

export interface WritePanelProps<T = WriteResult> {
  title: string
  /** What this action is about, e.g. `interpretation int_… @ revision 4`. */
  basis: string
  action: WriteAction<T>
  submitLabel: string
  /** Endpoint-specific fields; `reason` and `host_id` are added by the panel. */
  fields: ReactNode
  /** Builds the body; the panel adds `reason`, `host_id` and `idempotency_key`. */
  build: (input: { reason: string; hostId: string }) => Record<string, unknown> | string
  /**
   * Identity of the object being written. Changing it clears this panel's local
   * draft: the panels for two different interpretations share one component
   * instance, so without this the reason typed for one would appear on the next
   * (`useWriteAction` handles the key, but `reason` lives here).
   */
  targetKey?: string
  /** Disables submit until the endpoint-specific fields are valid. */
  ready: boolean
  /**
   * `'required'` (default) shows and requires the `reason` input.
   * `'optional'` shows it and sends it only when non-empty — for endpoints whose
   * contract *allows* `reason` without requiring it (P4's lifecycle commands).
   * `'none'` hides it, because this endpoint's Core contract rejects `reason`
   * as an unknown field — the panel must not offer a field that cannot be sent.
   */
  reasonMode?: 'required' | 'optional' | 'none'
  /**
   * Rendered instead of the generic refusal when Core was never reached because the
   * dashboard's own proxy does not forward this path. The P6-03b2 Memory writes are
   * default-off, so their refusal is "this dependency is not switched on", which is a
   * different statement from "你的 key 缺 scope" — and the generic block asserts the latter.
   */
  dependencyNote?: ReactNode
  onSucceeded?: () => void
}

/** What the replay key is, in Core's own terms, per endpoint family. */
const KEY_LABELS: Record<WriteKeyKind, { label: string; note: string }> = {
  idempotency_key: {
    label: 'idempotency_key',
    note: '幂等键在本次意图内复用;超时重试不会换键,Core 会识别为同一命令。',
  },
  event_id: {
    label: 'event_id(重放键)',
    note:
      'Authority 写入不接受 idempotency_key,而是按客户端提供的 event_id 去重:' +
      '同键同内容返回原结果(replayed),同键不同内容 Core 判 event_id_conflict。',
  },
}

export function WritePanel<T = WriteResult>({
  title,
  basis,
  action,
  submitLabel,
  fields,
  build,
  ready,
  targetKey,
  reasonMode = 'required',
  dependencyNote,
  onSucceeded,
}: WritePanelProps<T>): JSX.Element {
  const { endpoints } = useSession()
  const hosts = useResource((signal) => endpoints.listHosts(signal), [])
  const [hostId, setHostId] = useState('')
  const [reason, setReason] = useState('')
  const [validation, setValidation] = useState<string | null>(null)
  const reasonId = useId()
  const hostIdField = useId()

  // A different target is a different command: the reason written for the previous
  // one must not be submitted for this one.
  useEffect(() => {
    setReason('')
    setValidation(null)
  }, [targetKey])

  const effectiveHost = hostId || hosts.data?.hosts[0]?.host_id || ''
  const needsReason = reasonMode === 'required'
  const offersReason = reasonMode !== 'none'
  const canSubmit =
    ready && effectiveHost !== '' && (!needsReason || reason.trim() !== '') && !action.busy
  const keyLabel = KEY_LABELS[action.keyKind]

  const onSubmit = async (): Promise<void> => {
    const body = build({ reason: reason.trim(), hostId: effectiveHost })
    if (typeof body === 'string') {
      setValidation(body)
      return
    }
    setValidation(null)
    await action.submit(body)
  }

  return (
    // Stable hooks: the T03 Gate and the browser trace need to find *this*
    // panel's fields without depending on generated ids or on translated text.
    <section className="panel write-panel" aria-label={title} data-write-panel={title}>
      <header className="panel__head">
        <h2>{title}</h2>
        <span className="muted">{basis}</span>
      </header>
      <div className="panel__body">
        {hosts.status === 'loading' && hosts.data === null ? <Loading what="Hosts" /> : null}
        {hosts.error ? <ErrorPanel error={hosts.error} what="Hosts" onRetry={hosts.reload} /> : null}

        <div className="filters">
          <label htmlFor={hostIdField}>
            host_id(Core 校验)
            <select
              id={hostIdField}
              value={effectiveHost}
              onChange={(event) => setHostId(event.target.value)}
            >
              {(hosts.data?.hosts ?? []).map((host) => (
                <option key={host.host_id} value={host.host_id}>
                  {host.display_name} ({host.host_id})
                </option>
              ))}
            </select>
          </label>
          {offersReason ? (
            <label htmlFor={reasonId}>
              {needsReason ? 'reason(Core 必填)' : 'reason(Core 可选,留空则不发送)'}
              <input
                id={reasonId}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="为什么做这个改动"
                autoComplete="off"
                data-testid="write-reason"
              />
            </label>
          ) : (
            <p className="state state--meta" data-testid="no-reason-field">
              Core 的这个端点在 <code>rule.proposed</code>/<code>rule.*</code> 事件里记录 actor、
              host 与 <code>expected_revision</code>,但<strong>不接受 reason 字段</strong>
              (多传会被判 unknown fields)。因此本面板不提供 reason 输入,也不发送它。
            </p>
          )}
        </div>

        {fields}
        {validation ? (
          <p className="state state--error" role="alert">
            {validation}
          </p>
        ) : null}

        <div className="filters">
          <button
            type="button"
            onClick={() => void onSubmit()}
            disabled={!canSubmit}
            data-testid="write-submit"
          >
            {action.busy ? '提交中…' : submitLabel}
          </button>
          <button type="button" onClick={action.resetDraft} disabled={action.busy}>
            重置草稿(换新{action.keyKind === 'event_id' ? ' event_id' : '幂等键'})
          </button>
          <span className="muted">
            {action.state === 'succeeded' ? (
              <>
                {keyLabel.label} 上一次提交已完成;再次提交属于新的意图,将使用{' '}
                <code>{action.writeKey.slice(0, 18)}…</code>。{keyLabel.note}
              </>
            ) : (
              <>
                本次意图的 {keyLabel.label} <code>{action.writeKey.slice(0, 18)}…</code>{' '}
                {keyLabel.note}
              </>
            )}
          </span>
        </div>

        {action.state === 'conflict' && action.error ? (
          <div className="state state--pending" role="alert">
            <p className="state__headline">
              冲突,已保留草稿 — <code>{action.error.code}</code>
              {action.error.status !== null ? ` (HTTP ${action.error.status})` : ''}
            </p>
            <p>
              Core 认为对象已经变化,草稿没有被提交。下面是 Core 返回的当前值与判定依据;
              请重新审阅后再决定,界面不会用新 revision 自动重试原意。
            </p>
            <RefusalFields error={action.error} />
            <RequestIdLine requestId={action.requestId} />
            {action.error.details ? (
              <JsonBlock value={action.error.details} label="Core 当前值/详情" startsOpen />
            ) : null}
            {/* A conflict is resolved by re-reading, so the readback action is offered here too. */}
            {onSucceeded ? (
              <button type="button" onClick={onSucceeded}>
                从 Core 重新读取
              </button>
            ) : null}
          </div>
        ) : null}

        {action.state === 'refused' && action.error && isDependencyPending(action.error) &&
        dependencyNote ? (
          <div className="state state--pending" role="status" data-testid="write-dependency-pending">
            <p className="state__headline">
              写入未接入 — <code>{action.error.code}</code>
            </p>
            {dependencyNote}
          </div>
        ) : null}

        {/* An Evidence-gated edge whose validation did not pass is neither a failure nor a
            success: Core refused before any write, and the projection is unchanged (§7, §11).
            It is recorded as BLOCKED with Core's own reason rather than shown as a generic
            error, and it is deliberately not routed through the conflict block (re-reading
            will not make an unsatisfied criterion pass). */}
        {action.state === 'refused' && action.error?.code === 'missing_evidence' ? (
          <div className="state state--pending" role="status" data-testid="write-evidence-blocked">
            <p className="state__headline">
              Evidence 门槛未通过 — <strong>BLOCKED</strong> · <code>{action.error.code}</code>
              {action.error.status !== null ? ` (HTTP ${action.error.status})` : ''}
            </p>
            <p>{action.error.message}</p>
            <p className="state__meta">
              没有发生写入:对象的状态与 revision 不变,也没有追加 command Event。这不是失败,也不是成功;
              Core 的判定依据原样显示在下面。
            </p>
            <RefusalFields error={action.error} />
            {action.error.details ? <JsonBlock value={action.error.details} label="details" /> : null}
            <RequestIdLine requestId={action.requestId} />
          </div>
        ) : null}

        {action.state === 'refused' && action.error &&
        action.error.code !== 'missing_evidence' &&
        !(isDependencyPending(action.error) && dependencyNote) ? (
          <div className="state state--error" role="alert">
            <p className="state__headline">
              被拒绝 — <code>{action.error.code}</code>
              {action.error.status !== null ? ` (HTTP ${action.error.status})` : ''}
            </p>
            <p>{action.error.message}</p>
            <RefusalFields error={action.error} />
            <p className="state__meta">
              本页不会换更高权限的凭据重试;缺 scope 或角色不符需要操作者自己解决。
            </p>
            {action.error.details ? <JsonBlock value={action.error.details} label="details" /> : null}
            <RequestIdLine requestId={action.requestId} />
          </div>
        ) : null}

        {action.state === 'unknown' ? (
          <div className="state state--pending" role="alert" data-testid="write-unknown">
            <p className="state__headline">结果未知{action.error ? ` — ${action.error.code}` : ''}</p>
            <p>
              请求可能已经到达 Core,也可能没有。请<strong>读回</strong>对象或 command Event 后再决定;
              不要用新 {action.keyKind === 'event_id' ? 'event_id' : '幂等键'} 重做
              ({action.error?.message ?? ''})。
            </p>
            <RequestIdLine requestId={action.requestId} />
            {/* The recovery §5 names second: read the truth back rather than guess. */}
            {onSucceeded ? (
              <button type="button" onClick={onSucceeded}>
                从 Core 重新读取
              </button>
            ) : null}
          </div>
        ) : null}

        {action.state === 'succeeded' && action.result ? (
          <div className="state state--meta">
            <p className="state__headline">
              已提交{replayedFlag(action.result) ? '(replayed: Core 返回了原命令结果,未重复写入)' : ''}
            </p>
            <JsonBlock value={action.result} label="Core 响应(原文)" startsOpen />
            {commandEventId(action.result as Record<string, unknown>) ? (
              <p>
                command Event:{' '}
                <SourceLink
                  kind="event"
                  id={commandEventId(action.result as Record<string, unknown>)!}
                  label="command"
                />
              </p>
            ) : null}
            {onSucceeded ? (
              <button type="button" onClick={onSucceeded}>
                从 Core 重新读取
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  )
}

/**
 * The correlation id for a failure, rendered on every failure path (§5).
 *
 * A response with no id is shown as **unknown**, never silently omitted: "no id" and "no
 * request happened" are different statements, and the operator needs the first one to know the
 * outcome cannot be correlated. That is a gap to record, not a verdict.
 */
function RequestIdLine({ requestId }: { requestId: string | null }): JSX.Element {
  if (requestId) {
    return (
      <p className="state__meta" data-testid="write-request-id">
        request_id <code>{requestId}</code>
      </p>
    )
  }
  return (
    <p className="state__meta" data-testid="write-request-id">
      request_id <strong>未知</strong> —— 这次失败没有带回可关联的 id(例如代理 502 没有
      <code>X-Request-Id</code>);这是一个关联缺口,不是判决,请读回对象/history 再决定。
    </p>
  )
}

/**
 * Core's own fields for a refusal, named so a reader sees which revisions, which edge or which
 * scope was refused without decoding the JSON. The raw `details` block is still rendered beside
 * it — this is a reading aid, not a substitute.
 */
export function RefusalFields({ error }: { error: CoreApiError }): JSX.Element | null {
  const details = (error.details ?? null) as Record<string, unknown> | null
  if (details === null || typeof details !== 'object') return null
  const keys = [
    'expected_revision',
    'current_revision',
    'from_status',
    'to_status',
    'current_status',
    'task_status',
    'required',
    'granted',
    'field',
    'fields',
    'source_system',
    'source_event_id',
    'existing_event_id',
    // The `422 missing_evidence` details (spec §7) name the requirement/rule that failed.
    'requirement_key',
    'rule_id',
    'rule_version',
    'step_id',
    'task_id',
  ]
  const rows = keys
    .filter((key) => key in details)
    .map((key) => ({ label: key, value: <code>{JSON.stringify(details[key])}</code> }))
  if (rows.length === 0) return null
  return (
    <div data-testid="write-refusal-fields">
      <KeyValue rows={rows} />
    </div>
  )
}

/**
 * Core's `replayed` flag, when the family has one.
 *
 * Every milestone B and P4 lifecycle/retry response carries it; P4's reflect answers eight
 * named keys and does not. Read defensively so a family without the field cannot make the
 * panel claim a replay that never happened.
 */
function replayedFlag(result: unknown): boolean {
  return (
    typeof result === 'object' &&
    result !== null &&
    (result as { replayed?: unknown }).replayed === true
  )
}

/** Core nests the command Event differently per endpoint; find it without guessing. */
export function commandEventId(result: Record<string, unknown>): string | null {
  for (const key of ['event', 'result_event', 'status_event']) {
    const value = result[key]
    if (typeof value === 'object' && value !== null && 'event_id' in value) {
      const id = (value as { event_id?: unknown }).event_id
      if (typeof id === 'string') return id
    }
  }
  const nested = result['result']
  if (typeof nested === 'object' && nested !== null) {
    return commandEventId(nested as Record<string, unknown>)
  }
  return null
}

/** Parse a textarea that must contain JSON; returns an error string or null. */
export function parseJsonField(text: string, label: string): { value?: unknown; error?: string } {
  if (text.trim() === '') return { error: `${label} 不能为空` }
  try {
    return { value: JSON.parse(text) }
  } catch (error) {
    return { error: `${label} 不是合法 JSON: ${error instanceof Error ? error.message : String(error)}` }
  }
}
