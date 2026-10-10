"""Resealed author component adversaries; never mutate the collected bundle.

Recompute the verdict after each mutation so stale summaries/hashes cannot be the
reason for rejection. Run the formal CLI and retain its exact output and reason.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import checkpoint_negatives as shared
from context_budget_gate import evaluate, verify
from context_budget_run import ROOT, PYTHON, CACHE


def write(path,value):
    temp=path.with_name(path.name+'.component-temp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    os.replace(temp,path)  # Copy trees may contain hardlinks to originals.


def seal(directory,candidate):
    files=[{'path':p.relative_to(directory).as_posix(),'bytes':p.stat().st_size,'sha256':shared.digest(p.read_bytes())} for p in sorted(directory.rglob('*')) if p.is_file() and p.name!='manifest.json']
    write(directory/'manifest.json',{'candidate_commit':candidate,'source_archive_sha256':shared.digest((directory/'source.tar').read_bytes()),'files':files})


def sync(directory,raw):
    browser=raw['browser'];write(directory/'browser.json',browser)
    for filename,rows in [('browser-requests.jsonl',browser['network']),('click-trace.jsonl',browser['clicks'])]:
        temp=directory/(filename+'.component-temp');temp.write_text(''.join(json.dumps(r)+'\n' for r in rows));os.replace(temp,directory/filename)
    for ui in browser['records']:
        for o in ui['observations']:write(directory/o['dom_file'],o['dom'])
    for c in raw['cases']:
        for field in ('operation','checkpoint_operation','replay','admission'):
            op=c.get(field)
            if op:write(directory/op['operation_receipt'],op)
    write(directory/'raw-result.json',raw)


def observation(raw,ident='CB02',index=0):
    return next(r for r in raw['browser']['records'] if r['id']==ident)['observations'][index]


def main(args):
    original=args.bundle.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    baseline=verify(original,args.expected_candidate,args.cache,args.python)
    if baseline['status']!='PASS':raise RuntimeError('positive control not PASS')
    hashes={p.relative_to(original).as_posix():shared.digest(p.read_bytes()) for p in original.rglob('*') if p.is_file()}
    definitions=[
        ('missing_refresh_captures','delete all six refresh DOM/PNG', 'capture'),
        ('missing_all_captures','delete all twenty DOM/PNG', 'capture'),
        ('missing_all_DB','delete all eighteen DB backups', 'database'),
        ('refresh_reuses_read','point refresh roles to read artifacts', 'role'),
        ('suppress_refresh_role','change scene definition to omit its mandatory refresh', 'role'),
        ('false_omission_count','show zero omitted items instead of one', 'display'),
        ('false_omission_section','alter the visible omission section', 'display'),
        ('false_omission_tokens','change visible standalone token count', 'display'),
        ('false_omission_source','point omission source at the command Event', 'display'),
        ('false_injection_success','append injection success to the boundary', 'display'),
        ('false_budget_count','change exact visible pack count', 'display'),
        ('wrong_rendered_DOM','replace captured rendered body', 'display'),
        ('wrong_source_link','point source Event link at command Event', 'identity'),
        ('wrong_created_at','change visible creation timestamp', 'identity'),
        ('refusal_old_object_residue','mix prior successful Task into 404 text', 'absence'),
        ('borrow_true_pack_other_scene','use real CB01 fixture/pack for CB02', 'scene'),
        ('refresh_before_click','move refresh request before actual click', 'window'),
        ('missing_actual_audit','remove one browser audit from physical DB', 'audit'),
        ('wire_DOM_agree_DB_differs','synchronously alter projection in wire/DOM, keep DB correct', 'database'),
    ]
    records=[]
    with tempfile.TemporaryDirectory(prefix='p6-context-budget-adversaries-') as temp:
        for ident,description,family in definitions:
            directory=Path(temp)/ident;shutil.copytree(original,directory,copy_function=os.link)
            raw=json.loads((directory/'raw-result.json').read_text())
            dom=observation(raw)['dom'];budget=dom['budget']
            if ident=='refresh_reuses_read':
                for ui in raw['browser']['records']:
                    if len(ui['observations'])==2:
                        for k in ('dom_file','screenshot'):ui['observations'][1][k]=ui['observations'][0][k]
            elif ident=='suppress_refresh_role':raw['cases'][1]['browser_definition']['refresh']=False
            elif ident=='false_omission_count':budget['omissions']=budget['omissions'].replace('省略 1 项','省略 0 项')
            elif ident=='false_omission_section':budget['rows'][0]['text']=budget['rows'][0]['text'].replace('RESUME','AUTHORITY')
            elif ident=='false_omission_tokens':budget['rows'][0]['text']=budget['rows'][0]['text'].replace('standalone_tokens = ','standalone_tokens = 0')
            elif ident=='false_omission_source':budget['rows'][0]['links'][0]['href']='#/event/'+raw['cases'][1]['pack']['command_event_id']
            elif ident=='false_injection_success':budget['boundary']+=' 已实际注入成功。'
            elif ident=='false_budget_count':budget['count']=budget['count'].replace(str(raw['cases'][1]['pack']['token_count']), '0',1)
            elif ident=='wrong_rendered_DOM':dom['rawtexts'][0]['text']='WRONG CONTENT'
            elif ident=='wrong_source_link':
                next(x for x in dom['links'] if x['text'].startswith('≡source Event'))['href']='#/event/'+raw['cases'][1]['pack']['command_event_id']
            elif ident=='wrong_created_at':next(x for x in dom['kv'] if x['label']=='created_at')['text']='2000-01-01 00:00:00.000Z'
            elif ident=='refusal_old_object_residue':observation(raw,'CB09')['dom']['text']+=' '+raw['cases'][0]['pack']['task_id']
            elif ident=='borrow_true_pack_other_scene':
                for field in ('fixture','pack','operation','lineage'):raw['cases'][1][field]=copy.deepcopy(raw['cases'][0][field])
            elif ident=='refresh_before_click':
                ui=next(r for r in raw['browser']['records'] if r['id']=='CB02')
                target=[n for n in ui['network'] if n['path']==raw['cases'][1]['browser_definition']['path']][1]
                for rows in (ui['network'],raw['browser']['network']):
                    next(n for n in rows if n.get('request_id')==target['request_id'])['at_unix']=ui['navigation']['at_unix']-1
            elif ident=='wire_DOM_agree_DB_differs':
                target_id=raw['cases'][1]['pack']['context_pack_id']
                def alter(value):
                    if isinstance(value,dict):
                        if value.get('context_pack_id')==target_id and 'projection' in value:value['projection']['task']['description']='WRONG synchronized wire/DOM description'
                        if value.get('label')=='projection' and 'text' in value:
                            projection=json.loads(value['text'])
                            if projection['task_id']==raw['cases'][1]['fixture']['task']['task_id']:
                                projection['task']['description']='WRONG synchronized wire/DOM description';value['text']=json.dumps(projection,ensure_ascii=False,indent=2)
                        for child in value.values():alter(child)
                    elif isinstance(value,list):
                        for child in value:alter(child)
                alter(raw);calls=json.loads((directory/'http-calls.json').read_text());alter(calls);write(directory/'http-calls.json',calls)
            sync(directory,raw)
            if ident.startswith('missing_') and ident!='missing_actual_audit':
                patterns={'missing_refresh_captures':['dom/*-refresh.json','screenshots/*-refresh.png'],'missing_all_captures':['dom/*.json','screenshots/*.png'],'missing_all_DB':['**/*.db']}[ident]
                for pattern in patterns:
                    for path in directory.glob(pattern):path.unlink()
            if ident=='missing_actual_audit':
                db=directory/'after-browser.db';temp_db=db.with_suffix('.component-temp');shutil.copyfile(db,temp_db);os.replace(temp_db,db)
                request=next(r for r in raw['browser']['records'] if r['id']=='CB02')['observations'][0]['request_ids'][0]
                con=sqlite3.connect(db)
                try:
                    # Explicit evidence-corruption component, only in the copied DB.
                    # The real Core append-only guard is left intact everywhere else.
                    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'").fetchall():
                        con.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
                    con.execute('DELETE FROM audit_log WHERE request_id=?',(request,));con.commit()
                finally:con.close()
            changed=evaluate(directory,args.cache,args.python);write(directory/'result.json',changed);seal(directory,args.expected_candidate)
            cli=subprocess.run([str(args.python),str(ROOT/'scripts/p6/context_budget_gate.py'),'--bundle',str(directory),'--expected-candidate',args.expected_candidate,'--cache',str(args.cache),'--python',str(args.python)],text=True,capture_output=True,timeout=90)
            verdict=json.loads(cli.stdout);reason=verdict.get('reason') or '; '.join(r.get('reason','') for r in verdict.get('records',[]) if r['status']!='PASS')
            rejected=cli.returncode==1 and verdict['status'] in ('FAIL','BLOCKED') and reason and not cli.stderr and not any(s in reason for s in ('recomputed verdict differs','physical artifact absent/different','candidate binding differs'))
            receipt={'id':ident,'description':description,'family':family,'author_component_only':True,'CLI_exit':cli.returncode,'CLI_verdict':verdict,'stderr':cli.stderr,'resealed_content_rejection':bool(rejected)}
            write(out/(ident+'.json'),receipt);records.append({'id':ident,'rejected':bool(rejected),'reason':reason})
    unchanged=all((original/path).is_file() and shared.digest((original/path).read_bytes())==sha for path,sha in hashes.items()) and {p.relative_to(original).as_posix() for p in original.rglob('*') if p.is_file()}==set(hashes)
    result={'status':'PASS' if all(r['rejected'] for r in records) and unchanged else 'FAIL','role':'author-component-adversaries','candidate_commit':args.expected_candidate,'positive_control':baseline['status'],'rejected':sum(r['rejected'] for r in records),'total':len(records),'original_bytes_unchanged':unchanged,'records':records,'scope_acceptance_signed':False}
    write(out/'RESULT.json',result);print(json.dumps(result));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--expected-candidate',required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--cache',type=Path,default=CACHE);parser.add_argument('--python',type=Path,default=PYTHON)
    sys.exit(main(parser.parse_args()))
