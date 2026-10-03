"""Run one genuine native turn against a reviewed isolated prepared deployment.

This runner reports raw capture/admission states. It does not label one turn as
all seven phase Gates, and never changes project or hook trust.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from .config import AdapterConfig,private_json
from .profile import argv
from .native_client import NativeClient
from .http_client import DeadlineClient
from .orchestrator import NativeRunner,DynamicExecutor
from ..capture.p2_runtime import Deadline,Lease,atomic_json
from ..capture.p2_profile import environment
from ..api.client import CoreClient

MAX_TURN_REPORT_BYTES=16*1024*1024


def report_atomic_json(path,value):
    """Bounded private last-turn report; lease writers retain their 64 KiB cap."""
    path=Path(path)
    if path.name!='last-turn.json' or path.parent!=path.parent.resolve(strict=True) or path.is_symlink():
        raise ValueError('noncanonical private report path')
    metadata=path.parent.stat()
    if metadata.st_uid!=os.getuid() or metadata.st_mode & 0o077:
        raise ValueError('private report parent required')
    # Canonical keys/separators, with non-finite numbers rejected for reports.
    raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),
                    default=str,allow_nan=False)+'\n').encode()
    if len(raw)>MAX_TURN_REPORT_BYTES:raise ValueError('native turn report exceeds cap')
    fd,temporary=tempfile.mkstemp(prefix='.turn-report-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def write_turn_report(runtime,result):
    """Report I/O failure cannot erase the controller outcome or raw receipt."""
    try:
        report_atomic_json(runtime/'last-turn.json',result)
    except Exception as error:
        controller=result.get('controller_result')
        diagnostic={'status':'REPORT_WRITE_FAILED','error_type':type(error).__name__,
            'reason':str(error)[:512],'controller_status':controller.get('status') if isinstance(controller,dict) else None,
            'raw_native_receipt':result.get('raw_native_receipt')}
        result['report_write']=diagnostic
        try:atomic_json(runtime/'last-turn-write-failure.json',diagnostic)
        except Exception as diagnostic_error:
            diagnostic['diagnostic_write_error_type']=type(diagnostic_error).__name__


def run(manifest_path,prompt,*,user_reviewed_trust=False,controller=None,total_timeout=240,max_turns=16):
    if not user_reviewed_trust:raise ValueError('normal user project/exact hook trust is required')
    if type(total_timeout) is not int or not 30<=total_timeout<=1800:raise ValueError('bounded Gate budget required')
    if type(max_turns) is not int or not 1<=max_turns<=24:raise ValueError('bounded native turn limit required')
    manifest,_=private_json(manifest_path);runtime=Path(manifest['runtime']);code=Path(manifest['profile']['code_root'])
    for relative,digest in manifest['deployment_fingerprints'].items():
        if hashlib.sha256((code/relative).read_bytes()).hexdigest()!=digest:raise ValueError('prepared deployment changed')
    if hashlib.sha256((Path(manifest['fixture'])/'.codex/hooks.json').read_bytes()).hexdigest()!=manifest['hook_sha256']:
        raise ValueError('trusted hook definitions changed')
    if hashlib.sha256(Path(manifest['skill_path']).read_bytes()).hexdigest()!=manifest['skill_sha256']:
        raise ValueError('deployed skill changed')
    cache=Path(manifest['tokenizer_cache'])
    binding,_=private_json(Path(manifest['binding_path']));adapter,_=private_json(runtime/'adapter.json')
    config=AdapterConfig(adapter,config_sha256=hashlib.sha256((runtime/'adapter.json').read_bytes()).hexdigest())
    spec=importlib.util.spec_from_file_location('p1_owned_helpers',code/'scripts/p1-real-conversation.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    for port in (manifest['port'],manifest['page_port']):
        if helper._port_owners(port):raise ValueError('prepared port occupied; no existing service touched')
    env=environment(binding['run_nonce']);env.update(PYTHONPATH=str(code/'src'),TIKTOKEN_CACHE_DIR=str(cache),
        PYTHONDONTWRITEBYTECODE='1')
    core_env=dict(env);core_env.update(JASMINE_CORE_WORKSPACE_ROOT=adapter['work_root'],
        JASMINE_CORE_ADAPTER_CONFIG=str(runtime/'adapter.json'),JASMINE_CORE_EVIDENCE_PRODUCERS=str(runtime/'evidence-producers.json'),
        JASMINE_CORE_INTERPRETER_CODEX=manifest['profile']['provider']['executable_path'],
        JASMINE_CORE_INTERPRETER_CATALOG=manifest['profile']['provider']['catalog_path'],
        JASMINE_CORE_RESOLVER_ACTOR_ID=manifest['system_actor_id'],JASMINE_CORE_RESOLVER_HOST_ID=manifest['host_id'])
    processes=[];native=None;deadline=Deadline(total_timeout)
    try:
        for name,commands in [('core',[sys.executable,'-B','-m','jasmine_core.cli','serve','--db',str(runtime/'core.db'),'--host','127.0.0.1','--port',str(manifest['port'])]),
            ('page',[sys.executable,'-B',str(code/'scripts/p3-fixture-server.py'),'--root',adapter['work_root'],'--port',str(manifest['page_port'])])]:
            with (runtime/(name+'.log')).open('ab') as log:
                processes.append(subprocess.Popen(commands,env=core_env if name=='core' else env,cwd=code,stdout=log,stderr=subprocess.STDOUT))
        for attempt in range(80):
            if any(p.poll() is not None for p in processes):raise ValueError('owned service exited')
            try:
                helper._owned_endpoint(binding['core_url'],processes[0].pid)
                if CoreClient(binding['core_url']).get('/v1/health')['status']=='ok':break
            except Exception:
                if attempt==79:raise ValueError('owned Core readiness unavailable')
                time.sleep(.1)
        origin=adapter['executor_inputs']['jasmine_playwright']['origin']
        helper._owned_endpoint(origin,processes[1].pid)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args):return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(origin+'/',timeout=deadline.remaining(1)) as page:
            body=page.read(262145)
        deadline.remaining()
        if len(body)>262144 or body!=(Path(adapter['work_root'])/'index.html').read_bytes():
            raise ValueError('owned page does not serve the exact current fixture')
        helper._owned_endpoint(origin,processes[1].pid)
        raw_path=runtime/('native-'+str(time.time_ns())+'.jsonl')
        def raw_sink(direction,message):
            with raw_path.open('ab') as stream:
                stream.write((json.dumps({'direction':direction,'message':message},ensure_ascii=False)+'\n').encode())
                stream.flush();os.fsync(stream.fileno())
        token=(runtime/'operator.token').read_text().strip()
        lease=Lease(Path(manifest['binding_path']),deadline)
        client=DeadlineClient(binding['core_url'],token,deadline)
        executor=DynamicExecutor(client,lease,config,worker_inputs=adapter['executor_inputs'],allowed_paths=('callback.js','index.html'))
        native=NativeClient(argv(manifest['profile']),env,manifest['fixture'],receipt_sink=raw_sink)
        first_deadline=Deadline(end=min(deadline.end,time.monotonic()+140))
        runner=NativeRunner(native,executor,max_turns=max_turns);runner.initialize(first_deadline);runner.start(first_deadline,cwd=manifest['fixture'])
        response=runner.submit(prompt,first_deadline,skill_path=Path(manifest['fixture'])/'.agents/skills/jasmine-playwright/SKILL.md')
        turn=response['turn']['id']
        runner.drive(first_deadline,until=lambda msg:msg.get('method')=='turn/completed' and msg.get('params',{}).get('threadId')==runner.thread_id and msg.get('params',{}).get('turn',{}).get('id')==turn)
        restart_receipts=[]
        def restart_core(memory_config_path=None):
            replacement=dict(core_env)
            if memory_config_path is None:replacement.pop('JASMINE_CORE_MEMORY_CONFIG',None)
            else:
                allowed=runtime/'memory-failed.json'
                if Path(memory_config_path)!=allowed:raise ValueError('only reviewed failed Memory deployment allowed')
                _,digest=private_json(allowed)
                if digest!=manifest['memory_failure_config_sha256']:raise ValueError('Memory fixture config changed')
                replacement['JASMINE_CORE_MEMORY_CONFIG']=str(allowed)
            previous_pid=processes[0].pid
            helper._stop_core(processes[0],manifest['port'])
            if helper._port_owners(manifest['port']):raise ValueError('owned restart port occupied')
            with (runtime/'core.log').open('ab') as log:
                processes[0]=subprocess.Popen([sys.executable,'-B','-m','jasmine_core.cli','serve','--db',str(runtime/'core.db'),'--host','127.0.0.1','--port',str(manifest['port'])],env=replacement,cwd=code,stdout=log,stderr=subprocess.STDOUT)
            for attempt in range(80):
                deadline.remaining()
                if processes[0].poll() is not None:raise ValueError('owned Core restart exited')
                try:
                    helper._owned_endpoint(binding['core_url'],processes[0].pid)
                    if CoreClient(binding['core_url']).get('/v1/health')['status']=='ok':break
                except Exception:
                    if attempt==79:raise ValueError('owned restart readiness unavailable')
                    time.sleep(.1)
            receipt={'previous_pid':previous_pid,'current_pid':processes[0].pid,'port':manifest['port'],
                'memory_mode':'failed_fixture' if memory_config_path is not None else 'not_configured'}
            restart_receipts.append(receipt);atomic_json(runtime/'owned-restarts.json',{'restarts':restart_receipts})
            return receipt
        controller_result=None
        if controller is not None:
            controller_result=controller({'runner':runner,'client':client,'lease':lease,'config':config,
                'manifest':manifest,'deadline':deadline,'first_turn_id':turn,'restart_core':restart_core})
        client.deadline=deadline;lease.deadline=deadline
        from .orchestrator import attest_completed_lifecycle
        lifecycle=attest_completed_lifecycle(client,config,runner.notifications,deadline=deadline)
        state=lease.read()
        proof={'context':False,'skill':False,'reason':'source_not_ready'}
        if state and state.get('phase')=='READY' and runner.thread_id==state.get('session_id') and runner.last_turn_id==state.get('turn_id'):
            from .native_proof import read_thread_rollout,typed_input,skill_input
            records,rollout=read_thread_rollout(runner.thread_id,deadline)
            context=client.get('/v1/context/'+state['context_pack_id'])['context']
            skill=(Path(manifest['fixture'])/'.agents/skills/jasmine-playwright/SKILL.md').read_text()
            proof_turn=state['turn_id']
            proof={'context':typed_input(records,proof_turn,context['rendered_content']),
                'skill':skill_input(records,proof_turn,skill,native_request=runner.last_turn_request,skill_name='jasmine-playwright',skill_path=manifest['skill_path']),'rollout_path':str(rollout),'reason':'typed_current_turn_only'}
        result={'scope':'ONE_NATIVE_TURN_NOT_PHASE_GATE','typed_input_proof':proof,'lifecycle_attestations':lifecycle,'controller_result':controller_result,'thread_id':runner.thread_id,'turn_id':turn,
            'raw_native_receipt':str(raw_path),'lease':lease.read(),'native_notifications':runner.notifications}
        write_turn_report(runtime,result)
        return result
    finally:
        cleanup={'native':None,'services':[]}
        if native is not None:
            try:cleanup['native']=native.close(Deadline(2))
            except Exception as error:cleanup['native']={'status':'incomplete_or_denied','error_type':type(error).__name__}
        for index,proc in enumerate(processes):
            try:
                helper._stop_core(proc,manifest['port'] if index==0 else manifest['page_port'])
                cleanup['services'].append({'pid':proc.pid,'status':'complete'})
            except Exception as error:
                cleanup['services'].append({'pid':proc.pid,'status':'incomplete_or_denied','error_type':type(error).__name__})
        try:atomic_json(runtime/'last-cleanup.json',cleanup)
        except Exception:pass
        if 'result' in locals():result['cleanup']=cleanup


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--prompt',required=True);parser.add_argument('--user-reviewed-trust',action='store_true')
    args=parser.parse_args();result=run(args.manifest,args.prompt,user_reviewed_trust=args.user_reviewed_trust)
    print(json.dumps({k:v for k,v in result.items() if k not in ('lease','native_notifications')},ensure_ascii=False))
if __name__=='__main__':main()
