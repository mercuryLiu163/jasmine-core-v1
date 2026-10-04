"""Bounded descriptor walk for fixed worker code dependencies, not OS libraries."""
import hashlib
import os
import stat
import time
from ..canonical import sha256_hex


def directory_identity(path, *, max_members=20000,max_bytes=268435456):
    started=time.monotonic();members=[];total=0;seen=0
    root=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    def walk(fd,prefix):
        nonlocal total,seen
        if prefix.count('/')>64:raise ValueError('dependency depth cap')
        before=os.fstat(fd)
        with os.scandir(fd) as scan:names=sorted(entry.name for entry in scan)
        for name in names:
            seen+=1
            if seen>max_members:raise ValueError('dependency member cap')
            if time.monotonic()-started>10:raise ValueError('dependency scan deadline')
            metadata=os.stat(name,dir_fd=fd,follow_symlinks=False)
            relative=prefix+name
            if stat.S_ISDIR(metadata.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:walk(child,relative+'/')
                finally:os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                if len(members)>=max_members:raise ValueError('dependency member cap')
                child=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    first=os.fstat(child);digest=hashlib.sha256();size=0
                    if not stat.S_ISREG(first.st_mode):raise ValueError('dependency type changed')
                    while chunk:=os.read(child,1048576):
                        size+=len(chunk);total+=len(chunk)
                        if total>max_bytes or time.monotonic()-started>10:raise ValueError('dependency bytes/deadline cap')
                        digest.update(chunk)
                    last=os.fstat(child)
                    if (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)!=(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns):raise ValueError('dependency changed')
                    current=os.stat(name,dir_fd=fd,follow_symlinks=False)
                    if (current.st_dev,current.st_ino)!=(first.st_dev,first.st_ino):raise ValueError('dependency replaced')
                    members.append({'path':relative,'sha256':digest.hexdigest(),'bytes':size})
                finally:os.close(child)
            else:raise ValueError('symlink or special dependency rejected')
        after=os.fstat(fd)
        if (before.st_ino,before.st_mtime_ns)!=(after.st_ino,after.st_mtime_ns):raise ValueError('dependency membership changed')
    try:
        opened=os.fstat(root)
        walk(root,'')
        current=os.stat(path,follow_symlinks=False)
        if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):raise ValueError('dependency root replaced')
    finally:os.close(root)
    return {'path':str(path),'kind':'directory','tree_sha256':sha256_hex(members),
        'members':len(members),'bytes':total,'sample_window_only':True}
