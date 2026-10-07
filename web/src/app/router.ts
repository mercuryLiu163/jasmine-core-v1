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
  | {
      name: 'interpretations'
      projectId: string | null
      taskId: string | null
      eventId: string | null
      status: string | null
    }
  | { name: 'interpretation'; interpretationId: string }
  | { name: 'reviews'; projectId: string | null; taskId: string | null }
  | { name: 'review'; reviewId: string }
  | { name: 'resolution'; resolutionId: string }
  // P6-03a: P3 continuity reads. The routes exist so a deep link is stable; the views
  // say DEPENDENCY PENDING until a P3-enabled proxy is running.
  | { name: 'checkpoints'; taskId: string | null; checkpointId: string | null; sessionId: string | null }
  | { name: 'context'; contextPackId: string }
  | { name: 'resume'; resumeId: string }
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
  if (head === 'interpretations') {
    return {
      name: 'interpretations',
      projectId: query.get('project_id'),
      taskId: query.get('task_id'),
      eventId: query.get('event_id'),
      status: query.get('status'),
    }
  }
  if (head === 'interpretation') {
    const interpretationId = segment(parts[1])
    return interpretationId ? { name: 'interpretation', interpretationId } : { name: 'unknown', raw }
  }
  if (head === 'reviews') {
    return { name: 'reviews', projectId: query.get('project_id'), taskId: query.get('task_id') }
  }
  if (head === 'review') {
    const reviewId = segment(parts[1])
    return reviewId ? { name: 'review', reviewId } : { name: 'unknown', raw }
  }
  if (head === 'resolution') {
    const resolutionId = segment(parts[1])
    return resolutionId ? { name: 'resolution', resolutionId } : { name: 'unknown', raw }
  }
  if (head === 'checkpoints') {
    return {
      name: 'checkpoints',
      taskId: query.get('task_id'),
      checkpointId: query.get('checkpoint_id'),
      sessionId: query.get('session_id'),
    }
  }
  if (head === 'context') {
    const contextPackId = segment(parts[1])
    return contextPackId ? { name: 'context', contextPackId } : { name: 'unknown', raw }
  }
  if (head === 'resume') {
    const resumeId = segment(parts[1])
    return resumeId ? { name: 'resume', resumeId } : { name: 'unknown', raw }
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
    case 'checkpoints':
      return `#/checkpoints${queryString({
        task_id: route.taskId,
        checkpoint_id: route.checkpointId,
        session_id: route.sessionId,
      })}`
    case 'context':
      return `#/context/${encodeURIComponent(route.contextPackId)}`
    case 'resume':
      return `#/resume/${encodeURIComponent(route.resumeId)}`
    case 'evidence':
      return `#/evidence${queryString({ evidence_id: route.evidenceId, task_id: route.taskId })}`
    case 'interpretations':
      return `#/interpretations${queryString({
        project_id: route.projectId,
        task_id: route.taskId,
        event_id: route.eventId,
        status: route.status,
      })}`
    case 'interpretation':
      return `#/interpretation/${encodeURIComponent(route.interpretationId)}`
    case 'reviews':
      return `#/reviews${queryString({ project_id: route.projectId, task_id: route.taskId })}`
    case 'review':
      return `#/review/${encodeURIComponent(route.reviewId)}`
    case 'resolution':
      return `#/resolution/${encodeURIComponent(route.resolutionId)}`
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
  interpretations: (
    projectId: string | null,
    taskId: string | null = null,
    eventId: string | null = null,
    status: string | null = null,
  ): string => hrefFor({ name: 'interpretations', projectId, taskId, eventId, status }),
  interpretation: (interpretationId: string): string =>
    hrefFor({ name: 'interpretation', interpretationId }),
  /** P3 continuity (P6-03a). `latest` is addressed by task, a specific one by id. */
  checkpoints: (
    taskId: string | null,
    checkpointId: string | null = null,
    sessionId: string | null = null,
  ): string => hrefFor({ name: 'checkpoints', taskId, checkpointId, sessionId }),
  contextPack: (contextPackId: string): string =>
    hrefFor({ name: 'context', contextPackId }),
  resume: (resumeId: string): string => hrefFor({ name: 'resume', resumeId }),
  review: (reviewId: string): string => hrefFor({ name: 'review', reviewId }),
  reviews: (projectId: string | null, taskId: string | null = null): string =>
    hrefFor({ name: 'reviews', projectId, taskId }),
  resolution: (resolutionId: string): string => hrefFor({ name: 'resolution', resolutionId }),
}
