/**
 * P6-03a: the *other* allowlist implementation gates the P3 reads identically.
 *
 * `core-proxy.ts` is the Vite-plugin host and `serve.mjs` is the static host. Both read
 * `P6_ENABLE_P3_READS` and both must agree, or the same dashboard would forward a path in
 * development and refuse it in production. Only `serve.mjs` had a test; this closes that.
 *
 * The plugin reads the environment **once, at module load**, so each case resets the
 * module registry and imports it fresh — testing the import-time decision rather than an
 * option argument the real host never passes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

const ID = '01M4B6VGC29ESRDRQR62DX8AK8'
const PENDING = [
  `/v1/context/ctx_${ID}`,
  `/v1/tasks/tsk_${ID}/checkpoints/latest`,
  `/v1/checkpoints/ckp_${ID}`,
  `/v1/resumes/rms_${ID}`,
]

const ENV = 'P6_ENABLE_P3_READS'
const before = process.env[ENV]

afterEach(() => {
  if (before === undefined) delete process.env[ENV]
  else process.env[ENV] = before
  vi.resetModules()
})

/** Import the plugin with `env` in place, and return its GET decision for a path. */
async function allowedWith(env: string | undefined): Promise<(path: string) => boolean> {
  if (env === undefined) delete process.env[ENV]
  else process.env[ENV] = env
  vi.resetModules()
  const proxy = await import('../server/core-proxy')
  return (path: string) => proxy.isAllowedCoreRequest('GET', path)
}

describe('the Vite proxy host gates the P3 reads like the static host', () => {
  it.each([undefined, '', '0', 'no', 'off'])(
    'refuses all four when the flag is %j',
    async (env) => {
      const allowed = await allowedWith(env)
      for (const path of PENDING) expect(allowed(path), `${env} ${path}`).toBe(false)
    },
  )

  it.each(['1', 'true', 'yes'])('forwards all four when the flag is %j', async (env) => {
    const allowed = await allowedWith(env)
    for (const path of PENDING) expect(allowed(path), `${env} ${path}`).toBe(true)
  })

  it('still refuses the build shapes, the key endpoint and audit when enabled', async () => {
    process.env[ENV] = '1'
    vi.resetModules()
    const proxy = await import('../server/core-proxy')
    expect(proxy.isAllowedCoreRequest('POST', '/v1/context/build')).toBe(false)
    expect(proxy.isAllowedCoreRequest('POST', '/v1/checkpoints')).toBe(false)
    expect(proxy.isAllowedCoreRequest('POST', `/v1/tasks/tsk_${ID}/resume`)).toBe(false)
    expect(proxy.isAllowedCoreRequest('GET', '/v1/auth/keys')).toBe(false)
    expect(proxy.isAllowedCoreRequest('GET', '/v1/audit')).toBe(false)
    // A path that merely looks like a P3 read is not one.
    expect(proxy.isAllowedCoreRequest('GET', '/v1/context/not-an-id')).toBe(false)
    expect(proxy.isAllowedCoreRequest('GET', `/v1/context/ctx_${ID}/extra`)).toBe(false)
  })
})
