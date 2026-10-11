"""Independent receipt checks using the candidate's production read/render code.

SQLite inputs are consistent, immutable evidence backups, never live databases.
PNG decoding proves a valid image/clip, not that pixels and DOM cannot be forged.
"""
import argparse
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sqlite3
import struct
import sys
import zlib


def require(ok, reason):
    if not ok: raise ValueError(reason)


def same(left,right):
    return json.dumps(left,sort_keys=True,separators=(',',':'),allow_nan=False)==json.dumps(right,sort_keys=True,separators=(',',':'),allow_nan=False)


def connection(path):
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    con.row_factory=sqlite3.Row
    return con


def credential(path, metadata):
    con=connection(path)
    try:
        key=con.execute('SELECT key_id,actor_id,scopes,revoked_at FROM api_keys WHERE key_id=?',(metadata['key_id'],)).fetchone()
        require(key is not None and key['revoked_at'] is None,'F03 missing/revoked registered key')
        actor=con.execute('SELECT * FROM actors WHERE actor_id=?',(key['actor_id'],)).fetchone()
        require(actor is not None and metadata['actor_id']==actor['actor_id'],'F03 registered actor differs')
        scopes=json.loads(key['scopes'])
        require(sorted(metadata['scopes'])==sorted(scopes),'F03 registered scopes differ')
        return dict(actor),set(scopes)
    finally:con.close()


class OwnedText(HTMLParser):
    """Exclude only historical raw/JSON leaves and input containers."""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self):
        super().__init__();self.stack=[];self.parts=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs);classes=set(attrs.get('class','').split())
        ignored=bool(classes & {'rawtext','jsonblock'}) or tag in {'input','textarea','script','style'}
        if tag not in self.VOID:self.stack.append((tag,ignored or (self.stack[-1][1] if self.stack else False)))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:self.stack=self.stack[:i];break
    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:self.parts.append(data)


def product_claims(dom):
    parser=OwnedText();parser.feed(dom['html']);parser.close()
    text=' '.join(parser.parts)
    # Affirmative status grammar only, in product-owned surfaces. Historical data
    # is excluded structurally; the fixed negative disclaimer does not match.
    claims=r'(?:已经|已)\s*(?:被\s*)?(?:Agent\s*)?(?:实际)?\s*(?:接收|注入|交给\s*Agent)|注入\s*(?:成功|已执行)|可以直接使用|successfully\s+injected|Agent\s+(?:has\s+)?received'
    for match in re.finditer(claims,text,re.I):
        # Limited direct negation, scoped to this occurrence: a later affirmative
        # claim must still be rejected even if the sentence began negatively.
        prefix=text[:match.start()]
        if re.search(r'(?:尚未|还未|并未|没有|未曾|不曾|未|not)\s*(?:实际\s*)?$',prefix,re.I):continue
        raise ValueError('F02 contradictory product-owned admission/injection claim')


def png_capture(path, clip):
    data=Path(path).read_bytes();require(data[:8]==b'\x89PNG\r\n\x1a\n','F07 PNG signature missing')
    offset=8;chunks=[];payload=[];header=None;ended=False
    while offset<len(data):
        require(offset+12<=len(data),'F07 truncated PNG chunk')
        length=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8]
        require(offset+12+length<=len(data),'F07 truncated PNG payload')
        body=data[offset+8:offset+8+length];crc=struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
        require(zlib.crc32(kind+body)&0xffffffff==crc,'F07 PNG CRC mismatch')
        require(not ended,'F07 trailing data after IEND')
        if not chunks:require(kind==b'IHDR' and length==13,'F07 IHDR missing')
        if kind==b'IHDR':
            require(header is None,'F07 duplicate IHDR');header=struct.unpack('>IIBBBBB',body)
        elif kind==b'IDAT':payload.append(body)
        elif kind==b'IEND':require(length==0,'F07 invalid IEND');ended=True
        elif kind[:1].isupper() and kind!=b'PLTE':raise ValueError('F07 unsupported critical PNG chunk')
        chunks.append(kind);offset+=12+length
    require(ended and header and payload,'F07 incomplete PNG image')
    first,last=chunks.index(b'IDAT'),len(chunks)-1-chunks[::-1].index(b'IDAT')
    require(all(k==b'IDAT' for k in chunks[first:last+1]),'F07 discontinuous IDAT')
    width,height,depth,color,compression,filtering,interlace=header
    require(clip['x']==clip['y']==0 and clip['scale']==1 and clip['width']==1400 and 0<clip['height']<=16000,'F07 capture clip contract differs')
    require((width,height)==(math.ceil(clip['width']),math.ceil(clip['height'])),'F07 PNG dimensions differ from capture clip')
    require(depth==8 and color in (2,6) and compression==filtering==interlace==0,'F07 unsupported native Chrome PNG encoding')
    stride=width*(3 if color==2 else 4)+1;expected=stride*height
    decoder=zlib.decompressobj();scanlines=decoder.decompress(b''.join(payload),expected+1)
    require(len(scanlines)==expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,'F07 PNG image cannot fully decode')
    require(all(scanlines[i] in range(5) for i in range(0,expected,stride)),'F07 invalid PNG row filter')
    return width,height


def observer_witness(rows, archive, cleanup):
    require(rows and [r['seq'] for r in rows]==list(range(1,len(rows)+1)),'F05 observer sequence gap')
    require(rows[0]['kind']=='observer_started' and rows[-1]['kind']=='observer_stopped','F05 observer startup/shutdown absent')
    require(sum(r['kind']=='observer_started' for r in rows)==sum(r['kind']=='observer_stopped' for r in rows)==1,'F05 duplicated witness')
    start=rows[0];pid=start['pid']
    require(all(r['pid']==pid for r in rows) and all(a['at_unix']<=b['at_unix'] for a,b in zip(rows,rows[1:])),'F05 observer PID/time discontinuity')
    require(any(c['name']=='private-core' and c['pid']==pid and c['group_gone'] for c in cleanup),'F05 observer is not owned Core')
    import hashlib
    for field,path in [('scanner_source_sha256','src/jasmine_core/continuity_scan.py'),('continuity_source_sha256','src/jasmine_core/continuity.py')]:
        require(start[field]==hashlib.sha256((archive/path).read_bytes()).hexdigest(),'F05 observer candidate source hash differs')
    require(start['method']=='CPython profile entry/exit plus audit hook for every continuity scanner Popen attempt','F05 observer method differs')
    requests={}
    for r in rows[1:-1]:
        request=r.get('request_id');require(request is not None,'F05 orphan observer activity')
        requests.setdefault(request,[]).append(r)
    for window in requests.values():
        require(window[0]['kind']=='request_start' and window[-1]['kind']=='request_end' and sum(r['kind']=='request_start' for r in window)==sum(r['kind']=='request_end' for r in window)==1,'F05 incomplete request window')
        entered=sum(r['kind']=='scanner_call_enter' for r in window);exited=sum(r['kind']=='scanner_call_exit' for r in window);spawned=sum(r['kind']=='scanner_popen_attempt' for r in window)
        require(entered==exited==spawned,'F05 incomplete scanner enter/exit/Popen witness')
    require(any(r['kind']=='scanner_call_enter' for r in rows),'F05 scanner positive control absent')
    return start


def scalar_columns(row, pack):
    for key,value in row.items():
        if key=='full_result_json':require(same(json.loads(value),pack),'F01 stored full_result differs')
        elif key=='projection_version':require(same(value,pack['projection']['snapshot_schema']),'F01 projection version column differs')
        elif key=='selector_checkpoint_id':require(same(value,pack['selector']['latest_checkpoint_id']),'F01 selector column differs')
        elif key=='selector_event_seq':require(same(value,pack['selector']['selection_event_seq']),'F01 selector sequence column differs')
        elif key.endswith('_json'):require(same(json.loads(value),pack[key[:-5]]),'F01 Context JSON column differs '+key)
        else:require(same(value,pack[key]),'F01 Context scalar column differs '+key)


def creation_relation(before, after, item, response, payload, actor, checkpoint):
    """One complete production creation contract, shared by Context/Checkpoint."""
    from jasmine_core import SCHEMA_VERSION, clock, ids
    from jasmine_core.canonical import sha256_hex, canonical_json
    from jasmine_core.models import NewEvent
    body=item['body'];http=item['http']
    kind='checkpoint' if checkpoint else 'context'
    column=kind+'_id' if checkpoint else 'context_pack_id'
    table='checkpoint_request_keys' if checkpoint else 'context_request_keys'
    identity={'actor_id':actor['actor_id'],'actor_kind':actor['kind'],'host_id':body['host_id'],
              'project_id':response['projection']['project_id'],'task_id':body['task_id'],
              'session_id':body.get('session_id')}
    event=after.execute('SELECT * FROM events WHERE event_id=?',(response['command_event_id'],)).fetchone()
    require(event is not None,'creation command absent')
    require(ids.is_id(response[column],'ckp' if checkpoint else 'ctx') and
            ids.is_id(event['event_id'],'evt'),'creation identifier shape differs')
    source='core-continuity' if checkpoint else 'core-context'
    typ='checkpoint.created' if checkpoint else 'context.built'
    require(set(event.keys())=={'event_id','seq','schema_version','event_type','source_system',
            'source_event_id','occurred_at','recorded_at','actor_id','actor_kind','host_id',
            'session_id','project_id','task_id','payload_json','body_sha256'},
            'creation command envelope shape differs')
    require(event['schema_version']==SCHEMA_VERSION and event['event_type']==typ and
            event['source_system']==source and event['source_event_id'] is None and
            canonical_json(json.loads(event['payload_json']))==canonical_json(payload),'creation command envelope/source/payload differs')
    for key,value in identity.items():
        require(event[key]==value,'creation registered command identity differs '+key)
        if key!='actor_kind':require(response[key]==value,'creation response/request identity differs '+key)
    start=http['started_at_unix'];end=http['finished_at_unix']
    created=clock.parse_rfc3339(response['created_at']);occurred=clock.parse_rfc3339(event['occurred_at'])
    recorded=clock.parse_rfc3339(event['recorded_at'])
    require(start<=created.timestamp()<=occurred.timestamp()<=recorded.timestamp()<=end,
            'creation command/result outside actual request window')
    require(event['seq']==before.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0],
            'creation command sequence differs from before snapshot')
    spec=NewEvent(event_type=typ,source_system=source,payload=payload,occurred_at=occurred,
                  event_id=event['event_id'],**identity)
    require(event['body_sha256']==spec.digest(),'creation command production body digest differs')
    hashed=sha256_hex({'body':body,'task_id':body['task_id']}) if checkpoint else sha256_hex(body)
    expected={'actor_id':actor['actor_id'],'idempotency_key':body['idempotency_key'],
              'request_hash':hashed,column:response[column]}
    previous=[dict(r) for r in before.execute('SELECT * FROM '+table)]
    current=[dict(r) for r in after.execute('SELECT * FROM '+table)]
    require(all(r in current for r in previous) and
            [r for r in current if r not in previous]==[expected],
            'creation request key/actor/hash/result mapping differs')


def original_source(before, item, actor):
    from jasmine_core import SCHEMA_VERSION
    from jasmine_core.continuity_source import source_for_task
    body=item['body']
    caller,source,task=source_for_task(before,source_event_id=body['source_event_id'],
        task_id=body['task_id'],host_id=body['host_id'],actor_id=actor['actor_id'],
        session_id=body.get('session_id'),current_step_id=body.get('current_step_id'),schema_version=SCHEMA_VERSION)
    require(caller==actor and actor['home_host_id']==body['host_id'],
            'creation registered caller/request host differs')
    return source,task


def production_check(item):
    from jasmine_core.task_snapshot import read_task_snapshot
    from jasmine_core.continuity import ContinuityStore,compare_checkpoint
    from jasmine_core.context.renderer import render,RenderOverflow
    from jasmine_core.context.tokens import Tokenizer
    from jasmine_core.canonical import sha256_hex
    before=connection(item['before']);after=connection(item['after'])
    try:
        body=item['body'];snapshot=read_task_snapshot(before,body['task_id'],session_id=body.get('session_id'))
        cp_id=snapshot['auxiliary']['latest_checkpoint_id'];store=ContinuityStore(before,schema_version=12)
        cp=store.get_checkpoint(cp_id) if cp_id else None
        tokenizer=Tokenizer(cache_dir=item['cache']);pack=item.get('pack')
        if item['kind']=='checkpoint':
            response=item['checkpoint'];row=after.execute('SELECT * FROM checkpoints WHERE checkpoint_id=?',(response['checkpoint_id'],)).fetchone()
            require(row is not None and same(ContinuityStore(after,schema_version=12).get_checkpoint(response['checkpoint_id']),response),'F04 Checkpoint response differs from actual DB')
            require(response['note']==body.get('note') and response['source_event_id']==body['source_event_id'] and
                    response['reason']==body['reason'],'F04 Checkpoint note/reason/request/source differs')
            require(same(response['projection'],snapshot['truth_projection']) and response['full_context_digest']==snapshot['truth_digest']==response['projection_digest'],'F04 Checkpoint projection differs from build-before Truth')
            event=after.execute('SELECT * FROM events WHERE event_id=?',(response['command_event_id'],)).fetchone()
            actor,_=credential(item['after'],item['http'])
            source,task=original_source(before,item,actor)
            from jasmine_core.continuity_source import source_for_task,verified_transition
            provenance={'level':'UNVERIFIED_REQUEST','source_event_id':source['event_id']}
            if body['reason']=='STEP_VERIFIED':provenance=verified_transition(before,source,body.get('current_step_id'))
            require(same(response['trigger_provenance'],provenance),'F04 original trigger provenance differs')
            if body.get('note'):
                source_for_task(before,source_event_id=body['note']['source_event_id'],task_id=body['task_id'],
                    host_id=body['host_id'],actor_id=actor['actor_id'],schema_version=12)
            require(response['projection_version']==snapshot['truth_projection']['snapshot_schema'] and
                    response['task_revision']==task['revision'] and
                    response['workspace_digest']==sha256_hex(response['workspace']),
                    'F04 Checkpoint row revision/version/workspace digest differs')
            expected={'text':'','checkpoint_id':response['checkpoint_id'],'truth_digest':snapshot['truth_digest'],'reason':body['reason'],'trigger_provenance':provenance,'source_event_id':body['source_event_id']}
            require(event is not None and event['event_type']=='checkpoint.created' and json.loads(event['payload_json'])==expected and event['actor_id']==response['actor_id'],'F04 Checkpoint command differs')
            require(source is not None,'F04 original source absent')
            expected_identity={'actor_id':actor['actor_id'],'host_id':body['host_id'],'project_id':snapshot['truth_projection']['project_id'],'task_id':body['task_id'],'session_id':body.get('session_id')}
            require(actor['home_host_id']==body['host_id'],'F04 registered actor/request host differs')
            for key,value in expected_identity.items():
                require(response[key]==row[key]==event[key]==value,'F04 Checkpoint request/command identity differs '+key)
            for key in ('host_id','project_id','task_id','session_id'):
                require(source[key]==expected_identity[key],'F04 original source/request identity differs '+key)
            creation_relation(before,after,item,response,expected,actor,True)
            return
        workspace=pack['workspace'] if pack else {};comparison=compare_checkpoint(cp,snapshot['truth_projection'],workspace)
        if pack:
            actor,_=credential(item['after'],item['http']);source,task=original_source(before,item,actor)
            require(pack['source_event_id']==source['event_id'] and pack['reason']==body['reason'] and
                    pack['current_step_id']==body.get('current_step_id') and
                    pack['trigger_provenance']=={'level':'UNVERIFIED_REQUEST','source_event_id':source['event_id']},
                    'F01 Context original request/provenance differs')
            require(same(pack['projection'],snapshot['truth_projection']) and pack['truth_digest']==snapshot['truth_digest'],'F01 pack differs from original Authority/build-before Truth')
            require(same(pack['selector'],snapshot['auxiliary']) and same(pack['resume_comparison'],comparison),'F04 selected Checkpoint/comparison differs')
            require(pack['workspace_digest']==sha256_hex(workspace) and pack['config_digest']==sha256_hex(pack['configuration']),'F01 workspace/config digest differs')
            rendering=render(snapshot['truth_projection'],comparison,cp,pack['memory'],tokenizer,source_event_id=body['source_event_id'],current_step_id=body.get('current_step_id'))
            require(all(same(pack[k],v) for k,v in rendering.items()),'F04/F01 production render/note omission differs')
            stored=after.execute('SELECT * FROM context_packs WHERE context_pack_id=?',(pack['context_pack_id'],)).fetchone();require(stored is not None,'F01 stored pack absent');scalar_columns(dict(stored),pack)
            event=after.execute('SELECT * FROM events WHERE event_id=?',(pack['command_event_id'],)).fetchone()
            payload={'text':'','context_pack_id':pack['context_pack_id'],'source_event_id':pack['source_event_id'],'truth_digest':pack['truth_digest'],'rendered_sha256':pack['rendered_sha256'],'config_digest':pack['config_digest'],'token_count':pack['token_count']}
            require(event is not None and event['event_type']=='context.built' and json.loads(event['payload_json'])==payload,'F01 immutable Context command digest/count differs')
            for key in ('actor_id','host_id','project_id','task_id','session_id'):require(event[key]==pack[key],'F01 Context command identity differs')
            creation_relation(before,after,item,pack,payload,actor,False)
        else:
            try:render(snapshot['truth_projection'],comparison,cp,{'status':'not_configured','selected':[]},tokenizer,source_event_id=body['source_event_id'])
            except RenderOverflow as exc:require(exc.code==item['error']['code'] and exc.details==item['error']['details'],'F01 mandatory overflow differs from actual Authority')
            else:raise ValueError('F01 claimed mandatory overflow renders successfully')
    finally:before.close();after.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);args=parser.parse_args();sys.path.insert(0,args.source)
    results={}
    for item in json.load(sys.stdin):
        try:production_check(item);results[item['id']]={'matches':True}
        except Exception as exc:results[item['id']]={'matches':False,'reason':type(exc).__name__+': '+str(exc)}
    print(json.dumps(results))
