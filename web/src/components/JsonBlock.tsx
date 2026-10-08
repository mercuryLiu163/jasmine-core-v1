/**
 * Collapsible JSON viewer for Event payloads, Evidence results and Error
 * details. Same masking policy as `RawText`; long structures start collapsed so
 * a source-trace button is never pushed off screen by a 40 KiB tool response.
 */

import { useState } from 'react'

import { cls } from '../lib/classNames'
import { hasCredentialShape, stringifyJson } from '../lib/text'
import { useMaskedText } from '../lib/useMaskedText'

export function JsonBlock({
  value,
  label,
  startsOpen = false,
}: {
  value: unknown
  label: string
  startsOpen?: boolean
}): JSX.Element {
  const [open, setOpen] = useState(startsOpen)
  const text = stringifyJson(value)
  const masked = useMaskedText(text)
  const [copied, setCopied] = useState(false)

  return (
    <div className="jsonblock">
      <div className="jsonblock__bar">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className="jsonblock__toggle"
        >
          {open ? '▾' : '▸'} {label}
        </button>
        <span className="jsonblock__meta">{text.length} 字符</span>
        {masked.hasSecrets ? <span className="rawtext__tag rawtext__tag--masked">含脱敏字段</span> : null}
        {open && hasCredentialShape(text) ? (
          <button type="button" onClick={masked.revealed ? masked.hide : masked.reveal}>
            {masked.revealed ? '恢复脱敏' : '显示原文'}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard
              .writeText(masked.masked.text)
              .then(() => setCopied(true))
              .catch(() => setCopied(false))
          }}
        >
          {copied ? '已复制(脱敏)' : '复制(脱敏)'}
        </button>
      </div>
      {open ? (
        <pre className={cls('jsonblock__body', masked.hasSecrets && !masked.revealed && 'is-masked')}>
          {masked.shown}
        </pre>
      ) : null}
    </div>
  )
}
