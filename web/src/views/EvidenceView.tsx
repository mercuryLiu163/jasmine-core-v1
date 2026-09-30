/**
 * Single Evidence (deep link, P6-A06).
 *
 * Shows the immutable Evidence row exactly as Core returns it, including
 * `reference_status` (rechecked by Core on every read), the fingerprint, the
 * tool/command digests and both the source and change Events.
 *
 * There is deliberately **no** "open artifact" button: the baseline has no
 * arbitrary artifact download or preview endpoint, and a `workspace:` URI is a
 * Core-side reference, not a browser file URL. Building one from the browser
 * would reach outside Core's controlled access.
 */

import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { StatusBadge } from '../components/StatusBadge'
import { ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { formatTimestamp, isHttpUrl } from '../lib/text'

export function EvidenceView({
  evidenceId,
  taskId,
}: {
  evidenceId: string
  taskId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const evidence = useResource((signal) => endpoints.getEvidence(evidenceId, signal), [evidenceId])
  const record = evidence.data?.evidence ?? null

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        {taskId ? (
          <>
            <a href={`#/task/${encodeURIComponent(taskId)}/evidence`}>Task Evidence</a>
            <span aria-hidden="true">/</span>
          </>
        ) : null}
        <code>{evidenceId}</code>
      </nav>

      <Panel
        title="Evidence"
        actions={
          <button type="button" onClick={evidence.reload} disabled={evidence.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {evidence.status === 'loading' && record === null ? <Loading what="Evidence" /> : null}
        {evidence.error ? (
          <ErrorPanel error={evidence.error} what="Evidence" onRetry={evidence.reload} />
        ) : null}
        {record ? (
          <>
            <p className="evidencebadges">
              <strong>{record.kind}</strong>
              <StatusBadge kind="evidence" status={record.status} />
              <StatusBadge kind="reference" status={record.reference_status} />
            </p>
            <KeyValue
              rows={[
                { label: 'evidence_id', value: <code>{record.evidence_id}</code> },
                {
                  label: 'reference_status',
                  value: <StatusBadge kind="reference" status={record.reference_status} />,
                },
                { label: 'task_id', value: <code>{record.task_id}</code> },
                {
                  label: 'step_id',
                  value: record.step_id ? <code>{record.step_id}</code> : '—',
                },
                { label: 'created_at', value: formatTimestamp(record.created_at) },
                { label: 'actor / host', value: `${record.actor_id} · ${record.host_id}` },
                {
                  label: 'task / step revision',
                  value: `${record.task_revision} / ${record.step_revision ?? '—'}`,
                },
                {
                  label: 'fingerprint_sha256',
                  value: record.fingerprint_sha256 ? (
                    <code className="wrap">{record.fingerprint_sha256}</code>
                  ) : (
                    '—'
                  ),
                },
                {
                  label: 'source Event',
                  value: (
                    <SourceLink
                      kind="event"
                      id={record.source_event_id}
                      label="source"
                      taskId={record.task_id}
                    />
                  ),
                },
                {
                  label: 'change Event',
                  value: (
                    <SourceLink
                      kind="event"
                      id={record.change_event_id}
                      label="change"
                      taskId={record.task_id}
                    />
                  ),
                },
                {
                  label: 'related post-tool Event',
                  value: record.related_posttool_event_id ? (
                    <SourceLink
                      kind="event"
                      id={record.related_posttool_event_id}
                      label="post-tool"
                      taskId={record.task_id}
                    />
                  ) : (
                    '—'
                  ),
                },
                { label: 'tool_name', value: record.tool_name ?? '—' },
                { label: 'turn_id / tool_use_id', value: `${record.turn_id ?? '—'} / ${record.tool_use_id ?? '—'}` },
                {
                  label: 'command_sha256',
                  value: record.command_sha256 ? (
                    <code className="wrap">{record.command_sha256}</code>
                  ) : (
                    '—'
                  ),
                },
                {
                  label: 'confirmed rule',
                  value:
                    record.confirmed_rule_id === null
                      ? '—'
                      : `${record.confirmed_rule_id} v${record.confirmed_rule_version ?? '—'}`,
                },
                {
                  label: 'artifact',
                  value: record.artifact_uri ? (
                    <>
                      <code className="wrap">{record.artifact_uri}</code>
                      <div className="muted">
                        workspace: 是 Core 侧引用,不是浏览器可打开的文件 URL。
                      </div>
                    </>
                  ) : (
                    '—'
                  ),
                },
              ]}
            />
            <JsonBlock value={record.result} label="result" startsOpen />
            <p className="state state--meta">
              本次读取:<code>GET /v1/evidence/{evidenceId}</code> ·{' '}
              {evidence.loadedAt ? <time dateTime={evidence.loadedAt}>{evidence.loadedAt}</time> : '—'}
            </p>
            {evidence.loadedAt ? (
              <StaleBanner loadedAt={evidence.loadedAt} scope={`Evidence ${evidenceId}`} />
            ) : null}
          </>
        ) : null}
      </Panel>

      <Panel title="artifact 与外部链接">
        <Unavailable
          feature="artifact 下载 / 预览"
          owner="P7-01(最小 Validation 读取与安全 artifact 契约)"
          pending="受权、脱敏的 artifact 读取 API"
          note="基线只有 reference_status 与 workspace: 引用;浏览器不会去读服务器文件路径,也不把 artifact_uri 当链接打开。"
        />
        {record?.artifact_uri && isHttpUrl(record.artifact_uri) ? (
          <p>该 artifact_uri 形如 http(s) URL;本页仍不会自动打开,链接策略只允许冻结的安全协议。</p>
        ) : null}
      </Panel>
    </div>
  )
}
