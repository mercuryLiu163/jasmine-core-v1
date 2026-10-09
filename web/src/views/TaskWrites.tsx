/**
 * P6-02b — the Task/Step write panels, on the Task view's `write` tab.
 *
 * These are the seven routes the frozen spec (`web/P6-02B-TASK-STEP-WRITE-SPEC.md`) describes,
 * all served by this baseline's Core. The rules the spec freezes are encoded once in
 * `useWriteAction`/`WritePanel` and reused here, so no panel can quietly invent a different one:
 *
 * * the **basis is read, never guessed** — each panel starts from the Task/Step row Core
 *   returned and sends that integer `expected_revision`; a Step transition sends the Step's
 *   revision while a Task action sends the Task's (§4);
 * * **one `event_id` per intent**, reused on retry, so a repeat of the identical body is a
 *   replay (`replayed: true`) rather than a second write (§5);
 * * **the write is read back** — after a success the Task and the Steps are re-read and Core's
 *   re-read revision is displayed; a Step write re-reads **both** the Step and the Task because
 *   it bumps both counters (§4, §8);
 * * **a timeout is unknown**, never success and never failure, and the panel names the request
 *   id when one is available (§5);
 * * **the verdict is Core's** — the offered status list is Core's status *vocabulary*, not an
 *   edge map the UI decided, and the refusal (`409 invalid_state_transition` with Core's
 *   `from_status`/`to_status`) is rendered as its own condition. There is no browser route in
 *   this baseline that returns `STEP_EDGES`/`TASK_EDGES`, so the safest honest reading of §12
 *   is to offer every status Core defines and let Core refuse — see the report's spec
 *   ambiguities;
 * * **Evidence is Core's** — a `422 missing_evidence` is recorded as BLOCKED with Core's code,
 *   message and details, not as a failure and not as a success; the panel never promotes
 *   `TaskEvidence.tsx`'s client-side reference matrix into a sufficiency claim. That branch is
 *   rendered by `WritePanel` so every write panel inherits it.
 */

import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { Step, Task } from '../api/types'
import {
  acceptBody,
  criteriaBody,
  stepCreateBody,
  stepCriteriaBody,
  stepTransitionBody,
  taskCreateBody,
  transitionBody,
} from '../api/taskWrites'
import { useSession } from '../app/session'
import { sourceHref } from '../app/router'
import { useResource } from '../app/useResource'
import { useWriteAction, type WriteState } from '../app/useWriteAction'
import { WritePanel } from '../components/WritePanel'
import { JsonBlock } from '../components/JsonBlock'
import { KeyValue, SourceLink } from '../components/Trace'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { StatusBadge } from '../components/StatusBadge'
import { formatTimestamp } from '../lib/text'

/**
 * Core's frozen status vocabularies (`state.py:22-38`).
 *
 * This is deliberately the **whole vocabulary**, not the set of legal edges from the current
 * status: the UI does not decide that an edge is legal, and a list restricted to the "expected"
 * neighbours would be exactly the client-side state machine §6 forbids. Core answers an illegal
 * target with `409 invalid_state_transition` and its own `from_status`/`to_status`, which the
 * panel renders.
 */
export const STEP_STATUSES = [
  'PLANNED',
  'IN_PROGRESS',
  'EXECUTED',
  'VERIFIED',
  'ACCEPTED',
  'FAILED',
  'BLOCKED',
  'STALE',
  'SKIPPED',
] as const

export const TASK_STATUSES = ['ACTIVE', 'BLOCKED', 'ACCEPTED', 'CANCELLED'] as const

/** Parse the criteria textarea; refuses empty and non-JSON locally, because neither is sendable. */
export function parseCriteriaField(text: string): { value?: unknown; error?: string } {
  if (text.trim() === '') {
    return {
      error:
        'acceptance_criteria 不能为空。要清空 requirements 请显式填 {"requirements": []},本面板不会替你猜。',
    }
  }
  try {
    const parsed: unknown = JSON.parse(text)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      // `null`/a scalar/an array would be dropped by the body builder and Core would then see an
      // ABSENT field, silently defaulting `requirements` to []. Requiring an object here is what
      // makes the "state it explicitly" rule true.
      return {
        error:
          'acceptance_criteria 必须是 JSON 对象(例如 {"requirements": []});null、数字、字符串或数组会被当成未填写,Core 会静默使用默认值。',
      }
    }
    return { value: parsed }
  } catch (error) {
    return {
      error: `acceptance_criteria 不是合法 JSON: ${error instanceof Error ? error.message : String(error)}`,
    }
  }
}

/**
 * Re-read the object(s) a successful write touched; never trust the write response as state.
 *
 * The reload callback is held in a ref and the effect depends only on `state`: a caller's
 * `onWritten` is a fresh closure every render, so putting it in the dep list would re-run the
 * effect on every render while the state stays `succeeded` — reloading forever.
 */
function useAutoReread(state: WriteState, reload: () => void): void {
  const reloadRef = useRef(reload)
  reloadRef.current = reload
  useEffect(() => {
    if (state === 'succeeded') reloadRef.current()
  }, [state])
}

function currentCriteriaJson(value: unknown): string {
  return JSON.stringify(value ?? { requirements: [] }, null, 2)
}

/** The freshly re-read Task/Step rows, shown with the browser-side read time. */
function WriteReadback({
  title,
  loadedAt,
  rows,
}: {
  title: string
  loadedAt: string | null
  rows: Array<{ label: string; value: ReactNode }>
}): JSX.Element {
  return (
    <div className="state state--meta" data-testid="write-readback">
      <p className="state__headline">{title}</p>
      <KeyValue rows={rows} />
      <p className="state__meta">
        读回时间 <code>{loadedAt ?? '—'}</code> —— 这段值来自对 Core 的重新读取,不是写入响应。
      </p>
    </div>
  )
}

function CriteriaTextField({
  id,
  value,
  onChange,
  testId,
  note,
  error,
}: {
  id: string
  value: string
  onChange: (next: string) => void
  testId: string
  note: ReactNode
  error?: string
}): JSX.Element {
  return (
    <div className="filters">
      <label htmlFor={id}>
        acceptance_criteria(Core 必填;JSON,唯一键 <code>requirements</code>)
        <textarea
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={6}
          data-testid={testId}
        />
      </label>
      {error ? (
        <p className="state state--error" role="alert" data-testid={`${testId}-error`}>
          {error}
        </p>
      ) : null}
      <p className="state state--meta" data-testid={`${testId}-note`}>
        {note}
      </p>
    </div>
  )
}

function StepPicker({
  id,
  steps,
  value,
  onChange,
}: {
  id: string
  steps: Step[]
  value: string
  onChange: (next: string) => void
}): JSX.Element {
  return (
    <label htmlFor={id}>
      step(Core 校验 revision 归属)
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        data-testid="step-picker"
      >
        {steps.map((step) => (
          <option key={step.step_id} value={step.step_id}>
            {step.title} ({step.step_id}) — {step.status} r{step.revision}
          </option>
        ))}
      </select>
    </label>
  )
}

/* -------------------------------------------------------------------------- *
 * The Task view's write tab
 * -------------------------------------------------------------------------- */

export function TaskWrites({ taskId }: { taskId: string }): JSX.Element {
  const { endpoints } = useSession()
  const task = useResource((signal) => endpoints.getTask(taskId, signal), [taskId])
  const steps = useResource((signal) => endpoints.listSteps(taskId, signal), [taskId])
  // §8's readback is "the object's own row *and* the history": the command Event is the
  // auditable record of what was written, so it is re-read after a write too.
  const history = useResource((signal) => endpoints.taskHistory(taskId, signal), [taskId])
  const [selectedStepId, setSelectedStepId] = useState('')

  const rows = steps.data?.steps ?? []
  const selected = rows.find((step) => step.step_id === selectedStepId) ?? rows[0] ?? null
  const onWritten = (): void => {
    task.reload()
    steps.reload()
    history.reload()
  }

  return (
    <div className="stack">
      <Panel title="P6-02 TaskStep 写入(冻结审计写)">
        <p className="state state--meta">
          本页只提供冻结规格 <code>web/P6-02B-TASK-STEP-WRITE-SPEC.md</code> 的七条 Task/Step
          写入。写入前先读取对象与 revision 并原样回传;每次意图只铸一个 <code>event_id</code>,
          重试是重放而不是第二次写入;写入后重新读取并显示 Core 的值,不做乐观更新。超时/中断一律显示
          <strong>结果未知</strong>,不显示成功也不显示失败。判定(状态机、revision、Evidence
          门槛、scope、actor)全部是 Core 的,本页不预判、也不改用更高权限的凭据。
        </p>
        {task.status === 'loading' && task.data === null ? <Loading what="Task" /> : null}
        {task.error ? <ErrorPanel error={task.error} what="Task" onRetry={task.reload} /> : null}
        {steps.status === 'loading' && steps.data === null ? <Loading what="Steps" /> : null}
        {steps.error ? <ErrorPanel error={steps.error} what="Steps" onRetry={steps.reload} /> : null}
      </Panel>

      {task.data ? (
        <>
          <StepCreatePanel
            task={task.data.task}
            steps={rows}
            taskLoadedAt={task.loadedAt}
            stepsLoadedAt={steps.loadedAt}
            onWritten={onWritten}
          />
          <TaskTransitionPanel
            task={task.data.task}
            loadedAt={task.loadedAt}
            onWritten={onWritten}
          />
          <TaskCriteriaPanel task={task.data.task} loadedAt={task.loadedAt} onWritten={onWritten} />
          <TaskAcceptPanel task={task.data.task} loadedAt={task.loadedAt} onWritten={onWritten} />
          {selected ? (
            <>
              <StepTransitionPanel
                task={task.data.task}
                step={selected}
                steps={rows}
                selectedStepId={selected.step_id}
                onSelectStep={setSelectedStepId}
                loadedAt={steps.loadedAt}
                onWritten={onWritten}
              />
              <StepCriteriaPanel
                task={task.data.task}
                step={selected}
                steps={rows}
                selectedStepId={selected.step_id}
                onSelectStep={setSelectedStepId}
                loadedAt={steps.loadedAt}
                onWritten={onWritten}
              />
            </>
          ) : null}
        </>
      ) : null}

      {steps.data && rows.length === 0 ? (
        <Panel title="窗口内没有 Step">
          <Empty
            what="Step"
            hint="该 Task 还没有 Step;Step transition/criteria 需要先创建一个 Step。"
          />
        </Panel>
      ) : null}

      <Panel title="command history(写后重读)">
        {history.status === 'loading' && history.data === null ? <Loading what="History" /> : null}
        {history.error ? (
          <ErrorPanel error={history.error} what="History" onRetry={history.reload} />
        ) : null}
        {history.data && history.data.events.length === 0 ? <Empty what="command Event" /> : null}
        {history.data && history.data.events.length > 0 ? (
          <ul className="plain" data-testid="write-history">
            {history.data.events.slice(-5).map((event) => (
              <li key={event.event_id}>
                <code>{event.event_type}</code> · seq {event.seq} ·{' '}
                <SourceLink kind="event" id={event.event_id} label="command event" taskId={taskId} />
              </li>
            ))}
          </ul>
        ) : null}
        <p className="state state--meta">
          这是对 <code>GET /v1/tasks/{'{tsk}'}/history</code> 的重新读取:写入是否发生、写成了什么,
          以 Core 的 command Event 为准,不以本页的响应为准。
        </p>
      </Panel>

      <Panel title="写入依赖的读取">
        {steps.loadedAt ? (
          <StaleBanner
            loadedAt={steps.loadedAt}
            scope={`Task ${taskId} 的 Step 列表(写入 basis 来源)`}
          />
        ) : null}
        <p className="state state--meta">
          可迁移边与 criteria↔Evidence 的充分性都由 Core 判定:
          <code>409 invalid_state_transition</code> 会带回 Core 的
          <code>from_status</code>/<code>to_status</code>,<code>422 missing_evidence</code>
          会带回 Core 的 requirement 详情。本页不会把前端参考矩阵当作可提交的依据。只读的
          <a href={sourceHref.task(taskId, 'evidence')}>criteria → Evidence 参考矩阵</a>
          在 Evidence 标签页:它只显示 Core <code>_fresh</code> 的浏览器可读部分与
          F1/F2/F8/F9d/F9e 残差,永远不是通过结论。
        </p>
      </Panel>
    </div>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 2 — Step create
 * -------------------------------------------------------------------------- */

export function StepCreatePanel({
  task,
  steps,
  taskLoadedAt,
  stepsLoadedAt,
  onWritten,
}: {
  task: Task
  steps: Step[]
  taskLoadedAt: string | null
  stepsLoadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [criteriaText, setCriteriaText] = useState(() => currentCriteriaJson(null))
  const titleId = useId()
  const descriptionId = useId()
  const criteriaId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.createStep(task.task_id, body, signal),
    { keyKind: 'event_id', targetKey: `step.create:${task.task_id}` },
  )
  useAutoReread(action.state, onWritten)
  const parsed = parseCriteriaField(criteriaText)

  return (
    <>
      <WritePanel
        title="Step 创建 (state:write)"
        basis={`task ${task.task_id} @ revision r${task.revision} —— Step 创建读取的是 Task 的 revision`}
        action={action}
        submitLabel="创建 Step"
        reasonMode="none"
        targetKey={`step.create:${task.task_id}`}
        ready={title.trim() !== '' && parsed.value !== undefined}
        build={({ hostId }) =>
          stepCreateBody({
            title: title.trim(),
            expected_revision: task.revision,
            host_id: hostId,
            description: description.trim() === '' ? undefined : description,
            acceptance_criteria: parsed.value as { requirements: unknown[] } | undefined,
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <label htmlFor={titleId}>
              title(Core 必填,≤512)
              <input
                id={titleId}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                data-testid="step-create-title"
              />
            </label>
            <label htmlFor={descriptionId}>
              description(可选)
              <input
                id={descriptionId}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                data-testid="step-create-description"
              />
            </label>
            <CriteriaTextField
              id={criteriaId}
              value={criteriaText}
              onChange={setCriteriaText}
              testId="step-create-criteria"
              error={parsed.error}
              note="非空 requirements 会额外要求 state:accept 与 human/system actor;Core 才是判定者。"
            />
          </div>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Task 与 Step 列表"
          loadedAt={stepsLoadedAt ?? taskLoadedAt}
          rows={[
            { label: 'task revision', value: <code>r{task.revision}</code> },
            { label: 'task status', value: <StatusBadge kind="task" status={task.status} /> },
            { label: '窗口内 Step 数', value: String(steps.length) },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 3 — Step transition
 * -------------------------------------------------------------------------- */

export function StepTransitionPanel({
  task,
  step,
  steps,
  selectedStepId,
  onSelectStep,
  loadedAt,
  onWritten,
}: {
  task: Task
  step: Step
  steps: Step[]
  selectedStepId: string
  onSelectStep: (next: string) => void
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [status, setStatus] = useState('IN_PROGRESS')
  const statusId = useId()
  const stepPickerId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.transitionStep(step.step_id, body, signal),
    { keyKind: 'event_id', targetKey: `step.transition:${step.step_id}` },
  )
  useAutoReread(action.state, onWritten)

  return (
    <>
      <WritePanel
        title="Step 状态转换 (state:write)"
        basis={`step ${step.step_id} @ revision r${step.revision} —— status ${step.status}`}
        action={action}
        submitLabel="提交 Step 转换"
        // Core requires `reason` only for a SKIPPED target or a BLOCKED/SKIPPED source, so the
        // field is offered and omitted when blank; omitting it where Core needs it is Core's 400.
        reasonMode="optional"
        targetKey={`step.transition:${step.step_id}`}
        ready
        build={({ hostId, reason }) =>
          stepTransitionBody({
            status,
            expected_revision: step.revision,
            host_id: hostId,
            reason: reason === '' ? undefined : reason,
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <StepPicker id={stepPickerId} steps={steps} value={selectedStepId} onChange={onSelectStep} />
            <label htmlFor={statusId}>
              status(目标;Core 的状态词汇,不是本页认可的可迁移边)
              <select
                id={statusId}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                data-testid="step-transition-status"
              >
                {STEP_STATUSES.map((candidate) => (
                  <option key={candidate} value={candidate}>
                    {candidate}
                  </option>
                ))}
              </select>
            </label>
            <p className="state state--meta" data-testid="step-transition-edge-note">
              本页不判断这条边是否合法,也不替你禁用按钮。→VERIFIED/ACCEPTED 由 Core 的 Evidence
              validator 判定;不合法时 Core 返回 <code>409 invalid_state_transition</code>,带回
              <code>from_status</code>/<code>to_status</code>。Core 对 →SKIPPED(以及
              BLOCKED/SKIPPED 来源)要求 reason,空白时由 Core 以 400 拒绝。
            </p>
          </div>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Step 与 Task(Step 写入同时推进两者)"
          loadedAt={loadedAt}
          rows={[
            { label: 'step_id', value: <code>{step.step_id}</code> },
            { label: 'step revision', value: <code>r{step.revision}</code> },
            { label: 'step status', value: <StatusBadge kind="step" status={step.status} /> },
            { label: 'task revision', value: <code>r{task.revision}</code> },
            { label: 'task status', value: <StatusBadge kind="task" status={task.status} /> },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 7 — Step criteria
 * -------------------------------------------------------------------------- */

export function StepCriteriaPanel({
  task,
  step,
  steps,
  selectedStepId,
  onSelectStep,
  loadedAt,
  onWritten,
}: {
  task: Task
  step: Step
  steps: Step[]
  selectedStepId: string
  onSelectStep: (next: string) => void
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [criteriaText, setCriteriaText] = useState(() => currentCriteriaJson(step.acceptance_criteria))
  const stepPickerId = useId()
  const criteriaId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.setStepCriteria(step.step_id, body, signal),
    { keyKind: 'event_id', targetKey: `step.criteria:${step.step_id}` },
  )
  useAutoReread(action.state, onWritten)
  // A different Step is a different basis, so the shown criteria follow the selection rather
  // than carrying the previous Step's text into this one's write.
  useEffect(() => {
    setCriteriaText(currentCriteriaJson(step.acceptance_criteria))
  }, [step.step_id, step.revision, step.acceptance_criteria])
  const parsed = parseCriteriaField(criteriaText)

  return (
    <>
      <WritePanel
        title="Step criteria 更新 (state:accept)"
        basis={`step ${step.step_id} @ revision r${step.revision} —— criteria ${step.acceptance_criteria.requirements.length} 项`}
        action={action}
        submitLabel="提交 Step criteria"
        reasonMode="none"
        targetKey={`step.criteria:${step.step_id}`}
        ready={parsed.value !== undefined}
        build={({ hostId }) =>
          stepCriteriaBody({
            acceptance_criteria: parsed.value as { requirements: unknown[] },
            expected_revision: step.revision,
            host_id: hostId,
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <StepPicker id={stepPickerId} steps={steps} value={selectedStepId} onChange={onSelectStep} />
            <CriteriaTextField
              id={criteriaId}
              value={criteriaText}
              onChange={setCriteriaText}
              testId="step-criteria"
              error={parsed.error}
              note="空 requirements 的 Step 无法到达 VERIFIED/ACCEPTED(Core 返回 422 missing_evidence);本面板只显示 Core 返回的 evidence_ids/rule_versions,不自行判断是否充分。"
            />
          </div>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Step 与 Task(Step criteria 同时推进两者)"
          loadedAt={loadedAt}
          rows={[
            { label: 'step revision', value: <code>r{step.revision}</code> },
            { label: 'criteria 项数', value: String(step.acceptance_criteria.requirements.length) },
            { label: 'task revision', value: <code>r{task.revision}</code> },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 4 — Task transition
 * -------------------------------------------------------------------------- */

export function TaskTransitionPanel({
  task,
  loadedAt,
  onWritten,
}: {
  task: Task
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [status, setStatus] = useState('BLOCKED')
  const statusId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.transitionTask(task.task_id, body, signal),
    { keyKind: 'event_id', targetKey: `task.transition:${task.task_id}` },
  )
  useAutoReread(action.state, onWritten)

  return (
    <>
      <WritePanel
        title="Task 状态转换 (state:write)"
        basis={`task ${task.task_id} @ revision r${task.revision} —— status ${task.status}`}
        action={action}
        submitLabel="提交 Task 转换"
        // §2 note: `reason` is required on every legal Task edge, not only on CANCELLED ones.
        reasonMode="required"
        targetKey={`task.transition:${task.task_id}`}
        ready
        build={({ hostId, reason }) =>
          transitionBody({
            status,
            reason,
            expected_revision: task.revision,
            host_id: hostId,
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <label htmlFor={statusId}>
              status(目标;Core 的状态词汇,不是本页认可的可迁移边)
              <select
                id={statusId}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                data-testid="task-transition-status"
              >
                {TASK_STATUSES.map((candidate) => (
                  <option key={candidate} value={candidate}>
                    {candidate}
                  </option>
                ))}
              </select>
            </label>
            <p className="state state--meta" data-testid="task-transition-edge-note">
              Task 的每一条合法边都要求 reason;本页不判断边是否合法。CANCELLED 来源或目标还额外要求
              <code>state:accept</code> 与 human/system actor,由 Core 判定并返回
              <code>forbidden_scope</code>/<code>forbidden_actor_kind</code>。
            </p>
          </div>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Task 自己的行"
          loadedAt={loadedAt}
          rows={[
            { label: 'task_id', value: <code>{task.task_id}</code> },
            { label: 'revision', value: <code>r{task.revision}</code> },
            { label: 'status', value: <StatusBadge kind="task" status={task.status} /> },
            { label: 'updated_at', value: formatTimestamp(task.updated_at) },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 5 — Task criteria
 * -------------------------------------------------------------------------- */

export function TaskCriteriaPanel({
  task,
  loadedAt,
  onWritten,
}: {
  task: Task
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [criteriaText, setCriteriaText] = useState(() => currentCriteriaJson(task.acceptance_criteria))
  const criteriaId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.setTaskCriteria(task.task_id, body, signal),
    { keyKind: 'event_id', targetKey: `task.criteria:${task.task_id}` },
  )
  useAutoReread(action.state, onWritten)
  useEffect(() => {
    setCriteriaText(currentCriteriaJson(task.acceptance_criteria))
  }, [task.task_id, task.revision, task.acceptance_criteria])
  const parsed = parseCriteriaField(criteriaText)

  return (
    <>
      <WritePanel
        title="Task criteria 更新 (state:accept)"
        basis={`task ${task.task_id} @ revision r${task.revision} —— criteria ${task.acceptance_criteria.requirements.length} 项`}
        action={action}
        submitLabel="提交 Task criteria"
        reasonMode="none"
        targetKey={`task.criteria:${task.task_id}`}
        ready={parsed.value !== undefined}
        build={({ hostId }) =>
          criteriaBody({
            acceptance_criteria: parsed.value as { requirements: unknown[] },
            expected_revision: task.revision,
            host_id: hostId,
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <CriteriaTextField
              id={criteriaId}
              value={criteriaText}
              onChange={setCriteriaText}
              testId="task-criteria"
              error={parsed.error}
              note="criteria 在 Core 的 Evidence validator 里才变成充分性结论;本面板只显示 Core 的 evidence_ids/rule_versions。"
            />
          </div>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Task 自己的行"
          loadedAt={loadedAt}
          rows={[
            { label: 'revision', value: <code>r{task.revision}</code> },
            {
              label: 'criteria 项数',
              value: String(task.acceptance_criteria.requirements.length),
            },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 6 — Task accept
 * -------------------------------------------------------------------------- */

export function TaskAcceptPanel({
  task,
  loadedAt,
  onWritten,
}: {
  task: Task
  loadedAt: string | null
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const action = useWriteAction(
    (body, signal) => endpoints.acceptTask(task.task_id, body, signal),
    { keyKind: 'event_id', targetKey: `task.accept:${task.task_id}` },
  )
  useAutoReread(action.state, onWritten)

  return (
    <>
      <WritePanel
        title="Task 验收 (state:accept,Evidence 门槛)"
        basis={`task ${task.task_id} @ revision r${task.revision} —— status ${task.status}`}
        action={action}
        submitLabel="提交验收"
        reasonMode="none"
        targetKey={`task.accept:${task.task_id}`}
        ready
        build={({ hostId }) => acceptBody({ expected_revision: task.revision, host_id: hostId })}
        onSucceeded={onWritten}
        fields={
          <p className="state state--meta" data-testid="task-accept-note">
            Core 对这条路由<strong>总是</strong>运行 Evidence validator:需要至少一个 ACCEPTED
            Step、没有未完成的 Step、Task criteria 非空,以及当前的 human confirmation 与
            fingerprint。环境无法产生通过判定时,Core 返回 <code>422 missing_evidence</code>,
            本页记录为 <strong>BLOCKED</strong> 并显示 Core 的 code/message/details(不显示为失败,
            也不伪装成功)。验收返回的 <code>evidence_ids</code>/<code>rule_versions</code> 一律来自
            Core 的响应。
          </p>
        }
      />
      {action.state === 'succeeded' ? (
        <WriteReadback
          title="写后读回:Task 自己的行"
          loadedAt={loadedAt}
          rows={[
            { label: 'revision', value: <code>r{task.revision}</code> },
            { label: 'status', value: <StatusBadge kind="task" status={task.status} /> },
          ]}
        />
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- *
 * Route 1 — Task create (hosted by ProjectView)
 * -------------------------------------------------------------------------- */

export function TaskCreatePanel({
  projectId,
  onWritten,
}: {
  projectId: string
  onWritten: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [criteriaText, setCriteriaText] = useState('')
  const [sourceSystem, setSourceSystem] = useState('')
  const [sourceEventId, setSourceEventId] = useState('')
  const titleId = useId()
  const descriptionId = useId()
  const criteriaId = useId()
  const sourceSystemId = useId()
  const sourceEventIdId = useId()
  const action = useWriteAction(
    (body, signal) => endpoints.createTask(body, signal),
    { keyKind: 'event_id', targetKey: `task.create:${projectId}` },
  )
  useAutoReread(action.state, onWritten)

  const criteriaBlank = criteriaText.trim() === ''
  const parsed = criteriaBlank ? { value: undefined as unknown } : parseCriteriaField(criteriaText)

  return (
    <>
      <WritePanel
        title="Task 创建 (objects:write)"
        basis={`project ${projectId} —— 创建不读 revision,也禁止发送 expected_revision`}
        action={action}
        submitLabel="创建 Task"
        reasonMode="none"
        targetKey={`task.create:${projectId}`}
        ready={title.trim() !== '' && (criteriaBlank || parsed.value !== undefined)}
        build={({ hostId }) =>
          taskCreateBody({
            title: title.trim(),
            project_id: projectId,
            host_id: hostId,
            description: description.trim() === '' ? undefined : description,
            acceptance_criteria: parsed.value as { requirements: unknown[] } | undefined,
            source_system: sourceSystem.trim() === '' ? undefined : sourceSystem.trim(),
            source_event_id: sourceEventId.trim() === '' ? undefined : sourceEventId.trim(),
          })
        }
        onSucceeded={onWritten}
        fields={
          <div className="filters">
            <label htmlFor={titleId}>
              title(Core 必填,≤512)
              <input
                id={titleId}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                data-testid="task-create-title"
              />
            </label>
            <label htmlFor={descriptionId}>
              description(可选)
              <input
                id={descriptionId}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                data-testid="task-create-description"
              />
            </label>
            <label htmlFor={criteriaId}>
              acceptance_criteria(可选;留空表示不发送该字段,Core 记为 requirements: [])
              <textarea
                id={criteriaId}
                value={criteriaText}
                onChange={(event) => setCriteriaText(event.target.value)}
                rows={4}
                data-testid="task-create-criteria"
              />
            </label>
            {!criteriaBlank && parsed.error ? (
              <p
                className="state state--error"
                role="alert"
                data-testid="task-create-criteria-error"
              >
                {parsed.error}
              </p>
            ) : null}
            <label htmlFor={sourceSystemId}>
              source_system(可选;与 source_event_id 一起构成来源身份)
              <input
                id={sourceSystemId}
                value={sourceSystem}
                onChange={(event) => setSourceSystem(event.target.value)}
                data-testid="task-create-source-system"
              />
            </label>
            <label htmlFor={sourceEventIdId}>
              source_event_id(可选;同一来源身份+不同 event_id 会被 Core 判 409
              source_event_duplicate)
              <input
                id={sourceEventIdId}
                value={sourceEventId}
                onChange={(event) => setSourceEventId(event.target.value)}
                data-testid="task-create-source-event-id"
              />
            </label>
            <p className="state state--meta" data-testid="task-create-note">
              本面板<strong>不发送 expected_revision</strong>:Core 在 Task 创建上显式拒绝它
              (<code>400 unexpected_expected_revision</code>)。非空 criteria 会额外要求
              <code>state:accept</code> 与 human/system actor,由 Core 判定。
            </p>
          </div>
        }
      />
      {action.state === 'succeeded' && action.result ? (
        <JsonBlock
          value={action.result}
          label="创建结果(Core 原文,含 object.acceptance_criteria)"
        />
      ) : null}
    </>
  )
}
