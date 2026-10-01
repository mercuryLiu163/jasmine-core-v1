'use strict';
// Fixed scenarios; all runtime paths/origin come only from the trusted runner stdin.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');
const scenario = process.argv[2];
const events = [];
let browser, context;
let cleanupStatus = 'incomplete_or_denied';
const bounded = (promise, ms) => Promise.race([promise, new Promise((_, reject) => {
  const timer = setTimeout(() => reject(new Error('cleanup_timeout')), ms); timer.unref();
})]);
(async () => {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (Buffer.byteLength(raw) > 16384) throw new Error('stdin_too_large');
  }
  const input = JSON.parse(raw);
  if (JSON.stringify(Object.keys(input).sort()) !== JSON.stringify(['artifact_root','chrome_path','origin','work_root'])) throw new Error('invalid_input');
  if (!['callback_task_id','smoke'].includes(scenario)) throw new Error('invalid_scenario');
  const origin = new URL(input.origin);
  if (origin.protocol !== 'http:' || origin.hostname !== '127.0.0.1' || !origin.port || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('invalid_origin');
  if (input.chrome_path !== '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') throw new Error('invalid_chrome');
  for (const key of ['artifact_root','work_root']) {
    if (typeof input[key] !== 'string' || !path.isAbsolute(input[key]) || fs.realpathSync(input[key]) !== input[key]) throw new Error('unsafe_root');
  }
  const relative = path.relative(input.work_root,input.artifact_root);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('artifact_root_outside_work');
  const artifact = path.join(input.artifact_root,`playwright-${crypto.randomUUID()}.zip`);
  let outcome, cleanup='complete';
  try {
    browser = await chromium.launch({executablePath:input.chrome_path,headless:true,timeout:15000,args:['--disable-background-networking','--disable-component-update','--no-first-run']});
    events.push({event:'browser_launched',fresh_owned:true});
    context = await browser.newContext({acceptDownloads:false,serviceWorkers:'block'});
    await context.route('**/*', route => {
      const target=new URL(route.request().url());
      if (target.origin !== origin.origin) return route.abort('blockedbyclient');
      return route.continue();
    });
    await context.routeWebSocket('**/*', ws => ws.close());
    await context.tracing.start({screenshots:true,snapshots:true,sources:false});
    const page=await context.newPage();
    let download=false;
    page.on('download', d=>{download=true; d.cancel().catch(()=>{});});
    await page.goto(origin.href,{waitUntil:'networkidle',timeout:15000});
    events.push({event:'navigation',origin:origin.origin});
    if (scenario==='callback_task_id') {
      const value=await page.locator('#task-id').innerText({timeout:5000});
      const callbackValue=await page.evaluate(() => callbackTaskId());
      if (value!=='callback-task-001' || callbackValue!=='callback-task-001') throw new Error('task_id_assertion');
      events.push({event:'assertion',name:'callback_task_id',passed:true});
    } else {
      if (await page.title()!=='Jasmine callback fixture' || await page.locator('#status').innerText({timeout:5000})!=='ready') throw new Error('smoke_assertion');
      events.push({event:'assertion',name:'smoke',passed:true});
    }
    if(download) throw new Error('download_attempt');
    await context.tracing.stop({path:artifact});
    const st=fs.lstatSync(artifact);
    if(!st.isFile() || st.nlink!==1 || st.size>16*1024*1024) throw new Error('invalid_artifact');
    outcome={scenario,events,artifact_uri:'workspace:/'+path.relative(input.work_root,artifact).split(path.sep).join('/'),artifact_sha256:crypto.createHash('sha256').update(fs.readFileSync(artifact)).digest('hex'),artifact_bytes:st.size};
  } finally {
    if(context) try { await bounded(context.close(),3000); } catch (_) { cleanup='incomplete_or_denied'; }
    if(browser) try { await bounded(browser.close(),3000); } catch (_) { cleanup='incomplete_or_denied'; }
    cleanupStatus=cleanup;
    events.push({event:'cleanup',status:cleanup,descendants_verified_gone:false});
  }
  if(cleanup!=='complete') throw new Error('cleanup_incomplete');
  console.log(JSON.stringify({...outcome,cleanup}));
})().catch(error=>{ console.log(JSON.stringify({scenario,events,error:String(error.message),cleanup:cleanupStatus})); process.exitCode=1; });
