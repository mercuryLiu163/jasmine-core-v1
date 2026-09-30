"""Fixed candidate JSON Schema plus source-bound validation; standard library only."""
from __future__ import annotations
import json
import math
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = 'jasmine.interpretation.v1'
KINDS = ['TASK_CREATE_OR_ATTACH','REQUIRED_CAPABILITY','RULE','CORRECTION','DECISION','ACCEPTANCE','NO_STRUCTURE']
SCOPES = ['GLOBAL','PROJECT','TASK','PATH','TOOL']
CERTAINTIES = ['EXPLICIT','TENTATIVE','QUOTED','NEGATED','AMBIGUOUS']
MAX_OUTPUT_BYTES = 64 * 1024


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def string(size):
    return {'type':'string','minLength':1,'maxLength':size}


def nullable_string(size):
    return {'type':['string','null'],'maxLength':size}

CONFIDENCE = {'type':'number','minimum':0,'maximum':1}
OUTPUT_SCHEMA = obj({
    'event_id': string(30), 'confidence': CONFIDENCE,
    'candidates': {'type':'array','minItems':1,'maxItems':16,'items':obj({
        'kind': {'type':'string','enum':KINDS}, 'content': string(2048),
        'scope': obj({'kind':{'type':'string','enum':SCOPES},
            'project_id':nullable_string(30),'task_id':nullable_string(30),
            'path':nullable_string(512),'tool':nullable_string(80)}),
        'impact':{'type':'string','enum':['LOW','MEDIUM','HIGH']},
        'certainty':{'type':'string','enum':CERTAINTIES}, 'confidence':CONFIDENCE,
        'rationale':string(2048),
        'source_span':obj({'start':{'type':'integer','minimum':0},
            'end':{'type':'integer','minimum':1},'quote':string(32768)})})}})


class InvalidOutput(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidOutput('duplicate_json_key')
        result[key] = value
    return result


def parse_json(text: str):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InvalidOutput('non_finite_number')))
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidOutput('invalid_json') from exc


def _validate(value: Any, schema: dict):
    kind = schema['type']
    if isinstance(kind,list):
        if value is None and 'null' in kind:
            return
        kind = 'string'
    if kind == 'object':
        if not isinstance(value,dict) or set(value) != set(schema['properties']):
            raise InvalidOutput('invalid_fields')
        for k,s in schema['properties'].items():
            _validate(value[k],s)
    elif kind == 'array':
        if not isinstance(value,list) or not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise InvalidOutput('invalid_array')
        for item in value:
            _validate(item,schema['items'])
    elif kind == 'string':
        if not isinstance(value,str) or not schema.get('minLength',0) <= len(value) <= schema.get('maxLength',32768):
            raise InvalidOutput('invalid_string')
        # Surrogates cannot be encoded into our UTF-8 provenance/input contract.
        try:
            value.encode('utf-8')
        except UnicodeError as exc:
            raise InvalidOutput('invalid_unicode') from exc
    elif kind in ('number','integer'):
        if (isinstance(value,bool) or not isinstance(value,(int,float)) or
            (kind=='integer' and not isinstance(value,int)) or (isinstance(value,float) and not math.isfinite(value)) or
            value < schema.get('minimum',-float('inf')) or value > schema.get('maximum',float('inf'))):
            raise InvalidOutput('invalid_number')
    if 'enum' in schema and value not in schema['enum']:
        raise InvalidOutput('invalid_enum')


def validate_output(raw: str, source: dict) -> dict:
    if len(raw.encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise InvalidOutput('output_too_large')
    result = parse_json(raw)
    _validate(result, OUTPUT_SCHEMA)
    if result['event_id'] != source['event_id']:
        raise InvalidOutput('source_event_mismatch')
    text = source['text']
    candidates = result['candidates']
    if any(c['kind']=='NO_STRUCTURE' for c in candidates) and len(candidates)!=1:
        raise InvalidOutput('no_structure_mixed')
    for c in candidates:
        span = c['source_span']
        if not 0 <= span['start'] < span['end'] <= len(text) or text[span['start']:span['end']] != span['quote']:
            raise InvalidOutput('source_span_mismatch')
        scope = c['scope']
        for name in ('project_id','task_id'):
            if scope[name] is not None and scope[name] != source[name]:
                raise InvalidOutput('scope_reference_mismatch')
        k = scope['kind']
        if k=='GLOBAL' and any(scope[n] is not None for n in ('project_id','task_id','path','tool')):
            raise InvalidOutput('scope_shape')
        if k=='PROJECT' and (not scope['project_id'] or any(scope[n] is not None for n in ('task_id','path','tool'))):
            raise InvalidOutput('scope_shape')
        if k=='TASK' and (not scope['project_id'] or scope['path'] is not None or scope['tool'] is not None):
            raise InvalidOutput('scope_shape')
        if k=='PATH':
            p = scope['path']
            if (not p or '\\' in p or '\x00' in p or PurePosixPath(p).is_absolute() or
                '..' in PurePosixPath(p).parts or scope['tool'] is not None or not scope['project_id']):
                raise InvalidOutput('scope_shape')
        if k=='TOOL' and (not scope['tool'] or not scope['project_id'] or scope['path'] is not None):
            raise InvalidOutput('scope_shape')
        if c['kind'] in ('TASK_CREATE_OR_ATTACH','REQUIRED_CAPABILITY','CORRECTION') and k!='TASK':
            raise InvalidOutput('task_candidate_scope')
    return result
