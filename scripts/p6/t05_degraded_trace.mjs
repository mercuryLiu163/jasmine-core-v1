/**
 * P6-T05 degraded slice — the browser half: Memory unavailable, Core (and the P1 views) still up.
 *
 * Read `scripts/p6/run_t05_degraded.py` and `web/P6-REMAINING-TASKS.md`. The isolated real P4 Core in
 * this run is built with `--engine-state unavailable`: the wire-double engine is **stopped** and P4's
 * own probe recorded the refusal, so `GET /v1/memory/health` reports `unreachable` with
 * `core_impact: memory_paused` while `GET /v1/health` still says `ok`. That is P4's own documented
 * design ("Hindsight unavailable → long-term Memory pauses; Rules/State/Evidence/Checkpoint continue
 * normally"), and this pass checks the dashboard renders exactly that and nothing softer:
 *
 * * the Memory health panel states the status, `degraded: true`, `memory_paused` **and** the
 *   sentence that Core itself is still working — the panel must not turn "Memory is down" into
 *   "everything is down", and must not render the outage as an empty healthy set;
 * * the Memory job and trace lists still show Core's own rows (a paused engine is not a blank table);
 * * the P1 views (Overview, Task, Conversation, Rules, Evidence) still render real Core data with
 *   **no** dependency-pending panel and **no** alert: a Memory outage must not cascade;
 * * the traffic is against the real Core; the engine boundary is a labelled wire double, so
 *   `mode: boundary-fake` and no real Hindsight behaviour is claimed.
 *
 * The browser helpers are imported from `browser_trace.mjs` rather than copied, so the passes cannot
 * drift in how they click, wait or read a page.
 */

import { spawn } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  CHROME_CANDIDATES,
  Cdp,
  Trace,
  clickByText,
  evaluate,
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
      case '--tokens': args.tokens = next; index += 1; break
      case '--descriptor': args.descriptor = next; index += 1; break
      case '--out': args.out = next; index += 1; break
      case '--headful': args.headful = true; index += 1; break
      default: throw new Error(`unknown argument ${flag}`)
    }
  }
  for (const required of ['ui', 'tokens', 'descriptor', 'out']) {
    if (!args[required]) throw new Error(`--${required} is required`)
  }
  return args
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const tokens = JSON.parse(readFileSync(args.tokens, 'utf8'))
  const descriptor = JSON.parse(readFileSync(args.descriptor, 'utf8'))
  const token = tokens.human_manage ?? Object.values(tokens)[0]
  const project = descriptor.seeded.project_id
  const task = descriptor.seeded.task_id
  const rule = descriptor.seeded.rule_id
  const evidence = descriptor.seeded.evidence_id

  const chromePath = CHROME_CANDIDATES.find((candidate) => candidate && existsSync(candidate))
  if (!chromePath) throw new Error('no Chrome/Chromium found; set CHROME_PATH')
  const port = 9333 + Math.floor(Math.random() * 500)
  const profile = await mkdtemp(join(tmpdir(), 'p6-t05-chrome-'))
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
  const consoleErrors = []
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
    cdp.onEvent((message) => {
      if (message.method === 'Network.requestWillBeSent') {
        const url = new URL(message.params.request.url)
        requests.push({ requestId: message.params.requestId,
                        method: message.params.request.method, path: url.pathname })
      }
      if (message.method === 'Network.responseReceived') {
        responses.set(message.params.requestId, message.params.response.status)
      }
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
        consoleErrors.push(message.params.entry.text)
      }
    })

    const pageText = () => evaluate(cdp, `document.body.textContent ?? ''`)
    const pending = () => evaluate(cdp,
      `document.querySelector('[data-testid="dependency-pending"]') !== null`)
    const alerts = () => evaluate(cdp,
      `[...document.querySelectorAll('[role=alert]')].map((n) => (n.textContent ?? '').slice(0, 120))`)
    const rowsIn = (testid) => evaluate(cdp,
      `document.querySelectorAll('[data-testid=${JSON.stringify(testid)}] tbody tr').length`)
    // Read one `dl.kv` row by its label, so an assertion reads the panel's own row rather than a
    // substring from anywhere on the page.
    const kvRow = (label) => evaluate(cdp, `(() => {
      for (const row of document.querySelectorAll('dl.kv > div')) {
        const dt = row.querySelector('dt')
        const dd = row.querySelector('dd')
        if (dt && dd && (dt.textContent ?? '').trim() === ${JSON.stringify(label)})
          return (dd.textContent ?? '').trim()
      }
      return null
    })()`)
    const goto = async (hash, label, readyExpression) => {
      await evaluate(cdp, `(() => {
        const next = '#/' + ${JSON.stringify(hash)}
        if (location.hash !== next) location.hash = next
        dispatchEvent(new HashChangeEvent('hashchange'))
        return location.hash
      })()`)
      await waitFor(cdp, `location.hash === ${JSON.stringify('#/' + hash)}`, { label: `${label} hash` })
      await waitFor(cdp, readyExpression, { label })
    }
    const signIn = async (value) => {
      await waitFor(cdp, `document.querySelector('input[type=password]') !== null`,
        { label: 'sign-in form' })
      await typeInto(cdp, 'input[type=password]', value)
      await clickByText(cdp, 'button', '使用此 token')
      await waitFor(cdp, `document.querySelector('input[type=password]') === null`,
        { label: 'signed in' })
    }

    await cdp.send('Page.navigate', { url: `${args.ui}/#/overview` })
    await signIn(token)

    // --- 1. the Memory health panel states the outage, and that Core keeps working --------------
    await goto('memory/health', 'memory health',
      `document.body.textContent.includes('Memory 健康')`)
    const health = {
      status: await kvRow('status'),
      degraded: await kvRow('degraded'),
      coreImpact: await kvRow('core_impact'),
      configured: await kvRow('configured'),
      reachable: await kvRow('reachable'),
      lastError: await kvRow('last_error_code'),
      pausedSentence: (await pageText()).includes('Memory 已暂停'),
      nonAuthoritative: (await pageText()).includes('NON_AUTHORITATIVE'),
      pending: await pending(),
      alerts: await alerts(),
    }
    trace.record('Memory health says the engine is unreachable and Core is still working',
      health.status === 'unreachable' && health.degraded === 'true' &&
        (health.coreImpact ?? '').startsWith('memory_paused') && health.configured === 'true' &&
        health.reachable === 'false' && health.lastError === 'engine_unreachable' &&
        health.pausedSentence && health.nonAuthoritative &&
        health.pending === false && health.alerts.length === 0,
      JSON.stringify(health))
    await trace.screenshot(cdp, 't05-01-memory-health')

    // --- 2. the job and trace lists still show Core's own rows, not blank tables ----------------
    await goto('memory/jobs', 'memory jobs',
      `document.querySelector('[data-testid="memory-jobs"]') !== null`)
    const jobs = {
      rows: await rowsIn('memory-jobs'),
      statuses: (await pageText()).includes('DONE') || (await pageText()).includes('PENDING'),
      pending: await pending(),
      alerts: await alerts(),
    }
    trace.record('Memory jobs still renders Core rows while the engine is down',
      jobs.rows > 0 && jobs.statuses && jobs.pending === false && jobs.alerts.length === 0,
      JSON.stringify(jobs))
    await trace.screenshot(cdp, 't05-02-memory-jobs')

    await goto('memory/traces', 'memory traces',
      `document.querySelector('[data-testid="memory-traces"]') !== null`)
    const traces = {
      rows: await rowsIn('memory-traces'),
      failed: (await pageText()).includes('FAILED') || (await pageText()).includes('failed'),
      pending: await pending(),
      alerts: await alerts(),
    }
    trace.record('the degraded attempt is recorded as history, not hidden',
      traces.rows > 0 && traces.failed && traces.pending === false && traces.alerts.length === 0,
      JSON.stringify(traces))
    await trace.screenshot(cdp, 't05-03-memory-traces')

    // --- 3. the P1 views are untouched: no dependency panel, no alert, real data ----------------
    const p1 = {}
    for (const [name, hash, marker] of [
      ['overview', 'overview', 'Core 健康与 Schema'],
      ['task', `task/${task}`, 'Task 状态'],
      ['conversation', `conversation?project_id=${project}&task_id=${task}`, 'Sessions'],
      ['rules', `rules?project_id=${project}&task_id=${task}&rule_id=${rule}`, 'ACTIVE Rule'],
      ['evidence', `evidence?evidence_id=${evidence}`, 'Evidence'],
    ]) {
      await goto(hash, `P1 ${name}`, `document.body.textContent.includes(${JSON.stringify(marker)})`)
      p1[name] = {
        rows: await evaluate(cdp, `document.querySelectorAll('dl.kv > div').length`),
        pending: await pending(),
        alerts: await alerts(),
      }
    }
    trace.record('the P1 views are unaffected by the Memory outage: no dependency panel, no alert',
      Object.values(p1).every((view) => view.pending === false && view.alerts.length === 0 &&
        view.rows > 0),
      JSON.stringify(p1))
    await trace.screenshot(cdp, 't05-04-p1-overview')

    // --- 4. the traffic really went to the Core, whose own health is ok -------------------------
    const requested = (path) => requests.filter((entry) => entry.path === path)
    const healthCalls = requested('/v1/health')
    const memoryCalls = requests.filter((entry) => entry.path.startsWith('/v1/memory/'))
    trace.record('every page read the real Core, and Core health answered 200 while Memory was down',
      healthCalls.length > 0 && memoryCalls.length > 0 &&
        healthCalls.every((entry) => responses.get(entry.requestId) === 200) &&
        memoryCalls.every((entry) => responses.get(entry.requestId) === 200),
      JSON.stringify({ health: healthCalls.length, memory: memoryCalls.length,
                       healthStatuses: healthCalls.map((e) => responses.get(e.requestId)) }))

    // --- 5. no unexpected console errors (a 403/404 the UI provokes is not one) ----------------
    const expected = consoleErrors.filter((line) => /Failed to load resource/.test(line))
    const unexpected = consoleErrors.filter((line) => !expected.includes(line))
    trace.record('no unexpected console errors during the degraded walkthrough',
      unexpected.length === 0, `${unexpected.length} unexpected`)
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
    await writeFile(join(args.out, 'chrome-stderr.log'), chromeLog.join('')).catch(() => {})
    await writeFile(join(args.out, 'click-trace.jsonl'),
      trace.steps.map((step) => JSON.stringify(step)).join('\n') + '\n')
    await writeFile(join(args.out, 'browser-requests.jsonl'),
      requests.map((entry) => JSON.stringify({
        method: entry.method, path: entry.path, status: responses.get(entry.requestId) ?? null,
      })).join('\n') + '\n')
    await writeFile(join(args.out, 'browser-trace.md'),
      ['# P6-T05 degraded slice — browser pass', '',
        ...trace.steps.map((step) =>
          `- ${step.ok === null ? 'BLKD' : step.ok ? 'PASS' : 'FAIL'} ${step.step}` +
          (step.detail ? ` — \`${step.detail}\`` : '')),
        ''].join('\n'))
    process.exitCode = trace.failed ? 1 : 0
  }
}

main().catch((error) => {
  process.stderr.write(`t05 degraded pass failed: ${error.stack ?? error.message}\n`)
  process.exitCode = 1
})
