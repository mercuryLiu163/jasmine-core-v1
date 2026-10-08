/**
 * Task board (P6-A04) — the kanban columns are **Step** statuses.
 *
 * The Task's own status (ACTIVE/BLOCKED/ACCEPTED/CANCELLED) is displayed once,
 * above the board, and is never folded into the columns. Step statuses are
 * grouped by the board vocabulary in `StatusBadge.tsx`; a status this build does
 * not know gets its own column rather than being dropped into "other" or hidden.
 *
 * The summary line exists to stop the board from implying "all done": when any
 * Step is EXECUTED, STALE, FAILED or BLOCKED, it is named there in text.
 */

import { useMemo } from 'react'

import type { Step, Task } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { BOARD_COLUMNS, StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'

const TERMINAL = new Set(['ACCEPTED', 'SKIPPED'])

export function TaskBoard({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const task = useResource((signal) => endpoints.getTask(taskId, signal), [taskId])
  const steps = useResource((signal) => endpoints.listSteps(taskId, signal), [taskId])

  const byStatus = useMemo(() => {
    const map = new Map<string, Step[]>()
    for (const step of steps.data?.steps ?? []) {
      const bucket = map.get(step.status)
      if (bucket) bucket.push(step)
      else map.set(step.status, [step])
    }
    return map
  }, [steps.data])

  const extraColumns = useMemo(
    () => [...byStatus.keys()].filter((status) => !BOARD_COLUMNS.includes(status)).sort(),
    [byStatus],
  )

  const outstanding = useMemo(() => {
    const counts = new Map<string, number>()
    for (const step of steps.data?.steps ?? []) {
      if (TERMINAL.has(step.status)) continue
      counts.set(step.status, (counts.get(step.status) ?? 0) + 1)
    }
    return counts
  }, [steps.data])

  const allTerminal = steps.data !== null && steps.data.steps.length > 0 && outstanding.size === 0

  return (
    <div className="stack">
      {task.error ? (
        <ErrorPanel error={task.error} what="Task" onRetry={task.reload} />
      ) : null}
      {task.data ? <TaskSummary task={task.data.task} /> : null}
      {task.status === 'loading' && task.data === null ? <Loading what="Task" /> : null}

      <Panel
        title={`Step 看板(${steps.data?.steps.length ?? 0} 个 Step)`}
        actions={
          <button type="button" onClick={steps.reload} disabled={steps.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {steps.status === 'loading' && steps.data === null ? <Loading what="Steps" /> : null}
        {steps.error ? <ErrorPanel error={steps.error} what="Steps" onRetry={steps.reload} /> : null}
        {steps.data && steps.data.steps.length === 0 ? (
          <Empty what="Step" hint="该 Task 还没有 Step;看板列会全部为空。" />
        ) : null}
        {steps.data && steps.data.steps.length > 0 ? (
          <>
            <p className="board__summary" data-testid="board-summary">
              {allTerminal
                ? `全部 ${steps.data.steps.length} 个 Step 都处于终态(ACCEPTED/SKIPPED)。`
                : `未到终态: ${[...outstanding.entries()]
                    .map(([status, count]) => `${status}×${count}`)
                    .join('、')}。`}
            </p>
            <div className="board" role="list">
              {[...BOARD_COLUMNS, ...extraColumns].map((status) => {
                const columnSteps = byStatus.get(status) ?? []
                return (
                  <section
                    className="board__column"
                    key={status}
                    role="listitem"
                    aria-label={`Step 状态 ${status}`}
                    data-status={status}
                  >
                    <header className="board__column-head">
                      <StatusBadge kind="step" status={status} />
                      <span className="board__count">{columnSteps.length}</span>
                    </header>
                    {columnSteps.length === 0 ? (
                      <p className="board__empty muted">—</p>
                    ) : (
                      <ul className="board__cards">
                        {columnSteps.map((step) => (
                          <StepCard key={step.step_id} step={step} />
                        ))}
                      </ul>
                    )}
                  </section>
                )
              })}
            </div>
            {extraColumns.length > 0 ? (
              <p className="state state--error" role="alert">
                出现了本构建不认识的 Step 状态:{extraColumns.join('、')}。已原样单独成列,不会被归并或当作健康。
              </p>
            ) : null}
            {steps.loadedAt ? (
              <StaleBanner
                loadedAt={steps.loadedAt}
                scope={`Task ${taskId} 的 Step 列表`}
              />
            ) : null}
          </>
        ) : null}
      </Panel>
    </div>
  )
}

function TaskSummary({ task }: { task: Task }): JSX.Element {
  const requirements = task.acceptance_criteria.requirements
  return (
    <Panel title="Task 状态(与 Step 列分开)">
      <div className="taskhead">
        <h1>{task.title}</h1>
        <StatusBadge kind="task" status={task.status} />
      </div>
      <dl className="kv kv--compact">
        <div>
          <dt>task_id</dt>
          <dd>
            <code>{task.task_id}</code>
          </dd>
        </div>
        <div>
          <dt>revision</dt>
          <dd data-testid="task-revision">{task.revision}</dd>
        </div>
        <div>
          <dt>project</dt>
          <dd>
            <a href={sourceHref.project(task.project_id)}>
              <code>{task.project_id}</code>
            </a>
          </dd>
        </div>
        <div>
          <dt>source Event</dt>
          <dd>
            <SourceLink kind="event" id={task.source_event_id} label="task.created" />
          </dd>
        </div>
        <div>
          <dt>updated_at</dt>
          <dd>{formatTimestamp(task.updated_at)}</dd>
        </div>
      </dl>
      {task.description ? <p>{task.description}</p> : null}
      <h3>Task acceptance criteria({requirements.length} 项)</h3>
      {requirements.length === 0 ? (
        <p className="muted">Task 级没有 requirements;Step 级 criteria 在各自卡片上。</p>
      ) : (
        <ul className="plain">
          {requirements.map((requirement) => (
            <li key={requirement.key}>
              <code>{requirement.key}</code> · {requirement.kind} → {requirement.required_result}
              {requirement.tool_name ? ` · tool ${requirement.tool_name}` : ''}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function StepCard({ step }: { step: Step }): JSX.Element {
  const requirements = step.acceptance_criteria.requirements
  return (
    <li className="stepcard" data-step-id={step.step_id}>
      <h4>{step.title}</h4>
      <p>
        <StatusBadge kind="step" status={step.status} /> revision {step.revision}
      </p>
      <p className="muted">
        criteria {requirements.length} 项 · updated {formatTimestamp(step.updated_at)}
      </p>
      <p className="muted mono">{step.step_id}</p>
      <p className="trace-row">
        <SourceLink kind="event" id={step.source_event_id} label="step.created" />
        {/* Core has no per-Step page; Step history is read on the history tab
            after selecting this Step, so the label says where it goes. */}
        <a href={sourceHref.task(step.task_id, 'history')} title="在历史页选择该 Step 查看 State Event">
          历史(选择此 Step)
        </a>
      </p>
    </li>
  )
}
