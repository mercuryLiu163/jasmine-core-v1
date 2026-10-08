/**
 * P6-03b2: the *other* allowlist implementation gates the P4 writes identically.
 *
 * `core-proxy.ts` is the Vite-plugin host and `serve.mjs` is the static host. Both read
 * `P6_ENABLE_P4_WRITES` and both must agree, or the same dashboard would forward a write in
 * development and refuse it in production — the asymmetry that a test for only one host hid
 * once already (it was found for the P3 reads).
 *
 * The plugin reads the environment **once, at module load**, so each case resets the module
 * registry and imports it fresh — testing the import-time decision rather than an option
 * argument the real host never passes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const WRITES = [
  `/v1/memory/jobs/mjb_${ID}/retry`,
  `/v1/memory/items/mem_${ID}/obsolete`,
  `/v1/memory/items/mem_${ID}/delete`,
  `/v1/memory/items/mem_${ID}/pin`,
  `/v1/memory/items/mem_${ID}/unpin`,
  '/v1/memory/reflect',
] as const

const ENV = 'P6_ENABLE_P4_WRITES'
const READ_ENV = 'P6_ENABLE_P4_READS'
const before = process.env[ENV]
const beforeReads = process.env[READ_ENV]

afterEach(() => {
  if (before === undefined) delete process.env[ENV]
  else process.env[ENV] = before
  if (beforeReads === undefined) delete process.env[READ_ENV]
  else process.env[READ_ENV] = beforeReads
  vi.resetModules()
})

async function proxyWith(writes: string | undefined, reads?: string) {
  if (writes === undefined) delete process.env[ENV]
  else process.env[ENV] = writes
  if (reads === undefined) delete process.env[READ_ENV]
  else process.env[READ_ENV] = reads
  vi.resetModules()
  return import('../server/core-proxy')
}

describe('the Vite proxy host gates the P4 writes like the static host', () => {
  // Whitespace and mixed case are included because the static host is tested with them and
  // the two readers must accept the same set; dropping `.trim()` from one host's reader left
  // both suites green until these cases existed.
  it.each([undefined, '', ' ', '0', 'no', 'off', 'maybe'])(
    'refuses all six when the flag is %j', async (env) => {
      const proxy = await proxyWith(env)
      for (const path of WRITES) {
        expect(proxy.isAllowedCoreRequest('POST', path), `${String(env)} ${path}`).toBe(false)
      }
    })

  it.each(['1', 'true', 'yes', 'TRUE', ' yes ', 'Yes'])(
    'forwards all six when the flag is %j', async (env) => {
      const proxy = await proxyWith(env)
      for (const path of WRITES) {
        expect(proxy.isAllowedCoreRequest('POST', path), `${String(env)} ${path}`).toBe(true)
      }
    })

  it('does not follow the reads switch', async () => {
    const proxy = await proxyWith(undefined, '1')
    for (const path of WRITES) {
      expect(proxy.isAllowedCoreRequest('POST', path), path).toBe(false)
    }
    expect(proxy.isAllowedCoreRequest('GET', '/v1/memory/items')).toBe(true)
  })

  it('does not open the reads when only the writes are enabled', async () => {
    const proxy = await proxyWith('1', undefined)
    expect(proxy.isAllowedCoreRequest('GET', '/v1/memory/items')).toBe(false)
    expect(proxy.isAllowedCoreRequest('GET', `/v1/memory/items/mem_${ID}`)).toBe(false)
  })

  it('leaves the P6-02 milestone B writes and the reads reachable as before', async () => {
    const proxy = await proxyWith('1')
    // The always-on milestone B surface is unaffected by the P4 switch.
    expect(proxy.isAllowedCoreRequest('POST', `/v1/rules/rul_${ID}/retire`)).toBe(true)
    expect(proxy.isAllowedCoreRequest('POST', `/v1/interpretations/int_${ID}/correct`)).toBe(true)
    // And a write path is still unreachable with GET.
    for (const path of WRITES) {
      expect(proxy.isAllowedCoreRequest('GET', path), path).toBe(false)
    }
    // A malformed id is not a P4 write.
    expect(proxy.isAllowedCoreRequest('POST', '/v1/memory/items/mem_short/pin')).toBe(false)
    expect(proxy.isAllowedCoreRequest('POST', `/v1/memory/items/mem_${ID}/extra`)).toBe(false)
  })
})
