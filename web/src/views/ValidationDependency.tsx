/**
 * The honest-state pieces every P6-03c Validation view needs.
 *
 * The five P7-01 routes exist on the P7-01 branch (contract
 * `web/contracts/p7-validation-read.v1.json`, pinned to its implementation commit) and
 * **not** in this baseline's Core. They are opt-in behind the proxy's
 * `P6_ENABLE_P7_READS` switch, so four states have to stay distinguishable, and each is a
 * separate component here rather than a string in a view:
 *
 * - the proxy does not forward the path (`dependency-pending`);
 * - the field you typed is not the recorder's/bundle's id shape (`invalid-id`), refused
 *   before any request so a typo is never explained as an unmerged dependency;
 * - Core answered with a *refusal* — and the seven P7 codes each name a different
 *   condition (`not-configured`, `not-found`, `conflict`, `forbidden`, `unauthenticated`,
 *   `payload-too-large`, `unsupported-media`), never an empty list;
 * - Core answered with a body.
 *
 * The panels deliberately mirror the P6-03b1 ones in `MemoryDependency.tsx` rather than
 * sharing them. Those are frozen against their own evidence bundle; refactoring them now
 * would change a slice that has already been reviewed, for no behavioural gain.
 *
 * Two properties this file keeps:
 *
 * 1. **A refusal is never rendered as "no data".** `503 validation_not_configured` with
 *    `details.reason: "unset"` is the operator's run root being absent — a configuration
 *    fact — and it says so instead of showing zero runs.
 * 2. **`details.reason` is read, not guessed.** The API distinguishes `unset`, `missing`,
 *    `not_a_directory`, `symlinked_root`, `root_unreadable`, `index_incomplete` and
 *    `artifact_unreadable`, so the panel renders the reason it was given and prints an
 *    unrecognised one verbatim rather than mapping it onto a plausible-sounding cause.
 */

import type { CoreApiError } from '../api/errors'
import { isDependencyPending } from '../api/errors'
import { JsonBlock } from '../components/JsonBlock'
import { ErrorPanel } from '../components/States'

/**
 * Every route in this slice requires exactly `validation:read`. In `acl` deployment mode
 * an *unattributed* run (no manifest `projects`, including every corrupt/duplicate record)
 * additionally needs `validation:unattributed`, which Core refuses to issue to an `agent`
 * key. The views state the full requirement; they never hold the scope themselves.
 */
export const VALIDATION_READ_SCOPES = ['validation:read'] as const
export const VALIDATION_UNATTRIBUTED_SCOPE = 'validation:unattributed'

/**
 * The contract's pinned source (`web/contracts/p7-validation-read.v1.json` → `source.ref`).
 * Quoted so a reader can tell which reader the field list came from, exactly as the P4
 * panels quote `0c4f8f0`.
 */
export const P7_CONTRACT_REF = 'e79c11728dc0c7bfc7cbe5191642268e3705caa4'

/**
 * The recorder's run id and the bundle's artifact id grammars, copied from the frozen
 * contract (`^run-[0-9]{8}T[0-9]{6}Z$`, `^art_[a-z0-9][a-z0-9_.-]{0,63}$`). They are
 * deliberately not `isCoreId`: neither is a Core public id. Validating locally matters
 * because the proxy's allowlist spells these same shapes, so a typo would otherwise be
 * refused by the *proxy* and misreported as "P7 not merged".
 */
export const RUN_ID_GRAMMAR = 'run-[0-9]{8}T[0-9]{6}Z'
export const ARTIFACT_ID_GRAMMAR = 'art_[a-z0-9][a-z0-9_.-]{0,63}'

const RUN_ID_RE = /^run-[0-9]{8}T[0-9]{6}Z$/
const ARTIFACT_ID_RE = /^art_[a-z0-9][a-z0-9_.-]{0,63}$/

export function isValidationRunId(value: string): boolean {
  return RUN_ID_RE.test(value)
}

export function isValidationArtifactId(value: string): boolean {
  return ARTIFACT_ID_RE.test(value)
}

/**
 * The standing status of this slice, shown on every Validation view whether or not the read
 * succeeded.
 *
 * Without this line a screenshot taken against a P7-enabled proxy would show a populated
 * run record and nothing else — exactly the "P7 passed" reading this milestone must not
 * invite. The contract is frozen and independently validated, but ADR 0015 §8 records
 * acceptance criterion 1 (a real FAIL run, classified, raw output traceable) as **BLOCKED**
 * because no FAIL case exists on disk, and the read slice is not merged into this baseline.
 */
export function ValidationSliceStatus(): JSX.Element {
  return (
    <p className="state state--meta" data-testid="slice-status">
      <strong>Validation 记录读取</strong> —— 按原始记录展示 verdict 与缺失材料，不升级结论。
      这不是完整 P7/V1 发布验收；模拟记录不能替代真实运行证据。
    </p>
  )
}

/** The one place that decides how "this path is not being forwarded" looks. */
export function ValidationDependencyPending({
  what,
  method,
  path,
  scopes,
}: {
  what: string
  method: string
  path: string
  scopes: readonly string[]
}): JSX.Element {
  return (
    <div className="state state--pending" role="status" data-testid="dependency-pending">
      <p className="state__headline">
        依赖未满足:{what} 属于 P7-01(Validation 只读切片),仪表盘当前不转发这条路径
      </p>
      <p>
        本页读取的 <code>{method}</code> <code>{path}</code> 只存在于 P7-01 分支。仪表盘的 live
        白名单<strong>不会转发它</strong> —— 代理以 <code>proxy_path_not_allowed</code> 拒绝,而不是把
        请求送给一个必然 404 的 Core。有两种情况会这样:P7-01 尚未合并进本基线,或者 Core 已带该切片、
        但代理启动时没有打开 <code>P6_ENABLE_P7_READS</code>。代理的拒绝理由无法区分这两者,所以这里
        也不替你断定是哪一个。
      </p>
      <ul className="plain">
        <li>
          需要的 scope:<code>{scopes.join(' + ')}</code>(由 Core 校验,前端从不代持)
        </li>
        <li>
          契约来源:固定提交 <code>{P7_CONTRACT_REF}</code>(固定 SHA,不是分支名),冻结在
          <code> web/contracts/p7-validation-read.v1.json</code>
        </li>
        <li>
          状态:<strong>PARTIAL / DEPENDENCY_PENDING</strong> —— 本页不是已通过的 P6 能力
        </li>
      </ul>
      <p className="state__meta">
        要在真实 Core 上打开它,需要一个带 P7-01 只读切片的 Core,并以显式开关启动代理转发这五条路径
        (见 <code>web/server/core-paths.json</code> 的 <code>p7_pending_paths</code>)。
      </p>
    </div>
  )
}

/** A malformed id is refused here, with the right reason, before any request is made. */
export function ValidationInvalidId({
  grammar,
  value,
  field,
}: {
  grammar: string
  value: string
  field: string
}): JSX.Element {
  return (
    <div className="state state--error" role="alert" data-testid="invalid-id">
      <p className="state__headline">非法 id:{field}</p>
      <p>
        <code>{value}</code> 不符合 P7 的 id 形状 <code>{grammar}</code>。请求没有被发出 —— 否则代理会
        以 <code>proxy_path_not_allowed</code> 拒绝它,那会被误报成「P7 未合并」。
      </p>
    </div>
  )
}

/** `details` is `unknown`; read it defensively and never let a non-object through. */
function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function detailsOf(error: CoreApiError): Record<string, unknown> {
  return recordOf(error.details)
}

function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/**
 * The reason strings `503 validation_not_configured` can carry, each rendered as the
 * configuration condition it is. An unrecognised reason is printed verbatim rather than
 * silently mapped onto one of these.
 */
const NOT_CONFIGURED_REASONS: Record<string, string> = {
  unset:
    '运行根未配置:环境变量 JASMINE_CORE_VALIDATION_RUNS 没有设置。运行根是操作者配置,没有默认值 —— ' +
    '因此每一条 Validation 路由都会这样拒绝,而不是返回一个空列表。',
  missing: '配置的运行根路径不存在。',
  not_a_directory: '配置的运行根不是目录。',
  symlinked_root:
    '配置的运行根是符号链接。读取器以 O_DIRECTORY|O_NOFOLLOW 打开根,所以符号链接根被拒绝。',
  root_unreadable: '运行根存在,但打不开或不可读(权限/IO 错误)。',
  index_incomplete:
    '遍历被判定为不完整:某个子树读不了,所以 Core 拒绝分页 —— 它不会给你一页可能悄悄漏掉记录的索引。',
  artifact_unreadable: '清单声明的文件存在,但读不到(权限/IO 错误;不是符号链接)。',
}

/**
 * `503 validation_not_configured`: the specific configuration condition from
 * `details.reason`. This is the state that must never read as "no runs".
 */
export function ValidationNotConfigured({ error }: { error: CoreApiError }): JSX.Element {
  const details = detailsOf(error)
  const reason = textOf(details['reason'])
  const counts = recordOf(details['counts'])
  const samples = Array.isArray(details['samples']) ? details['samples'] : []
  return (
    <div className="state state--error" role="alert" data-testid="not-configured">
      <p className="state__headline">
        Validation 未配置 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
        {reason ? (
          <>
            {' '}
            · <code>details.reason = {reason}</code>
          </>
        ) : null}
      </p>
      <p>
        {reason && NOT_CONFIGURED_REASONS[reason]
          ? NOT_CONFIGURED_REASONS[reason]
          : reason
            ? `Core 给出的原因:${reason}(本页不认识这个 reason,原样展示,不替你归类)。`
            : 'Core 没有给出 details.reason,所以这里不替你猜测原因。'}
      </p>
      <p className="state__meta">
        这是<strong>拒绝</strong>,不是空集:没有运行根就没有任何 run 可读,也不会伪造 0 条结果。
      </p>
      {Object.keys(counts).length > 0 || samples.length > 0 ? (
        <details>
          <summary>遍历诊断(details)</summary>
          {Object.keys(counts).length > 0 ? <JsonBlock value={counts} label="details.counts" startsOpen /> : null}
          {samples.length > 0 ? <JsonBlock value={samples} label="details.samples" startsOpen /> : null}
        </details>
      ) : null}
    </div>
  )
}

/** `503 validation_index_too_large`: a walk cap was exceeded, so no page may be served. */
export function ValidationIndexTooLarge({ error }: { error: CoreApiError }): JSX.Element {
  const details = detailsOf(error)
  return (
    <div className="state state--error" role="alert" data-testid="index-too-large">
      <p className="state__headline">
        索引超过上限 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        运行根的遍历超过了{' '}
        {textOf(details['cap']) ? <code>{textOf(details['cap'])}</code> : '一个遍历上限'}
        {typeof details['limit'] === 'number' ? <> (limit {details['limit']})</> : null}。Core
        宁可拒绝也不返回一个悄悄截断的索引。
      </p>
      <p className="state__meta">这是拒绝,不是空集。</p>
    </div>
  )
}

/** `503 memory_deployment_not_ready`: an `acl` deployment with no grant rows is refused. */
export function ValidationDeploymentNotReady({ error }: { error: CoreApiError }): JSX.Element {
  return (
    <div className="state state--error" role="alert" data-testid="deployment-not-ready">
      <p className="state__headline">
        部署未就绪 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        这是 <code>acl</code> 部署模式但没有任何授权行:P7 复用了 P4 的授权机制
        (<code>memory_project_actors</code>),<code>require_mode_ready()</code> 先拒绝,而不是把整个运行
        根当作人人可读。先给 actor 授权,再读取。
      </p>
      {error.details ? <JsonBlock value={error.details} label="error.details" /> : null}
    </div>
  )
}

/** `404 validation_run_not_found` / `404 artifact_not_found`, with `details.reason` read. */
export function ValidationNotFound({ error, what }: { error: CoreApiError; what: string }): JSX.Element {
  const details = detailsOf(error)
  const reason = textOf(details['reason'])
  const reasons: Record<string, string> = {
    manifest_absent: '该 run 目录没有 manifest.json,所以清单声明的 artifact 都不可寻址。',
    manifest_corrupt: 'manifest 存在但违反了约定规则,它的 artifact 全部不可信,而不是部分可信。',
    unknown_artifact_id: 'manifest 里没有这个 artifact_id。',
    symlink_refused: '清单声明的文件是符号链接:读取器在打开时拒绝它(这是要报告的攻击,不是隐藏整份清单的理由)。',
    missing: '清单声明的文件在磁盘上不存在。',
  }
  return (
    <div className="state state--meta" role="status" data-testid="not-found">
      <p className="state__headline">
        Core 返回 {error.code}:{what} 不存在{reason ? `(reason: ${reason})` : ''}
      </p>
      <p>
        {reason && reasons[reason]
          ? reasons[reason]
          : reason
            ? `Core 给出的 reason 是 ${reason};本页不认识它,原样展示。`
            : 'Core 没有给出 details.reason。'}
      </p>
      <p>
        {error.code === 'validation_run_not_found' ? (
          <>
            在 <code>acl</code> 模式下,「run 不存在」和「当前 actor 无权读取」是<strong>同一个</strong> 404
            与同一个 code,Core 刻意不让读取者区分它们,所以这里也不替你猜测是哪一个。
          </>
        ) : (
          <>这个 artifact 无法寻址。artifact 只能通过一份通过全部语法规则的 manifest 访问。</>
        )}
      </p>
      {error.details ? <JsonBlock value={error.details} label="error.details" /> : null}
    </div>
  )
}

/**
 * The `409` family, each named for its own condition. They are not interchangeable:
 * `validation_run_corrupt` is an unusable record, `validation_run_ambiguous` is two
 * parseable records sharing one stem, `validation_manifest_corrupt` is a manifest breaking
 * a rule, and `artifact_modified` is bytes disagreeing with the frozen declaration.
 */
export function ValidationConflict({ error, what }: { error: CoreApiError; what: string }): JSX.Element {
  const details = detailsOf(error)
  const rule = textOf(details['rule'])
  const headline: Record<string, string> = {
    validation_run_corrupt: `${what} 的记录 run-shaped 但不可用(无法解析、run_id 不符或缺必需字段)`,
    validation_run_ambiguous: `同一个 stem 有多份可解析记录:${what} 无法确定是哪一份`,
    validation_manifest_corrupt: `manifest 存在但违反了冻结的清单规则:${what} 的 artifact 全部不可寻址`,
    artifact_modified: `${what} 的字节与 manifest 声明不一致`,
  }
  return (
    <div className="state state--error" role="alert" data-testid="conflict">
      <p className="state__headline">
        <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''} ——{' '}
        {headline[error.code] ?? '冲突'}
      </p>
      {rule ? (
        <p>
          违反的规则:<code>{rule}</code>
        </p>
      ) : null}
      <p className="state__meta">
        Core message: <q>{error.message}</q>
      </p>
      {error.code === 'artifact_modified' ? (
        // The two digests are the evidence for this refusal, so they are shown whole
        // rather than left inside a collapsed `error.details`.
        <p className="stack">
          <ValidationHash label="declared_sha256" value={textOf(details['declared_sha256'])} />
          <ValidationHash label="actual_sha256" value={textOf(details['actual_sha256'])} />
          <span className="muted">
            declared_bytes {typeof details['declared_bytes'] === 'number' ? details['declared_bytes'] : '—'} ·
            actual_bytes {typeof details['actual_bytes'] === 'number' ? details['actual_bytes'] : '—'}
          </span>
        </p>
      ) : null}
      {error.code === 'artifact_modified' ? (
        <p>
          读取器用同一份 manifest 校验权限与字节:它不会把被修改过的字节当作 artifact 内容返回。
        </p>
      ) : null}
      {Object.keys(details).length > 0 ? <JsonBlock value={details} label="error.details" /> : null}
    </div>
  )
}

/** `403 forbidden_scope`: the token lacks the route's scope. Never dressed as "missing". */
export function ValidationForbidden({ error }: { error: CoreApiError }): JSX.Element {
  const details = detailsOf(error)
  const required = details['required']
  return (
    <div className="state state--forbidden" role="alert" data-testid="forbidden">
      <p className="state__headline">
        缺少 scope — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        需要的 scope:<code>{textOf(required) ?? 'validation:read'}</code>。本视图不会改用更高权限的凭据,
        也不会把拒绝当作空集。
        {required === undefined ? (
          <>
            {' '}
            若这是一个无项目归属的 run,<code>{VALIDATION_UNATTRIBUTED_SCOPE}</code> 也是必需的,且 Core
            不会把它签发给 <code>agent</code> key。
          </>
        ) : null}
      </p>
      {error.details ? <JsonBlock value={error.details} label="error.details" /> : null}
    </div>
  )
}

/** `401 unauthenticated`: the token is missing, unknown or revoked. Sign in again. */
export function ValidationUnauthenticated({ what }: { what: string }): JSX.Element {
  return (
    <div className="state state--error" role="alert" data-testid="unauthenticated">
      <p className="state__headline">未认证(401):读取{what}的 token 缺失、未知或已撤销</p>
      <p>
        点右上角「退出(清除内存中的 token)」后重新登录。本页不会自动重试、不会改用其它凭据,也不会把 401
        显示成空列表。
      </p>
    </div>
  )
}

/** `413 payload_too_large`: the artifact (or its expanded masked form) exceeds the cap. */
export function ValidationPayloadTooLarge({ error, what }: { error: CoreApiError; what: string }): JSX.Element {
  const details = detailsOf(error)
  return (
    <div className="state state--error" role="alert" data-testid="payload-too-large">
      <p className="state__headline">
        内容超过上限 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        {what} 超过{' '}
        {typeof details['limit'] === 'number' ? <code>{details['limit']} bytes</code> : '读取上限'}。
        {details['form'] === 'masked' ? (
          <>
            {' '}
            注意这是 <strong>masked</strong> 结果超限:掩码会把 <code>token=x</code> 展开成
            <code> token=[REDACTED]</code>,所以原文件在上限内也可能出现这种情况。
          </>
        ) : null}
      </p>
      {error.details ? <JsonBlock value={error.details} label="error.details" /> : null}
    </div>
  )
}

/** `415 unsupported_media_type`: only text artifacts have a content form. */
export function ValidationUnsupportedMedia({ error, what }: { error: CoreApiError; what: string }): JSX.Element {
  const details = detailsOf(error)
  const mediaType = textOf(details['media_type'])
  return (
    <div className="state state--error" role="alert" data-testid="unsupported-media">
      <p className="state__headline">
        不是可读文本 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>
        {what} 的 media_type {mediaType ? <code>{mediaType}</code> : '不在允许列表内'}
        ,Core 不会猜一个类型或返回原始字节。允许的是 <code>text/*</code> 与
        <code> application/json</code> / <code>jsonl</code> / <code>x-ndjson</code> /
        <code> xml</code> / <code>yaml</code>。
        {textOf(details['reason']) === 'not_utf8' ? ' 这份字节不是严格 UTF-8,按文本返回会静默损坏它。' : null}
      </p>
      {error.details ? <JsonBlock value={error.details} label="error.details" /> : null}
    </div>
  )
}

/**
 * The one dispatcher every Validation read uses for a non-pending error, so the same code
 * renders the same condition on the list, the detail, the manifest and the artifact.
 * Anything this build does not recognise falls back to the generic error panel rather
 * than being forced into a known condition.
 */
export function ValidationErrorState({
  error,
  what,
  onRetry,
}: {
  error: CoreApiError
  what: string
  onRetry?: () => void
}): JSX.Element | null {
  if (isDependencyPending(error)) {
    return null
  }
  switch (error.code) {
    case 'unauthenticated':
      return <ValidationUnauthenticated what={what} />
    case 'forbidden_scope':
      return <ValidationForbidden error={error} />
    case 'validation_run_not_found':
    case 'artifact_not_found':
      return <ValidationNotFound error={error} what={what} />
    case 'validation_run_corrupt':
    case 'validation_run_ambiguous':
    case 'validation_manifest_corrupt':
    case 'artifact_modified':
      return <ValidationConflict error={error} what={what} />
    case 'payload_too_large':
      return <ValidationPayloadTooLarge error={error} what={what} />
    case 'unsupported_media_type':
      return <ValidationUnsupportedMedia error={error} what={what} />
    case 'validation_not_configured':
      return <ValidationNotConfigured error={error} />
    case 'validation_index_too_large':
      return <ValidationIndexTooLarge error={error} />
    case 'memory_deployment_not_ready':
      return <ValidationDeploymentNotReady error={error} />
    default:
      return <ErrorPanel error={error} what={what} onRetry={onRetry} />
  }
}

/** A digest renders whole, wrapped, because a truncated hash is not evidence. */
export function ValidationHash({
  label,
  value,
}: {
  label: string
  value: string | null | undefined
}): JSX.Element {
  return (
    <span className="kv__digest">
      <span className="muted">{label}</span> <code className="wrap">{value ?? '—'}</code>
    </span>
  )
}
