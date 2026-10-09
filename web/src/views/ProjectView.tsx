/**
 * Project view (P6-A03).
 *
 * Tasks come from `GET /v1/tasks?project_id=…` with real `limit`/`offset`
 * paging. The status filter only filters rows that have actually been loaded —
 * the label says so — because Core has no "count by status" endpoint and
 * inventing a total would be a fabricated number. "Last activity" is taken from
 * a real `GET /v1/events?project_id=…` read and carries its read time.
 */

import { useMemo, useState } from 'react'

import { DEFAULT_EVENT_LIMIT, DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Project, Task } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { offsetLoader, usePagedResource } from '../app/usePagedResource'
import { useResource } from '../app/useResource'
import { Pager } from '../components/Pager'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'
import { TaskCreatePanel } from './TaskWrites'

const LIMIT = DEFAULT_OBJECT_LIMIT
const TASK_FILTERS = ['ALL', 'ACTIVE', 'BLOCKED', 'ACCEPTED', 'CANCELLED'] as const
type TaskFilter = (typeof TASK_FILTERS)[number]

export function ProjectView({ projectId }: { projectId: string }): JSX.Element {
  const { endpoints } = useSession()
  const [filter, setFilter] = useState<TaskFilter>('ALL')

  const project = useResource((signal) => endpoints.getProject(projectId, signal), [projectId])
  const tasks = usePagedResource(
    offsetLoader<Task>({
      limit: LIMIT,
      fetch: async (offset, limit, signal) => {
        const page = await endpoints.listTasks(projectId, { limit, offset }, signal)
        return { items: page.tasks, count: page.count }
      },
    }),
    [projectId],
  )
  const sessions = usePagedResource(
    offsetLoader<{ session_id: string; status: string; task_id: string | null }>({
      limit: LIMIT,
      fetch: async (offset, limit, signal) => {
        const page = await endpoints.listSessions(projectId, { limit, offset }, signal)
        return {
          items: page.sessions.map((session) => ({
            session_id: session.session_id,
            status: session.status,
            task_id: session.task_id,
          })),
          count: page.count,
        }
      },
    }),
    [projectId],
  )
  const events = useResource(
    (signal) =>
      endpoints.listEvents({ project_id: projectId, limit: DEFAULT_EVENT_LIMIT }, signal),
    [projectId],
  )

  const visible = useMemo(
    () => (filter === 'ALL' ? tasks.items : tasks.items.filter((task) => task.status === filter)),
    [tasks.items, filter],
  )

  const lastEvent = useMemo(() => {
    const list = events.data?.events ?? []
    if (list.length === 0) return null
    return list.reduce((latest, event) => (event.seq > latest.seq ? event : latest), list[0]!)
  }, [events.data])

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <a href={sourceHref.conversation(null)}>Overview</a>
        <span aria-hidden="true">/</span>
        <span>Project</span>
      </nav>

      <Panel
        title={`Project ${projectId}`}
        actions={
          <button type="button" onClick={project.reload} disabled={project.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {project.status === 'loading' && project.data === null ? <Loading what="Project" /> : null}
        {project.error ? (
          <ErrorPanel error={project.error} what="Project" onRetry={project.reload} />
        ) : null}
        {project.data ? (
          <ProjectHeader project={project.data.project} />
        ) : null}
      </Panel>

      <Panel title={`Tasks(已载入范围内 ${tasks.items.length} 条)`}>
        <div className="filters" role="group" aria-label="按 Task 状态过滤已载入行">
          {TASK_FILTERS.map((candidate) => (
            <button
              key={candidate}
              type="button"
              aria-pressed={filter === candidate}
              onClick={() => setFilter(candidate)}
            >
              {candidate}
            </button>
          ))}
          <span className="muted">
            仅过滤已载入的 {tasks.items.length} 条;Core 没有按状态计数的端点,因此这里不给出总数。
          </span>
        </div>
        {tasks.loading && tasks.items.length === 0 ? <Loading what="Tasks" /> : null}
        {tasks.error && tasks.items.length === 0 ? (
          <ErrorPanel error={tasks.error} what="Tasks" onRetry={tasks.reload} />
        ) : null}
        {!tasks.loading && tasks.error === null && tasks.items.length === 0 ? (
          <Empty what="Tasks" hint="该 Project 没有 Task。" />
        ) : null}
        {tasks.items.length > 0 && visible.length === 0 ? (
          <Empty what={`状态为 ${filter} 的 Task(已载入范围内)`} />
        ) : null}
        {visible.length > 0 ? (
          <ul className="cards">
            {visible.map((task) => (
              <li key={task.task_id} className="card">
                <h3>
                  <a href={sourceHref.task(task.task_id)}>{task.title}</a>
                </h3>
                <p>
                  <StatusBadge kind="task" status={task.status} /> revision {task.revision} ·
                  criteria {task.acceptance_criteria.requirements.length} 项
                </p>
                <p className="muted mono">{task.task_id}</p>
                <p>
                  <a href={sourceHref.task(task.task_id, 'board')}>看板</a> ·{' '}
                  <a href={sourceHref.task(task.task_id, 'history')}>历史</a> ·{' '}
                  <a href={sourceHref.task(task.task_id, 'evidence')}>Evidence</a> ·{' '}
                  来源 <SourceLink kind="event" id={task.source_event_id} label="task.created" />
                </p>
              </li>
            ))}
          </ul>
        ) : null}
        <Pager paged={tasks} noun="Task" limit={LIMIT} />
      </Panel>

      <Panel title="Task 创建(P6-02 TaskStep 写入)">
        <p className="state state--meta">
          创建成功后重新读取本项目的 Task 列表;本页不做乐观插入。Core 创建 Task 时不读 revision,
          并显式拒绝 <code>expected_revision</code>。
        </p>
        <TaskCreatePanel projectId={projectId} onWritten={tasks.reload} />
      </Panel>

      <Panel title="Event 窗口(升序读取的前 100 条)">
        {events.status === 'loading' && events.data === null ? <Loading what="Events" /> : null}
        {events.error ? (
          <ErrorPanel error={events.error} what="Events" onRetry={events.reload} />
        ) : null}
        {events.data ? (
          events.data.events.length === 0 ? (
            <Empty what="Event" hint="本地没有采集到该项目的事件,不用本地对话填充。" />
          ) : (
            <>
              {lastEvent ? (
                <KeyValue
                  rows={[
                    { label: '最新 seq', value: lastEvent.seq },
                    { label: 'event_type', value: lastEvent.event_type },
                    {
                      label: 'occurred_at',
                      value: formatTimestamp(lastEvent.occurred_at),
                    },
                    {
                      label: '来源 Event',
                      value: (
                        <SourceLink kind="event" id={lastEvent.event_id} label={lastEvent.event_type} />
                      ),
                    },
                  ]}
                />
              ) : null}
              {events.loadedAt ? (
                <StaleBanner
                  loadedAt={events.loadedAt}
                  scope={`Project ${projectId} 的前 ${events.data.events.length} 条 Event(按 seq 升序)`}
                />
              ) : null}
              <p className="state state--meta">
                Core 按 <code>seq</code> 升序返回 Event,且本页只读取前 {DEFAULT_EVENT_LIMIT} 条。
                因此上面的 seq 是<strong>这一窗口内</strong>最大的一个,不是该项目全局的最新活动;
                事件超过 {DEFAULT_EVENT_LIMIT} 条时,更新的 Event 不在本读取范围内。Core 没有降序或
                “最新 N 条”参数,本页不会伪造一个。
              </p>
            </>
          )
        ) : null}
      </Panel>

      <Panel title="Sessions(已读范围内)">
        {sessions.items.length === 0 && !sessions.loading ? (
          <Empty what="Session" />
        ) : (
          <ul className="plain">
            {sessions.items.map((session) => (
              <li key={session.session_id}>
                <a href={sourceHref.conversation(projectId, session.task_id, session.session_id)}>
                  <code>{session.session_id}</code>
                </a>{' '}
                <StatusBadge kind="session" status={session.status} />
              </li>
            ))}
          </ul>
        )}
        <Pager paged={sessions} noun="Session" limit={LIMIT} />
        <p className="state state--meta">
          过滤在服务端完成:<code>GET /v1/sessions?project_id=…</code>;count 是本页长度,不是总数。
        </p>
      </Panel>

      <Panel title="Interpretation / Resolver">
        <p className="state state--meta">
          P2 契约已合并;本项目下的解释链可以只读查看,人工写入(correct/reject/rerun、
          approve/reject、Rule 管理)在各自的详情页交付,本页只做导航与只读概览。
        </p>
        <ul className="plain">
          <li>
            <a href={sourceHref.interpretations(projectId)}>本项目的 Interpretations</a>
          </li>
          <li>
            <a href={sourceHref.reviews(projectId)}>本项目的待审批 Review</a>
          </li>
        </ul>
      </Panel>
    </div>
  )
}

function ProjectHeader({ project }: { project: Project }): JSX.Element {
  return (
    <KeyValue
      rows={[
        { label: 'name', value: project.name },
        { label: 'project_id', value: <code>{project.project_id}</code> },
        { label: 'status', value: project.status },
        { label: 'revision', value: project.revision },
        { label: 'description', value: project.description || '—' },
        { label: 'created_at', value: formatTimestamp(project.created_at) },
        { label: 'updated_at', value: formatTimestamp(project.updated_at) },
        {
          label: 'source Event',
          value: <SourceLink kind="event" id={project.source_event_id} label="project.created" />,
        },
      ]}
    />
  )
}
