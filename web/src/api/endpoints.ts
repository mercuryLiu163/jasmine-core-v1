/**
 * The frozen P6-01 endpoint surface.
 *
 * Only paths that exist in the baseline Core build appear in the baseline sections.
 * Anything P4/P7 will add (memory jobs, validation runs) is *not* stubbed with a
 * plausible-looking call: those views render an explicit "未接入" panel instead, so a
 * UI bug can never look like a missing backend feature or vice versa.
 *
 * The P6-03a **P3 continuity reads** are the one exception, and a deliberate one: the
 * four paths are real (derived from branch `p3-checkpoint-context-resume` and frozen in
 * `web/contracts/p3-continuity-read.v1.json`), but they do not exist in this baseline's
 * Core. They are therefore kept out of the proxy allowlist's live section, so a baseline
 * run cannot forward them to a Core with no such route, and a P3-enabled proxy has to be
 * started deliberately. Until then the views render `DEPENDENCY PENDING`.
 */

import type { CoreClient, QueryValue } from './client'
import type {
  Actor,
  Checkpoint,
  ContextPack,
  JsonObject,
  Interpretation,
  InterpretationPage,
  EventList,
  EventPage,
  Evidence,
  EvidencePage,
  Health,
  Host,
  MemoryHealth,
  MemoryItem,
  MemoryItemListRow,
  MemoryJob,
  MemoryJobAttempt,
  MemoryJobCounts,
  MemoryLifecycle,
  MemoryLifecycleCommand,
  MemoryReflectResult,
  MemoryTrace,
  MemoryTraceDetail,
  MemoryWriteResult,
  MetaSchema,
  Project,
  ProjectPage,
  Resolution,
  ResolutionPreview,
  RawEvent,
  Resume,
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
  ValidationArtifact,
  ValidationArtifactContent,
  ValidationManifest,
  ValidationRunDetail,
  ValidationRunPage,
} from './types'

/**
 * Every P6-02B command body. Deliberately loose at the type level: Core validates
 * the exact field set, and inventing per-endpoint shapes here is how guessed
 * fields creep in. The panels build these objects from values Core returned.
 */
export type WriteBody = Record<string, unknown>

/** Core's write responses carry the command Event plus the replayed flag. */
export interface WriteResult {
  result?: JsonObject
  event?: RawEvent
  rule?: JsonObject
  interpretation?: JsonObject
  resolution?: JsonObject
  review?: JsonObject
  replayed?: boolean
  [key: string]: unknown
}

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

    // --- P6-02 milestone B: audited human writes -----------------------
    // Each call sends the caller's real key; Core enforces scope, actor kind,
    // idempotency (`idempotency_key`), revision and provenance. The UI never
    // retries with a fresh key and never computes a disposition itself.
    correctInterpretation: (interpretationId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/interpretations/${id(interpretationId)}/correct`, body, { signal }),
    rejectInterpretation: (interpretationId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/interpretations/${id(interpretationId)}/reject`, body, { signal }),
    rerunInterpretation: (interpretationId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/interpretations/${id(interpretationId)}/rerun`, body, { signal }),
    approveReview: (reviewId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/reviews/${id(reviewId)}/approve`, body, { signal }),
    rejectReview: (reviewId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/reviews/${id(reviewId)}/reject`, body, { signal }),
    proposeRule: (body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>('/rules/proposals', body, { signal }),
    approveRule: (ruleId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/rules/${id(ruleId)}/approve`, body, { signal }),
    supersedeRule: (ruleId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/rules/${id(ruleId)}/supersede`, body, { signal }),
    retireRule: (ruleId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<WriteResult>(`/rules/${id(ruleId)}/retire`, body, { signal }),

    // --- P6-03a: P3 continuity reads (DEPENDENCY PENDING) ---------------------
    // Read-only slice: Checkpoint (latest + by id), Context Pack (by id), Resume (by
    // id). The build/write shapes (`POST /v1/context/build`, `POST /v1/checkpoints`,
    // `POST /v1/tasks/{id}/resume`) are deliberately absent — this slice reads.
    //
    // A response of `{checkpoint: null}` from `checkpoints/latest` is an *empty* result
    // ("this Task has no checkpoint yet"), not an error, and the view says so.
    getCheckpoint: (checkpointId: string, signal?: AbortSignal) =>
      client.get<{ checkpoint: Checkpoint }>(`/checkpoints/${id(checkpointId)}`, { signal }),
    latestCheckpoint: (taskId: string, sessionId: string | null, signal?: AbortSignal) =>
      client.get<{ checkpoint: Checkpoint | null }>(`/tasks/${id(taskId)}/checkpoints/latest`, {
        query: { session_id: sessionId },
        signal,
      }),
    getContextPack: (contextPackId: string, signal?: AbortSignal) =>
      client.get<{ context: ContextPack }>(`/context/${id(contextPackId)}`, { signal }),
    getResume: (resumeId: string, signal?: AbortSignal) =>
      client.get<{ resume: Resume }>(`/resumes/${id(resumeId)}`, { signal }),

    // P6-03b1: P4 memory reads. Seven GETs, all `memory:read`, all DEPENDENCY PENDING —
    // the baseline Core has no /v1/memory/* route, so the proxy refuses them by name until
    // an operator points it at a P4 Core with P6_ENABLE_P4_READS=1. There is deliberately
    // no retry, no lifecycle command and no reflect here: those are writes (P6-03b2).
    memoryHealth: (signal?: AbortSignal) =>
      client.get<MemoryHealth>('/memory/health', { signal }),
    listMemoryJobs: (
      filters: { project_id?: string | null; status?: string | null } = {},
      signal?: AbortSignal,
    ) =>
      client.get<{ jobs: MemoryJob[]; count: number; counts: MemoryJobCounts }>(
        '/memory/jobs',
        { query: { project_id: filters.project_id, status: filters.status }, signal },
      ),
    getMemoryJob: (jobId: string, signal?: AbortSignal) =>
      client.get<{ job: MemoryJob; attempts: MemoryJobAttempt[] }>(
        `/memory/jobs/${id(jobId)}`,
        { signal },
      ),
    listMemoryItems: (
      filters: { project_id?: string | null; status?: string | null } = {},
      signal?: AbortSignal,
    ) =>
      client.get<{ items: MemoryItemListRow[]; count: number; non_authoritative: boolean }>(
        '/memory/items',
        { query: { project_id: filters.project_id, status: filters.status }, signal },
      ),
    getMemoryItem: (memoryId: string, signal?: AbortSignal) =>
      client.get<{
        memory: MemoryItem
        sources: unknown[]
        jobs: MemoryJob[]
        recallable: boolean
        lifecycle: MemoryLifecycle
        engine_claim: string
        non_authoritative: boolean
      }>(`/memory/items/${id(memoryId)}`, { signal }),
    listMemoryTraces: (
      filters: { project_id?: string | null; task_id?: string | null } = {},
      signal?: AbortSignal,
    ) =>
      client.get<{ traces: MemoryTrace[]; count: number }>('/memory/traces', {
        query: { project_id: filters.project_id, task_id: filters.task_id },
        signal,
      }),
    getMemoryTrace: (traceId: string, signal?: AbortSignal) =>
      client.get<{ trace: MemoryTraceDetail }>(`/memory/traces/${id(traceId)}`, { signal }),

    // P6-03b2: the six memory writes. These are P4 routes on the same unmerged commit as the
    // reads, and they are gated by their own default-off switch (`P6_ENABLE_P4_WRITES`), not
    // by `P6_ENABLE_P4_READS`: reading Memory and changing it are different decisions with
    // different scopes. Bodies are built by `api/memoryWrites.ts`, which cannot emit a field
    // the frozen contract does not list. `event_id` is added by `useWriteAction` so a retry
    // reuses the same replay key.
    retryMemoryJob: (jobId: string, body: WriteBody, signal?: AbortSignal) =>
      client.post<MemoryWriteResult>(`/memory/jobs/${id(jobId)}/retry`, body, { signal }),
    memoryLifecycle: (
      memoryId: string,
      command: MemoryLifecycleCommand,
      body: WriteBody,
      signal?: AbortSignal,
    ) =>
      client.post<MemoryWriteResult>(`/memory/items/${id(memoryId)}/${command}`, body, { signal }),
    reflectMemory: (body: WriteBody, signal?: AbortSignal) =>
      client.post<MemoryReflectResult>('/memory/reflect', body, { signal }),

    // P6-03c: the P7-01 Validation reads. Five GETs, all `validation:read` (plus
    // `validation:unattributed` on Core's side for a run with no project attribution), all
    // DEPENDENCY PENDING — the baseline Core has no /v1/validation/* route, so the proxy
    // refuses them by name until an operator points it at a Core carrying the read slice
    // with P6_ENABLE_P7_READS=1. The slice is read-only: no write is added here.
    //
    // The list filters are Core's own (`verdict`/`stage`/`mode`/`attribution`/`project`); a
    // null filter is omitted from the query string rather than sent as an empty value, and
    // Core refuses an unknown value with `400 invalid_request`.
    listValidationRuns: (
      query: {
        limit?: number
        offset?: number
        verdict?: string | null
        stage?: string | null
        mode?: string | null
        attribution?: string | null
        project?: string | null
      } = {},
      signal?: AbortSignal,
    ) =>
      client.get<ValidationRunPage>('/validation/runs', {
        query: {
          limit: query.limit,
          offset: query.offset,
          verdict: query.verdict,
          stage: query.stage,
          mode: query.mode,
          attribution: query.attribution,
          project: query.project,
        },
        signal,
      }),
    getValidationRun: (runId: string, signal?: AbortSignal) =>
      client.get<ValidationRunDetail>(`/validation/runs/${id(runId)}`, { signal }),
    getValidationManifest: (runId: string, signal?: AbortSignal) =>
      client.get<ValidationManifest>(`/validation/runs/${id(runId)}/manifest`, { signal }),
    getValidationArtifact: (runId: string, artifactId: string, signal?: AbortSignal) =>
      client.get<ValidationArtifact>(
        `/validation/runs/${id(runId)}/artifacts/${id(artifactId)}`,
        { signal },
      ),
    // `form` defaults to `masked` on Core; the caller still sends it explicitly, because a
    // default that lives only in Core is one a UI can forget it did not choose.
    getValidationArtifactContent: (
      runId: string,
      artifactId: string,
      form: 'masked' | 'original',
      signal?: AbortSignal,
    ) =>
      client.get<ValidationArtifactContent>(
        `/validation/runs/${id(runId)}/artifacts/${id(artifactId)}/content`,
        { query: { form }, signal },
      ),

    listHosts: (signal?: AbortSignal) => client.get<{ hosts: Host[] }>('/hosts', { signal }),
    listActors: (signal?: AbortSignal) => client.get<{ actors: Actor[] }>('/actors', { signal }),
  }
}

export type Endpoints = ReturnType<typeof makeEndpoints>
