/**
 * The criteria↔Evidence matrix (P6-02 item 5, frozen contract rev 2 §3).
 *
 * Read-only and deliberately non-verdict: it lists the object's own acceptance requirements and
 * the applicable Rule requirements beside the Evidence rows the read surface exposes, classifies
 * each with the **readable** half of Core's predicate (`lib/criteriaMatrix.ts`) and names the
 * residual the browser cannot decide (F1/F2/F8/F9d/F9e). The strongest label is *candidate*.
 *
 * Every read is one of §3.1's named routes, all already in the always-on proxy allowlist:
 * R-T, R-SL, R-TE, R-TH, R-EV, R-RA. Nothing is recomputed that Core's validator owns, and no
 * affordance here can mint Evidence or a confirmation (§3.5, §11).
 */

import { useCallback } from 'react'

import { CoreApiError } from '../api/errors'
import type { AcceptanceRequirement, Evidence, RawEvent, Rule, Step, Task } from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { KeyValue, SourceLink } from '../components/Trace'
import { Empty, ErrorPanel, Loading, Panel } from '../components/States'
import { StatusBadge } from '../components/StatusBadge'
import { TableScroll } from '../components/TableScroll'
import {
  evaluateRequirement,
  ruleApplies,
  type MatrixContext,
  type RequirementEvaluation,
} from '../lib/criteriaMatrix'
interface MatrixData {
  task: Task
  steps: Step[]
  evidence: Evidence[]
  commandEvents: RawEvent[]
  eventsById: Map<string, RawEvent>
  rules: Rule[]
}

async function loadMatrix(
  endpoints: ReturnType<typeof useSession>['endpoints'],
  taskId: string,
  signal: AbortSignal,
): Promise<MatrixData> {
  const { task } = await endpoints.getTask(taskId, signal)
  const [stepsPage, evidencePage, historyPage, rulesPage] = await Promise.all([
    endpoints.listSteps(taskId, signal),
    endpoints.listTaskEvidence(taskId, signal),
    endpoints.taskHistory(taskId, signal),
    endpoints.listActiveRules({ project_id: task.project_id, task_id: taskId }, signal),
  ])
  // R-EV supplies the two things the list rows cannot: `change_event_id`'s `seq` (F5) and the
  // source Event's producer identity (F9a–F9c). A 404 is a real "no such Event"; any other
  // refusal is surfaced rather than hidden.
  const eventIds = new Set<string>()
  for (const row of evidencePage.evidence) {
    eventIds.add(row.change_event_id)
    eventIds.add(row.source_event_id)
  }
  for (const rule of rulesPage.rules) {
    if (rule.status_event_id) eventIds.add(rule.status_event_id)
  }
  const events = await Promise.all(
    [...eventIds].map(async (eventId) => {
      try {
        return (await endpoints.getEvent(eventId, signal)).event
      } catch (error) {
        if (error instanceof CoreApiError && error.status === 404) return null
        throw error
      }
    }),
  )
  const eventsById = new Map<string, RawEvent>()
  for (const event of events) if (event) eventsById.set(event.event_id, event)
  return {
    task,
    steps: stepsPage.steps,
    evidence: evidencePage.evidence,
    commandEvents: historyPage.events,
    eventsById,
    rules: rulesPage.rules,
  }
}

const STATE_LABEL: Record<RequirementEvaluation['state'], string> = {
  candidate: '候选匹配(Core 未判定)',
  missing: '缺项(没有 kind/status 相符的 Evidence)',
  'stale-proven': '不可用(已由可读事实证明 stale)',
  'field-mismatch': '字段不符(kind/status 相符,requirement 字段或规则绑定不匹配)',
}

function EvidenceRowFacts({ row, taskId }: { row: Evidence; taskId: string }): JSX.Element {
  return (
    <li key={row.evidence_id}>
      <SourceLink kind="evidence" id={row.evidence_id} label="evidence" taskId={taskId} />
      {' · '}
      {row.kind} <StatusBadge kind="evidence" status={row.status} />{' '}
      <span className="muted">
        {row.step_id ? <code>{row.step_id}</code> : 'Task'} · task r{row.task_revision}
        {row.step_revision !== null ? ` / step r${row.step_revision}` : ''}
        {row.confirmed_rule_id
          ? ` · confirmed ${row.confirmed_rule_id}@${row.confirmed_rule_version}`
          : ''}
      </span>
      {row.fingerprint_sha256 ? (
        <div className="muted mono" data-testid="matrix-fingerprint">
          记录时的 fingerprint {row.fingerprint_sha256.slice(0, 16)}… —— 当前工作区 fingerprint
          在本 API 下不可读,这不是“一致”声明。
        </div>
      ) : (
        <div className="muted" data-testid="matrix-fingerprint">
          该 Evidence 没有记录 fingerprint_sha256;当前工作区 fingerprint 在本 API 下不可读。
        </div>
      )}
    </li>
  )
}

function RequirementRow({
  scope,
  evaluation,
  taskId,
}: {
  scope: string
  evaluation: RequirementEvaluation
  taskId: string
}): JSX.Element {
  const { requirement, state, candidates, stale, fieldMismatch, wrongKind, residualUnknown } =
    evaluation
  return (
    <tr data-testid={`matrix-requirement-${requirement.key}`}>
      <th scope="row">
        <code>{scope}</code>
      </th>
      <td>
        <code>{requirement.key}</code> · {requirement.kind} → {requirement.required_result}
        {requirement.tool_name ? <div className="muted">tool_name {requirement.tool_name}</div> : null}
        {requirement.command_sha256 ? (
          <div className="muted mono">command_sha256 {requirement.command_sha256.slice(0, 16)}…</div>
        ) : null}
        {requirement.artifact_sha256 ? (
          <div className="muted mono">artifact_sha256 {requirement.artifact_sha256.slice(0, 16)}…</div>
        ) : null}
      </td>
      <td data-testid={`matrix-state-${requirement.key}`}>
        <span className="state state--meta">{STATE_LABEL[state]}</span>
        {state === 'candidate' && residualUnknown.length > 0 ? (
          <div className="muted" data-testid="matrix-residual">
            Core 未判定 / 无法在此判定:{residualUnknown.join(' / ')}{' '}
            在当前读取面上无法判定,候选不等于通过;是否满足只能由 Core 在提交时给出(
            <code>422 missing_evidence</code> 或成功)。
          </div>
        ) : null}
        {requirement.kind === 'USER_CONFIRMATION' && requirement.required_result === 'PASS' ? (
          <div className="muted" data-testid="matrix-confirmation-authoring-hazard">
            Criteria authoring hazard:Core 的现有确认生成路由始终写入 INFO,与此 PASS
            requirement 精确匹配失败,因此通过该路由永久无法满足。这是 criteria 的定义限制,
            不表示操作人员漏做确认;本页不生成确认或改写 criteria。
          </div>
        ) : null}
        {state === 'missing' ? (
          <div className="muted">
            这<strong>不</strong>表示 Core 一定会回答 requirement_key:Core 对匹配但 stale 的行回答同一个
            key,本页不能替 Core 决定分支。
          </div>
        ) : null}
      </td>
      <td>
        {candidates.length > 0 ? (
          <ul className="plain" data-testid={`matrix-candidates-${requirement.key}`}>
            {candidates.map((row) => (
              <EvidenceRowFacts key={row.evidence_id} row={row} taskId={taskId} />
            ))}
          </ul>
        ) : null}
        {stale.length > 0 ? (
          <ul className="plain" data-testid={`matrix-stale-${requirement.key}`}>
            {stale.map(({ row, failures }) => (
              <li key={row.evidence_id}>
                <SourceLink kind="evidence" id={row.evidence_id} label="evidence" taskId={taskId} />
                <ul className="plain">
                  {failures.map((failure) => (
                    <li key={`${failure.condition}:${failure.detail}`} className="muted">
                      可读事实证明不可用:{failure.condition} — {failure.detail}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        ) : null}
        {fieldMismatch.length > 0 ? (
          <ul className="plain" data-testid={`matrix-field-mismatch-${requirement.key}`}>
            {fieldMismatch.map((row) => (
              <li key={row.evidence_id} className="muted">
                <SourceLink kind="evidence" id={row.evidence_id} label="evidence" taskId={taskId} />{' '}
                kind/status 相符,但 requirement 的可选字段或规则绑定不匹配
              </li>
            ))}
          </ul>
        ) : null}
        {wrongKind.length > 0 ? (
          <details data-testid={`matrix-wrong-kind-${requirement.key}`}>
            <summary>
              其他 kind/status 的 Evidence({wrongKind.length})—— 不匹配,也从不渲染为 PASS
            </summary>
            <ul className="plain">
              {wrongKind.map((row) => (
                <li key={row.evidence_id}>
                  <SourceLink kind="evidence" id={row.evidence_id} label="evidence" taskId={taskId} />{' '}
                  {row.kind} <StatusBadge kind="evidence" status={row.status} />
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {candidates.length === 0 &&
        stale.length === 0 &&
        fieldMismatch.length === 0 &&
        wrongKind.length === 0 ? (
          <span className="muted">没有可显示的 Evidence 行</span>
        ) : null}
      </td>
    </tr>
  )
}

function RequirementTable({
  scope,
  requirements,
  rows,
  context,
  taskId,
}: {
  scope: string
  requirements: readonly AcceptanceRequirement[]
  rows: readonly Evidence[]
  context: Omit<MatrixContext, 'ruleId' | 'ruleVersion' | 'afterSeq'> & {
    ruleId?: string | null
    ruleVersion?: number | null
    afterSeq?: number | null
  }
  taskId: string
}): JSX.Element {
  if (requirements.length === 0) {
    return (
      <Empty
        what={`${scope} 的 acceptance requirements`}
        hint="没有 requirements 就没有矩阵;空 requirements 是 Core 的 422 拒绝路径,不是“无需验收”。"
      />
    )
  }
  return (
    <TableScroll label={`${scope} 的 criteria 与 Evidence 参考矩阵`}>
      <table className="table">
        <caption className="sr-only">{scope} 的 criteria 与已载入 Evidence 的候选匹配</caption>
        <thead>
          <tr>
            <th scope="col">范围</th>
            <th scope="col">requirement</th>
            <th scope="col">前端候选状态(非判定)</th>
            <th scope="col">Evidence 行与可读事实</th>
          </tr>
        </thead>
        <tbody>
          {requirements.map((requirement) => (
            <RequirementRow
              key={`${scope}:${requirement.key}`}
              scope={scope}
              evaluation={evaluateRequirement(requirement, rows, { ...context, stepId: context.stepId })}
              taskId={taskId}
            />
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/**
 * The USER_CONFIRMATION rows and their revision binding (§4.3).
 *
 * This read matrix only shows existing rows. The separate opt-in Web confirmation
 * tab uses Core prepare/submit; old confirm and Raw event writes remain proxy-refused.
 */
function ConfirmationBinding({
  rows,
  submitRevision,
  stepId,
  taskId,
}: {
  rows: readonly Evidence[]
  submitRevision: number
  stepId: string | null
  taskId: string
}): JSX.Element | null {
  const confirmations = rows.filter((row) => row.kind === 'USER_CONFIRMATION')
  if (confirmations.length === 0) return null
  return (
    <div className="state state--meta" data-testid="matrix-confirmation">
      <p className="state__headline">
        已存在的 USER_CONFIRMATION(确认来源是它自己的 source Event,不是本页合成的)
      </p>
      <ul className="plain">
        {confirmations.map((row) => {
          const bound = stepId !== null ? row.step_revision : row.task_revision
          return (
            <li key={row.evidence_id}>
              <SourceLink kind="evidence" id={row.evidence_id} label="confirmation" taskId={taskId} />{' '}
              <StatusBadge kind="evidence" status={row.status} /> ·{' '}
              {stepId !== null ? `step_revision ${bound ?? '—'}` : `task_revision ${bound}`}
              {bound === submitRevision ? (
                <span className="muted"> (提交 revision r{submitRevision} 与此绑定一致)</span>
              ) : (
                <span className="muted">
                  {' '}
                  (提交 revision r{submitRevision} 与此不同:ACCEPTED 的确认必须绑定精确 revision,
                  此确认不会满足该提交)
                </span>
              )}{' '}
              ·{' '}
              <SourceLink kind="event" id={row.source_event_id} label="source" taskId={taskId} />
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function CriteriaEvidenceMatrix({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const resource = useResource(
    (signal) => loadMatrix(endpoints, taskId, signal),
    [taskId],
  )
  const reload = useCallback(() => resource.reload(), [resource])
  const data = resource.data

  return (
    <Panel
      title="criteria → Evidence 参考矩阵(前端候选,非判定)"
      actions={
        <button type="button" onClick={reload} disabled={resource.status === 'loading'}>
          重新读取
        </button>
      }
    >
      {resource.status === 'loading' && resource.data === null ? (
        <Loading what="criteria/Evidence 矩阵" />
      ) : null}
      {resource.error ? (
        <ErrorPanel error={resource.error} what="criteria/Evidence 矩阵" onRetry={reload} />
      ) : null}

      {data ? (
        <>
          <div className="state state--meta" data-testid="matrix-non-verdict">
            <p>
              这是只读的前端候选矩阵(<strong>不是</strong>验收结论)。它只计算 Core
              <code>_fresh</code> 中浏览器可读的那一半(F3–F7 与 F9a–F9c)与 requirement 的字段精确匹配;
              <strong>F1 / F2 / F8 / F9d / F9e 在当前 API 下无法判定</strong>
              (没有读取 stored fingerprint、当前 fingerprint、producer 映射或 source 复算的路由),
              所以每个候选都带着 “Core 未判定” 的残差,绝非 “通过 / 可提交”。
              真正的判定发生在 Core 提交时:不满足会得到 <code>422 missing_evidence</code>。
            </p>
          </div>

          <h3>Task 自身的 acceptance requirements</h3>
          <RequirementTable
            scope={`Task ${data.task.task_id}`}
            requirements={data.task.acceptance_criteria.requirements}
            rows={data.evidence}
            context={{
              projectId: data.task.project_id,
              taskRevision: data.task.revision,
              expectedRevision: data.task.revision,
              stepId: null,
              commandEvents: data.commandEvents,
              eventsById: data.eventsById,
            }}
            taskId={taskId}
          />
          <ConfirmationBinding
            rows={data.evidence.filter((row) => row.step_id === null)}
            submitRevision={data.task.revision}
            stepId={null}
            taskId={taskId}
          />

          {data.steps.map((step) => (
            <div key={step.step_id}>
              <h3>
                Step <code>{step.step_id}</code> @ r{step.revision} · {step.status}
              </h3>
              <RequirementTable
                scope={`Step ${step.step_id}`}
                requirements={step.acceptance_criteria.requirements}
                rows={data.evidence.filter((row) => row.step_id === step.step_id)}
                context={{
                  projectId: data.task.project_id,
                  taskRevision: data.task.revision,
                  expectedRevision: step.revision,
                  stepId: step.step_id,
                  commandEvents: data.commandEvents,
                  eventsById: data.eventsById,
                }}
                taskId={taskId}
              />
              <ConfirmationBinding
                rows={data.evidence.filter((row) => row.step_id === step.step_id)}
                submitRevision={step.revision}
                stepId={step.step_id}
                taskId={taskId}
              />
            </div>
          ))}

          {data.steps.length === 0 ? (
            <Empty
              what="Step"
              hint="该 Task 还没有 Step;Task 级 requirements 仍然显示在上面。"
            />
          ) : null}

          <ApplicableRules data={data} taskId={taskId} />

          <p className="state state--meta" data-testid="matrix-fingerprint-note">
            本页只显示每行 Evidence 记录时的 <code>fingerprint_sha256</code>。当前工作区 fingerprint
            与 stored snapshot 的比较(F1/F2)没有读取路由,所以本页<strong>永不</strong>声明
            fingerprint 一致;F5 的 cycle cutoff、F9a–F9c 的来源校验来自 R-TH 与 R-EV。
          </p>
        </>
      ) : null}
    </Panel>
  )
}

/**
 * The applicable-Rule half (§3.3): a copy of Core's WHERE clause over R-RA, never Core's answer.
 * A Rule whose mapping is absent is the one *reachable* refusal branch (`{rule_id, version}`).
 */
function ApplicableRules({ data, taskId }: { data: MatrixData; taskId: string }): JSX.Element {
  const applicable = data.rules.filter((rule) =>
    ruleApplies(rule, data.task.task_id, data.task.project_id),
  )
  return (
    <div data-testid="matrix-rules">
      <h3>适用的 ACTIVE Rule(kind=ACCEPTANCE 或 enforcement=VERIFY)</h3>
      <p className="state state--meta">
        这是对 Core <code>_rules</code> WHERE 子句的<strong>复制</strong>,不是 Core 的回答:R-RA
        返回所有 ACTIVE Rule。Rule 的 requirements 是否可满足(以及规则的激活 Event 之后是否有新
        Evidence)只有在提交时由 Core 判定;缺 mapping 的 Rule 是可达的
        <code>422 {'{rule_id, version}'}</code> 拒绝分支。每条 Rule 的 requirements 分别按 Task 与各 Step 的提交范围评估,
        使用各自的 Evidence 范围与 revision;候选仍然不是 Core 判定。
      </p>
      {applicable.length === 0 ? <Empty what="适用 Rule" /> : null}
      {applicable.map((rule) => {
        const activation = rule.status_event_id
          ? data.eventsById.get(rule.status_event_id)
          : undefined
        return (
          <div key={rule.rule_id} data-testid={`matrix-rule-${rule.rule_id}`}>
            <KeyValue
              rows={[
                {
                  label: 'rule',
                  value: (
                    <>
                      <SourceLink kind="rule" id={rule.rule_id} label="rule" taskId={taskId} /> @
                      version {rule.version}
                    </>
                  ),
                },
                {
                  label: 'kind / enforcement',
                  value: `${rule.kind} / ${rule.enforcement}`,
                },
                {
                  label: 'scope',
                  value: `${rule.scope.kind}${
                    rule.scope.project_id ? ` · ${rule.scope.project_id}` : ''
                  }${rule.scope.task_id ? ` · ${rule.scope.task_id}` : ''}`,
                },
                {
                  label: '激活 Event',
                  value: rule.status_event_id ? (
                    <>
                      <SourceLink
                        kind="event"
                        id={rule.status_event_id}
                        label="activation"
                        taskId={taskId}
                      />{' '}
                      {activation ? (
                        <span className="muted">seq {activation.seq}</span>
                      ) : (
                        <span className="muted">
                          该 Event 未读到;Core 的 “active Rule lacks activation Event” 分支在这里是防御性、
                          通过 API 不可达的。
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="muted">
                      R-RA 没有给出 status_event_id;Core 的 activation-Event 分支同样是防御性的。
                    </span>
                  ),
                },
              ]}
            />
            {rule.verification_requirements === null ? (
              <p className="state state--pending" data-testid={`matrix-rule-no-mapping-${rule.rule_id}`}>
                该 ACTIVE Rule 没有 <code>verification_requirements</code>:Core 会以
                <code>422 missing_evidence</code> 拒绝,<code>details</code> 为{' '}
                <code>{`{"rule_id": "${rule.rule_id}", "version": ${rule.version}}`}</code>
                (注意键是 <code>version</code>)。这是可达分支,本页不把它当作缺项。
              </p>
            ) : rule.verification_requirements.requirements.length === 0 ? (
              <p className="state state--meta">
                该 Rule 的 mapping 是空 requirements(<code>{'{"requirements": []}'}</code>)。
              </p>
            ) : (
              [
                { stepId: null, revision: data.task.revision, scope: `Task ${data.task.task_id}` },
                ...data.steps.map((step) => ({
                  stepId: step.step_id,
                  revision: step.revision,
                  scope: `Step ${step.step_id}`,
                })),
              ].map((submission) => (
                <div
                  key={submission.stepId ?? 'task'}
                  data-testid={`matrix-rule-context-${submission.stepId ?? 'task'}`}
                >
                  <h4>{submission.scope} @ r{submission.revision} 的 Rule requirements</h4>
                  <RequirementTable
                    scope={`${submission.scope} · Rule ${rule.rule_id}@${rule.version}`}
                    requirements={rule.verification_requirements!.requirements}
                    rows={submission.stepId === null
                      ? data.evidence
                      : data.evidence.filter((row) => row.step_id === submission.stepId)}
                    context={{
                      projectId: data.task.project_id,
                      taskRevision: data.task.revision,
                      expectedRevision: submission.revision,
                      stepId: submission.stepId,
                      commandEvents: data.commandEvents,
                      eventsById: data.eventsById,
                      ruleId: rule.rule_id,
                      ruleVersion: rule.version,
                      afterSeq: activation?.seq ?? null,
                    }}
                    taskId={taskId}
                  />
                </div>
              ))
            )}
          </div>
        )
      })}
    </div>
  )
}
