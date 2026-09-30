/**
 * The frozen P6-01 endpoint surface.
 *
 * Only paths that exist in the baseline Core build appear here. Anything P2/P3/
 * P4/P7 will add (interpretations, checkpoints, context packs, memory jobs,
 * validation runs) is *not* stubbed with a plausible-looking call: those views
 * render an explicit "未接入" panel instead, so a UI bug can never look like a
 * missing backend feature or vice versa.
 */

import type { CoreClient, QueryValue } from './client'
import type {
  Actor,
  EventList,
  EventPage,
  Evidence,
  EvidencePage,
  Health,
  Host,
  MetaSchema,
  Project,
  ProjectPage,
  RawEvent,
  Rule,
  RuleHistoryEntry,
  RulePage,
  SessionPage,
  Step,
  StepList,
  Task,
  TaskPage,
} from './types'

export const DEFAULT_OBJECT_LIMIT = 50
export const DEFAULT_EVENT_LIMIT = 100

export interface Window {
  limit: number
  offset: number
}

export interface EventQuery {
  session_id?: string | null
  task_id?: string | null
  project_id?: string | null
  event_type?: string | null
  source_system?: string | null
  after_seq?: number | null
  limit?: number
}

function id(value: string): string {
  return encodeURIComponent(value)
}

export function makeEndpoints(client: CoreClient) {
  return {
    health: (signal?: AbortSignal) => client.get<Health>('/health', { signal }),
    metaSchema: (signal?: AbortSignal) => client.get<MetaSchema>('/meta/schema', { signal }),

    listProjects: (window: Window, signal?: AbortSignal) =>
      client.get<ProjectPage>('/projects', {
        query: { limit: window.limit, offset: window.offset },
        signal,
      }),
    getProject: (projectId: string, signal?: AbortSignal) =>
      client.get<{ project: Project }>(`/projects/${id(projectId)}`, { signal }),

    listTasks: (projectId: string | null, window: Window, signal?: AbortSignal) =>
      client.get<TaskPage>('/tasks', {
        query: { project_id: projectId, limit: window.limit, offset: window.offset },
        signal,
      }),
    getTask: (taskId: string, signal?: AbortSignal) =>
      client.get<{ task: Task }>(`/tasks/${id(taskId)}`, { signal }),

    listSteps: (taskId: string, signal?: AbortSignal) =>
      client.get<StepList>(`/tasks/${id(taskId)}/steps`, { signal }),
    getStep: (stepId: string, signal?: AbortSignal) =>
      client.get<{ step: Step }>(`/steps/${id(stepId)}`, { signal }),
    taskHistory: (taskId: string, signal?: AbortSignal) =>
      client.get<EventList>(`/tasks/${id(taskId)}/history`, { signal }),
    stepHistory: (stepId: string, signal?: AbortSignal) =>
      client.get<EventList>(`/steps/${id(stepId)}/history`, { signal }),

    listSessions: (window: Window, signal?: AbortSignal) =>
      client.get<SessionPage>('/sessions', {
        query: { limit: window.limit, offset: window.offset },
        signal,
      }),

    listEvents: (query: EventQuery, signal?: AbortSignal) => {
      const params: Record<string, QueryValue> = {
        session_id: query.session_id,
        task_id: query.task_id,
        project_id: query.project_id,
        event_type: query.event_type,
        source_system: query.source_system,
        after_seq: query.after_seq,
        limit: query.limit ?? DEFAULT_EVENT_LIMIT,
      }
      return client.get<EventPage>('/events', { query: params, signal })
    },
    getEvent: (eventId: string, signal?: AbortSignal) =>
      client.get<{ event: RawEvent }>(`/events/${id(eventId)}`, { signal }),

    listActiveRules: (
      scope: { project_id?: string | null; task_id?: string | null },
      signal?: AbortSignal,
    ) =>
      client.get<RulePage>('/rules/active', {
        query: { project_id: scope.project_id, task_id: scope.task_id },
        signal,
      }),
    getRule: (ruleId: string, signal?: AbortSignal) =>
      client.get<{ rule: Rule }>(`/rules/${id(ruleId)}`, { signal }),
    getRuleHistory: (ruleId: string, signal?: AbortSignal) =>
      client.get<{ history: RuleHistoryEntry[] }>(`/rules/${id(ruleId)}/history`, { signal }),

    listTaskEvidence: (taskId: string, signal?: AbortSignal) =>
      client.get<EvidencePage>(`/tasks/${id(taskId)}/evidence`, { signal }),
    getEvidence: (evidenceId: string, signal?: AbortSignal) =>
      client.get<{ evidence: Evidence }>(`/evidence/${id(evidenceId)}`, { signal }),

    listHosts: (signal?: AbortSignal) => client.get<{ hosts: Host[] }>('/hosts', { signal }),
    listActors: (signal?: AbortSignal) => client.get<{ actors: Actor[] }>('/actors', { signal }),
  }
}

export type Endpoints = ReturnType<typeof makeEndpoints>
