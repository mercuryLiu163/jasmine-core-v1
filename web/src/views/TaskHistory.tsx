/**
 * Task history tab (P6-A04).
 *
 * Reads `GET /v1/tasks/{id}/history` (State Events in sequence) and, when a Step
 * is selected, `GET /v1/steps/{id}/history`. Evidence IDs recorded on a
 * transition link straight to the Evidence row, so the source trace
 * history → Evidence → source Event is clickable in both directions.
 *
 * The list is rendered newest-first for reading, but the raw `seq` is shown on
 * every row and the underlying read keeps Core's ascending order.
 */

import { useMemo, useState } from 'react'

import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { EventRow } from '../components/EventRow'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { StatusBadge } from '../components/StatusBadge'
import { formatTimestamp } from '../lib/text'

export function TaskHistory({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const [stepId, setStepId] = useState<string | null>(null)
  const [newestFirst, setNewestFirst] = useState(true)

  const steps = useResource((signal) => endpoints.listSteps(taskId, signal), [taskId])
  const history = useResource((signal) => endpoints.taskHistory(taskId, signal), [taskId])
  const stepHistory = useResource(
    stepId === null ? null : (signal) => endpoints.stepHistory(stepId, signal),
    [stepId],
  )

  const events = useMemo(() => {
    const list = history.data?.events ?? []
    return newestFirst ? [...list].reverse() : list
  }, [history.data, newestFirst])

  return (
    <div className="stack">
      <Panel
        title={`Task 历史(${history.data?.events.length ?? 0} 条 State Event)`}
        actions={
          <>
            <button type="button" onClick={() => setNewestFirst((value) => !value)}>
              {newestFirst ? '按 seq 升序' : '按 seq 降序'}
            </button>
            <button type="button" onClick={history.reload} disabled={history.status === 'loading'}>
              重新读取
            </button>
          </>
        }
      >
        {history.status === 'loading' && history.data === null ? <Loading what="Task 历史" /> : null}
        {history.error ? (
          <ErrorPanel error={history.error} what="Task 历史" onRetry={history.reload} />
        ) : null}
        {history.data && history.data.events.length === 0 ? <Empty what="State Event" /> : null}
        {events.length > 0 ? (
          <ol className="events">
            {events.map((event) => (
              <EventRow key={event.event_id} event={event} taskId={taskId} />
            ))}
          </ol>
        ) : null}
        {history.loadedAt ? (
          <StaleBanner loadedAt={history.loadedAt} scope={`Task ${taskId} 的历史`} />
        ) : null}
      </Panel>

      <Panel title="Step 历史">
        {steps.data && steps.data.steps.length === 0 ? (
          <Empty what="Step" hint="没有 Step 就没有 Step 历史。" />
        ) : null}
        {steps.data && steps.data.steps.length > 0 ? (
          <ul className="plain">
            {steps.data.steps.map((step) => (
              <li key={step.step_id}>
                <button
                  type="button"
                  aria-pressed={step.step_id === stepId}
                  onClick={() => setStepId(step.step_id === stepId ? null : step.step_id)}
                >
                  {step.step_id === stepId ? '收起' : '查看'}
                </button>{' '}
                <StatusBadge kind="step" status={step.status} /> {step.title}{' '}
                <code className="muted">{step.step_id}</code>
              </li>
            ))}
          </ul>
        ) : null}
        {stepId === null ? (
          <p className="muted">选择一个 Step 查看它的 State Event 序列。</p>
        ) : (
          <>
            {stepHistory.status === 'loading' && stepHistory.data === null ? (
              <Loading what="Step 历史" />
            ) : null}
            {stepHistory.error ? (
              <ErrorPanel error={stepHistory.error} what="Step 历史" onRetry={stepHistory.reload} />
            ) : null}
            {stepHistory.data ? (
              <>
                <p className="muted">
                  <code>{stepId}</code> · {stepHistory.data.events.length} 条 Event · 读取时间{' '}
                  {formatTimestamp(stepHistory.loadedAt)}
                </p>
                <ol className="events">
                  {[...stepHistory.data.events].reverse().map((event) => (
                    <EventRow key={event.event_id} event={event} taskId={taskId} />
                  ))}
                </ol>
                <p>
                  <a href={sourceHref.task(taskId, 'board')}>← 回到看板</a>
                </p>
              </>
            ) : null}
          </>
        )}
      </Panel>
    </div>
  )
}
