"""Trusted dynamic callback barrier; native receipt collection is separate."""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from ..canonical import canonical_json, sha256_hex
from ..capture.p2_runtime import Deadline, atomic_json
from ..continuity_scan import scan_workspace
from .config import private_json, RECEIPT_ID
from .executors import safe_read, safe_patch, run_fixed_worker
from .protocol import input_fingerprint_sha256

TOOLS=('jasmine_read','jasmine_patch','jasmine_test','jasmine_playwright')

class DynamicExecutor:
    def __init__(self,client,lease,config,*,worker_inputs,allowed_paths):
        self.client,self.lease,self.config=client,lease,config
        if worker_inputs!=config.value['executor_inputs']:raise ValueError('fixed worker stdin configuration changed')
        self.worker_inputs=config.value['executor_inputs']
        self.allowed_paths=tuple(allowed_paths)

    def receipt(self,name,value):
        if not isinstance(name,str) or not RECEIPT_ID.fullmatch(name):
            raise ValueError('safe private receipt identifier required')
        path=Path(self.config.value['receipt_root'])/(name+'.json')
        if path.exists():
            original,digest=private_json(path)
            comparable=lambda x:{k:v for k,v in x.items() if k!='received_at_monotonic'}
            if comparable(original)!=comparable(value):raise ValueError('immutable receipt conflict')
            return digest
        atomic_json(path,value)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def execute(self,params,*,received_at_monotonic):
        """Caller supplies an actual item/tool/call, never assistant prose.

        The same protected lease lock is held from admission through completion.
        Exact request and effect-start records survive a lost API response. An
        already-started call is never executed again, even without a terminal.
        """
        if set(params)-{'threadId','turnId','callId','tool','arguments','namespace'} or params.get('tool') not in TOOLS or params.get('namespace') not in (None,''):
            raise ValueError('unsupported native dynamic call')
        if any(not isinstance(params.get(k),str) or not params[k] or len(params[k])>128 for k in ('threadId','turnId','callId')) or not RECEIPT_ID.fullmatch(params['callId']) or not isinstance(params.get('arguments'),dict):
            raise ValueError('strict native call identity and arguments required')
        deadline=Deadline(end=received_at_monotonic+90)
        self.client.deadline=deadline
        self.lease.deadline=deadline
        with self.lease.locked():
            state=self.lease.read()
            if state.get('session_id')!=params['threadId'] or state.get('turn_id')!=params['turnId'] or state.get('phase')!='READY':
                raise ValueError('native call is not the admitted current turn')
            receipt={'kind':'dynamic_call','request':dict(params),'received_at_monotonic':received_at_monotonic}
            receipt['request'].pop('namespace',None)
            self.receipt(params['callId'],receipt)
            pack=self.client.get('/v1/context/'+state['context_pack_id'])['context']
            body={'idempotency_key':'reserve-'+params['callId'],'native_thread_id':params['threadId'],
                'native_turn_id':params['turnId'],'native_call_id':params['callId'],'tool':params['tool'],
                'args':params['arguments'],'task_id':state['task_id'],'step_id':state['step_id'],
                'source_event_id':state['event_id'],'host_id':state['host_id'],'session_id':state.get('core_session_id'),
                'context_pack_id':state['context_pack_id'],'lease_generation':state['generation'],
                'expected_task_revision':next(x['revision'] for x in pack['projection']['objects'] if x['object_id']==state['task_id']),
                'expected_step_revision':next(x['revision'] for x in pack['projection']['objects'] if x['object_id']==state['step_id'])}
            state['dynamic_request']=body;self.lease.write(state)
            reply=self.client.post('/v1/adapter/operations/reserve',body,cap=10)
            reservation=reply['operation'];operation=reservation['operation_event_id']
            if reservation['status']!='RESERVED':return reply
            started_path=Path(self.config.value['receipt_root'])/('effect_'+operation+'.json')
            if started_path.exists():
                return self.client.get('/v1/adapter/operations/'+operation)
            self.client.post('/v1/adapter/operations/'+operation+'/check-current',{k:body[k] for k in ('host_id','native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id')},cap=3)
            actual=scan_workspace(deadline,root=Path(self.config.value['work_root']))['snapshot']
            if not actual.get('complete') or input_fingerprint_sha256(actual)!=reservation['input_fingerprint_sha256']:
                raise ValueError('actual execution input changed before effect')
            deadline.remaining()
            started=time.monotonic()
            common={k:body[k] for k in ('native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id')}
            common.update(operation_event_id=operation,args_sha256=sha256_hex(body['args']),
                executor_manifest_sha256=reservation['executor_manifest_sha256'])
            self.receipt('effect_'+operation,{'kind':'effect_started',**common,'started_at_monotonic':started})
            tool=body['tool'];args=body['args'];root=Path(self.config.value['work_root'])
            output='';exit_code=None;cleanup='incomplete_or_denied';failure_code=None
            try:
                if tool=='jasmine_read':
                    output=canonical_json(safe_read(root,args['path'],deadline,self.allowed_paths));exit_code=0;cleanup='complete'
                elif tool=='jasmine_patch':
                    output=canonical_json(safe_patch(root,args['path'],args['expected_sha256'],args['new_content'],deadline,self.allowed_paths));exit_code=0;cleanup='complete'
                else:
                    choice=args['suite' if tool=='jasmine_test' else 'scenario']
                    result=run_fixed_worker(self.config.value['executors'][tool],choice,self.worker_inputs[tool],deadline,30 if tool=='jasmine_test' else 60)
                    output=result['output'];exit_code=result['exit_code'];cleanup=result['cleanup']
                    failure_code=result.get('failure')
                artifact_uri=artifact_sha=None
                if tool in ('jasmine_playwright','jasmine_test') and exit_code==0:
                    parsed=json.loads(output);artifact_uri=parsed['artifact_uri'];artifact_sha=parsed['artifact_sha256']
                    from ..fingerprint import hash_workspace_file
                    if not isinstance(artifact_uri,str) or not artifact_uri.startswith('workspace:/'):
                        raise ValueError('workspace artifact required')
                    digest,size=hash_workspace_file(root,artifact_uri.removeprefix('workspace:/'),16777216)
                    if digest!=artifact_sha or size!=parsed['artifact_bytes']:
                        raise ValueError('actual artifact bytes do not match worker output')
                status='SUCCEEDED' if exit_code==0 and cleanup=='complete' and failure_code is None else 'FAILED'
                if failure_code=='executor_timeout':status='INTERRUPTED'
            except Exception as error:
                from .executors import ExecutorFailure
                failure_code=error.code if isinstance(error,ExecutorFailure) else 'executor_output_or_internal_error'
                if isinstance(error,ExecutorFailure):cleanup=error.details.get('cleanup','incomplete_or_denied')
                artifact_uri=artifact_sha=None;status='UNKNOWN_OUTCOME'
            if failure_code is not None:
                try:self.receipt('error_'+operation,{'kind':'effect_failure','operation_event_id':operation,
                    'code':failure_code,'cleanup':cleanup,'exit_code':exit_code})
                except Exception:pass
            worker={'kind':'worker_result',**common,'started_at_monotonic':started,'finished_at_monotonic':time.monotonic(),
                'status':status,'exit_code':exit_code,'cleanup':cleanup,'artifact_uri':artifact_uri,'artifact_sha256':artifact_sha,
                'output':output,'output_sha256':sha256_hex(output),'input_fingerprint_sha256':input_fingerprint_sha256(actual)}
            rid='worker_'+operation;digest=self.receipt(rid,worker)
            completion={'idempotency_key':'complete-'+params['callId'],'reservation_event_id':operation,
                'receipt_id':rid,'receipt_sha256':digest}
            state['dynamic_completion_request']=completion;self.lease.write(state)
            result=self.client.post('/v1/adapter/operations/'+operation+'/complete',completion,cap=10)
            result['tool_output']=output if failure_code is None else canonical_json({'failure_code':failure_code,'output':output[:65536],'cleanup':cleanup})
            return result


class NativeRunner:
    """One persistent native process, with raw bidirectional receipt collection.

    Requests are native protocol messages; only actual item/tool/call requests
    enter DynamicExecutor. Compact ACKs and assistant messages prove no lifecycle.
    """
    def __init__(self,native,executor,*,receipt_sink=None):
        self.native,self.executor=native,executor
        self.receipt_sink=receipt_sink
        self.notifications=[]
        self.thread_id=None
        self.turn_count=0
        self.turn_deadline=None

    def initialize(self,budget):
        ident=self.native.send_request('initialize',{'clientInfo':{'name':'jasmine-p3-adapter','version':'1'},
            'capabilities':{'experimentalApi':True}},budget)
        result=self.native.wait_response(ident,budget)
        self.native.send_notification('initialized',{},budget)
        return result

    @staticmethod
    def tool_definitions():
        def tool(name,properties,required):
            return {'type':'function','name':name,'description':'Trusted Jasmine typed '+name+' operation.',
                'inputSchema':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}
        string={'type':'string'}
        return [tool('jasmine_read',{'path':string},['path']),
            tool('jasmine_patch',{'path':string,'expected_sha256':string,'new_content':string},['path','expected_sha256','new_content']),
            tool('jasmine_test',{'suite':{'type':'string','enum':['callback']}},['suite']),
            tool('jasmine_playwright',{'scenario':{'type':'string','enum':['smoke','callback_task_id']}},['scenario'])]

    def start(self,budget,*,cwd,model='gpt-6.1-sol'):
        ident=self.native.send_request('thread/start',{'model':model,'cwd':cwd,
            'approvalPolicy':'never','dynamicTools':self.tool_definitions()},budget)
        result=self.native.wait_response(ident,budget)
        self.thread_id=result['thread']['id']
        return result

    def submit(self,text,budget,*,skill_path=None):
        if not self.thread_id or not isinstance(text,str) or not text:
            raise ValueError('genuine nonempty user input and native thread required')
        if self.turn_count>=16:raise ValueError('native turn limit exceeded')
        self.turn_count+=1
        self.turn_deadline=Deadline(end=min(budget.end,time.monotonic()+140)) if budget is not None else None
        budget=self.turn_deadline if self.turn_deadline is not None else budget
        inputs=[{'type':'text','text':text,'text_elements':[]}]
        if skill_path is not None:
            inputs.append({'type':'skill','name':'jasmine-playwright','path':str(skill_path)})
        self.last_turn_request={'threadId':self.thread_id,'input':inputs,'collaborationMode':{'mode':'default','settings':{'model':'gpt-6.1-sol','reasoning_effort':'low','developer_instructions':None}}}
        ident=self.native.send_request('turn/start',self.last_turn_request,budget)
        result=self.native.wait_response(ident,budget)
        self.last_turn_id=result['turn']['id']
        return result

    def compact(self,budget):
        ident=self.native.send_request('thread/compact/start',{'threadId':self.thread_id},budget)
        # A successful empty response is only an ACK. drive() collects real items.
        return self.native.wait_response(ident,budget)

    def drive(self,budget,*,until):
        if self.turn_deadline is not None:budget=Deadline(end=min(budget.end,self.turn_deadline.end))
        while True:
            message=self.native.next_message(budget)
            received=self.native.pop_received_at(message)
            if self.receipt_sink is not None:self.receipt_sink(message,received)
            if 'method' in message and 'id' not in message:
                self.notifications.append(message)
            elif 'method' in message and 'id' in message:
                if message['method']!='item/tool/call':
                    self.native.respond(message['id'],{'contentItems':[{'type':'inputText','text':'unsupported native request'}],'success':False},budget)
                    raise ValueError('undeclared native execution surface')
                try:
                    result=self.executor.execute(message['params'],received_at_monotonic=received)
                    terminal=result.get('completion',{})
                    success=terminal.get('status')=='SUCCEEDED'
                    output=result.get('tool_output','')
                    raw=output.encode('utf-8')
                    view={'status':terminal.get('status','BLOCKED'),'operation_event_id':terminal.get('reservation_event_id'),
                        'completion_event_id':terminal.get('completion_event_id'),
                        'evidence_id':(terminal.get('evidence') or {}).get('evidence_id'),
                        'output':raw[:65536].decode('utf-8','ignore'),'original_output_bytes':len(raw),
                        'original_output_sha256':sha256_hex(output),'truncated':len(raw)>65536}
                    content=canonical_json(view)
                except Exception:
                    success=False;content='Jasmine typed operation blocked; no completion claim.'
                self.native.respond(message['id'],{'contentItems':[{'type':'inputText','text':content}],'success':success},budget)
            if until(message):return message


def attest_completed_lifecycle(client,config,notifications,*,deadline):
    """Post-collect actual notifications; never invent completion in a hook."""
    results=[]
    for path in sorted(Path(config.value['receipt_root']).glob('callback_*_checkpoint.json')):
        deadline.remaining()
        relation,_=private_json(path)
        if set(relation)!={'kind','callback_id','reported_event_id','checkpoint_id'} or relation['kind']!='lifecycle_checkpoint':
            raise ValueError('strict checkpoint relation required')
        callback,callback_sha=config.receipt(relation['callback_id'])
        report=callback['report']
        if report['hook_event_name'] not in ('PreCompact','Stop'):continue
        relevant=[]
        for notification in notifications:
            if notification.get('method') not in ('hook/started','hook/completed','item/started','item/completed','turn/completed'):
                continue
            params=notification.get('params')
            if not isinstance(params,dict) or params.get('threadId')!=report['native_thread_id']:
                continue
            direct=params.get('turnId')
            turn=params.get('turn')
            nested=turn.get('id') if isinstance(turn,dict) else None
            # Native turn/completed carries params.turn.id. Never attribute a
            # missing identity to the report, or accept contradictory identities.
            if direct is not None and nested is not None and direct!=nested:
                continue
            actual_turn=direct if direct is not None else nested
            if not isinstance(actual_turn,str) or not actual_turn or actual_turn!=report['native_turn_id']:
                continue
            relevant.append(notification)
        if len(relevant)>512:raise ValueError('native lifecycle notification cap')
        if not relevant:continue
        receipt={'kind':'lifecycle_native','callback_id':relation['callback_id'],'callback_receipt_sha256':callback_sha,
            **{k:config.value[k] for k in ('hook_definition_sha256','profile_sha256','deployment_sha256')},'notifications':relevant}
        rid='attest_'+relation['callback_id']
        target=Path(config.value['receipt_root'])/(rid+'.json')
        if target.exists():
            original,digest=private_json(target)
            if original!=receipt:raise ValueError('immutable native attestation receipt conflict')
        else:
            atomic_json(target,receipt);digest=hashlib.sha256(target.read_bytes()).hexdigest()
        reply=client.post('/v1/adapter/lifecycle/attest',{'idempotency_key':rid,
            'reported_event_id':relation['reported_event_id'],'checkpoint_id':relation['checkpoint_id'],
            'receipt_id':rid,'receipt_sha256':digest},cap=deadline.remaining(3))
        results.append(reply)
    return results


def seed_new_native_binding(runner,client,lease,config,*,previous_task_id,previous_step_id,thread_start_result):
    """Explicit trusted G05 bridge. No old Raw/READY or Core session is reused."""
    if runner.thread_id!=thread_start_result['thread']['id']:
        raise ValueError('binding seed requires the actual new thread/start response')
    task=client.get('/v1/tasks/'+previous_task_id)['task']
    step=client.get('/v1/steps/'+previous_step_id)['step']
    if task['project_id']!=lease.read()['project_id'] or step['task_id']!=task['task_id'] or task['status']!='ACTIVE':
        raise ValueError('current same-project Task/Step binding required')
    with lease.locked():
        previous=lease.read()
        if previous['task_id']!=task['task_id'] or previous['step_id']!=step['step_id']:
            raise ValueError('trusted binding changed')
        reread_task=client.get('/v1/tasks/'+previous_task_id)['task']
        reread_step=client.get('/v1/steps/'+previous_step_id)['step']
        if task!=reread_task or step!=reread_step:raise ValueError('binding read revision changed')
        raw=lease.path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        archive=Path(config.value['receipt_root'])/('priorlease_'+digest+'.json')
        if archive.exists():
            if archive.read_bytes()!=raw:raise ValueError('private lease archive conflict')
        else:
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        seed={'phase':'BINDING_ONLY','session_id':runner.thread_id,'turn_id':None,'generation':'0',
            'task_id':task['task_id'],'step_id':step['step_id'],'project_id':task['project_id'],
            'host_id':config.value['host_id'],'core_session_id':None}
        receipt={'kind':'trusted_binding_seed','previous_lease_sha256':digest,
            'task_read_sha256':sha256_hex(task),'step_read_sha256':sha256_hex(step),
            'task_revision':task['revision'],'step_revision':step['revision'],
            'native_thread_start_response':thread_start_result,'seed':seed}
        atomic_json(Path(config.value['receipt_root'])/('seed_'+hashlib.sha256(runner.thread_id.encode()).hexdigest()+'.json'),receipt)
        lease.write(seed)
    return seed
