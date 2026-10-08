/**
 * P6-03 real-Core UI pass — the dashboard's writes, submitted from a browser, against real P4.
 *
 * Read `web/REAL-CORE-ACCEPTANCE-PLAN.md` and `scripts/p6/p4_realcore_ui.py`. The Core-level matrix
 * already passes (`*-p6-03-realcore/`, 35/35); this is the half it left: the six writes driven
 * through the panels, with real P4 Core code behind the proxy.
 *
 * The browser helpers are imported from `browser_trace.mjs` rather than copied, so the two harnesses
 * cannot drift in how they click, wait or read a response body.
 *
 * Two honesty notes this file carries into the evidence:
 *
 * 1. the **dropped reply** in the replay case is produced by `p4_realcore_ui.py`'s proxy, not by the
 *    Core: the proxy forwards the command, the Core applies it and answers 200, and only then does
 *    the proxy answer the browser 502. The Core's real 200 is recorded in `proxy-drops.json`, which
 *    is what makes the following real replay meaningful;
 * 2. the engine is P4's wire double, so `mode` is `real-core+wire` and P4 phase acceptance is not
 *    claimed.
 */

import { spawn } from 'node:child_process'
import { writeFile, mkdtemp, readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { rm } from 'node:fs/promises'

import {
  CHROME_CANDIDATES,
  Cdp,
  Trace,
  clickByText,
  evaluate,
  responseBody,
  typeInto,
  waitFor,
  waitForCdp,
} from './browser_trace.mjs'

function parseArgs(argv) {
  const args = { width: 1440, height: 1000, headful: false }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    const next = argv[index + 1]
    switch (flag) {
      case '--ui': args.ui = next; index += 1; break
      case '--core': args.core = next; index += 1; break
      case '--proxy': args.proxy = next; index += 1; break
      case '--tokens': args.tokens = next; index += 1; break
      case '--out': args.out = next; index += 1; break
      case '--headful': args.headful = true; break
      default: throw new Error(`unknown argument ${flag}`)
    }
  }
  for (const required of ['ui', 'core', 'proxy', 'tokens', 'out']) {
    if (!args[required]) throw new Error(`--${required} is required`)
  }
  return args
}

/** A direct call to the real Core (or to the harness proxy), used for what the UI cannot do. */
async function coreCall(origin, method, path, token, body) {
  const response = await fetch(origin + path, {
    method,
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'content-type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  return { status: response.status, body: payload }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const tokens = JSON.parse(await readFile(args.tokens, 'utf8'))
  const contract = JSON.parse(await readFile(
    new URL('../../web/contracts/p4-memory-write.v1.json', import.meta.url), 'utf8'))
  const route = (suffix) => contract.writes.find(
    (write) => write.path_pattern.replace(/\$$/, '').endsWith(suffix))

  const chromePath = CHROME_CANDIDATES.find((candidate) => candidate && existsSync(candidate))
  if (!chromePath) throw new Error('no Chrome/Chromium found; set CHROME_PATH')
  const port = 9333 + Math.floor(Math.random() * 500)
  const profile = await mkdtemp(join(tmpdir(), 'p6-realcore-ui-chrome-'))
  const chromeArgs = [
    `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--disable-background-networking', '--disable-gpu', '--hide-scrollbars',
    `--window-size=${args.width},${args.height}`,
  ]
  if (!args.headful) chromeArgs.unshift('--headless=new')
  chromeArgs.push('about:blank')
  const chrome = spawn(chromePath, chromeArgs, { stdio: ['ignore', 'ignore', 'pipe'] })
  const chromeLog = []
  chrome.stderr.on('data', (chunk) => chromeLog.push(String(chunk)))
  const trace = new Trace(args.out)
  const requests = []
  const responses = new Map()
  let cdp

  try {
    await waitForCdp(port)
    const target = await (await fetch(
      `http://127.0.0.1:${port}/json/new?${encodeURIComponent(args.ui)}`, { method: 'PUT' })).json()
    cdp = new Cdp(target.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Network.enable')
    await cdp.send('Log.enable')
    const consoleErrors = []
    cdp.onEvent((message) => {
      if (message.method === 'Network.requestWillBeSent') {
        const url = new URL(message.params.request.url)
        requests.push({
          requestId: message.params.requestId,
          method: message.params.request.method,
          path: url.pathname,
          postData: typeof message.params.request.postData === 'string'
            ? message.params.request.postData : null,
        })
      }
      if (message.method === 'Network.responseReceived') {
        responses.set(message.params.requestId, { status: message.params.response.status })
      }
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
        consoleErrors.push(message.params.entry.text)
      }
    })

    const pageText = () => evaluate(cdp, `document.body.textContent ?? ''`)
    const goto = async (hash, label, waitForExpression) => {
      await evaluate(cdp, `(() => {
        const next = '#/' + ${JSON.stringify(hash)}
        if (location.hash !== next) location.hash = next
        dispatchEvent(new HashChangeEvent('hashchange'))
        return location.hash
      })()`)
      await waitFor(cdp, `location.hash === ${JSON.stringify('#/' + hash)}`, { label: `${label} hash` })
      await waitFor(cdp, waitForExpression, { label })
    }
    const signIn = async (token, label) => {
      await waitFor(cdp, `document.querySelector('input[type=password]') !== null`,
        { label: `sign-in form (${label})` })
      await typeInto(cdp, 'input[type=password]', token)
      await clickByText(cdp, 'button', '使用此 token')
      await waitFor(cdp, `document.querySelector('input[type=password]') === null`,
        { label: `signed in (${label})` })
    }
    const signOut = async () => {
      await clickByText(cdp, 'button', '退出')
      await waitFor(cdp, `document.querySelector('input[type=password]') !== null`,
        { label: 'signed out' })
    }
    const setControl = async (selector, value, prototype) => evaluate(cdp, `(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      if (!el) return null
      const setter = Object.getOwnPropertyDescriptor(window.${prototype}.prototype, 'value').set
      setter.call(el, ${JSON.stringify(value)})
      el.dispatchEvent(new Event('input', { bubbles: true }))
      el.dispatchEvent(new Event('change', { bubbles: true }))
      return el.value
    })()`)
    const panelText = (title) => evaluate(cdp, `(() => {
      const panel = document.querySelector('[data-write-panel=' +
        ${JSON.stringify(JSON.stringify(title))} + ']')
      return panel ? (panel.textContent ?? '') : ''
    })()`)
    const submit = (title) => clickByText(
      cdp, `[data-write-panel=${JSON.stringify(title)}] [data-testid="write-submit"]`, '')
    const waitPanel = (title, needle) => waitFor(cdp, `(() => {
      const panel = document.querySelector('[data-write-panel=' +
        ${JSON.stringify(JSON.stringify(title))} + ']')
      return panel ? (panel.textContent ?? '').includes(${JSON.stringify(needle)}) : false
    })()`, { label: `panel ${needle}` })
    const waitForCount = async (counter, expected, label) => {
      for (let attempt = 0; attempt < 150; attempt += 1) {
        if (counter() >= expected) return
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
      throw new Error(`timed out waiting for ${label}`)
    }
    const itemReads = () => requests.filter((request) => request.method === 'GET'
      && request.path === `/v1/memory/items/${item}`).length
    const refreshItem = async (label) => {
      const before = itemReads()
      await clickByText(cdp, 'button', '从 Core 重新读取')
      await waitForCount(itemReads, before + 1, label)
    }
    const writeResponse = async (suffix) => {
      const posts = requests.filter((request) => request.method === 'POST'
        && request.path.endsWith(suffix))
      const last = posts[posts.length - 1]
      if (!last) return null
      return {
        path: last.path,
        status: responses.get(last.requestId)?.status ?? null,
        requestBody: last.postData ? JSON.parse(last.postData) : null,
        responseBody: await responseBody(cdp, last.requestId),
        requestId: last.requestId,
      }
    }

    const descriptor = JSON.parse(await readFile(join(args.out, 'descriptor.json'), 'utf8'))
    const items = descriptor.items
    const healthy = items.filter((item) => !item.safe_error_summary)
    const failed = items.find((item) => item.safe_error_summary)
    const project = descriptor.seeded.project_id
    const host = descriptor.host_id
    const item = healthy[0].memory_id
    const PANEL = 'Memory 生命周期写入 (memory:manage)'
    const REFLECT_PANEL = 'reflect 写入 (memory:reflect)'

    // 1. sign in with the human key.
    await signIn(tokens.human_manage, 'human')
    trace.record('the dashboard signs in against real P4 with a key P4 minted', true)

    // 2. the reads render real rows.
    await goto('memory/items', 'memory items',
      `document.querySelector('[data-testid="memory-items"]') !== null`)
    const rows = await evaluate(cdp,
      `document.querySelectorAll('[data-testid="memory-items"] tbody tr').length`)
    await goto(`memory/items/${item}`, 'memory item detail',
      `document.body.textContent.includes('memory_id')`)
    const pending = await evaluate(cdp,
      `document.querySelector('[data-testid="dependency-pending"]') !== null`)
    trace.record('the item list and detail render real P4 rows (no dependency panel, the Core answered)',
      rows >= 1 && pending === false, `${rows} rows`)

    // 3. a key without memory:manage is refused by real Core, and the page says so.
    await signOut()
    await signIn(tokens.human_readonly, 'read-only')
    await goto(`memory/items/${item}`, 'item detail as read-only',
      `document.querySelector('[data-testid="lifecycle-command"]') !== null`)
    await setControl('[data-testid="lifecycle-command"]', 'pin', 'HTMLSelectElement')
    await submit(PANEL)
    await waitPanel(PANEL, '被拒绝')
    const refused = await writeResponse('/pin')
    const refusedText = await panelText(PANEL)
    trace.record('a key without memory:manage is refused by real P4, and the page shows Core\'s refusal',
      refused.status === 403 && refused.responseBody?.error?.code === 'forbidden_scope'
      && refusedText.includes('memory:manage') && !refusedText.includes('已提交'),
      JSON.stringify({ status: refused.status, code: refused.responseBody?.error?.code }))

    // 4. the human key: real 200, the frozen payload, the 7-key transition, and the readback.
    await signOut()
    await signIn(tokens.human_manage, 'human again')
    await goto(`memory/items/${item}`, 'item detail before the pin',
      `document.querySelector('[data-testid="lifecycle-command"]') !== null`)
    const beforePin = await coreCall(args.core, 'GET', `/v1/memory/items/${item}`,
      tokens.human_manage)
    const beforeRevision = beforePin.body.memory.revision
    await setControl('[data-testid="lifecycle-command"]', 'pin', 'HTMLSelectElement')
    await setControl('[data-testid="write-reason"]', 'real-core UI pass: pin', 'HTMLInputElement')
    await submit(PANEL)
    await waitPanel(PANEL, '已提交')
    const pin = await writeResponse('/pin')
    const pinShape = Object.keys(pin.responseBody ?? {}).sort()
    const allowed = [...route('/pin').allowed_body_fields].sort()
    const payloadKeys = Object.keys(pin.requestBody ?? {}).sort()
    await clickByText(cdp, `[data-write-panel=${JSON.stringify(PANEL)}] button`, '从 Core 重新读取')
    await waitFor(cdp, `document.querySelector('[data-testid="write-readback"]') !== null`,
      { label: 'write readback' })
    // The block renders as soon as the write succeeds, which is *before* the forced re-read lands —
    // so waiting for the block alone would read the pre-write row and call it a readback. Wait for
    // the fresh revision to appear.
    await waitFor(cdp, `document.body.textContent.includes('r${beforeRevision + 1}')`,
      { label: `the readback shows r${beforeRevision + 1}` })
    const readbackText = await pageText()
    const audit = await coreCall(args.core, 'GET',
      `/v1/events/${pin.responseBody.event.event_id}`, tokens.human_manage)
    trace.record('the pin reaches real P4: frozen payload, a real 7-key transition, the command Event read back, and the row re-read',
      pin.status === 200 && pinShape.join(',') === ['after', 'before', 'event', 'job', 'memory',
        'non_authoritative', 'replayed'].join(',')
      && payloadKeys.every((key) => allowed.includes(key) || key === 'event_id')
      && pin.responseBody.replayed === false
      && pin.responseBody.memory.revision === beforeRevision + 1
      && audit.status === 200
      && audit.body.event.event_id === pin.responseBody.event.event_id
      && readbackText.includes(`r${beforeRevision + 1}`),
      JSON.stringify({ status: pin.status, shape: pinShape, payloadKeys,
        auditStatus: audit.status }))

    // 5. a real second writer moves the item; the panel submits its stale basis.
    const current = await coreCall(args.core, 'GET', `/v1/memory/items/${item}`, tokens.human_manage)
    const moved = await coreCall(args.core, 'POST', `/v1/memory/items/${item}/unpin`,
      tokens.human_manage, { expected_revision: current.body.memory.revision, host_id: host,
        event_id: `evt_${String(Date.now()).padStart(26, '0')}` })
    const postsBefore = requests.filter((request) => request.method === 'POST'
      && request.path.endsWith('/unpin')).length
    await setControl('[data-testid="lifecycle-command"]', 'unpin', 'HTMLSelectElement')
    await setControl('[data-testid="write-reason"]', 'real-core UI pass: stale basis',
      'HTMLInputElement')
    await submit(PANEL)
    await waitPanel(PANEL, '冲突,已保留草稿')
    const conflict = await writeResponse('/unpin')
    const conflictText = await panelText(PANEL)
    const draftKept = await evaluate(cdp,
      `(document.querySelector('[data-testid="write-reason"]')?.value ?? '') !== ''`)
    await new Promise((resolve) => setTimeout(resolve, 300))
    const conflictPosts = requests.filter((request) => request.method === 'POST'
      && request.path.endsWith('/unpin')).length - postsBefore
    trace.record('a real second writer makes the panel\'s basis stale: Core answers revision_conflict, the draft is kept, and nothing is retried',
      moved.status === 200 && conflict.status === 409
      && conflict.responseBody?.error?.code === 'revision_conflict'
      && conflict.responseBody?.error?.details?.current_revision === moved.body.memory.revision
      && conflictText.includes('revision_conflict')
      && conflictText.includes(String(moved.body.memory.revision))
      && draftKept && conflictPosts === 1,
      JSON.stringify({ moved: moved.status, conflict: conflict.status,
        current: conflict.responseBody?.error?.details?.current_revision, conflictPosts }))

    // 6. the replay: the harness proxy loses the reply, real P4 replays on the resubmit.
    await refreshItem('a real re-read after the conflict')
    const armed = await coreCall(args.proxy, 'POST',
      `/v1/_p6_drop_next?path=/v1/memory/items/${item}/pin`, null, null)
    const beforeReplay = await coreCall(args.core, 'GET', `/v1/memory/items/${item}`,
      tokens.human_manage)
    const revisionBeforeReplay = beforeReplay.body.memory.revision
    await setControl('[data-testid="lifecycle-command"]', 'pin', 'HTMLSelectElement')
    await setControl('[data-testid="write-reason"]', 'real-core UI pass: dropped reply',
      'HTMLInputElement')
    await submit(PANEL)
    await waitPanel(PANEL, '结果未知')
    const dropped = await writeResponse('/pin')
    await submit(PANEL)
    await waitPanel(PANEL, 'replayed: Core 返回了原命令结果')
    const posts = requests.filter((request) => request.method === 'POST'
      && request.path.endsWith('/pin'))
    const replayed = {
      path: dropped.path,
      requestBody: posts[posts.length - 1].postData ? JSON.parse(posts[posts.length - 1].postData) : null,
      responseBody: await responseBody(cdp, posts[posts.length - 1].requestId),
      status: responses.get(posts[posts.length - 1].requestId)?.status ?? null,
    }
    const afterReplay = await coreCall(args.core, 'GET', `/v1/memory/items/${item}`,
      tokens.human_manage)
    trace.record('after the harness lost one reply, the resubmit is a REAL replay: same event_id, five keys, and the row moved exactly once',
      armed.status === 200 && dropped.status === 502
      && replayed.status === 200 && replayed.responseBody?.replayed === true
      && Object.keys(replayed.responseBody).sort().join(',') ===
        ['event', 'job', 'memory', 'non_authoritative', 'replayed'].join(',')
      && replayed.requestBody?.event_id === dropped.requestBody?.event_id
      && afterReplay.body.memory.revision === revisionBeforeReplay + 1,
      JSON.stringify({ droppedStatus: dropped.status, replayStatus: replayed.status,
        replayed: replayed.responseBody?.replayed,
        revisionBefore: revisionBeforeReplay, revisionAfter: afterReplay.body.memory.revision }))

    // 7. reflect: eight keys, the marker from Core's own field, and the real trace.
    // The reflect route is reached by hash; if the panel is not there, the snapshot says what
    // rendered instead — a screenshot cannot be diffed, and a bare timeout explains nothing.
    await goto('memory/reflect', 'reflect view',
      `document.querySelector('section[aria-label^="Memory"]') !== null`)
    await writeFile(join(args.out, 'reflect-dom.html'),
      await evaluate(cdp, `document.body.innerHTML`))
    await waitFor(cdp, `document.querySelector('[data-write-panel=${JSON.stringify(REFLECT_PANEL)}]') !== null`,
      { label: 'reflect write panel' })
    const reflectDisabled = await evaluate(cdp,
      `document.querySelector('[data-write-panel=${JSON.stringify(REFLECT_PANEL)}] [data-testid="write-submit"]').disabled === true`)
    await setControl('[data-testid="reflect-query"]', 'real-core UI pass: what did we learn?',
      'HTMLTextAreaElement')
    await submit(REFLECT_PANEL)
    await waitFor(cdp, `document.querySelector('[data-testid="reflect-outcome"]') !== null`,
      { label: 'reflect outcome' })
    const reflect = await writeResponse('/reflect')
    const reflectedText = await pageText()
    const traceRead = reflect.responseBody?.trace_id
      ? await coreCall(args.core, 'GET', `/v1/memory/traces/${reflect.responseBody.trace_id}`,
        tokens.human_manage)
      : { status: null }
    trace.record('reflect is an explicit submit, answers real P4\'s eight keys, is marked non-authoritative from Core\'s field, and its trace reads back',
      reflectDisabled === true && reflect.status === 200
      && Object.keys(reflect.responseBody).sort().join(',') ===
        ['banks', 'based_on_memory_ids', 'error_code', 'non_authoritative', 'safe_error_summary',
          'status', 'text', 'trace_id'].join(',')
      && reflect.responseBody.non_authoritative === true
      && reflectedText.includes(reflect.responseBody.trace_id)
      && traceRead.status === 200,
      JSON.stringify({ reflect: reflect.status, trace: traceRead.status }))

    // 8. the withholding rule as the two pages show it, on a row that really carries engine text.
    const engineText = failed.safe_error_summary
    await goto(`memory/items/${failed.memory_id}`, 'failed item as the human',
      `document.body.textContent.includes('memory_id')`)
    const humanSees = (await pageText()).includes(engineText)
    await signOut()
    await signIn(tokens.agent_read, 'agent')
    await goto(`memory/items/${failed.memory_id}`, 'failed item as the agent',
      `document.body.textContent.includes('memory_id')`)
    const agentPage = await pageText()
    trace.record('the withholding rule as the pages show it: the human sees the engine text, the agent does not',
      humanSees === true && agentPage.includes(engineText) === false
      && agentPage.includes('memory_id'),
      JSON.stringify({ humanSees, agentSees: agentPage.includes(engineText) }))

    // 9. the read views still offer no write affordance.
    await goto('memory/items', 'items list for the control scan',
      `document.querySelector('[data-testid="memory-items"]') !== null`)
    const controls = await evaluate(cdp, `(() => {
      const panel = document.querySelector('section[aria-label^="Memory"]')
      if (!panel) return ['<no memory panel>']
      return [...panel.querySelectorAll('button, input[type=submit]')]
        .map((node) => (node.textContent ?? '').trim())
    })()`)
    const unexpected = controls.filter((name) =>
      !['从 Core 重新读取', '打开'].includes(name) && !/^[▸▾]/.test(name)
      && !name.startsWith('复制'))
    trace.record('the read views still offer no write affordance', unexpected.length === 0,
      JSON.stringify(unexpected))

    // 10. no unexpected console errors (403/409/502 are provoked on purpose).
    const expected = consoleErrors.filter((line) => /Failed to load resource/.test(line)
      && /(403|409|502)/.test(line))
    const unexpectedErrors = consoleErrors.filter((line) => !expected.includes(line))
    trace.record('no unexpected console errors during the pass', unexpectedErrors.length === 0,
      `${unexpectedErrors.length} unexpected`)
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
    writeFile(join(args.out, 'chrome-stderr.log'), chromeLog.join('')).catch(() => {})
    const masked = requests.map((request) => ({
      method: request.method, path: request.path, status: responses.get(request.requestId)?.status ?? null,
      body: request.postData ? JSON.parse(request.postData) : null,
    }))
    await writeFile(join(args.out, 'browser-write-responses.jsonl'),
      masked.filter((entry) => entry.method === 'POST').map((entry) => JSON.stringify(entry)).join('\n') + '\n')
    await writeFile(join(args.out, 'click-trace.jsonl'),
      trace.steps.map((step) => JSON.stringify(step)).join('\n') + '\n')
    await writeFile(join(args.out, 'browser-trace.md'),
      ['# P6-03 real-Core UI pass', '',
        ...trace.steps.map((step) => `- ${step.ok ? 'PASS' : 'FAIL'} ${step.step}${step.detail ? ` — \`${step.detail}\`` : ''}`),
        ''].join('\n'))
    process.exitCode = trace.failed ? 1 : 0
  }
}

main().catch((error) => {
  process.stderr.write(`real-core UI pass failed: ${error.stack ?? error.message}\n`)
  process.exitCode = 1
})
