/**
 * P6-03b2: the P4 memory writes are gated by their own switch, not by the reads'.
 *
 * Retry, the four lifecycle commands and `reflect` are real at the pinned P4 commit and
 * absent from this baseline's Core, so the proxy must refuse them by default — forwarding one
 * would send the browser to a route that does not exist, and the five `memory:manage`
 * commands would be a state change attempted against nothing.
 *
 * Two properties matter and are checked here rather than asserted in prose:
 *
 * 1. The writes are **not** in `write_paths`. That list is the always-on P6-02 milestone B
 *    surface; an unmerged dependency must not ride along in it.
 * 2. The two P4 switches are independent in **both** directions. An operator who has the
 *    reads has not agreed to let the browser change Memory, and an operator who has the
 *    writes is not thereby reading it.
 *
 * These call the real matcher (`loadAllowlist` + `isAllowed`), because the JSON cannot prove
 * what the running host does with it.
 */

import { describe, expect, it } from 'vitest'

import { join } from 'node:path'

import { P4_WRITES_ENV, isAllowed, loadAllowlist } from '../server/serve.mjs'
import contract from '../contracts/p4-memory-write.v1.json'

const ALLOWLIST_DIR = join(process.cwd(), 'server')
const ID = '01M4B6VGC29ESRDRQR62DX8AK8'

const load = (options: Record<string, boolean>) => loadAllowlist(ALLOWLIST_DIR, options)

/** One literal path per frozen contract route, in the contract's own order. */
const WRITES = [
  ['POST', `/v1/memory/jobs/mjb_${ID}/retry`],
  ['POST', `/v1/memory/items/mem_${ID}/obsolete`],
  ['POST', `/v1/memory/items/mem_${ID}/delete`],
  ['POST', `/v1/memory/items/mem_${ID}/pin`],
  ['POST', `/v1/memory/items/mem_${ID}/unpin`],
  ['POST', '/v1/memory/reflect'],
] as const

describe('P4 memory writes are opt-in, separately from the reads', () => {
  it('refuses all six on a baseline proxy', () => {
    const allowlist = load({ enableP4Reads: false, enableP4Writes: false })
    for (const [method, path] of WRITES) {
      expect(isAllowed(allowlist, method, path), `${method} ${path}`).toBe(false)
    }
  })

  it('refuses all six when only the reads are enabled', () => {
    const allowlist = load({ enableP4Reads: true, enableP4Writes: false })
    for (const [method, path] of WRITES) {
      expect(isAllowed(allowlist, method, path), `${method} ${path}`).toBe(false)
    }
    expect(isAllowed(allowlist, 'GET', '/v1/memory/items')).toBe(true)
  })

  it('forwards all six when the operator states the Core has the P4 writes', () => {
    const allowlist = load({ enableP4Reads: false, enableP4Writes: true })
    for (const [method, path] of WRITES) {
      expect(isAllowed(allowlist, method, path), `${method} ${path}`).toBe(true)
    }
  })

  it('does not open the reads when only the writes are enabled', () => {
    // The independence must hold both ways: these are two decisions with two scopes.
    const allowlist = load({ enableP4Reads: false, enableP4Writes: true })
    for (const path of ['/v1/memory/health', '/v1/memory/jobs', `/v1/memory/jobs/mjb_${ID}`,
                        '/v1/memory/items', `/v1/memory/items/mem_${ID}`, '/v1/memory/traces',
                        `/v1/memory/traces/mtr_${ID}`]) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('never carries the writes in the always-on write list or the read list', () => {
    const allowlist = load({ enableP4Reads: true, enableP4Writes: true })
    const alwaysOn = allowlist.baseline_write_paths.map((pattern: string) => new RegExp(pattern).source)
    const reads = allowlist.paths.map((pattern: RegExp) => pattern.source)
    for (const pattern of allowlist.p4_write_paths.map((p: RegExp) => p.source)) {
      expect(alwaysOn).not.toContain(pattern)
      expect(reads).not.toContain(pattern)
    }
    // The always-on list is now milestone B's four families plus the seven P6-02b Task/Step
    // state routes. The P4 writes must still be absent from it and from the read list.
    expect(allowlist.baseline_write_paths).toHaveLength(11)
    expect(allowlist.paths).toHaveLength(25 + 7)
  })

  it('is not reachable with GET even when the writes are enabled', () => {
    // `isAllowed` picks a list by method, so a POST-only pattern can never answer a GET.
    const allowlist = load({ enableP4Writes: true })
    for (const path of ['/v1/memory/reflect', `/v1/memory/jobs/mjb_${ID}/retry`,
                        `/v1/memory/items/mem_${ID}/pin`]) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('covers exactly the six routes the frozen contract records', () => {
    // Derived from the contract rather than hand-listed, so a seventh write in the contract
    // without a proxy pattern fails here. The contract's patterns are loose (`([^/]+)`); the
    // proxy's are Core's real id shape, so the comparison is behavioural: build a literal
    // path from each contract route and require the proxy to forward it.
    const allowlist = load({ enableP4Writes: true })
    expect(contract.writes).toHaveLength(6)
    for (const write of contract.writes) {
      const prefix = write.id_prefix ?? ''
      const path = write.path_pattern
        .replace(/^\^/, '')
        .replace(/\$$/, '')
        .replace(/\(\[\^\/\]\+\)/g, prefix ? `${prefix}_${ID}` : ID)
      expect(isAllowed(allowlist, 'POST', path), path).toBe(true)
    }
  })

  it('does not forward a malformed memory id', () => {
    const allowlist = load({ enableP4Writes: true })
    for (const path of ['/v1/memory/items/mem_short/pin', '/v1/memory/items/not-an-id/delete',
                        `/v1/memory/jobs/mjb_${ID}`, '/v1/memory/items/mem_x/obsolete/extra']) {
      expect(isAllowed(allowlist, 'POST', path), path).toBe(false)
    }
  })

  it('defaults to off, so an unconfigured host is the safe one', () => {
    const before = process.env[P4_WRITES_ENV]
    try {
      delete process.env[P4_WRITES_ENV]
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_writes_enabled).toBe(false)
      expect(isAllowed(allowlist, 'POST', '/v1/memory/reflect')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P4_WRITES_ENV]
      else process.env[P4_WRITES_ENV] = before
    }
  })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes '])('reads %j as enabled', (value) => {
    const before = process.env[P4_WRITES_ENV]
    try {
      process.env[P4_WRITES_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_writes_enabled).toBe(true)
      expect(isAllowed(allowlist, 'POST', '/v1/memory/reflect')).toBe(true)
    } finally {
      if (before === undefined) delete process.env[P4_WRITES_ENV]
      else process.env[P4_WRITES_ENV] = before
    }
  })

  it.each(['', '0', 'no', 'off', 'maybe'])('reads %j as disabled', (value) => {
    const before = process.env[P4_WRITES_ENV]
    try {
      process.env[P4_WRITES_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_writes_enabled).toBe(false)
      expect(isAllowed(allowlist, 'POST', '/v1/memory/reflect')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P4_WRITES_ENV]
      else process.env[P4_WRITES_ENV] = before
    }
  })
})
