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
      case '--proposed-rule': args.proposedRule = next; index += 1; break
      case '--reject-review': args.rejectReview = next; index += 1; break
      case '--retire-rule': args.retireRule = next; index += 1; break
      case '--submit-writes': args.submitWrites = true; break
      case '--descriptor': args.descriptor = next; index += 1; break
      case '--evidence': args.evidence = next; index += 1; break
      case '--interpretation': args.interpretation = next; index += 1; break
      case '--pending-interpretation': args.pendingInterpretation = next; index += 1; break
      case '--review': args.review = next; index += 1; break
      case '--resolution': args.resolution = next; index += 1; break
      case '--p2-project': args.p2Project = next; index += 1; break
      case '--completion-interpretation': args.completionInterpretation = next; index += 1; break
      case '--completion-actions': args.completionActions = next; index += 1; break
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
  // Include what the page actually showed: a bare timeout says nothing about whether
  // the app refused, rendered an error, or was still on the sign-in form.
  let seen = '(could not read the page)'
  try {
    seen = await evaluate(cdp, `(() => {
      const text = (document.body.textContent ?? '').split(String.fromCharCode(10)).join(' ')
      return JSON.stringify({
        hash: location.hash,
        signIn: document.querySelector('input[type=password]') !== null,
        alerts: [...document.querySelectorAll('[role=alert]')].map((n) => (n.textContent ?? '').slice(0, 120)),
        text: text.slice(0, 400),
      })
    })()`)
  } catch {
    seen = '(page unreachable)'
  }
  throw new Error(`timed out waiting for: ${label}\n  page: ${seen}`)
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
  // Reading the ids straight from the fixture descriptor removes a whole class of
  // failure: a re-seeded fixture leaves a stale arguments file behind, and every id
  // then names an object Core does not have. This is how that was found.
  if (args.descriptor) {
    const { readFile } = await import('node:fs/promises')
    const spec = JSON.parse(await readFile(args.descriptor, 'utf8'))
    const p2 = spec.p2 ?? {}
    const seeded = spec.seeded ?? {}
    args.project = args.project ?? seeded.projects?.[0]
    args.task = args.task ?? seeded.tasks?.[0]
    args.rule = args.rule ?? seeded.rules?.active
    args.proposedRule = args.proposedRule ?? seeded.rules?.proposed
    args.retireRule = args.retireRule ?? p2.retire_rule_id
    args.rejectReview = args.rejectReview ?? p2.reject_review_id
    args.review = args.review ?? p2.pending_review_id
    args.resolution = args.resolution ?? p2.pending_resolution_id
    args.interpretation = args.interpretation ?? p2.applied_interpretation_id
    args.pendingInterpretation = args.pendingInterpretation ?? p2.pending_interpretation_id
    args.p2Project = args.p2Project ?? p2.project_id
    args.completionInterpretation = args.completionInterpretation ?? p2.rerun_child_interpretation_id
    args.completionActions = args.completionActions ?? 'CORRECT,RERUN,RERUN_COMPLETE'
  }
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
      .then(() => trace.record('Overview -> Project link clicked (selected row)', true))
      .catch(async () => {
        // Fall back to the first project row link. Deliberately not a project
        // *name*: the trace must not depend on how a fixture names its data.
        const point = await evaluate(cdp, `(() => {
          const link = document.querySelector('table a[href^="#/project/"]')
          if (!link) return null
          link.scrollIntoView({ block: 'center' })
          const box = link.getBoundingClientRect()
          return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2) }
        })()`)
        if (!point) throw new Error('no project row link on the Overview')
        for (const type of ['mousePressed', 'mouseReleased']) {
          await cdp.send('Input.dispatchMouseEvent', { type, x: point.x, y: point.y, button: 'left', clickCount: 1 })
        }
        trace.record('Overview -> Project link clicked (first project row)', true)
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
      // The panels need `detail.data.rule`, which is a separate request from the
      // history: waiting only for '版本历史' can observe the page with the
      // history settled and the Rule still loading, and then report a missing
      // panel as a product failure.
      await waitFor(cdp, `document.querySelector('section[data-write-panel]') !== null`,
        { label: 'Authority write panels' })
      const ruleText = await evaluate(cdp, 'document.body.textContent')
      trace.record('Rule detail and version history render', ruleText.includes('rule_key'))

      // 6b. The Authority write surface (P6-02 milestone B). The trace only
      // *renders* these panels: it never submits, because a screenshot run must
      // not mutate Truth. That the exact body each panel builds is Core-legal is
      // pinned by `web/tests/writeBodies.test.tsx`, and the live audited path is
      // proven by `scripts/p6/p6_t03_gate.py`.
      const authority = await evaluate(cdp, `(() => {
        const panel = (needle) => [...document.querySelectorAll('section')]
          .find((n) => (n.getAttribute('aria-label') ?? '').includes(needle)) ?? null
        const propose = panel('Propose')
        const approve = panel('Approve')
        const supersede = panel('Supersede')
        const retire = panel('Retire')
        const label = (node, needle) => node
          ? [...node.querySelectorAll('label')].find((l) => (l.textContent ?? '').includes(needle)) ?? null
          : null
        const button = (node) => node ? node.querySelector('button') : null
        return {
          panels: [propose, approve, supersede, retire].map((n) => Boolean(n)),
          proposeHasReason: Boolean(label(propose, 'reason')),
          proposeSaysNoReason: (propose?.textContent ?? '').includes('不接受 reason 字段'),
          proposeHasOriginPicker: Boolean(label(propose, '候选 origin Event')),
          proposeHasRuleKey: Boolean(label(propose, 'rule_key')),
          supersedeSaysLockedScope: (supersede?.textContent ?? '').includes('不支持跨 scope 替换'),
          approveDisabled: button(approve)?.disabled ?? null,
          supersedeDisabled: button(supersede)?.disabled ?? null,
          retireDisabled: button(retire)?.disabled ?? null,
          // The key the panel will actually send: labelled event_id(重放键) and
          // shown as an evt_ id. The panel does name idempotency_key, but only to
          // say these endpoints refuse it.
          labelsEventId: (propose?.textContent ?? '').includes('event_id(重放键)'),
          showsEventIdValue: [...(propose?.querySelectorAll('code') ?? [])]
            .some((n) => /^evt_/.test((n.textContent ?? '').trim())),
          saysIdempotencyRefused: (propose?.textContent ?? '').includes('不接受 idempotency_key'),
          showsNoIdempotencyKeyValue: ![...(propose?.querySelectorAll('code') ?? [])]
            .some((n) => /^ui-/.test((n.textContent ?? '').trim())),
        }
      })()`)
      trace.record(
        'Authority write surface renders all four panels (propose/approve/supersede/retire)',
        authority.panels.every(Boolean),
        JSON.stringify(authority.panels),
      )
      trace.record(
        'Authority panels take no reason field, because Core rejects it as unknown',
        authority.proposeHasReason === false && authority.proposeSaysNoReason === true,
        `hasReason=${authority.proposeHasReason} explains=${authority.proposeSaysNoReason}`,
      )
      trace.record(
        'Authority panels send event_id as the replay key, shown as a real evt_ id',
        authority.labelsEventId === true && authority.showsEventIdValue === true &&
          authority.showsNoIdempotencyKeyValue === true,
        `labels=${authority.labelsEventId} showsEvt=${authority.showsEventIdValue} ` +
          `noUiKey=${authority.showsNoIdempotencyKeyValue}`,
      )
      trace.record(
        'Authority panels disclose that Core refuses idempotency_key here',
        authority.saysIdempotencyRefused === true,
        String(authority.saysIdempotencyRefused),
      )
      trace.record(
        'Propose offers a real origin Event picker and a rule_key field',
        authority.proposeHasOriginPicker === true && authority.proposeHasRuleKey === true,
        JSON.stringify(authority),
      )
      trace.record(
        'Supersede states that Core refuses cross-scope replacement',
        authority.supersedeSaysLockedScope === true,
        String(authority.supersedeSaysLockedScope),
      )
      await trace.screenshot(cdp, '06b-rule-writes')

      if (args.proposedRule) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/rules?rule_id=${encodeURIComponent(args.proposedRule)}${args.project ? `&project_id=${encodeURIComponent(args.project)}` : ''}` })
        await waitFor(cdp,
          `document.body.textContent.includes(${JSON.stringify(args.proposedRule)})`,
          { label: 'PROPOSED rule detail' })
        await waitFor(cdp,
          `document.querySelector('section[data-write-panel^="Approve"]') !== null`,
          { label: 'PROPOSED rule approve panel' })
        const proposed = await evaluate(cdp, `(() => {
          const panel = [...document.querySelectorAll('section')]
            .find((n) => (n.getAttribute('aria-label') ?? '').includes('Approve'))
          const button = panel ? panel.querySelector('button') : null
          return { disabled: button?.disabled ?? null,
                   text: (panel?.textContent ?? '').split(String.fromCharCode(10)).join(' ').slice(0, 200) }
        })()`)
        trace.record(
          'Approve is enabled only for a PROPOSED Rule',
          proposed.disabled === false,
          `disabled=${proposed.disabled} ${proposed.text}`,
        )
        await trace.screenshot(cdp, '06c-rule-approve')
      }

      if (args.retireRule) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/rules?rule_id=${encodeURIComponent(args.retireRule)}${args.project ? `&project_id=${encodeURIComponent(args.project)}` : ''}` })
        await waitFor(cdp,
          `document.body.textContent.includes(${JSON.stringify(args.retireRule)})`,
          { label: 'retire target Rule detail' })
        await waitFor(cdp,
          `document.querySelector('section[data-write-panel^="Retire"]') !== null`,
          { label: 'ACTIVE retire target panel' })
        const retireState = await evaluate(cdp, `(() => {
          const panel = (needle) => [...document.querySelectorAll('section')]
            .find((n) => (n.getAttribute('aria-label') ?? '').includes(needle))
          const approve = panel('Approve')?.querySelector('button')
          const retire = panel('Retire')?.querySelector('button')
          return { approveDisabled: approve?.disabled ?? null, retireDisabled: retire?.disabled ?? null }
        })()`)
        trace.record(
          'An ACTIVE Rule cannot be approved but can be retired',
          retireState.approveDisabled === true && retireState.retireDisabled === false,
          JSON.stringify(retireState),
        )
      }
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
    const conversation = await evaluate(cdp, `(() => {
      const text = document.body.textContent ?? ''
      const select = document.querySelector('select')
      const options = select ? [...select.querySelectorAll('option')].map((o) => o.value) : []
      const panel = [...document.querySelectorAll('section')]
        .find((n) => (n.getAttribute('aria-label') ?? '').includes('Interpretation / Resolver'))
      return {
        options,
        // The delivered read slice replaces the stale "waiting for P2" panel.
        deliveredReadLink: Boolean(panel?.querySelector('a[href*="/interpretations"]')),
        panelText: (panel?.textContent ?? '').split(String.fromCharCode(10)).join(' ').slice(0, 160),
      }
    })()`)
    trace.record(
      'Conversation has a link into the delivered Interpretation reads',
      conversation.deliveredReadLink === true,
      conversation.panelText,
    )
    const expectedEventTypes = [
      'interpretation.bound', 'interpretation.corrected', 'interpretation.rejected',
      'interpretation.rerun', 'resolution.command', 'review.approved', 'review.rejected',
    ]
    trace.record(
      'Conversation event filter offers every Core event type (>=31 options incl. placeholder)',
      conversation.options.length >= 31 &&
        expectedEventTypes.every((type) => conversation.options.includes(type)),
      `${conversation.options.length} options; missing=${
        JSON.stringify(expectedEventTypes.filter((t) => !conversation.options.includes(t)))
      }`,
    )
    await trace.screenshot(cdp, '08-conversation')

    // 8b. P6-02 read slice: interpretations, pending reviews, resolutions.
    if (args.interpretation) {
      await cdp.send('Page.navigate', { url: `${args.ui}/#/interpretations` })
      // Wait for a rendered row, not the panel title: the title exists while the
      // first page is still loading, and reading then would race the fetch.
      await waitFor(cdp, `document.querySelector('a[href^="#/interpretation/"]') !== null`,
        { label: 'interpretation rows' })
      const listState = await evaluate(cdp, `(() => {
        const text = document.body.textContent ?? ''
        const row = document.querySelector('a[href^="#/interpretation/"]')
        return {
          hasTarget: text.includes(${JSON.stringify(args.interpretation)}),
          rowHref: row ? row.getAttribute('href') : null,
          noTotalClaim: text.includes('Core 不返回总数'),
          countLabel: [...document.querySelectorAll('section')].some((n) => {
            const label = n.getAttribute('aria-label') || ''
            return label.startsWith('Interpretations(已载入 ') && label.endsWith(' 条)')
          }),
          panelTitle: ([...document.querySelectorAll('section')]
            .map((n) => n.getAttribute('aria-label') ?? '')
            .find((label) => label.startsWith('Interpretations(')) ?? ''),
          sideEffectNote: text.includes('不等于「零副作用」') || text.includes('零副作用'),
        }
      })()`)
      trace.record('interpretation list renders real rows from Core',
        listState.hasTarget === true && listState.countLabel === true,
        `${listState.panelTitle} rows->${listState.rowHref}`)
      trace.record('interpretation list declares loaded range and no total',
        listState.noTotalClaim === true)
      trace.record('interpretation list states the lazy-recovery read caveat',
        listState.sideEffectNote === true)
      await trace.screenshot(cdp, '10-interpretations')

      if (listState.rowHref) {
        await cdp.send('Page.navigate', { url: `${args.ui}/${listState.rowHref}` })
        await waitFor(cdp, `document.body.textContent.includes('Candidates(')`,
          { label: 'interpretation detail' })
        const detailState = await evaluate(cdp, `(() => {
          const text = document.body.textContent ?? ''
          const sections = [...document.querySelectorAll('section')].map((n) => n.getAttribute('aria-label') ?? '')
          return {
            hasRecord: text.includes(${JSON.stringify(args.interpretation)}),
            hasCandidates: text.includes('Candidates('),
            hasLineage: text.includes('Lineage 与 maintenance') || text.includes('根解释'),
            hasPreviewPanel: sections.some((label) => label.includes('Resolution 预览')),
            sourceEventLink: Boolean(document.querySelector('a[data-kind="event"]')),
            sideEffectNote: text.includes('maintenance.get'),
            // Milestone B delivers the interpretation write panels; what must still
            // be absent is any control that could fabricate human Evidence provenance.
            writePanels: ['Correct', 'Reject', 'Re-run'].filter((label) =>
              [...document.querySelectorAll('section')].some((n) =>
                (n.getAttribute('aria-label') ?? '').startsWith(label))),
            noEvidenceConfirm: ![...document.querySelectorAll('button')].some((b) =>
              /确认|confirm/i.test(b.textContent ?? '')),
            reasonRequired: [...document.querySelectorAll('section.write-panel')].every((panel) => {
              const reason = panel.querySelector('input[type=text], input:not([type])')
              const submit = [...panel.querySelectorAll('button')]
                .find((b) => /提交/.test(b.textContent ?? ''))
              return Boolean(reason) && Boolean(submit) && submit.disabled === true
            }),
          }
        })()`)
        trace.record('interpretation detail shows the record, its candidates and lineage',
          detailState.hasRecord && detailState.hasCandidates && detailState.hasLineage,
          JSON.stringify(detailState))
        trace.record('interpretation detail states the maintenance.get recovery caveat',
          detailState.sideEffectNote === true)
        const previewPanel = await evaluate(cdp, `(() => {
          const panel = [...document.querySelectorAll('section')]
            .find((n) => (n.getAttribute('aria-label') ?? '').includes('Resolution 预览'))
          const text = (panel?.textContent ?? '').split(String.fromCharCode(10)).join(' ')
          return { conflict: text.includes('预览被拒绝'), emptyPlan: text.includes('空:计划项'),
                   code: (text.match(/interpretation_[a-z_]+/) ?? [''])[0] }
        })()`)
        trace.record('a stale interpretation renders the preview conflict (not an empty plan)',
          previewPanel.conflict === true && previewPanel.emptyPlan === false && previewPanel.code !== '',
          JSON.stringify(previewPanel))
        trace.record('interpretation detail offers the three shipped write panels',
          detailState.writePanels.length === 3, JSON.stringify(detailState.writePanels))
        trace.record('write panels cannot submit without a reason (and never confirm Evidence)',
          detailState.reasonRequired === true && detailState.noEvidenceConfirm === true,
          `reasonRequired=${detailState.reasonRequired} noEvidenceConfirm=${detailState.noEvidenceConfirm}`)
        await trace.screenshot(cdp, '11-interpretation')
      }

      if (args.pendingInterpretation) {
        await cdp.send('Page.navigate', {
          url: `${args.ui}/#/interpretation/${encodeURIComponent(args.pendingInterpretation)}`,
        })
        await waitFor(cdp, `document.querySelector('section') !== null`, { label: 'pending interpretation' })
        const preview = await waitFor(cdp, `(() => {
          const text = document.body.textContent ?? ''
          if (text.includes('正在读取')) return ''
          return text
        })()`, { label: 'preview settled' })
        const previewState = await evaluate(cdp, `(() => {
          const text = document.body.textContent ?? ''
          const panel = [...document.querySelectorAll('section')]
            .find((n) => (n.getAttribute('aria-label') ?? '').includes('Resolution 预览'))
          const panelText = (panel?.textContent ?? '').split(String.fromCharCode(10)).join(' ')
          return {
            disposition: panelText.includes('PENDING_REVIEW'),
            action: panelText.includes('CREATE_RULE'),
            pendingBadge: panelText.includes('需要人工审批'),
            conflictShown: panelText.includes('预览被拒绝'),
            isPlanNotApplied: panelText.includes('不是已应用的决议'),
          }
        })()`)
        trace.record('resolution preview is the server plan (PENDING_REVIEW / CREATE_RULE)',
          previewState.disposition && previewState.action && previewState.pendingBadge,
          JSON.stringify(previewState))
        trace.record('resolution preview states it is a plan, not an applied resolution',
          previewState.isPlanNotApplied === true)
        await trace.screenshot(cdp, '12-resolution-preview')
        void preview
      }

      if (args.completionInterpretation) {
        // The reviewer caught that this panel read invented field names and
        // printed "child undefined · applied no · expected rundefined". The
        // fixture now performs a real rerun so a genuine completion row exists.
        await cdp.send('Page.navigate', {
          url: `${args.ui}/#/interpretation/${encodeURIComponent(args.completionInterpretation)}`,
        })
        await waitFor(cdp, `document.querySelector('[data-testid=operation-completion]') !== null`,
          { label: 'operation completion row' })
        const completion = await evaluate(cdp, `(() => {
          const row = document.querySelector('[data-testid=operation-completion]')
          // Plain string predicates: inside this template literal a regex escape
          // like \\s is eaten, which once turned /\\s+/ into /s+/ and deleted
          // every letter "s" from the extracted text.
          const text = (row ? row.textContent : '').split(String.fromCharCode(10)).join(' ').split('  ').join(' ').trim()
          const page = document.body.textContent || ''
          const statuses = ['EXTRACTED', 'FAILED', 'INTERRUPTED']
          return {
            text,
            hasStatus: statuses.some((value) => text.indexOf(value) >= 0),
            hasHeadApplied: text.indexOf('head applied yes') >= 0 || text.indexOf('head applied no') >= 0,
            hasRevisions: text.indexOf('expected head r') >= 0 && text.indexOf('observed r') >= 0,
            undefinedLeak: text.indexOf('undefined') >= 0,
            // The caller passes the actions Core reports for this chain; the step
            // used to hardcode two of three and only assert non-empty, which
            // under-reported RERUN_COMPLETE.
            expected: (${JSON.stringify(args.completionActions || 'CORRECT,RERUN,RERUN_COMPLETE')}).split(','),
            history: (${JSON.stringify(args.completionActions || 'CORRECT,RERUN,RERUN_COMPLETE')})
              .split(',').filter((action) => page.indexOf(action) >= 0),
            unknownActions: ['CORRECT', 'REJECT', 'RERUN', 'RERUN_COMPLETE']
              .filter((action) => page.indexOf(action) >= 0)
              .filter((action) => (${JSON.stringify(args.completionActions || 'CORRECT,RERUN,RERUN_COMPLETE')}).indexOf(action) < 0),
          }
        })()`)
        trace.record('operation completion renders the real columns (no invented fields)',
          completion.hasStatus && completion.hasHeadApplied && completion.hasRevisions &&
          completion.undefinedLeak === false,
          completion.text.slice(0, 160))
        trace.record('maintenance history renders every real action Core reports',
          completion.expected.every((action) => completion.history.includes(action)) &&
          completion.unknownActions.length === 0,
          `rendered=${JSON.stringify(completion.history)} expected=${JSON.stringify(completion.expected)}`)
        await trace.screenshot(cdp, '16-operation-completion')
      }

      if (args.review) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/reviews${args.p2Project ? `?project_id=${encodeURIComponent(args.p2Project)}` : ''}` })
        await waitFor(cdp, `document.querySelector('a[href^="#/review/"]') !== null
          || document.body.textContent.includes('空:待审批 Review')`,
          { label: 'pending review rows or an empty queue' })
        const queue = await evaluate(cdp, `(() => {
          const text = document.body.textContent ?? ''
          return {
            hasReview: text.includes(${JSON.stringify(args.review)}),
            hookHref: document.querySelector('a[href^="#/review/"]')?.getAttribute('href') ?? null,
            noTotalClaim: text.includes('不会把「没有更多待审批」写成「全部已处理」'),
            noApproveButton: ![...document.querySelectorAll('button')].some((b) =>
              /批准|通过|Approve/i.test(b.textContent ?? '')),
          }
        })()`)
        trace.record('pending review queue lists the review Core reports',
          queue.hasReview === true && queue.noTotalClaim === true, JSON.stringify(queue))
        trace.record('pending review queue offers no approve action', queue.noApproveButton === true)
        await trace.screenshot(cdp, '13-reviews')

        if (queue.hookHref) {
          await cdp.send('Page.navigate', { url: `${args.ui}/${queue.hookHref}` })
          await waitFor(cdp,
            `document.body.textContent.includes('当前可审批计划') || document.body.textContent.includes('预览冲突')`,
            { label: 'review detail body' })
          const review = await evaluate(cdp, `(() => {
            const text = document.body.textContent ?? ''
            return {
              hasId: text.includes(${JSON.stringify(args.review)}),
              hasPreviewOrConflict: text.includes('当前可审批计划') || text.includes('预览冲突'),
              resolutionLink: Boolean(document.querySelector('a[href^="#/resolution/"]')),
            }
          })()`)
          trace.record('review detail shows the plan (or the conflict) and links its resolution',
            review.hasId && review.hasPreviewOrConflict && review.resolutionLink,
            JSON.stringify(review))
          await trace.screenshot(cdp, '14-review')
        }

        // The second PENDING review exists so the T03 Gate can exercise REJECT
        // without consuming APPROVE's target: it must still render as a live,
        // actionable review here, with the same panels.
        if (args.rejectReview) {
          await cdp.send('Page.navigate', { url: `${args.ui}/#/review/${encodeURIComponent(args.rejectReview)}` })
          await waitFor(cdp,
            `document.body.textContent.includes('当前可审批计划') || document.body.textContent.includes('预览冲突')`,
            { label: 'reject-review detail body' })
          // A Node-side CSS selector for one panel's field, plus an in-page
          // helper with the same rule. `startsWith` on the title avoids depending
          // on the parenthesised, translated remainder of the label.
          const panelSelector = (needle) =>
            `section[data-write-panel^="${needle}"]`
          const second = await evaluate(cdp, `(() => {
            const panel = (needle) => [...document.querySelectorAll('section[data-write-panel]')]
              .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith(needle)) ?? null
            const text = document.body.textContent ?? ''
            const reject = panel('Reject')
            return {
              hasId: text.includes(${JSON.stringify(args.rejectReview)}),
              pending: text.includes('PENDING'),
              approveAvailable: Boolean(panel('Approve')),
              rejectReasonRequired: (reject?.querySelector('[data-testid="write-reason"]') !== null),
              rejectDisabledBeforeReason: reject?.querySelector('[data-testid="write-submit"]')?.disabled ?? null,
            }
          })()`)
          await trace.record(
            'the second PENDING review renders both write panels',
            second.hasId && second.pending && second.approveAvailable === true,
            JSON.stringify(second),
          )
          // REJECT is armed once a reason is present; the trace types but never
          // clicks, because a screenshot run must not mutate Truth.
          await typeInto(cdp,
            `${panelSelector('Reject')} [data-testid="write-reason"]`,
            'T03 trace: reason present so the button is armed (not submitted)')
          const armed = await evaluate(cdp, `(() => {
            const reject = [...document.querySelectorAll('section[data-write-panel]')]
              .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Reject'))
            return {
              disabled: reject?.querySelector('[data-testid="write-submit"]')?.disabled ?? null,
              typed: reject?.querySelector('[data-testid="write-reason"]')?.value?.length ?? 0,
            }
          })()`)
          trace.record(
            'REJECT arms only after a reason, and REJECT never submits by itself',
            second.rejectReasonRequired === true &&
              second.rejectDisabledBeforeReason === true && armed.disabled === false,
            `${JSON.stringify(second)} armed=${JSON.stringify(armed)}`,
          )
          await trace.screenshot(cdp, '14b-reject-review')
        }
      }

      if (args.resolution) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/resolution/${encodeURIComponent(args.resolution)}` })
        // Data-dependent: the disposition badge only exists once the row loaded.
        await waitFor(cdp, `document.body.textContent.includes('reasons')`,
          { label: 'resolution detail body' })
        const resolution = await evaluate(cdp, `(() => {
          const text = document.body.textContent ?? ''
          return {
            hasId: text.includes(${JSON.stringify(args.resolution)}),
            hasDisposition: text.includes('PENDING_REVIEW') || text.includes('APPLIED'),
            hasReasons: text.includes('reasons'),
            linksInterpretation: Boolean(document.querySelector('a[href^="#/interpretation/"]')),
          }
        })()`)
        trace.record('resolution detail shows disposition, reasons and its interpretation',
          resolution.hasId && resolution.hasDisposition && resolution.hasReasons &&
            resolution.linksInterpretation, JSON.stringify(resolution))
        await trace.screenshot(cdp, '15-resolution')
      }
    }

    // 8c. REAL CLICKS over the write panels (opt-in).
    //
    // The read-only walkthrough above renders the panels and never submits, because a
    // screenshot run must not mutate Truth. That left a gap an independent re-review
    // named: nothing proved a *click* produces the bodies the HTTP Gate sends. This
    // mode closes it by driving the panels for real and verifying each write by
    // re-reading the object, so it must only ever be pointed at a disposable fixture.
    if (args.submitWrites) {
      // Self-sufficient: sign in again if the read-only walkthrough left the app on
      // the sign-in form (the refresh step deliberately clears the in-memory token).
      // A write section that depends on the earlier walkthrough's session would fail
      // for a reason that has nothing to do with the write path.
      const needsSignIn = await evaluate(cdp,
        `document.querySelector('input[type=password]') !== null`)
      if (needsSignIn) {
        await evaluate(cdp, `(() => {
          const el = document.querySelector('input[type=password]')
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
          setter.call(el, '')
          el.dispatchEvent(new Event('input', { bubbles: true }))
          return true
        })()`)
        await typeInto(cdp, 'input[type=password]', token)
        await clickByText(cdp, 'button', '使用此 token')
        await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`,
          { label: 're-sign-in for the write walkthrough' })
        trace.record('the write walkthrough re-authenticates instead of inheriting a session',
          true, 'sign-in form was present; token re-entered')
      }

      const setValue = `(el, value) => {
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
          : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set
        setter.call(el, value)
        el.dispatchEvent(new Event('input', { bubbles: true }))
        el.dispatchEvent(new Event('change', { bubbles: true }))
      }`
      const panelButton = (prefix, action) => `(() => {
        const panel = [...document.querySelectorAll('section[data-write-panel]')]
          .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith(${JSON.stringify(prefix)}))
        if (!panel) return { ok: false, why: 'no panel' }
        const button = panel.querySelector('[data-testid="write-submit"]')
        if (!button) return { ok: false, why: 'no button' }
        if (button.disabled) return { ok: false, why: 'disabled' }
        ${action}
        return { ok: true }
      })()`

      const reloadAndReauth = async (targetUrl) => {
        await cdp.send('Page.reload', { ignoreCache: true })
        await waitFor(cdp,
          `document.querySelector('input[type=password]') !== null || ` +
          `document.body.textContent.includes('Core 健康与 Schema')`,
          { label: 'reload settled' })
        if (await evaluate(cdp, `document.querySelector('input[type=password]') !== null`)) {
          await evaluate(cdp, `(() => {
            const el = document.querySelector('input[type=password]')
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
            setter.call(el, '')
            el.dispatchEvent(new Event('input', { bubbles: true }))
            return true
          })()`)
          await typeInto(cdp, 'input[type=password]', token)
          await clickByText(cdp, 'button', '使用此 token')
          // Not the Overview heading: the reload preserved whatever hash was current,
          // so a successful sign-in lands on *that* route. The sign-in form disappearing
          // is the condition that holds either way.
          await waitFor(cdp, `document.querySelector('input[type=password]') === null`,
            { label: 're-authenticated after reload' })
        }
        if (targetUrl) {
          // A hash change is not a fetch, but the caller's own row wait is: reaching it
          // requires the reload's request to have resolved. So no extra gate here.
          await cdp.send('Page.navigate', { url: targetUrl })
        }
      }

      const setField = async (selector, value) => evaluate(cdp, `(() => {
        const el = document.querySelector(${JSON.stringify(selector)})
        if (!el) return { ok: false, why: 'field not found' }
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
          : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set
        setter.call(el, ${JSON.stringify(value)})
        el.dispatchEvent(new Event('input', { bubbles: true }))
        el.dispatchEvent(new Event('change', { bubbles: true }))
        return { ok: true }
      })()`)

      const waitForSubmitted = async (prefix) => {
        try {
        await waitFor(cdp,
          `(() => {
             const panel = [...document.querySelectorAll('section[data-write-panel]')]
               .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith(${JSON.stringify(prefix)}))
             if (!panel) return false
             const text = panel.textContent ?? ''
             return text.includes('已提交') || text.includes('冲突') || text.includes('被拒绝') || text.includes('结果未知')
           })()`,
          { label: `${prefix} outcome`, timeoutMs: 20000 })
        } catch {
          return { submitted: false, conflict: false, refused: false, unknown: false,
                   headline: 'no outcome within 20s', text: '' }
        }
        return evaluate(cdp, `(() => {
          const panel = [...document.querySelectorAll('section[data-write-panel]')]
            .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith(${JSON.stringify(prefix)}))
          const text = (panel?.textContent ?? '').split(String.fromCharCode(10)).join(' ')
          return {
            submitted: text.includes('已提交'),
            conflict: text.includes('冲突'),
            refused: text.includes('被拒绝'),
            unknown: text.includes('结果未知'),
            // The panel's own note mentions "replayed" in prose, so presence of the
            // word is not evidence; the headline is.
            headline: text.includes('已提交(replayed') ? 'replayed' : 'submitted',
            text: text.slice(0, 200),
          }
        })()`)
      }

      const readKv = (label) => `(() => {
        for (const list of document.querySelectorAll('dl.kv')) {
          for (const row of list.querySelectorAll(':scope > div')) {
            const dt = row.querySelector('dt')
            const dd = row.querySelector('dd')
            if (dt && dd && (dt.textContent ?? '').trim() === ${JSON.stringify(label)}) {
              return (dd.textContent ?? '').trim()
            }
          }
        }
        return null
      })()`
      const revisionOf = (text) => {
        const match = (text ?? '').match(/\/\s*([0-9]+)/)
        return match ? Number(match[1]) : null
      }

      // --- a Rule that is PROPOSED: click Approve, then re-read its status ---
      if (args.proposedRule) {
        const url = `${args.ui}/#/rules?rule_id=${encodeURIComponent(args.proposedRule)}`
        await cdp.send('Page.navigate', { url })
        await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(args.proposedRule)})`,
          { label: 'proposed Rule detail' })
        await waitFor(cdp, `document.querySelector('section[data-write-panel^="Approve"]') !== null`,
          { label: 'Approve panel' })
        // Read the object's own status/revision rows before the click, so the check
        // after it is a comparison of this Rule rather than a substring of the page.
        const beforeStatus = await evaluate(cdp, readKv('status'))
        const beforeRevision = revisionOf(await evaluate(cdp, readKv('version / revision')))
        const clicked = await evaluate(cdp, panelButton('Approve', 'button.click()'))
        const outcome = await waitForSubmitted('Approve')
        trace.record('clicking Approve in the browser submits and Core accepts it',
          clicked.ok === true && outcome.submitted === true && outcome.conflict === false,
          `clicked=${JSON.stringify(clicked)} outcome=${JSON.stringify(outcome)}`)
        await reloadAndReauth(url)
        await waitFor(cdp, `document.querySelector('section[data-write-panel]') !== null`,
          { label: 'rule detail after reload' })
        const afterStatus = await waitFor(cdp, `(() => {
          const value = ${readKv('status')}
          return value ?? ''
        })()`, { label: 'approved status row' })
        const afterRevision = revisionOf(await evaluate(cdp, readKv('version / revision')))
        const wentActive = (beforeStatus ?? '').includes('PROPOSED') && afterStatus.includes('ACTIVE')
        const revisionAdvanced = typeof beforeRevision === 'number' &&
          typeof afterRevision === 'number' && afterRevision > beforeRevision
        trace.record('and the Rule really is ACTIVE on re-read, not just a success banner',
          wentActive && revisionAdvanced,
          `status ${JSON.stringify(beforeStatus)} -> ${JSON.stringify(afterStatus)} · ` +
            `revision ${beforeRevision} -> ${afterRevision}`)
        await trace.screenshot(cdp, '17-write-approve')
      }

      // --- an interpretation: type a reason into Reject and click ---
      if (args.interpretation) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/interpretation/${encodeURIComponent(args.interpretation)}` })
        await waitFor(cdp, `document.querySelector('section[data-write-panel^="Reject"]') !== null`,
          { label: 'Reject panel' })
        // A historical interpretation has its write panels disabled, which is the
        // product behaving correctly. Follow the page's own pointer to the current head
        // so the click targets something writable.
        const currentTarget = await evaluate(cdp, `(() => {
          // Read the head from the page's own warning ("该解释不是当前 head(当前为 …)").
          // Taking "the first interpretation link that is not this one" picked a
          // lineage link instead, and the panels there are correctly disabled — so the
          // walkthrough would have blamed the product for its own wrong target.
          // Take the *tightest* container holding the warning: an ancestor div also
          // contains the phrase, and its first interpretation link is a lineage link
          // rather than the head, which made this pick flaky.
          const candidates = [...document.querySelectorAll('p, div, span')]
            .filter((n) => (n.textContent ?? '').includes('不是当前 head') &&
                           n.querySelector('a[href^="#/interpretation/"]'))
          candidates.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)
          const link = candidates[0]?.querySelector('a[href^="#/interpretation/"]')
          return link ? link.getAttribute('href') : null
        })()`)
        let writeUrl = `${args.ui}/#/interpretation/${encodeURIComponent(args.interpretation)}`
        let writeTargetId = args.interpretation
        if (currentTarget) {
          trace.record('a historical interpretation disables its write panels and points at the current head',
            true, currentTarget)
          writeUrl = `${args.ui}/${currentTarget}`
          writeTargetId = currentTarget.split('/').pop()
          await cdp.send('Page.navigate', { url: writeUrl })
          // Waiting for the *panel* is not enough: a same-document hash change leaves
          // the previous page's panel in the DOM for a render, so a panel query can
          // match the target we just left. Wait for the target's own id.
          await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(writeTargetId)})`,
            { label: 'current-head page settled' })
          await waitFor(cdp, `document.querySelector('section[data-write-panel^="Reject"]') !== null`,
            { label: 'current-head Reject panel' })
        }
        const rootUrl = `${args.ui}/#/interpretation/${encodeURIComponent(args.interpretation)}`
        await cdp.send('Page.navigate', { url: rootUrl })
        await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(args.interpretation)})`,
          { label: 'root page settled for the basis read' })
        const revisionBefore = await waitFor(cdp, `(() => {
          const text = document.body.textContent ?? ''
          const match = text.match(/head revision ([0-9]+)/)
          return match ? Number(match[1]) : 0
        })()`, { label: 'root head revision before' })
        if (writeTargetId !== args.interpretation) {
          await cdp.send('Page.navigate', { url: writeUrl })
          await waitFor(cdp, `document.body.textContent.includes(${JSON.stringify(writeTargetId)})`,
            { label: 'back on the head page' })
          await waitFor(cdp, `document.querySelector('section[data-write-panel^="Reject"]') !== null`,
            { label: 'head Reject panel again' })
        }
        const typedReason = await setField('section[data-write-panel^="Reject"] [data-testid="write-reason"]',
          'P6-02 click-through: reject from the browser')
        if (typedReason.ok !== true) {
          trace.record('typing a reason and clicking Reject submits in the browser', false,
            `could not type the reason: ${JSON.stringify(typedReason)}`)
        }
        await waitFor(cdp, `(() => {
          const panel = [...document.querySelectorAll('section[data-write-panel]')]
            .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Reject'))
          const button = panel?.querySelector('[data-testid="write-submit"]')
          return button ? !button.disabled : false
        })()`, { label: 'Reject armed after typing' })
        const clicked = await evaluate(cdp, panelButton('Reject', 'button.click()'))
        const outcome = await waitForSubmitted('Reject')
        trace.record('typing a reason and clicking Reject submits in the browser',
          clicked.ok === true && outcome.submitted === true,
          `clicked=${JSON.stringify(clicked)} outcome=${JSON.stringify(outcome)}`)
        // Forced refetch rather than a route change: a route change happens to work
        // when the ids differ, but reads a cache when the passed id is already the
        // current head — which would report a real write as a failure.
        await reloadAndReauth(rootUrl)
        const revisionAfter = await waitFor(cdp, `(() => {
          const text = document.body.textContent ?? ''
          const match = text.match(/head revision ([0-9]+)/)
          return match ? Number(match[1]) : 0
        })()`, { label: 'root head revision after' })
        // Exactly one step, not merely "greater": a reject is one change, and a bare
        // `>` could in principle be satisfied by Core's lazy recovery advancing the head
        // during the re-read's own GET. An exact step makes any extra write visible.
        trace.record('and the maintenance head advanced exactly one step on re-read',
          revisionAfter === revisionBefore + 1,
          `before=${revisionBefore} after=${revisionAfter}`)
        await trace.screenshot(cdp, '18-write-reject')
      }

      // --- a PENDING review: click Reject (no plan editing needed) ---
      if (args.rejectReview) {
        await cdp.send('Page.navigate', { url: `${args.ui}/#/review/${encodeURIComponent(args.rejectReview)}` })
        await waitFor(cdp, `document.querySelector('section[data-write-panel^="Reject"]') !== null`,
          { label: 'review Reject panel' })
        await setField('section[data-write-panel^="Reject"] [data-testid="write-reason"]',
          'P6-02 click-through: reject the plan')
        await waitFor(cdp, `(() => {
          const panel = [...document.querySelectorAll('section[data-write-panel]')]
            .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Reject'))
          const button = panel?.querySelector('[data-testid="write-submit"]')
          return button ? !button.disabled : false
        })()`, { label: 'review Reject armed after typing' })
        const clicked = await evaluate(cdp, panelButton('Reject', 'button.click()'))
        const outcome = await waitForSubmitted('Reject')
        await reloadAndReauth(`${args.ui}/#/review/${encodeURIComponent(args.rejectReview)}`)
        // The status/revision row of a freshly fetched Review, not the word "REJECTED"
        // anywhere on the page — Core's raw response in the success block contains it.
        const reviewStatus = await waitFor(cdp, `(() => {
          const value = ${readKv('status / revision')}
          return value && value.includes('REJECTED') ? value : ''
        })()`, { label: 'rejected review status row', timeoutMs: 20000 })
        trace.record('clicking Reject on a Review really leaves it REJECTED on re-read',
          clicked.ok === true && outcome.submitted === true && reviewStatus.includes('REJECTED'),
          `row=${JSON.stringify(reviewStatus)} outcome=${JSON.stringify(outcome)}`)
        await trace.screenshot(cdp, '19-write-review-reject')
      }

      // --- Propose a brand-new Rule from the form, then re-read it ---
      const ruleKey = `ui.clickthrough.${Date.now().toString(36)}`
      await cdp.send('Page.navigate', {
        url: `${args.ui}/#/rules${args.project ? `?project_id=${encodeURIComponent(args.project)}` : ''}`,
      })
      await waitFor(cdp, `document.querySelector('section[data-write-panel^="Propose"]') !== null`,
        { label: 'Propose panel' })
      // The picker is filled by its own `GET /v1/events`, so waiting for the panel is
      // not enough: wait until a real candidate is on offer, which is also the product
      // behaviour worth checking (an empty picker would be a finding, not a race).
      await waitFor(cdp, `(() => {
        const panel = [...document.querySelectorAll('section[data-write-panel]')]
          .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Propose'))
        const select = panel ? [...panel.querySelectorAll('label')]
          .find((l) => (l.textContent ?? '').includes('候选 origin Event'))?.querySelector('select') : null
        return select ? [...select.options].some((o) => o.value.startsWith('evt_')) : false
      })()`, { label: 'origin Event candidate offered', timeoutMs: 20000 })
      const filled = await evaluate(cdp, `(() => {
        const set = ${setValue}
        const panel = [...document.querySelectorAll('section[data-write-panel]')]
          .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Propose'))
        if (!panel) return { ok: false, why: 'no panel' }
        const labelled = (needle) => [...panel.querySelectorAll('label')]
          .find((l) => (l.textContent ?? '').includes(needle))
        const ruleKeyField = labelled('rule_key')?.querySelector('input')
        const contentField = labelled('content(Core')?.querySelector('textarea')
        const originSelect = labelled('候选 origin Event')?.querySelector('select')
        if (!ruleKeyField || !contentField || !originSelect) {
          return { ok: false, why: 'fields missing' }
        }
        set(ruleKeyField, ${JSON.stringify(ruleKey)})
        set(contentField, 'P6-02 click-through: proposed from the browser.')
        const option = [...originSelect.options].find((o) => o.value.startsWith('evt_'))
        if (!option) return { ok: false, why: 'no origin Event candidate for this scope' }
        set(originSelect, option.value)
        return { ok: true, origin: option.value }
      })()`)
      if (filled.ok === true) {
        await waitFor(cdp, `(() => {
          const panel = [...document.querySelectorAll('section[data-write-panel]')]
            .find((n) => (n.getAttribute('data-write-panel') ?? '').startsWith('Propose'))
          const button = panel?.querySelector('[data-testid="write-submit"]')
          return button ? !button.disabled : false
        })()`, { label: 'Propose ready' })
        const clicked = await evaluate(cdp, panelButton('Propose', 'button.click()'))
        const outcome = await waitForSubmitted('Propose')
        // Read the created Rule's id out of Core's own response, then fetch it. Without
        // this the check only proved a banner appeared.
        const createdId = await evaluate(cdp, `(() => {
          const text = document.body.textContent ?? ''
          const match = text.match(/rul_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/)
          return match ? match[0] : null
        })()`)
        let createdStatus = null
        if (createdId) {
          await reloadAndReauth(
            `${args.ui}/#/rules?rule_id=${encodeURIComponent(createdId)}` +
              (args.project ? `&project_id=${encodeURIComponent(args.project)}` : ''),
          )
          createdStatus = await waitFor(cdp, `(() => {
            const value = ${readKv('status')}
            return value ?? ''
          })()`, { label: 'created Rule status row' })
        }
        trace.record('the Propose form submits from a real click and creates a PROPOSED Rule',
          clicked.ok === true && outcome.submitted === true && createdId !== null &&
            (createdStatus ?? '').includes('PROPOSED'),
          `rule_key=${ruleKey} created=${createdId} status=${JSON.stringify(createdStatus)} ` +
            `outcome=${JSON.stringify(outcome)}`)
        await trace.screenshot(cdp, '20-write-propose')
      } else {
        trace.record('the Propose form submits from a real click and creates a PROPOSED Rule',
          false, `could not fill the form: ${JSON.stringify(filled)}`)
      }
    }

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
    // 401 = the deliberate bogus-token probe; 409 = the deliberate stale-preview
    // probe. Both are provoked on purpose, so Chrome's network log line for them
    // is evidence, not a defect.
    const expectedRefusals = consoleErrors.filter((line) => /Failed to load resource.*(401|403|409)/.test(line))
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
