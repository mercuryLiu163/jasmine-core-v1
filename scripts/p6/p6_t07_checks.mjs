#!/usr/bin/env node
/**
 * P6-T07 extra checks (throwaway script, not part of the repository).
 *
 * Phases, each opening its own headless Chrome against an isolated fixture:
 *   paginate  long list: page through every Event via 继续加载 and assert the
 *             UI's own cursor produces no duplicate and no missing seq
 *   longtext  a ~216 KiB Event must not hide the source controls
 *   offline   with Core stopped, a reload must show the unreachable state and
 *             must not render an empty list
 *   recover   with Core back, a retry must read Truth from the server again
 */

import { spawn } from 'node:child_process'
import { existsSync, realpathSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

function parseArgs(argv) {
  const args = {}
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, '').replace(/-(\w)/g, (_, c) => c.toUpperCase())
    args[key] = argv[i + 1]
  }
  return args
}

class Cdp {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map() }
  async connect() {
    this.socket = new WebSocket(this.url)
    await new Promise((res, rej) => {
      this.socket.addEventListener('open', () => res())
      this.socket.addEventListener('error', (e) => rej(new Error(`socket: ${e.message ?? 'error'}`)))
    })
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const { resolve: res, reject: rej } = this.pending.get(message.id)
        this.pending.delete(message.id)
        message.error ? rej(new Error(message.error.message)) : res(message.result)
      }
    })
  }
  send(method, params = {}) {
    const id = this.nextId++
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }
  close() { this.socket?.close() }
}

const httpJson = (url, method = 'GET') => new Promise((res, rej) => {
  const req = http.request(url, { method }, (r) => {
    let b = ''; r.on('data', (c) => (b += c)); r.on('end', () => { try { res(JSON.parse(b)) } catch (e) { rej(e) } })
  })
  req.on('error', rej); req.end()
})

async function waitForCdp(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try { const v = await httpJson(`http://127.0.0.1:${port}/json/version`); if (v.webSocketDebuggerUrl) return v } catch {}
    await new Promise((r) => setTimeout(r, 150))
  }
  throw new Error('no devtools endpoint')
}

const evaluate = async (cdp, expression) => {
  const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(`evaluate: ${r.exceptionDetails.text}`)
  return r.result.value
}

async function waitFor(cdp, expression, { timeoutMs = 15000, label = expression } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try { if (await evaluate(cdp, expression)) return true } catch {}
    await new Promise((r) => setTimeout(r, 120))
  }
  throw new Error(`timeout: ${label}`)
}

async function clickByText(cdp, selector, text) {
  const point = await evaluate(cdp, `(() => {
    const node = [...document.querySelectorAll(${JSON.stringify(selector)})]
      .find((n) => (n.textContent ?? '').includes(${JSON.stringify(text)}))
    if (!node) return null
    node.scrollIntoView({ block: 'center' })
    const b = node.getBoundingClientRect()
    return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }
  })()`)
  if (!point) throw new Error(`no ${selector} with ${text}`)
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.send('Input.dispatchMouseEvent', { type, x: point.x, y: point.y, button: 'left', clickCount: 1 })
  }
  return point
}

async function withChrome(ui, out, fn) {
  const port = 9700 + Math.floor(Math.random() * 300)
  const profile = await mkdtemp(join(tmpdir(), 'p6-t07-chrome-'))
  const chrome = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
    '--window-size=1280,900', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] })
  let cdp
  try {
    await waitForCdp(port)
    const target = await httpJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(ui)}`, 'PUT')
    cdp = new Cdp(target.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable'); await cdp.send('Runtime.enable')
    return await fn(cdp)
  } finally {
    cdp?.close(); chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
  }
}

async function signIn(cdp, token) {
  await waitFor(cdp, `document.querySelector('input[type=password]') !== null`, { label: 'sign-in' })
  await evaluate(cdp, `document.querySelector('input[type=password]').focus()`)
  await cdp.send('Input.insertText', { text: token })
  await clickByText(cdp, 'button', '使用此 token')
  await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`, { label: 'overview' })
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const out = resolve(args.out)
  await mkdir(join(out, 'screenshots'), { recursive: true })
  const token = JSON.parse(await readFile(args.tokenFile, 'utf8'))[args.tokenKey ?? 'read']
  const results = []
  const record = (step, ok, detail = '') => {
    results.push({ step, ok, detail })
    process.stderr.write(`${ok ? 'ok  ' : 'FAIL'} ${step}${detail ? ` — ${detail}` : ''}\n`)
  }

  if (args.phase === 'paginate') {
    await withChrome(args.ui, out, async (cdp) => {
      await signIn(cdp, token)
      await cdp.send('Page.navigate', { url: `${args.ui}/#/conversation` })
      await waitFor(cdp, `document.querySelectorAll('.event').length > 0`, { label: 'events' })
      const pages = []
      let guard = 0
      while (guard < 12) {
        guard += 1
        const state = await evaluate(cdp, `(() => {
          const rows = [...document.querySelectorAll('.event')].map((n) => Number(n.getAttribute('data-seq')))
          const button = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('继续加载'))
          return { seqs: rows, hasMore: Boolean(button), meta: document.querySelector('.pager__meta')?.textContent ?? '' }
        })()`)
        pages.push({ rows: state.seqs.length, hasMore: state.hasMore, meta: state.meta.replace(/\s+/g, ' ').slice(0, 90) })
        if (!state.hasMore) break
        await clickByText(cdp, 'button', '继续加载')
        await new Promise((r) => setTimeout(r, 400))
      }
      const final = await evaluate(cdp, `[...document.querySelectorAll('.event')].map((n) => Number(n.getAttribute('data-seq')))`)
      const unique = new Set(final)
      const strictlyIncreasing = final.every((seq, i) => i === 0 || seq > final[i - 1])
      record('long list: multiple pages were needed', pages.filter((p) => p.hasMore).length >= 2, JSON.stringify(pages))
      record('long list: no duplicate seq after paging', unique.size === final.length, `${final.length} rows, ${unique.size} unique`)
      record('long list: seq strictly increasing (no gap in the UI order)', strictlyIncreasing, `first=${final[0]} last=${final[final.length - 1]}`)
      const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(out, 'screenshots', 't07-paginated.png'), Buffer.from(screenshot.data, 'base64'))
    })
  }

  if (args.phase === 'longtext') {
    await withChrome(args.ui, out, async (cdp) => {
      await signIn(cdp, token)
      await cdp.send('Page.navigate', { url: `${args.ui}/#/event/${args.event}` })
      // The panel title renders before the read resolves, so wait for the body.
      await waitFor(cdp, `document.querySelector('.rawtext__body') !== null`, { label: 'event body' })
      const state = await evaluate(cdp, `(() => {
        const body = document.querySelector('.rawtext__body')
        const link = document.querySelector('.event__links a[data-kind=event]') ?? document.querySelector('a[data-kind=event]')
        if (link) link.scrollIntoView({ block: 'center' })
        const box = link ? link.getBoundingClientRect() : null
        return {
          rawChars: body ? body.textContent.length : 0,
          collapsed: body ? body.textContent.includes('…(+') : false,
          sourceLinks: document.querySelectorAll('a[data-kind=event]').length,
          sourceLinkVisible: box ? box.width > 0 && box.height > 0 && box.top >= 0 && box.top < window.innerHeight : false,
        }
      })()`)
      record('long Event is collapsed, not printed in full', state.collapsed === true && state.rawChars < 1000, JSON.stringify(state))
      record('long Event does not hide the source controls', state.sourceLinks > 0 && state.sourceLinkVisible === true, `links=${state.sourceLinks}`)
      const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(out, 'screenshots', 't07-longtext.png'), Buffer.from(screenshot.data, 'base64'))
    })
  }

  if (args.phase === 'accept') {
    // Blocker #3 from the independent review: the Evidence panel must show a
    // real `task.accepted` acceptance, not only step transitions. This phase
    // needs a fixture that has actually accepted a Task.
    await withChrome(args.ui, out, async (cdp) => {
      await signIn(cdp, token)
      await cdp.send('Page.navigate', { url: `${args.ui}/#/task/${args.task}/evidence` })
      await waitFor(cdp, `document.body.textContent.includes('Core 记录的判定依据')`, { label: 'gate panel' })
      await waitFor(cdp, `!/正在读取/.test(document.body.textContent)`, { label: 'reads settled' })
      const state = await evaluate(cdp, `(() => {
        const panel = [...document.querySelectorAll('section')]
          .find((n) => (n.getAttribute('aria-label') ?? '').includes('Core 记录的判定依据'))
        if (!panel) return null
        const text = panel.textContent ?? ''
        const links = [...panel.querySelectorAll('a[data-kind=event]')].map((a) => a.getAttribute('data-id'))
        const evidenceLinks = [...panel.querySelectorAll('a[data-kind=evidence]')].map((a) => a.getAttribute('data-id'))
        return { text: text.replace(/\s+/g, ' ').slice(0, 220), eventIds: links, evidenceIds: evidenceLinks,
                 emptyState: text.includes('没有 VERIFIED/ACCEPTED 记录') }
      })()`)
      const showsAcceptance = Boolean(state) && state.eventIds.includes(args.event)
      const showsEvidence = Boolean(state) && state.evidenceIds.includes(args.evidence)
      record('task.accepted acceptance is listed as a gate record', showsAcceptance,
        state ? `eventIds=${JSON.stringify(state.eventIds)}` : 'panel missing')
      record('the acceptance links the evidence_ids Core recorded', showsEvidence,
        state ? `evidenceIds=${JSON.stringify(state.evidenceIds)}` : 'panel missing')
      record('the empty-state wording is not used when an acceptance exists',
        Boolean(state) && state.emptyState === false, state ? state.text : '')
      const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(out, 'screenshots', 'accept-panel.png'), Buffer.from(screenshot.data, 'base64'))
    })
  }

  if (args.phase === 'offline' || args.phase === 'recover') {
    await withChrome(args.ui, out, async (cdp) => {
      await signIn(cdp, token)
      await cdp.send('Page.navigate', { url: `${args.ui}/#/overview` })
      if (args.phase === 'offline') {
        await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`, { label: 'overview loaded' })
        // Signal the orchestrator that Core may now be stopped.
        await writeFile(join(out, 'ready-for-core-stop'), 'ready\n')
        const deadline = Date.now() + 20000
        while (Date.now() < deadline) {
          if (existsSync(join(out, 'core-is-down'))) break
          await new Promise((r) => setTimeout(r, 200))
        }
        await clickByText(cdp, 'button', '重新读取')
        await new Promise((r) => setTimeout(r, 1500))
        const text = await evaluate(cdp, 'document.body.textContent')
        const visible = /无法连接 Core|proxy_core_unreachable|未分类失败/.test(text)
        const copy = (text.match(/未分类失败[^。]*。|无法连接 Core[^。]*。/) ?? [''])[0]
        record('API down: the failure is visible, not an empty list', visible, copy.replace(/\s+/g, ' ').slice(0, 140))
        record('API down: the empty-state wording is not used', !text.includes('空:Projects') && !text.includes('空:Hosts'))
        const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
        await writeFile(join(out, 'screenshots', 't07-offline.png'), Buffer.from(screenshot.data, 'base64'))
      } else {
        const deadline = Date.now() + 20000
        while (Date.now() < deadline) {
          if (existsSync(join(out, 'core-is-up'))) break
          await new Promise((r) => setTimeout(r, 200))
        }
        await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`, { label: 'overview after recovery' })
        await clickByText(cdp, 'button', '重新读取')
        await waitFor(cdp, `document.body.textContent.includes('Jasmine Core V1') || document.body.textContent.includes('Projects(已载入')`, { label: 'projects after recovery' })
        const state = await evaluate(cdp, `(() => ({
          hasProjectRow: document.body.textContent.includes('Jasmine Core V1'),
          unreachableGone: !document.body.textContent.includes('无法连接 Core'),
          schema: document.body.textContent.includes('schema_version'),
        }))()`)
        record('API restored: the UI reads Core Truth again', state.hasProjectRow === true && state.unreachableGone === true, JSON.stringify(state))
        const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
        await writeFile(join(out, 'screenshots', 't07-recovered.png'), Buffer.from(screenshot.data, 'base64'))
      }
    })
  }

  await writeFile(join(out, `t07-${args.phase}.json`), JSON.stringify({
    phase: args.phase, ui: args.ui, at: new Date().toISOString(),
    passed: results.filter((r) => r.ok).length, total: results.length, results,
  }, null, 2) + '\n')
  process.exitCode = results.every((r) => r.ok) ? 0 : 1
}

// Resolve symlinks: `pathToFileURL('/tmp/x')` is not the same URL as
// `import.meta.url` when /tmp is a symlink to /private/tmp, and the mismatch
// makes the script exit 0 having done nothing.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  main().catch((e) => { process.stderr.write(`t07 check failed: ${e.stack ?? e.message}\n`); process.exitCode = 1 })
}
