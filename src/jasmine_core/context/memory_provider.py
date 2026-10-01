"""Canonical read-only Memory slot: default off, owned worker, factual provenance."""
from __future__ import annotations

import json
import hashlib
import stat
from pathlib import Path
import math
import os
import selectors
import subprocess
import time
from datetime import datetime

from .. import errors, ids
from ..canonical import canonical_json, sha256_hex, looks_like_credential
from ..continuity_scan import _cleanup
from ..continuity_source import verified_transition
from ..events import EventStore
from ..resolution_common import fields

TYPES=('CONFIRMED_DECISION','STABLE_PROJECT_FACT','RESOLVED_ISSUE','VERIFIED_OUTCOME','FAILURE_LESSON')
MAX_BYTES=65536


def _sha(value):
    return isinstance(value,str) and len(value)==64 and not set(value)-set('0123456789abcdef')


def _text(value,limit):
    return isinstance(value,str) and 1<=len(value)<=limit


def _bank(value):
    fields(value,{'scope','bank_id','project_id'},{'scope','bank_id','project_id'})
    if value['scope']=='GLOBAL':
        if value['bank_id']!='jasmine-global' or value['project_id'] is not None:
            raise errors.InvalidRequest('invalid global Memory bank')
    elif value['scope']=='PROJECT':
        if not ids.is_id(value['project_id'],'prj') or value['bank_id']!='jasmine-project-'+value['project_id']:
            raise errors.InvalidRequest('invalid project Memory bank')
    else:raise errors.InvalidRequest('invalid Memory bank scope')
    return (value['scope'],value['bank_id'],value['project_id'])


def provenance_digest(item):
    return sha256_hex({k:item[k] for k in ('memory_type','bank','source_event_ids','source_task_id',
                'origin_rule_ref','evidence_ids','verified_transition_event_id','fact')})


def validate_result(result):
    fields(result,{'status','provider_id','provider_config_digest','banks','items','query_trace_id','error_code'},
                  {'status','provider_id','provider_config_digest','banks','items','query_trace_id','error_code'})
    if result['status'] not in ('ok','empty','degraded') or not _text(result['provider_id'],128) or not _sha(result['provider_config_digest']):
        raise errors.InvalidRequest('invalid Memory result identity')
    for name in ('query_trace_id','error_code'):
        if result[name] is not None and not _text(result[name],128):raise errors.InvalidRequest('invalid Memory trace metadata')
    if not isinstance(result['banks'],list) or len(result['banks'])>2:
        raise errors.InvalidRequest('invalid Memory banks')
    seen=set()
    for bank in result['banks']:
        fields(bank,{'scope','bank_id','project_id','status','error_code'},{'scope','bank_id','project_id','status','error_code'})
        identity=_bank({k:bank[k] for k in ('scope','bank_id','project_id')})
        if identity in seen or bank['status'] not in ('ok','empty','degraded'):
            raise errors.InvalidRequest('invalid duplicate bank or status')
        if bank['error_code'] is not None and not _text(bank['error_code'],128):raise errors.InvalidRequest('invalid bank error')
        seen.add(identity)
    if any(b['status']=='degraded' for b in result['banks']) and result['status']!='degraded':
        raise errors.InvalidRequest('partial bank failure must be degraded')
    if result['status']=='empty' and result.get('items'):
        raise errors.InvalidRequest('empty Memory result cannot contain items')
    if not isinstance(result['items'],list) or len(result['items'])>8:
        raise errors.InvalidRequest('Memory item cap exceeded')
    seenitems=set()
    required={'memory_id','memory_type','bank','source_event_ids','source_task_id','origin_rule_ref',
        'evidence_ids','backend_classification','text','confidence','verified_transition_event_id',
        'recorded_at','provenance_digest','fact'}
    for item in result['items']:
        fields(item,required,required)
        if not _text(item['memory_id'],128) or item['memory_type'] not in TYPES or not _text(item['text'],2000):
            raise errors.InvalidRequest('invalid Memory item')
        bank=_bank(item['bank']);qualified=(item['bank']['bank_id'],item['memory_id'])
        if qualified in seenitems or bank not in seen:raise errors.InvalidRequest('duplicate or undeclared Memory identity')
        if any(b['bank_id']==item['bank']['bank_id'] and b['status']=='empty' for b in result['banks']):
            raise errors.InvalidRequest('empty bank cannot contain Memory items')
        seenitems.add(qualified)
        c=item['confidence']
        if isinstance(c,bool) or not isinstance(c,(int,float)) or not 0<=c<=1 or (isinstance(c,float) and not math.isfinite(c)):
            raise errors.InvalidRequest('invalid Memory confidence')
        try:
            if datetime.fromisoformat(item['recorded_at'].replace('Z','+00:00')).tzinfo is None:
                raise ValueError('timestamp requires timezone')
        except (ValueError,TypeError,AttributeError):raise errors.InvalidRequest('invalid Memory recorded_at')
        for name,prefix,minimum in [('source_event_ids','evt',1),('evidence_ids','evd',0)]:
            values=item[name]
            if not isinstance(values,list) or not minimum<=len(values)<=8 or not all(ids.is_id(i,prefix) for i in values) or len(set(values))!=len(values):
                raise errors.InvalidRequest('invalid Memory source references')
        for name,prefix in [('source_task_id','tsk'),('verified_transition_event_id','evt')]:
            if item[name] is not None and not ids.is_id(item[name],prefix):raise errors.InvalidRequest('invalid Memory optional reference')
        if item['backend_classification'] is not None and not _text(item['backend_classification'],64):
            raise errors.InvalidRequest('invalid backend classification')
        if item['origin_rule_ref'] is not None:
            fields(item['origin_rule_ref'],{'rule_id','version'},{'rule_id','version'})
            v=item['origin_rule_ref']['version']
            if not ids.is_id(item['origin_rule_ref']['rule_id'],'rul') or type(v) is not int or not 1<=v<=2**63-1:
                raise errors.InvalidRequest('invalid Memory Rule reference')
        fact=item['fact'];fields(fact,{'fact_type','subject_event_id','evidence_id','key','value'},
                               {'fact_type','subject_event_id','evidence_id','key','value'})
        if not ids.is_id(fact['subject_event_id'],'evt') or not ids.is_id(fact['evidence_id'],'evd') or \
           fact['evidence_id'] not in item['evidence_ids'] or not _text(fact['value'],256):
            raise errors.InvalidRequest('invalid Memory fact reference')
        if fact['fact_type']=='ARTIFACT':
            if fact['key']!='artifact_sha256' or not _sha(fact['value']):raise errors.InvalidRequest('invalid artifact fact')
        elif fact['fact_type']=='EVIDENCE_RESULT':
            if fact['key'] not in ('kind','status'):raise errors.InvalidRequest('invalid Evidence fact key')
        else:raise errors.InvalidRequest('invalid Memory fact type')
        if item['memory_type']=='VERIFIED_OUTCOME':
            if item['verified_transition_event_id'] is None or item['verified_transition_event_id'] not in item['source_event_ids']:
                raise errors.InvalidRequest('verified outcome requires explicit transition source')
        elif item['memory_type']=='FAILURE_LESSON' and item['verified_transition_event_id'] is not None:
            raise errors.InvalidRequest('failure lesson cannot claim VERIFIED qualification')
        if item['provenance_digest']!=provenance_digest(item):raise errors.InvalidRequest('invalid Memory provenance digest')
    return result


class MemoryProvider:
    def __init__(self,*,argv=None,provider_id='not_configured',global_bank=False,environment=None):
        self.argv=tuple(argv) if argv is not None else None
        self.provider_id=provider_id;self.global_bank=bool(global_bank)
        if self.argv is not None:
            if len(self.argv)!=2 or not all(isinstance(a,str) and Path(a).is_absolute() for a in self.argv):
                raise ValueError('Memory worker must name absolute interpreter and script paths')
        supplied=environment or {}
        if set(supplied)-{'PYTHONPATH','LANG','LC_ALL'} or not all(isinstance(v,str) for v in supplied.values()):
            raise ValueError('Memory worker accepts only explicit safe environment fields')
        self.environment={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONHASHSEED':'0','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',**supplied}
        self.config_digest=self.configuration()['config_digest']

    @classmethod
    def from_deployment(cls):
        """Opt-in private worker configuration; absent means the original off slot."""
        name=os.environ.get('JASMINE_CORE_MEMORY_CONFIG')
        if not name:return cls()
        from ..adapter.config import private_json
        from .. import fingerprint,errors
        path=Path(name)
        if not path.is_absolute() or path.is_relative_to(fingerprint.configured_root()):
            raise errors.InvalidRequest('Memory deployment config must be outside work root')
        value,digest=private_json(path,cap=65536)
        if not isinstance(value,dict) or set(value)!={'argv','provider_id','global_bank','environment'} or type(value['global_bank']) is not bool or not isinstance(value['provider_id'],str) or not 1<=len(value['provider_id'])<=128:
            raise errors.InvalidRequest('strict Memory deployment configuration required')
        try:provider=cls(**value)
        except (ValueError,TypeError) as exc:raise errors.InvalidRequest('invalid fixed Memory worker configuration') from exc
        if provider.argv is None:raise errors.InvalidRequest('explicit Memory deployment requires worker argv')
        work=fingerprint.configured_root().resolve(strict=True)
        resolved_argv=[]
        for index,item in enumerate(provider.argv):
            original=Path(item);resolved=original.resolve(strict=True)
            if original.is_relative_to(work) or resolved.is_relative_to(work):raise errors.InvalidRequest('Memory worker must be outside model-writable work root')
            # Keep only the explicitly permitted venv interpreter launcher; its
            # canonical parent is protected. Scripts execute their pinned target.
            if index==0:
                launcher=original.parent.resolve(strict=True)/original.name
                if launcher.parent.is_relative_to(work):raise errors.InvalidRequest('Memory interpreter parent must be outside work root')
                resolved_argv.append(str(launcher))
            else:resolved_argv.append(str(resolved))
        provider.argv=tuple(resolved_argv)
        pythonpath=provider.environment.get('PYTHONPATH')
        if pythonpath is not None:
            for item in pythonpath.split(os.pathsep):
                if not item or not Path(item).is_absolute() or Path(item)!=Path(item).resolve(strict=True) or not Path(item).is_dir() or Path(item).is_relative_to(work):
                    raise errors.InvalidRequest('Memory deployment PYTHONPATH must be absolute and outside work root')
        provider.deployment_config_sha256=digest
        provider.config_digest=provider.configuration()['config_digest']
        return provider

    @staticmethod
    def _file_identity(path):
        resolved=Path(path).resolve(strict=True)
        fd=os.open(resolved,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>134217728:
                raise ValueError('invalid Memory worker identity file')
            hashed=hashlib.sha256();size=0
            while True:
                chunk=os.read(fd,65536)
                if not chunk:break
                size+=len(chunk)
                if size>134217728:raise ValueError('Memory worker identity exceeds cap')
                hashed.update(chunk)
            return {'sha256':hashed.hexdigest(),'bytes':size}
        finally:os.close(fd)

    def configuration(self):
        identity=[]
        for path in self.argv or ():
            try:identity.append(self._file_identity(path))
            except (OSError,ValueError):identity.append({'status':'unavailable'})
        config={'provider_id':self.provider_id,'argv_identity':sha256_hex(self.argv),
            'worker_identity':identity,'environment_digest':sha256_hex(self.environment),
            'deployment_config_sha256':getattr(self,'deployment_config_sha256',None),
            'global_bank':self.global_bank,'protocol':'jasmine.memory-slot.v1',
            'maximum_bytes':MAX_BYTES,'timeout_seconds':3,'configured':self.argv is not None}
        return {**config,'config_digest':sha256_hex(config)}

    def recall(self,query,budget,*,conn):
        if conn.in_transaction:raise RuntimeError('Memory recall cannot hold a database transaction')
        if self.argv is None:return {'status':'not_configured','items':[],'banks':[],'filtered':[],
            'provider_identity':None,'provider_id':None,'provider_config_digest':self.config_digest,
            'query_trace_id':None,'error_code':None}
        current=self.configuration();self.config_digest=current['config_digest']
        if any(i.get('status')=='unavailable' for i in current['worker_identity']):
            return {'status':'degraded','items':[],'banks':[],'filtered':[],
                'provider_id':self.provider_id,'provider_config_digest':self.config_digest,
                'query_trace_id':None,'error_code':'memory_worker_unavailable'}
        remaining=budget.remaining(3);deadline=time.monotonic()+remaining
        proc=None;selector=None;failure=None;result=None
        try:
            proc=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                start_new_session=True,env={**self.environment,
                    'JASMINE_MEMORY_PROVIDER_ID':self.provider_id,
                    'JASMINE_MEMORY_CONFIG_DIGEST':self.config_digest})
            # Query is bounded well below PIPE_BUF? Write using nonblocking IO below.
            raw=canonical_json(query).encode('utf-8');sent=0;data=bytearray()
            os.set_blocking(proc.stdin.fileno(),False);os.set_blocking(proc.stdout.fileno(),False)
            selector=selectors.DefaultSelector();selector.register(proc.stdin,selectors.EVENT_WRITE)
            selector.register(proc.stdout,selectors.EVENT_READ)
            while selector.get_map():
                left=min(deadline-time.monotonic(),budget.remaining())
                if left<=0:raise TimeoutError()
                for selected,_ in selector.select(left):
                    if selected.fileobj is proc.stdin:
                        try:sent+=os.write(selected.fd,raw[sent:])
                        except BrokenPipeError:raise ValueError('worker closed query input')
                        if sent==len(raw):selector.unregister(proc.stdin);proc.stdin.close()
                    else:
                        chunk=os.read(selected.fd,65536)
                        if not chunk:selector.unregister(proc.stdout)
                        else:
                            data.extend(chunk)
                            if len(data)>MAX_BYTES:raise ValueError('memory_output_too_large')
            proc.wait(timeout=min(max(deadline-time.monotonic(),.001),budget.remaining()))
            if proc.returncode:raise ValueError('memory_worker_failed')
            result=validate_result(json.loads(data))
            declared={_bank({k:b[k] for k in ('scope','bank_id','project_id')}) for b in result['banks']}
            requested={_bank(b) for b in query['banks']}
            if declared!=requested:raise ValueError('missing or unrequested Memory bank status')
            if result['provider_id']!=self.provider_id or result['provider_config_digest']!=self.config_digest:
                raise ValueError('memory_provider_identity_mismatch')
            return result
        except Exception as exc:
            failure=exc
            code='memory_timeout' if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else 'memory_invalid_or_unavailable'
            result={'status':'degraded','items':[],'banks':[],'filtered':[],'provider_id':self.provider_id,
                'provider_config_digest':self.config_digest,'query_trace_id':None,'error_code':code}
            return result
        finally:
            if selector:selector.close()
            if proc:
                try:
                    if proc.stdin and not proc.stdin.closed:proc.stdin.close()
                except OSError:pass
                denied=_cleanup(proc)
                if denied and result is not None:
                    result['worker_cleanup']='incomplete_or_denied'
                    result['status']='degraded';result['items']=[]
                    result['error_code']=result.get('error_code') or 'memory_cleanup_incomplete'
                # The caller's overall budget is checked again before rendering/commit.


def filter_memory(conn,result,projection,*,schema_version):
    selected=[];filtered=[];seen=set();qualified=[]
    events=EventStore(conn,schema_version=schema_version)
    for item in result.get('items',[]):
        why=None;fact=item['fact'];bank=item['bank'];evidence=None;transition=None
        if looks_like_credential(canonical_json(item)):why='credential_content'
        elif any(b['bank_id']==bank['bank_id'] and b['status']!='ok' for b in result.get('banks',[])):why='bank_not_ok'
        elif bank['scope']=='GLOBAL':why='unsupported_global_memory_provenance'
        elif bank['project_id']!=projection['project_id']:why='foreign_project'
        elif item['memory_type'] not in ('VERIFIED_OUTCOME','FAILURE_LESSON'):why='unsupported_memory_type'
        else:
            evidence=conn.execute('SELECT * FROM evidence WHERE evidence_id=?',(fact['evidence_id'],)).fetchone()
            task=conn.execute('SELECT * FROM tasks WHERE task_id=?',(evidence['task_id'],)).fetchone() if evidence else None
            if not task or task['project_id']!=projection['project_id'] or item['source_task_id'] not in (None,evidence['task_id']):
                why='unverifiable_provenance'
            elif fact['subject_event_id']!=evidence['change_event_id'] or fact['value']!=evidence[fact['key']] or \
                 not {evidence['source_event_id'],evidence['change_event_id']}<=set(item['source_event_ids']):
                why='unverifiable_conflict'
            else:
                for eid in item['evidence_ids']:
                    related=conn.execute('SELECT task_id FROM evidence WHERE evidence_id=?',(eid,)).fetchone()
                    if not related or related['task_id']!=evidence['task_id']:why='unverifiable_provenance';break
                for source_id in item['source_event_ids']:
                    source=events.get(source_id)
                    if source is None or source['project_id']!=projection['project_id'] or source['task_id']!=evidence['task_id']:
                        why='unverifiable_provenance';break
                    owner=conn.execute('SELECT * FROM actors WHERE actor_id=?',(source['actor_id'],)).fetchone()
                    if not owner or owner['kind']!=source['actor_kind'] or owner['home_host_id']!=source['host_id']:
                        why='unverifiable_provenance';break
                if why is None and item['memory_type']=='VERIFIED_OUTCOME':
                    transition=events.get(item['verified_transition_event_id'])
                    try:
                        if transition is None or transition['task_id']!=evidence['task_id']:raise ValueError()
                        verified_transition(conn,transition,evidence['step_id'])
                        if fact['evidence_id'] not in transition['payload']['result']['evidence_ids']:raise ValueError()
                    except (errors.CoreError,ValueError,KeyError):why='unverified_outcome'
                elif why is None and evidence['status']!='FAIL':why='not_failure_evidence'
        identity={'bank_id':bank['bank_id'],'memory_id':item['memory_id']}
        if why is None:
            key=(item['memory_type'],tuple(sorted(item['source_event_ids'])),fact['fact_type'],
                 fact['subject_event_id'],fact['evidence_id'],fact['key'],fact['value'])
            if key in seen:why='duplicate_memory_fact'
            elif any(e['evidence_id']==fact['evidence_id'] for e in projection['evidence_refs']):why='duplicate_current_fact'
            else:seen.add(key)
        if why:filtered.append({**identity,'reason':why})
        else:
            selected.append(item)
            qualified.append({**identity,'historical_task_id':evidence['task_id'],
                'verified_transition_event_id':transition['event_id'] if transition else None,
                'evidence_ids':item['evidence_ids'],'source_event_ids':item['source_event_ids']})
    return {**result,'items':[],'selected':selected,'filtered':filtered,'qualified_provenance':qualified}
