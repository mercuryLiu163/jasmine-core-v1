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

const HERE = fileURLToPath(new URL('.', import.meta.url))
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost'])
const FORWARDED_HEADERS = ['authorization', 'content-type', 'accept', 'x-request-id']

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

export function loadAllowlist(directory = HERE) {
  const raw = JSON.parse(readFileSync(join(directory, 'core-paths.json'), 'utf8'))
  return {
    methods: new Set(raw.methods.map((method) => method.toUpperCase())),
    paths: raw.paths.map((pattern) => new RegExp(pattern)),
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
  return (
    allowlist.methods.has((method ?? 'GET').toUpperCase()) &&
    allowlist.paths.some((pattern) => pattern.test(pathname))
  )
}

function sendJson(res, status, body) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(body))
}

export function proxy(req, res, origin, allowlist) {
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
    if ((req.url ?? '').startsWith('/v1/')) proxy(req, res, origin, allowlist)
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
