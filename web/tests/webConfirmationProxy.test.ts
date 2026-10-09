import http, { type Server } from 'node:http'
import { once } from 'node:events'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDashboardServer, isAllowed, loadAllowlist, webConfirmationsEnabled } from '../server/serve.mjs'
const directory = resolve(process.cwd(), 'server')
const evt = `evt_${'0'.repeat(26)}`
const pairs: [string,string][] = [['GET','/v1/auth/me'],['GET',`/v1/evidence/confirmations/${evt}`],['POST','/v1/evidence/confirmations/prepare'],['POST','/v1/evidence/confirmations/submit']]
const servers: Server[]=[]
async function listen(server: Server): Promise<number> { servers.push(server);server.listen(0,'127.0.0.1');await once(server,'listening');return (server.address() as {port:number}).port }
afterEach(async () => { delete process.env.P6_ENABLE_WEB_CONFIRMATIONS;vi.resetModules();await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve())))) })
describe('Web confirmation proxy contract, real sockets on both hosts', () => {
  it('default-off, exact opt-in parity, strict evt ID and no old/raw/admin/scan routes', async () => {
    for (const enabled of [false,true]) {
      process.env.P6_ENABLE_WEB_CONFIRMATIONS = enabled ? '1':'0';vi.resetModules()
      const dev = await import('../server/core-proxy')
      const standalone = loadAllowlist(directory,{enableWebConfirmations:enabled})
      for (const [method,path] of pairs) { expect(dev.isAllowedCoreRequest(method,path)).toBe(enabled);expect(isAllowed(standalone,method,path)).toBe(enabled) }
      for (const [method,path] of [...pairs.map(([method,path]):[string,string] => [method==='GET'?'POST':'GET',path]), ['POST','/v1/evidence/confirm'],['POST','/v1/events'],['POST','/v1/tool-results'],['POST','/v1/workspaces/fingerprint'],['GET','/v1/auth/keys'],['GET','/v1/audit'],['GET','/v1/auth/else'],['GET',`/v1/evidence/confirmations/tsk_${'0'.repeat(26)}`],['POST','/v1/evidence/confirmations/submit/']] as [string,string][]) {
        expect(dev.isAllowedCoreRequest(method,path),`${method} ${path}`).toBe(false);expect(isAllowed(standalone,method,path),`${method} ${path}`).toBe(false)
      }
    }
    for (const value of ['', '0','false','no']) expect(webConfirmationsEnabled({P6_ENABLE_WEB_CONFIRMATIONS:value})).toBe(false)
    for (const value of ['1','true','yes']) expect(webConfirmationsEnabled({P6_ENABLE_WEB_CONFIRMATIONS:value})).toBe(true)
  })
  it('rejects hostile/null Origin before upstream; same-origin forwards exact JSON and correlation, never cookies', async () => {
    const received: {headers:http.IncomingHttpHeaders;body:string}[]=[]
    const corePort = await listen(http.createServer((req,res) => { let body='';req.on('data', chunk => body+=chunk);req.on('end',() => { received.push({headers:req.headers,body});res.setHeader('content-type','application/json');res.end('{"accepted":true}') }) }))
    process.env.P6_ENABLE_WEB_CONFIRMATIONS='1';vi.resetModules();const dev = await import('../server/core-proxy')
    const origin = new URL(`http://127.0.0.1:${corePort}`)
    const hosts = [http.createServer((req,res) => dev.proxyCoreRequest(req,res,origin)),createDashboardServer({root:directory,origin,allowlist:loadAllowlist(directory,{enableWebConfirmations:true})})]
    for (const host of hosts) {
      const port = await listen(host)
      for (const hostile of ['http://evil.invalid','null',`http://127.0.0.1:${port+1}`]) {
        const response = await fetch(`http://127.0.0.1:${port}/v1/evidence/confirmations/submit`,{method:'POST',headers:{origin:hostile,'content-type':'application/json'},body:'{}'})
        expect(response.status).toBe(403);expect((await response.json() as {error:{code:string}}).error.code).toBe('proxy_origin_not_allowed');expect(response.headers.get('x-request-id')).toMatch(/^proxy-/)
      }
      expect(received).toHaveLength(host===hosts[0]?0:1)
      const response = await fetch(`http://127.0.0.1:${port}/v1/evidence/confirmations/submit`,{method:'POST',headers:{origin:`http://127.0.0.1:${port}`,'content-type':'application/json',cookie:'session=should-not-forward',authorization:'Bearer synthetic','x-request-id':'ui-correlation'},body:'{"note":"你好"}'})
      expect(response.status).toBe(200);const forwarded=received.at(-1)!;expect(forwarded.body).toBe('{"note":"你好"}');expect(forwarded.headers.cookie).toBeUndefined();expect(forwarded.headers.origin).toBeUndefined();expect(forwarded.headers.authorization).toBe('Bearer synthetic');expect(forwarded.headers['x-request-id']).toBe('ui-correlation')
    }
  })
})
