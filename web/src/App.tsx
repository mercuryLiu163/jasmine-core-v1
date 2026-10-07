/**
 * Application shell.
 *
 * P6-01 is a read-only window on Core. The header states that, the navigation
 * exposes only routes that exist today (plus clearly-marked placeholders for the
 * domains whose contracts are not frozen), and "退出" clears the in-memory token
 * so no later request carries an Authorization header.
 */

import { hrefFor, type Route } from './app/router'
import { useHashRoute } from './app/useHashRoute'
import { useSession } from './app/session'
import { SignIn } from './components/SignIn'
import { Panel } from './components/States'
import { ConversationView } from './views/ConversationView'
import { EventView } from './views/EventView'
import { EvidenceView } from './views/EvidenceView'
import { InterpretationsView } from './views/InterpretationsView'
import { InterpretationView } from './views/InterpretationView'
import { OverviewView } from './views/OverviewView'
import { PendingView, pendingKeys } from './views/PendingView'
import { ProjectView } from './views/ProjectView'
import { ResolutionView, ReviewView } from './views/ReviewView'
import { ReviewsView } from './views/ReviewsView'
import { RulesView } from './views/RulesView'
import { TaskView } from './views/TaskView'

const NAV = [
  { label: 'Overview', href: hrefFor({ name: 'overview' }) },
  { label: 'Conversation', href: hrefFor({ name: 'conversation', projectId: null, taskId: null, sessionId: null }) },
  { label: 'Interpretations', href: hrefFor({ name: 'interpretations', projectId: null, taskId: null, eventId: null, status: null }) },
  { label: '待审批 Review', href: hrefFor({ name: 'reviews', projectId: null, taskId: null }) },
  { label: 'Rules', href: hrefFor({ name: 'rules', projectId: null, taskId: null, ruleId: null }) },
]

export function App(): JSX.Element {
  const route = useHashRoute()
  const { signedIn, signOut } = useSession()

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主内容
      </a>
      <header className="app__head">
        <div className="app__brand">
          <strong>Jasmine Core</strong> <span>Dashboard P6-01(只读)</span>
        </div>
        {signedIn ? (
          <nav className="app__nav" aria-label="主导航">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                aria-current={isCurrent(route, item.href) ? 'page' : undefined}
              >
                {item.label}
              </a>
            ))}
            <details className="navmenu">
              <summary>领域(未接入)</summary>
              <ul>
                {pendingKeys().map((key) => (
                  <li key={key}>
                    <a href={hrefFor({ name: 'pending', key })}>{key}</a>
                  </li>
                ))}
              </ul>
            </details>
          </nav>
        ) : null}
        {signedIn ? (
          <button type="button" className="app__signout" onClick={signOut}>
            退出(清除内存中的 token)
          </button>
        ) : null}
      </header>

      {signedIn ? (
        <>
          <p className="readonly-banner" role="note">
            只读视图:本页不提供 Rule/Task/State/Evidence 的写按钮,不调用 fingerprint 扫描,
            不代表 P6 已验收。浏览器只发 GET,但 Core 的解释/Review 读取会执行惰性恢复
            (可能写入 INTERRUPTED 或完成待处理 rerun),详见 P6-02-COMPLIANCE.md。<a href={hrefFor({ name: 'pending', key: 'validation' })}>Validation</a>、
            <a href={hrefFor({ name: 'pending', key: 'context' })}>Context</a>、
            <a href={hrefFor({ name: 'pending', key: 'memory' })}>Memory</a> 等按依赖分别标未接入。
          </p>
          <main id="main">{renderRoute(route)}</main>
        </>
      ) : (
        <SignIn />
      )}
    </div>
  )
}

function isCurrent(route: Route, href: string): boolean {
  if (href.endsWith('/overview')) return route.name === 'overview'
  if (href.includes('/conversation')) return route.name === 'conversation'
  if (href.includes('/interpretations')) return route.name === 'interpretations'
  if (href.includes('/reviews')) return route.name === 'reviews'
  if (href.includes('/rules')) return route.name === 'rules'
  return false
}

function renderRoute(route: Route): JSX.Element {
  switch (route.name) {
    case 'overview':
      return <OverviewView />
    case 'project':
      return <ProjectView projectId={route.projectId} />
    case 'task':
      return <TaskView taskId={route.taskId} tab={route.tab} />
    case 'conversation':
      return (
        <ConversationView
          projectId={route.projectId}
          taskId={route.taskId}
          sessionId={route.sessionId}
        />
      )
    case 'rules':
      return <RulesView projectId={route.projectId} taskId={route.taskId} ruleId={route.ruleId} />
    case 'evidence':
      return route.evidenceId === null ? (
        <Panel title="Evidence">
          <p>
            这个路由需要一个 <code>evidence_id</code>。请从 Task 的 Evidence 列表进入。
          </p>
        </Panel>
      ) : (
        <EvidenceView evidenceId={route.evidenceId} taskId={route.taskId} />
      )
    case 'event':
      return <EventView eventId={route.eventId} />
    case 'interpretations':
      return (
        <InterpretationsView
          projectId={route.projectId}
          taskId={route.taskId}
          eventId={route.eventId}
          status={route.status}
        />
      )
    case 'interpretation':
      return <InterpretationView interpretationId={route.interpretationId} />
    case 'reviews':
      return <ReviewsView projectId={route.projectId} taskId={route.taskId} />
    case 'review':
      return <ReviewView reviewId={route.reviewId} />
    case 'resolution':
      return <ResolutionView resolutionId={route.resolutionId} />
    case 'pending':
      return <PendingView pendingKey={route.key} />
    case 'unknown':
      return (
        <Panel title="未知路由">
          <p>
            无法解析 <code>#{route.raw}</code>。本页不会把它悄悄当作 Overview,因为“打开错链接”和
            “一切正常”必须看起来不同。
          </p>
          <p>
            <a href={hrefFor({ name: 'overview' })}>回到 Overview</a>
          </p>
        </Panel>
      )
  }
}
