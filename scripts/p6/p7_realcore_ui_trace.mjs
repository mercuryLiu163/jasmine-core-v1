/**
 * P6-03c — the Validation Inspector driven in a real browser, through the real dashboard proxy,
 * against a real Core running the P7-01 read slice over the operator's real records (read-only)
 * and over a clearly-labelled synthetic fixture root for the negatives the real records cannot
 * provide.
 *
 * Read `scripts/p6/p7_realcore_ui.py`, `scripts/p6/p7_realcore_env.py` and the frozen contract
 * `web/contracts/p7-validation-read.v1.json` first. The browser helpers are **imported** from
 * `browser_trace.mjs`, not copied, so no two harnesses can drift in how they click, wait or read
 * a response body.
 *
 * Three honesty properties this file carries into the evidence:
 *
 * 1. **The pass is a browser pass.** Every assertion below is made against the DOM of a real
 *    Chrome page, or against a `fetch()` the page itself issues through the same proxy with the
 *    same session credential. The one place the raw token crosses the CDP `Runtime.evaluate`
 *    channel is `browserFetch()`; it is never written to any bundle file and the temporary global
 *    holding it is deleted immediately.
 * 2. **The switch proxy is the harness's, not the Core's.** `p7_realcore_ui.py` runs one routing
 *    proxy in front of four real Core processes (real root, fixture root, unset root, acl mode) so
 *    one browser session can see all of them. The dashboard's own allowlist still decides what the
 *    browser may reach; the switch proxy only chooses which real Core answers.
 * 3. **A failing assertion is recorded as FAIL.** The withheld-original check is written against
 *    the UI as the task states it; if this build's UI cannot reach that state, the record says so
 *    with the DOM it observed rather than being softened.
 */

import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

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
      case '--proxy': args.proxy = next; index += 1; break
      case '--tokens': args.tokens = next; index += 1; break
      case '--descriptor': args.descriptor = next; index += 1; break
      case '--expectations': args.expectations = next; index += 1; break
      case '--out': args.out = next; index += 1; break
      case '--headful': args.headful = true; break
      default: throw new Error(`unknown argument ${flag}`)
    }
  }
  for (const required of ['ui', 'proxy', 'tokens', 'descriptor', 'expectations', 'out']) {
    if (!args[required]) throw new Error(`--${required} is required`)
  }
  return args
}

function headerValue(headers, name) {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === wanted) return value
  }
  return undefined
}

/** Select which real Core the harness switch proxy forwards to. Never the Core API. */
async function switchCore(proxy, core) {
  const response = await fetch(`${proxy}/__p6/select?core=${encodeURIComponent(core)}`)
  if (!response.ok) throw new Error(`switch to ${core} failed: HTTP ${response.status}`)
  return response.json()
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const tokens = JSON.parse(await readFile(args.tokens, 'utf8'))
  const descriptor = JSON.parse(await readFile(args.descriptor, 'utf8'))
  // The expected real-record population, written by `p7_realcore_ui.py` from the tree on disk and
  // named on this trace's command line. Every count and id below is read from it — this file
  // asserts the *shape* ("exactly these ids, each once", "the walk covers the union") against the
  // data rather than against a hard-coded size.
  const expected = JSON.parse(await readFile(args.expectations, 'utf8'))
  const expectedIds = Array.isArray(expected.ids) ? expected.ids : []
  if (expectedIds.length === 0) {
    throw new Error(`expectations ${args.expectations} carries no ids; cannot assert a population`)
  }
  const expectedIdsSorted = expectedIds.slice().sort()
  const expectedBlockedIds = (expected.blocked_run_ids ?? []).slice().sort()
  const expectedPassIds = (expected.pass_run_ids ?? []).slice().sort()
  // The real run used for the single-run manifest and ACL probes: the last id in sorted order, so
  // the probe names a record this tree actually holds rather than a fixed id.
  const probeRunId = expectedIdsSorted[expectedIdsSorted.length - 1]
  const fixture = descriptor.fixture
  const expectations = fixture.expectations
  const bundleRun = fixture.runs.bundle
  const failRun = fixture.runs.synthetic_fail

  const chromePath = CHROME_CANDIDATES.find((candidate) => candidate && existsSync(candidate))
  if (!chromePath) throw new Error('no Chrome/Chromium found; set CHROME_PATH')
  const port = 9333 + Math.floor(Math.random() * 500)
  const profile = await mkdtemp(join(tmpdir(), 'p6-03c-chrome-'))
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
  let cdpVersion = null

  try {
    cdpVersion = await waitForCdp(port)
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
        requests.push({
          at: new Date().toISOString(),
          requestId: message.params.requestId,
          method: message.params.request.method,
          path: url.pathname,
          query: url.search,
          // The Authorization header value is never recorded — only whether it was present.
          authPresent: headerValue(message.params.request.headers, 'authorization')
            ? 'present(redacted)'
            : 'absent',
          xRequestId: headerValue(message.params.request.headers, 'x-request-id') ?? null,
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

    /* ------------------------------------------------------------------ *
     * Browser helpers
     * ------------------------------------------------------------------ */

    const goto = async (hash, label, expression, timeoutMs = 15000) => {
      await evaluate(cdp, `(() => {
        const next = '#/' + ${JSON.stringify(hash)}
        if (location.hash !== next) location.hash = next
        dispatchEvent(new HashChangeEvent('hashchange'))
        return location.hash
      })()`)
      await waitFor(cdp, `location.hash === ${JSON.stringify('#/' + hash)}`, { label: `${label} hash` })
      await waitFor(cdp, expression, { label, timeoutMs })
    }
    const appears = async (expression, timeoutMs = 8000) => {
      try {
        await waitFor(cdp, expression, { label: expression, timeoutMs })
        return true
      } catch {
        return false
      }
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
    const changeToken = async (token, label) => {
      await signOut().catch(() => {})
      await signIn(token, label)
    }

    /**
     * A fetch the *page* issues, same-origin through the dashboard proxy, with the session's own
     * bearer. The token lives in a global for the duration of one call and is deleted after; it is
     * never written to a file.
     */
    const browserFetch = async (path, token) => {
      await evaluate(cdp, `window.__p6TraceAuth = ${JSON.stringify(token)}`)
      try {
        return await evaluate(cdp, `(async () => {
          const response = await fetch(${JSON.stringify(path)}, {
            headers: { authorization: 'Bearer ' + window.__p6TraceAuth },
            cache: 'no-store',
          })
          let body = null
          try { body = await response.json() } catch (error) { body = { __unparsed: true } }
          return { status: response.status, body }
        })()`)
      } finally {
        await evaluate(cdp, `delete window.__p6TraceAuth`).catch(() => {})
      }
    }

    const listState = () => evaluate(cdp, `(() => {
      const table = document.querySelector('[data-testid=validation-runs]')
      const summary = document.querySelector('[data-testid=validation-summary]')
      if (table === null) {
        return {
          present: false,
          summary: summary ? summary.textContent : null,
          rows: [],
          unusable: 0,
          reasons: [],
          emptyReason: document.querySelector('[data-testid=empty-reason]')?.textContent ?? null,
        }
      }
      const rows = [...table.querySelectorAll('tbody tr[data-testid=run-row]')].map((tr) => {
        const cells = tr.querySelectorAll('td')
        return {
          runId: (cells[0].textContent ?? '').trim(),
          href: cells[0].querySelector('a')?.getAttribute('href') ?? null,
          verdict: cells[3].querySelector('[data-status]')?.getAttribute('data-status') ?? null,
          recordStatus: cells[10].querySelector('[data-status]')?.getAttribute('data-status') ?? null,
          counts: (cells[4].textContent ?? '').trim(),
        }
      })
      const unusable = [...table.querySelectorAll('tbody tr[data-testid=run-row-unusable]')]
        .map((tr) => ({ runId: (tr.querySelector('td')?.textContent ?? '').trim() }))
      return {
        present: true,
        rows,
        unusable: unusable.length,
        unusableRunIds: unusable.map((row) => row.runId),
        reasons: [...document.querySelectorAll('[data-testid=run-row-unusable-reason]')]
          .map((tr) => (tr.textContent ?? '').split(String.fromCharCode(10)).join(' ').trim()),
        summary: summary ? (summary.textContent ?? '').split(String.fromCharCode(10)).join(' ') : null,
        emptyReason: document.querySelector('[data-testid=empty-reason]')?.textContent ?? null,
      }
    })()`)

    const kvValue = (label) => evaluate(cdp, `(() => {
      for (const list of document.querySelectorAll('dl.kv')) {
        for (const row of list.querySelectorAll(':scope > div')) {
          if (((row.querySelector('dt')?.textContent) ?? '').trim() === ${JSON.stringify(label)}) {
            const value = row.querySelector('dd')
            return value ? (value.textContent ?? '').trim() : null
          }
        }
      }
      return null
    })()`)

    const detailState = () => evaluate(cdp, `(() => {
      const cases = [...document.querySelectorAll('[data-testid=validation-case]')].map((card) => {
        const chain = {}
        for (const tr of card.querySelectorAll('tr[data-testid^=chain-]')) {
          const name = tr.getAttribute('data-testid').slice('chain-'.length)
          const cells = tr.querySelectorAll('td')
          chain[name] = {
            present: tr.getAttribute('data-present') === 'true',
            source: (cells[2].textContent ?? '').trim(),
            missingReason: (cells[3].textContent ?? '').trim(),
          }
        }
        const contextRow = card.querySelector('tr[data-testid=section-context]')
        return {
          chain,
          contextKind: contextRow
            ? (contextRow.querySelectorAll('td')[2].querySelector('code')?.textContent ?? null)
            : null,
          contextPackText: card.querySelector('[data-testid=context-pack-present]')?.textContent ?? null,
        }
      })
      // The run's verdict, from the hook the run-detail row carries. Selecting by the human
      // label alone took the LAST match, and every case card renders the same label, so a run
      // whose run-level verdict differs from its last case recorded the case's verdict as the
      // run's. The label is still accepted as a fallback, but only when the hook is absent.
      let verdict = null
      const hooked = document.querySelector('[data-testid="run-verdict"] [data-status]')
      if (hooked) {
        verdict = hooked.getAttribute('data-status') ?? null
      } else {
        for (const list of document.querySelectorAll('dl.kv')) {
          for (const row of list.querySelectorAll(':scope > div')) {
            if (((row.querySelector('dt')?.textContent) ?? '').trim() === 'verdict(按记录)') {
              verdict = row.querySelector('[data-status]')?.getAttribute('data-status') ?? null
              break
            }
          }
          if (verdict !== null) break
        }
      }
      return {
        verdict,
        cases,
        synthetic: document.querySelector('[data-testid=synthetic]') !== null,
        bodyText: (document.body.textContent ?? '').split(String.fromCharCode(10)).join(' '),
      }
    })()`)

    const stateOf = (selector) => evaluate(cdp, `(() => {
      const node = document.querySelector(${JSON.stringify(selector)})
      if (node === null) return null
      return (node.textContent ?? '').split(String.fromCharCode(10)).join(' ').trim()
    })()`)

    const observed = { listPages: [], details: {}, artifacts: {}, fixture: {}, acl: {}, misconfig: {} }

    /* ------------------------------------------------------------------ *
     * PASS A — the operator's real records, root = the operator's tree (read-only)
     * ------------------------------------------------------------------ */

    await switchCore(args.proxy, 'real')
    await signIn(tokens.read, 'validation:read')
    trace.record('A/sign-in: the dashboard signs in against the real P7-01 Core with a key the Core minted',
      true)

    await goto('validation/runs', 'validation run list',
      `document.querySelector('[data-testid=validation-runs]') !== null`)
    await trace.screenshot(cdp, 'passA-01-list')
    const list = await listState()
    observed.list = list
    const uniqueIds = [...new Set(list.rows.map((row) => row.runId))]
    const uniqueIdsSorted = uniqueIds.slice().sort()
    trace.record(`A/list renders exactly the ${expectedIds.length} real run ids with no repeats`,
      list.present === true && list.rows.length === expectedIds.length
        && uniqueIds.length === expectedIds.length
        && uniqueIdsSorted.join(',') === expectedIdsSorted.join(',')
        && list.unusable === 0,
      `${list.rows.length} usable rows, ${list.unusable} unusable, summary=${list.summary}`)

    // "walking every page with a small limit". The Inspector's page size is fixed at RUN_LIMIT=20,
    // so the small-limit walk is the page's own fetch through the same proxy with the same
    // credential — recorded here rather than claimed. The walk steps by the page size until it has
    // covered the expected population, so it is not a fixed list of offsets.
    const pageSize = 3
    const expectedPages = Math.ceil(expectedIds.length / pageSize)
    const pageUnion = []
    for (let offset = 0; offset < expectedIds.length; offset += pageSize) {
      const page = await browserFetch(`/v1/validation/runs?limit=${pageSize}&offset=${offset}`, tokens.read)
      observed.listPages.push({
        offset, status: page.status,
        count: page.body?.count ?? null, total: page.body?.total ?? null,
        hasMore: page.body?.has_more ?? null,
        runIds: (page.body?.runs ?? []).map((row) => row.run_id),
        verdicts: (page.body?.runs ?? []).map((row) => [row.run_id, row.verdict]),
      })
      for (const row of page.body?.runs ?? []) pageUnion.push(row.run_id)
    }
    const union = [...new Set(pageUnion)]
    trace.record(`A/paging: limit=3 walked every page in the browser and the union is exactly the ${expectedIds.length} with no repeats`,
      observed.listPages.every((page) => page.status === 200)
        && observed.listPages.length === expectedPages
        && pageUnion.length === expectedIds.length && union.length === expectedIds.length
        && union.slice().sort().join(',') === expectedIdsSorted.join(','),
      JSON.stringify(observed.listPages.map((page) => ({
        offset: page.offset, count: page.count, total: page.total, has_more: page.hasMore,
      }))))

    // Every expected detail view: chain links, context forms and per-run agent presence.
    const contextKinds = new Set()
    const contextReasons = new Set()
    const agentByRun = {}
    let chainRowProblems = []
    let contextPackTrue = 0
    let extractionBad = 0
    let resolutionBad = 0
    const runOrder = list.rows.map((row) => row.runId)
    for (const runId of runOrder) {
      await goto(`validation/runs/${runId}`, `detail ${runId}`,
        `document.querySelector('[data-testid=case-chain]') !== null`)
      const state = await detailState()
      observed.details[runId] = state
      agentByRun[runId] = { verdict: state.verdict, cases: [] }
      if (runId === probeRunId) {
        await trace.screenshot(cdp, `passA-02-detail-${probeRunId}`)
      }
      for (const item of state.cases) {
        const names = Object.keys(item.chain)
        if (names.length !== 8
          || !['raw', 'extraction', 'resolution', 'context', 'agent', 'tool', 'evidence', 'state']
            .every((name) => names.includes(name))) {
          chainRowProblems.push([runId, names.join(',')])
        }
        const extraction = item.chain.extraction
        const resolution = item.chain.resolution
        const context = item.chain.context
        if (extraction?.present !== false
          || extraction?.missingReason !== 'the recorder records no Extraction section') {
          extractionBad += 1
        }
        if (resolution?.present !== false
          || resolution?.missingReason !== 'the recorder records no Resolution section') {
          resolutionBad += 1
        }
        if (context?.present !== false) chainRowProblems.push([runId, 'context present'])
        if (item.contextKind !== null) contextKinds.add(item.contextKind)
        if (context?.missingReason) contextReasons.add(context.missingReason)
        if ((item.contextPackText ?? '').includes('context_pack_present = true')) contextPackTrue += 1
        agentByRun[runId].cases.push(item.chain.agent?.present === true)
      }
    }
    trace.record('A/chain: every case shows all eight T06 links with extraction/resolution present=false and verbatim reasons',
      chainRowProblems.length === 0 && extractionBad === 0 && resolutionBad === 0,
      `problems=${JSON.stringify(chainRowProblems)} extractionBad=${extractionBad} resolutionBad=${resolutionBad}`)
    trace.record(`A/chain: the Context link is absent in all ${expectedIds.length} runs`,
      Object.values(observed.details).every((state) =>
        state.cases.every((item) => item.chain.context?.present === false)),
      `context_pack_present=true seen ${contextPackTrue} times`)
    trace.record(`A/context: the three recorded forms (sentinel, reason-only, recorded-but-no-pack) all appear across the ${expectedIds.length}`,
      contextKinds.has('sentinel') && contextKinds.has('reason_only') && contextKinds.has('value')
        && contextPackTrue === 0,
      `present_kind=${JSON.stringify([...contextKinds])} pack_true=${contextPackTrue}`)
    trace.record('A/context: every missing Context reason is one of the three frozen strings',
      [...contextReasons].every((reason) => [
        'recorded as N/A (P0 not implemented)',
        'the case records a reason, not a context pack',
        'the section records run details, not a context pack',
      ].includes(reason)),
      JSON.stringify([...contextReasons]))
    const blockedRuns = Object.entries(agentByRun).filter(([, value]) => value.verdict === 'BLOCKED')
    const passRuns = Object.entries(agentByRun).filter(([, value]) => value.verdict === 'PASS')
    const blockedIdsSeen = blockedRuns.map(([id]) => id).sort()
    const passIdsSeen = passRuns.map(([id]) => id).sort()
    trace.record(`A/agent: present=false in every case of the ${expectedBlockedIds.length} BLOCKED runs`,
      blockedRuns.length === expectedBlockedIds.length
        && blockedIdsSeen.join(',') === expectedBlockedIds.join(',')
        && blockedRuns.every(([, value]) => value.cases.every((p) => p === false)),
      `blocked=${blockedRuns.length} ${JSON.stringify(blockedRuns.map(([id, v]) => [id, v.cases]))}`)
    trace.record('A/agent: present=true in at least one case of a PASS run',
      passRuns.length === expectedPassIds.length
        && passIdsSeen.join(',') === expectedPassIds.join(',')
        && passRuns.some(([, value]) => value.cases.some((p) => p === true)),
      `pass=${passRuns.length} ${JSON.stringify(passRuns.map(([id, v]) => [id, v.cases]))}`)

    await goto(`validation/runs/${probeRunId}/manifest`, 'real manifest',
      `document.querySelector('[data-testid=manifest-not-frozen]') !== null`)
    await trace.screenshot(cdp, 'passA-03-manifest-absent')
    const manifestAbsent = await stateOf('[data-testid=manifest-not-frozen]')
    const manifestWarning = await stateOf('[data-testid=manifest-unauthenticated]')
    trace.record('A/manifest: a real run reports status absent (history not reconstructed)',
      (manifestAbsent ?? '').includes('absent')
        && (manifestAbsent ?? '').includes('not reconstructed'),
      (manifestAbsent ?? '').slice(0, 200))
    trace.record('A/manifest: the manifest_authentication = unauthenticated warning is shown',
      (manifestWarning ?? '').includes('unauthenticated')
        && (manifestWarning ?? '').includes('未签名'),
      (manifestWarning ?? '').slice(0, 200))

    /* ---- acl pass (same real root, acl deployment mode) ---- */
    await switchCore(args.proxy, 'acl')
    await changeToken(tokens.acl_unattributed, 'acl unattributed')
    await goto('validation/runs', 'acl list (unattributed grant)',
      `document.querySelector('[data-testid=validation-runs]') !== null`)
    await trace.screenshot(cdp, 'passA-04-acl-list-granted')
    const aclGranted = await listState()
    observed.acl.granted = aclGranted
    trace.record(`A/acl: with validation:unattributed the grant-filtered list shows all ${expectedIds.length} and says visibility=grant_filtered`,
      aclGranted.present === true && aclGranted.rows.length === expectedIds.length
        && (aclGranted.summary ?? '').includes('grant_filtered'),
      `rows=${aclGranted.rows.length} summary=${aclGranted.summary}`)

    await changeToken(tokens.read, 'acl read-only')
    await goto('validation/runs', 'acl list (no unattributed grant)',
      `document.querySelector('[data-testid=validation-summary]') !== null`)
    await trace.screenshot(cdp, 'passA-05-acl-list-refused')
    const aclRefused = await listState()
    observed.acl.refused = aclRefused
    trace.record(`A/acl: without validation:unattributed the same list is grant-filtered to zero (not an error, not the ${expectedIds.length})`,
      aclRefused.present === false && aclRefused.rows.length === 0
        && (aclRefused.summary ?? '').includes('grant_filtered')
        && (await appears(`document.querySelector('[data-testid=not-configured]') === null`, 500)),
      `rows=${aclRefused.rows.length} summary=${aclRefused.summary} emptyReason=${aclRefused.emptyReason}`)

    const ungranted = await browserFetch(`/v1/validation/runs/${probeRunId}`, tokens.read)
    const absent = await browserFetch('/v1/validation/runs/run-20200101T000000Z', tokens.read)
    observed.acl.ungranted = { status: ungranted.status, code: ungranted.body?.error?.code }
    observed.acl.nonexistent = { status: absent.status, code: absent.body?.error?.code }
    trace.record('A/acl: an ungranted run and a nonexistent run get the same one-code 404 (no existence oracle)',
      ungranted.status === 404 && absent.status === 404
        && ungranted.body?.error?.code === 'validation_run_not_found'
        && absent.body?.error?.code === 'validation_run_not_found',
      JSON.stringify({ ungranted: observed.acl.ungranted, nonexistent: observed.acl.nonexistent }))

    /* ---- a key without validation:read ---- */
    await switchCore(args.proxy, 'real')
    await changeToken(tokens.no_validation, 'no validation:read')
    await goto('validation/runs', 'forbidden list',
      `document.querySelector('[data-testid=forbidden]') !== null`)
    await trace.screenshot(cdp, 'passA-06-forbidden-scope')
    const forbidden = await stateOf('[data-testid=forbidden]')
    const forbiddenTable = await appears(
      `document.querySelector('[data-testid=validation-runs]') !== null`, 400)
    const forbiddenPage = await browserFetch('/v1/validation/runs', tokens.no_validation)
    observed.forbidden = { status: forbiddenPage.status, code: forbiddenPage.body?.error?.code }
    trace.record('A/refusal: a key without validation:read is refused 403 forbidden_scope in the browser, not shown an empty list',
      (forbidden ?? '').includes('forbidden_scope') && forbiddenTable === false
        && forbiddenPage.status === 403
        && forbiddenPage.body?.error?.code === 'forbidden_scope',
      JSON.stringify({ text: (forbidden ?? '').slice(0, 160), status: forbiddenPage.status,
        code: forbiddenPage.body?.error?.code }))

    /* ------------------------------------------------------------------ *
     * PASS B — the labelled fixture root (NOT evidence)
     * ------------------------------------------------------------------ */

    await switchCore(args.proxy, 'fixture')
    await changeToken(tokens.read, 'fixture root')

    await goto('validation/runs', 'fixture run list',
      `document.querySelector('[data-testid=validation-runs]') !== null`)
    await trace.screenshot(cdp, 'passB-01-fixture-list')
    const fixtureList = await listState()
    observed.fixture.list = fixtureList
    const unusableReasons = (fixtureList.reasons ?? []).join(' ')
    trace.record('B/list: the corrupt record and the duplicate stem are listed as unusable rows with their reasons',
      fixtureList.present === true && fixtureList.unusable === 3
        && unusableReasons.includes('run_id does not match the filename stem')
        && unusableReasons.includes('files share this stem'),
      `unusable=${fixtureList.unusable} runIds=${JSON.stringify(fixtureList.unusableRunIds)} reasons=${unusableReasons.slice(0, 260)}`)

    await goto(`validation/runs/${failRun}`, 'synthetic fail detail',
      `document.querySelector('[data-testid=synthetic]') !== null`)
    await trace.screenshot(cdp, 'passB-02-synthetic-fail')
    const failState = await detailState()
    const syntheticText = await stateOf('[data-testid=synthetic]')
    observed.fixture.fail = { verdict: failState.verdict, synthetic: failState.synthetic,
      failureClass: /ASSERTION_FAILED/.test(failState.bodyText),
      failureOutput: /expected 3, observed 2/.test(failState.bodyText) }
    trace.record('B/synthetic: the fixture FAIL run displays synthetic=true, verdict FAIL and its failure_class, and says it is not evidence',
      failState.verdict === 'FAIL' && failState.synthetic === true
        && (syntheticText ?? '').includes('不是证据')
        && /ASSERTION_FAILED/.test(failState.bodyText)
        && /NOT evidence/.test(failState.bodyText),
      JSON.stringify(observed.fixture.fail))

    const secret = expectations.artifacts.art_secret
    await goto(`validation/runs/${bundleRun}/artifacts/art_secret`, 'secret artifact',
      `document.querySelector('[data-testid=artifact-digests]') !== null`)
    await trace.screenshot(cdp, 'passB-03-secret-digests')
    const digests = await evaluate(cdp, `(() => {
      const out = {}
      for (const node of document.querySelectorAll('[data-testid=artifact-digests] .kv__digest')) {
        const label = (node.querySelector('span.muted')?.textContent ?? '').trim()
        const value = (node.querySelector('code')?.textContent ?? '').trim()
        out[label] = value
      }
      return out
    })()`)
    observed.artifacts.secretDigests = digests
    trace.record('B/artifact: digests.original and digests.masked are separate labelled values and differ for the assignment-shaped secret',
      typeof digests['digests.original'] === 'string'
        && typeof digests['digests.masked'] === 'string'
        && digests['digests.original'] !== digests['digests.masked']
        && digests['digests.masked'] === secret.masked_sha256,
      JSON.stringify(digests))

    const maskedBody = await browserFetch(
      `/v1/validation/runs/${bundleRun}/artifacts/art_secret/content`, tokens.read)
    await appears(`document.querySelector('[data-testid=artifact-content] pre') !== null`, 8000)
    const maskedPre = await evaluate(cdp,
      `document.querySelector('[data-testid=artifact-content] pre')?.textContent ?? null`)
    observed.artifacts.masked = {
      status: maskedBody.status,
      servedSha256: maskedBody.body?.served_sha256 ?? null,
      maskedSha256: maskedBody.body?.masked_sha256 ?? null,
      content: maskedBody.body?.content ?? null,
      originalSha256: maskedBody.body?.original_sha256 ?? null,
      contentIsOriginal: maskedBody.body?.content_is_original ?? null,
      expectedMaskedSha256: secret.masked_sha256,
      expectedMaskedText: secret.masked_text,
      looksLikeCredential: secret.looks_like_credential,
      redactVersion: expectations.redact_text_version,
      // The page renders untrusted text through `RawText`, which applies its own
      // mask-by-default pass on top of Core's, so the DOM shows `token=«已脱敏:…»` rather than
      // Core's `token=[REDACTED]`. The assertion is therefore "no cleartext secret and a mask
      // marker is on screen", while the digest/text equality above is checked against Core's
      // own response body.
      domPre: maskedPre,
      domPreHasCleartextSecret: (maskedPre ?? '').includes('token=x'),
      domPreHasMaskMarker: /\[REDACTED\]|已脱敏/.test(maskedPre ?? ''),
    }
    trace.record('B/masked: the served masked content matches an independent canonical.redact_text computation (digest and text)',
      maskedBody.status === 200
        && maskedBody.body?.content === secret.masked_text
        && maskedBody.body?.served_sha256 === secret.masked_sha256
        && maskedBody.body?.masked_sha256 === secret.masked_sha256
        && maskedBody.body?.content_is_original === false
        && secret.looks_like_credential === false
        && maskedPre !== null
        && observed.artifacts.masked.domPreHasMaskMarker === true
        && observed.artifacts.masked.domPreHasCleartextSecret === false,
      JSON.stringify({ status: maskedBody.status,
        served: maskedBody.body?.served_sha256, expected: secret.masked_sha256,
        looksLikeCredential: secret.looks_like_credential,
        redactVersion: expectations.redact_text_version,
        domPre: (maskedPre ?? '').slice(0, 120) }))

    // The UI path the task names: click "读取 original" and expect the withheld state.
    const originalButton = await evaluate(cdp, `(() => {
      const button = [...document.querySelectorAll('[data-testid=artifact-form] button')]
        .find((node) => (node.textContent ?? '').includes('original')) ?? null
      return button === null ? null : { disabled: button.disabled, text: button.textContent }
    })()`)
    let clicked = false
    if (originalButton !== null && originalButton.disabled === false) {
      await clickByText(cdp, '[data-testid=artifact-form] button', 'original')
      clicked = true
    }
    const withheld = await appears(
      `document.querySelector('[data-testid=original-withheld]') !== null`, 8000)
    await trace.screenshot(cdp, 'passB-04-original-withheld-or-not')
    const withheldText = await stateOf('[data-testid=original-withheld]')
    const notOffered = await stateOf('[data-testid=original-not-offered]')
    const maskedStillThere = await appears(
      `document.querySelector('[data-testid=artifact-content]') !== null`, 4000)
    observed.artifacts.withheldUi = { clicked, button: originalButton, withheld,
      withheldText, notOffered, maskedStillThere }
    trace.record('B/withheld (UI): requesting the original of the secret yields the withheld panel with its reason and masked digest, masked body still on screen',
      clicked === true && withheld === true
        && (withheldText ?? '').includes('mask_not_noop')
        && (withheldText ?? '').includes(secret.masked_sha256 ?? '')
        && maskedStillThere === true,
      JSON.stringify({ clicked, buttonDisabled: originalButton?.disabled, withheld,
        notOffered: (notOffered ?? '').slice(0, 120), maskedStillThere }))

    const withheldApi = await browserFetch(
      `/v1/validation/runs/${bundleRun}/artifacts/art_secret/content?form=original`, tokens.read)
    observed.artifacts.withheldApi = {
      status: withheldApi.status,
      code: withheldApi.body?.error?.code ?? null,
      reason: withheldApi.body?.error?.details?.reason ?? null,
      maskedSha256: withheldApi.body?.error?.details?.masked_sha256 ?? null,
    }
    trace.record('B/withheld (API through the proxy): the real Core withholds the original with reason mask_not_noop and the masked digest',
      withheldApi.status === 403
        && withheldApi.body?.error?.code === 'original_withheld'
        && withheldApi.body?.error?.details?.reason === 'mask_not_noop'
        && withheldApi.body?.error?.details?.masked_sha256 === secret.masked_sha256,
      JSON.stringify(observed.artifacts.withheldApi))

    await goto(`validation/runs/${bundleRun}/artifacts/art_html`, 'html artifact',
      `document.querySelector('[data-testid=artifact-content]') !== null`)
    await appears(`document.querySelector('[data-testid=artifact-content] pre') !== null`, 8000)
    await trace.screenshot(cdp, 'passB-05-html-as-text')
    const htmlState = await evaluate(cdp, `(() => {
      const panel = document.querySelector('[data-testid=artifact-content]')
      const pre = panel ? panel.querySelector('pre') : null
      return {
        parsedElements: panel ? panel.querySelectorAll('script, img, b').length : -1,
        preText: pre ? pre.textContent : null,
        injectedGlobal: typeof window.__p6_xss,
        bodyHasInjectedBold: document.querySelectorAll('b').length,
        bodyHasInjectedImg: document.querySelectorAll('img').length,
        bodyHasInjectedScript: document.querySelectorAll('script').length,
      }
    })()`)
    observed.artifacts.html = htmlState
    trace.record('B/html: the HTML artifact is rendered as text — nothing from it was parsed into the page',
      htmlState.parsedElements === 0 && htmlState.injectedGlobal === 'undefined'
        && (htmlState.preText ?? '').includes('<b>bold</b>')
        && (htmlState.preText ?? '').includes('<script>'),
      JSON.stringify(htmlState))

    /* ------------------------------------------------------------------ *
     * PASS C — misconfiguration (root unset)
     * ------------------------------------------------------------------ */

    await switchCore(args.proxy, 'unset')
    await goto('validation/runs', 'unset-root list',
      `document.querySelector('[data-testid=not-configured]') !== null`)
    await trace.screenshot(cdp, 'passC-01-not-configured')
    const notConfigured = await stateOf('[data-testid=not-configured]')
    const notConfiguredApi = await browserFetch('/v1/validation/runs', tokens.read)
    const noTable = await appears(
      `document.querySelector('[data-testid=validation-runs]') === null`, 400)
    observed.misconfig = { text: notConfigured, status: notConfiguredApi.status,
      code: notConfiguredApi.body?.error?.code,
      reason: notConfiguredApi.body?.error?.details?.reason }
    trace.record('C/misconfiguration: with the root unset the Inspector shows 503 validation_not_configured (details.reason=unset), not an empty list',
      (notConfigured ?? '').includes('validation_not_configured')
        && (notConfigured ?? '').includes('unset')
        && noTable === true
        && notConfiguredApi.status === 503
        && notConfiguredApi.body?.error?.code === 'validation_not_configured'
        && notConfiguredApi.body?.error?.details?.reason === 'unset',
      JSON.stringify(observed.misconfig))

    /* ---- console errors ---- */
    const expectedErrors = consoleErrors.filter((line) =>
      /Failed to load resource/.test(line) && /(403|404|409|503)/.test(line))
    const unexpected = consoleErrors.filter((line) => !expectedErrors.includes(line))
    trace.record('all passes: no unexpected console errors (the 403/404/503 refusals are provoked on purpose)',
      unexpected.length === 0, `${unexpected.length} unexpected${unexpected.length ? `: ${unexpected.join(' | ').slice(0, 240)}` : ''}`)

    await writeFile(join(args.out, 'pagination-observed.json'),
      JSON.stringify(observed.listPages, null, 2) + '\n')
    await writeFile(join(args.out, 'verdicts-observed.json'), JSON.stringify({
      listRows: list.rows.map((row) => ({ runId: row.runId, verdict: row.verdict,
        recordStatus: row.recordStatus })),
      paginated: observed.listPages.flatMap((page) => page.verdicts),
      details: Object.fromEntries(Object.entries(observed.details)
        .map(([runId, state]) => [runId, state.verdict])),
      agentByRun,
      contextKinds: [...contextKinds],
      contextReasons: [...contextReasons],
    }, null, 2) + '\n')
    await writeFile(join(args.out, 'fixture-observations.json'),
      JSON.stringify(observed.fixture, null, 2) + '\n')
    await writeFile(join(args.out, 'artifact-observations.json'),
      JSON.stringify(observed.artifacts, null, 2) + '\n')
    await writeFile(join(args.out, 'acl-observations.json'),
      JSON.stringify(observed.acl, null, 2) + '\n')

    // Response bodies are only readable while the CDP session is alive.
    const apiResponses = []
    for (const request of requests) {
      if (!request.path.startsWith('/v1/')) continue
      const response = responses.get(request.requestId) ?? {}
      const entry = {
        request_id: request.requestId,
        method: request.method,
        path: request.path,
        query: request.query,
        status: response.status ?? null,
        error_code: null,
        body: null,
      }
      if (request.path.startsWith('/v1/validation/')) {
        const body = await responseBody(cdp, request.requestId)
        entry.body = body
        if (body && typeof body === 'object' && body.error && typeof body.error === 'object') {
          entry.error_code = body.error.code ?? null
        }
      }
      apiResponses.push(entry)
    }
    await writeFile(join(args.out, 'api-requests.jsonl'),
      requests.filter((request) => request.path.startsWith('/v1/'))
        .map((request) => JSON.stringify({
          at: request.at,
          request_id: request.requestId,
          method: request.method,
          path: request.path,
          query: request.query,
          status: responses.get(request.requestId)?.status ?? null,
          authorization: request.authPresent,
          x_request_id: request.xRequestId,
        })).join('\n') + '\n')
    await writeFile(join(args.out, 'api-responses.jsonl'),
      apiResponses.map((entry) => JSON.stringify(entry)).join('\n') + '\n')
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
    await writeFile(join(args.out, 'chrome-stderr.log'), chromeLog.join('')).catch(() => {})
    await writeFile(join(args.out, 'chrome-version.json'),
      JSON.stringify({ chrome: chromePath, browser: cdpVersion?.Browser ?? null,
        protocol: cdpVersion?.['Protocol-Version'] ?? null, user_agent: cdpVersion?.['User-Agent'] ?? null,
      }, null, 2) + '\n').catch(() => {})
    await writeFile(join(args.out, 'click-trace.jsonl'),
      trace.steps.map((step) => JSON.stringify(step)).join('\n') + '\n').catch(() => {})
    await writeFile(join(args.out, 'browser-trace.md'),
      ['# P6-03c Validation Inspector — browser trace', '',
        `- ui: ${args.ui}`,
        `- switch proxy: ${args.proxy}`,
        `- started: ${new Date(trace.start).toISOString()}`,
        `- chrome: ${chromePath}`,
        `- steps: ${trace.steps.filter((step) => step.ok === true).length}/${trace.steps.length} passed, `
          + `${trace.steps.filter((step) => step.ok === null).length} BLOCKED`,
        '',
        ...trace.steps.map((step) =>
          `- ${step.ok === null ? 'BLOCKED' : step.ok ? 'PASS' : '**FAIL**'} ${step.step}`
          + `${step.detail ? ` — \`${String(step.detail).slice(0, 400)}\`` : ''}`),
        ''].join('\n')).catch(() => {})
    process.exitCode = trace.failed ? 1 : 0
  }
}

main().catch((error) => {
  process.stderr.write(`p7 validation inspector trace failed: ${error.stack ?? error.message}\n`)
  process.exitCode = 1
})
