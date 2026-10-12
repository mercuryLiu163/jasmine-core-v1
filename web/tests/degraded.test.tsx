/**
 * Degraded and forbidden reads must not look like an empty database.
 *
 * The P6 negative cases require 401/403/404/409/422/503 to stay distinguishable
 * and require "unknown/degraded" never to be rendered as a zero count or an
 * empty list. These tests drive the real view components against a fake Core
 * that refuses, so the copy and the absence of a "0" are asserted together.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

async function signInAndOpen(
  user: ReturnType<typeof userEvent.setup>,
  routes: Parameters<typeof installFakeCore>[0],
): Promise<void> {
  fake = installFakeCore(routes)
  render(
    <SessionProvider>
      <App />
    </SessionProvider>,
  )
  await user.type(screen.getByLabelText('Core API token'), 'tok')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
}

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 6 }

describe('degraded Core', () => {
  it('shows a degraded message for 503 instead of an empty project list', async () => {
    const user = userEvent.setup()
    await signInAndOpen(user, {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/meta/schema': {
        body: { core_version: '0.1.0', schema_version: 6, expected_schema_version: 6, migrations: [] },
      },
      'GET /v1/hosts': { body: { hosts: [] } },
      'GET /v1/actors': { body: { actors: [] } },
      'GET /v1/projects': coreError(503, 'database_busy', 'the database is busy'),
    })

    const alerts = await screen.findAllByRole('alert')
    const text = alerts.map((node) => node.textContent ?? '').join('\n')
    expect(text).toContain('database_busy')
    expect(text).toContain('Core 暂时不可用')
    expect(text).toContain('这不是空集,也不是 0')
    // The empty-project wording must not appear for a refusal.
    expect(screen.queryByText(/空:Projects/)).toBeNull()
  })

  it('shows a forbidden message for 403 and does not retry with another credential', async () => {
    const user = userEvent.setup()
    await signInAndOpen(user, {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/meta/schema': {
        body: { core_version: '0.1.0', schema_version: 6, expected_schema_version: 6, migrations: [] },
      },
      'GET /v1/hosts': coreError(403, 'forbidden_scope', 'this key lacks objects:read'),
      'GET /v1/actors': { body: { actors: [] } },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
    })

    const alerts = await screen.findAllByRole('alert')
    const text = alerts.map((node) => node.textContent ?? '').join('\n')
    expect(text).toContain('forbidden_scope')
    expect(text).toContain('缺少 scope 或角色不允许')
    // Only the one token was used: no second, higher-privilege attempt.
    const authHeaders = new Set(fake?.requests.filter((request) => request.path.startsWith('/v1/')).map((request) => request.authorization))
    expect(authHeaders.size).toBe(1)
  })

  it('distinguishes an unreachable Core from an empty result', async () => {
    const user = userEvent.setup()
    await signInAndOpen(
      user,
      {
        'GET /v1/health': { body: HEALTH },
        'GET /v1/meta/schema': {
          body: { core_version: '0.1.0', schema_version: 6, expected_schema_version: 6, migrations: [] },
        },
        'GET /v1/hosts': { body: { hosts: [] } },
        'GET /v1/actors': { body: { actors: [] } },
        'GET /v1/projects': { body: { projects: [], count: 0 } },
      },
    )
    // The proxy answered the shell but Core is gone: the projects read must fail
    // loudly rather than render "0 projects".
    fake?.restore()
    fake = installFakeCore(
      {},
      (request) =>
        request.path === '/v1/projects' ? { networkError: 'connect ECONNREFUSED' } : undefined,
    )
    const projectsPanel = screen.getByRole('region', { name: /^Projects\(/ })
    await user.click(within(projectsPanel).getByRole('button', { name: '重新读取' }))

    await waitFor(() => {
      const text = projectsPanel.textContent ?? ''
      expect(text).toContain('无法连接 Core')
    })
    expect(projectsPanel.textContent ?? '').not.toContain('空:Projects')
  })
})
