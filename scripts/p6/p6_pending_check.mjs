#!/usr/bin/env node
/**
 * Throwaway check (not part of the repository): P6-01 "未接入" honesty.
 *
 * Two claims to falsify:
 *  1. Every area whose contract is not frozen (P2 Interpretation, P3
 *     Checkpoint/Context, P4 Memory, P7-01 Validation, P5 Registry online,
 *     P5/P7 Backup/Storage) is reachable and visibly marked 未接入 with the
 *     owning phase named.
 *  2. The dashboard never *attempts* a request to one of those unfrozen
 *     endpoints. The proxy allowlist would refuse it anyway, but a UI that tries
 *     is a UI that expects a contract nobody froze.
 */

import { spawn } from 'node:child_process'
import { existsSync, realpathSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const KEYS = ['interpretation', 'checkpoint', 'context', 'memory', 'validation', 'registry', 'backup']
// Paths that do not exist in the P1 baseline; the UI must never ask for them.
const UNFROZEN = [
  '/v1/interpret', '/v1/interpretations', '/v1/reviews', '/v1/checkpoints',
  '/v1/context', '/v1/memory', '/v1/jobs', '/v1/recall', '/v1/validation',
  '/v1/backups', '/v1/devices', '/v1/storage',
]

function parseArgs(argv) {
  const args = {}
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, '').replace(/-(\w)/g, (_, c) => c.toUpperCase())
    args[key] = argv[i + 1]
  }
  return args
}

class Cdp {
  constructor(url) { this.url = url; this.nextId = 1; this.pending = new Map(); this.events = [] }
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
        return
      }
      if (message.method) this.events.push(message)
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

async function waitFor(cdp, expression, { timeoutMs = 12000, label = expression } = {}) {
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

  const port = 9800 + Math.floor(Math.random() * 150)
  const profile = await mkdtemp(join(tmpdir(), 'p6-pending-chrome-'))
  const chrome = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
    '--window-size=1280,900', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] })
  let cdp
  try {
    await waitForCdp(port)
    const target = await httpJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(args.ui)}`, 'PUT')
    cdp = new Cdp(target.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')

    await waitFor(cdp, `document.querySelector('input[type=password]') !== null`, { label: 'sign-in' })
    await evaluate(cdp, `document.querySelector('input[type=password]').focus()`)
    await cdp.send('Input.insertText', { text: token })
    await clickByText(cdp, 'button', '使用此 token')
    await waitFor(cdp, `document.body.textContent.includes('Core 健康与 Schema')`, { label: 'overview' })

    const requested = []
    cdp.events.length = 0
    for (const key of KEYS) {
      await cdp.send('Page.navigate', { url: `${args.ui}/#/pending/${key}` })
      await waitFor(cdp, `document.body.textContent.includes('未接入')`, { label: `pending/${key}` })
      const state = await evaluate(cdp, `(() => {
        const panels = [...document.querySelectorAll('[data-testid=unavailable]')]
        const text = document.body.textContent
        return { panels: panels.length, hasOwner: text.includes('提供方'), hasWaiting: text.includes('等待冻结'),
                 text: (panels[0]?.textContent ?? '').replace(/\\s+/g, ' ').slice(0, 110) }
      })()`)
      record(`pending/${key} is marked 未接入 with owner and waiting-on`, state.panels > 0 && state.hasOwner && state.hasWaiting, state.text)
    }

    // An unknown placeholder key must not silently render something else.
    await cdp.send('Page.navigate', { url: `${args.ui}/#/pending/not-a-real-area` })
    await waitFor(cdp, `document.body.textContent.includes('未知的未接入页面')`, { label: 'unknown pending key' })
    record('an unknown placeholder key is reported, not silently rendered', true)

    // Also confirm the in-page placeholders on the real views.
    for (const [label, hash] of [
      ['overview', '#/overview'],
      ['conversation', '#/conversation'],
      ['task evidence', `#/task/${args.task}/evidence`],
      ['rules', '#/rules'],
    ]) {
      await cdp.send('Page.navigate', { url: `${args.ui}/${hash}` })
      await waitFor(cdp, `document.body.textContent.includes('未接入')`, { label })
    }
    record('the real views carry their own 未接入 panels', true)

    for (const message of cdp.events) {
      if (message.method !== 'Network.requestWillBeSent') continue
      const url = new URL(message.params.request.url)
      if (url.pathname.startsWith('/v1/')) requested.push(url.pathname)
    }
    const attempted = requested.filter((path) => UNFROZEN.some((prefix) => path.startsWith(prefix)))
    record(
      'no unfrozen P2/P3/P4/P7 endpoint is ever requested',
      attempted.length === 0,
      `api requests seen=${requested.length} unfrozen=${JSON.stringify([...new Set(attempted)])}`,
    )

    const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(out, 'screenshots', 'pending-context.png'), Buffer.from(screenshot.data, 'base64'))
  } finally {
    cdp?.close(); chrome.kill('SIGTERM')
    await rm(profile, { recursive: true, force: true }).catch(() => {})
  }

  await writeFile(join(out, 'pending-check.json'), JSON.stringify({
    ui: args.ui, at: new Date().toISOString(),
    passed: results.filter((r) => r.ok).length, total: results.length, results,
  }, null, 2) + '\n')
  process.exitCode = results.every((r) => r.ok) ? 0 : 1
}

function isEntryPoint() {
  if (!process.argv[1]) return false
  try { return import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href } catch { return false }
}

if (isEntryPoint()) {
  main().catch((e) => { process.stderr.write(`pending check failed: ${e.stack ?? e.message}\n`); process.exitCode = 1 })
}
