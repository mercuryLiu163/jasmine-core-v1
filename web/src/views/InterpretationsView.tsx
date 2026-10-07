/**
 * Interpretation list (P6-02 milestone A, read slice).
 *
 * `GET /v1/interpretations` is the only interpretation collection in the frozen
 * API and it pages by `after_id`, returning `next_after_id` with **no total
 * count**. The footer therefore says how many rows have been loaded and whether
 * Core said there is more — it never prints a total, and "已读完" only appears
 * when Core returned a null cursor.
 *
 * Every filter is a Core-side query parameter (event_id/project_id/task_id/
 * status); the status choices come from the frozen enum, and an unknown status is
 * still rendered verbatim on the row itself.
 */

import { useEffect, useMemo, useState } from 'react'

import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Interpretation } from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { afterIdLoader, usePagedResource } from '../app/usePagedResource'
import { Pager } from '../components/Pager'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { SourceLink } from '../components/Trace'
import { clip, formatTimestamp } from '../lib/text'

const LIMIT = DEFAULT_OBJECT_LIMIT
/** `interpretations.STATUSES` in the frozen Core. */
const STATUSES = ['PROCESSING', 'EXTRACTED', 'FAILED', 'INTERRUPTED'] as const

export function InterpretationsView({
  projectId,
  taskId,
  eventId,
  status,
}: {
  projectId: string | null
  taskId: string | null
  eventId: string | null
  status: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const [draftEvent, setDraftEvent] = useState(eventId ?? '')

  // The box is a draft of the *current* filter: when the route changes the
  // scope, it must follow, otherwise it shows an id that is no longer applied.
  useEffect(() => {
    setDraftEvent(eventId ?? '')
  }, [eventId])

  const interpretations = usePagedResource(
    afterIdLoader<Interpretation>({
      limit: LIMIT,
      fetch: async (afterId, limit, signal) => {
        const page = await endpoints.listInterpretations(
          { event_id: eventId, project_id: projectId, task_id: taskId, status, after_id: afterId, limit },
          signal,
        )
        return { items: page.items, nextAfterId: page.next_after_id }
      },
    }),
    [projectId, taskId, eventId, status],
  )

  const scope = useMemo(() => {
    const parts: string[] = []
    if (eventId) parts.push(`event ${eventId}`)
    if (taskId) parts.push(`task ${taskId}`)
    if (projectId) parts.push(`project ${projectId}`)
    if (status) parts.push(`status ${status}`)
    return parts.length > 0 ? parts.join(' · ') : '全部(无过滤)'
  }, [projectId, taskId, eventId, status])

  const link = (patch: { eventId?: string | null; status?: string | null }): string =>
    hrefFor({
      name: 'interpretations',
      projectId,
      taskId,
      eventId: patch.eventId === undefined ? eventId : patch.eventId,
      status: patch.status === undefined ? status : patch.status,
    })

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <a href={sourceHref.conversation(projectId, taskId)}>Conversation</a>
        <span aria-hidden="true">/</span>
        <span>Interpretations</span>
      </nav>

      <Panel
        title={`Interpretations(已载入 ${interpretations.items.length} 条)`}
        actions={
          <button type="button" onClick={interpretations.reload} disabled={interpretations.loading}>
            重新读取
          </button>
        }
      >
        <div className="filters">
          <label>
            status
            <select value={status ?? ''} onChange={(event) => {
              const value = event.target.value
              globalThis.location.hash = link({ status: value === '' ? null : value }).slice(1)
            }}>
              <option value="">(全部)</option>
              {STATUSES.map((candidate) => (
                <option key={candidate} value={candidate}>{candidate}</option>
              ))}
            </select>
          </label>
          <label>
            event_id
            <input
              value={draftEvent}
              onChange={(event) => setDraftEvent(event.target.value)}
              placeholder="evt_…"
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              const value = draftEvent.trim()
              globalThis.location.hash = link({ eventId: value === '' ? null : value }).slice(1)
            }}
          >
            按 event_id 过滤
          </button>
          {eventId || status ? (
            <a href={link({ eventId: null, status: null })}>清除过滤</a>
          ) : null}
        </div>
        <p className="state state--meta">
          读取范围:<strong>{scope}</strong>;按 <code>after_id</code> 游标分页,每页 {LIMIT} 条。
          Core 不返回总数,所以这里只声明<strong>已载入</strong>的条数;切 scope 会取消上一请求并丢弃其响应。
        </p>

        {interpretations.loading && interpretations.items.length === 0 ? (
          <Loading what="Interpretations" />
        ) : null}
        {interpretations.error && interpretations.items.length === 0 ? (
          <ErrorPanel error={interpretations.error} what="Interpretations" onRetry={interpretations.reload} />
        ) : null}
        {!interpretations.loading && interpretations.error === null && interpretations.items.length === 0 ? (
          <Empty
            what="Interpretation"
            hint="该范围没有解释记录;这不等同于解释服务不可用。"
          />
        ) : null}

        {interpretations.items.length > 0 ? (
          <table className="table">
            <caption className="sr-only">Interpretation 列表</caption>
            <thead>
              <tr>
                <th scope="col">interpretation</th>
                <th scope="col">status</th>
                <th scope="col">来源 Event</th>
                <th scope="col">extractor / model</th>
                <th scope="col">confidence</th>
                <th scope="col">candidates</th>
                <th scope="col">created_at</th>
              </tr>
            </thead>
            <tbody>
              {interpretations.items.map((item) => (
                <tr key={item.interpretation_id}>
                  <th scope="row">
                    <a href={sourceHref.interpretation(item.interpretation_id)}>
                      <code>{item.interpretation_id}</code>
                    </a>
                    {item.parent_interpretation_id ? (
                      <div className="muted">
                        派生自{' '}
                        <a href={sourceHref.interpretation(item.parent_interpretation_id)}>
                          <code>{item.parent_interpretation_id}</code>
                        </a>
                      </div>
                    ) : null}
                  </th>
                  <td>
                    <StatusBadge kind="interpretation" status={item.status} />
                    {item.error_code ? <div className="muted">{item.error_code}</div> : null}
                  </td>
                  <td>
                    <SourceLink
                      kind="event"
                      id={item.event_id}
                      label="source"
                      taskId={item.task_id}
                      projectId={item.project_id}
                    />
                  </td>
                  <td>
                    {item.extractor_id}
                    <div className="muted">
                      {item.extractor_model} · v{item.extractor_version} · {item.provider}
                    </div>
                    <div className="muted">prompt {item.prompt_version}</div>
                  </td>
                  <td>{item.confidence ?? '—'}</td>
                  <td>{(item.candidates ?? []).length}</td>
                  <td>{formatTimestamp(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        <Pager paged={interpretations} noun="Interpretation" limit={LIMIT} cursorKind="after_id" />
        {interpretations.loadedAt ? (
          <StaleBanner loadedAt={interpretations.loadedAt} scope={`Interpretation 列表(${scope})`} />
        ) : null}
        <p className="state state--meta">
          本页只发 <code>GET</code>。注意 Core 的读取实现会在必要时执行惰性恢复(把超时未完成的解释标记为
          <code>INTERRUPTED</code>、完成待处理的 rerun),因此「只读 HTTP 方法」不等于「零副作用」;
          详见 <code>web/P6-02-COMPLIANCE.md</code>。
        </p>
      </Panel>
    </div>
  )
}

/** Shared row summary used by the detail view header. */
export function interpretationSummary(item: Interpretation): string {
  return `${item.extractor_id} · ${item.extractor_model} · ${clip(item.extractor_version, 40)}`
}
