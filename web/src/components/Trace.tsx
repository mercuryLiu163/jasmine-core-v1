/**
 * Source-trace links.
 *
 * Every ID the UI shows is a link back to the object or Event it came from, so
 * P6-T01 ("Project → Task → Step history → Evidence → tool source Event") is a
 * sequence of real navigations rather than a claim. Links are plain anchors to
 * `#/…` routes: they work with the keyboard, can be opened in a new tab, and a
 * refreshed deep link re-reads the same Core object.
 */

import type { TaskTab } from '../app/router'
import { sourceHref } from '../app/router'
import { cls } from '../lib/classNames'

export type TraceKind = 'project' | 'task' | 'step' | 'event' | 'rule' | 'evidence' | 'conversation'

const GLYPH: Record<TraceKind, string> = {
  project: '▣',
  task: '▤',
  step: '▥',
  event: '≡',
  rule: '§',
  evidence: '⚑',
  conversation: '💬',
}

export function SourceLink({
  kind,
  id,
  tab = 'board',
  taskId = null,
  projectId = null,
  sessionId = null,
  label,
}: {
  kind: TraceKind
  id: string
  tab?: TaskTab
  taskId?: string | null
  projectId?: string | null
  sessionId?: string | null
  label?: string
}): JSX.Element {
  const href = ((): string => {
    switch (kind) {
      case 'project':
        return sourceHref.project(id)
      case 'task':
      case 'step':
        return sourceHref.task(id, tab)
      case 'event':
        return sourceHref.event(id)
      case 'rule':
        return sourceHref.rule(id, projectId, taskId)
      case 'evidence':
        return sourceHref.evidence(id, taskId)
      case 'conversation':
        return sourceHref.conversation(id, taskId, sessionId)
    }
  })()

  return (
    <a className={cls('trace', `trace--${kind}`)} href={href} data-kind={kind} data-id={id}>
      <span aria-hidden="true" className="trace__glyph">
        {GLYPH[kind]}
      </span>
      <span className="trace__label">{label ?? kind}</span>
      <code className="trace__id">{id}</code>
    </a>
  )
}

export function TraceList({
  items,
}: {
  items: ReadonlyArray<{
    kind: TraceKind
    id: string
    label?: string
    tab?: TaskTab
    taskId?: string | null
    projectId?: string | null
  }>
}): JSX.Element {
  if (items.length === 0) return <span className="muted">—</span>
  return (
    <span className="trace-list">
      {items.map((item) => (
        <SourceLink
          key={`${item.kind}:${item.id}`}
          kind={item.kind}
          id={item.id}
          label={item.label}
          tab={item.tab}
          taskId={item.taskId ?? null}
          projectId={item.projectId ?? null}
        />
      ))}
    </span>
  )
}

export function KeyValue({
  rows,
  compact = false,
}: {
  rows: ReadonlyArray<{ label: string; value: React.ReactNode }>
  compact?: boolean
}): JSX.Element {
  return (
    <dl className={cls('kv', compact && 'kv--compact')}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}
