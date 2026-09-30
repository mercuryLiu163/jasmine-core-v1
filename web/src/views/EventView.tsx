/**
 * Single Raw Event (deep link).
 *
 * This is the page a "source Event" link lands on. It re-reads the Event from
 * Core by ID, so a refreshed deep link shows the same stored bytes rather than
 * something reconstructed from list state. It also offers the surrounding
 * conversation as a *scoped* link, labelled as a separate read.
 */

import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { EventRow } from '../components/EventRow'
import { ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'

export function EventView({ eventId }: { eventId: string }): JSX.Element {
  const { endpoints } = useSession()
  const event = useResource((signal) => endpoints.getEvent(eventId, signal), [eventId])

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <a href={sourceHref.conversation(null)}>Conversation</a>
        <span aria-hidden="true">/</span>
        <code>{eventId}</code>
      </nav>

      <Panel
        title="Raw Event(逐字保留)"
        actions={
          <button type="button" onClick={event.reload} disabled={event.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {event.status === 'loading' && event.data === null ? <Loading what="Event" /> : null}
        {event.error ? <ErrorPanel error={event.error} what="Event" onRetry={event.reload} /> : null}
        {event.data ? (
          <>
            <ol className="events">
              <EventRow event={event.data.event} defaultOpen />
            </ol>
            <p className="state state--meta">
              这次读取的路径是 <code>GET /v1/events/{eventId}</code>;上方的跳转链接指向按
              session/task/project 的单独查询,不是同一个快照。
            </p>
            {event.loadedAt ? (
              <StaleBanner loadedAt={event.loadedAt} scope={`Event ${eventId}`} />
            ) : null}
          </>
        ) : null}
      </Panel>
    </div>
  )
}
