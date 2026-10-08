/**
 * P6-T05 degraded slice — the browser half: Memory unavailable (and then recovered), P1 untouched.
 *
 * Read `scripts/p6/run_t05_degraded.py`. The isolated real P4 Core is built with
 * `--engine-state unavailable`: the wire-double engine is **stopped** and P4's own probe recorded the
 * refusal, so `/v1/memory/health` reports `unreachable` with `core_impact: memory_paused` while
 * `/v1/health` still says `ok`. The runner then restarts the engine on the same address and this pass
 * re-reads the pages to show the recovery.
 *
 * Assertions, and why they are shaped this way (the operator's second review found the first version's
 * checks could pass on historical traffic or on a panel title alone):
 *
 * * every view's data assertion requires a **request issued after that view was navigated to** to
 *   have been observed with a 200 — a 200 captured earlier in the pass cannot satisfy it;
 * * every P1 view must additionally show **the object the page is about** (the project id, the task
 *   id, an event's `data-event-id`, the rule id, the evidence id), so "the panel rendered" is not
 *   enough;
 * * the Memory health panel must state the pause and that Core keeps working, and after recovery must
 *   state the recovery — never an empty healthy set for an outage, and never a success word for the
 *   `degraded` reflect payload;
 * * the P1 views must show no dependency-pending panel and no alert that names a dependency failure
 *   (the Rules page also renders the audited write forms, whose empty-field hints are `role=alert`;
 *   every alert is recorded verbatim so nothing is filtered away silently).
 */

import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { existsSync, readFileSync } from 'node:fs'
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
  const args = { width: 1440, height: 1000, headful: false, controlState: 'not-available' }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    const next = argv[index + 1]
    switch (flag) {
      case '--ui': args.ui = next; index += 1; break
      case '--tokens': args.tokens = next; index += 1; break
      case '--descriptor': args.descriptor = next; index += 1; break
      case '--expect': args.expect = next; index += 1; break
      case '--control-state': args.controlState = next; index += 1; break
      case '--out': args.out = next; index += 1; break
      case '--headful': args.headful = true; index += 1; break
      default: throw new Error(`unknown argument ${flag}`)
    }
  }
  for (const required of ['ui', 'tokens', 'descriptor', 'expect', 'out']) {
    if (!args[required]) throw new Error(`--${required} is required`)
  }
  return args
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const tokens = JSON.parse(readFileSync(args.tokens, 'utf8'))
  const descriptor = JSON.parse(readFileSync(args.descriptor, 'utf8'))
  const expect = JSON.parse(readFileSync(args.expect, 'utf8'))
  const token = tokens.human_manage ?? Object.values(tokens)[0]
  const project = descriptor.seeded.project_id
  const task = descriptor.seeded.task_id
  const rule = descriptor.seeded.rule_id
  const evidence = descriptor.seeded.evidence_id

  // The screenshots directory is written into by `Trace.screenshot`; a pass that fails before
  // creating it would lose the very screenshot that shows the failure.
  await mkdir(join(args.out, 'screenshots'), { recursive: true })
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
    // Requests issued *after* the mark, so a view cannot be credited with an earlier view's traffic.
    const freshStatuses = (prefix, mark) => requests.slice(mark)
      .filter((entry) => entry.path.startsWith(prefix))
      .map((entry) => responses.get(entry.requestId) ?? null)
    const waitForFresh = async (prefix, mark, label) => {
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const statuses = freshStatuses(prefix, mark)
        if (statuses.some((status) => status === 200)) return statuses
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
      throw new Error(`no fresh 200 observed for ${prefix} after navigating to ${label}`)
    }
    const goto = async (hash, label, readyExpression) => {
      const mark = requests.length
      await evaluate(cdp, `(() => {
        const next = '#/' + ${JSON.stringify(hash)}
        if (location.hash !== next) location.hash = next
        dispatchEvent(new HashChangeEvent('hashchange'))
        return location.hash
      })()`)
      await waitFor(cdp, `location.hash === ${JSON.stringify('#/' + hash)}`, { label: `${label} hash` })
      await waitFor(cdp, readyExpression, { label })
      return mark
    }
    const signIn = async (value) => {
      await waitFor(cdp, `document.querySelector('input[type=password]') !== null`,
        { label: 'sign-in form' })
      await typeInto(cdp, 'input[type=password]', value)
      await clickByText(cdp, 'button', '使用此 token')
      await waitFor(cdp, `document.querySelector('input[type=password]') === null`,
        { label: 'signed in' })
    }
    const dependencyAlerts = (list) => list.filter((text) =>
      /未接入|dependency|proxy|unreachable|not_found|forbidden/i.test(text))

    await cdp.send('Page.navigate', { url: `${args.ui}/#/overview` })
    await signIn(token)

    // === OUTAGE PHASE =========================================================================
    const markHealth = await goto('memory/health', 'memory health',
      `[...document.querySelectorAll('dl.kv dt')].some((n) => (n.textContent ?? '').trim() === 'status')`)
    const healthFresh = await waitForFresh('/v1/memory/health', markHealth, 'memory health')
    const health = {
      fresh: healthFresh,
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
        health.pending === false && health.alerts.length === 0 &&
        health.fresh.includes(200),
      JSON.stringify(health))
    await trace.screenshot(cdp, 't05-01-memory-health-outage')

    const markJobs = await goto('memory/jobs', 'memory jobs',
      `document.querySelectorAll('[data-testid="memory-jobs"] tbody tr').length > 0`)
    const jobsFresh = await waitForFresh('/v1/memory/jobs', markJobs, 'memory jobs')
    const jobs = {
      fresh: jobsFresh,
      rows: await rowsIn('memory-jobs'),
      statuses: (await pageText()).includes('DONE') || (await pageText()).includes('PENDING'),
      pending: await pending(),
      alerts: await alerts(),
    }
    trace.record('Memory jobs still renders Core rows while the engine is down',
      jobs.rows > 0 && jobs.statuses && jobs.fresh.includes(200) &&
        jobs.pending === false && jobs.alerts.length === 0,
      JSON.stringify(jobs))
    await trace.screenshot(cdp, 't05-02-memory-jobs')

    const markTraces = await goto('memory/traces', 'memory traces',
      `document.querySelectorAll('[data-testid="memory-traces"] tbody tr').length > 0`)
    const tracesFresh = await waitForFresh('/v1/memory/traces', markTraces, 'memory traces')
    const traces = {
      fresh: tracesFresh,
      rows: await rowsIn('memory-traces'),
      firstRow: await evaluate(cdp,
        `(document.querySelector('[data-testid="memory-traces"] tbody tr')?.textContent ?? '').trim()`),
      pending: await pending(),
      alerts: await alerts(),
    }
    trace.record('the degraded attempt is recorded as history, not hidden',
      traces.rows > 0 && /(failed|degraded|error)/i.test(traces.firstRow) &&
        !/(success|ok\b|done)/i.test(traces.firstRow) && traces.fresh.includes(200) &&
        traces.pending === false && traces.alerts.length === 0,
      JSON.stringify(traces))

    // Each P1 view: a **fresh** 200 behind it, the object it is about on the page, no dependency
    // panel, no dependency-failure alert.
    const p1 = {}
    for (const [name, hash, marker, prefix, shownBy] of [
      ['overview', 'overview', 'Core 健康与 Schema', '/v1/projects', `text:${expect.project_id}`],
      ['task', `task/${task}`, 'Task 状态', `/v1/tasks/${task}`, `text:${expect.task_id}`],
      ['conversation', `conversation?project_id=${project}&task_id=${task}`, 'Sessions',
       '/v1/events', expect.event_id ? `selector:[data-event-id="${expect.event_id}"]`
                                     : `text:${expect.task_id}`],
      ['rules', `rules?project_id=${project}&task_id=${task}&rule_id=${rule}`, 'ACTIVE Rule',
       '/v1/rules', `text:${expect.rule_id}`],
      ['evidence', `evidence?evidence_id=${evidence}`, 'Evidence', '/v1/evidence',
       `text:${expect.evidence_id}`],
    ]) {
      const mark = await goto(hash, `P1 ${name}`,
        `document.body.textContent.includes(${JSON.stringify(marker)})`)
      const fresh = await waitForFresh(prefix, mark, name)
      const shown = shownBy.startsWith('selector:')
        ? await evaluate(cdp,
            `document.querySelector(${JSON.stringify(shownBy.slice('selector:'.length))}) !== null`)
        : (await pageText()).includes(shownBy.slice('text:'.length))
      const list = await alerts()
      p1[name] = { fresh, shown, pending: await pending(), alerts: list,
                   dependencyAlerts: dependencyAlerts(list) }
    }
    trace.record('the P1 views are unaffected by the Memory outage: fresh data, no dependency failure',
      Object.values(p1).every((view) => view.shown === true && view.fresh.includes(200) &&
        view.fresh.every((status) => status === null || status === 200) &&
        view.pending === false && view.dependencyAlerts.length === 0),
      JSON.stringify(p1))
    await trace.screenshot(cdp, 't05-03-p1-during-outage')

    const healthCalls = requests.filter((entry) => entry.path === '/v1/health')
    trace.record('every page read the real Core, and Core health answered 200 while Memory was down',
      healthCalls.length > 0 && healthCalls.every((e) => responses.get(e.requestId) === 200),
      JSON.stringify({ health: healthCalls.length,
                       healthStatuses: healthCalls.map((e) => responses.get(e.requestId)) }))

    // === RECOVERY PHASE (the runner restarted the engine on the same address) ==================
    if (args.controlState === 'restored') {
      const markRecovered = await goto('memory/health', 'memory health after recovery',
        `[...document.querySelectorAll('dl.kv dt')].some((n) => (n.textContent ?? '').trim() === 'status')`)
      const recoveredFresh = await waitForFresh('/v1/memory/health', markRecovered,
                                                'memory health after recovery')
      const recovered = {
        fresh: recoveredFresh,
        status: await kvRow('status'),
        degraded: await kvRow('degraded'),
        coreImpact: await kvRow('core_impact'),
        reachable: await kvRow('reachable'),
        lastError: await kvRow('last_error_code'),
        pausedSentence: (await pageText()).includes('Memory 已暂停'),
        pending: await pending(),
        alerts: await alerts(),
      }
      trace.record('after the engine returns on the same address, the panel states the recovery',
        (recovered.status === 'idle' || recovered.status === 'ok') &&
          recovered.degraded === 'false' && (recovered.coreImpact ?? '').startsWith('none') &&
          recovered.reachable === 'true' && recovered.lastError === '—' &&
          recovered.pausedSentence === false && recovered.fresh.includes(200) &&
          recovered.pending === false && recovered.alerts.length === 0,
        JSON.stringify(recovered))
      await trace.screenshot(cdp, 't05-04-memory-health-recovered')

      const markTaskAfter = await goto(`task/${task}`, 'P1 task after recovery',
        `document.body.textContent.includes('Task 状态')`)
      const taskFresh = await waitForFresh(`/v1/tasks/${task}`, markTaskAfter, 'task after recovery')
      const taskShown = (await pageText()).includes(expect.task_id)
      const taskAlerts = await alerts()
      trace.record('a P1 view re-reads the same Core after the Memory recovery',
        taskShown && taskFresh.includes(200) &&
          taskFresh.every((status) => status === null || status === 200) &&
          (await pending()) === false && dependencyAlerts(taskAlerts).length === 0,
        JSON.stringify({ taskFresh, taskShown, alerts: taskAlerts }))
      await trace.screenshot(cdp, 't05-05-p1-after-recovery')

      const markTracesAfter = await goto('memory/traces', 'memory traces after recovery',
        `document.querySelectorAll('[data-testid="memory-traces"] tbody tr').length > 0`)
      const tracesAfterFresh = await waitForFresh('/v1/memory/traces', markTracesAfter,
                                                 'memory traces after recovery')
      const tracesAfter = {
        fresh: tracesAfterFresh,
        rows: await rowsIn('memory-traces'),
        outageStillRecorded: (await pageText()).includes('failed') ||
          (await pageText()).includes('degraded'),
      }
      trace.record('the outage attempt is still history after recovery, and the list re-read',
        tracesAfter.rows >= 1 && tracesAfter.fresh.includes(200) &&
          tracesAfter.outageStillRecorded, JSON.stringify(tracesAfter))
    }

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
      ['# P6-T05 degraded slice — browser pass (outage and recovery)', '',
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
