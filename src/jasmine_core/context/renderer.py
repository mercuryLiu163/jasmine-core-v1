"""Deterministic mandatory-first render with whole-pack selected-encoding budget."""
from .. import errors
from ..canonical import canonical_json, sha256_hex
from .tokens import MAX_TOKENS

RENDERER_VERSION='jasmine.context.v1'
EVIDENCE_COLUMNS=('evidence_id','kind','status','task_revision','step_revision',
                  'source_event_id','change_event_id','fingerprint_sha256')


class RenderOverflow(errors.CoreError):
    status=409
    def __init__(self,code,**details):
        super().__init__(code,**details);self.code=code


def _rule(rule):
    meta={k:rule.get(k) for k in ('rule_id','version','revision','scope','kind','severity',
            'enforcement','matcher','verification_requirements')}
    meta['origin_event_id']=rule.get('origin_event_id')
    return canonical_json(meta)+'\n'+rule['content']+'\n'


def _evidence_refs(refs,tokenizer):
    # Keep the original representation unless the explicit reversible table
    # has identical fields/types and actually saves selected-encoding tokens.
    if any(set(ref)!=set(EVIDENCE_COLUMNS) or
           not isinstance(ref['fingerprint_sha256'],str) for ref in refs):
        return refs
    fingerprints=[]
    rows=[]
    for ref in refs:
        fingerprint=ref['fingerprint_sha256']
        if fingerprint not in fingerprints:fingerprints.append(fingerprint)
        rows.append([fingerprints.index(fingerprint) if key=='fingerprint_sha256'
                     else ref[key] for key in EVIDENCE_COLUMNS])
    table={'format':'columns-rows.dictionary-columns.v1',
           'columns':EVIDENCE_COLUMNS,'rows':rows,
           'dictionary_columns':{'fingerprint_sha256':fingerprints}}
    return table if tokenizer.count(canonical_json(table))<tokenizer.count(canonical_json(refs)) else refs


def render(projection,comparison,checkpoint,memory,tokenizer,*,source_event_id,current_step_id=None):
    rules=sorted(projection['active_rules'],key=lambda r:(r['scope']['kind'],r['rule_id']))
    header='JASMINE CONTEXT v1\n'+canonical_json({
        'project_id':projection['project_id'],'task_id':projection['task_id'],
        'task_revision':projection['task']['revision'],'source_event_id':source_event_id,
        'precedence':'CURRENT TRUTH > CHECKPOINT > NON-AUTHORITATIVE MEMORY',
        'tokenizer_qualification':tokenizer.identity['qualification'],
        'reconciliation_required':comparison['reconciliation_required']})+'\n'
    hard='AUTHORITY\n'+''.join(_rule(r) for r in rules if r['severity']=='HARD')
    if tokenizer.count(header+hard)>MAX_TOKENS:
        raise RenderOverflow('AUTHORITY_TOO_LARGE',maximum_tokens=MAX_TOKENS,
                             mandatory_tokens=tokenizer.count(header+hard))
    state={'task':{k:projection['task'].get(k) for k in ('task_id','title','description','status','revision','acceptance_criteria')},
        'current_step_id':current_step_id,
        'steps':[{k:s.get(k) for k in ('step_id','title','description','status','revision','acceptance_criteria')} for s in projection['steps']],
        'safety':'Context never verifies or accepts Evidence or State; normal Guard and Evidence gates remain required.'}
    refs=projection['evidence_refs']
    sections=[('HEADER',header),('AUTHORITY','AUTHORITY\n'+''.join(_rule(r) for r in rules)),
        ('CURRENT STATE','CURRENT STATE\n'+canonical_json(state)+'\n'),
        ('RESUME','RESUME\n'+canonical_json({'checkpoint_id':checkpoint['checkpoint_id'] if checkpoint else None,
            'comparison':comparison,'checkpoint_is_historical':True})+'\n'),
        ('EVIDENCE HINTS','EVIDENCE HINTS — NOT VERIFIED BY CONTEXT\n'+canonical_json(_evidence_refs(refs,tokenizer))+'\n'),
        ('MEMORY','MEMORY — NON AUTHORITATIVE\n'+canonical_json({'status':memory['status']})+'\n')]
    mandatory=''.join(text for _,text in sections)
    if tokenizer.count(mandatory)>MAX_TOKENS:
        raise RenderOverflow('CONTEXT_MANDATORY_TOO_LARGE',maximum_tokens=MAX_TOKENS,
            mandatory_tokens=tokenizer.count(mandatory),loaded_rule_ids=[r['rule_id'] for r in rules])
    optional=[]
    # Optional allocation priority is Evidence bodies, historical note, then Memory.
    for row in sorted(projection['evidence'],key=lambda r:(r['created_at'],r['evidence_id']),reverse=True):
        optional.append(('EVIDENCE HINTS','evidence:'+row['evidence_id'],
            'OPTIONAL EVIDENCE DATA\n'+canonical_json({'evidence_id':row['evidence_id'],'result':row.get('result')})+'\n',250,[row['source_event_id']]))
    if checkpoint and checkpoint.get('note'):
        optional.append(('RESUME','note:'+checkpoint['checkpoint_id'],
            'HISTORICAL NON-AUTHORITATIVE NOTE DATA\n'+canonical_json(checkpoint['note'])+'\n',200,[checkpoint['note']['source_event_id']]))
    items=sorted(memory.get('selected',[]),key=lambda i:(i['bank']['bank_id'],i['memory_id']))
    items.sort(key=lambda i:i['recorded_at'],reverse=True)
    items.sort(key=lambda i:i['confidence'],reverse=True)
    for item in items:
        data={k:item[k] for k in ('memory_id','memory_type','bank','source_event_ids','source_task_id','fact')}
        optional.append(('MEMORY','memory:'+item['bank']['bank_id']+':'+item['memory_id'],
            'HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n'+canonical_json(data)+'\n',600,item['source_event_ids']))
    omissions=[];used={};selected=[]
    for section,ident,text,cap,source_ids in optional:
        count=tokenizer.count(text)
        candidate=[(name,content+(text if name==section else '')) for name,content in sections]
        total=tokenizer.count(''.join(content for _,content in candidate))
        if used.get(section,0)+count>cap or total>MAX_TOKENS:
            omissions.append({'section':section,'item_id':ident,'reason':'optional_budget','standalone_tokens':count,'source_ids':source_ids})
        else:
            sections=candidate;used[section]=used.get(section,0)+count;selected.append(ident)
    content=''.join(text for _,text in sections)
    if len(content.encode('utf-8'))>131072:
        raise RenderOverflow('CONTEXT_MANDATORY_TOO_LARGE',reason='rendered_utf8_cap')
    metrics=[];prefix='';offset=0;prior=0
    for name,text in sections:
        prefix+=text;cumulative=tokenizer.count(prefix);size=len(text.encode('utf-8'))
        metrics.append({'section':name,'utf8_start':offset,'utf8_end':offset+size,'sha256':sha256_hex(text),
            'standalone_tokens':tokenizer.count(text),'cumulative_prefix_tokens':cumulative,
            'prefix_delta_tokens':cumulative-prior})
        offset+=size;prior=cumulative
    return {'renderer_version':RENDERER_VERSION,'rendered_content':content,
        'rendered_sha256':sha256_hex(content),'rendered_utf8_bytes':len(content.encode('utf-8')),
        'token_count':tokenizer.count(content),'section_metrics':metrics,'omissions':omissions,
        'selected_optional_ids':selected}
