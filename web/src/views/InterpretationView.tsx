/**
 * Interpretation detail + Resolution preview (P6-02 milestone A).
 *
 * Two reads, kept visibly separate:
 *
 * - `GET /v1/interpretations/{id}` — the persisted record, its candidates and its
 *   maintenance lineage. Core's implementation calls `maintenance.get`, which may
 *   *recover* a pending rerun; the view says so instead of implying a strictly
 *   side-effect-free read.
 * - `GET /v1/resolutions/preview?interpretation_id=…` — the server's plan for this
 *   interpretation, used to show what a resolution *would* do. The UI never
 *   compiles a plan itself and never applies one.
 *
 * A preview conflict is a first-class state: Core refuses with a conflict code and
 * details (for example because the interpretation is not current any more or is
 * not ready), and the panel renders that refusal next to the record rather than
 * hiding it or presenting an empty plan.
 */

import { useMemo } from 'react'

import type { Interpretation, PlanItem, ResolutionPreview } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { RawText } from '../components/RawText'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'

export function InterpretationView({ interpretationId }: { interpretationId: string }): JSX.Element {
  const { endpoints } = useSession()
  const detail = useResource(
    (signal) => endpoints.getInterpretation(interpretationId, signal),
    [interpretationId],
  )
  const preview = useResource(
    (signal) => endpoints.previewResolution(interpretationId, signal),
    [interpretationId],
  )
  const item = detail.data?.interpretation ?? null

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <a href={sourceHref.interpretations(item?.project_id ?? null, item?.task_id ?? null)}>
          Interpretations
        </a>
        <span aria-hidden="true">/</span>
        <code>{interpretationId}</code>
      </nav>

      <Panel
        title="Interpretation(逐字记录)"
        actions={
          <button type="button" onClick={detail.reload} disabled={detail.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {detail.status === 'loading' && item === null ? <Loading what="Interpretation" /> : null}
        {detail.error ? (
          <ErrorPanel error={detail.error} what="Interpretation" onRetry={detail.reload} />
        ) : null}
        {item ? <InterpretationBody item={item} /> : null}
        {detail.loadedAt ? (
          <StaleBanner loadedAt={detail.loadedAt} scope={`Interpretation ${interpretationId}`} />
        ) : null}
        <p className="state state--meta">
          本页只发 GET,但 Core 的 <code>GET /v1/interpretations/{'{id}'}</code> 会调用
          <code> maintenance.get</code>,它可能为未完成的 rerun 执行恢复写入(完成
          <code> interpretation_operation_completions</code>,必要时推进 maintenance head)。
          因此这不是「严格只读」端点;本页不把恢复动作当成新事实展示。
        </p>
      </Panel>

      <Panel
        title="Resolution 预览(服务器计算)"
        actions={
          <button type="button" onClick={preview.reload} disabled={preview.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {preview.status === 'loading' && preview.data === null ? <Loading what="Resolution 预览" /> : null}
        {preview.error ? (
          <PreviewRefusal error={preview.error} onRetry={preview.reload} />
        ) : null}
        {preview.data ? <PreviewBody preview={preview.data} /> : null}
        {preview.loadedAt ? (
          <StaleBanner loadedAt={preview.loadedAt} scope="Resolution 预览" />
        ) : null}
        <p className="state state--meta">
          预览是服务器给出的<strong>计划</strong>,不是已应用的决议。本页不会自行编译计划、也不会应用它;
          需要人工审批的计划在 <a href={sourceHref.reviews(item?.project_id ?? null, item?.task_id ?? null)}>Pending Reviews</a> 中处理。
        </p>
      </Panel>

      <Panel title="本切片未交付">
        <Unavailable
          feature="Correct / Reject / Re-run 与人工 apply"
          owner="P6-02 里程碑 B"
          pending="correct/reject/rerun、review approve/reject 的写入契约与权限审计 Gate"
          tone="forbidden"
          note="本页不提供任何写按钮,也不会用网页点击冒充 human 来源或冒充自动 apply。"
        />
      </Panel>
    </div>
  )
}

function InterpretationBody({ item }: { item: Interpretation }): JSX.Element {
  const head = item.maintenance_head ?? null
  return (
    <>
      <p className="evidencebadges">
        <strong>{item.extractor_id}</strong>
        <StatusBadge kind="interpretation" status={item.status} />
        {item.source_type ? <span className="muted">{item.source_type}</span> : null}
      </p>
      <KeyValue
        rows={[
          { label: 'interpretation_id', value: <code>{item.interpretation_id}</code> },
          {
            label: '来源 Event',
            value: (
              <SourceLink
                kind="event"
                id={item.event_id}
                label="source Event"
                taskId={item.task_id}
                projectId={item.project_id}
              />
            ),
          },
          {
            label: 'project / task / session',
            value: (
              <>
                {item.project_id ? (
                  <a href={sourceHref.project(item.project_id)}><code>{item.project_id}</code></a>
                ) : '—'}
                {' · '}
                {item.task_id ? (
                  <a href={sourceHref.task(item.task_id)}><code>{item.task_id}</code></a>
                ) : '—'}
                {' · '}
                {item.session_id ? (
                  <a href={sourceHref.conversation(item.project_id, item.task_id, item.session_id)}>
                    <code>{item.session_id}</code>
                  </a>
                ) : '—'}
              </>
            ),
          },
          { label: 'source actor / kind', value: `${item.source_actor_id} · ${item.source_actor_kind}` },
          { label: 'processor actor', value: <code>{item.processor_actor_id}</code> },
          { label: 'host', value: <code>{item.host_id}</code> },
          {
            label: 'extractor / model / version',
            value: `${item.extractor_id} · ${item.extractor_model} · ${item.extractor_version}`,
          },
          { label: 'provider', value: item.provider },
          { label: 'prompt_version', value: <code>{item.prompt_version}</code> },
          { label: 'output_schema_version', value: <code>{item.output_schema_version}</code> },
          { label: 'confidence', value: item.confidence ?? '—' },
          { label: 'status / error_code', value: `${item.status}${item.error_code ? ` / ${item.error_code}` : ''}` },
          { label: 'created_at', value: formatTimestamp(item.created_at) },
          { label: 'deadline_at', value: formatTimestamp(item.deadline_at) },
          { label: 'completed_at', value: formatTimestamp(item.completed_at) },
          {
            label: 'parent',
            value: item.parent_interpretation_id ? (
              <a href={sourceHref.interpretation(item.parent_interpretation_id)}>
                <code>{item.parent_interpretation_id}</code>
              </a>
            ) : (
              '—(根解释)'
            ),
          },
          { label: 'input_hash', value: <code className="wrap">{item.input_hash}</code> },
          { label: 'schema / config digest', value: <code className="wrap">{item.schema_digest} · {item.config_digest}</code> },
        ]}
      />

      <h3>Candidates({(item.candidates ?? []).length})</h3>
      {(item.candidates ?? []).length === 0 ? (
        <Empty what="candidate" hint="该解释没有候选(可能仍在处理、失败或中断)。" />
      ) : (
        <ul className="cards">
          {(item.candidates ?? []).map((candidate, index) => (
            <li className="card" key={`${index}:${candidate.kind}`}>
              <h3>
                <code>{candidate.kind}</code> · {candidate.content}
              </h3>
              <p className="muted">
                impact {candidate.impact} · certainty {candidate.certainty} · confidence{' '}
                {candidate.confidence}
              </p>
              <p className="muted">
                scope{' '}
                {candidate.scope.kind}
                {candidate.scope.project_id ? ` · ${candidate.scope.project_id}` : ''}
                {candidate.scope.task_id ? ` · ${candidate.scope.task_id}` : ''}
                {candidate.scope.path ? ` · path ${candidate.scope.path}` : ''}
                {candidate.scope.tool ? ` · tool ${candidate.scope.tool}` : ''}
              </p>
              {candidate.rationale ? <p>{candidate.rationale}</p> : null}
              {candidate.source_span?.quote ? (
                <RawText value={candidate.source_span.quote} label="source_span.quote" />
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {head ? (
        <details open>
          <summary>Lineage 与 maintenance(来自 Core)</summary>
          <KeyValue
            compact
            rows={[
              { label: 'root', value: <code>{head.root_interpretation_id}</code> },
              {
                label: 'current',
                value: <a href={sourceHref.interpretation(head.current_interpretation_id)}><code>{head.current_interpretation_id}</code></a>,
              },
              { label: 'revision', value: head.revision },
              {
                label: 'status',
                value: (
                  <>
                    {head.status}
                    {head.pending_operation_event_id ? (
                      <>
                        {' '}· pending op{' '}
                        <SourceLink kind="event" id={head.pending_operation_event_id} label="operation" />
                      </>
                    ) : null}
                    {head.pending_child_interpretation_id ? (
                      <>
                        {' '}· pending child{' '}
                        <a href={sourceHref.interpretation(head.pending_child_interpretation_id)}>
                          <code>{head.pending_child_interpretation_id}</code>
                        </a>
                      </>
                    ) : null}
                  </>
                ),
              },
            ]}
          />
          {(item.maintenance_history ?? []).length > 0 ? (
            <ul className="plain">
              {(item.maintenance_history ?? []).map((change) => (
                <li key={`${change.operation_event_id}:${change.to_revision}`}>
                  r{change.from_revision}→r{change.to_revision} <strong>{change.action}</strong> ·{' '}
                  {change.reason ?? '—'} · <SourceLink kind="event" id={change.operation_event_id} label="command" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">没有人工维护历史。</p>
          )}
          {(item.operation_completions ?? []).length > 0 ? (
            <details>
              <summary>operation completions({(item.operation_completions ?? []).length})</summary>
              <ul className="plain">
                {(item.operation_completions ?? []).map((completion) => (
                  <li key={completion.operation_event_id}>
                    <code>{completion.operation_event_id}</code> → child {completion.child_status} ·
                    applied {completion.applied === 1 ? 'yes' : 'no'} · expected r{completion.expected_revision}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </details>
      ) : null}

      {item.raw_result_json ? (
        <JsonBlock value={safeJson(item.raw_result_json)} label="raw_result_json" />
      ) : (
        <p className="muted">没有原始结果(该解释尚未产出结果)。</p>
      )}
      <JsonBlock value={item.config} label="config(provider 配置,已脱敏显示)" />
    </>
  )
}

function PreviewBody({ preview }: { preview: ResolutionPreview }): JSX.Element {
  const plan = preview.plan
  return (
    <>
      <p className="evidencebadges">
        <strong>计划</strong>
        <StatusBadge kind="disposition" status={plan.disposition} />
        <span className="muted">policy {preview.policy_version}</span>
      </p>
      <KeyValue
        compact
        rows={[
          {
            label: '来源 Event',
            value: <SourceLink kind="event" id={preview.source_event_id} label="source Event" />,
          },
          {
            label: '已应用的决议',
            value: preview.source_application ? (
              <JsonBlock value={preview.source_application} label="source_application" />
            ) : (
              '—(该来源尚无已应用决议)'
            ),
          },
          { label: 'expected_context_digest', value: <code className="wrap">{preview.expected_context_digest}</code> },
        ]}
      />
      {plan.items.length === 0 ? (
        <Empty what="计划项" hint="服务器返回了空计划。" />
      ) : (
        <ul className="plain">
          {plan.items.map((item) => (
            <PlanRow key={`${item.candidate_index}:${item.action}`} item={item} />
          ))}
        </ul>
      )}
      <JsonBlock value={preview.expected_revisions} label="expected_revisions" />
      <JsonBlock value={plan} label="plan(服务器原文)" />
    </>
  )
}

function PlanRow({ item }: { item: PlanItem }): JSX.Element {
  return (
    <li>
      <StatusBadge kind="action" status={item.action} />{' '}
      {item.pending ? <span className="badge badge--warn">需要人工审批</span> : null}{' '}
      <code>{item.candidate.kind}</code> {item.candidate.content} · reason {item.reason} ·
      scope {item.candidate.scope.kind}
      {item.candidate.scope.task_id ? ` · ${item.candidate.scope.task_id}` : ''}
    </li>
  )
}

/**
 * A preview refusal is not an empty plan. Core returns a conflict code with
 * details (e.g. the interpretation is no longer current, or is not ready yet);
 * both are shown, and the retry keeps the same interpretation id.
 */
function PreviewRefusal({
  error,
  onRetry,
}: {
  error: import('../api/errors').CoreApiError
  onRetry: () => void
}): JSX.Element {
  const conflict = error.kind === 'conflict'
  return (
    <div className={conflict ? 'state state--pending' : 'state state--error'} role="alert">
      <p className="state__headline">
        {conflict ? '预览被拒绝(计划冲突)' : '预览读取失败'} — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        {conflict
          ? 'Core 认为当前解释/上下文不再满足预览条件,因此没有给出计划。这不是空计划,也不是已应用的决议。'
          : '预览接口没有返回计划;下方是 Core 的原始信息。'}
      </p>
      {error.details ? <JsonBlock value={error.details} label="conflict details" startsOpen /> : null}
      <button type="button" onClick={onRetry}>用同一个 interpretation_id 重试</button>
    </div>
  )
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export function useInterpretationSummary(item: Interpretation | null): string {
  return useMemo(() => (item ? `${item.interpretation_id} · ${item.status}` : '—'), [item])
}
