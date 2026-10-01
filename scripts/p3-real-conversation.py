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
# Preparation and copied installer imports must not mutate the deployment bundle.
sys.dont_write_bytecode=True
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
from jasmine_core.adapter.profile import configuration, argv
from jasmine_core.capture.p2_profile import environment


def bundle_sha(code):
    return hashlib.sha256(json.dumps({str(p.relative_to(code)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(code.rglob('*')) if p.is_file()},sort_keys=True).encode()).hexdigest()

def _fingerprints():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src/jasmine_core/adapter').glob('*.py'))}

def prepare(args):
    from jasmine_core.context.tokens import Tokenizer,ASSET_CACHE_NAME,ASSET_SHA256
    tokenizer=Tokenizer(cache_dir=args.tokenizer_cache.resolve(strict=True))
    out=args.out.resolve()
    if out.exists(): raise ValueError('--out must be a fresh directory')
    if ROOT==out or ROOT in out.parents: raise ValueError('Gate fixture must be outside trusted code tree')
    out.mkdir(mode=0o700,parents=True)
    runtime=out/'runtime';fixture=out/'project'
    runtime.mkdir(mode=0o700);fixture.mkdir(mode=0o700);(fixture/'work').mkdir()
    cache=runtime/'tokenizer-cache';cache.mkdir(mode=0o700)
    shutil.copy2(args.tokenizer_cache.resolve(strict=True)/ASSET_CACHE_NAME,cache/ASSET_CACHE_NAME)
    (cache/ASSET_CACHE_NAME).chmod(0o600)
    if hashlib.sha256((cache/ASSET_CACHE_NAME).read_bytes()).hexdigest()!=ASSET_SHA256:raise ValueError('copied tokenizer asset mismatch')
    (fixture/'.codex').mkdir(mode=0o700)
    skill=fixture/'.agents/skills/jasmine-playwright';skill.mkdir(parents=True)
    shutil.copy2(ROOT/'fixtures/p3-native/skill/SKILL.md',skill/'SKILL.md')
    (fixture/'work/artifacts').mkdir()
    (fixture/'work/callback.js').write_text('function callbackTaskId() { return \"wrong-task\"; }\n')
    (fixture/'work/index.html').write_text('<!doctype html><title>Jasmine callback fixture</title><div id=\"status\">ready</div><div id=\"task-id\"></div><script src=\"callback.js\"></script><script>document.getElementById(\"task-id\").textContent=callbackTaskId()</script>')
    (fixture/'.gitignore').write_text('work/artifacts/\n.codex/\n')
    (fixture/'README.md').write_text('Isolated Jasmine P3 native typed execution fixture.\n')
    subprocess.run(['git','init','-q',str(fixture)],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture),'add','README.md','.gitignore','.agents','work/callback.js','work/index.html'],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture),'-c','user.name=Jasmine fixture','-c','user.email=fixture@local.invalid','commit','-qm','fixture'],check=True,timeout=10)
    (fixture/'work/.gitignore').write_text('artifacts/\n')
    subprocess.run(['git','init','-q',str(fixture/'work')],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture/'work'),'add','callback.js','index.html','.gitignore'],check=True,timeout=10)
    subprocess.run(['git','-C',str(fixture/'work'),'-c','user.name=Jasmine fixture','-c','user.email=fixture@local.invalid','commit','-qm','callback fixture'],check=True,timeout=10)
    code=out/'code';code.mkdir(mode=0o700);(code/'scripts').mkdir();(code/'src').mkdir()
    shutil.copytree(ROOT/'src/jasmine_core',code/'src/jasmine_core',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('jasmine-p3-hook.sh','p3-codex-hook-install.py','p3-playwright-worker.cjs','p3-test-worker.cjs','p3-fixture-server.py','p3-memory-fail-worker.py','p2-operator.py','p1-real-conversation.py'):
        shutil.copy2(ROOT/'scripts'/name,code/'scripts'/name)
    catalog=runtime/'catalog.json';catalog.write_bytes(args.catalog.resolve(strict=True).read_bytes());catalog.chmod(0o600)
    executable=Path(args.codex or shutil.which('codex')).resolve(strict=True)
    profile=configuration(executable,catalog,fixture,runtime,code)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));page_port=listener.getsockname()[1]
    while page_port==port:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));page_port=listener.getsockname()[1]
    host=ids.new_id('hst');human=ids.new_id('act');system=ids.new_id('act');agent=ids.new_id('act');operator=ids.new_id('act')
    conn=db.connect(runtime/'core.db')
    try:
        migrate(conn); registry=Registry(conn)
        with db.transaction(conn):
            registry.upsert_host(host)
            for actor,kind in ((human,'human'),(system,'system'),(agent,'agent'),(operator,'system')):
                registry.upsert_actor(actor,kind=kind,home_host_id=host)
        objects=ObjectStore(conn,schema_version=SCHEMA_VERSION)
        project=objects.create(NewObject.project({'name':'P3 actual native continuity fixture','host_id':host}),actor_id=human)['object']['project_id']
        issuer=auth.Auth(conn)
        roles={'capture':(human,['events:write']),
            'processor':(system,['events:read','objects:read','state:read','state:write','authority:read','interpretations:read','interpretations:process','resolutions:read','resolutions:process','reviews:read']),
            'operator':(operator,['events:read','objects:read','objects:write','state:read','state:write','state:accept','authority:read','authority:propose','authority:manage','interpretations:read','interpretations:process','interpretations:manage','resolutions:read','resolutions:process','reviews:read','reviews:manage'])}
        adapter_path=runtime/'adapter.json'
        adapter={'actor_id':operator,'host_id':host,'key_id':ids.new_id('key'),'receipt_root':str(runtime),
            'lease_file':str(runtime/'binding.json.p2-lease'),'work_root':str(fixture/'work'),
            'deployment_root':str(code),'deployment_sha256':bundle_sha(code),'profile_sha256':profile['config_digest'],
            'hook_definition_sha256':'0'*64,'hook_definition_path':str(fixture/'.codex/hooks.json'),
            'executor_inputs':{'jasmine_test':{'work_root':str(fixture/'work'),'artifact_root':str(fixture/'work/artifacts')},
                'jasmine_playwright':{'origin':f'http://127.0.0.1:{page_port}','chrome_path':'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','work_root':str(fixture/'work'),'artifact_root':str(fixture/'work/artifacts')}},'executors':{
                name:{'argv':[str(args.node.resolve(strict=True)),str(code/'scripts'/script)],'choices':choices,
                    'dependencies':[str(args.node_modules.resolve(strict=True)/'playwright'),str(args.node_modules.resolve(strict=True)/'playwright-core'),'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if name=='jasmine_playwright' else [],'environment':{'PATH':'/usr/bin:/bin','NODE_PATH':str(args.node_modules.resolve(strict=True))}}
                for name,script,choices in [('jasmine_test','p3-test-worker.cjs',['callback']),('jasmine_playwright','p3-playwright-worker.cjs',['smoke','callback_task_id'])]}}
        atomic_json(adapter_path,adapter)
        old_adapter_env=os.environ.get('JASMINE_CORE_ADAPTER_CONFIG')
        old_workspace_env=os.environ.get('JASMINE_CORE_WORKSPACE_ROOT')
        os.environ['JASMINE_CORE_WORKSPACE_ROOT']=str(fixture/'work')
        os.environ['JASMINE_CORE_ADAPTER_CONFIG']=str(adapter_path)
        roles['operator'][1].extend(['adapter:report','adapter:attest','adapter:read','guard:check','evidence:read','evidence:write','context:read','context:build','checkpoint:read','checkpoint:write','resume:read','resume:build'])
        for name,(actor,scopes) in roles.items():
            issued=issuer.issue_key(actor_id=actor,label='p3-fixture-'+name,scopes=scopes)
            token=issued['token']
            if name=='operator':adapter['key_id']=issued['key_id'];atomic_json(adapter_path,adapter)
            path=runtime/(name+'.token');path.write_text(token+'\n');path.chmod(0o600)
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM steps').fetchone()[0]==0
    finally:
        conn.close()
        if 'old_adapter_env' in locals():
            if old_adapter_env is None:os.environ.pop('JASMINE_CORE_ADAPTER_CONFIG',None)
            else:os.environ['JASMINE_CORE_ADAPTER_CONFIG']=old_adapter_env
            if old_workspace_env is None:os.environ.pop('JASMINE_CORE_WORKSPACE_ROOT',None)
            else:os.environ['JASMINE_CORE_WORKSPACE_ROOT']=old_workspace_env
    binding={'mode':2,'run_nonce':secrets.token_hex(24),'project_id':project,'host_id':host,
        'core_url':f'http://127.0.0.1:{port}','token_file':str(runtime/'processor.token'),
        'human_token_file':str(runtime/'capture.token'),'trace_file':str(runtime/'hook.jsonl'),
        'p3_adapter_config_file':str(runtime/'adapter.json'),'p3_operator_token_file':str(runtime/'operator.token'),'p3_core_session_id':None}
    atomic_json(runtime/'binding.json',binding)
    installer=subprocess.run([sys.executable,'-B',str(code/'scripts/p3-codex-hook-install.py'),
        '--project-root',str(fixture),'--binding',str(runtime/'binding.json'),'--python',sys.executable,'--write'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=10,check=True)
    installation=json.loads(installer.stdout)
    atomic_json(runtime/'hook-diff.json',installation)
    adapter['hook_definition_sha256']=installation['new_sha256'];atomic_json(runtime/'adapter.json',adapter)
    from jasmine_core.canonical import canonical_json,sha256_hex
    producers=[{'kind':'TEST','tool_name':name,'command_sha256':sha256_hex(canonical_json(value['argv']+[choice]))}
        for name,value in adapter['executors'].items() for choice in value['choices']]
    atomic_json(runtime/'evidence-producers.json',producers)
    atomic_json(runtime/'memory-failed.json',{'argv':[sys.executable,str(code/'scripts/p3-memory-fail-worker.py')],
        'provider_id':'p3-owned-failed-memory','global_bank':False,'environment':{}})
    if any(p.name=='__pycache__' or p.suffix=='.pyc' for p in code.rglob('*')):
        raise ValueError('deployment must contain source assets without generated bytecode')
    if bundle_sha(code)!=adapter['deployment_sha256']:
        raise ValueError('deployment changed during preparation')
    metadata={'schema':'jasmine.p3.native-gate.v1','profile':profile,'binding_path':str(runtime/'binding.json'),
        'fixture':str(fixture),'runtime':str(runtime),'host_id':host,'project_id':project,'system_actor_id':system,
        'operator_actor_id':operator,'port':port,'page_port':page_port,'hook_sha256':installation['new_sha256'],
        'source_fingerprints':_fingerprints(), 'deployment_fingerprints':{str(p.relative_to(code)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(code.rglob('*')) if p.is_file()},'commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        'skill_path':str(skill/'SKILL.md'),'skill_sha256':hashlib.sha256((skill/'SKILL.md').read_bytes()).hexdigest(),
        'memory_failure_config_sha256':hashlib.sha256((runtime/'memory-failed.json').read_bytes()).hexdigest(),
        'tokenizer':tokenizer.identity,'tokenizer_cache':str(cache),'python_executable':sys.executable,
        'python_sha256':hashlib.sha256(Path(sys.executable).resolve(strict=True).read_bytes()).hexdigest(),
        'prepared_zero_tasks':True,'prepared_zero_steps':True,'trust_verified':False}
    atomic_json(runtime/'manifest.json',metadata)
    trust_env=environment('');trust_env.pop('JASMINE_CORE_GATE_NONCE',None)
    trust_command=shlex.join(['env','-i']+[key+'='+value for key,value in trust_env.items()]+argv(profile,interactive=True))
    (runtime/'normal-trust-command.txt').write_text(trust_command+'\n')
    # The output has paths/hashes only. Operator credential path is not emitted.
    print(json.dumps({'status':'PREPARED_NOT_VERIFIED','fixture':str(fixture),
        'manifest':str(runtime/'manifest.json'),'hook_diff':str(runtime/'hook-diff.json'),
        'hook_sha256':metadata['hook_sha256'],'profile_digest':profile['config_digest'],'normal_trust_command':str(runtime/'normal-trust-command.txt'),
        'required_user_action':'After exact artifact review and matching boundary proof: launch normal-trust-command.txt in interactive CLI, approve this fixture project and review exact /hooks definitions. No runner changes trust.'},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description='Prepare concrete native P3 fixture; no trust or Gate execution.')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--codex')
    parser.add_argument('--tokenizer-cache',type=Path,required=True)
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--node-modules',type=Path,required=True)
    args=parser.parse_args();prepare(args)
if __name__=='__main__':main()
