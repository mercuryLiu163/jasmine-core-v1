/**
 * One Raw Event, rendered as data.
 *
 * `payload.text` is shown through `RawText` (text node, masked by default) and
 * machine-generated event types are labelled so a tool result can never be
 * mistaken for a human utterance (P6-A05). State events additionally surface the
 * `evidence_ids` and `rule_versions` Core recorded on the transition, which is
 * what makes "history → Evidence" a real trace instead of a guess.
 */

import type { RawEvent } from '../api/types'
import { sourceHref } from '../app/router'
import { formatTimestamp, isMachineEvent, payloadText } from '../lib/text'
import { JsonBlock } from './JsonBlock'
import { RawText } from './RawText'
import { SourceLink } from './Trace'

interface StateResult {
  from?: unknown
  to?: unknown
  reason?: unknown
  evidence_ids?: unknown
  rule_versions?: unknown
  task_revision?: unknown
}

function stateResult(event: RawEvent): StateResult {
  const payload = event.payload as Record<string, unknown>
  const result: StateResult = {
    from: payload['from'],
    to: payload['to'],
    reason: payload['reason'],
  }
  const nested = payload['result']
  if (typeof nested === 'object' && nested !== null) {
    const record = nested as Record<string, unknown>
    result.evidence_ids = record['evidence_ids']
    result.rule_versions = record['rule_versions']
    result.task_revision = record['task_revision']
  }
  return result
}

function evidenceIdsOf(event: RawEvent): string[] {
  const payload = event.payload as Record<string, unknown>
  const direct = payload['evidence_ids']
  if (Array.isArray(direct)) return direct.filter((value): value is string => typeof value === 'string')
  const result = payload['result']
  if (typeof result === 'object' && result !== null) {
    const nested = (result as Record<string, unknown>)['evidence_ids']
    if (Array.isArray(nested)) return nested.filter((value): value is string => typeof value === 'string')
  }
  return []
}

export function EventRow({
  event,
  taskId = null,
  defaultOpen = false,
}: {
  event: RawEvent
  taskId?: string | null
  defaultOpen?: boolean
}): JSX.Element {
  const text = payloadText(event.payload)
  const machine = isMachineEvent(event.event_type)
  const result = stateResult(event)
  const evidenceIds = evidenceIdsOf(event)

  return (
    <li className="event" data-event-id={event.event_id} data-seq={event.seq}>
      <div className="event__head">
        <span className="event__seq">#{event.seq}</span>
        <span className="event__type">{event.event_type}</span>
        <span className="muted">{formatTimestamp(event.occurred_at)}</span>
        <span className="event__actor">
          {event.actor_kind} <code>{event.actor_id}</code>
        </span>
        <span className="muted">src {event.source_system}</span>
        <SourceLink
          kind="event"
          id={event.event_id}
          label="event"
          taskId={taskId ?? event.task_id}
        />
      </div>
      <div className="event__links">
        {event.project_id ? (
          <SourceLink kind="project" id={event.project_id} label="project" />
        ) : null}
        {event.task_id ? <SourceLink kind="task" id={event.task_id} label="task" /> : null}
        {event.session_id ? (
          <a href={sourceHref.conversation(event.project_id, event.task_id, event.session_id)}>
            <code>{event.session_id}</code>
          </a>
        ) : null}
      </div>
      {typeof result.from === 'string' || typeof result.to === 'string' ? (
        <p className="event__transition">
          {String(result.from ?? '—')} → <strong>{String(result.to ?? '—')}</strong>
          {typeof result.reason === 'string' && result.reason ? <> · reason {result.reason}</> : null}
          {typeof result.task_revision === 'number' ? <> · task_revision {result.task_revision}</> : null}
        </p>
      ) : null}
      {evidenceIds.length > 0 ? (
        <p className="event__evidence">
          Core 记录的判定 Evidence:{' '}
          {evidenceIds.map((evidenceId) => (
            <SourceLink
              key={evidenceId}
              kind="evidence"
              id={evidenceId}
              label="evidence"
              taskId={taskId ?? event.task_id}
            />
          ))}
        </p>
      ) : null}
      {text !== null ? (
        <RawText value={text} label="payload.text" machine={machine} />
      ) : (
        <p className="muted">payload 没有 text 字段(非对话事件)。</p>
      )}
      <JsonBlock value={event.payload} label="payload" startsOpen={defaultOpen} />
    </li>
  )
}
