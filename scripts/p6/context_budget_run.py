"""Bounded real Context budget run. No native injection or failed-build read API claim."""
import argparse
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint_negatives as shared

ROOT = Path(__file__).resolve().parents[2]
SPEC = 'docs/validation/p6-context-budget/v2'
PYTHON = ROOT.parent / 'p6-03-integration/validation-runs/p6/realcore-toolchain/venv/bin/python3.13'
CACHE = ROOT.parent / 'p6-03-integration/validation-runs/p6/realcore-toolchain/tiktoken-cache'
write = shared.write
digest = shared.digest


def serve(args):
    # Private bootstrap keys only; production routes/scanner/tokenizer are untouched.
    shared.READ.append('context:read')
    shared.FULL.extend(['context:read', 'context:build'])
    shared.serve(args)


def rule(client, fixture, severity, text):
    r = client.ok('POST', '/v1/rules/proposals', {'host_id': fixture['host'],
        'rule_key': 'budget-' + os.urandom(8).hex(), 'kind': 'RULE', 'severity': severity,
        'enforcement': 'CONTEXT', 'content': text, 'matcher': {},
        'scope': {'kind': 'task', 'task_id': fixture['task']['task_id'], 'project_id': fixture['project']['project_id']},
        'origin_event_id': fixture['source']['event_id']})['rule']
    return client.ok('POST', '/v1/rules/' + r['rule_id'] + '/approve',
        {'host_id': fixture['host'], 'expected_revision': r['revision']})['rule']


def body(fixture, name):
    return {'task_id': fixture['task']['task_id'], 'host_id': fixture['host'],
            'source_event_id': fixture['source']['event_id'], 'reason': 'MANUAL', 'idempotency_key': name}


def run(args):
    os.umask(0o077)
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    owned = shared.Owned(out)
    private = Path(tempfile.mkdtemp(prefix='p6-context-budget-private-'))
    raw = {'status': 'COLLECTION_PENDING', 'role': 'author-implementation-check', 'cases': [],
           'scope_acceptance_signed': False, 'item6_flag': 0, 'daily_use_released': False,
           'native_injection_claimed': False, 'failed_build_dashboard_receipt': 'UNAVAILABLE_EXISTING_CONTRACT',
           'P4_experiment_restart': False}
    client = None
    frozen_gate = None
    try:
        candidate = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', args.ref], text=True).strip()
        archive_bytes = subprocess.check_output(['git', '-C', str(ROOT), 'archive', '--format=tar', candidate])
        archive = private / 'archive'; archive.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as t: t.extractall(archive, filter='data')
        (out / 'source.tar').write_bytes(archive_bytes)
        for path in ['scripts/p6/context_budget_run.py', 'scripts/p6/context_budget_gate.py', 'scripts/p6/context_budget_browser.mjs']:
            if (ROOT/path).read_bytes() != (archive/path).read_bytes(): raise RuntimeError('launcher/archive drift')
        for path in ['scripts/p6/checkpoint_negatives.py','scripts/p6/checkpoint_negative_gate.py','scripts/p6/context_budget_integrity.py']:
            if (ROOT/path).read_bytes() != (archive/path).read_bytes(): raise RuntimeError('helper/archive drift')
        module_spec=importlib.util.spec_from_file_location('context_budget_frozen_gate',archive/'scripts/p6/context_budget_gate.py')
        frozen_gate=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(frozen_gate)
        matrix = json.loads((archive / SPEC / 'acceptance-matrix.v2.json').read_text())
        write(out / 'matrix.json', matrix)
        raw.update(candidate_commit=candidate, source_tree=subprocess.check_output(['git','-C',str(ROOT),'rev-parse',candidate+'^{tree}'],text=True).strip(), source_archive_sha256=digest(archive_bytes), matrix_sha256=digest((out/'matrix.json').read_bytes()))
        workspace = private / 'workspace'; workspace.mkdir(); (workspace/'input.txt').write_text('private Context budget fixture\n')
        for command in [['git','init','-q'],['git','add','input.txt'],['git','-c','user.name=Private fixture','-c','user.email=fixture@invalid','commit','-qm','Context budget fixture']]: subprocess.run(command,cwd=workspace,check=True)
        cache = private / 'tokenizer-cache'; cache.mkdir()
        asset = 'fb374d419588a4632f3f557e76b4b70aebbca790'; shutil.copyfile(args.cache/asset, cache/asset)
        env = {k:os.environ[k] for k in ('HOME','USER','LANG','TMPDIR') if k in os.environ}
        env.update(PATH='/opt/homebrew/bin:/usr/bin:/bin', PYTHONPATH=str(archive/'src'), PYTHONNOUSERSITE='1',
            PYTHONDONTWRITEBYTECODE='1', TIKTOKEN_CACHE_DIR=str(cache), JASMINE_CORE_WORKSPACE_ROOT=str(workspace), npm_config_cache=str(private/'npm-cache'))
        for label,command in [('npm-ci',['npm','ci','--no-audit','--no-fund']),('build',['npm','run','build'])]:
            with (out/(label+'.log')).open('w') as f:
                if subprocess.run(command,cwd=archive/'web',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=120).returncode: raise RuntimeError(label+' failed')
        raw['toolchain']={'python':str(args.python),'python_sha256':digest(args.python.resolve().read_bytes()),'asset_sha256':digest((cache/asset).read_bytes()),'lockfile_sha256':digest((archive/'web/package-lock.json').read_bytes())}
        runtime = private / 'runtime'; runtime.mkdir()
        server = owned.start('private-core',[str(args.python),'-I',str(archive/'scripts/p6/context_budget_run.py'),'serve','--archive',str(archive),'--runtime',str(runtime)],archive,env)
        shared.wait_file(runtime/'address.json',server)
        evidence=out/'core';evidence.mkdir();client=shared.Client(runtime,evidence)
        raw['health']=client.ok('GET','/v1/health')
        if raw['health']['schema_version']!=12: raise RuntimeError('schema 12 required')
        def build(f, name): return client.operation(name,'POST','/v1/context/build',body(f,name))
        def case(ident,f,operation=None,pack=None,**fields):
            value={'id':ident,'fixture':f,'operation':operation,'pack':pack,**fields};raw['cases'].append(value);return value
        f1=client.fixture('CB01');rule(client,f1,'HARD','SYNTHETIC: preserve all mandatory rules.')
        op1=build(f1,'CB01');p1=op1['http']['body']['context'];case('CB01',f1,op1,p1)
        f2=client.fixture('CB02');rule(client,f2,'NORMAL','SYNTHETIC: optional note cannot replace this rule.')
        note='SYNTHETIC optional historical note: '+'x '*900
        cp=client.operation('CB02-checkpoint','POST','/v1/checkpoints',{'host_id':f2['host'],'task_id':f2['task']['task_id'],'source_event_id':f2['source']['event_id'],'reason':'MANUAL','idempotency_key':'CB02-checkpoint','note':{'text':note,'source_event_id':f2['source']['event_id']}})
        op2=build(f2,'CB02');p2=op2['http']['body']['context'];case('CB02',f2,op2,p2,checkpoint_operation=cp)
        f3=client.fixture('CB03');old=build(f3,'CB05-old');old_pack=old['http']['body']['context']
        for _ in range(3): rule(client,f3,'HARD','SYNTHETIC mandatory boundary: '+'红线不可跳过。 '*300)
        op3=build(f3,'CB03');case('CB03',f3,op3)
        f4=client.fixture('CB04')
        for _ in range(3): rule(client,f4,'NORMAL','SYNTHETIC mandatory rule: '+'规则完整保留。 '*300)
        op4=build(f4,'CB04');case('CB04',f4,op4)
        replay=client.operation('CB05-replay','POST','/v1/context/build',body(f3,'CB05-old'))
        check=client.operation('CB05-admission','POST','/v1/context/'+old_pack['context_pack_id']+'/check-current',{'host_id':f3['host'],'source_event_id':f3['source']['event_id']})
        case('CB05',f3,old,old_pack,replay=replay,admission=check)
        for ident,kind in [('CB06','error'),('CB07','pending'),('CB08','invalid'),('CB09','error')]:
            case(ident,f1,pack=p1,kind=kind)
        absent=frozen_gate.ids_in_source(archive,'ctx')
        raw['cases'][-1]['absent_id']=absent
        # Successful source/command reads are real Core responses, independently auditable.
        for c in raw['cases']:
            if c['id'] in ('CB01','CB02','CB05'):
                c['lineage']={k:client.request('GET','/v1/events/'+c['pack'][k],phase='source-command-readback') for k in ('source_event_id','command_event_id')}
        shared.backup(client.database,out/'before-browser.db');raw['before_browser']=shared.snapshot(client.database)
        on=shared.start_dashboard(owned,archive,client.core,env,'private-dashboard-on')
        off=shared.start_dashboard(owned,archive,client.core,env,'private-dashboard-off',enabled=False)
        browser_cases=[]
        for c in raw['cases']:
            if c['id'] in ('CB03','CB04'): continue
            ident=c['id'];target='bad-context-id' if ident=='CB08' else absent if ident=='CB09' else c['pack']['context_pack_id']
            kind=c.get('kind','context');key='checkpoint_missing_context_read' if ident=='CB06' else 'browser'
            c['browser_definition']={'id':ident,'kind':kind,'hash':'#/context/'+target,'path':None if ident=='CB08' else '/v1/context/'+target,'entity_id':target,'refresh':ident in ('CB01','CB02','CB05'),'status':403 if ident=='CB06' else 404 if ident in ('CB07','CB09') else 200,'key_metadata':{k:v for k,v in client.credentials['keys'][key].items() if k!='token'}}
            browser_cases.append({**c['browser_definition'],'pages':[{'kind':kind}],'ui':off if ident=='CB07' else on,'token':client.credentials['keys'][key]['token']})
        profile=private/'chrome-profile';profile.mkdir()
        config={'out':str(out),'profile':str(profile),'cases':browser_cases}
        browser=owned.start('private-browser',['node',str(archive/'scripts/p6/context_budget_browser.mjs')],archive,env,stdin=shared.canonical(config).encode())
        if browser.wait(timeout=180): raise RuntimeError('browser collection failed')
        raw['browser']=json.loads((out/'browser.json').read_text())
        shared.backup(client.database,out/'after-browser.db');raw['after_browser']=shared.snapshot(client.database)
        ids={n.get('request_id') for n in raw['browser']['network'] if n.get('request_id')}
        audit=client.request('GET','/v1/audit?limit=1000',key='admin',phase='browser-authorized-audit')
        raw['browser_audit']={'http':audit,'matching':[a for a in audit['body']['entries'] if a['request_id'] in ids]}
        shared.backup(client.database,out/'after-audit.db')
        raw['status']='COLLECTED_NOT_A_VERDICT'
    except Exception as exc: raw['error']=type(exc).__name__+': '+str(exc);raw['status']='COLLECTION_FAILED'
    finally:
        raw['cleanup']=owned.close()
        if client is not None:
            write(out/'http-calls.json',client.calls)
            shutil.copyfile(client.runtime/'observer.jsonl',out/'observer.jsonl')
        shutil.rmtree(private,ignore_errors=True);raw['private_runtime_removed']=not private.exists()
        write(out/'raw-result.json',raw)
    # Qualification uses the unchanged installed library and the copied offline asset.
    result=frozen_gate.evaluate(out,args.cache,args.python) if frozen_gate else {'status':'BLOCKED','reason':raw.get('error'),'counts':{'PASS':0,'FAIL':0,'BLOCKED':9}}
    write(out/'result.json',result)
    files=[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p.read_bytes())}for p in sorted(out.rglob('*'))if p.is_file() and p.name!='manifest.json']
    write(out/'manifest.json',{'candidate_commit':raw.get('candidate_commit'),'source_archive_sha256':raw.get('source_archive_sha256'),'files':files})
    print(json.dumps({'status':result['status'],'counts':result['counts'],'error':raw.get('error')}))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('serve');s.add_argument('--archive',type=Path,required=True);s.add_argument('--runtime',type=Path,required=True)
    r=sub.add_parser('run');r.add_argument('--ref',required=True);r.add_argument('--out',type=Path,required=True);r.add_argument('--python',type=Path,default=PYTHON);r.add_argument('--cache',type=Path,default=CACHE)
    a=p.parse_args();serve(a) if a.action=='serve' else sys.exit(run(a))
