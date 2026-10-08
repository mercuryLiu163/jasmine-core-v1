/**
 * Presentation helpers for untrusted Core content.
 *
 * Everything the Core returns is data. These helpers are the only place the UI
 * decides how to *show* it, and they are intentionally conservative:
 *
 * - `maskCredentials` hides high-confidence credential shapes by default. The
 *   operator can expand the exact original text of a field, but expansion is a
 *   view action, not an export, and it resets when the field changes.
 * - `safeHref` accepts only `#/…`, `http:`, `https:` and `mailto:`. `javascript:`,
 *   `data:`, `vbscript:` and protocol-relative URLs are refused, so a hostile
 *   Event body cannot become a live link.
 * - Nothing here produces HTML. React renders text nodes; there is no
 *   `dangerouslySetInnerHTML` in this bundle.
 */

const CREDENTIAL_PATTERNS: ReadonlyArray<{ label: string; pattern: RegExp }> = [
  { label: 'bearer', pattern: /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi },
  { label: 'jwt', pattern: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g },
  { label: 'openai-key', pattern: /\bsk-[A-Za-z0-9_-]{16,}/g },
  { label: 'github-token', pattern: /\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}/g },
  { label: 'slack-token', pattern: /\bxox[abpsr]-[A-Za-z0-9-]{10,}/g },
  { label: 'aws-key-id', pattern: /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g },
  {
    label: 'assigned-secret',
    pattern:
      /\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*["']?[^\s"',}]{8,}/gi,
  },
]

export interface MaskResult {
  text: string
  masked: ReadonlyArray<{ label: string; count: number }>
}

/** Replace credential-looking substrings with a labelled placeholder. */
export function maskCredentials(input: string): MaskResult {
  let text = input
  const masked: Array<{ label: string; count: number }> = []
  for (const { label, pattern } of CREDENTIAL_PATTERNS) {
    let count = 0
    text = text.replace(pattern, () => {
      count += 1
      return `«已脱敏:${label}»`
    })
    if (count > 0) masked.push({ label, count })
  }
  return { text, masked }
}

/** True when the text carries something the masker recognised. */
export function hasCredentialShape(input: string): boolean {
  return CREDENTIAL_PATTERNS.some(({ pattern }) => {
    pattern.lastIndex = 0
    return pattern.test(input)
  })
}

/**
 * Only safe link targets survive. Core IDs are rendered as in-app `#/…` links
 * by the source-trace helpers, never as absolute URLs taken from data.
 */
export function safeHref(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') return null
  const value = raw.trim()
  if (value.length === 0) return null
  if (value.startsWith('#/')) return value
  if (value.startsWith('//')) return null
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(value)
  if (match === null) return null
  const scheme = (match[1] ?? '').toLowerCase()
  if (scheme === 'http' || scheme === 'https' || scheme === 'mailto') return value
  return null
}

export function isHttpUrl(raw: string | null | undefined): boolean {
  const href = safeHref(raw)
  return href !== null && /^https?:/i.test(href)
}

/** Trim a value for a compact table cell without hiding that it was trimmed. */
export function clip(value: string, max = 160): string {
  return value.length <= max ? value : `${value.slice(0, max)}…(+${value.length - max})`
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toISOString().replace('T', ' ').replace('Z', 'Z')
}

/** Raw Event text, when the payload carries it as a string. */
export function payloadText(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null
  const candidate = (payload as Record<string, unknown>)['text']
  return typeof candidate === 'string' ? candidate : null
}

/** Event types whose content is machine output rather than a human utterance. */
export function isMachineEvent(eventType: string): boolean {
  return (
    eventType.startsWith('tool.') ||
    eventType.startsWith('evidence.') ||
    eventType.startsWith('workspace.') ||
    eventType.startsWith('step.') ||
    eventType.startsWith('task.') ||
    eventType.startsWith('rule.') ||
    eventType.startsWith('project.')
  )
}

export function stringifyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

/** A copyable, already-masked summary. Never the full raw transcript. */
export function maskedSummary(value: string): string {
  return maskCredentials(value).text
}
