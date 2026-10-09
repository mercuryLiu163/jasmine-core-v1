/**
 * Application shell.
 *
 * The dashboard is a window on Core that also issues the frozen audited writes.
 * The header states the posture, the navigation exposes only routes that exist
 * today (plus clearly-marked placeholders for domains whose contracts are not
 * frozen), and "退出" clears the in-memory token so no later request carries an
 * Authorization header.
 */

import { hrefFor, type Route } from './app/router'
import { useHashRoute } from './app/useHashRoute'
import { useSession } from './app/session'
import { SignIn } from './components/SignIn'
import { Panel } from './components/States'
import { CheckpointsView, ContextPackView, ResumeView } from './views/ContinuityViews'
import {
  MemoryHealthView,
  MemoryItemsView,
  MemoryJobsView,
  MemoryTracesView,
} from './views/MemoryViews'
import { MemoryReflectView } from './views/MemoryWrites'
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
import {
  ValidationArtifactView,
  ValidationManifestView,
  ValidationRunsView,
  ValidationRunView,
} from './views/ValidationViews'

const NAV = [
  { label: 'Overview', href: hrefFor({ name: 'overview' }) },
  { label: 'Conversation', href: hrefFor({ name: 'conversation', projectId: null, taskId: null, sessionId: null }) },
  { label: 'Interpretations', href: hrefFor({ name: 'interpretations', projectId: null, taskId: null, eventId: null, status: null }) },
  { label: '待审批 Review', href: hrefFor({ name: 'reviews', projectId: null, taskId: null }) },
  { label: 'Rules', href: hrefFor({ name: 'rules', projectId: null, taskId: null, ruleId: null }) },
]

export function App(): JSX.Element {
  const route = useHashRoute()
  const { signedIn, signOut, pendingConfirmations } = useSession()

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主内容
      </a>
      <header className="app__head">
        <div className="app__brand">
          <strong>Jasmine Core</strong> <span>Dashboard P6-02</span>
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
            {pendingConfirmations.map((pending) => <a key={pending.id} href={`#/task/${pending.taskId}/confirmation`}>恢复未知确认 {pending.id}</a>)}
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
            受控写入:本页只提供已冻结的审计写(Interpretation 的 Correct/Reject/Re-run、
            Review 的 Approve/Reject、Rule 的 Propose/Approve/Supersede/Retire,以及 P6-02
            TaskStep 的 Task 创建、Step 创建/转换、Task 转换、Task/Step criteria 与 Task
            验收)。人工确认使用专用 prepare/submit（P6_ENABLE_WEB_CONFIRMATIONS 显式启用），由 Core 扫描并准备材料；仅记录 INFO，状态另行提交。不代表 P6
            已验收。浏览器还会发 GET,而 Core 的解释/Review 读取会执行惰性恢复(可能写入
            INTERRUPTED 或完成待处理 rerun),详见 P6-02-COMPLIANCE.md。<a href={hrefFor({ name: 'pending', key: 'validation' })}>Validation</a>、
            <a href={hrefFor({ name: 'pending', key: 'context' })}>Context</a>、
            <a href={hrefFor({ name: 'pending', key: 'memory' })}>Memory</a> 等按依赖分别标注;Memory 的只读切片已实现,但 P4 未合并;Validation 的只读切片(P7-01)同样已实现,但 P7-01 未合并且其验收标准 1(真实 FAIL run)仍为 BLOCKED —— 因此两者都仍列在此处而不在导航里,并以 <code>P6_ENABLE_P4_READS</code>/<code>P6_ENABLE_P7_READS</code> 显式开关才转发。
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
    // P6-03a: P3 continuity reads. These render DEPENDENCY PENDING on a baseline
    // dashboard, because this Core has no such route; the views say so themselves.
    case 'checkpoints':
      return (
        <CheckpointsView
          taskId={route.taskId}
          checkpointId={route.checkpointId}
          sessionId={route.sessionId}
        />
      )
    case 'context':
      return <ContextPackView contextPackId={route.contextPackId} />
    case 'resume':
      return <ResumeView resumeId={route.resumeId} />
    // P6-03b1 reads, plus P6-03b2's reflect route. The read views issue only GETs; the write
    // controls live on the item and job details, and reflect has its own route because it is
    // an explicit user request rather than an action on a row. Everything Memory is a deep
    // link: nothing here is in the navigation.
    case 'memoryHealth':
      return <MemoryHealthView />
    case 'memoryJobs':
      return <MemoryJobsView jobId={route.jobId} />
    case 'memoryJob':
      return <MemoryJobsView jobId={route.jobId} />
    case 'memoryItems':
      return <MemoryItemsView memoryId={route.memoryId} />
    case 'memoryItem':
      return <MemoryItemsView memoryId={route.memoryId} />
    case 'memoryTraces':
      return <MemoryTracesView traceId={route.traceId} />
    case 'memoryTrace':
      return <MemoryTracesView traceId={route.traceId} />
    case 'memoryReflect':
      return <MemoryReflectView />
    // P6-03c: P7-01 Validation reads, deep links only (nothing here is in the navigation,
    // exactly like the P3/P4 reads). The views say DEPENDENCY PENDING until a proxy with
    // P6_ENABLE_P7_READS is running.
    case 'validationRuns':
      return (
        <ValidationRunsView
          verdict={route.verdict}
          stage={route.stage}
          mode={route.mode}
          attribution={route.attribution}
          project={route.project}
        />
      )
    case 'validationRun':
      return <ValidationRunView runId={route.runId} />
    case 'validationManifest':
      return <ValidationManifestView runId={route.runId} />
    case 'validationArtifact':
      return <ValidationArtifactView runId={route.runId} artifactId={route.artifactId} />
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
