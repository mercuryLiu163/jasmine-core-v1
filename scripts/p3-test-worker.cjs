'use strict';
// Fixed callback unit executor. Browser qualification uses the separate skill.
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
(async()=>{
  let input='';for await(const chunk of process.stdin)input+=chunk;
  const config=JSON.parse(input);
  if(process.argv[2]!=='callback'||Object.keys(config).sort().join(',')!=='artifact_root,work_root')throw new Error('invalid fixed input');
  const root=fs.realpathSync(config.work_root),artifacts=fs.realpathSync(config.artifact_root);
  if(!artifacts.startsWith(root+path.sep))throw new Error('outside artifact directory');
  const source=fs.readFileSync(path.join(root,'callback.js'),'utf8');
  // Structural fixture test deliberately never evaluates patched JavaScript.
  // Actual JavaScript behavior is tested by the isolated Playwright browser.
  const expected='function callbackTaskId() { return \"callback-task-001\"; }\n';
  const passed=source===expected;const actual=passed?'expected-source-shape':'source-shape-mismatch';
  const body=JSON.stringify({suite:'callback',actual,expected:'callback-task-001',passed})+'\n';
  const file=path.join(artifacts,'callback-test-'+crypto.randomUUID()+'.json');fs.writeFileSync(file,body,{flag:'wx',mode:0o600});
  console.log(JSON.stringify({suite:'callback',passed,artifact_uri:'workspace:/'+path.relative(root,file).split(path.sep).join('/'),artifact_sha256:crypto.createHash('sha256').update(body).digest('hex'),artifact_bytes:Buffer.byteLength(body),cleanup:'complete'}));
  process.exitCode=passed?0:1;
})().catch(()=>{process.exitCode=1;});
