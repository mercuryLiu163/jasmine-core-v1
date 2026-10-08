/**
 * Standalone host for the built dashboard.
 *
 * `vite build` produces `web/dist`; this server serves those files and forwards
 * `/v1/*` to the operator-configured loopback Core through the same allowlist
 * the dev proxy uses (`web/server/core-paths.json`). It is deliberately small
 * and dependency-free so that hosting the dashboard never needs a second
 * toolchain.
 *
 *   node server/serve.mjs --root dist --port 5180
 *   P6_CORE_ORIGIN=http://127.0.0.1:8789 node server/serve.mjs --root dist
 *
 * It binds loopback only and never sets an Authorization header.
 */

import http from 'node:http'
import { createReadStream, existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { extname, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// `import.meta.url` is a file: URL when this module is run by node, but a test
// runner may load it through its own module graph (an http: URL), so fall back
// to the working directory instead of throwing at import time.
let HERE = process.cwd()
try {
  HERE = fileURLToPath(new URL('.', import.meta.url))
} catch {
  HERE = process.cwd()
}
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost'])
// `content-length` matters: Core reads the body by Content-Length only, so a
// chunked forward (no Content-Length) arrives empty and every write is refused.
const FORWARDED_HEADERS = ['authorization', 'content-type', 'content-length', 'accept', 'x-request-id']

/**
 * Response headers applied to every served file. The CSP is sent as a real
 * header because `frame-ancestors` is ignored when delivered in a meta element,
 * and clickjacking protection that only looks present would be worse than none.
 */
const SECURITY_HEADERS = {
  'content-security-policy':
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'",
  'x-frame-options': 'DENY',
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
}

export function parseArgs(argv) {
  const args = { root: join(HERE, '..', 'dist'), port: 5180, host: '127.0.0.1' }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    if (flag === '--root') args.root = argv[++index]
    else if (flag === '--port') args.port = Number.parseInt(argv[++index], 10)
    else if (flag === '--host') args.host = argv[++index]
    else throw new Error(`unknown argument ${flag}`)
  }
  return args
}

/** Env var that says the Core behind this proxy is a P3 build. */
export const P3_READS_ENV = 'P6_ENABLE_P3_READS'
/** Env var that says the Core behind this proxy is a P4 build. */
export const P4_READS_ENV = 'P6_ENABLE_P4_READS'
/**
 * Env var that says the Core behind this proxy accepts the P6-03b2 memory writes.
 *
 * Separate from the reads switch on purpose: reading Memory and changing it carry different
 * scopes and different consequences, so an operator who has the P4 reads does not thereby
 * get the writes.
 */
export const P4_WRITES_ENV = 'P6_ENABLE_P4_WRITES'
/** Env var that says the Core behind this proxy carries the P7-01 Validation read slice. */
export const P7_READS_ENV = 'P6_ENABLE_P7_READS'

/** One spelling of "the operator says yes" for every opt-in, so the hosts cannot diverge. */
function flagEnabled(env, name) {
  const raw = (env[name] ?? '').trim().toLowerCase()
  return raw === '1' || raw === 'true' || raw === 'yes'
}

export function p3ReadsEnabled(env = process.env) {
  return flagEnabled(env, P3_READS_ENV)
}

export function p4ReadsEnabled(env = process.env) {
  return flagEnabled(env, P4_READS_ENV)
}

export function p4WritesEnabled(env = process.env) {
  return flagEnabled(env, P4_WRITES_ENV)
}

export function p7ReadsEnabled(env = process.env) {
  return flagEnabled(env, P7_READS_ENV)
}

export function loadAllowlist(directory = HERE, {
  enableP3Reads = p3ReadsEnabled(),
  enableP4Reads = p4ReadsEnabled(),
  enableP4Writes = p4WritesEnabled(),
  enableP7Reads = p7ReadsEnabled(),
} = {}) {
  const raw = JSON.parse(readFileSync(join(directory, 'core-paths.json'), 'utf8'))
  const baseline = raw.paths.map((pattern) => new RegExp(pattern))
  // The P3 continuity reads are real paths on a P3 Core and absent from this baseline,
  // so `paths` never carries them. They are appended only when the operator states that
  // the Core behind this proxy has P3; a baseline run therefore refuses them by name
  // (`proxy_path_not_allowed`) instead of forwarding them to a Core that cannot answer.
  const pending = (raw.p3_pending_paths ?? []).map((pattern) => new RegExp(pattern))
  // The P4 memory reads are the same arrangement for a different unmerged dependency: real
  // on a P4 Core, absent here, and appended only on the operator's say-so.
  const pendingP4 = (raw.p4_pending_paths ?? []).map((pattern) => new RegExp(pattern))
  // The P6-03b2 memory writes are the same arrangement again, but they are POSTs and they are
  // gated by their own switch. They are appendable to `write_paths` only, never to the read
  // list: `isAllowed` picks one list by method, so a P4 write can never be reached with GET.
  const pendingP4Writes = (raw.p4_write_paths ?? []).map((pattern) => new RegExp(pattern))
  // The P7-01 Validation reads are the same arrangement again: real on a Core that carries
  // the read slice, absent from this baseline, and appended only on the operator's say-so.
  const pendingP7 = (raw.p7_pending_paths ?? []).map((pattern) => new RegExp(pattern))
  return {
    methods: new Set(raw.methods.map((method) => method.toUpperCase())),
    paths: [
      ...baseline,
      ...(enableP3Reads ? pending : []),
      ...(enableP4Reads ? pendingP4 : []),
      ...(enableP7Reads ? pendingP7 : []),
    ],
    baseline_paths: baseline,
    p3_pending_paths: pending,
    p3_reads_enabled: enableP3Reads,
    p4_pending_paths: pendingP4,
    p4_reads_enabled: enableP4Reads,
    p7_pending_paths: pendingP7,
    p7_reads_enabled: enableP7Reads,
    write_methods: raw.write_methods ?? [],
    write_paths: [
      ...(raw.write_paths ?? []),
      ...(enableP4Writes ? raw.p4_write_paths ?? [] : []),
    ],
    baseline_write_paths: raw.write_paths ?? [],
    p4_write_paths: pendingP4Writes,
    p4_writes_enabled: enableP4Writes,
  }
}

export function resolveCoreOrigin(raw) {
  const value = (raw ?? '').trim() || 'http://127.0.0.1:8787'
  let origin
  try {
    origin = new URL(value)
  } catch {
    throw new Error(`P6_CORE_ORIGIN is not a URL: ${JSON.stringify(value)}`)
  }
  if (origin.protocol !== 'http:') throw new Error('P6_CORE_ORIGIN must be http')
  if (!LOOPBACK.has(origin.hostname)) throw new Error('P6_CORE_ORIGIN must be loopback')
  if (origin.username || origin.password) {
    throw new Error('P6_CORE_ORIGIN must not embed credentials')
  }
  if (origin.pathname !== '/' || origin.search) throw new Error('P6_CORE_ORIGIN must be a bare origin')
  return origin
}

export function isAllowed(allowlist, method, pathname) {
  const verb = (method ?? 'GET').toUpperCase()
  if (allowlist.methods.has(verb)) {
    return allowlist.paths.some((pattern) => pattern.test(pathname))
  }
  // Uppercased like `core-proxy.ts`: the JSON could carry a lowercase entry, and the two hosts
  // must not disagree about which methods the write list covers.
  if ((allowlist.write_methods ?? []).map((method) => method.toUpperCase()).includes(verb)) {
    return (allowlist.write_paths ?? []).some((pattern) => new RegExp(pattern).test(pathname))
  }
  return false
}

/**
 * See `core-proxy.ts` for why: an absolute-form or protocol-relative request
 * target must be refused, not resolved against the bundle root, or it falls
 * through to the SPA shell and answers 200 for an API-shaped path.
 */
export function classifyRequestTarget(rawUrl) {
  const value = rawUrl ?? ''
  if (value.length === 0) return { kind: 'bad', pathname: value, reason: 'empty request target' }
  if (value.startsWith('//')) {
    return { kind: 'bad', pathname: value, reason: 'protocol-relative request target' }
  }
  if (!value.startsWith('/')) {
    return { kind: 'bad', pathname: value, reason: 'absolute or non-origin request target' }
  }
  let pathname
  try {
    pathname = new URL(value, 'http://ui.invalid').pathname
  } catch {
    return { kind: 'bad', pathname: value, reason: 'unparsable request target' }
  }
  if (pathname.startsWith('/v1/') || pathname === '/v1') return { kind: 'api', pathname }
  return { kind: 'static', pathname }
}

function sendJson(res, status, body) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(body))
}

export function proxy(req, res, origin, allowlist) {
  const classified = classifyRequestTarget(req.url)
  if (classified.kind === 'bad') {
    sendJson(res, 400, {
      error: { code: 'proxy_bad_target', message: `refused request target: ${classified.reason}` },
    })
    return
  }
  let target
  try {
    target = new URL(req.url, 'http://ui.invalid')
  } catch {
    sendJson(res, 400, { error: { code: 'proxy_bad_target', message: 'unparsable request target' } })
    return
  }
  if (!isAllowed(allowlist, req.method, target.pathname)) {
    sendJson(res, 404, {
      error: {
        code: 'proxy_path_not_allowed',
        message: `the dashboard proxy does not forward ${req.method} ${target.pathname}`,
      },
    })
    return
  }
  const headers = { host: origin.host }
  for (const name of FORWARDED_HEADERS) {
    const value = req.headers[name]
    if (typeof value === 'string' && value.length > 0) headers[name] = value
  }
  const upstream = http.request(
    {
      hostname: origin.hostname,
      port: origin.port || 80,
      method: req.method,
      path: target.pathname + target.search,
      headers,
    },
    (reply) => {
      res.statusCode = reply.statusCode ?? 502
      for (const [name, value] of Object.entries(reply.headers)) {
        const lower = name.toLowerCase()
        if (lower === 'transfer-encoding' || lower === 'connection') continue
        if (value !== undefined) res.setHeader(name, Array.isArray(value) ? value.join(', ') : value)
      }
      res.setHeader('cache-control', 'no-store')
      reply.pipe(res)
    },
  )
  upstream.on('error', (error) => {
    if (res.headersSent) {
      res.destroy()
      return
    }
    sendJson(res, 502, {
      error: {
        code: 'proxy_core_unreachable',
        message: `${origin.origin}: ${error.code ?? error.message}`,
      },
    })
  })
  req.pipe(upstream)
}

export function serveStatic(req, res, root) {
  const classified = classifyRequestTarget(req.url)
  if (classified.kind === 'bad' || classified.kind === 'api') {
    // Belt and braces: an API-shaped path must never be answered with the shell.
    sendJson(res, 400, {
      error: {
        code: classified.kind === 'api' ? 'static_api_path_refused' : 'static_bad_path',
        message: classified.kind === 'api'
          ? 'API paths are only served by the proxy'
          : `refused request target: ${classified.reason}`,
      },
    })
    return
  }
  let target
  let relative
  try {
    target = new URL(req.url, 'http://ui.invalid')
    // A malformed percent-escape must be a 400, not an exception inside the
    // request handler (which would surface as an opaque 500/socket error).
    relative = normalize(decodeURIComponent(target.pathname)).replace(/^[/\\]+/, '')
  } catch {
    sendJson(res, 400, { error: { code: 'static_bad_path', message: 'unparsable request path' } })
    return
  }
  const base = resolve(root)
  let filePath = resolve(base, relative)
  if (filePath !== base && !filePath.startsWith(base + sep)) {
    sendJson(res, 403, { error: { code: 'static_path_denied', message: 'path escapes the bundle root' } })
    return
  }
  // Hash routing keeps every real route inside `#/…`, so unknown paths get the shell.
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) filePath = join(base, 'index.html')
  if (!existsSync(filePath)) {
    sendJson(res, 404, { error: { code: 'bundle_missing', message: `build the dashboard first (${base})` } })
    return
  }
  res.statusCode = 200
  res.setHeader('content-type', MIME[extname(filePath)] ?? 'application/octet-stream')
  res.setHeader('cache-control', extname(filePath) === '.html' ? 'no-store' : 'no-cache')
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(name, value)
  createReadStream(filePath).pipe(res)
}

export function createDashboardServer({ root, origin, allowlist = loadAllowlist() }) {
  return http.createServer((req, res) => {
    const target = classifyRequestTarget(req.url)
    if (target.kind === 'bad') {
      sendJson(res, 400, {
        error: { code: 'proxy_bad_target', message: `refused request target: ${target.reason}` },
      })
      return
    }
    if (target.kind === 'api') proxy(req, res, origin, allowlist)
    else serveStatic(req, res, root)
  })
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  if (!LOOPBACK.has(args.host)) throw new Error('refusing to bind a non-loopback host')
  const origin = resolveCoreOrigin(process.env.P6_CORE_ORIGIN)
  const root = resolve(args.root)
  const server = createDashboardServer({ root, origin })
  server.listen(args.port, args.host, () => {
    process.stderr.write(
      `p6 dashboard on http://${args.host}:${args.port} (bundle ${root}, Core ${origin.origin})\n`,
    )
  })
}

/**
 * Compare resolved paths: `pathToFileURL('/tmp/x.mjs')` is not `import.meta.url`
 * when the file was reached through a symlink (macOS `/tmp` -> `/private/tmp`),
 * and the mismatch makes this entry point exit 0 without serving anything.
 */
function isEntryPoint() {
  if (!process.argv[1]) return false
  try {
    return import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href
  } catch {
    return false
  }
}

if (isEntryPoint()) main()
