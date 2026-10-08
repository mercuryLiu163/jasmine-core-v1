/**
 * The pieces every P6-03b1 Memory view needs to be honest about what it is.
 *
 * P4 is **not merged**: baseline `850fbec` (schema 8) has no `src/jasmine_core/memory/` and
 * no `/v1/memory/*` route, and the contract is pinned to commit
 * `0c4f8f0e2118711ac9aa2a7006fddc6982962248` — a commit whose phase acceptance has **not**
 * passed. Three states therefore have to stay distinguishable, and each is a separate
 * component here rather than a string in a view:
 *
 * - the proxy does not forward the path (`dependency-pending`);
 * - the field you typed is not a Core id (`invalid-id`), refused before any request so a
 *   typo is never explained as a missing dependency;
 * - Core answered (`not_found`, `forbidden_scope`, an empty list, or a record).
 *
 * The panels deliberately mirror the P6-03a ones rather than sharing them. P6-03a's copy is
 * frozen against its own evidence bundle; refactoring it to share code would change a slice
 * that has already been reviewed and frozen, for no behavioural gain. A later P6-03a
 * re-verification is the right moment to merge them.
 */

import { isDependencyPending, type CoreApiError } from '../api/errors'

/** Every Memory view states this, whether or not the read succeeded. */
export function MemorySliceStatus(): JSX.Element {
  return (
    <p className="state state--meta" data-testid="slice-status">
      <strong>状态:PARTIAL / DEPENDENCY_PENDING</strong> —— P6-03b1 的契约固定自 P4 提交{' '}
      <code>0c4f8f0</code>,该阶段<strong>验收未通过</strong>;本页不是已通过的 P6 能力。
    </p>
  )
}

/**
 * Memory is NON_AUTHORITATIVE, and Core says so itself.
 *
 * The `non_authoritative` field is read from the response rather than asserted here: a
 * hardcoded claim would still render if Core stopped sending the field, which is exactly
 * when a reader most needs to know.
 */
export function NonAuthoritative({ payload }: { payload: { non_authoritative?: unknown } }): JSX.Element | null {
  if (payload.non_authoritative !== true) return null
  return (
    <p className="state state--meta" data-testid="non-authoritative">
      <strong>NON_AUTHORITATIVE:</strong>Core 在此响应中声明 Memory 不是权威 Truth。
      Memory 不能修改 Rule 或 State,本页也不把它当作 Truth 展示。
    </p>
  )
}

/** The one place that decides how "this path is not being forwarded" looks. */
export function MemoryDependencyPending({
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
        依赖未满足:{what} 属于 P4(Memory),仪表盘当前不转发这条路径
      </p>
      <p>
        本页读取的 <code>{method}</code> <code>{path}</code> 只存在于固定的 P4 提交。仪表盘的
        live 白名单<strong>不会转发它</strong> —— 代理以 <code>proxy_path_not_allowed</code>
        拒绝,而不是把请求送给一个必然 404 的 Core。有两种情况会这样:P4 尚未合并进本基线
        (<code>850fbec</code>,schema 8),或者 Core 已是 P4、但代理启动时没有打开
        <code> P6_ENABLE_P4_READS</code>。代理的拒绝理由无法区分这两者,所以这里也不替你
        断定是哪一个。
      </p>
      <ul className="plain">
        <li>
          需要的 scope:<code>{scopes.join(' + ')}</code>(由 Core 校验,前端从不代持)
        </li>
        <li>
          契约来源:提交 <code>0c4f8f0e2118711ac9aa2a7006fddc6982962248</code>(固定 SHA,不是
          分支名),派生并冻结在 <code> web/contracts/p4-memory-read.v1.json</code>
        </li>
        <li>
          验收状态:<strong>P4 阶段验收未通过</strong> —— 本页只对模拟 fixture 验证过
        </li>
        <li>
          状态:<strong>PARTIAL / DEPENDENCY_PENDING</strong> —— 本页不是已通过的 P6 能力
        </li>
      </ul>
      <p className="state__meta">
        要在真实 Core 上打开它,需要一个 P4 已合并的 Core,并以显式开关启动代理转发这几条
        路径(见 <code>web/server/core-paths.json</code> 的 <code>p4_pending_paths</code>)。
      </p>
    </div>
  )
}

/**
 * Core answered `not_found` — and P4 deliberately makes that one answer mean two things.
 *
 * `_may_see` refuses a row the actor may not see with the same `not_found` it uses for a row
 * that does not exist, so that the Inspector and the job list cannot be used to enumerate
 * another project's work. The UI therefore must not dress it up as a failure with a retry
 * button — that would imply "try again and you might get it", recreate the enumeration hint
 * P4 removed, and invite a retry that can never succeed. It is not the missing dependency
 * either: Core answered, so the dependency is present.
 */
export function MemoryNotFound({ what }: { what: string }): JSX.Element {
  return (
    <div className="state state--meta" role="status" data-testid="not-found">
      <p className="state__headline">Core 返回 not_found:{what} 不存在,或者当前 actor 无权访问</p>
      <p>
        P4 刻意<b>不区分</b>这两种情况(<code>_may_see</code> 对两种情况都回 <code>not_found</code>),
        这样 Inspector 就不能被用来枚举他人项目的内容。所以这里也不替你猜测是哪一个,更不会建议
        重试 —— 重试不会改变结果。若你认为自己本应有权限,那是 Core 的授权判断,不是本页能修的。
      </p>
    </div>
  )
}

/** A malformed id is refused here, with the right reason, before any request is made. */
export function MemoryInvalidId({
  prefix,
  value,
  field,
}: {
  prefix: string
  value: string
  field: string
}): JSX.Element {
  return (
    <div className="state state--error" role="alert" data-testid="invalid-id">
      <p className="state__headline">非法 id:{field}</p>
      <p>
        <code>{value}</code> 不是 Core 的 id 形状(<code>{prefix}_</code> + 26 位 Crockford
        base32)。请求没有被发出 —— 否则代理会以 <code>proxy_path_not_allowed</code> 拒绝它,
        那会被误报成「P4 未合并」。
      </p>
    </div>
  )
}

/** A malformed id in the URL that this view's read does not use: reported, not blocking. */
export function MemoryUnusedInvalidId({
  prefix,
  value,
  field,
}: {
  prefix: string
  value: string
  field: string
}): JSX.Element {
  return (
    <div className="state state--meta" role="status" data-testid="invalid-id-unused">
      <p>
        忽略无法使用的 <code>{field}</code>:<code>{value}</code> 不是 Core 的 id 形状
        (<code>{prefix}_</code> + 26 位 Crockford base32)。当前 URL 不使用这个字段,所以记录
        仍然显示。
      </p>
    </div>
  )
}

/** A resource error is nullable; only a real proxy refusal counts as pending. */
export function isMemoryPending(error: CoreApiError | null): boolean {
  return error !== null && isDependencyPending(error)
}

/** A compact label/value row, so a Memory record is attributable. */
export function MemoryRow({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

/** Digests and hashes render whole, wrapped, because a truncated hash is not evidence. */
export function Hash({ label, value }: { label: string; value: string | null | undefined }): JSX.Element {
  return (
    <span className="kv__digest">
      <span className="muted">{label}</span> <code className="wrap">{value ?? '—'}</code>
    </span>
  )
}
