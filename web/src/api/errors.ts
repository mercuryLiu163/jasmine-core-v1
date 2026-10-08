/**
 * Error model for the dashboard.
 *
 * Core answers refusals with `{"error": {code, message, request_id, details}}`
 * and a stable `code` (ADR 0003 §1.4). The UI branches on `code`/`status` and
 * shows `message` as data — it never parses prose to decide what happened, and
 * it never turns an unknown failure into an empty list or a zero count.
 */

export type ErrorKind =
  | 'unauthenticated'
  | 'forbidden'
  | 'missing'
  | 'conflict'
  | 'invalid'
  | 'degraded'
  | 'unreachable'
  | 'unknown'

export interface CoreErrorEnvelope {
  code: string
  message: string
  request_id?: string
  details?: unknown
}

export class CoreApiError extends Error {
  readonly kind: ErrorKind
  readonly status: number | null
  readonly code: string
  readonly requestId: string | null
  readonly details: unknown

  constructor(init: {
    kind: ErrorKind
    status: number | null
    code: string
    message: string
    requestId?: string | null
    details?: unknown
  }) {
    super(init.message)
    this.name = 'CoreApiError'
    this.kind = init.kind
    this.status = init.status
    this.code = init.code
    this.requestId = init.requestId ?? null
    this.details = init.details ?? null
  }
}

export function kindForStatus(status: number): ErrorKind {
  if (status === 401) return 'unauthenticated'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'missing'
  if (status === 409) return 'conflict'
  if (status === 400 || status === 413 || status === 415 || status === 422) return 'invalid'
  if (status === 503) return 'degraded'
  // A gateway status means the proxy answered but Core did not: the same
  // condition as a refused connection, and it must not be shown as
  // "unclassified" when the operator's Core is simply stopped.
  if (status === 502 || status === 504) return 'unreachable'
  return 'unknown'
}

/** Codes the dashboard's own proxy emits when Core is not answering. */
const PROXY_UNREACHABLE_CODES = new Set(['proxy_core_unreachable', 'proxy_bad_target'])

/** The proxy refuses a path it does not forward (`METHOD path` not in its allowlist). */
export const PROXY_PATH_REFUSED = 'proxy_path_not_allowed'

/**
 * True when a read was refused by our own proxy rather than by Core.
 *
 * For the P6-03a continuity reads this is the *expected* state on a baseline dashboard:
 * the four routes exist in P3 and not in this baseline's Core, so they are kept out of
 * the live allowlist and the proxy answers `proxy_path_not_allowed`. The views use this
 * to say "dependency pending (P3 not merged)" — which is a different statement from
 * Core's own `not_found`, and must not be shown as one.
 */
export function isDependencyPending(error: CoreApiError): boolean {
  return error.code === PROXY_PATH_REFUSED
}

export function kindForEnvelope(status: number, code: string): ErrorKind {
  if (PROXY_UNREACHABLE_CODES.has(code)) return 'unreachable'
  return kindForStatus(status)
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name?: unknown }).name === 'AbortError'
  )
}

/** A short, human label for a failure, used by every view. */
export function describeError(error: CoreApiError): string {
  switch (error.kind) {
    case 'unauthenticated':
      return '未认证:token 缺失、未知或已撤销。Core 返回 401。'
    case 'forbidden':
      return `缺少 scope 或角色不允许:${error.code}。本视图不会改用更高权限的凭据。`
    case 'missing':
      return `目标不存在:${error.code}。`
    case 'conflict':
      return `revision/幂等冲突:${error.code}。需要重新读取后再决定。`
    case 'invalid':
      return `请求被拒绝:${error.code}。`
    case 'degraded':
      return `Core 暂时不可用:${error.code}。这不是空集,也不是 0。`
    case 'unreachable':
      return '无法连接 Core。请确认本地 Core 与代理仍在运行。'
    default:
      return `未分类失败:${error.code}(HTTP ${error.status ?? '—'})。`
  }
}
