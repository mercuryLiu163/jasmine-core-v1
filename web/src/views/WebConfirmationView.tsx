import { useEffect, useRef, useState } from 'react'
import { CoreApiError } from '../api/errors'
import { WEB_CONFIRMATION_STATEMENT } from '../api/webConfirmations'
import type { ConfirmationIdentity, ConfirmationPreparation, ConfirmationReceipt, ConfirmationStatus, PendingConfirmation, ConfirmationPrepareBody, ConfirmationSubmitBody } from '../api/webConfirmations'
import type { Rule, Step, Task } from '../api/types'
import { useSession } from '../app/session'
import { sourceHref } from '../app/router'
import { isEventId, newEventId } from '../lib/ids'
import { ruleApplies } from '../lib/criteriaMatrix'

// Authorized raw review must remain complete. React renders escaped text, never HTML.
function OriginalJson({ value, testId }: { value: unknown; testId?: string }): JSX.Element {
  return <pre data-testid={testId} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value, null, 2)}</pre>
}
function errorText(error: unknown): string {
  return error instanceof CoreApiError ? `${error.status ?? 'network'} ${error.code} · request_id ${error.requestId ?? 'unknown'} · ${error.message}` : '读取失败；结果未核实'
}
function uncertain(error: unknown): boolean {
  return !(error instanceof CoreApiError) || error.status === null || error.status >= 500 || error.code === 'ui_unparsable_response'
}

export function WebConfirmationView({ taskId }: { taskId: string }): JSX.Element {
  const { token, endpoints, pendingConfirmations, trackConfirmation, clearConfirmation } = useSession()
  const [identity, setIdentity] = useState<ConfirmationIdentity | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [steps, setSteps] = useState<Step[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [stepId, setStepId] = useState('')
  const [target, setTarget] = useState<'VERIFIED' | 'ACCEPTED'>('ACCEPTED')
  const [rulePair, setRulePair] = useState('')
  const [preparation, setPreparation] = useState<ConfirmationPreparation | null>(null)
  const [affirmed, setAffirmed] = useState(false)
  const [note, setNote] = useState('')
  const [noteOrigin, setNoteOrigin] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const generation = useRef(0)
  const [message, setMessage] = useState('正在读取 Core 身份与能力…')
  const [receipt, setReceipt] = useState<ConfirmationReceipt | null>(null)
  const [readback, setReadback] = useState<unknown>(null)
  const [status, setStatus] = useState<ConfirmationStatus | null>(null)
  const [statusId, setStatusId] = useState('')
  useEffect(() => {
    const current = ++generation.current
    const abort = new AbortController()
    setIdentity(null); setTask(null); setPreparation(null); setAffirmed(false); setReceipt(null); setReadback(null); setStatus(null); setRules([]); setSteps([])
    setStepId(''); setTarget('ACCEPTED'); setRulePair('')
    setBusy(false); busyRef.current = false
    setMessage('正在读取 Core 身份与能力…')
    void Promise.all([endpoints.confirmationIdentity(abort.signal), endpoints.getTask(taskId, abort.signal), endpoints.listSteps(taskId, abort.signal)])
      .then(async ([me, result, list]) => {
        if (current !== generation.current) return
        setIdentity(me); setTask(result.task); setSteps(list.steps)
        if (me.capabilities?.web_confirmation_v1 !== true) { setMessage('DEPENDENCY_PENDING：Core 未提供 web_confirmation_v1；请启用支持本契约的 Core 与代理。'); return }
        setMessage('确认仅记录 INFO；Evidence 充分性未判定，VERIFIED/ACCEPTED 另由 Core State API 判定。')
        const active = await endpoints.listActiveRules({ project_id: result.task.project_id, task_id: taskId }, abort.signal)
        if (current === generation.current) setRules(active.rules.filter((rule) => rule.status === 'ACTIVE' && ruleApplies(rule, taskId, result.task.project_id)))
      }).catch((error) => { if (current === generation.current && !abort.signal.aborted) setMessage(`DEPENDENCY_PENDING / 身份或读取不可用：${errorText(error)}。代理须 P6_ENABLE_WEB_CONFIRMATIONS=1；不使用旧 confirm。`) })
    return () => { ++generation.current; abort.abort() }
  }, [taskId, token, endpoints])
  const selectedStep = steps.find((step) => step.step_id === stepId)
  const canConfirm = identity?.capabilities?.web_confirmation_v1 === true && identity.actor_kind === 'human' && !!identity.home_host_id &&
    ['evidence:confirm', 'objects:read', 'state:read', 'authority:read', 'events:read', 'evidence:read'].every((scope) => identity.scopes.includes(scope))
  const pendingForSelection = pendingConfirmations.some((pending) => pending.keyId === identity?.key_id && pending.taskId === taskId && pending.stepId === (stepId || null))
  const lock = busy || pendingForSelection
  function discardReview(): void { setPreparation(null); setAffirmed(false); setReceipt(null); setReadback(null); setStatus(null) }
  function receiptShapeValid(result: ConfirmationReceipt): boolean {
    return result?.contract_version === 'web_confirmation_v1' && isEventId(result.preparation_id) && isEventId(result.source_event?.event_id ?? '') &&
      isEventId(result.event?.event_id ?? '') && !!result.evidence?.evidence_id && result.evidence.kind === 'USER_CONFIRMATION' && result.evidence.status === 'INFO' &&
      result.source_event.event_type === 'human.confirmation_submitted' && result.evidence.source_event_id === result.source_event.event_id && result.evidence.change_event_id === result.event.event_id
  }
  async function readReceipt(result: ConfirmationReceipt, current: number): Promise<boolean> {
    if (current !== generation.current) return false
    setReceipt(result); setReadback(null)
    setMessage('确认已记录 / INFO；正在 GET 读取 source、command、Evidence。VERIFIED/ACCEPTED 另行由 Core 判定。')
    try {
      const [source, command, evidence] = await Promise.all([endpoints.getEvent(result.source_event.event_id), endpoints.getEvent(result.event.event_id), endpoints.getEvidence(result.evidence.evidence_id)])
      if (current !== generation.current) return false
      if (source.event.event_id !== result.source_event.event_id || command.event.event_id !== result.event.event_id || evidence.evidence.evidence_id !== result.evidence.evidence_id || evidence.evidence.source_event_id !== source.event.event_id || evidence.evidence.change_event_id !== command.event.event_id) throw new Error('readback mismatch')
      setReadback({ source_event: source.event, command_event: command.event, evidence: evidence.evidence })
      setMessage('确认已记录 / INFO；三个独立 GET readback 已完成。Evidence 充分性未判定，VERIFIED/ACCEPTED 仍需另行提交。')
      return true
    } catch (error) { if (current === generation.current) setMessage(`确认返回了 receipt，但 GET readback 未完成 / 未核实：${errorText(error)}；可读取 status 或打开来源深链。`); return false }
  }
  async function send(original: PendingConfirmation): Promise<void> {
    if (busyRef.current || identity?.key_id !== original.keyId) return
    busyRef.current = true; setBusy(true)
    const current = generation.current
    const pending = { ...original, body: { ...original.body, event_id: original.id } }
    trackConfirmation(pending)
    try {
      if (pending.kind === 'prepare') {
        const prepared = await endpoints.prepareConfirmation(pending.body as ConfirmationPrepareBody)
        if (current !== generation.current) return
        if (prepared.contract_version !== 'web_confirmation_v1' || prepared.review_document?.actor?.key_id !== pending.keyId || prepared.review_document?.task?.task_id !== pending.taskId || (prepared.review_document.step?.step_id ?? null) !== pending.stepId || prepared.review_document.statement !== WEB_CONFIRMATION_STATEMENT || prepared.review_document.target_status !== (pending.body as ConfirmationPrepareBody).target_status || prepared.review_document.task.revision !== (pending.body as ConfirmationPrepareBody).expected_task_revision || (prepared.review_document.step?.revision ?? null) !== (pending.body as ConfirmationPrepareBody).expected_step_revision || prepared.review_document.workspace?.complete !== true) throw new Error('invalid preparation response')
        clearConfirmation(pending.id); setPreparation(prepared); setAffirmed(false); setMessage('请审阅以下完整 Core 原文材料；确认不等于验收。')
      } else {
        const result = await endpoints.submitConfirmation(pending.body as ConfirmationSubmitBody)
        if (current !== generation.current) return
        if (!receiptShapeValid(result) || result.event?.event_id !== pending.id || result.preparation_id !== (pending.body as ConfirmationSubmitBody).preparation_id || !result.source_event?.event_id || !result.evidence?.evidence_id) throw new Error('invalid receipt response')
        clearConfirmation(pending.id); setAffirmed(false); setPreparation(null)
        await readReceipt(result, current)
      }
    } catch (error) {
      if (current !== generation.current) return
      if (uncertain(error)) setMessage(`UNKNOWN_OUTCOME / 结果未知：${errorText(error)}。固定原 event_id/body；使用下方 status 或精确重放恢复，不能另建提交。`)
      else { clearConfirmation(pending.id); setMessage(`Core 拒绝：${errorText(error)}。已保留 note 草稿；请重新 prepare 并审阅，不会自动换 revision 重试。`); setPreparation(null); setAffirmed(false) }
    } finally { if (current === generation.current) { busyRef.current = false; setBusy(false) } }
  }
  async function loadStatus(id: string, pending?: PendingConfirmation): Promise<void> {
    if (busyRef.current || !isEventId(id) || (pending && pending.keyId !== identity?.key_id)) return
    const current = generation.current
    busyRef.current = true; setBusy(true)
    try {
      const result = await endpoints.confirmationStatus(id)
      if (current !== generation.current) return
      if (result.preparation?.preparation_id !== id || !['OPEN','CONSUMED','EXPIRED','CLOCK_INVALID'].includes(result.availability) || (result.consumption && (!receiptShapeValid(result.consumption.receipt) || result.consumption.command_event_id !== result.consumption.receipt.event.event_id || result.consumption.source_event_id !== result.consumption.receipt.source_event.event_id || result.consumption.evidence_id !== result.consumption.receipt.evidence.evidence_id))) throw new Error('invalid status response')
      setStatus(result); setMessage(`status ${result.availability} · server_time ${result.server_time}；OPEN 不证明仍可提交；历史 receipt 不代表 fresh。`)
      if (result.consumption) {
        const sameCommand = pending?.kind === 'submit' && result.consumption.command_event_id === pending.id
        if (sameCommand && pending) clearConfirmation(pending.id)
        const readbackComplete = await readReceipt(result.consumption.receipt, current)
        if (!pending && readbackComplete && current === generation.current) setMessage('历史确认 receipt / INFO，三个 GET readback 已完成；不代表 fresh，不是本次提交。')
        if (current === generation.current && pending?.kind === 'submit' && !sameCommand) setMessage(`此 preparation 已由另一命令 ${result.consumption.command_event_id} 消费；不是当前草稿成功。原命令可精确重放核实。`)
      }
    } catch (error) { if (current === generation.current) setMessage(`status 未核实：${errorText(error)}；pending 保留，404/OPEN 不证明仍在途写失败。`) }
    finally { if (current === generation.current) { busyRef.current = false; setBusy(false) } }
  }
  return <section className="stack" aria-label="Core Web 人工确认" data-testid="confirmation-panel">
    <h2>人工确认 · Core prepare → submit</h2><p role="status" data-testid="confirmation-message">{message}</p>
    <p>bearer 信任边界：确认表示已认证 human 凭据提交结构化意图，不证明自然人本人实际点击。State 操作在 <a href={sourceHref.task(taskId, 'write')}>独立写入页面</a>。</p>
    {identity ? <OriginalJson value={identity} testId="confirmation-identity" /> : null}
    {identity && !canConfirm ? <p>确认不可用：需要 human、登记 home_host、确认与完整材料/readback 读 scopes。不会签发或更换凭据。</p> : null}
    {task ? <p>当前 Task {task.task_id} · {task.title} · {task.status} · revision {task.revision}；note 草稿属于当前 Task/所选 Step；切换对象或目标会清除准备材料和 affirmation。</p> : null}
    <label>确认对象 <select data-testid="confirmation-step" value={stepId} disabled={busy} onChange={(event) => { setStepId(event.target.value); setTarget(event.target.value && steps.find((row) => row.step_id === event.target.value)?.status === 'EXECUTED' ? 'VERIFIED' : 'ACCEPTED'); discardReview() }}>
      <option value="">整个 Task</option>{steps.map((step) => <option key={step.step_id} value={step.step_id}>{step.title} · {step.status} · revision {step.revision} · {step.step_id}</option>)}
    </select></label>
    <label>目标状态 <select data-testid="confirmation-target" value={target} disabled={busy} onChange={(event) => { setTarget(event.target.value as 'VERIFIED' | 'ACCEPTED'); discardReview() }}>
      {stepId ? <option value="VERIFIED">VERIFIED</option> : null}<option value="ACCEPTED">ACCEPTED</option>
    </select></label>
    <label>确认 Rule pair（一次一条） <select data-testid="confirmation-rule" value={rulePair} disabled={busy} onChange={(event) => { setRulePair(event.target.value); discardReview() }}>
      <option value="">普通对象确认 · 无 Rule pair</option>{rules.map((rule) => <option key={rule.rule_id} value={`${rule.rule_id}:${rule.version}`}>{rule.rule_id} · version {rule.version}</option>)}
    </select></label>
    <label>note（原文；仅说明；≤4096 UTF-8 bytes）<textarea data-testid="confirmation-note" value={note} disabled={busy} onChange={(event) => { setNote(event.target.value); setNoteOrigin(`${taskId} / ${stepId || 'Task'}`) }} /></label>
    {note && noteOrigin ? <p data-testid="confirmation-note-origin">保留的 note 草稿原对象：{noteOrigin}；本次 submit 会按当前材料绑定，须自行核对。</p> : null}
    <button data-testid="confirmation-prepare" disabled={!canConfirm || !task || lock} onClick={() => {
      if (!identity || !task || busyRef.current || pendingForSelection) return
      const id = newEventId(); const pair = rulePair.split(':'); discardReview()
      void send({ id, keyId: identity.key_id, taskId, stepId: stepId || null, kind: 'prepare', body: { event_id: id, task_id: taskId, step_id: stepId || null, expected_task_revision: task.revision, expected_step_revision: selectedStep?.revision ?? null, target_status: target, confirmed_rule_id: pair[0] || null, confirmed_rule_version: pair[1] ? Number(pair[1]) : null } })
    }}>prepare 完整审阅材料</button>
    {preparation ? <div>
      <p>prepare {preparation.preparation_id} · 创建 {preparation.created_at} · 到期 {preparation.expires_at} · 文件采样 {preparation.sampled_at} · review_sha256 {preparation.review_sha256}</p>
      <OriginalJson value={preparation.review_document} testId="confirmation-review" /><p>{preparation.review_document.statement}</p>
      <label><input type="checkbox" data-testid="confirmation-affirm" checked={affirmed} disabled={lock} onChange={(event) => setAffirmed(event.target.checked)} />我主动确认以上完整材料与固定声明</label>
      <button data-testid="confirmation-submit" disabled={!affirmed || !canConfirm || lock || new TextEncoder().encode(note).length > 4096} onClick={() => {
        if (!identity || !affirmed || busyRef.current || pendingForSelection) return
        const id = newEventId(); void send({ id, keyId: identity.key_id, taskId, stepId: stepId || null, kind: 'submit', body: { event_id: id, preparation_id: preparation.preparation_id, review_sha256: preparation.review_sha256, affirmed: true, note } })
      }}>submit 人工确认（INFO）</button>
    </div> : null}
    {receipt ? <div data-testid="confirmation-receipt"><p>receipt / {receipt.replayed ? '精确重放原结果' : '首次结果'} / INFO</p>
      <a href={sourceHref.event(receipt.source_event.event_id)}>来源 Event {receipt.source_event.event_id}</a>{' · '}<a href={sourceHref.event(receipt.event.event_id)}>command Event {receipt.event.event_id}</a>{' · '}<a href={sourceHref.evidence(receipt.evidence.evidence_id, taskId)}>Evidence {receipt.evidence.evidence_id}</a>
      <OriginalJson value={receipt} />{readback ? <OriginalJson value={readback} testId="confirmation-readback" /> : null}
    </div> : null}
    <h3>未知结果恢复（仅本标签页内存）</h3>
    {pendingConfirmations.map((pending) => <div key={pending.id} data-testid={`confirmation-pending-${pending.id}`}>
      <p>UNKNOWN_OUTCOME · {pending.kind} · 固定 event_id {pending.id} · Task {pending.taskId} / Step {pending.stepId ?? 'Task'} · owner key {pending.keyId}</p>
      <button data-testid={`confirmation-recovery-status-${pending.id}`} disabled={busy || pending.keyId !== identity?.key_id || pending.taskId !== taskId || pending.stepId !== (stepId || null)} onClick={() => void loadStatus(pending.kind === 'prepare' ? pending.id : (pending.body as ConfirmationSubmitBody).preparation_id, pending)}>读取 preparation status</button>
      <button data-testid={`confirmation-replay-${pending.id}`} disabled={busy || pending.keyId !== identity?.key_id || pending.taskId !== taskId || pending.stepId !== (stepId || null)} onClick={() => void send(pending)}>精确重放原 ID/body</button>
    </div>)}
    <p>刷新会清除凭据与内存草稿。可用 preparation ID 只读恢复历史；不会重新提交或把历史当 fresh。</p>
    <label>preparation ID <input data-testid="confirmation-status-id" value={statusId} onChange={(event) => setStatusId(event.target.value)} /></label>
    <button data-testid="confirmation-status-read" disabled={busy || !isEventId(statusId) || !identity} onClick={() => void loadStatus(statusId)}>读取历史 status</button>
    {status ? <OriginalJson value={status} testId="confirmation-status" /> : null}
  </section>
}
