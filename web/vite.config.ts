import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { coreProxyPlugin, resolveCoreOrigin } from './server/core-proxy'

/**
 * P6-A02: the browser only ever talks to its own origin.
 *
 * In development Vite proxies `/v1/*` to the operator-configured loopback Core
 * (default `http://127.0.0.1:8787`). The proxy is limited to the frozen Core
 * path allowlist in `server/core-proxy.ts`; it never follows a target taken
 * from the request, and it never adds a token. Core keeps its loopback bind and
 * no wildcard CORS is opened.
 */
const coreOrigin = resolveCoreOrigin(process.env.P6_CORE_ORIGIN)
const port = Number.parseInt(process.env.P6_UI_PORT ?? '5173', 10)
const host = process.env.P6_UI_HOST ?? '127.0.0.1'

export default defineConfig({
  plugins: [react(), coreProxyPlugin(coreOrigin)],
  server: {
    host,
    port: Number.isFinite(port) ? port : 5173,
    strictPort: true,
  },
  preview: {
    host,
    port: Number.isFinite(port) ? port : 5173,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/setup.ts'],
    globals: false,
    restoreMocks: true,
  },
})
