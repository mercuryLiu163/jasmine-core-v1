/**
 * P6-03b1: the P4 memory reads are gated, not implicit.
 *
 * The seven paths are real at the pinned P4 commit and absent from this baseline's Core, so
 * the proxy must refuse them by default — forwarding one would send the browser to a route
 * that does not exist. They become live only when the operator states the Core behind the
 * proxy has P4 (`P6_ENABLE_P4_READS=1`).
 *
 * This exercises the real matcher (`loadAllowlist` + `isAllowed`) rather than the JSON,
 * because the JSON cannot prove what the running host does with it.
 */

import { describe, expect, it } from 'vitest'

import { join } from 'node:path'

import { P4_READS_ENV, loadAllowlist, isAllowed } from '../server/serve.mjs'

const ALLOWLIST_DIR = join(process.cwd(), 'server')
const load = (enableP4Reads: boolean) => loadAllowlist(ALLOWLIST_DIR, { enableP4Reads })

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const PENDING = [
  '/v1/memory/health',
  '/v1/memory/jobs',
  `/v1/memory/jobs/mjb_${ID}`,
  '/v1/memory/items',
  `/v1/memory/items/mem_${ID}`,
  '/v1/memory/traces',
  `/v1/memory/traces/mtr_${ID}`,
]

const WRITES = [
  ['POST', '/v1/memory/reflect'],
  ['POST', `/v1/memory/jobs/mjb_${ID}/retry`],
  ['POST', `/v1/memory/items/mem_${ID}/obsolete`],
  ['POST', `/v1/memory/items/mem_${ID}/delete`],
  ['POST', `/v1/memory/items/mem_${ID}/pin`],
  ['POST', `/v1/memory/items/mem_${ID}/unpin`],
] as const

describe('P4 memory reads are opt-in', () => {
  it('refuses all seven on a baseline proxy', () => {
    const allowlist = load(false)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('forwards all seven when the operator states the Core has P4', () => {
    const allowlist = load(true)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(true)
    }
  })

  it('leaves the P6-03a switch independent', () => {
    // Enabling P4 must not enable P3, and vice versa: two unmerged dependencies, two
    // decisions.
    const p4Only = loadAllowlist(ALLOWLIST_DIR, { enableP3Reads: false, enableP4Reads: true })
    expect(isAllowed(p4Only, 'GET', `/v1/checkpoints/ckp_${ID}`)).toBe(false)
    expect(isAllowed(p4Only, 'GET', '/v1/memory/items')).toBe(true)
    const p3Only = loadAllowlist(ALLOWLIST_DIR, { enableP3Reads: true, enableP4Reads: false })
    expect(isAllowed(p3Only, 'GET', '/v1/memory/items')).toBe(false)
    expect(isAllowed(p3Only, 'GET', `/v1/checkpoints/ckp_${ID}`)).toBe(true)
  })

  it('never carries the pending paths in the baseline list itself', () => {
    const allowlist = load(false)
    const live = allowlist.paths.map((pattern: RegExp) => pattern.source)
    for (const pending of allowlist.p4_pending_paths.map((p: RegExp) => p.source)) {
      expect(live).not.toContain(pending)
    }
    expect(allowlist.paths).toHaveLength(25)
  })

  it('does not open the P6-03b2 writes even with P4 enabled', () => {
    // Retry, the lifecycle commands and reflect are writes with their own permission and
    // audit Gate; enabling the reads must not drag them in.
    const allowlist = load(true)
    for (const [method, path] of WRITES) {
      expect(isAllowed(allowlist, method, path), `${method} ${path}`).toBe(false)
    }
  })

  it('defaults to off, so an unconfigured host is the safe one', () => {
    const before = process.env[P4_READS_ENV]
    try {
      delete process.env[P4_READS_ENV]
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_reads_enabled).toBe(false)
      expect(isAllowed(allowlist, 'GET', '/v1/memory/items')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P4_READS_ENV]
      else process.env[P4_READS_ENV] = before
    }
  })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes '])('reads %j as enabled', (value) => {
    const before = process.env[P4_READS_ENV]
    try {
      process.env[P4_READS_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_reads_enabled).toBe(true)
      expect(isAllowed(allowlist, 'GET', '/v1/memory/items')).toBe(true)
    } finally {
      if (before === undefined) delete process.env[P4_READS_ENV]
      else process.env[P4_READS_ENV] = before
    }
  })

  it.each(['', '0', 'no', 'off', 'maybe'])('reads %j as disabled', (value) => {
    const before = process.env[P4_READS_ENV]
    try {
      process.env[P4_READS_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p4_reads_enabled).toBe(false)
      expect(isAllowed(allowlist, 'GET', '/v1/memory/items')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P4_READS_ENV]
      else process.env[P4_READS_ENV] = before
    }
  })
})
