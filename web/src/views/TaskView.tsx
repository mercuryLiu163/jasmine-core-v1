import { WebConfirmationView } from './WebConfirmationView'
/**
 * Task shell with three deep-linkable tabs.
 *
 * Tabs are anchors to real routes (`#/task/{id}/board|history|evidence`), so the
 * current view survives a refresh and can be shared. The tab strip uses
 * `aria-current` rather than colour alone.
 */

import type { TaskTab } from '../app/router'
import { sourceHref } from '../app/router'
import { TaskBoard } from './TaskBoard'
import { TaskEvidence } from './TaskEvidence'
import { TaskHistory } from './TaskHistory'
import { TaskWrites } from './TaskWrites'

const TABS: ReadonlyArray<{ key: TaskTab; label: string; hint: string }> = [
  { key: 'board', label: '看板', hint: '按 Step 状态分列' },
  { key: 'history', label: '历史', hint: 'Task/Step State Event' },
  { key: 'evidence', label: 'Evidence', hint: '判定依据与 criteria 矩阵' },
  { key: 'confirmation', label: '人工确认', hint: 'Core 准备材料与独立 INFO 确认' },
  { key: 'write', label: '写入', hint: 'Task/Step 的冻结审计写(P6-02 TaskStep)' },
]

export function TaskView({ taskId, tab }: { taskId: string; tab: TaskTab }): JSX.Element {
  return (
    <div className="stack">
      <nav className="tabs" aria-label="Task 视图">
        {TABS.map((candidate) => (
          <a
            key={candidate.key}
            href={sourceHref.task(taskId, candidate.key)}
            aria-current={candidate.key === tab ? 'page' : undefined}
            title={candidate.hint}
          >
            {candidate.label}
          </a>
        ))}
      </nav>
      {tab === 'board' ? <TaskBoard taskId={taskId} /> : null}
      {tab === 'history' ? <TaskHistory taskId={taskId} /> : null}
      {tab === 'evidence' ? <TaskEvidence taskId={taskId} /> : null}
      {tab === 'confirmation' ? <WebConfirmationView key={taskId} taskId={taskId} /> : null}
      {tab === 'write' ? <TaskWrites taskId={taskId} /> : null}
    </div>
  )
}
