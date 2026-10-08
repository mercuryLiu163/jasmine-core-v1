/**
 * Status badges.
 *
 * Every status carries a glyph and its literal Core token as text, so the value
 * is readable without colour perception, in a screenshot and in a terminal
 * browser. Unknown vocabulary is *not* mapped onto a known tone: it renders as
 * `? <VALUE>` with the "unknown" style (P6 negative case "未知 enum 原样醒目展示").
 */

import { cls } from '../lib/classNames'

export type BadgeKind =
  | 'task'
  | 'step'
  | 'evidence'
  | 'reference'
  | 'rule'
  | 'session'
  | 'interpretation'
  | 'disposition'
  | 'review'
  | 'action'

type Tone = 'neutral' | 'progress' | 'good' | 'warn' | 'bad' | 'unknown'

interface ToneSpec {
  glyph: string
  tone: Tone
  label?: string
}

const STEP: Record<string, ToneSpec> = {
  PLANNED: { glyph: '○', tone: 'neutral' },
  IN_PROGRESS: { glyph: '◐', tone: 'progress' },
  EXECUTED: { glyph: '●', tone: 'progress' },
  VERIFIED: { glyph: '✔', tone: 'good' },
  ACCEPTED: { glyph: '★', tone: 'good' },
  FAILED: { glyph: '✖', tone: 'bad' },
  BLOCKED: { glyph: '⛔', tone: 'bad' },
  STALE: { glyph: '⚠', tone: 'warn' },
  SKIPPED: { glyph: '⊘', tone: 'neutral' },
}

const TASK: Record<string, ToneSpec> = {
  ACTIVE: { glyph: '▶', tone: 'progress' },
  BLOCKED: { glyph: '⛔', tone: 'bad' },
  ACCEPTED: { glyph: '★', tone: 'good' },
  CANCELLED: { glyph: '⊘', tone: 'neutral' },
}

const EVIDENCE: Record<string, ToneSpec> = {
  PASS: { glyph: '✔', tone: 'good' },
  FAIL: { glyph: '✖', tone: 'bad' },
  INFO: { glyph: 'ℹ', tone: 'warn', label: 'INFO 不是 PASS' },
}

const REFERENCE: Record<string, ToneSpec> = {
  OK: { glyph: '✔', tone: 'good' },
  NONE: { glyph: '○', tone: 'neutral', label: '无引用可校验' },
  BROKEN_REFERENCE: { glyph: '⚠', tone: 'bad', label: '引用已失效,不能满足 criteria' },
}

const RULE: Record<string, ToneSpec> = {
  ACTIVE: { glyph: '✔', tone: 'good' },
  PROPOSED: { glyph: '◌', tone: 'warn', label: '已提议,未生效' },
  SUPERSEDED: { glyph: '↦', tone: 'neutral' },
  RETIRED: { glyph: '⊘', tone: 'neutral' },
}

const SESSION: Record<string, ToneSpec> = {
  open: { glyph: '▶', tone: 'progress' },
  ended: { glyph: '■', tone: 'neutral' },
}

// P2 vocabularies, copied from the frozen enums: interpretations.STATUSES
// (PROCESSING/EXTRACTED/FAILED/INTERRUPTED), the resolution dispositions the
// policy and review paths emit, `reviews.change` statuses, and the plan actions
// in resolution_policy. Anything else renders as `? VALUE` in the unknown tone.
const INTERPRETATION: Record<string, ToneSpec> = {
  PROCESSING: { glyph: '◐', tone: 'progress' },
  EXTRACTED: { glyph: '✔', tone: 'good' },
  FAILED: { glyph: '✖', tone: 'bad' },
  INTERRUPTED: { glyph: '⚠', tone: 'warn', label: '提供方中断,结果不完整' },
}

const DISPOSITION: Record<string, ToneSpec> = {
  APPLIED: { glyph: '✔', tone: 'good' },
  PENDING_REVIEW: { glyph: '◌', tone: 'warn', label: '等待人工审批,未生效' },
  NO_ACTION: { glyph: '○', tone: 'neutral' },
  REJECTED: { glyph: '✖', tone: 'bad' },
  SOURCE_REPLAYED: { glyph: '↺', tone: 'neutral', label: '来源已被其他决议应用' },
}

const REVIEW: Record<string, ToneSpec> = {
  PENDING: { glyph: '◌', tone: 'warn', label: '待人工审批' },
  APPROVED: { glyph: '✔', tone: 'good' },
  REJECTED: { glyph: '✖', tone: 'bad' },
  SOURCE_REPLAYED: { glyph: '↺', tone: 'neutral' },
}

const ACTION: Record<string, ToneSpec> = {
  CREATE_RULE: { glyph: '§', tone: 'progress' },
  ATTACH_TASK: { glyph: '▤', tone: 'progress' },
  CREATE_TASK: { glyph: '▤', tone: 'progress' },
  NO_ACTION: { glyph: '○', tone: 'neutral' },
}

const VOCABULARY: Record<BadgeKind, Record<string, ToneSpec>> = {
  interpretation: INTERPRETATION,
  disposition: DISPOSITION,
  review: REVIEW,
  action: ACTION,
  step: STEP,
  task: TASK,
  evidence: EVIDENCE,
  reference: REFERENCE,
  rule: RULE,
  session: SESSION,
}

export function StatusBadge({
  kind,
  status,
  title,
}: {
  kind: BadgeKind
  status: string
  title?: string
}): JSX.Element {
  const spec = VOCABULARY[kind][status]
  const glyph = spec?.glyph ?? '?'
  const tone: Tone = spec?.tone ?? 'unknown'
  const hint = title ?? spec?.label
  return (
    <span
      className={cls('badge', `badge--${tone}`)}
      data-status={status}
      title={hint ?? `${kind} status ${status}`}
    >
      <span aria-hidden="true" className="badge__glyph">
        {glyph}
      </span>
      <span className="badge__text">{status}</span>
    </span>
  )
}

/** The set of Step statuses the board renders as columns, in workflow order. */
export const BOARD_COLUMNS: readonly string[] = [
  'PLANNED',
  'IN_PROGRESS',
  'EXECUTED',
  'VERIFIED',
  'ACCEPTED',
  'FAILED',
  'BLOCKED',
  'STALE',
  'SKIPPED',
]

/** Columns a Step may occupy that this build does not know about. */
export function unknownStepStatuses(statuses: readonly string[]): string[] {
  return statuses.filter((status) => !(status in STEP))
}
