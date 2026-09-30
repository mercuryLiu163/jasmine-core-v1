/**
 * Rule view (P6-A06).
 *
 * `GET /v1/rules/active` lists only applicable ACTIVE rules; there is no
 * all-status list endpoint in the baseline. So this view can either list the
 * active rules for a scope, or read one Rule by ID — and it says which of the
 * two it is doing. It never synthesises a "PROPOSED rules" table out of
 * histories it happens to have seen.
 *
 * Each Rule shows scope, status, version/revision, enforcement, origin Event and
 * change Event, plus the full version history from
 * `GET /v1/rules/{id}/history`.
 */

import { useId, useState } from 'react'

import type { Rule } from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { JsonBlock } from '../components/JsonBlock'
import { KeyValue, SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'

export function RulesView({
  projectId,
  taskId,
  ruleId,
}: {
  projectId: string | null
  taskId: string | null
  ruleId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const projectInputId = useId()
  const taskInputId = useId()
  const ruleInputId = useId()
  const [projectDraft, setProjectDraft] = useState(projectId ?? '')
  const [taskDraft, setTaskDraft] = useState(taskId ?? '')
  const [ruleDraft, setRuleDraft] = useState(ruleId ?? '')

  const active = useResource(
    (signal) => endpoints.listActiveRules({ project_id: projectId, task_id: taskId }, signal),
    [projectId, taskId],
  )
  const detail = useResource(
    ruleId === null ? null : (signal) => endpoints.getRule(ruleId, signal),
    [ruleId],
  )
  const history = useResource(
    ruleId === null ? null : (signal) => endpoints.getRuleHistory(ruleId, signal),
    [ruleId],
  )

  const applyScope = (): void => {
    globalThis.location.hash = hrefFor({
      name: 'rules',
      projectId: projectDraft.trim() || null,
      taskId: taskDraft.trim() || null,
      ruleId,
    })
  }
  const openRule = (): void => {
    const value = ruleDraft.trim()
    if (value) {
      globalThis.location.hash = hrefFor({ name: 'rules', projectId, taskId, ruleId: value })
    }
  }

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Rules</span>
      </nav>

      <Panel title="按 scope 读取 ACTIVE Rule">
        <div className="filters">
          <label htmlFor={projectInputId}>
            project_id
            <input
              id={projectInputId}
              value={projectDraft}
              onChange={(event) => setProjectDraft(event.target.value)}
              placeholder="prj_…"
              autoComplete="off"
            />
          </label>
          <label htmlFor={taskInputId}>
            task_id
            <input
              id={taskInputId}
              value={taskDraft}
              onChange={(event) => setTaskDraft(event.target.value)}
              placeholder="tsk_…(可空)"
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={applyScope}>
            应用 scope
          </button>
        </div>
        <p className="state state--meta">
          当前 scope:project {projectId ?? '—'} · task {taskId ?? '—'}。
          <code>GET /v1/rules/active</code> 只返回适用的 ACTIVE Rule;它不是全状态列表。
        </p>
        {active.status === 'loading' && active.data === null ? <Loading what="ACTIVE Rule" /> : null}
        {active.error ? (
          <ErrorPanel error={active.error} what="ACTIVE Rule" onRetry={active.reload} />
        ) : null}
        {active.data && active.data.rules.length === 0 ? (
          <Empty what="适用的 ACTIVE Rule" hint="这不代表系统里没有 PROPOSED/其他 scope 的 Rule。" />
        ) : null}
        {active.data && active.data.rules.length > 0 ? (
          <ul className="cards">
            {active.data.rules.map((rule) => (
              <li className="card" key={rule.rule_id}>
                <h3>
                  <a href={sourceHref.rule(rule.rule_id, projectId, taskId)}>
                    {rule.rule_key}
                  </a>
                </h3>
                <p>
                  <StatusBadge kind="rule" status={rule.status} /> v{rule.version} · rev{' '}
                  {rule.revision} · {rule.kind}/{rule.severity}/{rule.enforcement}
                </p>
                <p>{rule.content}</p>
              </li>
            ))}
          </ul>
        ) : null}
        {active.loadedAt ? (
          <StaleBanner loadedAt={active.loadedAt} scope="ACTIVE Rule 查询" />
        ) : null}
      </Panel>

      <Panel title="按 ID 读取单个 Rule(含非 ACTIVE)">
        <div className="filters">
          <label htmlFor={ruleInputId}>
            rule_id
            <input
              id={ruleInputId}
              value={ruleDraft}
              onChange={(event) => setRuleDraft(event.target.value)}
              placeholder="rul_…"
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={openRule}>
            读取这个 Rule
          </button>
        </div>
        <p className="state state--meta">
          已知 ID 可以读到 PROPOSED/SUPERSEDED/RETIRED 的当前值与历史;这是按 ID 读取,
          不是全状态列表。
        </p>
        {ruleId === null ? <p className="muted">尚未选择 rule_id。</p> : null}
        {detail.error ? (
          <ErrorPanel error={detail.error} what={`Rule ${ruleId}`} onRetry={detail.reload} />
        ) : null}
        {detail.data ? <RuleDetail rule={detail.data.rule} projectId={projectId} taskId={taskId} /> : null}
        {history.error ? (
          <ErrorPanel error={history.error} what="Rule 历史" onRetry={history.reload} />
        ) : null}
        {history.data ? (
          <details open>
            <summary>版本历史({history.data.history.length} 条快照)</summary>
            <ol className="events">
              {history.data.history.map((entry) => (
                <li key={`${entry.rule.revision}:${entry.change_event_id}`} className="event">
                  <div className="event__head">
                    <span className="event__type">
                      v{entry.rule.version} · rev {entry.rule.revision}
                    </span>
                    <StatusBadge kind="rule" status={entry.rule.status} />
                    <span className="muted">{formatTimestamp(entry.changed_at)}</span>
                    <SourceLink
                      kind="event"
                      id={entry.change_event_id}
                      label="change event"
                      projectId={projectId}
                      taskId={taskId}
                    />
                  </div>
                  <p>{entry.rule.content}</p>
                  <JsonBlock value={entry.rule.matcher} label="matcher" />
                </li>
              ))}
            </ol>
          </details>
        ) : null}
      </Panel>

      <Panel title="未接入">
        <Unavailable
          feature="Rule 人工写入(proposal / approve / supersede / retire)"
          owner="P6-02(依赖 Core 权限与人工修改审计 Gate)"
          pending="受控表单 + expected_revision/409 交互 + 独立权限 Gate"
          tone="forbidden"
          note="P6-01 只读:本页不提供任何写按钮,也不会代替 human/system actor 批准 Rule。"
        />
      </Panel>
    </div>
  )
}

function RuleDetail({
  rule,
  projectId,
  taskId,
}: {
  rule: Rule
  projectId: string | null
  taskId: string | null
}): JSX.Element {
  return (
    <>
      <KeyValue
        rows={[
          { label: 'rule_key', value: rule.rule_key },
          { label: 'rule_id', value: <code>{rule.rule_id}</code> },
          {
            label: 'status',
            value: <StatusBadge kind="rule" status={rule.status} />,
          },
          { label: 'version / revision', value: `${rule.version} / ${rule.revision}` },
          {
            label: 'scope',
            value: `${rule.scope.kind}${rule.scope.project_id ? ` · ${rule.scope.project_id}` : ''}${
              rule.scope.task_id ? ` · ${rule.scope.task_id}` : ''
            }`,
          },
          { label: 'kind / severity / enforcement', value: `${rule.kind} / ${rule.severity} / ${rule.enforcement}` },
          { label: 'content', value: rule.content },
          {
            label: 'origin Event',
            value: (
              <SourceLink
                kind="event"
                id={rule.origin_event_id}
                label={`${rule.origin_event_type ?? '未知类型'}(${rule.origin_actor_kind ?? '未知 actor'})`}
                projectId={projectId}
                taskId={taskId}
              />
            ),
          },
          {
            label: 'change Event',
            value: rule.change_event_id ? (
              <SourceLink
                kind="event"
                id={rule.change_event_id}
                label="change"
                projectId={projectId}
                taskId={taskId}
              />
            ) : (
              '—(Core 未返回 change_event_id)'
            ),
          },
          {
            label: 'status Event',
            value: rule.status_event_id ? (
              <SourceLink
                kind="event"
                id={rule.status_event_id}
                label={`status(${rule.status_actor_kind ?? '未知 actor'})`}
                projectId={projectId}
                taskId={taskId}
              />
            ) : (
              '—(Core 未返回 status_event_id)'
            ),
          },
        ]}
      />
      <JsonBlock value={rule.matcher} label="matcher" />
      {rule.verification_requirements ? (
        <JsonBlock value={rule.verification_requirements} label="verification_requirements" />
      ) : (
        <p className="muted">没有 verification_requirements。</p>
      )}
    </>
  )
}
