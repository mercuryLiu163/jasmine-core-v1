#!/usr/bin/env python3
"""Launch the explicitly configured loopback Core or Dashboard. No external workers."""
import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--base',required=True);p.add_argument('component',choices=['core','dashboard']);args=p.parse_args()
b=Path(args.base).resolve();c=json.loads((b/'config/runtime.json').read_text());r=Path(c['release']).resolve()
assert r.parent==b/'releases' and r.name==c['commit'] and len(c['commit'])==40
assert Path(c['database']).resolve().parent==b/'data'
env={k:os.environ[k] for k in ('HOME','USER','LANG') if k in os.environ};env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin';env['PYTHONNOUSERSITE']='1';env['PYTHONDONTWRITEBYTECODE']='1'
if args.component=='core':
 env.update(JASMINE_CORE_DB=c['database'],JASMINE_CORE_WORKSPACE_ROOT=c['workspace'],JASMINE_CORE_EVIDENCE_PRODUCERS=c['producer_config'],JASMINE_CORE_FINGERPRINT_EXTRA_PATHS=json.dumps(c['extra_paths'],separators=(',',':')),TIKTOKEN_CACHE_DIR=c['tokenizer_cache'],JASMINE_CORE_VALIDATION_RUNS=c['validation_root'])
 argv=[c['python'],'-I','-m','jasmine_core.cli','serve','--db',c['database'],'--host','127.0.0.1','--port',str(c.get('core_port',18787))]
else:
 assert (r/'web/dist/index.html').is_file()
 env.update(P6_CORE_ORIGIN=c['core_origin'],P6_ENABLE_WEB_CONFIRMATIONS=str(c['web_confirmation_flag']),P6_ENABLE_P3_READS='1',P6_ENABLE_P7_READS='1',P6_ENABLE_P4_READS='0',P6_ENABLE_P4_WRITES='0')
 argv=[c['node'],str(r/'web/server/serve.mjs'),'--root',str(r/'web/dist'),'--host','127.0.0.1','--port',str(c.get('dashboard_port',15180))]
os.umask(0o077);os.chdir(r);os.execve(argv[0],argv,env)
