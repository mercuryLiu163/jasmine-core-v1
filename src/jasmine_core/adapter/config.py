"""Purpose-bound adapter identity and private receipt reader."""
from __future__ import annotations
import json
import hashlib
import os
import re
import stat
from pathlib import Path
from .. import errors, fingerprint, ids
from ..canonical import sha256_hex
from ..resolution_common import actor, fields, Conflict

RECEIPT_ID=re.compile(r'^[a-zA-Z0-9_-]{1,128}$')
CONFIG_FIELDS={'actor_id','host_id','key_id','receipt_root','lease_file','work_root','deployment_root',
               'deployment_sha256','profile_sha256','hook_definition_sha256','hook_definition_path','executors','executor_inputs'}


def _finite_float(value):
    import math
    number=float(value)
    if not math.isfinite(number):raise ValueError("nonfinite JSON number")
    return number


def _pairs(items):
    result={}
    for name,value in items:
        if name in result:raise ValueError('duplicate JSON key')
        result[name]=value
    return result


def private_json(path,*,cap=4194304):
    """Anchor every directory FD; reject symlinks and changed ancestor links."""
    import hashlib
    path=Path(path);fds=[];links=[]
    try:
        if not path.is_absolute() or path!=path.resolve(strict=True):raise ValueError('noncanonical path')
        directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY);fds.append(directory)
        for component in path.parts[1:-1]:
            parent=directory
            directory=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            fds.append(directory);info=os.fstat(directory)
            links.append((parent,component,info.st_dev,info.st_ino))
        parent=os.fstat(directory)
        if parent.st_uid!=os.getuid() or parent.st_mode & 0o077:raise ValueError('unsafe parent')
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory);fds.append(fd)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>cap:
            raise ValueError('unsafe private file')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(cap+1)
        after=os.fstat(fd);linked=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
        if len(raw)>cap or (after.st_size,after.st_mtime_ns)!=(info.st_size,info.st_mtime_ns) or (linked.st_dev,linked.st_ino)!=(info.st_dev,info.st_ino):
            raise ValueError('changed private file')
        for parentfd,name,dev,ino in links:
            linked=os.stat(name,dir_fd=parentfd,follow_symlinks=False)
            if (linked.st_dev,linked.st_ino)!=(dev,ino):raise ValueError('changed private ancestor')
        value=json.loads(raw,object_pairs_hook=_pairs,parse_float=_finite_float,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not isinstance(value,dict):raise ValueError('JSON object required')
        # Ensure valid Unicode, not lone-surrogate escaped JSON.
        json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        return value,hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError,UnicodeError,TypeError) as exc:
        raise errors.InvalidRequest('protected adapter file unavailable or invalid') from exc
    finally:
        for fd in reversed(fds):os.close(fd)


class AdapterConfig:
    def __init__(self,value,*,config_sha256):
        fields(value,CONFIG_FIELDS,CONFIG_FIELDS)
        for name,prefix in (('actor_id','act'),('host_id','hst'),('key_id','key')):
            if not ids.is_id(value[name],prefix):raise errors.InvalidRequest('invalid adapter identity')
        for name in ('deployment_sha256','profile_sha256','hook_definition_sha256'):
            if not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{64}',value[name]):raise errors.InvalidRequest('invalid deployment digest')
        import copy
        self.value=copy.deepcopy(value);self.config_sha256=config_sha256
        for name in ('receipt_root','lease_file','work_root','deployment_root'):
            if not isinstance(value[name],str):raise errors.InvalidRequest('adapter paths must be strings')
        roots=[]
        for name in ('receipt_root','work_root','deployment_root'):
            p=Path(value[name])
            if not p.is_absolute() or p!=p.resolve(strict=True) or not p.is_dir():raise errors.InvalidRequest('canonical adapter root required')
            roots.append(p)
        if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(roots) for b in roots[i+1:]):
            raise errors.InvalidRequest('adapter roots must be disjoint')
        private=roots[0].stat()
        if private.st_uid!=os.getuid() or private.st_mode&0o077:raise errors.InvalidRequest('private receipt root required')
        lease=Path(value['lease_file'])
        if not lease.is_absolute() or not lease.is_relative_to(roots[0]):raise errors.InvalidRequest('lease must be in private root')
        hook=value['hook_definition_path']
        if not isinstance(hook,str):raise errors.InvalidRequest('hook definition path must be a string')
        hook=Path(hook)
        if not hook.is_absolute() or hook.name!='hooks.json' or hook.parent.name!='.codex' or hook.parent!=hook.parent.resolve(strict=True) or hook.is_relative_to(roots[0]) or hook.is_relative_to(roots[1]) or hook.is_relative_to(roots[2]):
            raise errors.InvalidRequest('canonical deployed hook definition outside adapter roots required')
        if not isinstance(value['executors'],dict):raise errors.InvalidRequest('executor manifests required')
        inputs=value['executor_inputs']
        if not isinstance(inputs,dict) or set(inputs)!=set(value['executors']):raise errors.InvalidRequest('fixed executor stdin required')
        for tool,stdin in inputs.items():
            required={'work_root','artifact_root'}|({'origin','chrome_path'} if tool=='jasmine_playwright' else set())
            fields(stdin,required,required)
            if stdin['work_root']!=value['work_root']:raise errors.InvalidRequest('worker work root mismatch')
            artifact=Path(stdin['artifact_root'])
            if artifact!=artifact.resolve(strict=True) or not artifact.is_relative_to(Path(value['work_root'])):raise errors.InvalidRequest('worker artifact root mismatch')
            if tool=='jasmine_playwright':
                from urllib.parse import urlsplit
                origin=urlsplit(stdin['origin'])
                if origin.scheme!='http' or origin.hostname!='127.0.0.1' or not origin.port or origin.path or origin.query or origin.fragment or origin.username or origin.password:raise errors.InvalidRequest('fixed loopback fixture origin required')
                chrome=Path(stdin['chrome_path'])
                if chrome!=chrome.resolve(strict=True) or not chrome.is_file():raise errors.InvalidRequest('fixed Chrome executable required')

    def hook_definition(self):
        """Verify the frozen project definition outside every DB transaction."""
        path=Path(self.value['hook_definition_path'])
        if path.parent!=path.parent.resolve(strict=True):raise Conflict('adapter_provenance_mismatch')
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size>1048576:raise Conflict('adapter_provenance_mismatch')
                raw=os.read(fd,1048577)
                if len(raw)>1048576 or hashlib.sha256(raw).hexdigest()!=self.value['hook_definition_sha256']:
                    raise Conflict('adapter_provenance_mismatch')
                current=path.lstat()
                if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns):
                    raise Conflict('adapter_provenance_mismatch')
            finally:os.close(fd)
        except OSError as exc:raise Conflict('adapter_provenance_mismatch') from exc
        return str(path)

    @classmethod
    def load(cls):
        name=os.environ.get('JASMINE_CORE_ADAPTER_CONFIG')
        if not name:raise errors.InvalidRequest('adapter is not configured')
        path=Path(name)
        if path.is_relative_to(fingerprint.configured_root()):raise errors.InvalidRequest('adapter config must be outside work root')
        value,digest=private_json(path,cap=65536)
        return cls(value,config_sha256=digest)

    def principal(self,conn,principal,scope):
        principal.require(scope)
        if principal.actor_id!=self.value['actor_id'] or principal.key_id!=self.value['key_id']:
            raise errors.ForbiddenActorKind('key is not the configured adapter capability')
        row=actor(conn,principal.actor_id,self.value['host_id'])
        if row['kind']!='system':raise errors.ForbiddenActorKind('adapter requires configured system actor')

    def receipt(self,ident,digest=None):
        if not isinstance(ident,str) or not RECEIPT_ID.fullmatch(ident):raise errors.InvalidRequest('safe receipt id required')
        value,actual=private_json(Path(self.value['receipt_root'])/(ident+'.json'))
        if digest is not None and digest!=actual:raise errors.InvalidRequest('adapter receipt digest mismatch')
        return value,actual

    def lease(self):return private_json(self.value['lease_file'],cap=65536)[0]
