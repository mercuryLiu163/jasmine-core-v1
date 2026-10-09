/**
 * P6-03c: the *other* allowlist implementation gates the P7-01 reads identically.
 *
 * `core-proxy.ts` is the Vite-plugin host and `serve.mjs` is the static host. Both read
 * `P6_ENABLE_P7_READS` and both must agree, or the same dashboard would forward a path in
 * development and refuse it in production.
 *
 * The plugin reads the environment **once, at module load**, so each case resets the module
 * registry and imports it fresh — testing the import-time decision rather than an option
 * argument the real host never passes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

const RUN = 'run-20261008T101010Z'
const PENDING = [
  '/v1/validation/runs',
  `/v1/validation/runs/${RUN}`,
  `/v1/validation/runs/${RUN}/manifest`,
  `/v1/validation/runs/${RUN}/artifacts/art_stdout`,
  `/v1/validation/runs/${RUN}/artifacts/art_stdout/content`,
]

const ENV = 'P6_ENABLE_P7_READS'
const before = process.env[ENV]

afterEach(() => {
  if (before === undefined) delete process.env[ENV]
  else process.env[ENV] = before
  vi.resetModules()
})

async function allowedWith(env: string | undefined): Promise<(method: string, path: string) => boolean> {
  if (env === undefined) delete process.env[ENV]
  else process.env[ENV] = env
  vi.resetModules()
  const proxy = await import('../server/core-proxy')
  return (method: string, path: string) => proxy.isAllowedCoreRequest(method, path)
}

describe('the Vite proxy host gates the P7-01 reads like the static host', () => {
  // Whitespace and mixed case are included because the static host is tested with them and
  // the two hosts must accept the same set; dropping `.trim()` from this host's reader left
  // both suites green until the P4 pair of tests existed.
  it.each([undefined, '', ' ', '0', 'no', 'off', 'maybe'])('refuses all five when the flag is %j', async (env) => {
    const allowed = await allowedWith(env)
    for (const path of PENDING) expect(allowed('GET', path), `${env} ${path}`).toBe(false)
  })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes ', 'Yes'])('forwards all five when the flag is %j', async (env) => {
    const allowed = await allowedWith(env)
    for (const path of PENDING) expect(allowed('GET', path), `${env} ${path}`).toBe(true)
  })

  it('does not let the P7 switch turn on the P3 or P4 reads', async () => {
    const allowed = await allowedWith('1')
    expect(allowed('GET', '/v1/memory/items')).toBe(false)
    expect(allowed('GET', '/v1/checkpoints/ckp_01M4B6VGC29ESRDRQR62DX8AK8')).toBe(false)
  })

  it('refuses every malformed shape even with the switch on', async () => {
    const allowed = await allowedWith('1')
    for (const path of [
      '/v1/validation/runs/run-2026-10-08',
      '/v1/validation/runs/..',
      `/v1/validation/runs/${RUN}/artifacts/ART_STDOUT`,
      `/v1/validation/runs/${RUN}/artifacts/art_stdout/extra`,
      `/v1/validation/runs/${RUN}/extra`,
      '/v1/validation/runs/',
    ]) {
      expect(allowed('GET', path), path).toBe(false)
    }
  })

  it('never allows a write to the read slice', async () => {
    const allowed = await allowedWith('1')
    for (const path of PENDING) expect(allowed('POST', path), `POST ${path}`).toBe(false)
  })
})
