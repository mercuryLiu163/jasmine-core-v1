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
  Interpretation,
  InterpretationPage,
  EventList,
  EventPage,
  Evidence,
  EvidencePage,
  Health,
  Host,
  MetaSchema,
  Project,
  ProjectPage,
  Resolution,
  ResolutionPreview,
  RawEvent,
  Review,
  ReviewPage,
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

    listSessions: (projectId: string | null, window: Window, signal?: AbortSignal) =>
      client.get<SessionPage>('/sessions', {
        // Core supports project_id on the sessions collection
        // (`objects.ObjectStore.list`), so the filter belongs on the server: a
        // client-side filter over one page reports a wrong "no sessions" state.
        query: { project_id: projectId, limit: window.limit, offset: window.offset },
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

    // --- P2 read slice -------------------------------------------------
    // There is no `GET /v1/reviews` or `GET /v1/resolutions` collection: the
    // only collection reads are `interpretations` and `reviews/pending`, both
    // cursor-paged with `after_id` and returning `next_after_id` (no count).
    listInterpretations: (
      query: {
        event_id?: string | null
        project_id?: string | null
        task_id?: string | null
        status?: string | null
        after_id?: string | null
        limit?: number
      },
      signal?: AbortSignal,
    ) =>
      client.get<InterpretationPage>('/interpretations', {
        query: {
          event_id: query.event_id,
          project_id: query.project_id,
          task_id: query.task_id,
          status: query.status,
          after_id: query.after_id,
          limit: query.limit ?? DEFAULT_OBJECT_LIMIT,
        },
        signal,
      }),
    getInterpretation: (interpretationId: string, signal?: AbortSignal) =>
      client.get<{ interpretation: Interpretation }>(
        `/interpretations/${id(interpretationId)}`,
        { signal },
      ),
    listPendingReviews: (
      query: { project_id?: string | null; task_id?: string | null; after_id?: string | null; limit?: number },
      signal?: AbortSignal,
    ) =>
      client.get<ReviewPage>('/reviews/pending', {
        query: {
          project_id: query.project_id,
          task_id: query.task_id,
          after_id: query.after_id,
          limit: query.limit ?? DEFAULT_OBJECT_LIMIT,
        },
        signal,
      }),
    getReview: (reviewId: string, signal?: AbortSignal) =>
      client.get<{ review: Review }>(`/reviews/${id(reviewId)}`, { signal }),
    previewResolution: (interpretationId: string, signal?: AbortSignal) =>
      client.get<ResolutionPreview>('/resolutions/preview', {
        query: { interpretation_id: interpretationId },
        signal,
      }),
    getResolution: (resolutionId: string, signal?: AbortSignal) =>
      client.get<{ resolution: Resolution }>(`/resolutions/${id(resolutionId)}`, { signal }),

    listHosts: (signal?: AbortSignal) => client.get<{ hosts: Host[] }>('/hosts', { signal }),
    listActors: (signal?: AbortSignal) => client.get<{ actors: Actor[] }>('/actors', { signal }),
  }
}

export type Endpoints = ReturnType<typeof makeEndpoints>
