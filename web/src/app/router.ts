/**
 * Hash routing.
 *
 * The dashboard uses `#/…` rather than history routing for three reasons:
 * a deep link to a source Event must survive a refresh without server rewrites,
 * the static bundle needs no SPA fallback configuration, and no part of a route
 * can put state (least of all a token) into a request line that a server or
 * proxy might log.
 *
 * `parseHash` is total: it never throws and never silently maps an unknown
 * route onto the Overview, because "I opened the wrong URL" and "everything is
 * fine" must not look the same.
 */

export type TaskTab = 'board' | 'history' | 'evidence'

export type Route =
  | { name: 'overview' }
  | { name: 'project'; projectId: string }
  | { name: 'task'; taskId: string; tab: TaskTab }
  | {
      name: 'conversation'
      projectId: string | null
      taskId: string | null
      sessionId: string | null
    }
  | { name: 'rules'; projectId: string | null; taskId: string | null; ruleId: string | null }
  | { name: 'evidence'; evidenceId: string | null; taskId: string | null }
  | { name: 'event'; eventId: string }
  | { name: 'pending'; key: string }
  | { name: 'unknown'; raw: string }

const TABS: readonly TaskTab[] = ['board', 'history', 'evidence']

function queryOf(search: string): URLSearchParams {
  return new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
}

function segment(value: string | undefined): string | null {
  if (value === undefined) return null
  const decoded = decodeURIComponent(value)
  return decoded.length > 0 ? decoded : null
}

export function parseHash(hash: string): Route {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash
  const [pathPart = '', searchPart = ''] = raw.split('?', 2)
  const parts = pathPart.split('/').filter((piece) => piece.length > 0)
  const head = parts[0]
  const query = queryOf(searchPart ?? '')

  if (head === undefined || head === 'overview') return { name: 'overview' }
  if (head === 'project') {
    const projectId = segment(parts[1])
    return projectId ? { name: 'project', projectId } : { name: 'unknown', raw }
  }
  if (head === 'task') {
    const taskId = segment(parts[1])
    const tab = segment(parts[2])
    if (!taskId) return { name: 'unknown', raw }
    if (tab !== null && !TABS.includes(tab as TaskTab)) return { name: 'unknown', raw }
    return { name: 'task', taskId, tab: (tab as TaskTab | null) ?? 'board' }
  }
  if (head === 'conversation') {
    return {
      name: 'conversation',
      projectId: query.get('project_id'),
      taskId: query.get('task_id'),
      sessionId: query.get('session_id'),
    }
  }
  if (head === 'rules') {
    return {
      name: 'rules',
      projectId: query.get('project_id'),
      taskId: query.get('task_id'),
      ruleId: query.get('rule_id'),
    }
  }
  if (head === 'evidence') {
    return { name: 'evidence', evidenceId: query.get('evidence_id'), taskId: query.get('task_id') }
  }
  if (head === 'event') {
    const eventId = segment(parts[1])
    return eventId ? { name: 'event', eventId } : { name: 'unknown', raw }
  }
  if (head === 'pending') {
    const key = segment(parts[1])
    return key ? { name: 'pending', key } : { name: 'unknown', raw }
  }
  return { name: 'unknown', raw }
}

function queryString(pairs: Record<string, string | null>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(pairs)) {
    if (value !== null && value !== '') params.set(key, value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case 'overview':
      return '#/overview'
    case 'project':
      return `#/project/${encodeURIComponent(route.projectId)}`
    case 'task':
      return `#/task/${encodeURIComponent(route.taskId)}${route.tab === 'board' ? '' : `/${route.tab}`}`
    case 'conversation':
      return `#/conversation${queryString({
        project_id: route.projectId,
        task_id: route.taskId,
        session_id: route.sessionId,
      })}`
    case 'rules':
      return `#/rules${queryString({
        project_id: route.projectId,
        task_id: route.taskId,
        rule_id: route.ruleId,
      })}`
    case 'evidence':
      return `#/evidence${queryString({ evidence_id: route.evidenceId, task_id: route.taskId })}`
    case 'event':
      return `#/event/${encodeURIComponent(route.eventId)}`
    case 'pending':
      return `#/pending/${encodeURIComponent(route.key)}`
    case 'unknown':
      return `#/${route.raw}`
  }
}

/** Deep-linkable source links used by trace chips throughout the UI. */
export const sourceHref = {
  project: (projectId: string): string => hrefFor({ name: 'project', projectId }),
  task: (taskId: string, tab: TaskTab = 'board'): string => hrefFor({ name: 'task', taskId, tab }),
  step: (taskId: string, tab: TaskTab = 'board'): string => hrefFor({ name: 'task', taskId, tab }),
  event: (eventId: string): string => hrefFor({ name: 'event', eventId }),
  rule: (ruleId: string, projectId: string | null = null, taskId: string | null = null): string =>
    hrefFor({ name: 'rules', projectId, taskId, ruleId }),
  evidence: (evidenceId: string, taskId: string | null = null): string =>
    hrefFor({ name: 'evidence', evidenceId, taskId }),
  conversation: (
    projectId: string | null,
    taskId: string | null = null,
    sessionId: string | null = null,
  ): string => hrefFor({ name: 'conversation', projectId, taskId, sessionId }),
}
