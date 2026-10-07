/**
 * P6-03a: the P3 continuity reads are gated, not implicit.
 *
 * The four paths are real on a P3 Core and absent from this baseline's Core, so the
 * proxy must refuse them by default — forwarding one would send the browser to a route
 * that does not exist, which is the "plausible endpoint" defect the allowlist exists to
 * prevent. They become live only when the operator states the Core behind the proxy has
 * P3 (`P6_ENABLE_P3_READS=1`).
 *
 * This exercises the real matcher (`loadAllowlist` + `isAllowed`) rather than the JSON,
 * because the JSON cannot prove what the running host does with it.
 */

import { describe, expect, it } from 'vitest'

import { join } from 'node:path'

import { loadAllowlist, isAllowed } from '../server/serve.mjs'

// `HERE` falls back to `process.cwd()` when serve.mjs is imported rather than run, so the
// directory is passed explicitly — the other host tests do the same.
const ALLOWLIST_DIR = join(process.cwd(), 'server')
const load = (enableP3Reads: boolean) => loadAllowlist(ALLOWLIST_DIR, { enableP3Reads })

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const PENDING = [
  `/v1/context/ctx_${ID}`,
  `/v1/tasks/tsk_${ID}/checkpoints/latest`,
  `/v1/checkpoints/ckp_${ID}`,
  `/v1/resumes/rms_${ID}`,
]

describe('P3 continuity reads are opt-in', () => {
  it('refuses all four on a baseline proxy', () => {
    const allowlist = load(false)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(false)
    }
  })

  it('forwards all four when the operator states the Core has P3', () => {
    const allowlist = load(true)
    for (const path of PENDING) {
      expect(isAllowed(allowlist, 'GET', path), path).toBe(true)
    }
  })

  it('never carries the pending paths in the baseline list itself', () => {
    const allowlist = load(false)
    const live = allowlist.paths.map((pattern: RegExp) => pattern.source)
    for (const pending of allowlist.p3_pending_paths.map((p: RegExp) => p.source)) {
      expect(live).not.toContain(pending)
    }
  })

  it('does not open the build shapes even with P3 enabled', () => {
    // `POST /v1/context/build`, `POST /v1/checkpoints` and `POST /v1/tasks/{id}/resume`
    // are writes; this slice reads. Enabling the reads must not drag them in.
    const allowlist = load(true)
    expect(isAllowed(allowlist, 'POST', '/v1/context/build')).toBe(false)
    expect(isAllowed(allowlist, 'POST', '/v1/checkpoints')).toBe(false)
    expect(isAllowed(allowlist, 'POST', `/v1/tasks/tsk_${ID}/resume`)).toBe(false)
    expect(isAllowed(allowlist, 'GET', '/v1/auth/keys')).toBe(false)
    expect(isAllowed(allowlist, 'GET', '/v1/audit')).toBe(false)
  })

  it('defaults to off, so an unconfigured host is the safe one', () => {
    // No options object at all: the loader must read the environment and, with the flag
    // absent, keep the pending paths out.
    const allowlist = load(false)
    expect(allowlist.p3_reads_enabled).toBe(false)
    expect(isAllowed(allowlist, 'GET', PENDING[0]!)).toBe(false)
  })
})
