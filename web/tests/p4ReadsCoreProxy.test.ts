/**
 * P6-03b1: the *other* allowlist implementation gates the P4 reads identically.
 *
 * `core-proxy.ts` is the Vite-plugin host and `serve.mjs` is the static host. Both read
 * `P6_ENABLE_P4_READS` and both must agree, or the same dashboard would forward a path in
 * development and refuse it in production.
 *
 * The plugin reads the environment **once, at module load**, so each case resets the module
 * registry and imports it fresh — testing the import-time decision rather than an option
 * argument the real host never passes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

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

const ENV = 'P6_ENABLE_P4_READS'
const before = process.env[ENV]

afterEach(() => {
  if (before === undefined) delete process.env[ENV]
  else process.env[ENV] = before
  vi.resetModules()
})

async function allowedWith(env: string | undefined): Promise<(path: string) => boolean> {
  if (env === undefined) delete process.env[ENV]
  else process.env[ENV] = env
  vi.resetModules()
  const proxy = await import('../server/core-proxy')
  return (path: string) => proxy.isAllowedCoreRequest('GET', path)
}

describe('the Vite proxy host gates the P4 reads like the static host', () => {
  // The values include whitespace and mixed case because the static host is tested with
  // them and the two hosts must accept the same set; dropping `.trim()` from this host's
  // reader left both suites green until these cases existed.
  it.each([undefined, '', ' ', '0', 'no', 'off', 'maybe'])('refuses all seven when the flag is %j', async (env) => {
    const allowed = await allowedWith(env)
    for (const path of PENDING) expect(allowed(path), `${env} ${path}`).toBe(false)
  })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes ', 'Yes'])('forwards all seven when the flag is %j', async (env) => {
    const allowed = await allowedWith(env)
    for (const path of PENDING) expect(allowed(path), `${env} ${path}`).toBe(true)
  })

  it('still refuses the P6-03b2 writes, and the P3 reads, when P4 is enabled', async () => {
    process.env[ENV] = '1'
    vi.resetModules()
    const proxy = await import('../server/core-proxy')
    for (const path of ['/v1/memory/reflect', `/v1/memory/jobs/mjb_${ID}/retry`,
                        `/v1/memory/items/mem_${ID}/obsolete`,
                        `/v1/memory/items/mem_${ID}/delete`,
                        `/v1/memory/items/mem_${ID}/pin`,
                        `/v1/memory/items/mem_${ID}/unpin`]) {
      expect(proxy.isAllowedCoreRequest('POST', path), path).toBe(false)
    }
    // P4 enabled does not imply P3 enabled.
    expect(proxy.isAllowedCoreRequest('GET', `/v1/checkpoints/ckp_${ID}`)).toBe(false)
    // A path that merely looks like a memory read is not one.
    expect(proxy.isAllowedCoreRequest('GET', '/v1/memory/items/not-an-id')).toBe(false)
    expect(proxy.isAllowedCoreRequest('GET', `/v1/memory/items/mem_${ID}/extra`)).toBe(false)
  })
})
