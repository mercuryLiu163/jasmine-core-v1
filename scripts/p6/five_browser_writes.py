#!/usr/bin/env python3.13
"""Clean-archive real Core/Chrome write coverage; Interpreter ONLY is simulated.

All runtime DB/keys/profiles are private and temporary. No operator service,
credential, database, hook or deployment setting is read or changed.
"""
from __future__ import annotations
import argparse, hashlib, io, json, os, socket, sqlite3, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

NAMES=('correct','rerun','review_approve','supersede','retire')
REQUIRED=tuple(f'{name}.{kind}' for name in NAMES for kind in ('success','conflict','replay','changed_body'))
COMMON=('prewrite_exact_object_get200','fresh_success_POST','browser_body_uses_actual_basis','correct_replay_field_family','independent_command_GET','separate_operator_audit_http200','exact_audit_request_actor_scope_decision','original_raw_source_byte_identity','changed_object_GET200','forced_reload_fresh_exact_object_GET')
EXTRA={
 'correct':('new_child_current_head_provenance','no_automatic_business_truth_apply','exact_corrected_result'),
 'rerun':('new_child_current_head_provenance','no_automatic_business_truth_apply','rerun_config_and_completion'),
 'review_approve':('review_approved_once_with_original_preview','real_applied_CREATE_RULE','applied_rule_scoped_active','resolution_source_and_application_link'),
 'supersede':('rule_expected_version_revision_status_scope','immutable_old_rule_versions','actual_rule_history_command','supersede_original_scope_origin_content'),
 'retire':('rule_expected_version_revision_status_scope','immutable_old_rule_versions','actual_rule_history_command'),
}
CHECKS={name+'.'+kind: (COMMON+EXTRA[name] if kind=='success' else
 ('competing_change_real_core_succeeded','actual_stale_basis_409','all_business_source_operation_tables_unchanged','draft_and_replay_key_preserved','no_automatic_retry','separate_operator_audit_http200','exact_audit_request_actor_scope_decision') if kind=='conflict' else
 ('exact_replay200','immutable_original_receipt','replay_no_second_command_child_version_application') if kind=='replay' else
 ('same_key_changed_body_exact_conflict','replay_no_second_command_child_version_application')) for name in NAMES for kind in ('success','conflict','replay','changed_body')}
ROOT=Path(__file__).resolve().parents[2]
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v):return hashlib.sha256(v if isinstance(v,bytes) else canonical(v).encode()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def verdict(records):
    if not isinstance(records,list) or len(records)!=len(REQUIRED) or not all(isinstance(r,dict) for r in records):return False
    if set(r.get('id') for r in records)!=set(REQUIRED):return False
    for row in records:
        checks=row.get('checks')
        if row.get('passed') is not True or not isinstance(checks,list) or not all(isinstance(c,dict) and c.get('passed') is True for c in checks):return False
        names=[c.get('name') for c in checks]
        if len(names)!=len(set(names)) or not set(CHECKS[row['id']])<=set(names):return False
    return True
def snapshot(database):
    con=sqlite3.connect(Path(database).as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    try:
        tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name") if r[0] not in ('audit_log','api_keys')]
        return {t:[dict(r) for r in con.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in tables}
    finally:con.close()

def serve_private(args):
    sys.path.insert(0,str(args.archive/'src'))
    from jasmine_core import SCHEMA_VERSION,db,ids,registry,auth
    from jasmine_core.migrations import migrate
    from jasmine_core.models import NewEvent,NewObject
    from jasmine_core.events import EventStore
    from jasmine_core.objects import ObjectStore
    from jasmine_core.interpretations import InterpretationStore
    from jasmine_core.resolver import ResolverStore
    from jasmine_core.reviews import ReviewStore
    from jasmine_core.authority import AuthorityStore
    from jasmine_core.interpreter_provider import _base_config
    from jasmine_core.api.server import Application,_LoopbackHTTPServer,_Handler
    runtime=args.runtime;database=runtime/'core.db';trace=runtime/'provider.jsonl'
    class Provider:
        def configuration(self):
            value=_base_config();value.update(provider='component_simulation',model='deterministic-fixture-no-LLM',version='five-browser-fixture.v1');return value
        def extract(self,source):
            value={'event_id':source['event_id'],'confidence':0.95,'candidates':[{'kind':'RULE','content':'Synthetic browser coverage rule','scope':{'kind':'TASK','project_id':source['project_id'],'task_id':source['task_id'],'path':None,'tool':None},'impact':'HIGH','certainty':'EXPLICIT','confidence':0.95,'rationale':'Explicitly synthetic provider boundary; no model quality claim','source_span':{'start':0,'end':len(source['text']),'quote':source['text']}}]}
            with trace.open('a') as f:f.write(canonical({'mode':'component_simulation','source':source,'configuration':self.configuration(),'output':value})+'\n')
            return canonical(value)
    con=db.connect(database);migrate(con)
    host=ids.new_id('hst');human=ids.new_id('act');admin=ids.new_id('act');system=ids.new_id('act')
    reg=registry.Registry(con);reg.upsert_host(host,display_name='isolated five-browser host')
    for ident,kind in [(human,'human'),(admin,'human'),(system,'system')]:reg.upsert_actor(ident,kind=kind,home_host_id=host)
    scopes=['objects:read','objects:write','events:read','events:write','state:read','authority:read','authority:propose','authority:manage','evidence:read','interpretations:read','interpretations:manage','interpretations:process','reviews:read','reviews:manage','resolutions:read','resolutions:process']
    human_key=auth.Auth(con).issue_key(actor_id=human,label='isolated browser write fixture',scopes=scopes)
    admin_key=auth.Auth(con).issue_key(actor_id=admin,label='separate operator audit',scopes=['admin'])
    os.environ['JASMINE_CORE_RESOLVER_ACTOR_ID']=system;os.environ['JASMINE_CORE_RESOLVER_HOST_ID']=host
    objects=ObjectStore(con,schema_version=SCHEMA_VERSION);events=EventStore(con,schema_version=SCHEMA_VERSION)
    store=InterpretationStore(con,schema_version=SCHEMA_VERSION,provider=Provider());resolver=ResolverStore(store);authority=AuthorityStore(con,schema_version=SCHEMA_VERSION)
    project=objects.create(NewObject.project({'name':'Synthetic five-browser isolated project','host_id':host}),actor_id=human)['object']['project_id']
    cases=[]
    for name in NAMES:
        for negative in (False,True):
            task=objects.create(NewObject.task({'title':f'Synthetic {name} '+('conflict' if negative else 'success'),'project_id':project,'host_id':host}),actor_id=human)['object']['task_id']
            source=events.append(NewEvent.from_request({'event_type':'assistant.message','source_system':'synthetic-five-browser-fixture','host_id':host,'project_id':project,'task_id':task,'payload':{'text':'SYNTHETIC BROWSER FIXTURE: keep the test output for review; not a native human prompt.'}},actor_id=human,actor_kind='human'))[0]
            row={'name':name,'negative':negative,'project':project,'task':task,'source':source,'host':host}
            if name in ('correct','rerun','review_approve'):
                interpreted=store.process({'event_id':source['event_id'],'idempotency_key':'seed-'+task},actor_id=human)['interpretation'];row['interpretation']=resolver.maintenance.get(interpreted['interpretation_id']);row['id']=interpreted['interpretation_id']
                result=json.loads(interpreted['raw_result_json']);result['candidates'][0]['content']='Synthetic corrected browser requirement';row['corrected_result']=result
                if name=='review_approve':
                    preview=resolver.preview(row['id']);body={'idempotency_key':'seed-resolve-'+task,'host_id':host,'policy_version':preview['policy_version'],'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest']}
                    resolved=resolver.resolve(row['id'],body,actor_id=human);assert resolved['resolution']['result']['disposition']=='PENDING_REVIEW'
                    row['id']=resolved['resolution']['result']['review_id'];row['review']=ReviewStore(resolver).get(row['id']);row['actions']=[{'candidate_index':0,'action':'CREATE_RULE','payload':{'kind':'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':'Human approved synthetic browser requirement','matcher':{}}}]
            else:
                proposed=authority.propose({'rule_key':'fixture-'+task.lower().replace('_','-'),'kind':'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':'Synthetic version 1','matcher':{},'scope':{'kind':'task','project_id':project,'task_id':task},'origin_event_id':source['event_id'],'host_id':host},actor_id=human)
                active=authority.transition(proposed['rule']['rule_id'],'approve',{'host_id':host,'expected_revision':1},actor_id=human);row['id']=active['rule']['rule_id'];row['rule']=active['rule']
                row['new_origin']=events.append(NewEvent.from_request({'event_type':'assistant.message','source_system':'synthetic-five-browser-fixture','host_id':host,'project_id':project,'task_id':task,'payload':{'text':'Synthetic replacement rule origin; labelled test input.'}},actor_id=human,actor_kind='human'))[0]
                row['new_content']='Synthetic version 2 from real browser'
            cases.append(row)
    con.close()
    class FixtureApplication(Application):
        def _core(self):
            core=super()._core()
            # Public store constructor injection, ONLY at the provider boundary.
            core.interpretations=InterpretationStore(core.conn,schema_version=core.schema_version,provider=Provider())
            core.resolver=ResolverStore(core.interpretations);core.reviews=ReviewStore(core.resolver)
            return core
    server=_LoopbackHTTPServer(('127.0.0.1',0),_Handler);server.app=FixtureApplication(database);server.daemon_threads=True
    write(runtime/'descriptor.json',{'mode':'real-Core-real-Chrome; component_simulation-provider-only','core':f'http://127.0.0.1:{server.server_port}','database':str(database),'provider_trace':str(trace),'human':human_key,'admin':admin_key,'host':host,'system':system,'cases':cases})
    try:server.serve_forever()
    finally:server.server_close()

def run(args):
    os.umask(0o077);out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    ref=subprocess.check_output(['git','-C',str(ROOT),'rev-parse',args.ref],text=True).strip()
    report={'candidate_commit':ref,'mode':'real-Core-real-Chrome; component_simulation-provider-only','scope':'five missing milestone-B browser write paths only','status':'FAIL','records':[],'limitations':['No native capture or actual model extraction; synthetic assistant sources and provider are labelled.','No deployment, main merge, daily-use release or whole P6/V1 acceptance.']}
    logs=[];processes=[]
    try:
        with tempfile.TemporaryDirectory(prefix='p6-five-writes-private-') as temp:
            private=Path(temp);archive=private/'archive';archive.mkdir();runtime=private/'runtime';runtime.mkdir()
            data=subprocess.check_output(['git','-C',str(ROOT),'archive',ref]);
            with tarfile.open(fileobj=io.BytesIO(data)) as t:t.extractall(archive,filter='data')
            report['source_archive_sha256']=hashlib.sha256(data).hexdigest();report['source_tree']=subprocess.check_output(['git','-C',str(ROOT),'rev-parse',ref+'^{tree}'],text=True).strip()
            env={k:os.environ[k] for k in ('HOME','USER','LANG','TMPDIR') if k in os.environ};env.update(PATH='/opt/homebrew/bin:/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',npm_config_cache=str(private/'npm-cache'))
            for label,cmd in [('npm-ci',['npm','ci','--no-audit','--no-fund']),('build',['npm','run','build'])]:
                with (out/(label+'.log')).open('w') as f:done=subprocess.run(cmd,cwd=archive/'web',env=env,stdout=f,stderr=subprocess.STDOUT)
                if done.returncode:raise RuntimeError(label+' failed')
            report['built_assets']={str(p.relative_to(archive/'web/dist')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (archive/'web/dist').rglob('*') if p.is_file()}
            f=(out/'core.log').open('w');logs.append(f)
            proc=subprocess.Popen([sys.executable,'-I',str(archive/'scripts/p6/five_browser_writes.py'),'serve','--archive',str(archive),'--runtime',str(runtime)],cwd=archive,env=env,stdout=f,stderr=f);processes.append(proc)
            deadline=time.monotonic()+25
            while not (runtime/'descriptor.json').exists():
                if proc.poll() is not None:raise RuntimeError('isolated Core startup failed; see core.log')
                if time.monotonic()>deadline:raise RuntimeError('Core startup deadline')
                time.sleep(.1)
            descriptor=json.loads((runtime/'descriptor.json').read_text())
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            descriptor.update(ui=f'http://127.0.0.1:{port}',out=str(out),python=sys.executable,snapshot_script=str(archive/'scripts/p6/five_browser_writes.py'))
            ui_env={**env,'P6_CORE_ORIGIN':descriptor['core'],'P6_ENABLE_WEB_CONFIRMATIONS':'0','P6_ENABLE_P3_READS':'0','P6_ENABLE_P4_READS':'0','P6_ENABLE_P4_WRITES':'0','P6_ENABLE_P7_READS':'0'}
            f=(out/'dashboard.log').open('w');logs.append(f);ui=subprocess.Popen(['node',str(archive/'web/server/serve.mjs'),'--root',str(archive/'web/dist'),'--port',str(port),'--host','127.0.0.1'],cwd=archive,env=ui_env,stdout=f,stderr=f);processes.append(ui)
            import urllib.request
            deadline=time.monotonic()+20
            while True:
                try:urllib.request.urlopen(descriptor['ui']+'/v1/health',timeout=1).close();break
                except Exception:
                    if ui.poll() is not None or time.monotonic()>deadline:raise RuntimeError('isolated Dashboard startup failed')
                    time.sleep(.1)
            start_snapshot=snapshot(descriptor['database']);write(out/'setup-state.json',start_snapshot)
            browser=subprocess.run(['node',str(archive/'scripts/p6/five_browser_writes.mjs')],input=json.dumps(descriptor),text=True,capture_output=True,env=env,cwd=archive,timeout=240)
            (out/'browser.stdout.log').write_text(browser.stdout);(out/'browser.stderr.log').write_text(browser.stderr)
            trace=json.loads((out/'browser.json').read_text());report.update(records=trace['records'],browser_status=trace['status'],browser_error=trace.get('error'))
            write(out/'final-state.json',snapshot(descriptor['database']))
            (out/'provider-calls.jsonl').write_bytes(Path(descriptor['provider_trace']).read_bytes())
            calls=[json.loads(line) for line in Path(descriptor['provider_trace']).read_text().splitlines()]
            reruns=[r for r in calls if r['source'].get('operation_context',{}).get('type')=='rerun']
            expected_source=next(c['source']['event_id'] for c in descriptor['cases'] if c['name']=='rerun' and not c['negative'])
            report['provider_validation']={'passed':len(calls)==7 and len(reruns)==1 and reruns[0]['source']['event_id']==expected_source,'total_seed_and_rerun_calls':len(calls),'rerun_calls':len(reruns),'expected_rerun_source':expected_source,'mode':'component_simulation-only'}
            report['runtime']={'core_origin':descriptor['core'],'dashboard_origin':descriptor['ui'],'schema_version':12,'core_pid':proc.pid,'dashboard_pid':ui.pid,'isolated_runtime_removed_on_exit':True,'item6_flag':0}
            report['identities']={role:{k:v for k,v in descriptor[role].items() if k!='token'} for role in ('human','admin')}
            encoded='\n'.join(p.read_text(errors='replace') for p in out.rglob('*') if p.is_file() and p.suffix!='.png')
            if any(descriptor[k]['token'] in encoded for k in ('human','admin')):raise RuntimeError('credential found in evidence')
            report['tokens_in_shared_evidence']=False
            if browser.returncode or trace['status']!='PASS' or not verdict(trace['records']) or not report['provider_validation']['passed']:raise RuntimeError('browser/provider required record gate failed')
            report['status']='PASS_FIVE_BROWSER_WRITES_SCOPED'
    except Exception as e:report['error']=type(e).__name__+': '+str(e)
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
        for f in logs:f.close()
        write(out/'acceptance.json',report)
        write(out/'manifest.json',{'candidate_commit':ref,'status':report['status'],'mode':report['mode'],'sha256':{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file() and p.name!='manifest.json'}})
        (out/'result.md').write_text('# Five browser writes\n\n'+report['status']+'\n\nCandidate: `'+ref+'`.\n\nProvider/source fixtures are synthetic; Core and browser writes are real. No native P2, deployment or whole P6/V1 claim.\n\n'+(report.get('error') or 'All 20 required records passed.')+'\n')
        # Seal the result together with the other artifacts.
        m=json.loads((out/'manifest.json').read_text());m['sha256']['result.md']=hashlib.sha256((out/'result.md').read_bytes()).hexdigest();write(out/'manifest.json',m)
    print(json.dumps({'status':report['status'],'candidate':ref,'records':len(report['records']),'out':str(out),'error':report.get('error')}))
    return 0 if report['status']=='PASS_FIVE_BROWSER_WRITES_SCOPED' else 1

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    r=sub.add_parser('run');r.add_argument('--ref',required=True);r.add_argument('--out',type=Path,required=True)
    r=sub.add_parser('serve');r.add_argument('--archive',type=Path,required=True);r.add_argument('--runtime',type=Path,required=True)
    r=sub.add_parser('snapshot');r.add_argument('--db',required=True)
    a=p.parse_args()
    if a.mode=='serve':serve_private(a);return 0
    if a.mode=='snapshot':print(canonical(snapshot(a.db)));return 0
    return run(a)
if __name__=='__main__':raise SystemExit(main())
