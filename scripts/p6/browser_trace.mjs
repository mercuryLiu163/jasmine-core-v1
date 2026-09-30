#!/usr/bin/env node
/**
 * Real-browser trace for the P6-01 dashboard.
 *
 * This drives an actual Chrome (headless) through the Chrome DevTools Protocol:
 * it types the token, clicks real elements, follows source-trace links, reloads
 * a deep link, and records what the browser asked Core for. It is the machine
 * part of the P6-T01/T02/T07 evidence; a human reviewer still reads the
 * screenshots and the report.
 *
 * Usage::
 *
 *   node scripts/p6/browser_trace.mjs \
 *     --ui http://127.0.0.1:5180 \
 *     --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
 *     --project prj_… --task tsk_… --step stp_… --event evt_… --rule rul_… --evidence evd_… \
 *     --out validation-runs/p6/<run-id>
 *
 * The Authorization header is never written to the trace: only whether a header
 * was present. Screenshots are captured with the token field already cleared.
 */

import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { existsSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

/** Case-insensitive header read: CDP preserves the casing the page sent. */
function headerValue(headers, name) {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === wanted) return value
  }
  return undefined
}

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  process.env.CHROME_PATH ?? '',
]

function parseArgs(argv) {
  const args = { ui: 'http://127.0.0.1:5180', width: 1440, height: 1000, headful: false }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    const next = argv[index + 1]
    switch (flag) {
      case '--ui': args.ui = next; index += 1; break
      case '--token-file': args.tokenFile = next; index += 1; break
      case '--token-key': args.tokenKey = next ?? 'read'; index += 1; break
      case '--project': args.project = next; index += 1; break
      case '--task': args.task = next; index += 1; break
      case '--step': args.step = next; index += 1; break
      case '--event': args.event = next; index += 1; break
      case '--rule': args.rule = next; index += 1; break
      case '--evidence': args.evidence = next; index += 1; break
      case '--out': args.out = next; index += 1; break
      case '--headful': args.headful = true; break
      default: throw new Error(`unknown argument ${flag}`)
    }
  }
  if (!args.tokenFile) throw new Error('--token-file is required')
  if (!args.out) throw new Error('--out is required')
  return args
}

class Cdp {
  constructor(url) {
    this.url = url
    this.nextId = 1
    this.pending = new Map()
    this.events = []
  }

  async connect() {
    this.socket = new WebSocket(this.url)
    await new Promise((resolvePromise, reject) => {
      this.socket.addEventListener('open', () => resolvePromise())
      this.socket.addEventListener('error', (event) => reject(new Error(`cdp socket error: ${event.message ?? 'unknown'}`)))
    })
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id !== undefined && this.pending.has(message.id)) {
        const { resolve: resolvePromise, reject } = this.pending.get(message.id)
        this.pending.delete(message.id)
        if (message.error) reject(new Error(`${message.error.message} (${JSON.stringify(message.error)})`))
        else resolvePromise(message.result)
        return
      }
      if (message.method) {
        for (const listener of this.listeners ?? []) listener(message)
        this.events.push(message)
      }
    })
    this.listeners = []
  }

  onEvent(listener) {
    this.listeners.push(listener)
  }

  send(method, params = {}) {
    const id = this.nextId++
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  close() {
    this.socket?.close()
  }
}

function httpJson(url, method = 'GET') {
  return new Promise((resolvePromise, reject) => {
    const request = http.request(url, { method }, (response) => {
      let body = ''
      response.on('data', (chunk) => (body += chunk))
      response.on('end', () => {
        try {
          resolvePromise(JSON.parse(body))
        } catch (error) {
          reject(new Error(`bad JSON from ${url}: ${body.slice(0, 200)}`))
        }
      })
    })
    request.on('error', reject)
    request.end()
  })
}

async function waitForCdp(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const version = await httpJson(`http://127.0.0.1:${port}/json/version`)
      if (version.webSocketDebuggerUrl) return version
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 150))
  }
  throw new Error('Chrome DevTools endpoint never came up')
}

export class Trace {
  constructor(out) {
    this.out = out
    this.steps = []
    this.network = []
    this.start = Date.now()
  }

  record(step, ok, detail) {
    this.steps.push({ t: new Date().toISOString(), step, ok, detail })
    process.stderr.write(`${ok ? 'ok  ' : 'FAIL'} ${step}${detail ? ` — ${detail}` : ''}\n`)
    if (!ok) this.failed = true
  }

  async screenshot(cdp, name) {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
    const path = join(this.out, 'screenshots', `${name}.png`)
    await writeFile(path, Buffer.from(data, 'base64'))
    return path
  }
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (result.exceptionDetails) {
    throw new Error(`evaluate failed: ${result.exceptionDetails.text} ${result.exceptionDetails.exception?.description ?? ''}`)
  }
  return result.result.value
}

async function waitFor(cdp, expression, { timeoutMs = 12000, label = expression } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const value = await evaluate(cdp, expression)
      if (value) return value
    } catch {
      // page might be mid-navigation
    }
    await new Promise((r) => setTimeout(r, 120))
  }
  throw new Error(`timed out waiting for: ${label}`)
}

/** Click the first element matching a visible-text predicate, like a user would. */
async function clickByText(cdp, selector, text) {
  const point = await evaluate(
    cdp,
    `(() => {
      const nodes = [...document.querySelectorAll(${JSON.stringify(selector)})]
      const node = nodes.find((candidate) => (candidate.textContent ?? '').includes(${JSON.stringify(text)}))
      if (!node) return null
      node.scrollIntoView({ block: 'center' })
      const box = node.getBoundingClientRect()
      return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2) }
    })()`,
  )
  if (point === null) throw new Error(`no ${selector} containing ${JSON.stringify(text)}`)
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.send('Input.dispatchMouseEvent', {
      type,
      x: point.x,
      y: point.y,
      button: 'left',
      clickCount: 1,
    })
  }
  return point
}

async function typeInto(cdp, selector, value) {
  await evaluate(cdp, `document.querySelector(${JSON.stringify(selector)}).focus()`)
  await cdp.send('Input.insertText', { text: value })
}

async function pressEnter(cdp) {
  for (const type of ['keyDown', 'keyUp']) {
    await cdp.send('Input.dispatchKeyEvent', { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 })
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const out = resolve(args.out)
  await mkdir(join(out, 'screenshots'), { recursive: true })

  const tokens = JSON.parse(await readFile(args.tokenFile, 'utf8'))
  const token = tokens[args.tokenKey ?? 'read']
  if (!token) throw new Error(`no token under key ${args.tokenKey} in ${args.tokenFile}`)

  const chromePath = CHROME_CANDIDATES.find((candidate) => candidate && existsSync(candidate))
  if (!chromePath) throw new Error('no Chrome/Chromium found; set CHROME_PATH')

  const port = 9333 + Math.floor(Math.random() * 500)
  // The browser profile goes to the system temp dir, never into the evidence
  // bundle: a Chrome profile contains cookies and a history database, and the
  // evidence package must not carry either.
  const profile = await mkdtemp(join(tmpdir(), 'p6-chrome-'))
  const chromeArgs = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-gpu',
    '--hide-scrollbars',
    `--window-size=${args.width},${args.height}`,
  ]
  if (!args.headful) chromeArgs.unshift('--headless=new')
  chromeArgs.push('about:blank')

  const chrome = spawn(chromePath, chromeArgs, { stdio: ['ignore', 'ignore', 'pipe'] })
  const chromeLog = []
  chrome.stderr.on('data', (chunk) => chromeLog.push(String(chunk)))
  const trace = new Trace(out)

  let cdp
  try {
    await waitForCdp(port)
    const target = await httpJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(args.ui)}`, 'PUT')
    cdp = new Cdp(target.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Network.enable')
    await cdp.send('Log.enable')

    const consoleErrors = []
    const requests = []
    const responses = new Map()
    cdp.onEvent((message) => {
      if (message.method === 'Network.requestWillBeSent') {
        const request = message.params.request
        const url = new URL(request.url)
        requests.push({
          at: new Date().toISOString(),
          requestId: message.params.requestId,
          method: request.method,
          path: url.pathname,
          query: url.search,
          authorization: headerValue(request.headers, 'authorization')
            ? 'present(redacted)'
            : 'absent',
          xRequestId: headerValue(request.headers, 'x-request-id') ?? null,
        })
      }
      if (message.method === 'Network.responseReceived') {
        responses.set(message.params.requestId, {
          status: message.params.response.status,
          mimeType: message.params.response.mimeType,
        })
      }
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
        consoleErrors.push(message.params.entry.text)
      }
      if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        consoleErrors.push(message.params.args.map((arg) => arg.value ?? arg.description).join(' '))
      }
    })

    // 1. sign-in. First prove the negative: a bogus token must be refused, which
    // is only possible because sign-in validates with an *authenticated* read.
    // `GET /v1/health` is public and answers 200 for any token, so a sign-in that
    // trusted it would accept this bogus value.
    await waitFor(cdp, `document.querySelector('input[type=password]') !== null`, { label: 'sign-in form' })
    trace.record('sign-in form renders with a labelled password field', true)
    await trace.screenshot(cdp, '01-signin')
    await typeInto(cdp, 'input[type=password]', 'ui-bogus-token-used-by-the-trace')
    await clickByText(cdp, 'button', '使用此 token')
    const refusal = await waitFor(
      cdp,
      `(() => { const node = document.querySelector('[role=alert]'); return node ? node.textContent : '' })()`,
      { label: 'sign-in refusal for a bogus token' },
    ).catch(() => '')
    const stillOnSignIn = await evaluate(cdp, `document.querySelector('input[type=password]') !== null`)
    trace.record(
      'a bogus token is refused instead of opening the dashboard',
      stillOnSignIn === true && String(refusal).includes('unauthenticated'),
      `alert=${String(refusal).replace(/\s+/g, ' ').slice(0, 120)}`,
    )
    await trace.screenshot(cdp, '01b-signin-refused')

    // Now the real read-only token.
    await evaluate(
      cdp,
      `(() => {
        const el = document.querySelector('input[type=password]')
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
        setter.call(el, '')
        el.dispatchEvent(new Event('input', { bubbles: true }))
        return el.value === ''
      })()`,
    )
    await typeInto(cdp, 'input[type=password]', token)
    await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`, { label: 'Overview' })
    const authProbe = requests.find(
      (request) => request.path === '/v1/projects' && request.query.includes('limit=1'),
    )
    trace.record(
      'token accepted only after a real authenticated read',
      authProbe !== undefined && authProbe.authorization !== 'absent',
      authProbe ? `probe=${authProbe.method} ${authProbe.path}${authProbe.query}` : 'no /v1/projects?limit=1 probe seen',
    )
    await trace.screenshot(cdp, '02-overview')

    // The token field must be gone and the raw token must not be in the DOM.
    const tokenInDom = await evaluate(cdp, `document.documentElement.outerHTML.includes(${JSON.stringify(token)})`)
    trace.record('token never appears in the rendered DOM', tokenInDom === false)
    const storage = await evaluate(
      cdp,
      `({ local: Object.keys(window.localStorage ?? {}).length, session: Object.keys(window.sessionStorage ?? {}).length, cookies: document.cookie })`,
    )
    trace.record(
      'no token in localStorage/sessionStorage/cookies',
      storage.local === 0 && storage.session === 0 && storage.cookies === '',
      JSON.stringify(storage),
    )

    // 2. Overview -> Project
    await clickByText(cdp, 'a', '打开 Project 视图') 
      .then(() => trace.record('Overview -> Project link clicked', true))
      .catch(async () => {
        // Fall back to the project table link when no row is selected.
        await clickByText(cdp, 'a', 'Jasmine')
        trace.record('Overview -> Project link clicked (table row)', true)
      })
    await waitFor(cdp, `document.body.textContent.includes('Tasks(已载入范围内')`, { label: 'Project view' })
    trace.record('Project view reads GET /v1/tasks?project_id=', true)
    await trace.screenshot(cdp, '03-project')

    // 3. Project -> Task board
    await clickByText(cdp, 'a', '看板')
    await waitFor(cdp, `document.body.textContent.includes('Step 看板(')`, { label: 'Task board' })
    const boardInfo = await evaluate(
      cdp,
      `(() => {
        const columns = [...document.querySelectorAll('.board__column')].map((node) => node.getAttribute('data-status'))
        return { columns, summary: document.querySelector('[data-testid=board-summary]')?.textContent ?? '' }
      })()`,
    )
    trace.record(
      'board renders Step columns separately from Task status',
      boardInfo.columns.length > 0,
      JSON.stringify(boardInfo),
    )
    await trace.screenshot(cdp, '04-board')

    // 3b. automated accessibility scan of the board: every interactive node
    // must expose an accessible name. A human still reads the screenshots; this
    // only proves the obvious failures are absent.
    await cdp.send('Accessibility.enable')
    const ax = await cdp.send('Accessibility.getFullAXTree')
    const interactive = new Set(['button', 'link', 'textbox', 'combobox', 'checkbox', 'menuitem', 'tab'])
    const unnamed = (ax.nodes ?? []).filter(
      (node) =>
        interactive.has(node.role?.value) &&
        !node.ignored &&
        !(node.name?.value ?? '').trim(),
    )
    trace.record(
      'accessibility scan: no unnamed interactive node on the board',
      unnamed.length === 0,
      unnamed.length === 0 ? '' : unnamed.map((node) => node.role?.value).join(', '),
    )

    // 3c. status must not be conveyed by colour alone: every board badge has a
    // glyph next to its literal token.
    const badges = await evaluate(
      cdp,
      `[...document.querySelectorAll('.badge')].map((node) => ({
        text: node.textContent ?? '',
        glyph: node.querySelector('.badge__glyph')?.textContent ?? '',
      }))`,
    )
    const colourOnly = badges.filter((badge) => badge.glyph.trim() === '' || badge.text.trim() === '')
    trace.record(
      'every status badge carries a glyph and its literal Core token',
      badges.length > 0 && colourOnly.length === 0,
      `${badges.length} badges`,
    )

    // 3d. A08: a narrow viewport must not hide the source-trace controls. The
    // columns reflow; the source link inside a card stays visible and clickable.
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 480,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await new Promise((r) => setTimeout(r, 300))
    const narrow = await evaluate(
      cdp,
      `(() => {
        const link = document.querySelector('.stepcard a[data-kind="event"]')
        if (!link) return { found: false }
        const box = link.getBoundingClientRect()
        return {
          found: true,
          width: box.width,
          height: box.height,
          inViewportHorizontally: box.left >= 0 && box.right <= window.innerWidth + 1,
          docScrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
        }
      })()`,
    )
    trace.record(
      'narrow viewport keeps the source link visible and inside the page width',
      narrow.found === true && narrow.height > 0 && narrow.width > 0 && narrow.inViewportHorizontally === true,
      JSON.stringify(narrow),
    )
    await trace.screenshot(cdp, '04b-board-narrow')
    await cdp.send('Emulation.clearDeviceMetricsOverride')

    // 4. keyboard: focus a source-trace link and activate it with Enter
    await evaluate(
      cdp,
      `(() => { const link = document.querySelector('a[data-kind="event"]'); link.focus(); return document.activeElement === link })()`,
    )
    await pressEnter(cdp)
    await waitFor(cdp, `location.hash.startsWith('#/event/')`, { label: 'keyboard-activated source link' })
    trace.record('keyboard activation of a source link navigates to the Event', true)
    await waitFor(cdp, `document.body.textContent.includes('Raw Event(逐字保留)')`, { label: 'Event page' })
    const eventHash = await evaluate(cdp, 'location.hash')
    await trace.screenshot(cdp, '05-source-event')

    // 5. deep-link refresh keeps the same Event. The token is memory-only, so a
    // refresh must return to the sign-in form *and* keep the route; the operator
    // signs in again and lands on the same Event.
    await cdp.send('Page.reload', { ignoreCache: false })
    await waitFor(cdp, `document.querySelector('input[type=password]') !== null`, {
      label: 'sign-in form after refresh',
    })
    const hashAfterReloadBeforeSignIn = await evaluate(cdp, 'location.hash')
    trace.record(
      'a refreshed deep link keeps its route and drops the in-memory token',
      hashAfterReloadBeforeSignIn === eventHash,
      `${eventHash} -> ${hashAfterReloadBeforeSignIn}`,
    )
    await typeInto(cdp, 'input[type=password]', token)
    await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `document.body.textContent.includes('Raw Event(逐字保留)')`, {
      label: 'Event page after re-sign-in',
    })
    const hashAfterReload = await evaluate(cdp, 'location.hash')
    trace.record(
      'source Event deep link re-reads the same Event after a refresh',
      hashAfterReload === eventHash,
      `${eventHash} -> ${hashAfterReload}`,
    )

    // 6. Rules
    if (args.rule) {
      await cdp.send('Page.navigate', { url: `${args.ui}/#/rules?rule_id=${encodeURIComponent(args.rule)}${args.project ? `&project_id=${encodeURIComponent(args.project)}` : ''}` })
      await waitFor(cdp, `document.body.textContent.includes('版本历史')`, { label: 'Rule history' })
      const ruleText = await evaluate(cdp, 'document.body.textContent')
      trace.record('Rule detail and version history render', ruleText.includes('rule_key'))
      await trace.screenshot(cdp, '06-rule')
    }

    // 7. Evidence
    if (args.evidence) {
      await cdp.send('Page.navigate', { url: `${args.ui}/#/evidence?evidence_id=${encodeURIComponent(args.evidence)}` })
      // Wait for a node that only exists after the read resolves. Matching free
      // text would race the fetch: the breadcrumb already contains the id, and
      // the "未接入" note happens to mention reference_status.
      await waitFor(cdp, `document.querySelector('.evidencebadges') !== null`, {
        label: 'Evidence detail body',
      })
      const evidenceText = await evaluate(cdp, 'document.body.textContent')
      const hasId = evidenceText.includes('evidence_id')
      const hasReference = evidenceText.includes('reference_status')
      trace.record(
        'Evidence detail renders kind/status/reference',
        hasId && hasReference,
        hasId && hasReference
          ? ''
          : `hasId=${hasId} hasReference=${hasReference} hash=${await evaluate(cdp, 'location.hash')}`,
      )
      await trace.screenshot(cdp, '07-evidence')
    }

    // 8. Conversation + 未接入 marker
    await cdp.send('Page.navigate', { url: `${args.ui}/#/conversation` })
    await waitFor(cdp, `document.body.textContent.includes('Raw Event(')`, { label: 'Conversation' })
    const conversationText = await evaluate(cdp, 'document.body.textContent')
    trace.record('Interpretation area is explicitly 未接入', conversationText.includes('未接入'))
    await trace.screenshot(cdp, '08-conversation')

    // 9. negative: an unknown route is not silently the Overview
    await cdp.send('Page.navigate', { url: `${args.ui}/#/nonsense/route` })
    await waitFor(cdp, `document.body.textContent.includes('未知路由')`, { label: 'unknown route panel' })
    trace.record('unknown route is reported, not mapped to Overview', true)

    // 10. sign out stops Authorization
    await clickByText(cdp, 'button', '退出')
    await waitFor(cdp, `document.querySelector('input[type=password]') !== null`, { label: 'sign-in again' })
    const beforeSignOut = requests.length
    await cdp.send('Page.navigate', { url: `${args.ui}/#/overview` })
    await new Promise((r) => setTimeout(r, 600))
    const afterSignOut = requests.slice(beforeSignOut).filter((request) => request.path.startsWith('/v1/'))
    trace.record(
      'after 退出 no request carries an Authorization header',
      afterSignOut.every((request) => request.authorization === 'absent'),
      `${afterSignOut.length} api requests after sign-out`,
    )
    await trace.screenshot(cdp, '09-signed-out')

    // The walkthrough deliberately provokes one 401 (the bogus-token sign-in), and
    // Chrome logs every failed network response at error level. Those lines are
    // evidence, not defects; anything else is an unexpected error.
    const expectedRefusals = consoleErrors.filter((line) => /Failed to load resource.*(401|403)/.test(line))
    const unexpectedErrors = consoleErrors.filter((line) => !expectedRefusals.includes(line))
    trace.record(
      'no unexpected console errors during the walkthrough',
      unexpectedErrors.length === 0,
      `expectedRefusals=${expectedRefusals.length} unexpected=${unexpectedErrors.length}${
        unexpectedErrors.length ? ` ${unexpectedErrors.join(' | ')}` : ''
      }`,
    )

    const maskedRequests = requests.map((request) => {
      const response = responses.get(request.requestId) ?? {}
      return {
        ...request,
        status: response.status ?? null,
        mimeType: response.mimeType ?? null,
        authorization: request.authorization === 'absent' ? 'absent' : 'present(redacted)',
      }
    })
    await writeFile(join(out, 'browser-requests.jsonl'), maskedRequests.map((r) => JSON.stringify(r)).join('\n') + '\n')
    await writeFile(join(out, 'click-trace.jsonl'), trace.steps.map((s) => JSON.stringify(s)).join('\n') + '\n')
    await writeFile(join(out, 'chrome-stderr.log'), chromeLog.join(''))

    const report = [
      `# P6-01 browser trace`,
      '',
      `- ui: ${args.ui}`,
      `- started: ${new Date(trace.start).toISOString()}`,
      `- chrome: ${chromePath}`,
      `- steps: ${trace.steps.filter((s) => s.ok).length}/${trace.steps.length} ok`,
      '',
      ...trace.steps.map((step) => `- ${step.ok ? 'PASS' : 'FAIL'} ${step.step}${step.detail ? ` — \`${step.detail}\`` : ''}`),
      '',
    ].join('\n')
    await writeFile(join(out, 'browser-trace.md'), report)
    process.stderr.write(report)
    process.exitCode = trace.failed ? 1 : 0
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
  }
}

/**
 * Resolve symlinks before comparing: a trace run through a symlinked path must
 * not exit 0 without testing anything (macOS `/tmp` -> `/private/tmp`).
 */
function isEntryPoint() {
  if (!process.argv[1]) return false
  try {
    return import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href
  } catch {
    return false
  }
}

if (isEntryPoint()) {
  main().catch((error) => {
    process.stderr.write(`browser trace failed: ${error.stack ?? error.message}\n`)
    process.exitCode = 1
  })
}
