/**
 * P6-03b1 — the read-only Memory views: health, jobs, items and traces.
 *
 * **DEPENDENCY PENDING, and P4 phase acceptance has not passed.** The seven routes exist at
 * the pinned P4 commit; the baseline Core has none of them. These views render whatever
 * answered — a P4 Core, or the labelled simulated fixture used for the Gate — and the
 * standing status line says which slice this is regardless.
 *
 * Four things this file is careful about, each because the alternative is a lie:
 *
 * 1. **Nothing here writes.** There is no retry, no obsolete/delete/pin/unpin, and no
 *    reflect control: those are `memory:manage` / `memory:reflect` and belong to P6-03b2,
 *    which needs its own permission and audit Gate. The views are read-only by
 *    construction, not by hiding a button.
 * 2. **Memory is marked NON_AUTHORITATIVE** by reading Core's own `non_authoritative`
 *    field, not by printing a claim that would survive Core stopping sending it.
 * 3. **Empty, not-found, refused and pending are four different states.** An actor with no
 *    grants gets an empty list; an unauthorised project answers `not_found`; a token
 *    without `memory:read` gets `forbidden_scope`; a proxy that does not forward the path
 *    says so. None of them may be rendered as another.
 * 4. **A malformed id is refused locally**, naming the field, with no request sent — so a
 *    typo is never explained as a missing dependency.
 */

import { useState } from 'react'

import type { CoreApiError } from '../api/errors'
import type {
  MemoryHealth,
  MemoryItem,
  MemoryItemListRow,
  MemoryJob,
  MemoryJobCounts,
  MemoryTrace,
  MemoryTraceDetail,
} from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { RawText } from '../components/RawText'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { isCoreId } from '../lib/ids'
import { formatTimestamp } from '../lib/text'
import {
  Hash,
  MemoryDependencyPending,
  MemoryNotFound,
  MemoryInvalidId,
  MemorySliceStatus,
  MemoryUnusedInvalidId,
  NonAuthoritative,
  isMemoryPending,
} from './MemoryDependency'

/** Every route in this slice requires exactly this scope; P4 adds no helper scopes. */
const READ_SCOPES = ['memory:read'] as const

type BadId = { prefix: string; field: string; value: string }

/** The shared shape of a Memory view: pick the read, then render one of the four states. */
function MemoryPanel<T>({
  title,
  what,
  method,
  path,
  scopes,
  badId,
  unusedBadIds = [],
  resource,
  isEmpty,
  emptyHint,
  children,
}: {
  title: string
  what: string
  method: string
  path: string
  scopes: readonly string[]
  badId: BadId | null
  unusedBadIds?: readonly BadId[]
  resource: {
    status: string
    data: T | null
    error: CoreApiError | null
    loadedAt: string | null
    reload: () => void
  }
  isEmpty?: (data: T) => boolean
  emptyHint?: string
  children: (data: T) => React.ReactNode
}): JSX.Element {
  const pending = isMemoryPending(resource.error)
  const empty = badId === null && resource.data !== null && isEmpty?.(resource.data) === true
  return (
    <Panel title={title}>
      <div className="stack">
        <MemorySliceStatus />
        {badId ? <MemoryInvalidId {...badId} /> : null}
        {badId === null
          ? unusedBadIds.map((unused) => <MemoryUnusedInvalidId key={unused.field} {...unused} />)
          : null}

        {badId === null && pending ? (
          <MemoryDependencyPending what={what} method={method} path={path} scopes={scopes} />
        ) : null}

        {badId === null && !pending && resource.status === 'loading' && resource.data === null ? (
          <Loading what={what} />
        ) : null}

        {/* A 404 is Core answering, not a failure: P4 uses it for "absent" and "not yours"
            alike, so it gets its own state rather than an error banner with a retry that can
            never succeed. Every other error is a real failure and says so. */}
        {badId === null && !pending && resource.error !== null && !isMemoryPending(resource.error)
          ? resource.error.kind === 'missing'
            ? <MemoryNotFound what={what} />
            : <ErrorPanel error={resource.error} what={what} onRetry={resource.reload} />
          : null}

        {empty ? (
          <Empty what={what} hint={emptyHint ?? '这是一个空集,不是错误,也不是记录不存在。'} />
        ) : null}

        {badId === null && !pending && resource.data !== null && !empty
          ? children(resource.data)
          : null}

        {resource.loadedAt ? <StaleBanner loadedAt={resource.loadedAt} scope={title} /> : null}
        <button type="button" onClick={resource.reload}>
          从 Core 重新读取
        </button>
      </div>
    </Panel>
  )
}

/** The id lookup form every by-id view needs, with local shape validation. */
function MemoryIdForm({
  label,
  prefix,
  draft,
  setDraft,
  onSubmit,
}: {
  label: string
  prefix: string
  draft: string
  setDraft: (value: string) => void
  onSubmit: () => void
}): JSX.Element {
  const invalid = draft.trim() !== '' && !isCoreId(draft.trim(), prefix)
  return (
    <form
      className="row"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <label>
        {label}
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={`${prefix}_…`}
          aria-invalid={invalid}
        />
      </label>
      {invalid ? (
        <span className="muted" data-testid="form-invalid">
          不是 {prefix}_ 形状的 id
        </span>
      ) : null}
      <button type="submit" disabled={draft.trim() === '' || invalid}>
        打开
      </button>
    </form>
  )
}

/* -------------------------------------------------------------------------- *
 * Health
 * -------------------------------------------------------------------------- */

export function MemoryHealthView(): JSX.Element {
  const { endpoints } = useSession()
  const resource = useResource<MemoryHealth>((signal) => endpoints.memoryHealth(signal), [])
  return (
    <MemoryPanel
      title="Memory 健康"
      what="Memory health"
      method="GET"
      path="/v1/memory/health"
      scopes={READ_SCOPES}
      badId={null}
      resource={resource}
    >
      {(health) => (
        <>
          <NonAuthoritative payload={health} />
          <KeyValue
            rows={[
              { label: 'status', value: <code>{health.status}</code> },
              {
                label: 'core_impact',
                value: (
                  <>
                    <code>{health.core_impact}</code>
                    {health.core_impact !== 'none' ? (
                      <span className="muted"> —— Memory 已暂停,Core 本身仍在工作</span>
                    ) : null}
                  </>
                ),
              },
              { label: 'configured', value: String(health.configured) },
              { label: 'reachable', value: health.reachable === null ? '未知(未探测)' : String(health.reachable) },
              { label: 'degraded', value: String(health.degraded) },
              { label: 'engine', value: <code>{health.engine_id ?? '—'}</code> },
              { label: 'api_version', value: <code>{health.api_version ?? '—'}</code> },
              { label: 'origin_host', value: <code>{health.origin_host ?? '—'}</code> },
              { label: 'last_check_at', value: formatTimestamp(health.last_check_at) },
              { label: 'last_success_at', value: formatTimestamp(health.last_success_at) },
              { label: 'last_error_code', value: <code>{health.last_error_code ?? '—'}</code> },
              { label: 'checked_at', value: formatTimestamp(health.checked_at) },
            ]}
          />
          <KeyValue
            rows={[
              {
                label: 'jobs',
                value: (
                  <span className="stack">
                    {Object.entries(health.jobs).map(([key, value]) => (
                      <span key={key}>
                        <code>{key}</code> {String(value)}
                      </span>
                    ))}
                  </span>
                ),
              },
              {
                label: 'items',
                value: (
                  <span className="stack">
                    {Object.entries(health.items).map(([key, value]) => (
                      <span key={key}>
                        <code>{key}</code> {String(value)}
                      </span>
                    ))}
                  </span>
                ),
              },
            ]}
          />
          <JsonBlock value={health.features} label="features" />
          <JsonBlock value={health.deployment} label="deployment" />
        </>
      )}
    </MemoryPanel>
  )
}

/* -------------------------------------------------------------------------- *
 * Jobs
 * -------------------------------------------------------------------------- */

function JobTable({ jobs }: { jobs: readonly MemoryJob[] }): JSX.Element {
  return (
    <table className="table" data-testid="memory-jobs">
      <thead>
        <tr>
          <th>job_id</th>
          <th>kind</th>
          <th>status</th>
          <th>memory_id</th>
          <th>updated_at</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.job_id}>
            <td>
              <a href={hrefFor({ name: 'memoryJob', jobId: job.job_id })}>
                <code>{job.job_id}</code>
              </a>
            </td>
            <td>{job.kind}</td>
            <td>{job.status}</td>
            <td>
              <a href={hrefFor({ name: 'memoryItem', memoryId: job.memory_id })}>
                <code>{job.memory_id}</code>
              </a>
            </td>
            <td>{formatTimestamp(job.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MemoryJobsView({ jobId }: { jobId: string | null }): JSX.Element {
  const { endpoints } = useSession()
  const [draft, setDraft] = useState('')
  const badJobId = jobId !== null && !isCoreId(jobId, 'mjb') ? { prefix: 'mjb', field: 'job_id', value: jobId } : null
  const detail = useResource(
    badJobId === null && jobId !== null
      ? (signal) => endpoints.getMemoryJob(jobId, signal)
      : null,
    [jobId],
  )
  const list = useResource(
    jobId === null ? (signal) => endpoints.listMemoryJobs({}, signal) : null,
    [],
  )

  if (jobId !== null) {
    return (
      <MemoryPanel
        title="Memory Job"
        what="Memory job"
        method="GET"
        path="/v1/memory/jobs/{mjb}"
        scopes={READ_SCOPES}
        badId={badJobId}
        resource={detail}
      >
        {({ job, attempts }) => (
          <>
            <MemoryJobBody job={job} />
            <h3>attempts({attempts.length})</h3>
            {attempts.length === 0 ? (
              <p className="muted">没有 attempt 记录。</p>
            ) : (
              <JsonBlock value={attempts} label="attempts" startsOpen />
            )}
            <MemoryIdForm
              label="打开另一个 job"
              prefix="mjb"
              draft={draft}
              setDraft={setDraft}
              onSubmit={() => {
                globalThis.location.hash = hrefFor({ name: 'memoryJob', jobId: draft.trim() })
              }}
            />
          </>
        )}
      </MemoryPanel>
    )
  }

  return (
    <MemoryPanel
      title="Memory Jobs"
      what="Memory jobs"
      method="GET"
      path="/v1/memory/jobs"
      scopes={READ_SCOPES}
      badId={null}
      resource={list}
      isEmpty={(data) => data.jobs.length === 0}
      emptyHint="这个 actor 没有任何可见的 job。P4 对无授权的 actor 返回空列表,而不是整个数据库。"
    >
      {(data) => (
        <>
          <MemoryJobCountsRow counts={data.counts} />
          <JobTable jobs={data.jobs} />
          <MemoryIdForm
            label="按 ID 打开 job"
            prefix="mjb"
            draft={draft}
            setDraft={setDraft}
            onSubmit={() => {
              globalThis.location.hash = hrefFor({ name: 'memoryJob', jobId: draft.trim() })
            }}
          />
        </>
      )}
    </MemoryPanel>
  )
}

function MemoryJobCountsRow({ counts }: { counts: MemoryJobCounts }): JSX.Element {
  return (
    <KeyValue
      rows={[
        {
          label: 'counts',
          value: (
            <span className="stack">
              {Object.entries(counts).map(([key, value]) => (
                <span key={key}>
                  <code>{key}</code> {String(value)}
                </span>
              ))}
            </span>
          ),
        },
      ]}
    />
  )
}

function MemoryJobBody({ job }: { job: MemoryJob }): JSX.Element {
  return (
    <>
      <KeyValue
        rows={[
          { label: 'job_id', value: <code>{job.job_id}</code> },
          { label: 'kind', value: <code>{job.kind}</code> },
          { label: 'status', value: <code>{job.status}</code> },
          { label: 'memory_type', value: <code>{job.memory_type}</code> },
          { label: 'memory_id', value: <Link kind="memoryItem" id={job.memory_id} /> },
          { label: 'source Event', value: <SourceLink kind="event" id={job.source_event_id} label="source Event" /> },
          { label: 'project', value: <Link kind="project" id={job.project_id} /> },
          { label: 'task', value: <Link kind="task" id={job.task_id} /> },
          { label: 'bank_id', value: <code>{job.bank_id}</code> },
          { label: 'retry', value: <code>{job.retry_count} / {job.max_attempts}</code> },
          { label: 'claim_generation', value: <code>{job.claim_generation}</code> },
          { label: 'next_attempt_at', value: formatTimestamp(job.next_attempt_at) },
          { label: 'completed_at', value: formatTimestamp(job.completed_at) },
          { label: 'last_error_code', value: <code>{job.last_error_code ?? '—'}</code> },
        ]}
      />
      {job.safe_error_summary ? (
        <RawText value={job.safe_error_summary} label="safe_error_summary" machine={false} />
      ) : null}
      <KeyValue
        rows={[{ label: 'content_sha256', value: <Hash label="sha256" value={job.content_sha256} /> }]}
      />
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Items
 * -------------------------------------------------------------------------- */

function ItemTable({ items }: { items: readonly MemoryItemListRow[] }): JSX.Element {
  return (
    <table className="table" data-testid="memory-items">
      <thead>
        <tr>
          <th>memory_id</th>
          <th>memory_type</th>
          <th>status</th>
          <th>engine_state</th>
          <th>revision</th>
          <th>source</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.memory_id}>
            <td>
              <a href={hrefFor({ name: 'memoryItem', memoryId: item.memory_id })}>
                <code>{item.memory_id}</code>
              </a>
            </td>
            <td>{item.memory_type}</td>
            <td>{item.status}</td>
            <td>{item.engine_state}</td>
            <td>{item.revision}</td>
            {/* P4 keeps provenance: the Event this memory was derived from stays reachable. */}
            <td>
              <SourceLink kind="event" id={item.source_event_id} label="source Event" />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MemoryItemsView({ memoryId }: { memoryId: string | null }): JSX.Element {
  const { endpoints } = useSession()
  const [draft, setDraft] = useState('')
  const badMemoryId =
    memoryId !== null && !isCoreId(memoryId, 'mem') ? { prefix: 'mem', field: 'memory_id', value: memoryId } : null
  const detail = useResource(
    badMemoryId === null && memoryId !== null
      ? (signal) => endpoints.getMemoryItem(memoryId, signal)
      : null,
    [memoryId],
  )
  const list = useResource(
    memoryId === null ? (signal) => endpoints.listMemoryItems({}, signal) : null,
    [],
  )

  if (memoryId !== null) {
    return (
      <MemoryPanel
        title="Memory Item"
        what="Memory item"
        method="GET"
        path="/v1/memory/items/{mem}"
        scopes={READ_SCOPES}
        badId={badMemoryId}
        resource={detail}
      >
        {(payload) => (
          <>
            <NonAuthoritative payload={payload} />
            <MemoryItemBody item={payload.memory} />
            <KeyValue
              rows={[
                { label: 'recallable', value: String(payload.recallable) },
                {
                  label: 'lifecycle',
                  value: (
                    <span className="stack">
                      {Object.entries(payload.lifecycle).map(([key, value]) => (
                        <span key={key}>
                          <code>{key}</code> {value === null ? '—' : String(value)}
                        </span>
                      ))}
                    </span>
                  ),
                },
                { label: 'engine_claim', value: payload.engine_claim },
              ]}
            />
            <JsonBlock value={payload.sources} label={`sources(${String((payload.sources as unknown[]).length)})`} />
            <JsonBlock value={payload.jobs} label={`jobs(${String(payload.jobs.length)})`} />
            <MemoryIdForm
              label="打开另一个 item"
              prefix="mem"
              draft={draft}
              setDraft={setDraft}
              onSubmit={() => {
                globalThis.location.hash = hrefFor({ name: 'memoryItem', memoryId: draft.trim() })
              }}
            />
          </>
        )}
      </MemoryPanel>
    )
  }

  return (
    <MemoryPanel
      title="Memory Items"
      what="Memory items"
      method="GET"
      path="/v1/memory/items"
      scopes={READ_SCOPES}
      badId={null}
      resource={list}
      isEmpty={(data) => data.items.length === 0}
      emptyHint="这个 actor 没有任何可见的 memory item。P4 对无授权的 actor 返回空列表,而不是整个数据库。"
    >
      {(data) => (
        <>
          <NonAuthoritative payload={data} />
          <ItemTable items={data.items} />
          <MemoryIdForm
            label="按 ID 打开 item"
            prefix="mem"
            draft={draft}
            setDraft={setDraft}
            onSubmit={() => {
              globalThis.location.hash = hrefFor({ name: 'memoryItem', memoryId: draft.trim() })
            }}
          />
        </>
      )}
    </MemoryPanel>
  )
}

function MemoryItemBody({ item }: { item: MemoryItem }): JSX.Element {
  return (
    <>
      <KeyValue
        rows={[
          { label: 'memory_id', value: <code>{item.memory_id}</code> },
          { label: 'memory_type', value: <code>{item.memory_type}</code> },
          { label: 'status', value: <code>{item.status}</code> },
          { label: 'engine_state', value: <code>{item.engine_state}</code> },
          { label: 'revision', value: <code>r{item.revision}</code> },
          { label: 'scope', value: <code>{item.scope_kind}</code> },
          { label: 'project', value: <Link kind="project" id={item.project_id} /> },
          { label: 'task', value: <Link kind="task" id={item.task_id} /> },
          { label: 'bank_id', value: <code>{item.bank_id}</code> },
          { label: 'source Event', value: <SourceLink kind="event" id={item.source_event_id} label="source Event" /> },
          { label: 'origin_event_id', value: <code>{item.origin_event_id ?? '—'}</code> },
          { label: 'origin_rule', value: <code>{item.origin_rule_id ? `${item.origin_rule_id} v${String(item.origin_rule_version)}` : '—'}</code> },
          { label: 'pinned', value: String(item.pinned) },
          { label: 'recall_count', value: String(item.recall_count) },
          { label: 'last_recalled_at', value: formatTimestamp(item.last_recalled_at) },
          { label: 'lifecycle_reason', value: item.lifecycle_reason ?? '—' },
          { label: 'last_error_code', value: <code>{item.last_error_code ?? '—'}</code> },
          { label: 'remote_deleted_at', value: formatTimestamp(item.remote_deleted_at) },
          { label: 'created_at', value: formatTimestamp(item.created_at) },
          { label: 'updated_at', value: formatTimestamp(item.updated_at) },
        ]}
      />
      <KeyValue
        rows={[
          { label: 'content_sha256', value: <Hash label="sha256" value={item.content_sha256} /> },
          { label: 'evidence_ids', value: <code>{item.evidence_ids.length} 条</code> },
        ]}
      />
      {/* P4 withholds this from a non-human actor by nulling the value and keeping the key,
          so rendering it conditionally is what makes the withholding visible rather than
          papered over with a placeholder. */}
      {item.safe_error_summary ? (
        <RawText value={item.safe_error_summary} label="safe_error_summary" machine={false} />
      ) : null}
      {/* The stored text is Core's own sanitized copy; it renders as text, never markup. */}
      <RawText value={item.sanitized_text} label="sanitized_text" machine={false} />
      <JsonBlock value={item.evidence_ids} label="evidence_ids" />
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Traces
 * -------------------------------------------------------------------------- */

function TraceTable({ traces }: { traces: readonly MemoryTrace[] }): JSX.Element {
  return (
    <table className="table" data-testid="memory-traces">
      <thead>
        <tr>
          <th>trace_id</th>
          <th>mode</th>
          <th>status</th>
          <th>query_length</th>
          <th>used_tokens</th>
          <th>created_at</th>
        </tr>
      </thead>
      <tbody>
        {traces.map((trace) => (
          <tr key={trace.trace_id}>
            <td>
              <a href={hrefFor({ name: 'memoryTrace', traceId: trace.trace_id })}>
                <code>{trace.trace_id}</code>
              </a>
            </td>
            <td>{trace.mode}</td>
            <td>{trace.status}</td>
            <td>{trace.query_length}</td>
            <td>{trace.used_tokens}</td>
            <td>{formatTimestamp(trace.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MemoryTracesView({ traceId }: { traceId: string | null }): JSX.Element {
  const { endpoints } = useSession()
  const [draft, setDraft] = useState('')
  const badTraceId =
    traceId !== null && !isCoreId(traceId, 'mtr') ? { prefix: 'mtr', field: 'trace_id', value: traceId } : null
  const detail = useResource(
    badTraceId === null && traceId !== null
      ? (signal) => endpoints.getMemoryTrace(traceId, signal)
      : null,
    [traceId],
  )
  const list = useResource(
    traceId === null ? (signal) => endpoints.listMemoryTraces({}, signal) : null,
    [],
  )

  if (traceId !== null) {
    return (
      <MemoryPanel
        title="Memory Trace"
        what="Memory trace"
        method="GET"
        path="/v1/memory/traces/{mtr}"
        scopes={READ_SCOPES}
        badId={badTraceId}
        resource={detail}
      >
        {({ trace }) => (
          <>
            <MemoryTraceBody trace={trace} />
            <JsonBlock value={trace.items} label={`items(${String(trace.items.length)})`} startsOpen />
            <MemoryIdForm
              label="打开另一个 trace"
              prefix="mtr"
              draft={draft}
              setDraft={setDraft}
              onSubmit={() => {
                globalThis.location.hash = hrefFor({ name: 'memoryTrace', traceId: draft.trim() })
              }}
            />
          </>
        )}
      </MemoryPanel>
    )
  }

  return (
    <MemoryPanel
      title="Memory Traces"
      what="Memory traces"
      method="GET"
      path="/v1/memory/traces"
      scopes={READ_SCOPES}
      badId={null}
      resource={list}
      isEmpty={(data) => data.traces.length === 0}
      emptyHint="没有 recall/reflect trace。P4 不存储查询原文,只存 hash 与长度。"
    >
      {(data) => (
        <>
          <TraceTable traces={data.traces} />
          <MemoryIdForm
            label="按 ID 打开 trace"
            prefix="mtr"
            draft={draft}
            setDraft={setDraft}
            onSubmit={() => {
              globalThis.location.hash = hrefFor({ name: 'memoryTrace', traceId: draft.trim() })
            }}
          />
        </>
      )}
    </MemoryPanel>
  )
}

function MemoryTraceBody({ trace }: { trace: MemoryTraceDetail }): JSX.Element {
  return (
    <KeyValue
      rows={[
        { label: 'trace_id', value: <code>{trace.trace_id}</code> },
        { label: 'mode', value: <code>{trace.mode}</code> },
        { label: 'status', value: <code>{trace.status}</code> },
        { label: 'actor', value: <code>{trace.actor_id}</code> },
        { label: 'project', value: <Link kind="project" id={trace.project_id} /> },
        { label: 'task', value: <Link kind="task" id={trace.task_id} /> },
        { label: 'session', value: <code>{trace.session_id ?? '—'}</code> },
        // P4 stores the query hash and length, never the query text; showing a "query" field
        // here would be inventing one.
        { label: 'query_sha256', value: <Hash label="sha256" value={trace.query_sha256} /> },
        { label: 'query_length', value: String(trace.query_length) },
        { label: 'budget_tokens', value: trace.budget_tokens === null ? '—' : String(trace.budget_tokens) },
        { label: 'used_tokens', value: String(trace.used_tokens) },
        { label: 'state_revision', value: trace.state_revision === null ? '—' : `r${String(trace.state_revision)}` },
        { label: 'error_code', value: <code>{trace.error_code ?? '—'}</code> },
        { label: 'created_at', value: formatTimestamp(trace.created_at) },
      ]}
    />
  )
}

/* -------------------------------------------------------------------------- *
 * Small shared link helper
 * -------------------------------------------------------------------------- */

/** A project/task/memory link that renders `—` rather than a broken href when null. */
function Link({
  kind,
  id,
}: {
  kind: 'memoryItem' | 'project' | 'task'
  id: string | null
}): JSX.Element {
  if (id === null) return <>—</>
  const href =
    kind === 'memoryItem'
      ? hrefFor({ name: 'memoryItem', memoryId: id })
      : kind === 'project'
        ? sourceHref.project(id)
        : sourceHref.task(id)
  return (
    <a href={href}>
      <code>{id}</code>
    </a>
  )
}
