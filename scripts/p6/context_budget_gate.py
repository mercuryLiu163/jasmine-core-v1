"""Recompute four mandatory gates per frozen Context case from physical receipts."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tarfile
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import checkpoint_negatives as shared
from checkpoint_negative_gate import capture_receipts, jsonblock, serialized_event, normalized, format_timestamp, no_success_residue

ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_text())


def ids_in_source(archive,prefix):
    # Deliberate absent read target, never an asserted created object or source.
    return prefix+'_'+'0'*25+'1'


def require(ok,reason):
    if not ok:raise ValueError(reason)


def sealed_tables(path):
    # These are consistent SQLite backup files, never a live WAL database.
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    con.row_factory=sqlite3.Row
    try:
        tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t:sorted([dict(r) for r in con.execute('SELECT * FROM "'+t.replace('"','""')+'"')],key=shared.canonical) for t in tables if t!='api_keys'}
    finally:con.close()


def sealed_snapshot(path):
    return {t:r for t,r in sealed_tables(path).items() if t!='audit_log'}


def audit_rows(path,request_id):
    rows=[dict(r) for r in sealed_tables(path)['audit_log'] if r['request_id']==request_id]
    for r in rows:r['detail']=json.loads(r.pop('detail_json'))
    return rows


def budget_proof(dom,p):
    budget=dom['budget'];count=f"构建记录 token: {p['token_count']} / 2500。 仅表示已渲染 pack 的编码计数，不表示模型完整提示额度。"
    boundary='这是构建时的历史记录；重新读取不证明当前可注入。本页没有 Agent 实际接收回执，构建不等于注入。'
    require(budget['status']=='within' and normalized(budget['count'])==count and normalized(budget['boundary'])==boundary and budget['warning'] is None and budget['omissions_warning'] is None,'exact budget/current admission boundary differs')
    omissions=[r for r in p['omissions'] if r['reason']=='optional_budget']
    expected=[]
    for row in omissions:
        links=[{'text':'≡event'+s,'href':'#/event/'+s} for s in row['source_ids']]
        expected.append({'text':f"{row['item_id']} · {row['section']} · {row['reason']} · standalone_tokens = {row['standalone_tokens']} 来源："+''.join(x['text'] for x in links),'links':links})
    require(budget['rows']==expected,'exact visible omission rows/source links differ')
    if omissions:
        headline=f'可选内容预算不足，已按整项省略 {len(omissions)} 项。原因可能为分区上限或整包额度；必需内容不因可选内容预算而截断。'
        require(normalized(budget['omissions'])==headline+''.join(r['text'] for r in expected) and budget['no_omissions'] is None,'exact visible omission count/boundary differs')
    else:require(budget['omissions'] is None and budget['no_omissions']=='Core 记录中没有 optional_budget 省略项；其他原因见 omissions 原件。','false optional omission outcome')
    kv={x['label']:normalized(x['text']) for x in dom['kv']}
    for label,value in {'context_pack_id':p['context_pack_id'],'reason':p['reason'],'actor / host':p['actor_id']+' · '+p['host_id'],'created_at':format_timestamp(p['created_at']),'scope':p['project_id']+' · '+p['task_id']+(' · session '+p['session_id'] if p['session_id'] else ' · 无 session')}.items():
        require(sum(x['label']==label for x in dom['kv'])==1 and kv[label]==value,'exact Context metadata differs '+label)
    for label,field in [('source Event','source_event_id'),('command Event','command_event_id')]:
        text='≡'+label+p[field]
        require(kv[label]==text and [x for x in dom['links'] if x['text'].startswith('≡'+label)]==[{'text':text,'href':'#/event/'+p[field]}],'Context source/command link differs')
    for field,route in [('task_id','task'),('project_id','project')]:require([x for x in dom['links'] if x['text']==p[field]]==[{'text':p[field],'href':'#/'+route+'/'+p[field]}],'Context object link differs')
    require(dom['digests']==[{'label':k,'value':p[k]} for k in ('truth_digest','rendered_sha256','config_digest','workspace_digest')],'Context digest display differs')


def operation(directory,op):
    name=op['operation_receipt'];require(load(directory/name)==op,'operation receipt differs')
    expected=[name.removesuffix('-operation.json')+'-'+phase+'.db' for phase in ('before','after')]
    require(op['database_receipts']==expected,'operation DB role differs')
    for ref,phase in zip(expected,('before','after')):require(sealed_snapshot(directory/ref)==op[phase],'actual DB differs from operation '+phase)
    h=op['http'];rows=op['audit'];require(len(rows)==1,'missing/duplicate operation audit')
    require(rows==audit_rows(directory/expected[1],h['request_id']),'operation audit differs from actual DB')
    a=rows[0];require((a['request_id'],a['method'],a['path'],a['status_code'])==(h['request_id'],h['method'],h['path'],h['status']),'operation audit target differs')
    require(a['error_code']==h['body'].get('error',{}).get('code'),'operation audit error differs')
    observed=[e for e in shared.lines(directory/'observer.jsonl') if e.get('request_id')==h['request_id']]
    require(observed==op['observer'],'observer differs from actual request window')
    requests=[e for e in observed if e['kind'] in ('request_start','request_end')]
    require(len(requests)==2 and requests[0]['kind']=='request_start' and requests[-1]['kind']=='request_end' and requests[0]['path']==h['path'] and requests[0]['method']==h['method'] and requests[-1]['status']==h['status'],'actual operation request window differs')
    before,after=op['before'],op['after']
    if h['status']>=400 or h['body'].get('replayed'):
        require(before==after,'refusal/replay changed business/history DB')
    elif h['path']=='/v1/context/build':
        p=h['body']['context'];added=[e for e in after['events'] if e not in before['events']]
        require(len(added)==1 and serialized_event(added[0])['event_id']==p['command_event_id'] and added[0]['event_type']=='context.built','context command append differs')
        require(all(e in after['events'] for e in before['events']),'old Event changed')
        delta=[r for r in after['context_packs'] if r not in before['context_packs']]
        require(len(delta)==1 and json.loads(delta[0]['full_result_json'])==p and all(r in after['context_packs'] for r in before['context_packs']),'stored pack append differs')
        keys=[r for r in after['context_request_keys'] if r not in before['context_request_keys']]
        require(len(keys)==1 and keys[0]['context_pack_id']==p['context_pack_id'] and all(r in after['context_request_keys'] for r in before['context_request_keys']),'Context key append differs')
        allowed={'context_packs','context_request_keys','events'}
        for table in before:
            if table not in allowed and not table.startswith('events_fts'):require(before[table]==after[table],'Context mutated other table '+table)
    return expected+[name]


def evaluate(directory,cache,python):
    directory=Path(directory);result={'status':'BLOCKED','role':'author-implementation-check','scope_acceptance_signed':False,'records':[],'counts':{'PASS':0,'FAIL':0,'BLOCKED':9},'case_gate_evaluations_passed':0,'failed_build_dashboard_receipt':'UNAVAILABLE_EXISTING_CONTRACT'}
    try:
        raw=load(directory/'raw-result.json');matrix=load(directory/'matrix.json')
        require(raw['status']=='COLLECTED_NOT_A_VERDICT' and 'error' not in raw,'collection failed/incomplete')
        require([c['id'] for c in raw['cases']]==[c['id'] for c in matrix['cases']],'incomplete/duplicate scene matrix')
        archive=(directory/'source.tar').read_bytes();require(shared.digest(archive)==raw['source_archive_sha256'],'archive digest differs')
        with tempfile.TemporaryDirectory(prefix='p6-context-recompute-') as temp:
            tree=Path(temp)
            with tarfile.open(fileobj=io.BytesIO(archive)) as source:source.extractall(tree,filter='data')
            frozen=load(tree/'docs/validation/p6-context-budget/SPEC-FROZEN.json')
            for f in frozen['files']:
                data=(tree/f['path']).read_bytes();require(len(data)==f['bytes'] and shared.digest(data)==f['sha256'],'Context spec freeze drift')
            require((tree/'docs/validation/p6-context-budget/acceptance-matrix.v1.json').read_bytes()==(directory/'matrix.json').read_bytes(),'frozen matrix drift')
            code='import sys,json;sys.path.insert(0,sys.argv[1]);from jasmine_core.context.tokens import Tokenizer;t=Tokenizer(cache_dir=sys.argv[2]);print(json.dumps({"identity":t.identity,"counts":[t.count(s) for s in json.load(sys.stdin)]}))'
            packs=[c['pack'] for c in raw['cases'] if c['id'] in ('CB01','CB02','CB05')]
            counted=subprocess.run([str(python),'-I','-c',code,str(tree/'src'),str(cache)],input=json.dumps([p['rendered_content'] for p in packs]),capture_output=True,text=True,timeout=30)
            require(counted.returncode==0,'real pinned tokenizer unavailable: '+counted.stderr)
            tokens=json.loads(counted.stdout)
        require(raw['before_browser']==sealed_snapshot(directory/'before-browser.db')==raw['after_browser']==sealed_snapshot(directory/'after-browser.db'),'browser changed actual business/history DB')
        require(raw['private_runtime_removed'] and raw['cleanup'] and all(c['group_gone'] for c in raw['cleanup']),'owned cleanup incomplete')
        browser=load(directory/'browser.json');require(browser==raw['browser'],'browser raw differs')
        require(browser['status']=='COLLECTED_NOT_A_VERDICT' and not browser['console_errors'],'browser collection failed')
        require(browser['credential_storage']=={'local':0,'session':0,'indexed':0},'persistent credentials/storage detected')
        require(shared.lines(directory/'browser-requests.jsonl')==browser['network'] and shared.lines(directory/'click-trace.jsonl')==browser['clicks'],'wire/click trace differs')
        want=[c['id'] for c in raw['cases'] if c['id'] not in ('CB03','CB04')]
        require([c['id'] for c in browser['records']]==want,'missing/extra browser scene')
        observer=shared.lines(directory/'observer.jsonl');calls=load(directory/'http-calls.json');required=['source.tar','matrix.json','raw-result.json','browser.json','browser-requests.jsonl','click-trace.jsonl','http-calls.json','observer.jsonl','before-browser.db','after-browser.db']
        for c in raw['cases']:
            gates={};ident=c['id']
            try:
                label={'CB05':'CB03','CB06':'CB01','CB07':'CB01','CB08':'CB01','CB09':'CB01'}.get(ident,ident)
                fixture=c['fixture'];state=raw['before_browser']
                task=next(t for t in state['tasks'] if t['task_id']==fixture['task']['task_id'])
                project=next(p for p in state['projects'] if p['project_id']==fixture['project']['project_id'])
                source=next(e for e in state['events'] if e['event_id']==fixture['source']['event_id'])
                require(task['title']=='SYNTHETIC '+label and project['name']=='SYNTHETIC isolated '+label and task['project_id']==project['project_id'],'actual scene Task/project differs')
                require(serialized_event(source)==fixture['source'] and source['task_id']==task['task_id'] and source['host_id']==fixture['host'],'actual fixture source/Task/host differs')
                if c['operation']:
                    name='CB05-old' if ident=='CB05' else ident
                    require(c['operation']['operation_receipt']=='core/'+name+'-operation.json','named scene operation role differs')
                    require(c['operation']['http'] in calls,'operation absent from actual HTTP transcript')
                    require(c['operation']['http']['request_body']=={'task_id':task['task_id'],'host_id':fixture['host'],'source_event_id':source['event_id'],'reason':'MANUAL','idempotency_key':name},'actual build source/Task/key role differs')
                if ident in ('CB03','CB04'):
                    op=c['operation'];h=op['http'];code='AUTHORITY_TOO_LARGE' if ident=='CB03' else 'CONTEXT_MANDATORY_TOO_LARGE'
                    require(h['status']==409 and h['body']['error']['code']==code,'mandatory overflow outcome differs')
                    details=h['body']['error']['details'];require(details['mandatory_tokens']>details['maximum_tokens']==2500,'not observed exact mandatory overflow')
                    require(op['http']['request_body']=={'task_id':c['fixture']['task']['task_id'],'host_id':c['fixture']['host'],'source_event_id':c['fixture']['source']['event_id'],'reason':'MANUAL','idempotency_key':ident},'refusal scene/request identity differs')
                    required+=operation(directory,op)
                    require(not any(json.loads(r['full_result_json'])['source_event_id']==c['fixture']['source']['event_id'] and json.loads(r['full_result_json'])['reason']=='MANUAL' for r in op['after']['context_packs']) if ident=='CB04' else op['before']==op['after'],'failed build persisted a receipt')
                else:
                    d=c['browser_definition'];ui=next(u for u in browser['records'] if u['id']==ident)
                    require(ui['status']=='COLLECTED_NOT_A_VERDICT' and ui['navigation'] in browser['clicks'] and ui['navigation']['hash']==d['hash'],'actual navigation differs')
                    require(ui['network'] and all(n in browser['network'] and n['id']==ident for n in ui['network']),'scene wire differs')
                    role_names=[ident+'-read']+([ident+'-refresh'] if d['refresh'] else [])
                    required+=capture_receipts(ui['observations'],role_names)
                    target=[n for n in ui['network'] if n['path']==d['path']]
                    require(len(target)==(0 if d['kind']=='invalid' else 2 if d['refresh'] else 1),'read/refresh request cardinality differs')
                    for i,o in enumerate(ui['observations']):
                        require(load(directory/o['dom_file'])==o['dom'] and (directory/o['screenshot']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),'DOM/PNG physical capture differs')
                        require(o['dom']['hash']==d['hash'] and ui['navigation']['at_unix']<=o['dom_at_unix']<=o['at_unix'],'capture navigation/time differs')
                        ids=[n['request_id'] for n in target if n.get('response_finished_at_unix',float('inf'))<=o['dom_at_unix']]
                        require(o['request_ids']==ids and len(ids)==(0 if d['kind']=='invalid' else i+1),'capture not bound to completed read/refresh')
                        if target:
                            n=target[i];require(n['method']=='GET' and n['query']=='' and n['authorization_present'] and n['request_id_sent']==n['request_id'] and ui['navigation']['at_unix']<=n['at_unix']<=n['response_finished_at_unix']<=o['dom_at_unix'],'actual read request phase/authentication differs')
                            if i:
                                clicks=[x for x in browser['clicks'] if x['id']==ident and x.get('text')=='从 Core 重新读取']
                                require(len(clicks)==1 and ui['observations'][i-1]['at_unix']<=clicks[0]['at_unix']<=n['at_unix'],'refresh request outside actual click phase')
                        dom=o['dom']
                        if ident in ('CB01','CB02','CB05'):
                            p=c['pack'];require(d['entity_id']==p['context_pack_id'] and all(n['body']=={'context':p} and n['status']==200 for n in target),'wire body/scene differs')
                            idx=('CB01','CB02','CB05').index(ident);require(tokens['counts'][idx]==p['token_count']<=2500 and tokens['identity']==p['tokenizer'],'exact pinned pack count/identity differs')
                            require(shared.digest(p['rendered_content'].encode())==p['rendered_sha256'] and len(p['rendered_content'].encode())==p['rendered_utf8_bytes'],'actual rendered hash/bytes differs')
                            budget_proof(dom,p)
                            require(next(r['text'] for r in dom['rawtexts'] if 'rendered_content' in r['label'])==p['rendered_content'],'actual full DOM render differs')
                            for field in ('section_metrics','omissions','tokenizer','projection','configuration','selected_optional_ids','selector','resume_comparison','memory','workspace','sample_window'):require(jsonblock(dom,field)==p[field],'dynamic DOM block differs: '+field)
                            kv={x['label']:x['text'] for x in dom['kv']};require(kv['context_pack_id']==p['context_pack_id'],'pack identity differs')
                            for field in ('task_id','project_id','source_event_id','command_event_id'):
                                require(p[field] in dom['text'],'DOM metadata differs '+field)
                            if ident=='CB02':
                                cp=c['checkpoint_operation']['http']['body']['checkpoint'];omissions=p['omissions']
                                require(any(x['item_id']=='note:'+cp['checkpoint_id'] and x['source_ids']==[cp['note']['source_event_id']] and x['reason']=='optional_budget' for x in omissions),'real note omission/source absent')
                                require(dom['budget']['omissions'] and len(dom['budget']['rows'])==len(omissions),'visible omission count differs')
                                for row,want_row in zip(dom['budget']['rows'],omissions):
                                    require(want_row['item_id'] in row['text'] and str(want_row['standalone_tokens']) in row['text'],'visible omission metadata differs')
                                    require([x['href'] for x in row['links']]==['#/event/'+s for s in want_row['source_ids']],'omission source link differs')
                                require(cp['note']['text'] not in p['rendered_content'],'omitted optional note was rendered')
                        else:
                            require(dom['budget'] is None and no_success_residue(dom,d['kind']),'refused/local/pending page carries old pack success')
                            require(not any(c['pack'][k] in dom['text'] for k in ('task_id','project_id','source_event_id','command_event_id')) and c['pack']['rendered_content'] not in dom['text'],'refused page mixes prior pack content')
                            if d['kind']=='invalid': require(dom['invalid'] and dom['alerts'],'local invalid not shown')
                            elif d['kind']=='pending': require(dom['pending'] and all(n['body']['error']['code']=='proxy_path_not_allowed' for n in target),'proxy pending conflated')
                            else: require(dom['alerts'] and all(n['status']==d['status'] and n['body']['error']['code'] in dom['text'] for n in target),'actual Core refusal not shown')
                    if d['refresh']:require(len([x for x in browser['clicks'] if x['id']==ident and x.get('text')=='从 Core 重新读取'])==1,'refresh not actual pointer click')
                    for n in target:
                        events=[e for e in observer if e.get('request_id')==n['request_id']]
                        if ident=='CB07':require(not events,'proxy refusal reached Core')
                        else:
                            require([e['kind'] for e in events]==['request_start','request_end'] and events[0]['path']==d['path'] and events[-1]['status']==n['status'],'actual Core read/zero-scan window differs')
                            require(n['at_unix']<=events[0]['at_unix']<=events[-1]['at_unix']<=n['response_finished_at_unix'],'Core read outside actual browser request window')
                            audits=[a for a in raw['browser_audit']['matching'] if a['request_id']==n['request_id']]
                            require(audits==audit_rows(directory/'after-browser.db',n['request_id']) and audits[0]['actor_id']==d['key_metadata']['actor_id'],'browser audit differs from actual DB/actor')
                            require(len(audits)==1 and audits[0]['status_code']==n['status'] and audits[0]['path']==d['path'] and audits[0]['error_code']==n['body'].get('error',{}).get('code'),'authorized browser audit differs')
                if ident in ('CB01','CB02','CB05'):
                    required+=operation(directory,c['operation'])
                    p=c['pack'];stored=next(r for r in raw['before_browser']['context_packs'] if r['context_pack_id']==p['context_pack_id'])
                    require(json.loads(stored['full_result_json'])==p,'expected pack differs from actual stored DB')
                    for field,h in c['lineage'].items():
                        event=next(e for e in raw['before_browser']['events'] if e['event_id']==p[field])
                        require(h in calls and h['body']['event']==serialized_event(event) and h['status']==200,'source/command actual DB HTTP differs')
                    source=c['lineage']['source_event_id']['body']['event'];command=c['lineage']['command_event_id']['body']['event']
                    require(source['event_id']==c['fixture']['source']['event_id'] and p['task_id']==c['fixture']['task']['task_id']==source['task_id']==command['task_id'] and command['payload']['context_pack_id']==p['context_pack_id'] and command['payload']['source_event_id']==source['event_id'],'source/command scene lineage differs')
                    for r in p['projection']['active_rules']:require(r['content'] in p['rendered_content'],'mandatory rule truncated/omitted')
                    if ident=='CB02':required+=operation(directory,c['checkpoint_operation'])
                    if ident=='CB05':
                        require(c['replay']['operation_receipt']=='core/CB05-replay-operation.json' and c['admission']['operation_receipt']=='core/CB05-admission-operation.json','old replay/admission operation role differs')
                        for key in ('replay','admission'):required+=operation(directory,c[key])
                        require(c['replay']['http'] in calls and c['replay']['http']['request_body']==c['operation']['http']['request_body'] and c['replay']['http']['path']=='/v1/context/build','replay old key/source role differs')
                        require(c['admission']['http'] in calls and c['admission']['http']['path']=='/v1/context/'+p['context_pack_id']+'/check-current' and c['admission']['http']['request_body']=={'host_id':fixture['host'],'source_event_id':source['event_id']},'current admission role differs')
                        require(c['replay']['http']['status']==200 and c['replay']['http']['body']=={'context':p,'replayed':True},'old replay changed')
                        require(c['admission']['http']['status']==409 and c['admission']['http']['body']['error']['code']=='context_stale','stale admission not refused')
                        require(not any(e['kind'].startswith('scanner_') for e in c['replay']['observer']),'replay scanned workspace')
                gates={name:True for name in matrix['required_case_gates']}
            except Exception as exc:gates={name:False for name in matrix['required_case_gates']};c_reason=type(exc).__name__+': '+str(exc)
            result['records'].append({'id':ident,'status':'PASS' if all(gates.values()) else 'FAIL','gates':gates,**({'reason':c_reason} if not all(gates.values()) else {})})
        result['required_artifact_paths']=sorted(set(required));passed=sum(r['status']=='PASS' for r in result['records']);result['counts']={'PASS':passed,'FAIL':9-passed,'BLOCKED':0}
        result['case_gate_evaluations_passed']=sum(sum(r['gates'].values()) for r in result['records']);result['status']='PASS' if passed==9 else 'FAIL'
    except Exception as exc:result['reason']=type(exc).__name__+': '+str(exc)
    return result


def verify(directory,candidate,cache,python):
    m=load(directory/'manifest.json');raw=load(directory/'raw-result.json')
    require(m['candidate_commit']==raw['candidate_commit']==candidate,'candidate binding differs')
    actual=subprocess.check_output(['git','-C',str(ROOT),'archive','--format=tar',candidate])
    require(actual==(directory/'source.tar').read_bytes(),'clean candidate archive differs')
    paths={f['path'] for f in m['files']};require(len(paths)==len(m['files']),'duplicate manifest file')
    for f in m['files']:
        p=(directory/f['path']).resolve();require(p.is_relative_to(directory.resolve()) and p.stat().st_size==f['bytes'] and shared.digest(p.read_bytes())==f['sha256'],'physical artifact absent/different '+f['path'])
    result=evaluate(directory,cache,python)
    require(set(result.get('required_artifact_paths',[]))<=paths,'required artifacts absent from manifest')
    require(result==load(directory/'result.json'),'recomputed verdict differs')
    return result


if __name__=='__main__':
    from context_budget_run import PYTHON,CACHE
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--expected-candidate',required=True);p.add_argument('--cache',type=Path,default=CACHE);p.add_argument('--python',type=Path,default=PYTHON);a=p.parse_args()
    try:r=verify(a.bundle,a.expected_candidate,a.cache,a.python)
    except Exception as exc:r={'status':'BLOCKED','reason':str(exc)}
    print(json.dumps(r));sys.exit(0 if r['status']=='PASS' else 1)
