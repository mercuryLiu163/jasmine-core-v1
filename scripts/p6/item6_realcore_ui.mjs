#!/usr/bin/env node
/** Real Chrome item 6 confirmation clicks; private descriptor arrives via stdin.
 * Never save headers/token/profile. The sole forced 502 occurs AFTER real Core
 * submit committed (CDP response stage), so status recovery must read real Truth.
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import net from 'node:net'
import { createHash } from 'node:crypto'
import { Cdp, CHROME_CANDIDATES, waitForCdp, httpJson, evaluate, waitFor, clickByText, typeInto } from './browser_trace.mjs'

let input = ''
for await (const chunk of process.stdin) input += chunk
const config = JSON.parse(input)
const phase = process.argv[2] ?? 'loop'
const report = { phase, mode: 'real-core-real-chrome', checks: [], clicks: [], network: [], preparations: [], receipts: [] }
let browser, cdp, profile
function check(name, passed, details) {
  report.checks.push({ name, passed: Boolean(passed), details })
  if (!passed) throw new Error(name)
}
const note = `Escaped note: <script>globalThis.item6Injected=2</script> ${config.syntheticSecret}; INFO only.`
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  return JSON.stringify(value)
}
async function api(method, path, body) {
  return evaluate(cdp, `(async () => { const response = await fetch(${JSON.stringify(path)}, {method:${JSON.stringify(method)},headers:{Authorization:'Bearer '+${JSON.stringify(config.humanToken)},'Content-Type':'application/json'},${body === undefined ? '' : 'body:' + JSON.stringify(JSON.stringify(body))}}); return {status:response.status,body:await response.json()}; })()`)
}
async function freePort() {
  const server = net.createServer()
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise((resolve) => server.close(resolve))
  return port
}
const selector = (id) => `[data-testid="${id}"]`
async function click(id, text = '') {
  const point = await clickByText(cdp, selector(id), text)
  report.clicks.push({ id, point, at: new Date().toISOString() })
}
async function select(id, value) {
  // Drive the real browser DOM control; confirmation buttons/checkboxes use
  // CDP pointer input. No application handler or backend is bypassed here.
  const sel = selector(id)
  await waitFor(cdp, `!!document.querySelector(${JSON.stringify(sel)})`)
  const index = await evaluate(cdp, `Array.from(document.querySelector(${JSON.stringify(sel)}).options).findIndex(o => o.value === ${JSON.stringify(value)})`)
  check('select_option_' + id, index >= 0, { value })
  await evaluate(cdp, `(() => { const control = document.querySelector(${JSON.stringify(sel)}); control.value = ${JSON.stringify(value)}; control.dispatchEvent(new Event('change', {bubbles:true})); })()`)
  await waitFor(cdp, `document.querySelector(${JSON.stringify(sel)}).value === ${JSON.stringify(value)}`)
  report.clicks.push({ id, action: 'browser-dom-select-change', value })
}
async function navigate(path) {
  await cdp.send('Page.navigate', { url: config.ui + path })
  await waitFor(cdp, `!!(document.querySelector('input[type="password"]') || document.querySelector('[data-testid="confirmation-panel"]') || document.querySelector('[data-testid="step-picker"]'))`)
  if (await evaluate(cdp, `!!document.querySelector('input[type="password"]')`)) {
    await typeInto(cdp, 'input[type="password"]', config.humanToken)
    const point = await clickByText(cdp, 'button', '使用此 token')
    report.clicks.push({ id: 'signin', point })
    await waitFor(cdp, `!document.querySelector('input[type="password"]')`)
  }
}
async function screenshot(name) {
  // A labelled derivative is captured only after raw comparisons. Restore DOM
  // immediately, before any affirmation/submit action. Core bytes stay intact.
  await evaluate(cdp, `(() => { window.__item6ScreenshotRestore = []; const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let node; while(node=walker.nextNode()) { if(node.nodeValue.includes(${JSON.stringify(config.syntheticSecret)})) { window.__item6ScreenshotRestore.push([node,node.nodeValue]); node.nodeValue=node.nodeValue.split(${JSON.stringify(config.syntheticSecret)}).join('[REDACTED_SYNTHETIC_SECRET_PATTERN]'); } } for(const control of document.querySelectorAll('textarea,input')) { if(control.value.includes(${JSON.stringify(config.syntheticSecret)})) { window.__item6ScreenshotRestore.push([control,control.value,true]); control.value=control.value.split(${JSON.stringify(config.syntheticSecret)}).join('[REDACTED_SYNTHETIC_SECRET_PATTERN]'); } } })()`)
  try {
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
    await writeFile(join(config.out, `${phase}-${name}-derived-redacted.png`), Buffer.from(shot.data, 'base64'))
  } finally {
    await evaluate(cdp, `(() => { for(const [node,value,control] of window.__item6ScreenshotRestore ?? []) { if(control) node.value=value; else node.nodeValue=value; } delete window.__item6ScreenshotRestore; })()`)
  }
}
async function readJson(id) {
  return JSON.parse(await evaluate(cdp, `document.querySelector(${JSON.stringify(selector(id))}).textContent`))
}
async function confirm(step, target, dropResponse = false) {
  await navigate(`/#/task/${config.task}/confirmation`)
  await waitFor(cdp, `document.querySelector('[data-testid="confirmation-prepare"]') && !document.querySelector('[data-testid="confirmation-prepare"]').disabled`)
  await select('confirmation-step', step ?? '')
  await select('confirmation-target', target)
  // Clear DOM textarea through the native setter before CDP inserts this exact note.
  await evaluate(cdp, `(() => { const n=document.querySelector('[data-testid="confirmation-note"]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(n,''); n.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  await typeInto(cdp, selector('confirmation-note'), note)
  await click('confirmation-prepare')
  await waitFor(cdp, `!!document.querySelector('[data-testid="confirmation-review"]')`)
  const material = await readJson('confirmation-review')
  check('complete_review_identity_and_object', material.actor.key_id && material.task.task_id === config.task && (material.step?.step_id ?? null) === step)
  check('affirmation_initially_unchecked', await evaluate(cdp, `!document.querySelector('[data-testid="confirmation-affirm"]').checked && document.querySelector('[data-testid="confirmation-submit"]').disabled`))
  check('full_review_no_dom_html_execution', await evaluate(cdp, `!document.querySelector('[data-testid="confirmation-review"] script')`))
  check('full_raw_secret_pattern_rule_visible', material.applicable_rules.some((rule) => rule.content === config.ruleText) && (await evaluate(cdp, `document.querySelector('[data-testid="confirmation-review"]').textContent.includes(${JSON.stringify(config.syntheticSecret)})`)))
  const preparedRow = report.network.findLast((row) => row.path.endsWith('/confirmations/prepare') && row.status === 201)
  check('real_prepare_network_observed', !!preparedRow)
  const prepareWire = await cdp.send('Network.getResponseBody', { requestId: preparedRow.cdp_id })
  const prepared = JSON.parse(prepareWire.base64Encoded ? Buffer.from(prepareWire.body, 'base64').toString() : prepareWire.body)
  check('displayed_raw_sha_matches_server', createHash('sha256').update(canonical(material)).digest('hex') === prepared.review_sha256 && canonical(material) === canonical(prepared.review_document))
  await screenshot(`review-${report.preparations.length + 1}`)
  await click('confirmation-affirm')
  await waitFor(cdp, `!document.querySelector('[data-testid="confirmation-submit"]').disabled`)
  let intercepted = false
  if (dropResponse) {
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*evidence/confirmations/submit', requestStage: 'Response' }] })
    cdp.onEvent((message) => {
      if (message.method !== 'Fetch.requestPaused') return
      const event = message.params
      void (async () => {
        if (!intercepted && event.responseStatusCode === 201) {
          intercepted = true
          const network = report.network.find((row) => row.cdp_id === event.networkId)
          if (network) { network.core_status = 201; network.request_id = event.responseHeaders.find((h) => h.name.toLowerCase() === 'x-request-id')?.value }
          await cdp.send('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: 502, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify({ error: { code: 'gate_dropped_response', message: 'Harness dropped a committed response' } })).toString('base64') })
        } else await cdp.send('Fetch.continueResponse', { requestId: event.requestId })
      })().catch((error) => { report.interception_error = String(error) })
    })
  }
  await click('confirmation-submit')
  if (dropResponse) {
    await waitFor(cdp, `document.querySelector('[data-testid="confirmation-message"]').textContent.includes('UNKNOWN_OUTCOME')`)
    check('lost_committed_response_is_unknown', intercepted && !(await evaluate(cdp, `!!document.querySelector('[data-testid="confirmation-readback"]')`)))
    await screenshot('unknown-committed-response')
    const pendingId = await evaluate(cdp, `document.querySelector('[data-testid^="confirmation-recovery-status-"]').getAttribute('data-testid')`)
    const pendingCommand = pendingId.replace('confirmation-recovery-status-', '')
    const submitsBeforeRecovery = report.network.filter((row) => row.path.endsWith('/confirmations/submit')).length
    await click(pendingId)
    await waitFor(cdp, `!!document.querySelector('[data-testid="confirmation-readback"]')`)
    const recovered = await readJson('confirmation-readback')
    check('status_recovery_preserves_command_and_no_new_submit', recovered.command_event.event_id === pendingCommand && report.network.filter((row) => row.path.endsWith('/confirmations/submit')).length === submitsBeforeRecovery)
    await cdp.send('Fetch.disable')
  }
  await waitFor(cdp, `!!document.querySelector('[data-testid="confirmation-readback"]')`)
  const raw = await readJson('confirmation-readback')
  check('three_actual_get_readback', raw.evidence.status === 'INFO' && raw.evidence.kind === 'USER_CONFIRMATION' && raw.evidence.source_event_id === raw.source_event.event_id && raw.evidence.change_event_id === raw.command_event.event_id)
  check('new_dedicated_human_source', raw.source_event.event_type === 'human.confirmation_submitted' && raw.source_event.source_system === 'core-web-confirmation-v1')
  check('raw_note_preserved_and_html_escaped', raw.source_event.payload.text === note && await evaluate(cdp, `globalThis.item6Injected === undefined`))
  const statusRead = await api('GET', '/v1/evidence/confirmations/' + prepared.preparation_id)
  check('status_full_original_material_and_note', statusRead.status === 200 && canonical(statusRead.body.preparation.review_document) === canonical(material) && statusRead.body.consumption.receipt.source_event.payload.text === note)
  const replay = await api('POST', '/v1/evidence/confirmations/submit', { event_id: raw.command_event.event_id, preparation_id: prepared.preparation_id, review_sha256: prepared.review_sha256, affirmed: true, note })
  check('exact_replay_original_raw_note', replay.status === 200 && replay.body.replayed === true && replay.body.source_event.payload.text === note)
  const list = await api('GET', '/v1/events?task_id=' + config.task)
  check('event_list_original_raw_note', list.status === 200 && list.body.events.some((event) => event.event_id === raw.source_event.event_id && event.payload.text === note))
  report.receipts.push(raw)
  report.preparations.push(raw.source_event.source_event_id)
  await screenshot(`readback-${report.preparations.length}`)
  return raw
}
async function stepState(status) {
  await navigate(`/#/task/${config.task}/write`)
  await select('step-picker', config.step)
  await select('step-transition-status', status)
  const panel = '[data-write-panel="Step 状态转换 (state:write)"]'
  await waitFor(cdp, `document.querySelector(${JSON.stringify(panel + ' [data-testid="write-submit"]')}) && !document.querySelector(${JSON.stringify(panel + ' [data-testid="write-submit"]')}).disabled`)
  const point = await clickByText(cdp, panel + ' button', '提交 Step 转换')
  report.clicks.push({ action: 'separate-state-step', status, point })
  await waitFor(cdp, `document.querySelector(${JSON.stringify(panel)}).textContent.includes('已提交')`)
  check('separate_step_' + status, !(await evaluate(cdp, `!!document.querySelector(${JSON.stringify(panel + ' [data-testid="write-evidence-blocked"]')})`)))
}
try {
  check('recognized_phase', phase === 'loop' || phase === 'history')
  const chrome = CHROME_CANDIDATES.find((path) => path && existsSync(path))
  check('chrome_available', !!chrome)
  profile = await mkdtemp(join(tmpdir(), 'jasmine-item6-chrome-'))
  const debuggingPort = await freePort()
  browser = spawn(chrome, [`--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${profile}`, '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-gpu', 'about:blank'], { stdio: 'ignore' })
  await waitForCdp(debuggingPort)
  const target = await httpJson(`http://127.0.0.1:${debuggingPort}/json/new?about:blank`, 'PUT')
  cdp = new Cdp(target.webSocketDebuggerUrl)
  await cdp.connect()
  report.browser = await cdp.send('Browser.getVersion')
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false })
  cdp.onEvent((message) => {
    if (message.method !== 'Network.requestWillBeSent') return
    const event = message.params
    const url = new URL(event.request.url)
    if (url.pathname.startsWith('/v1/')) report.network.push({ cdp_id: event.requestId, method: event.request.method, path: url.pathname, at: new Date().toISOString() })
  })
  cdp.onEvent((message) => {
    if (message.method !== 'Network.responseReceived') return
    const event = message.params
    const row = report.network.findLast((entry) => entry.cdp_id === event.requestId)
    if (row) { row.status = event.response.status; if (!row.core_status) row.core_status = event.response.status; row.request_id ??= Object.entries(event.response.headers).find(([key]) => key.toLowerCase() === 'x-request-id')?.[1] }
  })
  if (phase === 'history') {
    await navigate(`/#/task/${config.task}/confirmation`)
    await waitFor(cdp, `!!document.querySelector('[data-testid="confirmation-status-id"]')`)
    await typeInto(cdp, selector('confirmation-status-id'), config.preparationId)
    await click('confirmation-status-read')
    await waitFor(cdp, `!!(document.querySelector('[data-testid="confirmation-status"]') && document.querySelector('[data-testid="confirmation-readback"]'))`)
    const status = await readJson('confirmation-status')
    check('fresh_browser_restart_history', status.consumption !== null && status.preparation.preparation_id === config.preparationId)
    const raw = await readJson('confirmation-readback')
    await screenshot('restart-historical-readback')
    await cdp.send('Page.navigate', { url: config.ui + '/#/event/' + raw.source_event.event_id })
    await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(raw.source_event.event_id)}) && document.body.textContent.includes('human.confirmation_submitted')`)
    await cdp.send('Page.reload')
    await waitFor(cdp, `!!document.querySelector('input[type="password"]')`)
    await typeInto(cdp, 'input[type="password"]', config.humanToken)
    await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(raw.source_event.event_id)}) && document.body.textContent.includes('human.confirmation_submitted')`)
    check('source_deeplink_reload', true)
    await screenshot('source-deeplink-reload')
  } else {
    await confirm(config.step, 'VERIFIED', true)
    await stepState('VERIFIED')
    await confirm(config.step, 'ACCEPTED')
    await stepState('ACCEPTED')
    // Re-run real producer after final Steps, then record its actual exit/output.
    const actual = spawnSync(config.testArgv[0], config.testArgv.slice(1), { cwd: config.workspace, encoding: 'utf8' })
    check('task_actual_fixture_tests', actual.status === 0)
    const text = actual.stdout + actual.stderr
    await writeFile(join(config.workspace, 'results/task.txt'), text)
    await writeFile(join(config.out, 'executed-task.txt'), text)
    const captured = await fetch(config.core + '/v1/tool-results', { method: 'POST', headers: { Authorization: 'Bearer ' + config.systemToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ task_id: config.task, host_id: config.host, codex_session_id: 'isolated-gate', turn_id: 'task-test', tool_use_id: 'task-executed-tests', tool_name: 'item6_unittest', tool_input: { command: config.testCommand }, tool_response: { exit_code: actual.status, output: text }, kind: 'TEST', artifact_uri: 'workspace:/results/task.txt' }) })
    check('task_test_captured_by_real_core', captured.status === 201)
    report.task_test_receipt = await captured.json()
    await confirm(null, 'ACCEPTED')
    await navigate(`/#/task/${config.task}/write`)
    const panel = '[data-write-panel="Task 验收 (state:accept,Evidence 门槛)"]'
    await waitFor(cdp, `document.querySelector(${JSON.stringify(panel + ' [data-testid="write-submit"]')}) && !document.querySelector(${JSON.stringify(panel + ' [data-testid="write-submit"]')}).disabled`)
    const point = await clickByText(cdp, panel + ' button', '提交验收')
    report.clicks.push({ action: 'separate-state-task-accept', point })
    await waitFor(cdp, `document.querySelector(${JSON.stringify(panel)}).textContent.includes('已提交')`)
    check('separate_task_accept', !(await evaluate(cdp, `!!document.querySelector(${JSON.stringify(panel + ' [data-testid="write-evidence-blocked"]')})`)))
    await screenshot('task-accepted')
  }
  check('all_required_network_readbacks', report.network.some((row) => row.path.startsWith('/v1/events/') && row.status === 200) && report.network.some((row) => row.path.startsWith('/v1/evidence/') && row.status === 200))
  check('no_bearer_in_bundle', !JSON.stringify(report).includes(config.humanToken) && !JSON.stringify(report).includes(config.systemToken))
  check('no_persistent_browser_credentials', await evaluate(cdp, `(async () => localStorage.length === 0 && sessionStorage.length === 0 && (await indexedDB.databases()).length === 0)()`))
  check('nonempty_checks', report.checks.length >= (phase === 'history' ? 5 : 20))
  const required = phase === 'history' ? {
    recognized_phase: 1, chrome_available: 1, fresh_browser_restart_history: 1,
    source_deeplink_reload: 1, all_required_network_readbacks: 1,
    no_bearer_in_bundle: 1, no_persistent_browser_credentials: 1,
  } : {
    recognized_phase: 1, chrome_available: 1, complete_review_identity_and_object: 3,
    affirmation_initially_unchecked: 3, full_review_no_dom_html_execution: 3,
    full_raw_secret_pattern_rule_visible: 3, real_prepare_network_observed: 3,
    displayed_raw_sha_matches_server: 3, lost_committed_response_is_unknown: 1,
    status_recovery_preserves_command_and_no_new_submit: 1,
    three_actual_get_readback: 3, new_dedicated_human_source: 3,
    raw_note_preserved_and_html_escaped: 3, status_full_original_material_and_note: 3,
    exact_replay_original_raw_note: 3, event_list_original_raw_note: 3,
    separate_step_VERIFIED: 1, separate_step_ACCEPTED: 1,
    task_actual_fixture_tests: 1, task_test_captured_by_real_core: 1,
    separate_task_accept: 1, all_required_network_readbacks: 1,
    no_bearer_in_bundle: 1, no_persistent_browser_credentials: 1,
  }
  const counts = Object.fromEntries(Object.keys(required).map((name) => [name, report.checks.filter((row) => row.name === name && row.passed).length]))
  check('all_required_browser_scenarios_and_counts', Object.entries(required).every(([name, count]) => counts[name] === count) && report.checks.every((row) => row.passed), { required, counts })
  if (phase === 'loop') {
    const expected = [{ step: config.step, target: 'VERIFIED' }, { step: config.step, target: 'ACCEPTED' }, { step: null, target: 'ACCEPTED' }]
    check('exact_three_confirmation_object_target_chains', report.receipts.length === 3 && new Set(report.preparations).size === 3 && report.receipts.every((raw, index) => raw.source_event.task_id === config.task && raw.command_event.payload.step_id === expected[index].step && raw.command_event.payload.target_status === expected[index].target) && new Set(report.receipts.map((raw) => raw.command_event.event_id)).size === 3 && new Set(report.receipts.map((raw) => raw.evidence.evidence_id)).size === 3)
  }
  report.status = 'PASS'
} catch (error) {
  report.status = 'FAIL'
  report.error = String(error.stack ?? error)
  if (cdp) await screenshot('failure').catch(() => {})
} finally {
  cdp?.close()
  if (browser) {
    browser.kill('SIGTERM')
    await new Promise((resolve) => { if (browser.exitCode !== null) resolve(); else { browser.once('exit', resolve); setTimeout(() => { browser.kill('SIGKILL'); resolve() }, 3000).unref() } })
  }
  if (profile) await rm(profile, { recursive: true, force: true })
  report.redaction = { mode: 'derived-only', replacement: '[REDACTED_SYNTHETIC_SECRET_PATTERN]', screenshots: 'raw comparisons precede screenshot-only DOM substitutions; restored before signing' }
  const derived = JSON.stringify(report, null, 2).split(config.syntheticSecret).join('[REDACTED_SYNTHETIC_SECRET_PATTERN]')
  await writeFile(join(config.out, `browser-${phase}.json`), derived + '\n')
}
console.log(JSON.stringify({ status: report.status, checks: report.checks.length, error: report.error }))
process.exitCode = report.status === 'PASS' ? 0 : 1
