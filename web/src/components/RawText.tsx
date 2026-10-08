/**
 * Text renderer for untrusted content.
 *
 * Renders as a React text node — there is no HTML parsing and no
 * `dangerouslySetInnerHTML` anywhere in this bundle. Credential shapes are
 * masked by default; the operator can reveal the exact original of *this* field,
 * and a copy action always copies the masked form.
 */

import { useState } from 'react'

import { cls } from '../lib/classNames'
import { clip, hasCredentialShape } from '../lib/text'
import { useMaskedText } from '../lib/useMaskedText'

export function RawText({
  value,
  label,
  maxCollapsed = 480,
  emptyText = '(空原文)',
  machine = false,
}: {
  value: string
  label: string
  maxCollapsed?: number
  emptyText?: string
  machine?: boolean
}): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const masked = useMaskedText(value)
  const clipped = expanded ? masked.shown : clip(masked.shown, maxCollapsed)
  const [copied, setCopied] = useState<'idle' | 'done' | 'blocked'>('idle')

  const copy = async (): Promise<void> => {
    // The clipboard receives the masked summary only; a revealed view is not an
    // export channel.
    try {
      await navigator.clipboard.writeText(masked.masked.text)
      setCopied('done')
    } catch {
      setCopied('blocked')
    }
  }

  const long = masked.shown.length > maxCollapsed

  return (
    <div className={cls('rawtext', machine && 'rawtext--machine')}>
      <div className="rawtext__bar">
        <span className="rawtext__label">{label}</span>
        {machine ? <span className="rawtext__tag">机器事件(非用户发言)</span> : null}
        {masked.hasSecrets ? (
          <span className="rawtext__tag rawtext__tag--masked" data-testid="masked-note">
            已按默认策略脱敏 {masked.masked.masked.map((item) => item.label).join('、')}
          </span>
        ) : null}
        <span className="rawtext__actions">
          {long ? (
            <button type="button" onClick={() => setExpanded((value) => !value)}>
              {expanded ? '折叠' : '展开全文'}
            </button>
          ) : null}
          {hasCredentialShape(value) ? (
            <button type="button" onClick={masked.revealed ? masked.hide : masked.reveal}>
              {masked.revealed ? '恢复脱敏' : '显示原文'}
            </button>
          ) : null}
          <button type="button" onClick={() => void copy()}>
            {copied === 'done' ? '已复制脱敏摘要' : copied === 'blocked' ? '复制被拒绝' : '复制脱敏摘要'}
          </button>
        </span>
      </div>
      {value.length === 0 ? (
        <p className="rawtext__empty">{emptyText}</p>
      ) : (
        <pre className="rawtext__body">{clipped}</pre>
      )}
    </div>
  )
}
