/**
 * P6-03c: the P7-01 Validation reads are gated, not implicit.
 *
 * The five paths are real on the P7-01 branch and absent from this baseline's Core, so the
 * proxy must refuse them by default — forwarding one would send the browser to a route that
 * does not exist. They become live only when the operator states the Core behind the proxy
 * carries the read slice (`P6_ENABLE_P7_READS=1`).
 *
 * This exercises the real matcher (`loadAllowlist` + `isAllowed`) rather than the JSON,
 * because the JSON cannot prove what the running host does with it.
 */

import { describe, expect, it } from 'vitest'

import { join } from 'node:path'

import { P7_READS_ENV, loadAllowlist, isAllowed } from '../server/serve.mjs'

const ALLOWLIST_DIR = join(process.cwd(), 'server')
const load = (enableP7Reads: boolean) => loadAllowlist(ALLOWLIST_DIR, { enableP7Reads })

const RUN = 'run-20261008T101010Z'
const PENDING = [
  '/v1/validation/runs',
  `/v1/validation/runs/${RUN}`,
  `/v1/validation/runs/${RUN}/manifest`,
  `/v1/validation/runs/${RUN}/artifacts/art_stdout`,
  `/v1/validation/runs/${RUN}/artifacts/art_stdout/content`,
]

/** Shapes the allowlist must not match, whatever the switch says. */
const REFUSED_EVEN_WHEN_ENABLED = [
  // A run id that is not the recorder's grammar.
  '/v1/validation/runs/run-2026-10-08',
  '/v1/validation/runs/run-2026100T101010Z',
  '/v1/validation/runs/..',
  '/v1/validation/runs/../runs',
  // An artifact id outside `art_[a-z0-9][a-z0-9_.-]{0,63}`.
  `/v1/validation/runs/${RUN}/artifacts/ART_STDOUT`,
  `/v1/validation/runs/${RUN}/artifacts/art`,
  `/v1/validation/runs/${RUN}/artifacts/art_`,
  // Extra or missing segments.
  `/v1/validation/runs/${RUN}/artifacts/art_stdout/extra`,
  `/v1/validation/runs/${RUN}/artifacts/art_stdout/content/extra`,
  `/v1/validation/runs/${RUN}/extra`,
  '/v1/validation/runs/',
  // A write that does not exist on this slice.
  '/v1/validation/runs/record',
]

describe('P7-01 Validation reads are opt-in', () => {
  it('refuses all five on a baseline proxy', () => {
    const allowlist = load(false)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('forwards all five when the operator states the Core has the read slice', () => {
    const allowlist = load(true)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(true)
    }
  })

  it('leaves the P3 and P4 switches independent', () => {
    // Three unmerged dependencies, three decisions: enabling Validation must not enable
    // Memory or continuity, and enabling those must not enable Validation.
    const p7Only = loadAllowlist(ALLOWLIST_DIR, {
      enableP3Reads: false, enableP4Reads: false, enableP7Reads: true,
    })
    expect(isAllowed(p7Only, 'GET', '/v1/validation/runs')).toBe(true)
    expect(isAllowed(p7Only, 'GET', '/v1/memory/items')).toBe(false)
    expect(isAllowed(p7Only, 'GET', '/v1/checkpoints/ckp_01M4B6VGC29ESRDRQR62DX8AK8')).toBe(false)

    const p4Only = loadAllowlist(ALLOWLIST_DIR, {
      enableP3Reads: false, enableP4Reads: true, enableP7Reads: false,
    })
    expect(isAllowed(p4Only, 'GET', '/v1/memory/items')).toBe(true)
    expect(isAllowed(p4Only, 'GET', '/v1/validation/runs')).toBe(false)
  })

  it('never carries the pending paths in the baseline list itself', () => {
    const allowlist = load(false)
    const live = allowlist.paths.map((pattern: RegExp) => pattern.source)
    for (const pending of allowlist.p7_pending_paths.map((p: RegExp) => p.source)) {
      expect(live).not.toContain(pending)
    }
  })

  it('refuses every malformed shape even with the switch on', () => {
    const allowlist = load(true)
    for (const path of REFUSED_EVEN_WHEN_ENABLED) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('matches on the pathname, so the content query does not change the decision', () => {
    // The proxy is given the pathname; `?form=masked` is Core's business, not the allowlist's.
    const allowlist = load(true)
    expect(isAllowed(allowlist, 'GET', `/v1/validation/runs/${RUN}/artifacts/art_stdout/content`))
      .toBe(true)
  })

  it('never opens the Validation slice to a write method', () => {
    // The slice is read-only, so nothing was added to write_paths: a POST to any of these
    // paths is refused even with the read switch on.
    const allowlist = load(true)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'POST', path), `POST ${path}`).toBe(false)
    }
  })

  it('defaults to off, so an unconfigured host is the safe one', () => {
    const before = process.env[P7_READS_ENV]
    try {
      delete process.env[P7_READS_ENV]
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p7_reads_enabled).toBe(false)
      expect(isAllowed(allowlist, 'GET', '/v1/validation/runs')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P7_READS_ENV]
      else process.env[P7_READS_ENV] = before
    }
  })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes '])('reads %j as enabled', (value) => {
    const before = process.env[P7_READS_ENV]
    try {
      process.env[P7_READS_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p7_reads_enabled).toBe(true)
      expect(isAllowed(allowlist, 'GET', '/v1/validation/runs')).toBe(true)
    } finally {
      if (before === undefined) delete process.env[P7_READS_ENV]
      else process.env[P7_READS_ENV] = before
    }
  })

  it.each(['', ' ', '0', 'no', 'off', 'maybe'])('reads %j as disabled', (value) => {
    const before = process.env[P7_READS_ENV]
    try {
      process.env[P7_READS_ENV] = value
      const allowlist = loadAllowlist(ALLOWLIST_DIR)
      expect(allowlist.p7_reads_enabled).toBe(false)
      expect(isAllowed(allowlist, 'GET', '/v1/validation/runs')).toBe(false)
    } finally {
      if (before === undefined) delete process.env[P7_READS_ENV]
      else process.env[P7_READS_ENV] = before
    }
  })

  it('names its switch P6_ENABLE_P7_READS, matching the other hosts', () => {
    expect(P7_READS_ENV).toBe('P6_ENABLE_P7_READS')
  })
})
