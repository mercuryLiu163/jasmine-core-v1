"""Rebase the eight reviewed mutations plus extensions onto a new clean run.

These are explicitly corrupted evidence COPIES. The actual Core, immutable
original receipts, Authority and deployment are never modified by this script.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parent))
import context_budget_adversaries as a
import context_budget_gate as gate
from context_budget_run import ROOT,PYTHON,CACHE


ORIGINAL_EIGHT=[
    ('mandatory_rule_dropped_consistent_pack','F01'),
    ('positive_page_extra_injection_claim','F02'),
    ('operation_actor_does_not_match_audit','F03'),
    ('browser_audit_wrong_method','F03'),
    ('checkpoint_note_response_does_not_match_DB','F04'),
    ('observer_attestation_removed','F05'),
    ('proxy_pending_HTTP_200','F06'),
    ('PNG_signature_without_image','F07'),
]
EXTENSIONS=[
    ('command_digest_wrong','F01'),('command_count_wrong','F01'),('Context_column_JSON_diverge','F01'),
    ('extra_claim_refresh_only','F02'),('extra_success_summary','F02'),
    ('browser_refresh_audit_wrong_method','F03'),('key_scope_forgery','F03'),
    ('wrong_selected_checkpoint','F04'),('wrong_note_source','F04'),('wrong_omission_token_count','F04'),
    ('observer_sequence_gap','F05'),('observer_code_hash_wrong','F05'),('observer_stop_absent','F05'),
    ('PNG_truncated','F07'),('PNG_wrong_dimensions','F07'),
]


RESIDUALS = [
    ('metadata_claim_'+phase,'R01') for phase in ('first','refresh','both')
] + [
    ('admin_phase_'+role+'_'+phase,'R02') for role in ('operation','browser')
    for phase in ('unknown','missing','other_valid')
] + [
    ('checkpoint_identity_'+role+'_'+field,'R03') for role in ('response','command')
    for field in ('host_id','actor_id','project_id','task_id','session_id')
] + [('negative_then_affirmative','R04')]
POSITIVES = [('honest_direct_negative','R04'),('historical_raw_claim','R01/R04')]


def recurse(value, fn):
    if isinstance(value,dict):
        fn(value)
        for child in list(value.values()):recurse(child,fn)
    elif isinstance(value,list):
        for child in value:recurse(child,fn)


def change_DB(directory,table,where,args,updates):
    for path in directory.rglob('*.db'):
        con=sqlite3.connect(path)
        try:
            if not con.execute('SELECT 1 FROM '+table+' WHERE '+where,args).fetchone():continue
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchall():con.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
            con.execute('UPDATE '+table+' SET '+','.join('"'+key+'"=?' for key in updates)+' WHERE '+where,[*updates.values(),*args]);con.commit()
        finally:con.close()


def mutation(directory,raw,calls,ident,cache):
    cb1=raw['cases'][0];cb2=raw['cases'][1]
    if ident.startswith('metadata_claim_') or ident in ('honest_direct_negative','historical_raw_claim','negative_then_affirmative'):
        observations=raw['browser']['records'][0]['observations']
        selected=observations[:1] if ident=='metadata_claim_first' else observations[1:] if ident=='metadata_claim_refresh' else observations
        for o in selected:
            dom=o['dom']
            if ident.startswith('metadata_claim_'):
                claim='已实际注入成功';dom['kv'].append({'label':'注入状态','text':claim})
                html='<dl class="kv"><dt>注入状态</dt><dd>'+claim+'</dd></dl>'
            elif ident=='historical_raw_claim':
                claim='来源原文：注入成功';html='<div class="rawtext"><pre>'+claim+'</pre></div>'
            else:
                claim='本页尚未注入成功。'+('但此包已经被 Agent 实际接收。' if ident=='negative_then_affirmative' else '')
                html='<p>'+claim+'</p>'
            dom['html']+=html;dom['text']+='\n'+claim
    elif ident.startswith('admin_phase_'):
        role,phase=ident.removeprefix('admin_phase_').split('_',1)
        h=cb1['operation']['audit_read'] if role=='operation' else raw['browser_audit']['http']
        request=h['request_id'];browser=cb1['browser_definition']['key_metadata']
        def alter(v):
            if v.get('request_id')==request and 'request_body' in v:
                for field in ('actor_id','key_id','scopes'):v[field]=copy.deepcopy(browser[field])
                v['method']='POST'
                if phase=='missing':v.pop('phase',None)
                else:v['phase']='unverified-readback' if phase=='unknown' else 'source-command-readback'
        recurse(raw,alter);recurse(calls,alter)
    elif ident.startswith('checkpoint_identity_'):
        role,field=ident.removeprefix('checkpoint_identity_').split('_',1)
        cp=cb2['checkpoint_operation']['http']['body']['checkpoint'];prefix={'host_id':'hst','actor_id':'act','project_id':'prj','task_id':'tsk','session_id':'ses'}[field]
        wrong=prefix+'_'+'0'*25+'1'
        if role=='response':
            def alter(v):
                if v.get('checkpoint_id')==cp['checkpoint_id'] and ('note' in v or 'note_json' in v):v[field]=wrong
            recurse(raw,alter);recurse(calls,alter);change_DB(directory,'checkpoints','checkpoint_id=?',[cp['checkpoint_id']],{field:wrong})
        else:
            def alter(v):
                if v.get('event_id')==cp['command_event_id']:v[field]=wrong
            recurse(raw,alter);recurse(calls,alter);change_DB(directory,'events','event_id=?',[cp['command_event_id']],{field:wrong})
    elif ident=='mandatory_rule_dropped_consistent_pack':
        from jasmine_core.context.renderer import render
        from jasmine_core.context.tokens import Tokenizer
        from jasmine_core.canonical import canonical_json,sha256_hex
        old=copy.deepcopy(cb1['pack']);pack=copy.deepcopy(old);pack['projection']['active_rules']=[]
        pack['truth_digest']=sha256_hex(canonical_json(pack['projection']))
        pack.update(render(pack['projection'],pack['resume_comparison'],None,pack['memory'],Tokenizer(cache_dir=cache),source_event_id=pack['source_event_id'],current_step_id=pack['current_step_id']))
        updates={'full_result_json':canonical_json(pack),'projection_json':canonical_json(pack['projection']),**{k:pack[k] for k in ('truth_digest','rendered_content','rendered_sha256','rendered_utf8_bytes','token_count')},'section_metrics_json':canonical_json(pack['section_metrics']),'omissions_json':canonical_json(pack['omissions'])}
        def alter(value):
            if value.get('context_pack_id')==pack['context_pack_id'] and 'rendered_content' in value:value.update(updates if 'full_result_json' in value else copy.deepcopy(pack))
        recurse(raw,alter);recurse(calls,alter);change_DB(directory,'context_packs','context_pack_id=?',[pack['context_pack_id']],updates)
        for ui in raw['browser']['records']:
            for o in ui['observations']:
                dom=o['dom']
                if not dom['budget'] or not any(x['label']=='context_pack_id' and x['text']==pack['context_pack_id'] for x in dom['kv']):continue
                dom['budget']['count']=dom['budget']['count'].replace(str(old['token_count'])+' / 2500',str(pack['token_count'])+' / 2500')
                for d in dom['digests']:
                    if d['label'] in ('truth_digest','rendered_sha256'):d['value']=pack[d['label']]
                for text in dom['rawtexts']:text['text']=pack['rendered_content']
                for block in dom['jsonblocks']:
                    field=block['label'].split('(')[0]
                    if field in ('projection','section_metrics'):block['text']=json.dumps(pack[field],ensure_ascii=False,indent=2)
                for field in ('html','text'):
                    dom[field]=dom[field].replace(old['rendered_content'],pack['rendered_content']).replace(old['rendered_sha256'],pack['rendered_sha256']).replace(old['truth_digest'],pack['truth_digest'])
    elif ident in ('positive_page_extra_injection_claim','extra_claim_refresh_only','extra_success_summary'):
        observations=raw['browser']['records'][0]['observations'];selected=observations[1:] if ident=='extra_claim_refresh_only' else observations
        for o in selected:
            dom=o['dom'];claim='此包已经被 Agent 实际接收，可以直接使用。'
            if ident!='extra_success_summary':dom['alerts'].append(claim)
            dom['text']+='\n'+claim;dom['html']+=('<summary>' if ident=='extra_success_summary' else '<div role="alert">')+claim+('</summary>' if ident=='extra_success_summary' else '</div>')
    elif ident=='operation_actor_does_not_match_audit':
        request=cb1['operation']['http']['request_id']
        def alter(v):
            if v.get('request_id')==request and 'request_body' in v:v['actor_id']=cb1['fixture']['host'].replace('hst_','act_')
        recurse(raw,alter);recurse(calls,alter)
    elif ident in ('browser_audit_wrong_method','browser_refresh_audit_wrong_method'):
        request=raw['browser']['records'][0]['observations'][1 if ident.startswith('browser_refresh') else 0]['request_ids'][-1]
        change_DB(directory,'audit_log','request_id=?',[request],{'method':'POST'})
        def alter(v):
            if v.get('request_id')==request and 'detail' in v:v['method']='POST'
        recurse(raw,alter);recurse(calls,alter)
    elif ident=='key_scope_forgery':cb1['browser_definition']['key_metadata']['scopes'].append('admin')
    elif ident in ('checkpoint_note_response_does_not_match_DB','wrong_note_source'):
        request=cb2['checkpoint_operation']['http']['request_id']
        def alter(v):
            if v.get('request_id')==request and 'request_body' in v:
                note=v['body']['checkpoint']['note'];note['text' if ident.startswith('checkpoint_note') else 'source_event_id']='SYNTHETIC wrong short note; not stored.' if ident.startswith('checkpoint_note') else cb1['fixture']['source']['event_id']
        recurse(raw,alter);recurse(calls,alter)
    elif ident=='wrong_selected_checkpoint':cb2['pack']['selector']['latest_checkpoint_id']='ckp_'+'0'*25+'1'
    elif ident=='wrong_omission_token_count':cb2['pack']['omissions'][0]['standalone_tokens']+=1
    elif ident.startswith('observer_'):
        rows=[json.loads(s) for s in (directory/'observer.jsonl').read_text().splitlines()]
        if ident=='observer_attestation_removed':
            rows=[r for r in rows if r['kind']!='observer_started']
            for c in raw['cases']:
                for field in ('operation','checkpoint_operation','replay','admission'):
                    if c.get(field):c[field].pop('observer_started',None)
        elif ident=='observer_sequence_gap':rows[3]['seq']+=1
        elif ident=='observer_code_hash_wrong':rows[0]['scanner_source_sha256']='0'*64
        elif ident=='observer_stop_absent':rows=rows[:-1]
        (directory/'observer.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    elif ident=='proxy_pending_HTTP_200':
        request=next(n for u in raw['browser']['records'] if u['id']=='CB07' for n in u['network'] if n['path'].startswith('/v1/context/'))['request_id']
        def alter(v):
            if v.get('request_id')==request and 'authorization_present' in v:v['status']=200
        recurse(raw,alter)
    elif ident.startswith('PNG_'):
        for path in (directory/'screenshots').glob('*.png'):
            data=path.read_bytes()
            if ident=='PNG_signature_without_image':data=data[:8]
            elif ident=='PNG_truncated':data=data[:-20]
            else:
                image=bytearray(data);width=struct.unpack('>I',image[16:20])[0];image[16:20]=struct.pack('>I',width-1);image[29:33]=struct.pack('>I',zlib.crc32(image[12:29])&0xffffffff);data=bytes(image)
            path.write_bytes(data)
    elif ident.startswith('command_'):
        event=cb1['pack']['command_event_id'];row=next(e for e in raw['before_browser']['events'] if e['event_id']==event);payload=json.loads(row['payload_json']);payload['token_count' if ident=='command_count_wrong' else 'rendered_sha256']=0 if ident=='command_count_wrong' else '0'*64;encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        def alter(v):
            if v.get('event_id')==event:
                if 'payload_json' in v:v['payload_json']=encoded
                elif 'payload' in v:v['payload']=copy.deepcopy(payload)
        recurse(raw,alter);recurse(calls,alter);change_DB(directory,'events','event_id=?',[event],{'payload_json':encoded})
    elif ident=='Context_column_JSON_diverge':
        context=cb1['pack']['context_pack_id'];change_DB(directory,'context_packs','context_pack_id=?',[context],{'token_count':0})
        def alter(v):
            if v.get('context_pack_id')==context and 'full_result_json' in v:v['token_count']=0
        recurse(raw,alter)
    else:raise ValueError('unknown regression '+ident)


def main(args):
    os.umask(0o077);original=args.bundle.resolve();out=args.out.resolve();out.mkdir(exist_ok=False,parents=True)
    require=gate.require;require(gate.verify(original,args.expected_candidate,args.cache,args.python)['status']=='PASS','positive control is not PASS')
    hashes={str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()};records=[]
    sys.path.insert(0,str(ROOT/'src'))
    with tempfile.TemporaryDirectory(prefix='p6-context-review-regressions-') as temp:
        for ident,family in ORIGINAL_EIGHT+EXTENSIONS+RESIDUALS+POSITIVES:
            directory=Path(temp)/ident;shutil.copytree(original,directory)
            raw=json.loads((directory/'raw-result.json').read_text());calls=json.loads((directory/'http-calls.json').read_text())
            mutation(directory,raw,calls,ident,args.cache);a.sync(directory,raw);a.write(directory/'http-calls.json',calls)
            a.write(directory/'NOT-AN-ACCEPTANCE-RUN.json',{'component_only':True,'mutation':ident,'family':family,'original_review_mutation':ident in dict(ORIGINAL_EIGHT),'candidate_commit':args.expected_candidate})
            a.write(directory/'result.json',gate.evaluate(directory,args.cache,args.python));a.seal(directory,args.expected_candidate)
            cli=subprocess.run([str(args.python),str(ROOT/'scripts/p6/context_budget_gate.py'),'--bundle',str(directory),'--expected-candidate',args.expected_candidate],capture_output=True,text=True,timeout=90)
            verdict=json.loads(cli.stdout);reason=verdict.get('reason') or '; '.join(r.get('reason','') for r in verdict.get('records',[]) if r['status']!='PASS')
            positive=ident in dict(POSITIVES)
            rejected=cli.returncode==1 and verdict['status'] in ('FAIL','BLOCKED') and bool(reason) and not cli.stderr and not any(s in reason for s in ('recomputed verdict differs','physical artifact absent/different','candidate binding differs'))
            a.write(out/(ident+'.json'),{'id':ident,'family':family,'original_review_mutation':ident in dict(ORIGINAL_EIGHT),'CLI_exit':cli.returncode,'CLI_verdict':verdict,'stderr':cli.stderr,'rejected':rejected,'positive_control':positive,'expected_outcome_met':(cli.returncode==0 and verdict['status']=='PASS' and not cli.stderr) if positive else rejected,'reason':reason,'component_only':True});records.append({'id':ident,'family':family,'rejected':rejected,'positive_control':positive,'expected_outcome_met':(cli.returncode==0 and verdict['status']=='PASS' and not cli.stderr) if positive else rejected,'reason':reason})
    unchanged={str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}==hashes
    result={'candidate_commit':args.expected_candidate,'role':'author-review-regressions','status':'PASS' if unchanged and all(r['expected_outcome_met'] for r in records) else 'FAIL','original_eight_rejected':sum(r['rejected'] for r in records[:8]),'rejected':sum(r['rejected'] for r in records),'total':sum(not r['positive_control'] for r in records),'positive_controls_passed':sum(r['expected_outcome_met'] for r in records if r['positive_control']),'positive_controls':len(POSITIVES),'original_bytes_unchanged':unchanged,'records':records,'scope_acceptance_signed':False}
    a.write(out/'RESULT.json',result);print(json.dumps(result));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--expected-candidate',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--cache',type=Path,default=CACHE);p.add_argument('--python',type=Path,default=PYTHON);sys.exit(main(p.parse_args()))
