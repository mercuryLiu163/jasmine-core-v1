/**
 * Explicit view states.
 *
 * The four outcomes a window on Core can have are kept visually and textually
 * distinct on purpose, because the P6 taskbook forbids collapsing them:
 * loading, empty (Core said "no rows"), failed (Core refused or is unreachable)
 * and not-implemented (the API does not exist yet). "Unavailable" never renders
 * as an empty list and never as a zero count.
 */

import type { ReactNode } from 'react'

import { CoreApiError, describeError } from '../api/errors'
import { cls } from '../lib/classNames'
import { JsonBlock } from './JsonBlock'

export function Panel({
  title,
  children,
  actions,
  id,
}: {
  title: string
  children: ReactNode
  actions?: ReactNode
  id?: string
}): JSX.Element {
  return (
    <section className="panel" id={id} aria-label={title}>
      <header className="panel__head">
        <h2>{title}</h2>
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  )
}

export function Loading({ what }: { what: string }): JSX.Element {
  return (
    <p className="state state--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> 正在读取{what}…
    </p>
  )
}

export function Empty({ what, hint }: { what: string; hint?: string }): JSX.Element {
  return (
    <p className="state state--empty">
      空:{what}在这段读取范围内没有记录。
      {hint ? <span className="state__hint"> {hint}</span> : null}
    </p>
  )
}

export function ErrorPanel({
  error,
  what,
  onRetry,
}: {
  error: CoreApiError
  what: string
  onRetry?: () => void
}): JSX.Element {
  return (
    <div className="state state--error" role="alert">
      <p className="state__headline">
        读取{what}失败 — <code>{error.code}</code>
        {error.status !== null ? ` (HTTP ${error.status})` : ''}
      </p>
      <p>{describeError(error)}</p>
      <p className="state__meta">
        Core message: <q>{error.message}</q>
        {error.requestId ? (
          <>
            {' '}
            · request_id <code>{error.requestId}</code>
          </>
        ) : null}
      </p>
      {error.details ? (
        <details>
          <summary>错误 details</summary>
          <JsonBlock value={error.details} label="error.details" />
        </details>
      ) : null}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          重新读取
        </button>
      ) : null}
    </div>
  )
}

/**
 * A dependency that is not frozen yet. `owner` and `pending` come from the P6
 * taskbook ownership table; this panel must never be replaced by fabricated
 * rows or by a call to a guessed endpoint.
 */
export function Unavailable({
  feature,
  owner,
  pending,
  note,
  tone = 'pending',
}: {
  feature: string
  owner: string
  pending: string
  note?: string
  tone?: 'pending' | 'forbidden'
}): JSX.Element {
  return (
    <div className={cls('state', `state--${tone}`)} data-testid="unavailable">
      <p className="state__headline">
        <span aria-hidden="true">{tone === 'forbidden' ? '⛔' : '◌'}</span> {feature} — 未接入
      </p>
      <dl className="kv kv--compact">
        <div>
          <dt>提供方</dt>
          <dd>{owner}</dd>
        </div>
        <div>
          <dt>等待冻结</dt>
          <dd>{pending}</dd>
        </div>
      </dl>
      {note ? <p>{note}</p> : null}
      <p className="state__meta">
        本页不会用模拟数据、猜测字段或本地推断填充此区域;依赖冻结并通过独立验证后再接真实接口。
      </p>
    </div>
  )
}

export function StaleBanner({ loadedAt, scope }: { loadedAt: string; scope: string }): JSX.Element {
  return (
    <p className="state state--meta">
      本段显示的是 <time dateTime={loadedAt}>{loadedAt}</time> 从 Core 读回的
      <strong> {scope} </strong>内容;它不是事务快照,也不是实时推送。
    </p>
  )
}
