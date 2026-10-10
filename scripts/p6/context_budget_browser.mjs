#!/usr/bin/env node
/** Real Context reads only. No mocked response, build, admission or injection. */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, writeFileSync } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import net from 'node:net'
import { Cdp, CHROME_CANDIDATES, waitForCdp, httpJson, evaluate, waitFor, clickByText, typeInto } from './browser_trace.mjs'

let input = ''
for await (const chunk of process.stdin) input += chunk
const cfg = JSON.parse(input)
const report = { status: 'FAIL', records: [], network: [], clicks: [], console_errors: [] }
const pendingBodies = []
let chrome, cdp, current, activeToken, activeOrigin
const secrets = [...new Set(cfg.cases.flatMap(c => [c.token, c.admin_token]))]
function safeJson(value) {
  const text = JSON.stringify(value, null, 2)
  if (secrets.filter(Boolean).some(secret => text.includes(secret))) throw new Error('credential in artifact; refused to publish')
  return text + '\n'
}
function persist() { writeFileSync(join(cfg.out, 'browser-progress.json'), safeJson(report)) }
function local(command, flag, value) {
  const r = spawnSync(cfg.python, ['-I', cfg.snapshot_script, command, flag, value], { encoding: 'utf8' })
  if (r.status !== 0) throw new Error(command + ' receipt failed: ' + r.stderr)
  return JSON.parse(r.stdout)
}
function snapshot(c) { return local('snapshot', '--db', c.database) }
function observer(c) { return local('observer', '--path', c.observer_path) }
async function click(selector, text) {
  const started_at_unix = Date.now() / 1000
  const point = await clickByText(cdp, selector, text)
  const r = { id: current.id, action: 'actual pointer click', selector, text, point, started_at_unix, at_unix: Date.now() / 1000 }
  report.clicks.push(r)
  return r
}
async function login(c) {
  if (activeOrigin !== c.ui) {
    await cdp.send('Page.navigate', { url: c.ui + '/' })
    await waitFor(cdp, '!!document.querySelector("input[type=password]")')
    activeOrigin = c.ui
    activeToken = null
  }
  if (activeToken !== c.token) {
    if (!await evaluate(cdp, '!!document.querySelector("input[type=password]")')) {
      await click('button', '退出')
      await waitFor(cdp, '!!document.querySelector("input[type=password]")')
    }
    await evaluate(cdp, 'document.querySelector("input[type=password]").select()')
    await typeInto(cdp, 'input[type=password]', c.token)
    report.clicks.push({ id: current.id, action: 'actual token input', value: '[PRIVATE TOKEN NOT RECORDED]', at_unix: Date.now() / 1000 })
    await click('button', '使用此 token')
    if (c.pages[0]?.kind === 'signin-error') {
      await waitFor(cdp, '!!document.querySelector("input[type=password]")&&!!document.querySelector("[role=alert]")')
      activeToken = null
      return
    }
    await waitFor(cdp, '!document.querySelector("input[type=password]")')
    activeToken = c.token
  }
}
async function capture(name) {
  const domAt = Date.now() / 1000
  const dom = await evaluate(cdp, `(() => ({
    budget: (()=>{const x=document.querySelector('[data-testid=context-budget]');return x?{status:x.dataset.status,count:x.querySelector('[data-testid=context-budget-count]')?.textContent,boundary:x.querySelector('[data-testid=context-injection-boundary]')?.textContent,warning:x.querySelector('[data-testid=context-budget-warning]')?.textContent??null,omissions_warning:x.querySelector('[data-testid=context-omissions-warning]')?.textContent??null,omissions:x.querySelector('[data-testid=context-budget-omissions]')?.textContent??null,no_omissions:x.querySelector('[data-testid=context-no-budget-omissions]')?.textContent??null,rows:[...x.querySelectorAll('[data-testid=context-budget-omission]')].map(y=>({text:y.textContent,links:[...y.querySelectorAll('a')].map(a=>({text:a.textContent,href:a.getAttribute('href')}))}))}:null})(),
    hash: location.hash, text: document.body.innerText,
    html: (()=>{const x=document.body.cloneNode(true);for(const i of x.querySelectorAll('input[type=password]'))i.setAttribute('value','[REDACTED]');return x.innerHTML})(),
    inputs: [...document.querySelectorAll('input')].map(x => ({label: document.querySelector('label[for="'+x.id+'"]')?.textContent.trim(), value: x.type==='password'?'[REDACTED]':x.value})),
    links: [...document.querySelectorAll('a')].map(x => ({text:x.textContent.trim(),href:x.getAttribute('href')})),
    kv: [...document.querySelectorAll('dl.kv dt')].map(x=>({label:x.textContent.trim(),text:x.nextElementSibling.textContent.trim()})),
    digests: [...document.querySelectorAll('.kv__digest')].map(x=>({label:x.querySelector('.muted').textContent.trim(),value:x.querySelector('code').textContent})),
    rawtexts: [...document.querySelectorAll('.rawtext')].map(x=>({label:x.querySelector('.rawtext__label').textContent.trim(),text:x.querySelector('.rawtext__body')?.textContent??null})),
    jsonblocks: [...document.querySelectorAll('.jsonblock')].map(x=>({label:x.querySelector('.jsonblock__toggle')?.textContent.trim().replace(/^[▾▸]/,'').trim(),text:x.querySelector('pre')?.textContent??null})),
    comparison_status: document.querySelector('[data-testid="resume-comparison-status"]')?.innerText??null,
    checkpoint_boundary: document.querySelector('[data-testid="checkpoint-read-boundary"]')?.textContent??null,
    alerts: [...document.querySelectorAll('[role=alert]')].map(x=>x.textContent),
    pending: !!document.querySelector('[data-testid="dependency-pending"]'),
    invalid: !!document.querySelector('[data-testid="invalid-id"]'),
    note: document.querySelector('[data-testid="resume-note"]')?.textContent??null,
    next_actions: document.querySelector('[data-testid="next-actions"]')?.textContent??null,
    next_action_header: document.querySelector('[data-testid="next-actions"] > .muted')?.textContent??null,
    next_action_rows: [...document.querySelectorAll('[data-testid="next-action"]')].map(x=>({text:x.textContent,codes:[...x.querySelectorAll('code')].map(y=>y.textContent),fields:[...x.querySelectorAll('span')].map(y=>y.textContent)}))
  }))()`)
  const file = 'dom/' + name + '.json'
  await writeFile(join(cfg.out, file), safeJson(dom))
  await evaluate(cdp, 'window.scrollTo(0,0)')
  const layout = await cdp.send('Page.getLayoutMetrics')
  const bounds = layout.cssContentSize ?? layout.contentSize
  const clip = { x: 0, y: 0, width: 1400, height: Math.min(bounds.height, 16000), scale: 1 }
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip })
  const screenshot = 'screenshots/' + name + '.png'
  await writeFile(join(cfg.out, screenshot), Buffer.from(shot.data, 'base64'))
  return { dom, dom_file: file, screenshot, screenshot_clip: clip, dom_at_unix: domAt, at_unix: Date.now() / 1000 }
}
function beginStage(c, label) {
  return { id: c.id, label, started_at_unix: Date.now()/1000, network_start: report.network.length, observer_start: observer(c).length }
}
function endStage(c, stage) {
  return { ...stage, network_end: report.network.length, observer_end: observer(c).length, finished_at_unix: Date.now()/1000 }
}
function associate(observation, p, network) {
  observation.request_ids = network.filter(n => n.method === 'GET' && n.path + n.query === p.path && n.response_finished_at_unix <= observation.dom_at_unix).map(n => n.request_id)
  return observation
}
async function expand() {
  if (await evaluate(cdp, '[...document.querySelectorAll("details:not([open]) summary")].some(x=>x.textContent.includes("错误 details"))')) await click('summary', '错误 details')
  const labels = await evaluate(cdp, '[...document.querySelectorAll(".jsonblock__toggle[aria-expanded=false]")].map(x=>x.textContent.trim())')
  for (const label of labels) await click('.jsonblock__toggle', label)
  while (await evaluate(cdp, '[...document.querySelectorAll(".rawtext button")].some(x=>x.textContent==="展开全文")')) await click('.rawtext button', '展开全文')
}
try {
  await mkdir(join(cfg.out, 'dom'), { recursive: true })
  await mkdir(join(cfg.out, 'screenshots'), { recursive: true })
  const executable = CHROME_CANDIDATES.find(x => x && existsSync(x))
  if (!executable) throw new Error('actual Chrome unavailable')
  const s = net.createServer()
  await new Promise(r => s.listen(0, '127.0.0.1', r))
  const port = s.address().port
  await new Promise(r => s.close(r))
  const argv = [`--remote-debugging-port=${port}`, `--user-data-dir=${cfg.profile}`, '--headless=new', '--no-first-run',
    '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-gpu', 'about:blank']
  chrome = spawn(executable, argv, { stdio: 'ignore' })
  report.launch = { executable, argv, pid: chrome.pid, parent_node_pid: process.pid, owned_profile: cfg.profile }
  await waitForCdp(port)
  const target = await httpJson(`http://127.0.0.1:${port}/json/new?about:blank`, 'PUT')
  cdp = new Cdp(target.webSocketDebuggerUrl)
  await cdp.connect()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false })
  report.browser_version = await cdp.send('Browser.getVersion')
  cdp.onEvent(m => {
    if (m.method === 'Runtime.exceptionThrown') report.console_errors.push({ id: current?.id, detail: m.params.exceptionDetails })
    if (m.method === 'Network.requestWillBeSent') {
      const e = m.params, url = new URL(e.request.url)
      if (!url.pathname.startsWith('/v1/')) return
      report.network.push({ id: current?.id, cdp_id: e.requestId, method: e.request.method, path: url.pathname, query: url.search,
        at_unix: Date.now() / 1000, request_id_sent: Object.entries(e.request.headers).find(([k]) => k.toLowerCase() === 'x-request-id')?.[1],
        authorization_present: Object.keys(e.request.headers).some(k => k.toLowerCase() === 'authorization') })
    }
    if (m.method === 'Network.responseReceived') {
      const e = m.params, row = report.network.findLast(r => r.cdp_id === e.requestId)
      if (row) { row.status = e.response.status; row.request_id = Object.entries(e.response.headers).find(([k]) => k.toLowerCase() === 'x-request-id')?.[1] }
    }
    if (m.method === 'Network.loadingFinished') {
      const row = report.network.findLast(r => r.cdp_id === m.params.requestId)
      if (row) pendingBodies.push(cdp.send('Network.getResponseBody', { requestId: row.cdp_id })
        .then(r => { row.body = JSON.parse(r.base64Encoded ? Buffer.from(r.body, 'base64').toString() : r.body); row.response_finished_at_unix = Date.now()/1000 })
        .catch(e => { row.body_capture_error = String(e) }))
    }
    if (m.method === 'Network.loadingFailed') {
      const row = report.network.findLast(r => r.cdp_id === m.params.requestId)
      if (row) { row.failed_at_unix = Date.now()/1000; row.loading_error = m.params.errorText; row.canceled = m.params.canceled ?? false }
    }
  })
  for (const c of cfg.cases) {
    current = { id: c.id, status: 'FAIL', pages: [], switches: [] }
    report.records.push(current)
    const start = report.network.length
    try {
      await login(c)
      const navigation = { id: c.id, action: 'actual browser hash navigation', hash: c.hash, at_unix: Date.now()/1000 }
      report.clicks.push(navigation)
      await evaluate(cdp, 'location.hash = ' + JSON.stringify(c.hash))
      const ready = c.kind === 'invalid' ? '!!document.querySelector("[data-testid=invalid-id]")' : c.kind === 'pending' ? '!!document.querySelector("[data-testid=dependency-pending]")' : c.kind === 'error' ? '!!document.querySelector("[role=alert]") && !document.querySelector("[data-testid=context-budget]")' : '!!document.querySelector("[data-testid=context-budget]") && [...document.querySelectorAll("dl.kv dt")].some(x=>x.textContent==="context_pack_id"&&x.nextElementSibling.textContent===' + JSON.stringify(c.entity_id) + ')'
      await waitFor(cdp, ready)
      await expand()
      await Promise.allSettled(pendingBodies)
      const first = await capture(c.id + '-read')
      first.request_ids = report.network.slice(start).filter(n=>n.path===c.path&&n.response_finished_at_unix<=first.dom_at_unix).map(n=>n.request_id)
      const observations = [first]
      if (c.refresh) {
        await click('button', '从 Core 重新读取')
        const limit=Date.now()+10000
        while(report.network.slice(start).filter(n=>n.path===c.path&&n.response_finished_at_unix).length!==2){if(Date.now()>limit)throw new Error('actual refresh incomplete');await new Promise(r=>setTimeout(r,20))}
        await waitFor(cdp, ready)
        await expand();await Promise.allSettled(pendingBodies)
        const second=await capture(c.id+'-refresh')
        second.request_ids=report.network.slice(start).filter(n=>n.path===c.path&&n.response_finished_at_unix<=second.dom_at_unix).map(n=>n.request_id)
        observations.push(second)
      }
      current.navigation=navigation;current.observations=observations;current.status='COLLECTED_NOT_A_VERDICT'
    }catch(e){current.error=String(e.stack??e);current.failure_dom=await capture(c.id+'-failure').catch(()=>null)}
    await Promise.allSettled(pendingBodies)
    current.network=report.network.slice(start)
    console.log(JSON.stringify({variant:c.id,collection:current.status,error:current.error}))
    persist()
  }
  report.credential_storage = await evaluate(cdp, '(async()=>({local:localStorage.length,session:sessionStorage.length,indexed:(await indexedDB.databases()).length}))()')
  report.status = report.records.length === cfg.cases.length && report.records.every(r => r.status === 'COLLECTED_NOT_A_VERDICT') && report.console_errors.length === 0 ? 'COLLECTED_NOT_A_VERDICT' : 'FAIL'
} catch (e) {
  report.error = String(e.stack ?? e)
} finally {
  await Promise.allSettled(pendingBodies)
  cdp?.close()
  if (chrome) {
    chrome.kill('SIGTERM')
    await new Promise(resolve => {
      if (chrome.exitCode !== null) return resolve()
      chrome.once('exit', resolve)
      setTimeout(() => { chrome.kill('SIGKILL'); resolve() }, 2500).unref()
    })
  }
  await writeFile(join(cfg.out, 'browser.json'), safeJson(report))
  await writeFile(join(cfg.out, 'browser-requests.jsonl'), report.network.map(r => JSON.stringify(r)).join('\n') + '\n')
  await writeFile(join(cfg.out, 'click-trace.jsonl'), report.clicks.map(r => JSON.stringify(r)).join('\n') + '\n')
}
process.exitCode = report.status === 'COLLECTED_NOT_A_VERDICT' ? 0 : 1
