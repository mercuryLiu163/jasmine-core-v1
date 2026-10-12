#!/usr/bin/env python3
"""Launch explicitly configured loopback services and the opt-in Memory writer."""
import argparse,json,os,datetime,hashlib
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--base',required=True);p.add_argument('component',choices=['core','dashboard','memory']);args=p.parse_args()
b=Path(args.base).resolve();c=json.loads((b/'config/runtime.json').read_text());r=Path(c['release']).resolve()
assert r.parent==b/'releases' and r.name==c['commit'] and len(c['commit'])==40
assert Path(c['database']).resolve().parent==b/'data'
env={k:os.environ[k] for k in ('HOME','USER','LANG') if k in os.environ};env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin';env['PYTHONNOUSERSITE']='1';env['PYTHONDONTWRITEBYTECODE']='1'
memory=c.get('memory',{});enabled=memory.get('enabled') is True
if args.component in ('core','memory'):
 env.update(JASMINE_CORE_DB=c['database'],JASMINE_CORE_WORKSPACE_ROOT=c['workspace'],JASMINE_CORE_EVIDENCE_PRODUCERS=c['producer_config'],JASMINE_CORE_FINGERPRINT_EXTRA_PATHS=json.dumps(c['extra_paths'],separators=(',',':')),TIKTOKEN_CACHE_DIR=c['tokenizer_cache'],JASMINE_CORE_VALIDATION_RUNS=c['validation_root'])
 if enabled:
  env.update(JASMINE_HINDSIGHT_URL=memory['engine_url'],JASMINE_MEMORY_BANK_NAMESPACE=memory['namespace'],JASMINE_MEMORY_DEPLOYMENT_MODE='single_user',JASMINE_HINDSIGHT_TIMEOUT_SECONDS='30')
  env['JASMINE_CORE_MEMORY_CONFIG']=memory['provider_config']
 if args.component=='memory':
  if not enabled:raise SystemExit('Memory is disabled in runtime.json')
  argv=[c['python'],'-I','-m','jasmine_core.cli','memory-worker','--db',c['database']]
 else:argv=[c['python'],'-I','-m','jasmine_core.cli','serve','--db',c['database'],'--host','127.0.0.1','--port',str(c.get('core_port',18787))]
else:
 assert (r/'web/dist/index.html').is_file()
 env.update(P6_CORE_ORIGIN=c['core_origin'],P6_ENABLE_WEB_CONFIRMATIONS=str(c['web_confirmation_flag']),P6_ENABLE_P3_READS='1',P6_ENABLE_P7_READS='1',P6_ENABLE_P4_READS='1' if enabled else '0',P6_ENABLE_P4_WRITES='1' if enabled else '0')
 # Only public operational metadata reaches the dashboard; never configuration or keys.
 backups=[]
 for report in sorted((b/'backups').glob('*/BACKUP.json'),reverse=True):
  try:
   value=json.loads(report.read_text());database=report.parent/'core.db'
   valid=value.get('consistent_sqlite_backup') is True and hashlib.sha256(database.read_bytes()).hexdigest()==value.get('database_sha256')
   if valid:backups.append({'id':report.parent.name,'tables':len(value['all_tables']),'verified':True})
  except (OSError,ValueError,KeyError):continue
 status={'sampled_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manual_backup_available':(b/'bin/manage.py').is_file(),'automatic_backups':False,'latest_verified_backup':backups[0] if backups else None}
 (r/'web/dist/local-status.json').write_text(json.dumps(status,ensure_ascii=False)+'\n')
 argv=[c['node'],str(r/'web/server/serve.mjs'),'--root',str(r/'web/dist'),'--host','127.0.0.1','--port',str(c.get('dashboard_port',15180))]
os.umask(0o077);os.chdir(r);os.execve(argv[0],argv,env)
