"""Exact selected-encoding counts; no claim of the model's hidden prompt total."""
from __future__ import annotations

import importlib.metadata
import base64
import hashlib
import os
import stat
import types
from pathlib import Path
from .. import errors
from ..canonical import sha256_hex

LIBRARY_VERSION = '0.14.0'
ENCODING = 'o200k_base'
TARGET_MODEL = 'gpt-6.1-sol'
ASSET_CACHE_NAME='fb374d419588a4632f3f557e76b4b70aebbca790'
ASSET_SHA256='446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
MAX_TOKENS = 2500


class TokenizerUnavailable(errors.CoreError):
    status=503
    code='context_tokenizer_unavailable'


class Tokenizer:
    def __init__(self,*,cache_dir=None):
        try:
            import tiktoken
            if importlib.metadata.version('tiktoken')!=LIBRARY_VERSION:
                raise ValueError('unsupported tokenizer library version')
            cache=cache_dir or os.environ.get('TIKTOKEN_CACHE_DIR')
            if not cache:raise ValueError('offline tokenizer cache is not configured')
            fd=os.open(Path(cache)/ASSET_CACHE_NAME,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size>10485760:
                    raise ValueError('invalid tokenizer asset file')
                raw=b''
                while True:
                    chunk=os.read(fd,65536)
                    if not chunk:break
                    raw+=chunk
                    if len(raw)>10485760:raise ValueError('tokenizer asset exceeds cap')
            finally:os.close(fd)
            if hashlib.sha256(raw).hexdigest()!=ASSET_SHA256:raise ValueError('tokenizer asset hash mismatch')
            ranks={base64.b64decode(token,validate=True):int(rank) for token,rank in
                   (line.split() for line in raw.splitlines() if line)}
            # Use the pinned package's encoding constructor with an isolated local
            # loader namespace. No global monkeypatch, cache lookup or network IO.
            from tiktoken_ext import openai_public
            constructor=openai_public.o200k_base
            local=types.FunctionType(constructor.__code__,{**constructor.__globals__,
                'load_tiktoken_bpe':lambda *a,**k:ranks})
            self.encoding=tiktoken.Encoding(**local())
            # Identity of the actual encoding definition, not its cache filename.
            ranks=[[token.hex(),rank] for token,rank in self.encoding._mergeable_ranks.items()]
            ranks.sort(key=lambda row:row[0])
            self.identity={'name':'tiktoken','version':LIBRARY_VERSION,'encoding':ENCODING,
                'encoding_asset_sha256':ASSET_SHA256,
                'encoding_definition_sha256':sha256_hex({'ranks':ranks,'pattern':self.encoding._pat_str,
                    'special_tokens':self.encoding._special_tokens}),
                'target_model':TARGET_MODEL,'qualification':'EXPLICIT_ENCODING_MODEL_UNVERIFIED',
                'counting_scope':'RENDERED_PACK_ENCODING_ONLY','maximum_tokens':MAX_TOKENS}
        except Exception as exc:
            raise TokenizerUnavailable('pinned tokenizer or encoding asset is unavailable') from exc

    def count(self,text):
        if not isinstance(text,str):raise errors.InvalidRequest('tokenizer input must be text')
        try:text.encode('utf-8')
        except UnicodeError as exc:raise errors.InvalidRequest('text must be valid Unicode') from exc
        return len(self.encoding.encode_ordinary(text))
