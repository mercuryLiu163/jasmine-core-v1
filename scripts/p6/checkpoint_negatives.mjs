#!/usr/bin/env node
/** Genuine Chrome DOM + genuine Core responses; only CP12 delays a real Response. */
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
  if (secrets.some(secret => text.includes(secret))) throw new Error('credential in artifact; refused to publish')
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
  const point = await clickByText(cdp, selector, text)
  const r = { id: current.id, action: 'actual pointer click', selector, text, point, at_unix: Date.now() / 1000 }
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
  const dom = await evaluate(cdp, `(() => ({
    hash: location.hash, text: document.body.innerText,
    html: (()=>{const x=document.body.cloneNode(true);for(const i of x.querySelectorAll('input[type=password]'))i.setAttribute('value','[REDACTED]');return x.innerHTML})(),
    inputs: [...document.querySelectorAll('input')].map(x => ({label: document.querySelector('label[for="'+x.id+'"]')?.textContent.trim(), value: x.type==='password'?'[REDACTED]':x.value})),
    links: [...document.querySelectorAll('a')].map(x => ({text:x.textContent.trim(),href:x.getAttribute('href')})),
    jsonblocks: [...document.querySelectorAll('.jsonblock')].map(x=>({label:x.querySelector('.jsonblock__toggle')?.textContent.trim().replace(/^[▾▸]/,'').trim(),text:x.querySelector('pre')?.textContent??null})),
    comparison_status: document.querySelector('[data-testid="resume-comparison-status"]')?.textContent??null,
    checkpoint_boundary: document.querySelector('[data-testid="checkpoint-read-boundary"]')?.textContent??null,
    alerts: [...document.querySelectorAll('[role=alert]')].map(x=>x.textContent),
    pending: !!document.querySelector('[data-testid="dependency-pending"]'),
    invalid: !!document.querySelector('[data-testid="invalid-id"]'),
    note: document.querySelector('[data-testid="resume-note"]')?.textContent??null,
    next_actions: document.querySelector('[data-testid="next-actions"]')?.textContent??null
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
  return { dom, dom_file: file, screenshot, screenshot_clip: clip, at_unix: Date.now() / 1000 }
}
async function expand() {
  if (await evaluate(cdp, '[...document.querySelectorAll("details:not([open]) summary")].some(x=>x.textContent.includes("错误 details"))')) await click('summary', '错误 details')
  const labels = await evaluate(cdp, '[...document.querySelectorAll(".jsonblock__toggle[aria-expanded=false]")].map(x=>x.textContent.trim())')
  for (const label of labels) await click('.jsonblock__toggle', label)
}
async function page(c, p, index) {
  const start = report.network.length
  if (p.kind !== 'signin-error' && await evaluate(cdp, 'location.hash===' + JSON.stringify(p.hash))) {
    await evaluate(cdp, 'location.hash="#/"')
    await waitFor(cdp, 'location.hash==="#/"&&!document.querySelector("[data-testid=checkpoint-read-boundary]")&&!document.querySelector("[data-testid=resume-comparison-status]")')
    report.clicks.push({ id: current.id, action: 'actual browser hash navigation to unmount previous object', hash: '#/', at_unix: Date.now() / 1000 })
  }
  await evaluate(cdp, 'location.hash=' + JSON.stringify(p.hash))
  report.clicks.push({ id: current.id, action: 'actual browser hash navigation', hash: p.hash, at_unix: Date.now() / 1000 })
  const state = p.kind === 'invalid' ? '!!document.querySelector("[data-testid=invalid-id]")'
    : p.kind === 'pending' ? '!!document.querySelector("[data-testid=dependency-pending]")'
      : p.kind === 'error' || p.kind === 'signin-error' ? '!!document.querySelector("[role=alert]")'
        : p.kind === 'empty' ? 'document.body.textContent.includes("空:Checkpoint")'
          : p.kind === 'checkpoint' ? '!!document.querySelector("[data-testid=checkpoint-read-boundary]")&&document.body.textContent.includes(' + JSON.stringify(p.entity_id) + ')'
            : '!!document.querySelector("[data-testid=resume-comparison-status]")&&document.body.textContent.includes(' + JSON.stringify(p.entity_id) + ')'
  await waitFor(cdp, 'location.hash===' + JSON.stringify(p.hash) + '&&(' + state + ')')
  await expand()
  const first = await capture(c.id + '-' + index + '-read')
  const observations = [first]
  if (p.refresh) {
    await click('button', '从 Core 重新读取')
    await waitFor(cdp, state)
    // The old body remains during a same-object reload. Require a fresh completed wire GET.
    for (let n = 0; n < 100; n++) {
      const target = report.network.slice(start).filter(r => r.method === 'GET' && r.path === p.path.split('?')[0] && r.query === (p.path.includes('?') ? '?' + p.path.split('?')[1] : '') && r.status === 200)
      if (target.length >= 2) break
      if (n === 99) throw new Error('fresh readback missing for ' + p.path)
      await new Promise(r => setTimeout(r, 30))
    }
    await expand()
    observations.push(await capture(c.id + '-' + index + '-refresh'))
  }
  await Promise.allSettled(pendingBodies)
  return { definition: p, observations, network: p.kind === 'signin-error' ? current.login_network : report.network.slice(start) }
}
async function lateSwitch(c, kind, a, b) {
  const pa = c.pages.find(p => p.kind === kind && p.entity_id === a)
  const pb = c.pages.find(p => p.kind === kind && p.entity_id === b)
  await page(c, pa, 'switch-' + kind + '-initial')
  const start = report.network.length
  // Remove only the targeted AbortSignal so a genuine Response can arrive late.
  // No headers, URL, payload or Core state are altered. This is labelled transport intervention.
  await evaluate(cdp, `(() => {
    const original=window.fetch;
    window.__checkpointHold={state:'waiting',original};
    window.fetch=async(input,options={})=>{
      const u=new URL(typeof input==='string'?input:input.url,location.href);
      if(u.pathname!==${JSON.stringify(pa.path)}) return original(input,options);
      const response=await original(input,{...options,signal:undefined});
      window.__checkpointHold.body=await response.clone().json();
      window.__checkpointHold.state='held';
      return new Promise(resolve=>{window.__checkpointHold.release=()=>{window.__checkpointHold.state='released';resolve(response)}});
    };
  })()`)
  await click('button', '从 Core 重新读取')
  await waitFor(cdp, 'window.__checkpointHold?.state==="held"')
  const held = await evaluate(cdp, '({state:window.__checkpointHold.state,body:window.__checkpointHold.body})')
  await evaluate(cdp, 'location.hash=' + JSON.stringify(pb.hash))
  await waitFor(cdp, 'document.body.textContent.includes(' + JSON.stringify(b) + ')&&!!document.querySelector(' + JSON.stringify(kind === 'checkpoint' ? '[data-testid=checkpoint-read-boundary]' : '[data-testid=resume-comparison-status]') + ')')
  await expand()
  const before = await capture(c.id + '-switch-' + kind + '-before-release')
  await evaluate(cdp, 'window.__checkpointHold.release();window.fetch=window.__checkpointHold.original')
  await new Promise(r => setTimeout(r, 180))
  await expand()
  const after = await capture(c.id + '-switch-' + kind + '-after-release')
  await Promise.allSettled(pendingBodies)
  return { kind, a, b, held, before, after, network: report.network.slice(start),
    intervention: 'Targeted real GET Response delayed; only AbortSignal removed to force late arrival; body unchanged.' }
}
async function audit(c, network) {
  const r = await fetch(c.core + '/v1/audit?limit=1000&actor_id=' + c.key_metadata.actor_id,
    { headers: { Authorization: 'Bearer ' + c.admin_token } })
  const body = await r.json()
  const ids = new Set(network.map(n => n.request_id).filter(Boolean))
  return { channel: 'separate-authorized-validator-direct-Core-HTTP', path: '/v1/audit?limit=1000&actor_id=' + c.key_metadata.actor_id,
    status: r.status, request_id: r.headers.get('x-request-id'), body, matching: body.entries?.filter(x => ids.has(x.request_id)) ?? [] }
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
        .then(r => { row.body = JSON.parse(r.base64Encoded ? Buffer.from(r.body, 'base64').toString() : r.body) })
        .catch(e => { row.body_capture_error = String(e) }))
    }
  })
  for (const c of cfg.cases) {
    current = { id: c.id, status: 'FAIL', pages: [], switches: [], key_metadata: c.key_metadata }
    report.records.push(current)
    const networkStart = report.network.length
    const start = observer(c).length
    try {
      current.before = snapshot(c)
      await login(c)
      current.login_network = report.network.slice(networkStart)
      for (let i = 0; i < c.pages.length; i++) current.pages.push(await page(c, c.pages[i], i))
      for (const [kind, a, b] of c.switches ?? []) current.switches.push(await lateSwitch(c, kind, a, b))
      if (c.proxy_probe) {
        const requestId = 'proxy-negative-' + crypto.randomUUID()
        const r = await fetch(c.ui + c.proxy_probe.path, { method: 'POST', headers: { 'content-type': 'application/json',
          authorization: 'Bearer ' + c.token, 'x-request-id': requestId }, body: JSON.stringify(c.proxy_probe.body) })
        current.proxy_probe = { channel: 'operator-proxy-probe-not-UI-write', method: 'POST', path: c.proxy_probe.path,
          request_body: c.proxy_probe.body, status: r.status, body: await r.json(), request_id: r.headers.get('x-request-id') }
      }
      await Promise.allSettled(pendingBodies)
      current.network = report.network.slice(networkStart)
      current.audit_read = await audit(c, current.network)
      current.observer_window = observer(c).slice(start)
      current.after = snapshot(c)
      current.status = 'COLLECTED_NOT_A_VERDICT'
    } catch (e) {
      current.error = String(e.stack ?? e)
      if (cdp) current.failure_dom = await capture(c.id + '-failure').catch(() => null)
    } finally {
      await Promise.allSettled(pendingBodies)
      current.network = report.network.slice(networkStart)
      current.audit_read = await audit(c, current.network)
      current.observer_window = observer(c).slice(start)
      current.after = snapshot(c)
    }
    console.log(JSON.stringify({ variant: c.id, collection: current.status, error: current.error }))
    persist()
  }
  report.credential_storage = await evaluate(cdp, '(async()=>({local:localStorage.length,session:sessionStorage.length,indexed:(await indexedDB.databases()).length}))()')
  report.status = report.records.length === 43 && report.records.every(r => r.status === 'COLLECTED_NOT_A_VERDICT') && report.console_errors.length === 0 ? 'COLLECTED_NOT_A_VERDICT' : 'FAIL'
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
