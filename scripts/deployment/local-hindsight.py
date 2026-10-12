#!/usr/bin/env python3
"""Daily-use engine. Persistent HOME/DB; existing Keychain credential stays secret."""
import argparse
import os
from pathlib import Path
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--base', required=True)
a = p.parse_args()
base = Path(a.base).resolve()
engine = base / 'engine'
home = engine / 'home'
home.mkdir(parents=True, exist_ok=True, mode=0o700)
key = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s',
                      'com.jasmine.hindsight.llm', '-a', 'api-key', '-w'],
                     capture_output=True, text=True)
if key.returncode or not key.stdout.strip():
    raise SystemExit('Hindsight cloud credential unavailable in the existing login Keychain')
env = {'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
       'PYTHONDONTWRITEBYTECODE': '1', 'TOKENIZERS_PARALLELISM': 'false',
       'HINDSIGHT_API_DATABASE_URL': 'pg0://jasmine-local-item6:15434',
       'HINDSIGHT_API_LLM_PROVIDER': 'deepseek',
       'HINDSIGHT_API_LLM_MODEL': 'deepseek-chat',
       'HINDSIGHT_API_LLM_API_KEY': key.stdout.strip(),
       'HINDSIGHT_API_EMBEDDINGS_PROVIDER': 'onnx',
       'HINDSIGHT_API_RERANKER_PROVIDER': 'flashrank',
       'HINDSIGHT_API_LLM_TIMEOUT': '120',
       'HINDSIGHT_API_REFLECT_LLM_TIMEOUT': '120'}
os.umask(0o077)
os.chdir(engine)
command = str(engine / 'venv/bin/hindsight-api')
os.execve(command, [command, '--host', '127.0.0.1', '--port', '18084',
                    '--workers', '1', '--no-access-log'], env)
