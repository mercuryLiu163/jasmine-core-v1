/**
 * P6-A02 — the only browser-to-Core channel.
 *
 * The dashboard is served from its own origin and reaches Core through this
 * loopback proxy. Three properties matter and are enforced here, not in the UI:
 *
 * 1. The upstream target is operator configuration (`P6_CORE_ORIGIN`) resolved
 *    once at start-up. No request header, query parameter or body can choose or
 *    influence it, so the proxy cannot be turned into an SSRF gadget.
 * 2. Only the frozen Core paths in `core-paths.json` are forwarded, and only for
 *    the methods listed there. `/v1/auth/keys` and `/v1/audit` are absent: the
 *    browser cannot mint a key or read audit through this channel even if its
 *    token happened to carry `admin`.
 * 3. The proxy never adds an Authorization header. Whatever the page holds in
 *    memory is what Core sees, and Core remains the only authority on scopes.
 */

import { randomUUID } from 'node:crypto'
import http from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

import allowlist from './core-paths.json' with { type: 'json' }

const LOOPBACK_HOSTNAMES = new Set(['127.0.0.1', '::1', 'localhost'])
const ALLOWED_METHODS = new Set(allowlist.methods.map((method) => method.toUpperCase()))

/**
 * Must match `serve.mjs`: the P3, P4 and P7 reads are opt-in for a Core that has them, never
 * implicit, and both hosts spell "the operator says yes" the same way. The environment is
 * read once at module load, which is why the tests reset the module registry rather than
 * passing an option. The P4 *writes* have their own switch, so an operator who has the reads
 * has not thereby agreed to let the browser change Memory.
 */
function flagEnabled(name: string): boolean {
  const raw = (process.env[name] ?? '').trim().toLowerCase()
  return raw === '1' || raw === 'true' || raw === 'yes'
}
const p3ReadsEnabled = flagEnabled('P6_ENABLE_P3_READS')
const p4ReadsEnabled = flagEnabled('P6_ENABLE_P4_READS')
const p4WritesEnabled = flagEnabled('P6_ENABLE_P4_WRITES')
const p7ReadsEnabled = flagEnabled('P6_ENABLE_P7_READS')
const pendingPaths = allowlist as {
  p3_pending_paths?: string[]
  p4_pending_paths?: string[]
  p7_pending_paths?: string[]
}
const ALLOWED_PATHS = [
  ...allowlist.paths,
  ...(p3ReadsEnabled ? pendingPaths.p3_pending_paths ?? [] : []),
  ...(p4ReadsEnabled ? pendingPaths.p4_pending_paths ?? [] : []),
  ...(p7ReadsEnabled ? pendingPaths.p7_pending_paths ?? [] : []),
].map((pattern) => new RegExp(pattern))
const WRITE_METHODS = new Set(
  (allowlist.write_methods ?? []).map((method) => method.toUpperCase()),
)
const WRITE_PATHS = [
  ...(allowlist.write_paths ?? []),
  // Only ever appended to the POST list: `isAllowedCoreRequest` selects a list by method, so
  // a b2 write cannot be reached with GET even when the switch is on.
  ...(p4WritesEnabled ? (allowlist as { p4_write_paths?: string[] }).p4_write_paths ?? [] : []),
].map((pattern) => new RegExp(pattern))
/** Headers worth forwarding. Anything else (cookies, origin, host) is dropped. */
const FORWARDED_HEADERS = [
  'authorization',
  'content-type',
  // Core reads a request body by Content-Length only; forwarding the body without
  // this header makes Node use chunked encoding and Core sees an empty body.
  'content-length',
  'accept',
  'x-request-id',
] as const

export class ProxyConfigError extends Error {}

export type TargetKind = 'api' | 'static' | 'bad'

/**
 * Classify an HTTP request target before anything else looks at it.
 *
 * Only an origin-form path (`/…`, but not `//…`) is a path this host serves.
 * An absolute-form target (`scheme://host/…`) and a protocol-relative one
 * (`//host/…`) are legal HTTP for a proxy but are not something this dashboard
 * ever issues, and treating them as a local path let a malformed request fall
 * through to the static handler and receive the SPA shell with 200 — which looks
 * like a reachable endpoint. They are refused outright instead.
 */
export function classifyRequestTarget(rawUrl: string | undefined): {
  kind: TargetKind
  pathname: string
  reason?: string
} {
  const value = rawUrl ?? ''
  if (value.length === 0) return { kind: 'bad', pathname: value, reason: 'empty request target' }
  if (value.startsWith('//')) {
    return { kind: 'bad', pathname: value, reason: 'protocol-relative request target' }
  }
  if (!value.startsWith('/')) {
    return { kind: 'bad', pathname: value, reason: 'absolute or non-origin request target' }
  }
  let pathname: string
  try {
    pathname = new URL(value, 'http://ui.invalid').pathname
  } catch {
    return { kind: 'bad', pathname: value, reason: 'unparsable request target' }
  }
  if (pathname.startsWith('/v1/') || pathname === '/v1') return { kind: 'api', pathname }
  return { kind: 'static', pathname }
}

/** Parse and validate the operator-configured Core origin. Loopback HTTP only. */
export function resolveCoreOrigin(raw: string | undefined): URL {
  const value = (raw ?? '').trim() || 'http://127.0.0.1:8787'
  let origin: URL
  try {
    origin = new URL(value)
  } catch {
    throw new ProxyConfigError(`P6_CORE_ORIGIN is not a URL: ${JSON.stringify(value)}`)
  }
  if (origin.protocol !== 'http:') {
    throw new ProxyConfigError(`P6_CORE_ORIGIN must be http (Core has no TLS): ${value}`)
  }
  if (!LOOPBACK_HOSTNAMES.has(origin.hostname)) {
    throw new ProxyConfigError(
      `P6_CORE_ORIGIN must be loopback so Core stays off-network; got ${origin.hostname}`,
    )
  }
  if (origin.username || origin.password) {
    throw new ProxyConfigError('P6_CORE_ORIGIN must not embed credentials')
  }
  if (origin.pathname !== '/' || origin.search || origin.hash) {
    throw new ProxyConfigError('P6_CORE_ORIGIN must be an origin without a path')
  }
  return origin
}

export function isAllowedCoreRequest(method: string, pathname: string): boolean {
  const verb = method.toUpperCase()
  if (ALLOWED_METHODS.has(verb)) return ALLOWED_PATHS.some((pattern) => pattern.test(pathname))
  if (WRITE_METHODS.has(verb)) return WRITE_PATHS.some((pattern) => pattern.test(pathname))
  return false
}

/**
 * The correlation id a proxy-generated response must carry.
 *
 * A refusal that Core never saw has no Core `request_id`, so a reader cannot correlate it with
 * anything unless this hop supplies one. The incoming `X-Request-Id` is echoed when the client
 * sent it (so a browser-raised failure and its proxy refusal share one id); otherwise this hop
 * mints one rather than answering with no id at all.
 */
export function requestIdFor(req: IncomingMessage | undefined): string {
  const incoming = req?.headers['x-request-id']
  if (typeof incoming === 'string' && incoming.trim().length > 0) return incoming
  return `proxy-${randomUUID()}`
}

function refuse(
  res: ServerResponse,
  req: IncomingMessage | undefined,
  status: number,
  code: string,
  message: string,
): void {
  const body = JSON.stringify({ error: { code, message } })
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  // §5 change 3: the browser cannot name a proxy refusal unless this hop answers with an id,
  // because a proxy refusal has no Core envelope to read one from.
  res.setHeader('x-request-id', requestIdFor(req))
  res.end(body)
}

/** Forward one allowed request to Core. Never throws; failures become 502 JSON. */
export function proxyCoreRequest(
  req: IncomingMessage,
  res: ServerResponse,
  origin: URL,
): void {
  const method = req.method ?? 'GET'
  let target: URL
  try {
    target = new URL(req.url ?? '/', 'http://ui.invalid')
  } catch {
    refuse(res, req, 400, 'proxy_bad_target', 'unparsable request target')
    return
  }
  if (!isAllowedCoreRequest(method, target.pathname)) {
    refuse(
      res,
      req,
      404,
      'proxy_path_not_allowed',
      `the dashboard proxy does not forward ${method} ${target.pathname}`,
    )
    return
  }

  const headers: Record<string, string> = { host: origin.host }
  for (const name of FORWARDED_HEADERS) {
    const value = req.headers[name]
    if (typeof value === 'string' && value.length > 0) headers[name] = value
  }

  const upstream = http.request(
    {
      protocol: origin.protocol,
      hostname: origin.hostname,
      port: origin.port || 80,
      method,
      path: target.pathname + target.search,
      headers,
    },
    (reply) => {
      res.statusCode = reply.statusCode ?? 502
      for (const [name, value] of Object.entries(reply.headers)) {
        const lower = name.toLowerCase()
        // Hop-by-hop and length headers are recomputed by Node for this hop.
        if (lower === 'transfer-encoding' || lower === 'connection') continue
        if (value === undefined) continue
        res.setHeader(name, Array.isArray(value) ? value.join(', ') : value)
      }
      res.setHeader('cache-control', 'no-store')
      reply.pipe(res)
    },
  )
  upstream.on('error', (error: NodeJS.ErrnoException) => {
    if (res.headersSent) {
      res.destroy()
      return
    }
    refuse(
      res,
      req,
      502,
      'proxy_core_unreachable',
      `Core at ${origin.origin} did not answer: ${error.code ?? error.message}`,
    )
  })
  req.pipe(upstream)
}

/** Vite dev/preview middleware: same-origin `/v1/*` -> operator's loopback Core. */
export function coreProxyPlugin(origin: URL): Plugin {
  const middleware = (req: IncomingMessage, res: ServerResponse, next: () => void): void => {
    const target = classifyRequestTarget(req.url)
    if (target.kind === 'bad') {
      refuse(res, req, 400, 'proxy_bad_target', `refused request target: ${target.reason ?? 'malformed'}`)
      return
    }
    if (target.kind === 'static') {
      next()
      return
    }
    proxyCoreRequest(req, res, origin)
  }
  return {
    name: 'jasmine-core-proxy',
    configureServer(server) {
      server.middlewares.use(middleware)
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware)
    },
  }
}
