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
 * 3. "criteria → Evidence 参考矩阵" — a *read aid* that matches requirements to
 *    loaded Evidence by `kind` and explicit requirement fields. It is labelled
 *    as UI matching; the UI never promotes a Step to VERIFIED, and Core remains
 *    the only judge (a request that does not qualify still returns
 *    `422 missing_evidence`).
 */

import { useMemo } from 'react'

import type { AcceptanceRequirement, Evidence, RawEvent } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { TableScroll } from '../components/TableScroll'
import { SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'

interface GateRecord {
  stepId: string | null
  to: string
  eventId: string
  evidenceIds: string[]
  ruleVersions: unknown
  occurredAt: string
}

function gateRecords(events: RawEvent[]): GateRecord[] {
  const records: GateRecord[] = []
  for (const event of events) {
    if (event.event_type !== 'step.transitioned' && event.event_type !== 'task.transitioned') continue
    const payload = event.payload as Record<string, unknown>
    const to = payload['to']
    if (to !== 'VERIFIED' && to !== 'ACCEPTED') continue
    const result = payload['result']
    const nested =
      typeof result === 'object' && result !== null ? (result as Record<string, unknown>) : {}
    const ids = [payload['evidence_ids'], nested['evidence_ids']]
      .flatMap((value) => (Array.isArray(value) ? value : []))
      .filter((value): value is string => typeof value === 'string')
    records.push({
      stepId: typeof payload['step_id'] === 'string' ? payload['step_id'] : null,
      to: String(to),
      eventId: event.event_id,
      evidenceIds: [...new Set(ids)],
      ruleVersions: nested['rule_versions'] ?? payload['rule_versions'] ?? null,
      occurredAt: event.occurred_at,
    })
  }
  return records
}

function matches(requirement: AcceptanceRequirement, evidence: Evidence): boolean {
  // Only the fields the requirement actually states are compared, mirroring the
  // frozen requirement structure. Artifact/tool/command constraints that Core
  // checks are shown, not relaxed.
  if (evidence.kind !== requirement.kind) return false
  if (evidence.status !== requirement.required_result) return false
  if (requirement.tool_name && evidence.tool_name !== requirement.tool_name) return false
  if (requirement.command_sha256 && evidence.command_sha256 !== requirement.command_sha256) return false
  if (requirement.artifact_sha256 && evidence.artifact_sha256 !== requirement.artifact_sha256) return false
  if (evidence.reference_status === 'BROKEN_REFERENCE') return false
  return true
}

export function TaskEvidence({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const evidence = useResource((signal) => endpoints.listTaskEvidence(taskId, signal), [taskId])
  const history = useResource((signal) => endpoints.taskHistory(taskId, signal), [taskId])
  const steps = useResource((signal) => endpoints.listSteps(taskId, signal), [taskId])

  const gates = useMemo(() => gateRecords(history.data?.events ?? []), [history.data])
  const items = evidence.data?.evidence ?? []

  const requirements = useMemo(() => {
    const collected: Array<{ scope: string; requirement: AcceptanceRequirement }> = []
    for (const step of steps.data?.steps ?? []) {
      for (const requirement of step.acceptance_criteria.requirements) {
        collected.push({ scope: step.step_id, requirement })
      }
    }
    return collected
  }, [steps.data])

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

      <Panel title="criteria → Evidence 参考矩阵(前端匹配,仅供参考)">
        {requirements.length === 0 ? (
          <Empty what="Step acceptance requirements" hint="没有 requirements 就没有矩阵。" />
        ) : (
          <TableScroll label="criteria 与 Evidence 参考矩阵">
          <table className="table">
            <caption className="sr-only">criteria 与已载入 Evidence 的参考匹配</caption>
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">requirement</th>
                <th scope="col">已载入 Evidence 中的匹配</th>
              </tr>
            </thead>
            <tbody>
              {requirements.map(({ scope, requirement }) => {
                const matched = items.filter((record) => matches(requirement, record))
                return (
                  <tr key={`${scope}:${requirement.key}`}>
                    <th scope="row">
                      <code>{scope}</code>
                    </th>
                    <td>
                      <code>{requirement.key}</code> · {requirement.kind} →{' '}
                      {requirement.required_result}
                    </td>
                    <td>
                      {matched.length === 0 ? (
                        <span className="muted">未在已载入 Evidence 中找到匹配</span>
                      ) : (
                        matched.map((record) => (
                          <SourceLink
                            key={record.evidence_id}
                            kind="evidence"
                            id={record.evidence_id}
                            label={record.status}
                            taskId={taskId}
                          />
                        ))
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </TableScroll>
        )}
        <p className="state state--meta">
          这是前端按 requirement 字段做的只读匹配,不是验收结论。真正的判定发生在 Core:
          requirement 不满足时请求会得到 <code>422 missing_evidence</code>,本页不会提升任何状态。
        </p>
      </Panel>

      <Panel title="未接入">
        <Unavailable
          feature="人工 Evidence confirmation"
          owner="Core 接口负责人(P6-02)"
          pending="ADR 0007 要求的真实同任务 human prompt 来源"
          note="网页点击不能冒充 human source;在来源与权限链被后端认可前,该动作保持禁用。"
        />
      </Panel>
    </div>
  )
}
