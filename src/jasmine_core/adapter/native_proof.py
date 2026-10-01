"""Typed current-turn native input proof; assistant prose is never proof."""
import json
import os
import stat
from pathlib import Path


def typed_input(records,turn_id,text):
    current=None
    for row in records:
        payload=row.get('payload',{})
        if row.get('type')=='turn_context':current=payload.get('turn_id')
        turn=row.get('turn_id') or payload.get('turn_id') or current
        if turn!=turn_id or row.get('type')!='response_item' or payload.get('type')!='message' or payload.get('role')!='developer':
            continue
        for content in payload.get('content',[]):
            if content.get('type')=='input_text' and text.encode('utf-8')==content.get('text','').encode('utf-8'):
                return True
    return False


def read_thread_rollout(thread_id,budget):
    if not isinstance(thread_id,str) or not thread_id or any(c not in '0123456789abcdef-' for c in thread_id):
        raise ValueError('native thread UUID required')
    root=Path.home()/'.codex/sessions'
    matches=list(root.rglob('*'+thread_id+'*.jsonl'))
    if len(matches)!=1:raise ValueError('unique actual native rollout unavailable')
    path=matches[0];fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>67108864:raise ValueError('bounded native rollout required')
        with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as stream:
            records=[]
            for line in stream:
                budget.remaining()
                if len(line.encode())>1048576:raise ValueError('native receipt line cap')
                records.append(json.loads(line))
                if len(records)>20000:raise ValueError('native receipt member cap')
    finally:os.close(fd)
    sessions=[r.get('payload',{}).get('id') for r in records if r.get('type')=='session_meta']
    if sessions!=[thread_id]:raise ValueError('rollout does not bind current native thread')
    return records,path


def skill_input(records,turn_id,body,*,native_request,skill_name,skill_path):
    """Explicit reviewed skill request plus exact loaded body, no user quote.

    Unknown platform wrappers are BLOCKED until independently frozen.
    """
    inputs=native_request.get('input',[])
    if not any(item=={'type':'skill','name':skill_name,'path':skill_path} for item in inputs):return False
    expected='<skill>\n<name>'+skill_name+'</name>\n<path>'+skill_path+'</path>\n'+body+'\n</skill>'
    if any(item.get('type')=='text' and expected in item.get('text','') for item in inputs):return False
    current=None
    for row in records:
        payload=row.get('payload',{})
        if row.get('type')=='turn_context':current=payload.get('turn_id')
        turn=row.get('turn_id') or payload.get('turn_id') or current
        if turn==turn_id and row.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='user':
            if payload.get('content')==[{'type':'input_text','text':expected}]:return True
    return False
