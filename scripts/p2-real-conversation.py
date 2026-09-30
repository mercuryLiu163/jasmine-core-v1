#!/usr/bin/env python3
"""Prepare/run actual admission-only Codex capture. No trust mutation or CI gate.

Component boundary evidence and real hook/LLM evidence are separate. Prepare
creates zero Tasks/Steps and concrete hook definitions, not a capture PASS.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from jasmine_core import SCHEMA_VERSION, auth, db, ids
from jasmine_core.registry import Registry
from jasmine_core.objects import ObjectStore
from jasmine_core.models import NewObject
from jasmine_core.migrations import migrate
from jasmine_core.api.client import CoreClient
from jasmine_core.capture.p1_codex_hook import _private_file
from jasmine_core.capture.p2_runtime import atomic_json
from jasmine_core.capture.p2_profile import configuration, argv, environment, trust_argv

PROMPTS = [
    '请创建一个测试本项目的任务，测试必须使用Playwright skill。不要调用任何工具，只报告收到的Jasmine当前状态。',
    '我不是让你用curl，我说使用Playwright skill。不要调用任何工具，只报告收到的Jasmine当前状态。',
    '考虑研究是否采用另一个测试方案，这还不是决定。不要调用任何工具。',
]

class Blocked(Exception): pass


def _load(path): return json.loads(_private_file(path))
def _fingerprints():
    paths = [ROOT/'scripts/jasmine-p2-hook.sh'] + sorted((ROOT/'src/jasmine_core/capture').glob('p2_*.py'))
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def injection_in_current_turn(records, turn_id, text):
    """Only typed input messages in the exact current turn prove injection.

    Assistant quotations and historical resume messages are never evidence.
    Missing typed turn association is intentionally BLOCKED.
    """
    current = None
    for row in records:
        payload = row.get('payload', {})
        if row.get('type') == 'turn_context':
            current = payload.get('turn_id')
        explicit = row.get('turn_id') or payload.get('turn_id') or current
        if explicit != turn_id or row.get('type') != 'response_item':
            continue
        if payload.get('type') != 'message' or payload.get('role') not in ('user', 'developer'):
            continue
        for content in payload.get('content', []):
            if content.get('type') == 'input_text' and text in content.get('text', ''):
                return True
    return False


def prepare(args):
    out=args.out.resolve()
    if out.exists(): raise ValueError('--out must be a fresh directory')
    if ROOT==out or ROOT in out.parents: raise ValueError('Gate fixture must be outside trusted code tree')
    out.mkdir(mode=0o700,parents=True)
    runtime=out/'runtime';fixture=out/'project'
    runtime.mkdir(mode=0o700);fixture.mkdir(mode=0o700);(fixture/'work').mkdir()
    (fixture/'.codex').mkdir(mode=0o700)
    (fixture/'README.md').write_text('Isolated Jasmine P2 admission-only fixture. No execution tools.\n')
    subprocess.run(['git','init','-q',str(fixture)],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture),'add','README.md'],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture),'-c','user.name=Jasmine fixture','-c','user.email=fixture@local.invalid','commit','-qm','fixture'],check=True,timeout=10)
    code=out/'code';code.mkdir(mode=0o700);(code/'scripts').mkdir();(code/'src').mkdir()
    shutil.copytree(ROOT/'src/jasmine_core',code/'src/jasmine_core',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('jasmine-p2-hook.sh','p2-codex-hook-install.py','p2-operator.py','p1-real-conversation.py'):
        shutil.copy2(ROOT/'scripts'/name,code/'scripts'/name)
    catalog=runtime/'catalog.json';catalog.write_bytes(args.catalog.resolve(strict=True).read_bytes());catalog.chmod(0o600)
    executable=Path(args.codex or shutil.which('codex')).resolve(strict=True)
    profile=configuration(executable,catalog,fixture,runtime,code)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    host=ids.new_id('hst');human=ids.new_id('act');system=ids.new_id('act');agent=ids.new_id('act');operator=ids.new_id('act')
    conn=db.connect(runtime/'core.db')
    try:
        migrate(conn); registry=Registry(conn)
        with db.transaction(conn):
            registry.upsert_host(host)
            for actor,kind in ((human,'human'),(system,'system'),(agent,'agent'),(operator,'system')):
                registry.upsert_actor(actor,kind=kind,home_host_id=host)
        objects=ObjectStore(conn,schema_version=SCHEMA_VERSION)
        project=objects.create(NewObject.project({'name':'P2 actual admission-only fixture','host_id':host}),actor_id=human)['object']['project_id']
        issuer=auth.Auth(conn)
        roles={'capture':(human,['events:write']),
            'processor':(system,['events:read','objects:read','state:read','state:write','authority:read','interpretations:read','interpretations:process','resolutions:read','resolutions:process','reviews:read']),
            'operator':(operator,['events:read','objects:read','objects:write','state:read','state:write','state:accept','authority:read','authority:propose','authority:manage','interpretations:read','interpretations:process','interpretations:manage','resolutions:read','resolutions:process','reviews:read','reviews:manage'])}
        for name,(actor,scopes) in roles.items():
            token=issuer.issue_key(actor_id=actor,label='p2-fixture-'+name,scopes=scopes)['token']
            path=runtime/(name+'.token');path.write_text(token+'\n');path.chmod(0o600)
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM steps').fetchone()[0]==0
    finally: conn.close()
    binding={'mode':2,'run_nonce':secrets.token_hex(24),'project_id':project,'host_id':host,
        'core_url':f'http://127.0.0.1:{port}','token_file':str(runtime/'processor.token'),
        'human_token_file':str(runtime/'capture.token'),'trace_file':str(runtime/'hook.jsonl')}
    atomic_json(runtime/'binding.json',binding)
    installer=subprocess.run([sys.executable,str(code/'scripts/p2-codex-hook-install.py'),
        '--project-root',str(fixture),'--binding',str(runtime/'binding.json'),'--python',sys.executable,'--write'],capture_output=True,text=True,timeout=10,check=True)
    installation=json.loads(installer.stdout)
    atomic_json(runtime/'hook-diff.json',installation)
    metadata={'schema':'jasmine.p2.real-gate.v1','profile':profile,'binding_path':str(runtime/'binding.json'),
        'fixture':str(fixture),'runtime':str(runtime),'host_id':host,'project_id':project,'system_actor_id':system,
        'operator_actor_id':operator,'port':port,'hook_sha256':installation['new_sha256'],
        'source_fingerprints':_fingerprints(), 'deployment_fingerprints':{str(p.relative_to(code)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(code.rglob('*')) if p.is_file()},'commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        'prepared_zero_tasks':True,'prepared_zero_steps':True,'trust_verified':False}
    atomic_json(runtime/'manifest.json',metadata)
    trust_env=environment('');trust_env.pop('JASMINE_CORE_GATE_NONCE',None)
    trust_command=shlex.join(['env','-i']+[key+'='+value for key,value in trust_env.items()]+trust_argv(profile))
    (runtime/'normal-trust-command.txt').write_text(trust_command+'\n')
    # The output has paths/hashes only. Operator credential path is not emitted.
    print(json.dumps({'status':'PREPARED_NOT_VERIFIED','fixture':str(fixture),
        'manifest':str(runtime/'manifest.json'),'hook_diff':str(runtime/'hook-diff.json'),
        'hook_sha256':metadata['hook_sha256'],'profile_digest':profile['config_digest'],'normal_trust_command':str(runtime/'normal-trust-command.txt'),
        'required_user_action':'After exact artifact review and matching boundary proof: launch normal-trust-command.txt in interactive CLI, approve this fixture project and review exact /hooks definitions. No runner changes trust.'},ensure_ascii=False))


def _start(manifest):
    runtime=Path(manifest['runtime']);fixture=Path(manifest['fixture']);profile=manifest['profile']
    env=environment('')
    env.pop('JASMINE_CORE_GATE_NONCE',None)
    code=Path(profile['code_root'])
    env.update(PYTHONPATH=str(code/'src'),JASMINE_CORE_WORKSPACE_ROOT=str(fixture),
        JASMINE_CORE_INTERPRETER_CODEX=profile['provider']['executable_path'],
        JASMINE_CORE_INTERPRETER_CATALOG=profile['provider']['catalog_path'],
        JASMINE_CORE_RESOLVER_ACTOR_ID=manifest['system_actor_id'],JASMINE_CORE_RESOLVER_HOST_ID=manifest['host_id'])
    # Own listener confirmation, reused from existing P1 runner. This does not
    # attach to, stop or restart any existing service.
    spec=importlib.util.spec_from_file_location('p1_gate_helpers',code/'scripts/p1-real-conversation.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    if helper._port_owners(manifest['port']): raise Blocked('fixture port occupied; no service touched')
    log=(runtime/'api.log').open('ab')
    proc=subprocess.Popen([sys.executable,'-m','jasmine_core.cli','serve','--db',str(runtime/'core.db'),
        '--host','127.0.0.1','--port',str(manifest['port'])],cwd=code,env=env,stdout=log,stderr=subprocess.STDOUT)
    log.close()
    try:
        for _ in range(80):
            if proc.poll() is not None: raise Blocked('owned Core exited; see private api.log')
            try:
                helper._owned_endpoint(f"http://127.0.0.1:{manifest['port']}",proc.pid)
                if CoreClient(f"http://127.0.0.1:{manifest['port']}").get('/v1/health')['status']=='ok':return proc,helper
            except Exception as error:
                if isinstance(error,Blocked):raise
            time.sleep(.1)
        raise Blocked('owned Core readiness deadline')
    except BaseException:
        helper._stop_core(proc,manifest['port']);raise


def run(args):
    manifest=_load(args.manifest.resolve(strict=True));runtime=Path(manifest['runtime'])
    profile=manifest['profile'];binding=_load(Path(manifest['binding_path']))
    if not args.user_reviewed_trust: raise Blocked('normal-interface project/exact hook trust required')
    if manifest['source_fingerprints']!=_fingerprints():raise Blocked('source changed; prepare/review exact artifact again')
    code=Path(profile['code_root'])
    if any(hashlib.sha256((code/name).read_bytes()).hexdigest()!=hashed for name,hashed in manifest['deployment_fingerprints'].items()):raise Blocked('protected deployment changed')
    hooks=Path(manifest['fixture'])/'.codex/hooks.json'
    if hashlib.sha256(hooks.read_bytes()).hexdigest()!=manifest['hook_sha256']:raise Blocked('hook definition changed')
    if not args.boundary_proof:raise Blocked('independent exact-profile fullsurface/forcedcalls/FS proof required')
    proof=_load(args.boundary_proof.resolve(strict=True))
    if proof.get('status')!='PASS' or proof.get('profile_digest')!=profile['config_digest']:
        raise Blocked('boundary proof does not cover exact profile')
    prompt=args.prompt if args.prompt is not None else PROMPTS[args.turn]
    proc,helper=_start(manifest)
    try:
        native=runtime/('native-'+secrets.token_hex(6)+'.jsonl')
        output=native.with_suffix('.result.txt');stderr=native.with_suffix('.stderr')
        trace_path=runtime/'hook.jsonl'
        trace_offset=trace_path.stat().st_size if trace_path.exists() else 0
        old_lease=_load(runtime/'binding.json.p2-lease') if (runtime/'binding.json.p2-lease').exists() else None
        commands=argv(profile,session=args.session,output=output)
        with native.open('wb') as stdout,stderr.open('wb') as err:
            main=subprocess.Popen(commands,cwd=manifest['fixture'],env=environment(binding['run_nonce']),
                stdin=subprocess.PIPE,stdout=stdout,stderr=err,start_new_session=True)
            try:main.communicate(prompt.encode(),timeout=210)
            except subprocess.TimeoutExpired:
                from jasmine_core.interpreter_provider import _cancel_owned_process
                diagnostics=_cancel_owned_process(main)
                raise Blocked('actual main timeout; cleanup '+str(diagnostics))
        if not (runtime/'hook.jsonl').exists():raise Blocked('no actual hook trace; trust not established by flag')
        with trace_path.open('rb') as trace:
            trace.seek(trace_offset)
            lines=[json.loads(line) for line in trace.read().decode().splitlines()]
        native_records=[json.loads(line) for line in native.read_text().splitlines()]
        threads=[row['thread_id'] for row in native_records if row.get('type')=='thread.started']
        if len(threads)!=1:raise Blocked('actual native unique fresh thread identity unavailable')
        lease=_load(runtime/'binding.json.p2-lease')
        if lease['session_id']!=threads[0] or (old_lease and lease['generation']<=old_lease['generation']):raise Blocked('historical lease cannot prove current capture')
        source=CoreClient(binding['core_url'],_private_file(runtime/'processor.token').decode().strip()).get('/v1/events/'+lease['event_id'])['event']
        if source['payload']['text']!=prompt:raise Blocked('actual source does not match current submitted prompt')
        matching=[line for line in lines if line['session_id']==lease['session_id'] and line['turn_id']==lease['turn_id'] and line['event_id']==source['event_id']]
        if not matching:raise Blocked('current source has no actual hook trace')
        injection_verified=False
        if lease['phase']=='READY':
            # Inspect only this exact native session, never other conversations.
            candidates=list((Path.home()/'.codex/sessions').glob('**/*'+threads[0]+'*.jsonl'))
            for candidate in candidates:
                records=[json.loads(line) for line in candidate.read_text().splitlines()]
                if injection_in_current_turn(records, lease['turn_id'], lease['context_text']):
                    injection_verified=True
            if not injection_verified:raise Blocked('exact additionalContext absent from this native session transcript; assistant prose is not injection proof')
        graph={}
        conn=db.connect(runtime/'core.db')
        try:
            for table in ('events','interpretations','interpretation_results','resolutions','resolution_results','reviews','review_changes','rules','rule_versions','tasks','steps','resolution_source_bindings','interpretation_changes','interpretation_operation_completions'):
                graph[table]=[dict(row) for row in conn.execute('SELECT * FROM '+table)]
        finally:conn.close()
        atomic_json(runtime/('turn-'+lease['event_id']+'.json'),{'source':source,'lease':lease,'trace':matching,'native':str(native),'native_exit':main.returncode,'injection_verified':injection_verified,'profile_digest':profile['config_digest']})
        # Large DB graph is a private standalone receipt, not the small lease.
        graph_path=runtime/('graph-'+lease['event_id']+'.json');graph_path.write_text(json.dumps(graph,ensure_ascii=False,indent=2));graph_path.chmod(0o600)
        print(json.dumps({'status':'CAPTURED_READY' if lease['phase']=='READY' else 'CAPTURED_BLOCKED',
            'phase':lease['phase'],'event_id':source['event_id'],'session_id':lease['session_id'],
            'turn_id':lease['turn_id'],'graph':str(graph_path),'claim':'one actual turn only; complete P2 Gate requires independent graph/restart/operator validation'},ensure_ascii=False))
    finally:helper._stop_core(proc,manifest['port'])


def main():
    os.umask(0o077)  # Only this fixture runner/owned children, never global config.
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',action='store_true');group.add_argument('--run',action='store_true')
    parser.add_argument('--out',type=Path);parser.add_argument('--catalog',type=Path);parser.add_argument('--codex')
    parser.add_argument('--manifest',type=Path);parser.add_argument('--boundary-proof',type=Path)
    parser.add_argument('--user-reviewed-trust',action='store_true');parser.add_argument('--session')
    parser.add_argument('--turn',type=int,choices=range(3),default=0);parser.add_argument('--prompt')
    args=parser.parse_args()
    try:
        if args.prepare:
            if not args.out or not args.catalog:parser.error('prepare requires --out and --catalog')
            prepare(args)
        else:
            if not args.manifest:parser.error('run requires --manifest')
            run(args)
    except (Blocked,OSError,ValueError,subprocess.CalledProcessError) as error:
        print(json.dumps({'status':'BLOCKED','reason':str(error)}));return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
