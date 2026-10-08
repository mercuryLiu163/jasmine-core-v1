/**
 * Overview (P6-A03).
 *
 * Shows only what the baseline API can support: Core liveness and schema, the
 * registered hosts/actors as *static registry rows*, and a paged project list.
 * Counts are always qualified with the loaded range, and the panels for
 * Hindsight, Backup, adapter health and device online state say "未接入" instead
 * of printing a zero or a green dot.
 */

import { useMemo, useState } from 'react'

import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Project } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { usePagedResource, offsetLoader } from '../app/usePagedResource'
import { useResource } from '../app/useResource'
import { KeyValue, SourceLink } from '../components/Trace'
import { Pager } from '../components/Pager'
import { TableScroll } from '../components/TableScroll'
import { Empty, ErrorPanel, Loading, Panel, Unavailable } from '../components/States'
import { formatTimestamp } from '../lib/text'

const LIMIT = DEFAULT_OBJECT_LIMIT

export function OverviewView(): JSX.Element {
  const { endpoints } = useSession()

  const health = useResource((signal) => endpoints.health(signal), [])
  const schema = useResource((signal) => endpoints.metaSchema(signal), [])
  const hosts = useResource((signal) => endpoints.listHosts(signal), [])
  const actors = useResource((signal) => endpoints.listActors(signal), [])

  const projects = usePagedResource(
    offsetLoader<Project>({
      limit: LIMIT,
      fetch: async (offset, limit, signal) => {
        const page = await endpoints.listProjects({ limit, offset }, signal)
        return { items: page.projects, count: page.count }
      },
    }),
    [],
  )

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const selected = useMemo(
    () => projects.items.find((project) => project.project_id === selectedProjectId) ?? null,
    [projects.items, selectedProjectId],
  )

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Overview</span>
      </nav>

      <Panel
        title="Core 健康与 Schema"
        actions={
          <button type="button" onClick={() => { health.reload(); schema.reload() }}>
            重新读取
          </button>
        }
      >
        {health.status === 'loading' && health.data === null ? <Loading what="健康" /> : null}
        {health.error ? <ErrorPanel error={health.error} what="健康" onRetry={health.reload} /> : null}
        {health.data ? (
          <KeyValue
            rows={[
              { label: 'status', value: health.data.status },
              { label: 'core_version', value: health.data.core_version },
              { label: 'schema_version', value: health.data.schema_version },
              { label: '读取时间', value: formatTimestamp(health.loadedAt) },
            ]}
          />
        ) : null}
        {schema.error ? (
          <ErrorPanel error={schema.error} what="schema" onRetry={schema.reload} />
        ) : null}
        {schema.data ? (
          <details>
            <summary>
              已应用迁移 {schema.data.migrations.length} 条(expected v{schema.data.expected_schema_version})
            </summary>
            <ul className="plain">
              {schema.data.migrations.map((migration) => (
                <li key={migration.name}>
                  v{migration.version} <code>{migration.name}</code>{' '}
                  <span className="muted">{migration.applied_at}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        <p className="state state--meta">
          这里只证明 Core 的 liveness 与 schema。它不证明 Hindsight、Backup 或 Adapter
          的健康;那三类在下面各自标为未接入。
        </p>
      </Panel>

      <Panel
        title={`Projects(已载入 ${projects.items.length} 条)`}
        actions={
          <button type="button" onClick={projects.reload} disabled={projects.loading}>
            重新读取
          </button>
        }
      >
        {projects.loading && projects.items.length === 0 ? <Loading what="Projects" /> : null}
        {projects.error && projects.items.length === 0 ? (
          <ErrorPanel error={projects.error} what="Projects" onRetry={projects.reload} />
        ) : null}
        {!projects.loading && !projects.error && projects.items.length === 0 ? (
          <Empty what="Projects" hint="空库为空,不是错误。" />
        ) : null}
        {projects.items.length > 0 ? (
          <TableScroll label="Project 列表">
          <table className="table">
            <caption className="sr-only">Project 列表</caption>
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">状态</th>
                <th scope="col">revision</th>
                <th scope="col">来源 Event</th>
                <th scope="col">updated_at</th>
                <th scope="col">选中</th>
              </tr>
            </thead>
            <tbody>
              {projects.items.map((project) => (
                <tr
                  key={project.project_id}
                  className={project.project_id === selectedProjectId ? 'is-selected' : undefined}
                >
                  <th scope="row">
                    <a href={sourceHref.project(project.project_id)}>{project.name}</a>
                    <div className="muted mono">{project.project_id}</div>
                  </th>
                  <td>{project.status}</td>
                  <td>{project.revision}</td>
                  <td>
                    <SourceLink kind="event" id={project.source_event_id} label="created" />
                  </td>
                  <td>{formatTimestamp(project.updated_at)}</td>
                  <td>
                    <button
                      type="button"
                      aria-pressed={project.project_id === selectedProjectId}
                      onClick={() => setSelectedProjectId(project.project_id)}
                    >
                      {project.project_id === selectedProjectId ? '已选中' : '选中'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>
        ) : null}
        <Pager paged={projects} noun="Project" limit={LIMIT} />
        {selected ? (
          <p>
            已选中 <strong>{selected.name}</strong>
            <code className="mono">{selected.project_id}</code>{' '}
            <a href={sourceHref.project(selected.project_id)}>打开 Project 视图 →</a>
          </p>
        ) : null}
      </Panel>

      <Panel
        title="Registry(静态身份)"
        actions={
          <button type="button" onClick={() => { hosts.reload(); actors.reload() }}>
            重新读取
          </button>
        }
      >
        {hosts.status === 'loading' && hosts.data === null ? <Loading what="Hosts" /> : null}
        {hosts.error ? <ErrorPanel error={hosts.error} what="Hosts" onRetry={hosts.reload} /> : null}
        {hosts.data ? (
          hosts.data.hosts.length === 0 ? (
            <Empty what="Hosts" />
          ) : (
            <ul className="plain">
              {hosts.data.hosts.map((host) => (
                <li key={host.host_id}>
                  <code>{host.host_id}</code> {host.display_name} · {host.kind} ·
                  first_seen {formatTimestamp(host.first_seen_at)} · last_seen{' '}
                  {formatTimestamp(host.last_seen_at)}
                </li>
              ))}
            </ul>
          )
        ) : null}
        {actors.status === 'loading' && actors.data === null ? <Loading what="Actors" /> : null}
        {actors.error ? (
          <ErrorPanel error={actors.error} what="Actors" onRetry={actors.reload} />
        ) : null}
        {actors.data ? (
          <p className="muted">
            已注册 actor {actors.data.actors.length}:{' '}
            {actors.data.actors.map((actor) => `${actor.display_name}(${actor.kind})`).join('、')}
          </p>
        ) : null}
        <p className="state state--meta">
          这些是静态注册行。它们不是 heartbeat、不是 online/capabilities;在线状态在 P5 交付前显示为未实现。
        </p>
      </Panel>

      <Panel title="未接入的领域能力">
        <Unavailable
          feature="Hindsight / Memory 健康"
          owner="P4 独立线"
          pending="读切片(health/jobs/items/traces)已实现但 P4 未合并,列为依赖未满足;retry 与生命周期管理 API 仍待交付"
        />
        <Unavailable
          feature="Backup / Storage / Devices"
          owner="P5 / P7"
          pending="Dashboard 读取契约与真实健康戳"
        />
        <Unavailable
          feature="Registry online/capabilities"
          owner="P5"
          pending="heartbeat 读 API"
          note="hosts 列表不能当作在线状态;超时与未知必须区别于离线。"
        />
      </Panel>
    </div>
  )
}
