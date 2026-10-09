import { afterEach, describe, expect, it } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SessionProvider, useSession } from '../src/app/session'
import { WebConfirmationView } from '../src/views/WebConfirmationView'
import { WEB_CONFIRMATION_STATEMENT } from '../src/api/webConfirmations'
import { installFakeCore, type FakeCore, type FakeReply, type RecordedRequest } from './helpers/fakeCore'
const id = (prefix: string, c = '0') => `${prefix}_${c.repeat(26)}`
const taskId = id('tsk'), stepId = id('stp'), keyId = id('key'), sourceId = id('evt','2'), evidenceId = id('evd')
const secret = 'token=synthetic-note-only'
const identity = { actor_id: id('act'), actor_kind: 'human', key_id: keyId, home_host_id: id('hst'), scopes: ['evidence:confirm','objects:read','state:read','authority:read','events:read','evidence:read'], capabilities: { web_confirmation_v1: true }, trust_model: 'registered_human_bearer_key' }
const task = { task_id: taskId, project_id: id('prj'), title:'Review Task',status:'ACTIVE',revision:7,acceptance_criteria:{requirements:[{kind:'USER_CONFIRMATION',required_result:'INFO'}]} }
const step = { step_id:stepId,task_id:taskId,title:'Review Step',status:'EXECUTED',revision:2,acceptance_criteria:task.acceptance_criteria }
let fake: FakeCore | undefined
let prepared: Record<string, unknown>
let result: Record<string, unknown>
function defaultHandler(req: RecordedRequest): FakeReply | undefined {
  if (req.path === '/v1/auth/me') return { body:identity }
  if (req.path === `/v1/tasks/${taskId}`) return {body:{task}}
  if (req.path === `/v1/tasks/${taskId}/steps`) return {body:{steps:[step]}}
  if (req.path === '/v1/rules/active') return {body:{rules:[{rule_id:id('rul'),version:3,status:'ACTIVE',scope:{kind:'global',project_id:null,task_id:null},kind:'ACCEPTANCE',enforcement:'VERIFY'}]}}
  if (req.path.endsWith('/prepare')) {
    const body = req.body!
    prepared = {preparation_id:body.event_id,contract_version:'web_confirmation_v1',review_sha256:'a'.repeat(64),created_at:'2026-10-09T01:00:00Z',expires_at:'2026-10-09T01:10:00Z',sampled_at:'2026-10-09T01:00:00Z',replayed:false,review_document:{actor:{key_id:keyId},task,step:body.step_id ? step:null,target_status:body.target_status,applicable_rules:[{content:'<script>globalThis.compromised=true</script> '+secret}],workspace:{complete:true,coverage:{paths:['all']}},statement:WEB_CONFIRMATION_STATEMENT}}
    return {body:prepared,status:201}
  }
  if (req.path.endsWith('/submit')) {
    result = {preparation_id:req.body!.preparation_id,contract_version:'web_confirmation_v1',review_sha256:'a'.repeat(64),source_event:{event_id:sourceId,event_type:'human.confirmation_submitted',payload:{text:req.body!.note}},event:{event_id:req.body!.event_id},evidence:{evidence_id:evidenceId,kind:'USER_CONFIRMATION',status:'INFO',source_event_id:sourceId,change_event_id:req.body!.event_id},replayed:false}
    return {body:result,status:201}
  }
  if (req.path === `/v1/events/${sourceId}`) return {body:{event:result.source_event}}
  if (req.path.startsWith('/v1/events/')) return {body:{event:result.event}}
  if (req.path === `/v1/evidence/${evidenceId}`) return {body:{evidence:result.evidence}}
  if (req.path.startsWith('/v1/evidence/confirmations/evt_')) return {body:{preparation:prepared,consumption:null,availability:'OPEN',server_time:'2026-10-09T01:01:00Z'}}
  return undefined
}
function Harness({ alternate = false }: { alternate?: boolean }): JSX.Element {
  const {signIn,token} = useSession()
  return <><button onClick={() => signIn(token === 'A' ? 'B' : 'A')}>key switch</button><WebConfirmationView taskId={alternate ? id('tsk','1'):taskId} /></>
}
async function enter(handler?: (request: RecordedRequest) => FakeReply | undefined) {
  fake = installFakeCore({}, req => handler?.(req) ?? defaultHandler(req))
  render(<SessionProvider><Harness /></SessionProvider>)
  await userEvent.click(screen.getByText('key switch'))
  await waitFor(() => expect((screen.getByTestId('confirmation-prepare') as HTMLButtonElement).disabled).toBe(false))
}
async function prepare() {
  await userEvent.selectOptions(screen.getByTestId('confirmation-step'),stepId)
  await userEvent.click(screen.getByTestId('confirmation-prepare'))
  await screen.findByTestId('confirmation-review')
}
afterEach(() => fake?.restore())
describe('Web confirmations: mock UI contract (not real Core acceptance)', () => {
  it('uses exact Core revisions/target/Rule pair, full escaped raw review and an unchecked affirmation; no State request', async () => {
    await enter(); await userEvent.selectOptions(screen.getByTestId('confirmation-rule'),`${id('rul')}:3`); await prepare()
    const body = fake!.callsTo('POST','/v1/evidence/confirmations/prepare')[0]!.body!
    expect(body).toMatchObject({task_id:taskId,step_id:stepId,expected_task_revision:7,expected_step_revision:2,target_status:'VERIFIED',confirmed_rule_id:id('rul'),confirmed_rule_version:3})
    expect(Object.keys(body).sort()).toEqual(['event_id','task_id','step_id','expected_task_revision','expected_step_revision','target_status','confirmed_rule_id','confirmed_rule_version'].sort())
    expect((screen.getByTestId('confirmation-affirm') as HTMLInputElement).checked).toBe(false)
    expect((screen.getByTestId('confirmation-submit') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('confirmation-review').textContent).toContain(secret)
    expect(document.querySelector('script')).toBeNull()
    await userEvent.type(screen.getByTestId('confirmation-note'),secret)
    await userEvent.click(screen.getByTestId('confirmation-affirm')); await userEvent.click(screen.getByTestId('confirmation-submit'))
    await screen.findByTestId('confirmation-readback')
    const sent = fake!.callsTo('POST','/v1/evidence/confirmations/submit')[0]!.body!
    expect(Object.keys(sent).sort()).toEqual(['event_id','preparation_id','review_sha256','affirmed','note'].sort())
    expect(sent.note).toBe(secret); expect(sent.affirmed).toBe(true)
    expect(fake!.requests.filter(req => req.method === 'GET' && (req.path.startsWith('/v1/events/') || req.path === `/v1/evidence/${evidenceId}`))).toHaveLength(3)
    expect(fake!.requests.filter(req => req.method === 'POST')).toHaveLength(2)
    expect(screen.getByTestId('confirmation-message').textContent).toContain('另行提交')
  })
  it('blocks missing capability and agent identity without POST', async () => {
    fake = installFakeCore({}, req => req.path === '/v1/auth/me' ? {body:{...identity,actor_kind:'agent',capabilities:{web_confirmation_v1:false}}}:defaultHandler(req))
    render(<SessionProvider><Harness /></SessionProvider>); await userEvent.click(screen.getByText('key switch'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('DEPENDENCY_PENDING'))
    expect((screen.getByTestId('confirmation-prepare') as HTMLButtonElement).disabled).toBe(true); expect(fake.requests.filter(req => req.method === 'POST')).toHaveLength(0)
  })
  it('preserves note and requires explicit prepare after conflict; no revision retry', async () => {
    await enter(req => req.path.endsWith('/submit') ? {status:409,body:{error:{code:'confirmation_context_conflict',message:'rules changed',details:{changed:['rules']}}}}:undefined)
    await prepare(); await userEvent.type(screen.getByTestId('confirmation-note'),'saved note'); await userEvent.click(screen.getByTestId('confirmation-affirm')); await userEvent.click(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('Core 拒绝'))
    expect((screen.getByTestId('confirmation-note') as HTMLTextAreaElement).value).toBe('saved note'); expect(screen.queryByTestId('confirmation-review')).toBeNull()
    expect(fake!.callsTo('POST','/v1/evidence/confirmations/submit')).toHaveLength(1)
  })
  it('UNKNOWN keeps immutable original ID/body despite note editing; OPEN status is not success; explicit replay restores', async () => {
    let submits = 0
    await enter(req => req.path.endsWith('/submit') && submits++ === 0 ? {networkError:'response lost'}:undefined)
    await prepare(); await userEvent.type(screen.getByTestId('confirmation-note'),'original'); await userEvent.click(screen.getByTestId('confirmation-affirm')); await userEvent.click(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('UNKNOWN_OUTCOME'))
    const command = fake!.callsTo('POST','/v1/evidence/confirmations/submit')[0]!.body!.event_id as string
    expect((screen.getByTestId('confirmation-prepare') as HTMLButtonElement).disabled).toBe(true); expect((screen.getByTestId('confirmation-submit') as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByTestId('confirmation-note'),' changed')
    await userEvent.click(screen.getByTestId(`confirmation-recovery-status-${command}`))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('OPEN 不证明'))
    expect(screen.queryByTestId('confirmation-receipt')).toBeNull()
    await userEvent.click(screen.getByTestId(`confirmation-replay-${command}`)); await screen.findByTestId('confirmation-readback')
    const calls = fake!.callsTo('POST','/v1/evidence/confirmations/submit'); expect(calls).toHaveLength(2); expect(calls[0]!.body).toEqual(calls[1]!.body)
  })
  it('object switching clears review/affirmation; late prepare cannot flow into another key', async () => {
    await enter(); await prepare(); await userEvent.click(screen.getByTestId('confirmation-affirm'))
    await userEvent.selectOptions(screen.getByTestId('confirmation-target'),'ACCEPTED')
    expect(screen.queryByTestId('confirmation-review')).toBeNull()
    fake!.restore()
    fake = installFakeCore({},req => req.path.endsWith('/prepare') ? {...defaultHandler(req),delayMs:80}:defaultHandler(req))
    await userEvent.click(screen.getByTestId('confirmation-prepare')); await userEvent.click(screen.getByText('key switch'))
    await act(async () => new Promise(resolve => setTimeout(resolve,100)))
    expect(screen.queryByTestId('confirmation-review')).toBeNull()
    expect(screen.getByText(/UNKNOWN_OUTCOME · prepare/)).toBeTruthy()
  })
  it('GET readback refusal never claims complete readback', async () => {
    await enter(req => req.path.startsWith('/v1/events/') ? {status:403,body:{error:{code:'forbidden_scope',message:'denied'}}}:undefined)
    await prepare(); await userEvent.click(screen.getByTestId('confirmation-affirm')); await userEvent.click(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('GET readback 未完成'))
    expect(screen.queryByTestId('confirmation-readback')).toBeNull()
  })
})

describe('confirmation recovery boundaries', () => {
  it('a consumed status from a different command does not promote the pending command to success', async () => {
    await enter(req => req.path.endsWith('/submit') ? {...defaultHandler(req),networkError:'lost'} : req.path.startsWith('/v1/evidence/confirmations/evt_') ? {body:{preparation:prepared,availability:'CONSUMED',server_time:'2026-10-09T01:01:00Z',consumption:{command_event_id:id('evt','3'),source_event_id:sourceId,evidence_id:evidenceId,receipt:{...result,event:{event_id:id('evt','3')},evidence:{...(result.evidence as Record<string,unknown>),change_event_id:id('evt','3')}}}}}:undefined)
    await prepare();await userEvent.click(screen.getByTestId('confirmation-affirm'));await userEvent.click(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('UNKNOWN_OUTCOME'))
    const command=fake!.callsTo('POST','/v1/evidence/confirmations/submit')[0]!.body!.event_id as string
    await userEvent.click(screen.getByTestId(`confirmation-recovery-status-${command}`))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('不是当前草稿成功'))
    expect(screen.getByTestId(`confirmation-pending-${command}`)).toBeTruthy()
    expect(fake!.callsTo('POST','/v1/evidence/confirmations/submit')).toHaveLength(1)
  })
  it('keeps a malformed success receipt unknown and prevents duplicate submit from rapid clicks', async () => {
    await enter(req => req.path.endsWith('/submit') ? {body:{...defaultHandler(req)!.body as Record<string,unknown>,source_event:null},delayMs:20}:undefined)
    await prepare();await userEvent.click(screen.getByTestId('confirmation-affirm'))
    await userEvent.dblClick(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('UNKNOWN_OUTCOME'))
    expect(fake!.callsTo('POST','/v1/evidence/confirmations/submit')).toHaveLength(1)
    expect(screen.queryByTestId('confirmation-receipt')).toBeNull()
  })
  it('different key cannot replay old pending body; key switch clears affirmation/material', async () => {
    await enter(req => req.path === '/v1/auth/me' && req.authorization==='Bearer B' ? {body:{...identity,key_id:id('key','1')}}:req.path.endsWith('/submit') ? {networkError:'lost'}:undefined)
    await prepare();await userEvent.click(screen.getByTestId('confirmation-affirm'));await userEvent.click(screen.getByTestId('confirmation-submit'))
    await waitFor(() => expect(screen.getByTestId('confirmation-message').textContent).toContain('UNKNOWN_OUTCOME'))
    const command=fake!.callsTo('POST','/v1/evidence/confirmations/submit')[0]!.body!.event_id as string
    await userEvent.click(screen.getByText('key switch'))
    await waitFor(() => expect(screen.getByTestId('confirmation-identity').textContent).toContain(id('key','1')))
    expect((screen.getByTestId(`confirmation-replay-${command}`) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId(`confirmation-recovery-status-${command}`) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByTestId('confirmation-review')).toBeNull()
  })
  it('changing Step keeps an explicitly named note draft while clearing review', async () => {
    await enter();await prepare();await userEvent.type(screen.getByTestId('confirmation-note'),'draft for step')
    await userEvent.selectOptions(screen.getByTestId('confirmation-step'),'')
    expect(screen.queryByTestId('confirmation-review')).toBeNull()
    expect(screen.getByTestId('confirmation-note-origin').textContent).toContain(stepId)
    expect((screen.getByTestId('confirmation-note') as HTMLTextAreaElement).value).toBe('draft for step')
  })
})


describe('Task scope switch in the same component', () => {
  it('clears old Step/target/Rule selections, preserving named note and pending owner/body', async () => {
    const otherTask = id('tsk','1')
    fake = installFakeCore({}, req => req.path === `/v1/tasks/${otherTask}` ? {body:{task:{...task,task_id:otherTask,project_id:id('prj','1')}}}:req.path === `/v1/tasks/${otherTask}/steps` ? {body:{steps:[]}}:defaultHandler(req))
    function ScopeHarness(): JSX.Element {
      const {signIn} = useSession()
      const [current,setCurrent] = useState(taskId)
      return <><button onClick={() => signIn('A')}>sign in</button><button onClick={() => setCurrent(otherTask)}>switch task</button><WebConfirmationView taskId={current} /></>
    }
    render(<SessionProvider><ScopeHarness /></SessionProvider>)
    await userEvent.click(screen.getByText('sign in'))
    await waitFor(() => expect((screen.getByTestId('confirmation-prepare') as HTMLButtonElement).disabled).toBe(false))
    await userEvent.selectOptions(screen.getByTestId('confirmation-rule'),`${id('rul')}:3`)
    await prepare();await userEvent.type(screen.getByTestId('confirmation-note'),'old object draft')
    await userEvent.click(screen.getByText('switch task'))
    await waitFor(() => expect(screen.getByText(new RegExp(`当前 Task ${otherTask}`))).toBeTruthy())
    expect((screen.getByTestId('confirmation-step') as HTMLSelectElement).value).toBe('')
    expect((screen.getByTestId('confirmation-target') as HTMLSelectElement).value).toBe('ACCEPTED')
    expect((screen.getByTestId('confirmation-rule') as HTMLSelectElement).value).toBe('')
    expect(screen.queryByTestId('confirmation-review')).toBeNull()
    expect(screen.getByTestId('confirmation-note-origin').textContent).toContain(taskId)
    await userEvent.click(screen.getByTestId('confirmation-prepare'))
    const sent = fake!.callsTo('POST','/v1/evidence/confirmations/prepare').at(-1)!.body!
    expect(sent).toMatchObject({task_id:otherTask,step_id:null,expected_step_revision:null,target_status:'ACCEPTED',confirmed_rule_id:null,confirmed_rule_version:null})
  })
})
