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

import { useId, useState } from 'react'
import type { ReactNode } from 'react'

import type { WriteAction, WriteKeyKind } from '../app/useWriteAction'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from './JsonBlock'
import { SourceLink } from './Trace'
import { ErrorPanel, Loading } from './States'

export interface WritePanelProps {
  title: string
  /** What this action is about, e.g. `interpretation int_… @ revision 4`. */
  basis: string
  action: WriteAction
  submitLabel: string
  /** Endpoint-specific fields; `reason` and `host_id` are added by the panel. */
  fields: ReactNode
  /** Builds the body; the panel adds `reason`, `host_id` and `idempotency_key`. */
  build: (input: { reason: string; hostId: string }) => Record<string, unknown> | string
  /** Disables submit until the endpoint-specific fields are valid. */
  ready: boolean
  /**
   * `'required'` (default) shows and requires the `reason` input.
   * `'none'` hides it, because this endpoint's Core contract rejects `reason`
   * as an unknown field — the panel must not offer a field that cannot be sent.
   */
  reasonMode?: 'required' | 'none'
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

export function WritePanel({
  title,
  basis,
  action,
  submitLabel,
  fields,
  build,
  ready,
  reasonMode = 'required',
  onSucceeded,
}: WritePanelProps): JSX.Element {
  const { endpoints } = useSession()
  const hosts = useResource((signal) => endpoints.listHosts(signal), [])
  const [hostId, setHostId] = useState('')
  const [reason, setReason] = useState('')
  const [validation, setValidation] = useState<string | null>(null)
  const reasonId = useId()
  const hostIdField = useId()

  const effectiveHost = hostId || hosts.data?.hosts[0]?.host_id || ''
  const needsReason = reasonMode === 'required'
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
          {needsReason ? (
            <label htmlFor={reasonId}>
              reason(Core 必填)
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
            重置草稿(换新幂等键)
          </button>
          <span className="muted">
            {keyLabel.label} <code>{action.writeKey.slice(0, 18)}…</code> {keyLabel.note}
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
            {action.error.details ? (
              <JsonBlock value={action.error.details} label="Core 当前值/详情" startsOpen />
            ) : null}
          </div>
        ) : null}

        {action.state === 'refused' && action.error ? (
          <div className="state state--error" role="alert">
            <p className="state__headline">
              被拒绝 — <code>{action.error.code}</code>
              {action.error.status !== null ? ` (HTTP ${action.error.status})` : ''}
            </p>
            <p>{action.error.message}</p>
            <p className="state__meta">
              本页不会换更高权限的凭据重试;缺 scope 或角色不符需要操作者自己解决。
            </p>
            {action.error.details ? <JsonBlock value={action.error.details} label="details" /> : null}
          </div>
        ) : null}

        {action.state === 'unknown' ? (
          <div className="state state--pending" role="alert">
            <p className="state__headline">结果未知{action.error ? ` — ${action.error.code}` : ''}</p>
            <p>
              请求可能已经到达 Core,也可能没有。请<strong>读回</strong>对象或 command Event 后再决定;
              不要用新幂等键重做({action.error?.message ?? ''})。
            </p>
            {action.requestId ? (
              <p className="state__meta">
                request_id <code>{action.requestId}</code>
              </p>
            ) : null}
          </div>
        ) : null}

        {action.state === 'succeeded' && action.result ? (
          <div className="state state--meta">
            <p className="state__headline">
              已提交{action.result.replayed ? '(replayed: Core 返回了原命令结果,未重复写入)' : ''}
            </p>
            <JsonBlock value={action.result} label="Core 响应(原文)" startsOpen />
            {commandEventId(action.result) ? (
              <p>
                command Event:{' '}
                <SourceLink kind="event" id={commandEventId(action.result)!} label="command" />
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
