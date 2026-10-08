/**
 * P6-03c — the read-only Validation Inspector: run list, run detail, bundle manifest and
 * artifact metadata + content.
 *
 * **DEPENDENCY PENDING, and P7-01's acceptance criterion 1 is BLOCKED.** The five routes
 * exist on the P7-01 branch (contract frozen in `web/contracts/p7-validation-read.v1.json`)
 * and **not** in this baseline's Core; they are opt-in behind the proxy's
 * `P6_ENABLE_P7_READS`. These views render whatever answered — a P7-enabled Core, or the
 * proxy's refusal — and the standing status line says which slice this is regardless.
 *
 * Five things this file is careful about, each because the alternative is a lie:
 *
 * 1. **The recorded verdict is served as recorded.** Nothing here re-derives, re-signs or
 *    upgrades a verdict; `verdict_consistent` reports the recorder's own comparison and is
 *    shown as a fact, not as a correction.
 * 2. **A missing chain link is visibly missing.** The eight T06 links are rendered one per
 *    row with `present` and the API's own `missing_reason`; the reader has no Extraction or
 *    Resolution source at all, and the Context link needs `present ∧ context_pack_present`
 *    (a recorded run-details object is not a pack).
 * 3. **A corrupt or duplicate record is shown as unusable, not hidden.** It stays in the
 *    list with its reason; its mode/attribution/grade are `null` and render as `—` rather
 *    than as an invented value.
 * 4. **A refusal is never an empty list.** Empty, filtered-empty, not-configured,
 *    not-found, corrupt, modified, withheld, too-large and unsupported are separate states,
 *    and every one of them is handled by `ValidationDependency.tsx` from Core's own code
 *    and `details`.
 * 5. **Artifact content is text.** It is rendered through `RawText` (a `<pre>` with a text
 *    node); there is no HTML parsing, no `iframe`/`srcDoc`, and no `dangerouslySetInnerHTML`
 *    anywhere in this file.
 */

import { Fragment, useState } from 'react'

import type { CoreApiError } from '../api/errors'
import { isDependencyPending } from '../api/errors'
import type {
  ValidationArtifact,
  ValidationArtifactContent,
  ValidationCase,
  ValidationChainLinkName,
  ValidationManifest,
  ValidationManifestFile,
  ValidationRunDetail,
  ValidationRunPage,
  ValidationRunRow,
  ValidationSection,
  ValidationSectionName,
  ValidationSections,
} from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { usePagedResource } from '../app/usePagedResource'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { Pager } from '../components/Pager'
import { RawText } from '../components/RawText'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Panel, StaleBanner } from '../components/States'
import { TableScroll } from '../components/TableScroll'
import { KeyValue } from '../components/Trace'
import { isCoreId } from '../lib/ids'
import { formatTimestamp } from '../lib/text'
import {
  ARTIFACT_ID_GRAMMAR,
  RUN_ID_GRAMMAR,
  VALIDATION_READ_SCOPES,
  ValidationDependencyPending,
  ValidationErrorState,
  ValidationHash,
  ValidationInvalidId,
  ValidationSliceStatus,
  isValidationArtifactId,
  isValidationRunId,
} from './ValidationDependency'

/** Core's list default is 20 and its max is 100; the Inspector asks for the default. */
const RUN_LIMIT = 20

/** The recorder's `SECTIONS`, in the order the record stores them. */
const SECTION_ORDER: readonly ValidationSectionName[] = [
  'user_raw',
  'agent_response',
  'tools',
  'state_before',
  'state_after',
  'interpretation',
  'context',
]

/** The taskbook T06 chain, in order. */
const CHAIN_ORDER: readonly ValidationChainLinkName[] = [
  'raw',
  'extraction',
  'resolution',
  'context',
  'agent',
  'tool',
  'evidence',
  'state',
]

const VERDICT_VALUES = ['PASS', 'FAIL', 'BLOCKED'] as const
const MODE_VALUES = ['real', 'component', 'simulated', 'unknown'] as const
const ATTRIBUTION_VALUES = ['attributed', 'unattributed'] as const

/** `—` is the display for an absent value; it is never confused with the string "null". */
function dash(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function isPendingError(error: CoreApiError | null): boolean {
  return error !== null && isDependencyPending(error)
}

/**
 * The `empty_reason` Core returns is one of two very different facts, so the view spells
 * them out instead of printing the token alone. The token is echoed first so an operator
 * can still grep for exactly what Core said.
 */
function emptyReasonText(reason: string | undefined): string {
  if (reason === 'no_run_shaped_files') {
    return '运行根里没有 run-shaped 文件:这是一个空集,不是遍历失败(遍历不完整会返回 503,而不是空)。'
  }
  if (reason === 'all_filtered_out') {
    return '有可见的 run-shaped 记录,但当前过滤条件把它们全部排除:这不是「没有记录」。'
  }
  if (reason === undefined) {
    return 'Core 没有返回这个字段:只有空页才会带 empty_reason,所以这一页不是空的。'
  }
  return `Core 给出的 empty_reason 是 ${reason}(本页不认识,原样展示)。`
}

/** A `<select>` that also shows a hand-edited value Core will refuse, rather than hiding it. */
function EnumSelect({
  label,
  value,
  values,
  onChange,
}: {
  label: string
  value: string
  values: readonly string[]
  onChange: (next: string) => void
}): JSX.Element {
  const unknown = value !== '' && !values.includes(value)
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">(全部)</option>
        {values.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        {unknown ? <option value={value}>{value}(Core 不认识的取值)</option> : null}
      </select>
    </label>
  )
}

/* -------------------------------------------------------------------------- *
 * Run list
 * -------------------------------------------------------------------------- */

function CountsCell({ row }: { row: ValidationRunRow }): JSX.Element {
  const counts = row.case_counts
  return (
    <span className="muted">
      pass {counts.pass} · fail {counts.fail} · blocked {counts.blocked} · total {counts.total}
    </span>
  )
}

function AttributionCell({ row }: { row: ValidationRunRow }): JSX.Element {
  if (row.attribution === null) return <span className="muted">—(不可用)</span>
  return (
    <span className="stack">
      <StatusBadge kind="validation" status={row.attribution.status} />
      {row.attribution.projects.length > 0 ? (
        <span className="muted">
          {row.attribution.projects.map((project) => (
            <code key={project}>{project}</code>
          ))}
        </span>
      ) : (
        <span className="muted">
          {row.attribution.reason ?? '没有项目归属'}
        </span>
      )}
    </span>
  )
}

function ModeCell({ row }: { row: ValidationRunRow }): JSX.Element {
  if (row.mode === null) return <span className="muted">—(不可用)</span>
  return (
    <span className="stack">
      <code>{row.mode}</code>
      <span className="muted">{row.mode_basis}</span>
    </span>
  )
}

/**
 * One row, plus a second row naming why it is unusable. A `corrupt`/`duplicate` record is
 * listed rather than dropped, and its absent fields render as `—` — never as a value the
 * reader could not read.
 */
function RunRow({ row }: { row: ValidationRunRow }): JSX.Element {
  const usable = row.record_status === 'ok'
  const reason = row.record_status === 'duplicate' ? row.duplicate_reason : row.corrupt_reason
  return (
    <Fragment>
      <tr data-testid={usable ? 'run-row' : 'run-row-unusable'}>
        <td>
          <a href={sourceHref.validationRun(row.run_id)}>
            <code>{row.run_id}</code>
          </a>
        </td>
        <td>{dash(row.stage)}</td>
        <td>{formatTimestamp(row.created_at)}</td>
        <td>
          {row.verdict ? <StatusBadge kind="validation" status={row.verdict} /> : <span className="muted">—</span>}
        </td>
        <td>
          <CountsCell row={row} />
        </td>
        <td>
          <ModeCell row={row} />
        </td>
        <td>
          <AttributionCell row={row} />
        </td>
        <td>{row.evidence_grade ? <StatusBadge kind="validation" status={row.evidence_grade} /> : <span className="muted">—</span>}</td>
        <td>{row.synthetic ? <StatusBadge kind="validation" status="synthetic" /> : <span className="muted">—</span>}</td>
        <td>
          {row.manifest_status ? (
            <StatusBadge kind="validation" status={row.manifest_status} />
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td>
          <StatusBadge kind="validation" status={row.record_status} />
        </td>
        <td>{row.verdict_consistent === null ? <span className="muted">—</span> : String(row.verdict_consistent)}</td>
        <td>
          {usable ? (
            <a href={sourceHref.validationManifest(row.run_id)}>manifest</a>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
      </tr>
      {usable ? null : (
        <tr data-testid="run-row-unusable-reason">
          <td colSpan={13}>
            <span className="badge badge--bad">不可用</span>{' '}
            <strong>
              record_status = {row.record_status}
            </strong>
            {reason ? <> · {reason}</> : null} · record_sha256{' '}
            <code className="wrap">{row.record_sha256}</code>
            <div className="muted">
              这一行仍被列出(不是空集,也没有被藏起来),但它的 verdict / mode / attribution /
              evidence_grade / manifest_status / verdict_consistent 都不可用,所以显示为 —。
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

function RunTable({ rows }: { rows: readonly ValidationRunRow[] }): JSX.Element {
  return (
    <TableScroll label="Validation run 列表">
      <table className="table" data-testid="validation-runs">
        <thead>
          <tr>
            <th>run_id</th>
            <th>stage</th>
            <th>created_at</th>
            <th>verdict(按记录)</th>
            <th>case_counts</th>
            <th>mode / mode_basis</th>
            <th>attribution</th>
            <th>evidence_grade</th>
            <th>synthetic</th>
            <th>manifest_status</th>
            <th>record_status</th>
            <th>verdict_consistent</th>
            <th>链接</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <RunRow key={row.run_id + row.record_sha256} row={row} />
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/**
 * Everything the list body reports about the *set*, so an empty result can be told apart
 * from a filtered one and from a broken index. `discovery` is Core's raw root diagnostics;
 * in `acl` mode `ignored_samples[]` is deliberately empty and only the count is reported.
 */
function ListSummary({ page }: { page: ValidationRunPage }): JSX.Element {
  return (
    <div className="stack" data-testid="validation-summary">
      <KeyValue
        rows={[
          { label: 'total', value: <code>{page.total}</code> },
          { label: 'count(本页)', value: <code>{page.count}</code> },
          { label: 'has_more', value: <code>{String(page.has_more)}</code> },
          { label: 'corrupt_count', value: <code>{page.corrupt_count}</code> },
          { label: 'duplicate_count', value: <code>{page.duplicate_count}</code> },
          { label: 'ignored_entries', value: <code>{page.ignored_entries}</code> },
          {
            label: 'visibility',
            value: (
              <>
                <code>{page.visibility}</code>
                <span className="muted">
                  {page.visibility === 'grant_filtered'
                    ? ' —— acl 模式:runs[]/count/total 只统计当前 actor 可读的记录'
                    : ' —— 每个 run 都可见'}
                </span>
              </>
            ),
          },
          { label: 'complete', value: <code>{String(page.complete)}</code> },
        ]}
      />
      <p className="state state--meta" data-testid="empty-reason">
        <strong>empty_reason:</strong> <code>{page.empty_reason ?? '(未给出)'}</code> ——{' '}
        {emptyReasonText(page.empty_reason)}
      </p>
      <JsonBlock value={page.discovery} label="discovery(运行根诊断,非按项目)" />
      {page.ignored_samples.length > 0 ? (
        <JsonBlock value={page.ignored_samples} label={`ignored_samples(${page.ignored_samples.length})`} />
      ) : (
        <p className="muted">
          没有 ignored_samples(共 {page.ignored_entries} 条 ignored 条目
          {page.visibility === 'grant_filtered' ? ';acl 模式刻意只给计数,不给路径样本' : ''})。
        </p>
      )}
    </div>
  )
}

/**
 * The list view. Pagination follows the dashboard's offset convention (`limit`/`offset` +
 * `Pager`) but completion is Core's own `has_more`, not "the last page looked short":
 * Core computes `offset + count < total`, so a full last page is still the last page.
 */
export function ValidationRunsView({
  verdict,
  stage,
  mode,
  attribution,
  project,
}: {
  verdict: string | null
  stage: string | null
  mode: string | null
  attribution: string | null
  project: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const [verdictDraft, setVerdictDraft] = useState(verdict ?? '')
  const [stageDraft, setStageDraft] = useState(stage ?? '')
  const [modeDraft, setModeDraft] = useState(mode ?? '')
  const [attributionDraft, setAttributionDraft] = useState(attribution ?? '')
  const [projectDraft, setProjectDraft] = useState(project ?? '')
  const [projectInvalid, setProjectInvalid] = useState(false)

  // The page metadata is stamped with the filter set it belongs to, so a previous filter's
  // `total` can never be shown under a new filter while the new page is in flight.
  const filterKey = [verdict, stage, mode, attribution, project].join('\u0000')
  const [meta, setMeta] = useState<{ key: string; page: ValidationRunPage } | null>(null)

  const runs = usePagedResource<ValidationRunRow>(
    async (cursor, signal) => {
      const offset = typeof cursor === 'number' ? cursor : 0
      const page = await endpoints.listValidationRuns(
        { limit: RUN_LIMIT, offset, verdict, stage, mode, attribution, project },
        signal,
      )
      setMeta({ key: filterKey, page })
      return {
        items: page.runs,
        count: page.count,
        complete: page.has_more === false,
        nextCursor: page.has_more ? page.offset + page.count : null,
      }
    },
    [verdict, stage, mode, attribution, project],
  )

  const page = meta !== null && meta.key === filterKey ? meta.page : null
  const pending = isPendingError(runs.error)

  const apply = (): void => {
    const projectValue = projectDraft.trim()
    if (projectValue !== '' && !isCoreId(projectValue, 'prj')) {
      setProjectInvalid(true)
      return
    }
    setProjectInvalid(false)
    globalThis.location.hash = sourceHref.validationRuns({
      verdict: verdictDraft === '' ? null : verdictDraft,
      stage: stageDraft.trim() === '' ? null : stageDraft.trim(),
      mode: modeDraft === '' ? null : modeDraft,
      attribution: attributionDraft === '' ? null : attributionDraft,
      project: projectValue === '' ? null : projectValue,
    })
  }

  const clear = (): void => {
    setVerdictDraft('')
    setStageDraft('')
    setModeDraft('')
    setAttributionDraft('')
    setProjectDraft('')
    setProjectInvalid(false)
    globalThis.location.hash = sourceHref.validationRuns()
  }

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Validation(P7-01 只读)</span>
      </nav>
      <Panel title="Validation Runs">
        <div className="stack">
          <ValidationSliceStatus />

          <div className="filters" data-testid="validation-filters">
            <EnumSelect label="verdict" value={verdictDraft} values={VERDICT_VALUES} onChange={setVerdictDraft} />
            <label>
              stage
              <input value={stageDraft} onChange={(event) => setStageDraft(event.target.value)} placeholder="P0" />
            </label>
            <EnumSelect label="mode" value={modeDraft} values={MODE_VALUES} onChange={setModeDraft} />
            <EnumSelect
              label="attribution"
              value={attributionDraft}
              values={ATTRIBUTION_VALUES}
              onChange={setAttributionDraft}
            />
            <label>
              project
              <input
                value={projectDraft}
                onChange={(event) => setProjectDraft(event.target.value)}
                placeholder="prj_…"
                aria-invalid={projectInvalid}
              />
            </label>
            <button type="button" onClick={apply}>
              应用过滤
            </button>
            <button type="button" onClick={clear}>
              清除过滤
            </button>
          </div>
          {projectInvalid ? (
            <p className="state state--error" role="alert" data-testid="filter-invalid">
              project 必须是 <code>prj_</code> + 26 位 Crockford base32(ADR 0001)。请求没有发出 ——
              否则代理会以 <code>proxy_path_not_allowed</code> 拒绝它,把一次手误误报成「P7 未合并」。
            </p>
          ) : null}
          <p className="state state--meta">
            过滤走 Core 本身(<code>verdict</code>/<code>stage</code>/<code>mode</code>/
            <code>attribution</code>/<code>project</code>);未知取值由 Core 以{' '}
            <code>400 invalid_request</code> 拒绝。corrupt 行不匹配任何正向过滤 —— 它没有可用的
            verdict/stage/mode/attribution,所以只出现在不过滤的列表里。
          </p>

          {pending ? (
            <ValidationDependencyPending
              what="Validation run 列表"
              method="GET"
              path="/v1/validation/runs"
              scopes={VALIDATION_READ_SCOPES}
            />
          ) : null}
          {!pending && runs.error !== null ? (
            <ValidationErrorState error={runs.error} what="Validation runs" onRetry={runs.reload} />
          ) : null}
          {!pending && runs.error === null && runs.loading && runs.items.length === 0 ? (
            <Loading what="Validation run" />
          ) : null}

          {!pending && runs.error === null && page !== null ? <ListSummary page={page} /> : null}

          {!pending && runs.error === null && !runs.loading && runs.items.length === 0 ? (
            <Empty what="Validation run" hint={emptyReasonText(page?.empty_reason)} />
          ) : null}

          {!pending && runs.error === null && runs.items.length > 0 ? <RunTable rows={runs.items} /> : null}

          {!pending && runs.error === null ? (
            <Pager paged={runs} noun="Validation run" limit={RUN_LIMIT} cursorKind="has_more" />
          ) : null}

          {runs.loadedAt ? <StaleBanner loadedAt={runs.loadedAt} scope="Validation runs" /> : null}
        </div>
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- *
 * Run detail
 * -------------------------------------------------------------------------- */

/** The recorded value of a free-form field: text stays text, structures collapse. */
function RecordedValue({
  value,
  label,
  machine = false,
}: {
  value: unknown
  label: string
  machine?: boolean
}): JSX.Element {
  if (value === null) return <span className="muted">null</span>
  if (value === undefined) return <span className="muted">—</span>
  if (typeof value === 'string') {
    return value.length === 0 ? (
      <span className="muted">(空字符串)</span>
    ) : (
      <RawText value={value} label={label} maxCollapsed={320} machine={machine} />
    )
  }
  return <JsonBlock value={value} label={label} />
}

function StringList({ label, items }: { label: string; items: readonly string[] }): JSX.Element {
  if (items.length === 0) return <p className="muted">{label}:没有。</p>
  return (
    <div className="stack">
      <span className="muted">{label}({items.length})</span>
      <ul className="plain">
        {items.map((item, index) => (
          <li key={`${index}:${item}`}>
            <code>{item}</code>
          </li>
        ))}
      </ul>
    </div>
  )
}

function SectionReason({ section }: { section: ValidationSection }): JSX.Element {
  if (section.present_kind === 'reason_only') {
    return (
      <span>
        记录的是原因,不是内容:<q>{section.recorded_reason ?? '—'}</q>
      </span>
    )
  }
  return <span className="muted">{section.missing_reason ?? '未记录'}</span>
}

function SectionsTable({ sections }: { sections: ValidationSections }): JSX.Element {
  return (
    <TableScroll label="case sections(present_kind)">
      <table className="table" data-testid="case-sections">
        <thead>
          <tr>
            <th>section</th>
            <th>present</th>
            <th>present_kind</th>
            <th>evidence_grade</th>
            <th>值 / 原因</th>
          </tr>
        </thead>
        <tbody>
          {SECTION_ORDER.map((name) => {
            const section = sections[name]
            const isContext = name === 'context'
            return (
              <tr key={name} data-testid={`section-${name}`} data-present={String(section.present)}>
                <td>
                  <code>{name}</code>
                </td>
                <td>{section.present ? 'true' : 'false'}</td>
                <td>
                  <code>{section.present_kind}</code>
                  {isContext ? (
                    <div className="muted" data-testid="context-pack-present">
                      context_pack_present ={' '}
                      <strong>{String(section.context_pack_present === true)}</strong>
                      {section.present && section.context_pack_present !== true
                        ? ' —— 这一段被记录了,但里面没有 context pack,不能当作 pack'
                        : ''}
                    </div>
                  ) : null}
                </td>
                <td>
                  <StatusBadge kind="validation" status={section.evidence_grade} />
                </td>
                <td>
                  {section.present ? (
                    <RecordedValue value={section.value} label={name} />
                  ) : (
                    <SectionReason section={section} />
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </TableScroll>
  )
}

function ChainTable({ chain }: { chain: ValidationCase['chain'] }): JSX.Element {
  return (
    <TableScroll label="T06 chain links">
      <table className="table" data-testid="case-chain">
        <thead>
          <tr>
            <th>link</th>
            <th>present</th>
            <th>source</th>
            <th>missing_reason</th>
          </tr>
        </thead>
        <tbody>
          {CHAIN_ORDER.map((name) => {
            const link = chain[name]
            return (
              <tr key={name} data-testid={`chain-${name}`} data-present={String(link.present)}>
                <td>
                  <code>{name}</code>
                </td>
                <td>
                  {link.present ? (
                    <span className="badge badge--good">✔ present</span>
                  ) : (
                    <span className="badge badge--bad">✖ missing</span>
                  )}
                </td>
                <td>{link.source ? <code>{link.source}</code> : <span className="muted">没有记录来源</span>}</td>
                <td>{link.present ? <span className="muted">—</span> : link.missing_reason ?? '未给出原因'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </TableScroll>
  )
}

function CaseCard({ item, index }: { item: ValidationCase; index: number }): JSX.Element {
  const evidence = item.evidence
  return (
    <div className="stack" data-testid="validation-case">
      <h3>
        <code>{item.case_id ?? `(case ${index})`}</code>
        {item.name ? <> — {item.name}</> : null}
      </h3>
      <KeyValue
        rows={[
          {
            label: 'verdict(按记录)',
            value: item.verdict ? <StatusBadge kind="validation" status={item.verdict} /> : <span className="muted">—</span>,
          },
          {
            label: 'evidence_grade / grade_basis',
            value: (
              <>
                <StatusBadge kind="validation" status={item.evidence_grade} />{' '}
                <span className="muted">{item.grade_basis}</span>
              </>
            ),
          },
          { label: 'failure_class', value: item.failure_class ? <code>{item.failure_class}</code> : <span className="muted">—</span> },
          { label: 'not_applied_reason', value: item.not_applied_reason ?? <span className="muted">—</span> },
        ]}
      />
      <p className="state state--meta">
        verdict 按记录原样展示;本页不重新推导、不上调,也不因为 chain 缺失而改写它。
      </p>

      {item.failure_output !== null && item.failure_output !== undefined ? (
        <RecordedValue value={item.failure_output} label="failure_output(原始输出)" machine />
      ) : null}

      {item.reproduction.length > 0 ? (
        <StringList label="reproduction" items={item.reproduction} />
      ) : (
        <p className="muted">reproduction:没有。</p>
      )}

      <h4>chain(T06 八段)</h4>
      <ChainTable chain={item.chain} />

      <h4>sections(present_kind)</h4>
      <SectionsTable sections={item.sections} />

      <h4>
        evidence({evidence.length})
      </h4>
      {evidence.length === 0 ? (
        <p className="muted">这个 case 没有记录 evidence。</p>
      ) : (
        <TableScroll label="case evidence">
          <table className="table" data-testid="case-evidence">
            <thead>
              <tr>
                <th>recorded</th>
                <th>status</th>
                <th>reason</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((entry, entryIndex) => (
                <tr key={entryIndex}>
                  <td>
                    <RecordedValue value={entry.recorded} label={`evidence[${entryIndex}]`} />
                  </td>
                  <td>
                    <span className="badge badge--warn">{entry.status}</span>
                  </td>
                  <td>{entry.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </div>
  )
}

function RunDetailBody({ run }: { run: ValidationRunDetail }): JSX.Element {
  const notes = Array.isArray(run.notes) ? run.notes : []
  const notImplemented = Array.isArray(run.not_implemented) ? run.not_implemented : []
  return (
    <div className="stack">
      <KeyValue
        rows={[
          { label: 'run_id', value: <code>{dash(run.run_id)}</code> },
          { label: 'stage', value: <code>{dash(run.stage)}</code> },
          { label: 'created_at', value: formatTimestamp(run.created_at) },
          { label: 'verdict(按记录)', value: run.verdict ? <StatusBadge kind="validation" status={run.verdict} /> : <span className="muted">—</span> },
          {
            label: 'verdict_consistent',
            value: (
              <span data-testid="verdict-consistent">
                <code>{String(run.verdict_consistent)}</code>
                <span className="muted">
                  {run.verdict_consistent
                    ? ' —— 与 recorder 的最坏情况规则一致(仅报告,不改写)'
                    : ' —— 记录的 verdict 与 recorder 的最坏情况规则不一致:本页仍按记录展示,不替你改判'}
                </span>
              </span>
            ),
          },
          { label: 'evidence_grade', value: <StatusBadge kind="validation" status={run.evidence_grade} /> },
          { label: 'commit', value: <code className="wrap">{dash(run.commit)}</code> },
          { label: 'schema_version', value: <code>{dash(run.schema_version)}</code> },
          { label: 'core_version', value: <code>{dash(run.core_version)}</code> },
          { label: 'executor', value: <code>{dash(run.executor)}</code> },
          { label: 'reviewer', value: <code>{dash(run.reviewer)}</code> },
          { label: 'verifier', value: <code>{dash(run.verifier)}</code> },
        ]}
      />

      {run.synthetic ? (
        <div className="state state--pending" role="status" data-testid="synthetic">
          <p className="state__headline">synthetic = true:这是一条自带标记的合成记录,不是证据</p>
          <p>
            它被记为 evidence_grade <code>simulated</code>,永远不会被报告为 <code>real</code>。
            {run.synthetic_note ? <> 记录附注:<q>{run.synthetic_note}</q></> : null}
          </p>
        </div>
      ) : run.synthetic_note ? (
        <p className="state state--meta">
          synthetic_note(记录自述):<q>{run.synthetic_note}</q>
        </p>
      ) : null}

      {run.grade_downgraded_by.length > 0 ? (
        <div className="stack">
          <span className="muted">grade_downgraded_by:这些 case 把 run grade 拉低到 bundle mode 之下</span>
          <StringList label="grade_downgraded_by" items={run.grade_downgraded_by} />
        </div>
      ) : null}

      {run.warnings.length > 0 ? (
        <div className="state state--pending" role="status" data-testid="run-warnings">
          <p className="state__headline">warnings({run.warnings.length})</p>
          <ul className="plain">
            {run.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted">没有 run 级 warning。</p>
      )}

      <div data-testid="unknown-record-keys">
        {run.unknown_record_keys.length === 0 ? (
          <p className="muted">unknown_record_keys:没有(记录里的顶层键都被本读取器解释)。</p>
        ) : (
          <StringList label="unknown_record_keys(本读取器不解释,原样保留)" items={run.unknown_record_keys} />
        )}
      </div>

      {run.environment ? <JsonBlock value={run.environment} label="environment" /> : null}
      <StringList label="notes" items={notes} />
      <StringList label="not_implemented" items={notImplemented} />
      {run.redaction_warning ? (
        <p className="state state--pending" data-testid="redaction-warning">
          <strong>redaction_warning:</strong> {run.redaction_warning}
        </p>
      ) : null}
      {run.redactions !== undefined ? <JsonBlock value={run.redactions} label="redactions" /> : null}

      <h3>cases({run.cases.length})</h3>
      {run.cases.length === 0 ? (
        <p className="muted">这条记录没有 case。</p>
      ) : (
        run.cases.map((item, index) => <CaseCard key={item.case_id ?? index} item={item} index={index} />)
      )}
    </div>
  )
}

export function ValidationRunView({ runId }: { runId: string }): JSX.Element {
  const { endpoints } = useSession()
  const [draft, setDraft] = useState('')
  const valid = isValidationRunId(runId)
  const run = useResource(valid ? (signal) => endpoints.getValidationRun(runId, signal) : null, [runId])
  const pending = isPendingError(run.error)

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Validation run(P7-01 只读)</span>
      </nav>
      <Panel title="Validation Run">
        <div className="stack">
          <ValidationSliceStatus />
          {!valid ? <ValidationInvalidId grammar={RUN_ID_GRAMMAR} value={runId} field="run_id" /> : null}
          <div className="filters">
            <label>
              打开另一个 run_id
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="run-20260929T162925Z"
                autoComplete="off"
              />
            </label>
            <button
              type="button"
              disabled={draft.trim() === ''}
              onClick={() => {
                globalThis.location.hash = sourceHref.validationRun(draft.trim())
              }}
            >
              打开
            </button>
            <a href={sourceHref.validationManifest(runId)}>读取 manifest</a>
            <a href={sourceHref.validationRuns()}>返回列表</a>
          </div>

          {valid && pending ? (
            <ValidationDependencyPending
              what="Validation run"
              method="GET"
              path={`/v1/validation/runs/${runId}`}
              scopes={VALIDATION_READ_SCOPES}
            />
          ) : null}
          {valid && !pending && run.status === 'loading' && run.data === null ? <Loading what="Validation run" /> : null}
          {valid && !pending && run.error !== null ? (
            <ValidationErrorState error={run.error} what={`Validation run ${runId}`} onRetry={run.reload} />
          ) : null}
          {valid && !pending && run.data !== null ? <RunDetailBody run={run.data} /> : null}
          {run.loadedAt ? <StaleBanner loadedAt={run.loadedAt} scope="Validation run" /> : null}
        </div>
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- *
 * Manifest
 * -------------------------------------------------------------------------- */

function ManifestFileRow({ runId, file }: { runId: string; file: ValidationManifestFile }): JSX.Element {
  return (
    <tr data-testid="manifest-file">
      <td>
        <a href={sourceHref.validationArtifact(runId, file.artifact_id)}>
          <code>{file.artifact_id}</code>
        </a>
      </td>
      <td>
        <code className="wrap">{file.path}</code>
      </td>
      <td>{file.bytes}</td>
      <td>
        <ValidationHash label="declared_sha256" value={file.sha256} />
      </td>
      <td>
        <StatusBadge kind="validation" status={file.integrity} />
      </td>
      <td>
        <span className={file.bytes_agree ? 'badge badge--good' : 'badge badge--bad'}>
          {String(file.bytes_agree)}
        </span>
      </td>
    </tr>
  )
}

function ManifestBody({ manifest }: { manifest: ValidationManifest }): JSX.Element {
  const unauthenticated = manifest.manifest_authentication === 'unauthenticated'
  return (
    <div className="stack">
      <KeyValue
        rows={[
          { label: 'status', value: <StatusBadge kind="validation" status={manifest.status} /> },
          { label: 'run_id', value: <code>{manifest.run_id}</code> },
          { label: 'frozen_at', value: formatTimestamp(manifest.frozen_at) },
          { label: 'declared_mode', value: <code>{dash(manifest.declared_mode)}</code> },
          {
            label: 'mode(有效,派生)',
            value: (
              <>
                <code>{manifest.mode}</code>
                <span className="muted"> —— {manifest.mode_basis}</span>
                {manifest.declared_mode !== undefined &&
                manifest.declared_mode !== null &&
                manifest.declared_mode !== manifest.mode ? (
                  <div className="muted" data-testid="mode-differs">
                    清单自述 mode = <code>{manifest.declared_mode}</code>,但有效 mode 是{' '}
                    <code>{manifest.mode}</code>:mode 是派生出来的,不信任自述。
                  </div>
                ) : null}
              </>
            ),
          },
          {
            label: 'projects',
            value:
              manifest.projects.length > 0 ? (
                <span>
                  {manifest.projects.map((project) => (
                    <code key={project}>{project}</code>
                  ))}
                </span>
              ) : (
                <span className="muted">—(没有项目归属)</span>
              ),
          },
          {
            label: 'manifest_sha256',
            value: <ValidationHash label="sha256" value={manifest.manifest_sha256} />,
          },
          { label: 'manifest_authentication', value: <code>{manifest.manifest_authentication}</code> },
        ]}
      />

      {unauthenticated ? (
        <div className="state state--pending" role="status" data-testid="manifest-unauthenticated">
          <p className="state__headline">
            manifest_authentication = unauthenticated:这份 manifest 是<strong>未签名</strong>的
          </p>
          <p>
            <code>frozen_at</code>、<code>mode</code> 与 <code>credential_shaped</code> 都是清单的
            <strong>自述</strong>,没有签名也没有被验证;读取器从不称它们已验证。只读契约无法验证一份自述的
            manifest,所以这里也不这么声称。
          </p>
        </div>
      ) : null}

      {manifest.status !== 'frozen' ? (
        <div className="state state--meta" role="status" data-testid="manifest-not-frozen">
          <p className="state__headline">manifest status = {manifest.status}</p>
          <p>
            {manifest.status === 'absent'
              ? manifest.reason ??
                '该 run 目录没有 manifest.json:这份记录早于 bundle 约定,它的历史不会被重建,artifact 也不可寻址。'
              : `清单违反了规则${manifest.rule ? `:${manifest.rule}` : ''};它的 artifact 全部不可寻址,而不是部分可信。`}
          </p>
        </div>
      ) : null}

      <JsonBlock value={manifest.sources} label="sources(原样返回)" />

      <h3>files({manifest.files.length})</h3>
      {manifest.files.length === 0 ? (
        <p className="muted">没有可寻址的文件。</p>
      ) : (
        <TableScroll label="manifest files">
          <table className="table" data-testid="manifest-files">
            <thead>
              <tr>
                <th>artifact_id</th>
                <th>path</th>
                <th>bytes</th>
                <th>declared_sha256</th>
                <th>integrity</th>
                <th>bytes_agree</th>
              </tr>
            </thead>
            <tbody>
              {manifest.files.map((file) => (
                <ManifestFileRow key={file.artifact_id} runId={manifest.run_id} file={file} />
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </div>
  )
}

export function ValidationManifestView({ runId }: { runId: string }): JSX.Element {
  const { endpoints } = useSession()
  const valid = isValidationRunId(runId)
  const manifest = useResource(
    valid ? (signal) => endpoints.getValidationManifest(runId, signal) : null,
    [runId],
  )
  const pending = isPendingError(manifest.error)

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Validation manifest(P7-01 只读)</span>
      </nav>
      <Panel title="Validation Manifest">
        <div className="stack">
          <ValidationSliceStatus />
          {!valid ? <ValidationInvalidId grammar={RUN_ID_GRAMMAR} value={runId} field="run_id" /> : null}
          <div className="filters">
            <a href={sourceHref.validationRun(runId)}>返回 run 详情</a>
            <a href={sourceHref.validationRuns()}>返回列表</a>
          </div>

          {valid && pending ? (
            <ValidationDependencyPending
              what="Validation manifest"
              method="GET"
              path={`/v1/validation/runs/${runId}/manifest`}
              scopes={VALIDATION_READ_SCOPES}
            />
          ) : null}
          {valid && !pending && manifest.status === 'loading' && manifest.data === null ? (
            <Loading what="Validation manifest" />
          ) : null}
          {valid && !pending && manifest.error !== null ? (
            <ValidationErrorState
              error={manifest.error}
              what={`${runId} 的 manifest`}
              onRetry={manifest.reload}
            />
          ) : null}
          {valid && !pending && manifest.data !== null ? <ManifestBody manifest={manifest.data} /> : null}
          {manifest.loadedAt ? <StaleBanner loadedAt={manifest.loadedAt} scope="Validation manifest" /> : null}
        </div>
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- *
 * Artifact metadata + content
 * -------------------------------------------------------------------------- */

/** The content body. `content.content` is a text field; `RawText` renders a text node. */
function ArtifactContentBody({ content }: { content: ValidationArtifactContent }): JSX.Element {
  return (
    <div className="stack" data-testid="artifact-content">
      <KeyValue
        rows={[
          { label: 'form', value: <code>{content.form}</code> },
          { label: 'content_is_original', value: <code>{String(content.content_is_original)}</code> },
          { label: 'media_type', value: <code>{dash(content.media_type)}</code> },
          { label: 'bytes(实际提供)', value: <code>{content.bytes}</code> },
          {
            label: 'mask',
            value: content.mask_algorithm ? (
              <>
                <code>{content.mask_algorithm}</code>
                <span className="muted"> v{content.mask_version ?? '—'}</span>
              </>
            ) : (
              <span className="muted">—(original form 不经掩码)</span>
            ),
          },
          {
            label: 'digests(三个含义不同,分开标注)',
            value: (
              <span className="stack">
                <ValidationHash label="served_sha256(实际提供)" value={content.served_sha256} />
                <ValidationHash label="original_sha256(原文)" value={content.original_sha256} />
                <ValidationHash label="masked_sha256(掩码后)" value={content.masked_sha256} />
              </span>
            ),
          },
        ]}
      />
      {/* Untrusted text: `RawText` renders `<pre>{text}</pre>`. No HTML, no iframe, no
          srcDoc, no dangerouslySetInnerHTML. */}
      <RawText value={content.content} label={`content(form=${content.form})`} maxCollapsed={2000} />
    </div>
  )
}

/** `403 original_withheld`: the reason and the digest that *is* available. */
function OriginalWithheld({ error }: { error: CoreApiError }): JSX.Element {
  const details =
    typeof error.details === 'object' && error.details !== null && !Array.isArray(error.details)
      ? (error.details as Record<string, unknown>)
      : {}
  const reason = typeof details['reason'] === 'string' ? details['reason'] : null
  const masked = typeof details['masked_sha256'] === 'string' ? details['masked_sha256'] : null
  const reasons: Record<string, string> = {
    credential_shaped_declared: 'manifest 声明 credential_shaped: true。',
    mask_not_noop: 'canonical.redact_text 对这段内容不是 no-op:原文里确实有会被掩码的形状。',
    not_utf8: '字节不是严格 UTF-8。',
  }
  return (
    <div className="state state--pending" role="status" data-testid="original-withheld">
      <p className="state__headline">
        original form 被拒绝(403 <code>{error.code}</code>)
        {reason ? (
          <>
            {' '}
            · <code>details.reason = {reason}</code>
          </>
        ) : null}
      </p>
      <p>
        {reason && reasons[reason]
          ? reasons[reason]
          : reason
            ? `Core 给出的原因:${reason}(原样展示)。`
            : 'Core 没有给出 details.reason。'}
        {' '}这不是读取失败,也不是「没有内容」:Core 明确告诉你 original 不可提供。
      </p>
      <p>
        仍然可用的 masked 摘要:<ValidationHash label="details.masked_sha256" value={masked} />
      </p>
      <JsonBlock value={error.details} label="error.details" />
    </div>
  )
}

function ArtifactBody({
  runId,
  artifact,
}: {
  runId: string
  artifact: ValidationArtifact
}): JSX.Element {
  const { endpoints } = useSession()
  const [form, setForm] = useState<'masked' | 'original'>('masked')
  const masked = useResource(
    (signal) => endpoints.getValidationArtifactContent(runId, artifact.artifact_id, 'masked', signal),
    [runId, artifact.artifact_id],
  )
  const original = useResource(
    form === 'original'
      ? (signal) => endpoints.getValidationArtifactContent(runId, artifact.artifact_id, 'original', signal)
      : null,
    [runId, artifact.artifact_id, form],
  )
  const offersOriginal = artifact.forms.includes('original')
  const withheld = original.error !== null && original.error.code === 'original_withheld'
  const scan = artifact.content_scan

  return (
    <div className="stack">
      <KeyValue
        rows={[
          { label: 'artifact_id', value: <code>{artifact.artifact_id}</code> },
          { label: 'path(按清单记录)', value: <code className="wrap">{artifact.path}</code> },
          { label: 'media_type', value: <code>{dash(artifact.media_type)}</code> },
          { label: 'bytes(实际)', value: <code>{artifact.bytes}</code> },
          { label: 'declared_bytes', value: <code>{artifact.declared_bytes}</code> },
          {
            label: 'declared_sha256',
            value: <ValidationHash label="declared_sha256" value={artifact.declared_sha256} />,
          },
          { label: 'integrity', value: <StatusBadge kind="validation" status={artifact.integrity} /> },
          {
            label: 'credential_shaped(清单自述)',
            value:
              artifact.credential_shaped === null ? (
                <span className="muted">—(清单未声明)</span>
              ) : (
                <code>{String(artifact.credential_shaped)}</code>
              ),
          },
          {
            label: 'forms(可提供的 form)',
            value: (
              <span>
                {artifact.forms.map((item) => (
                  <code key={item}>{item}</code>
                ))}
              </span>
            ),
          },
        ]}
      />

      <KeyValue
        rows={[
          {
            label: 'digests(original 与 masked 是两个不同的值)',
            value: (
              <span className="stack" data-testid="artifact-digests">
                <ValidationHash label="digests.original" value={artifact.digests.original} />
                <ValidationHash label="digests.masked" value={artifact.digests.masked} />
              </span>
            ),
          },
        ]}
      />

      <KeyValue
        rows={[
          {
            label: 'content_scan(读取器对实际字节的扫描)',
            value: (
              <span className="stack" data-testid="content-scan">
                <span>
                  performed <code>{String(scan.performed)}</code>
                </span>
                <span>
                  mask_is_noop <code>{String(scan.mask_is_noop)}</code>
                </span>
                <span>
                  credential_shaped <code>{String(scan.credential_shaped)}</code>
                </span>
              </span>
            ),
          },
        ]}
      />

      <div className="filters" data-testid="artifact-form">
        <button type="button" aria-pressed={form === 'masked'} onClick={() => setForm('masked')}>
          读取 masked(默认)
        </button>
        <button
          type="button"
          aria-pressed={form === 'original'}
          onClick={() => setForm('original')}
        >
          {offersOriginal ? '读取 original' : '请求 original(预期被拒绝,以显示原因)'}
        </button>
      </div>
      {offersOriginal ? (
        <p className="state state--meta">
          original 可用,因为 Core 报告 <code>forms</code> 含 <code>original</code>
          (masker 对这段内容是 no-op、清单未声明 credential_shaped、且字节是严格 UTF-8)。
        </p>
      ) : (
        <p className="state state--meta" data-testid="original-not-offered">
          <code>forms</code> 只含 <code>masked</code>,所以 Core 不把 original 列为可提供的 form。
          这台 Inspector 仍允许你请求一次:Core 会用它自己的 <code>details.reason</code> 回答为什么
          (metadata 没有单独给出原因,所以这里不替你猜)。
        </p>
      )}

      {form === 'masked' ? (
        <>
          {masked.status === 'loading' && masked.data === null ? <Loading what="masked artifact 内容" /> : null}
          {masked.error !== null ? (
            <ValidationErrorState
              error={masked.error}
              what={`artifact ${artifact.artifact_id} 的 masked 内容`}
              onRetry={masked.reload}
            />
          ) : null}
          {masked.data !== null ? <ArtifactContentBody content={masked.data} /> : null}
        </>
      ) : (
        <>
          {original.status === 'loading' && original.data === null ? <Loading what="original artifact 内容" /> : null}
          {withheld ? (
            <>
              <OriginalWithheld error={original.error as CoreApiError} />
              <p className="state state--meta">下面仍是 Core 可提供的 masked 内容:</p>
              {masked.data !== null ? <ArtifactContentBody content={masked.data} /> : null}
            </>
          ) : null}
          {original.error !== null && !withheld ? (
            <ValidationErrorState
              error={original.error}
              what={`artifact ${artifact.artifact_id} 的 original 内容`}
              onRetry={original.reload}
            />
          ) : null}
          {original.data !== null ? <ArtifactContentBody content={original.data} /> : null}
        </>
      )}
    </div>
  )
}

export function ValidationArtifactView({
  runId,
  artifactId,
}: {
  runId: string
  artifactId: string
}): JSX.Element {
  const { endpoints } = useSession()
  const runValid = isValidationRunId(runId)
  const artifactValid = isValidationArtifactId(artifactId)
  const valid = runValid && artifactValid
  const artifact = useResource(
    valid ? (signal) => endpoints.getValidationArtifact(runId, artifactId, signal) : null,
    [runId, artifactId],
  )
  const pending = isPendingError(artifact.error)

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Validation artifact(P7-01 只读)</span>
      </nav>
      <Panel title="Validation Artifact">
        <div className="stack">
          <ValidationSliceStatus />
          {!runValid ? <ValidationInvalidId grammar={RUN_ID_GRAMMAR} value={runId} field="run_id" /> : null}
          {!artifactValid ? (
            <ValidationInvalidId grammar={ARTIFACT_ID_GRAMMAR} value={artifactId} field="artifact_id" />
          ) : null}
          <div className="filters">
            <a href={sourceHref.validationRun(runId)}>返回 run 详情</a>
            <a href={sourceHref.validationManifest(runId)}>读取 manifest</a>
            <a href={sourceHref.validationRuns()}>返回列表</a>
          </div>

          {valid && pending ? (
            <ValidationDependencyPending
              what="Validation artifact"
              method="GET"
              path={`/v1/validation/runs/${runId}/artifacts/${artifactId}`}
              scopes={VALIDATION_READ_SCOPES}
            />
          ) : null}
          {valid && !pending && artifact.status === 'loading' && artifact.data === null ? (
            <Loading what="Validation artifact" />
          ) : null}
          {valid && !pending && artifact.error !== null ? (
            <ValidationErrorState
              error={artifact.error}
              what={`artifact ${artifactId}`}
              onRetry={artifact.reload}
            />
          ) : null}
          {valid && !pending && artifact.data !== null ? (
            <ArtifactBody runId={runId} artifact={artifact.data} />
          ) : null}
          {artifact.loadedAt ? <StaleBanner loadedAt={artifact.loadedAt} scope="Validation artifact" /> : null}
        </div>
      </Panel>
    </div>
  )
}
