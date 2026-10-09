/**
 * Task Evidence tab (P6-A04/A06).
 *
 * Three panels, deliberately separated by authority:
 *
 * 1. "Core 记录的判定依据" — the `evidence_ids` Core wrote onto each
 *    VERIFIED/ACCEPTED transition. This is the authoritative answer to "what
 *    satisfied the gate", taken from State history rather than recomputed here.
 * 2. "Evidence 列表" — every `evd_` row for the task with its `kind`, `status`
 *    (PASS/FAIL/INFO), `reference_status` and fingerprint. INFO is labelled as
 *    INFO, never shown as PASS.
 * 3. "criteria → Evidence 参考矩阵" — the P6-02 item 5 read-only candidate matrix
 *    (`CriteriaEvidenceMatrix`). It computes only the browser-readable half of Core's
 *    `_fresh` predicate and names the F1/F2/F8/F9d/F9e residual; it is never a verdict.
 *    Core remains the only judge (a request that does not qualify still returns
 *    `422 missing_evidence`).
 */

import { useMemo } from 'react'

import type { RawEvent } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { TableScroll } from '../components/TableScroll'
import { SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'
import { CriteriaEvidenceMatrix } from './CriteriaEvidenceMatrix'

interface GateRecord {
  stepId: string | null
  to: string
  eventId: string
  evidenceIds: string[]
  ruleVersions: unknown
  occurredAt: string
}

function idsFrom(...values: unknown[]): string[] {
  const collected = values.flatMap((value) => (Array.isArray(value) ? value : []))
  return [...new Set(collected.filter((value): value is string => typeof value === 'string'))]
}

/**
 * Core's authoritative "what satisfied the gate" records.
 *
 * They are written on more than one event type and all of them must be read:
 * `step.transitioned` carries `to: VERIFIED|ACCEPTED` plus `evidence_ids`;
 * `task.accepted` (`state.py: accept_task`) is the acceptance command and carries
 * `evidence_ids` both at the payload top level and under `result`; a plain
 * `task.transitioned` never carries evidence. Reading only the first two would
 * leave a real `POST /v1/tasks/{id}/accept` invisible behind an empty panel.
 */
function gateRecords(events: RawEvent[]): GateRecord[] {
  const records: GateRecord[] = []
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>
    const nested = payload['result']
    const result =
      typeof nested === 'object' && nested !== null ? (nested as Record<string, unknown>) : {}

    if (event.event_type === 'task.accepted') {
      records.push({
        stepId: null,
        to: 'ACCEPTED',
        eventId: event.event_id,
        evidenceIds: idsFrom(payload['evidence_ids'], result['evidence_ids']),
        ruleVersions: result['rule_versions'] ?? payload['rule_versions'] ?? null,
        occurredAt: event.occurred_at,
      })
      continue
    }
    if (event.event_type !== 'step.transitioned' && event.event_type !== 'task.transitioned') continue
    const to = payload['to']
    if (to !== 'VERIFIED' && to !== 'ACCEPTED') continue
    records.push({
      stepId: typeof payload['step_id'] === 'string' ? payload['step_id'] : null,
      to: String(to),
      eventId: event.event_id,
      evidenceIds: idsFrom(payload['evidence_ids'], result['evidence_ids']),
      ruleVersions: result['rule_versions'] ?? payload['rule_versions'] ?? null,
      occurredAt: event.occurred_at,
    })
  }
  return records
}

export function TaskEvidence({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const evidence = useResource((signal) => endpoints.listTaskEvidence(taskId, signal), [taskId])
  const history = useResource((signal) => endpoints.taskHistory(taskId, signal), [taskId])

  const gates = useMemo(() => gateRecords(history.data?.events ?? []), [history.data])
  const items = evidence.data?.evidence ?? []

  return (
    <div className="stack">
      <Panel title={`Core 记录的判定依据(${gates.length} 次 VERIFIED/ACCEPTED)`}>
        {history.status === 'loading' && history.data === null ? <Loading what="历史" /> : null}
        {history.error ? (
          <ErrorPanel error={history.error} what="历史" onRetry={history.reload} />
        ) : null}
        {history.data && gates.length === 0 ? (
          <Empty
            what="已达 gate 的转换"
            hint="没有 VERIFIED/ACCEPTED 记录,或该 Task 尚未通过任何 gate。"
          />
        ) : null}
        {gates.length > 0 ? (
          <ul className="plain">
            {gates.map((gate) => (
              <li key={`${gate.eventId}:${gate.to}`}>
                {gate.stepId ? (
                  <code>{gate.stepId}</code>
                ) : (
                  <span>Task 级</span>
                )}{' '}
                → <strong>{gate.to}</strong> · {formatTimestamp(gate.occurredAt)} ·{' '}
                <SourceLink kind="event" id={gate.eventId} label="command event" taskId={taskId} />{' '}
                {gate.evidenceIds.length === 0 ? (
                  <span className="muted">(该转换没有 evidence_ids)</span>
                ) : (
                  gate.evidenceIds.map((evidenceId) => (
                    <SourceLink
                      key={evidenceId}
                      kind="evidence"
                      id={evidenceId}
                      label="evidence"
                      taskId={taskId}
                    />
                  ))
                )}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="state state--meta">
          这一节直接显示 State Event 里的判定记录,不由前端重新计算 pass/fail。
        </p>
      </Panel>

      <Panel
        title={`Evidence 列表(${items.length} 条)`}
        actions={
          <button type="button" onClick={evidence.reload} disabled={evidence.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {evidence.status === 'loading' && evidence.data === null ? <Loading what="Evidence" /> : null}
        {evidence.error ? (
          <ErrorPanel error={evidence.error} what="Evidence" onRetry={evidence.reload} />
        ) : null}
        {evidence.data && items.length === 0 ? <Empty what="Evidence" /> : null}
        {items.length > 0 ? (
          <TableScroll label="Task Evidence 列表">
          <table className="table">
            <caption className="sr-only">Task Evidence</caption>
            <thead>
              <tr>
                <th scope="col">Evidence</th>
                <th scope="col">kind / status</th>
                <th scope="col">reference</th>
                <th scope="col">step / revision</th>
                <th scope="col">fingerprint</th>
                <th scope="col">来源</th>
              </tr>
            </thead>
            <tbody>
              {items.map((record) => (
                <tr key={record.evidence_id}>
                  <th scope="row">
                    <a href={sourceHref.evidence(record.evidence_id, taskId)}>
                      <code>{record.evidence_id}</code>
                    </a>
                    <div className="muted">{formatTimestamp(record.created_at)}</div>
                  </th>
                  <td>
                    {record.kind} <StatusBadge kind="evidence" status={record.status} />
                  </td>
                  <td>
                    <StatusBadge kind="reference" status={record.reference_status} />
                    {record.artifact_uri ? (
                      <div className="muted mono">{record.artifact_uri}</div>
                    ) : null}
                  </td>
                  <td>
                    {record.step_id ? <code>{record.step_id}</code> : '—'}
                    <div className="muted">
                      task r{record.task_revision}
                      {record.step_revision !== null ? ` / step r${record.step_revision}` : ''}
                    </div>
                  </td>
                  <td className="mono">
                    {record.fingerprint_sha256 ? `${record.fingerprint_sha256.slice(0, 12)}…` : '—'}
                  </td>
                  <td>
                    <SourceLink kind="event" id={record.source_event_id} label="source" taskId={taskId} />
                    <div>
                      <SourceLink
                        kind="event"
                        id={record.change_event_id}
                        label="change"
                        taskId={taskId}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>
        ) : null}
        {evidence.loadedAt ? (
          <StaleBanner loadedAt={evidence.loadedAt} scope={`Task ${taskId} 的 Evidence`} />
        ) : null}
      </Panel>

      <CriteriaEvidenceMatrix taskId={taskId} />

      <Panel title="未接入">
        <Unavailable
          feature="人工 Evidence confirmation"
          owner="Core 接口负责人(P6-02)"
          pending="旧 ADR 0007 human prompt 路径仍不向浏览器开放"
          note="专用 Core Web 两阶段确认在人工确认页；需显式启用代理及 web_confirmation_v1 能力。确认只是 INFO，不能替代验收。"
        />
      </Panel>
    </div>
  )
}
