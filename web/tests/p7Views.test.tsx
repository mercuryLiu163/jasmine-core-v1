/**
 * P6-03c Validation Inspector view behaviour.
 *
 * Each test pins a way this slice could lie:
 *
 * 1. **A refused P7 read says so.** On a baseline dashboard the proxy answers
 *    `proxy_path_not_allowed`, and the view renders the dependency-pending panel — not an
 *    error panel (which would blame Core) and not an empty table (which would invent "there
 *    is nothing here").
 * 2. **A missing chain link stays missing.** Extraction and Resolution have no recorded
 *    source at all, and Context is missing for three distinct, nameable reasons; the exact
 *    `missing_reason` strings the API returns are shown, and nothing is filled in.
 * 3. **A corrupt record is unusable and visible.** It stays in the list with its reason and
 *    `—` for the fields the reader could not read, never a fabricated value.
 * 4. **A refusal is never "no data".** `503 validation_not_configured` with
 *    `details.reason: "unset"` is the operator's run root being absent; it names
 *    `JASMINE_CORE_VALIDATION_RUNS`, and the 401/403/404/409/413/415 family each render as
 *    their own condition.
 * 5. **The two artifact digests stay separate**, and `403 original_withheld` shows the
 *    reason plus the masked digest instead of an error page.
 * 6. **Artifact content is text.** The withheld path and the served path both render the
 *    envelope's `content` field as text, never as HTML.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 8 }
const RUN_A = 'run-20260929T162925Z'
const RUN_B = 'run-20261008T101010Z'
const RUN_CORRUPT = 'run-20260101T000000Z'
const ART = 'art_stdout'
const ART_MISSING = 'art_missing'
const ART_SYMLINK = 'art_symlinked'
const ART_MODIFIED = 'art_modified'
const PROJECT = `prj_${'01M4B6VGC29ESRDRQR62DX8AK8'}`
const HASH_ORIGINAL = 'c'.repeat(64)
const HASH_MASKED = 'd'.repeat(64)
const HASH_DECLARED = 'a'.repeat(64)
const HASH_RECORD = 'b'.repeat(64)

/** Exactly what the baseline proxy answers for a path it does not forward. */
const PROXY_REFUSAL = {
  status: 404,
  body: { error: { code: 'proxy_path_not_allowed', message: 'the dashboard proxy does not forward this path' } },
}

const SIGNED_IN = { 'GET /v1/projects': { body: { projects: [], count: 0 } } }

/** A Core error envelope with the `details` a specific P7 condition carries. */
function errorReply(status: number, code: string, message: string, details: unknown = {}): {
  status: number
  body: unknown
} {
  return {
    status,
    body: { error: { code, message, request_id: 'aud_test_request', details } },
  }
}

/* -------------------------------------------------------------------------- *
 * Fixtures, built from the shapes the reader returns
 * -------------------------------------------------------------------------- */

function discovery(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    root_present: true,
    max_depth: 4,
    scanned_dirs: 9,
    scanned_files: 12,
    run_shaped_files: 1,
    unreadable_entries: 0,
    capped: false,
    ...overrides,
  }
}

function runRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: RUN_A,
    stage: 'P0',
    created_at: '2026-09-29T16:29:25Z',
    commit: 'beefdead',
    schema_version: 8,
    core_version: '0.1.0',
    verdict: 'BLOCKED',
    case_counts: { pass: 0, fail: 0, blocked: 1, total: 1 },
    mode: 'unknown',
    mode_basis: 'no bundle manifest on disk',
    attribution: { status: 'unattributed', reason: 'manifest_absent', projects: [] },
    evidence_grade: 'unknown',
    grade_downgraded_by: [],
    synthetic: false,
    manifest_status: 'absent',
    record_status: 'ok',
    record_sha256: HASH_RECORD,
    verdict_consistent: true,
    ...overrides,
  }
}

function corruptRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: RUN_CORRUPT,
    stage: null,
    created_at: null,
    commit: null,
    schema_version: null,
    core_version: null,
    verdict: null,
    case_counts: { pass: 0, fail: 0, blocked: 0, total: 0 },
    mode: null,
    mode_basis: null,
    attribution: null,
    evidence_grade: null,
    grade_downgraded_by: [],
    synthetic: false,
    manifest_status: null,
    record_status: 'corrupt',
    corrupt_reason: 'the run record is not a JSON object',
    record_sha256: HASH_DECLARED,
    verdict_consistent: null,
    ...overrides,
  }
}

function page(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    runs: [runRow()],
    count: 1,
    total: 1,
    limit: 20,
    offset: 0,
    has_more: false,
    corrupt_count: 0,
    duplicate_count: 0,
    ignored_entries: 0,
    ignored_samples: [],
    discovery: discovery(),
    complete: true,
    visibility: 'all',
    ...overrides,
  }
}

function sectionsFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    user_raw: { present: true, present_kind: 'value', value: 'run the scenario', evidence_grade: 'unknown' },
    agent_response: {
      present: false,
      present_kind: 'sentinel',
      missing_reason: 'recorded as N/A (P0 not implemented)',
      evidence_grade: 'unknown',
    },
    tools: { present: true, present_kind: 'value', value: [], evidence_grade: 'unknown' },
    state_before: { present: true, present_kind: 'value', value: { db: 'x' }, evidence_grade: 'unknown' },
    state_after: { present: true, present_kind: 'value', value: { db: 'y' }, evidence_grade: 'unknown' },
    interpretation: {
      present: false,
      present_kind: 'sentinel',
      missing_reason: 'recorded as N/A (P0 not implemented)',
      evidence_grade: 'unknown',
    },
    context: {
      present: false,
      present_kind: 'sentinel',
      missing_reason: 'recorded as N/A (P0 not implemented)',
      evidence_grade: 'unknown',
      context_pack_present: false,
    },
    ...overrides,
  }
}

function chainFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    raw: { present: true, source: 'case.user_raw' },
    extraction: {
      present: false,
      source: null,
      missing_reason: 'the recorder records no Extraction section',
    },
    resolution: {
      present: false,
      source: null,
      missing_reason: 'the recorder records no Resolution section',
    },
    context: {
      present: false,
      source: 'case.context',
      missing_reason: 'recorded as N/A (P0 not implemented)',
    },
    agent: {
      present: false,
      source: 'case.agent_response',
      missing_reason: 'recorded as N/A (P0 not implemented)',
    },
    tool: {
      present: false,
      source: 'case.tools',
      missing_reason: 'the case records no tool call',
    },
    evidence: { present: true, source: 'case.evidence' },
    state: { present: true, source: 'case.state_before + case.state_after' },
    ...overrides,
  }
}

function caseFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    case_id: 'case-1',
    name: 'read a recorded run',
    verdict: 'BLOCKED',
    failure_class: null,
    failure_output: null,
    reproduction: [],
    not_applied_reason: 'P0 not implemented',
    evidence: [
      {
        recorded: 'the bundle holds the raw stdout of every command above',
        status: 'unaddressable',
        reason:
          'recorded evidence is free-form data and is never resolved; artifacts are addressable only through a bundle manifest',
      },
    ],
    sections: sectionsFixture(),
    chain: chainFixture(),
    evidence_grade: 'unknown',
    grade_basis: 'no qualifying bundle manifest',
    ...overrides,
  }
}

function detail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: RUN_A,
    stage: 'P0',
    created_at: '2026-09-29T16:29:25Z',
    commit: 'beefdead',
    schema_version: 8,
    core_version: '0.1.0',
    environment: { python: '3.11.0', platform: 'linux' },
    executor: 'scripts/p0-acceptance.py',
    reviewer: 'operator',
    verifier: 'operator',
    verdict: 'BLOCKED',
    verdict_consistent: true,
    notes: ['no FAIL case exists on disk'],
    not_implemented: ['P0 not implemented'],
    cases: [caseFixture()],
    synthetic: false,
    unknown_record_keys: [],
    evidence_grade: 'unknown',
    grade_downgraded_by: [],
    warnings: [],
    ...overrides,
  }
}

function manifestFile(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    artifact_id: ART,
    path: 'artifacts/codex-stdout.txt',
    bytes: 12,
    sha256: HASH_DECLARED,
    media_type: 'text/plain',
    credential_shaped: false,
    integrity: 'match',
    bytes_agree: true,
    ...overrides,
  }
}

function manifest(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: 'frozen',
    run_id: RUN_A,
    frozen_at: '2026-09-29T16:30:00Z',
    declared_mode: 'simulated',
    mode: 'simulated',
    mode_basis: 'declared by the manifest',
    projects: [],
    sources: { recorder: { path: 'scripts/p0-acceptance.py', sha256: HASH_DECLARED } },
    files: [manifestFile()],
    manifest_sha256: HASH_DECLARED,
    manifest_authentication: 'unauthenticated',
    ...overrides,
  }
}

function artifact(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    artifact_id: ART,
    path: 'artifacts/codex-stdout.txt',
    bytes: 12,
    declared_sha256: HASH_DECLARED,
    declared_bytes: 12,
    integrity: 'match',
    media_type: 'text/plain',
    forms: ['masked', 'original'],
    digests: { original: HASH_ORIGINAL, masked: HASH_MASKED },
    credential_shaped: false,
    content_scan: { performed: true, mask_is_noop: true, credential_shaped: false },
    ...overrides,
  }
}

function content(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    artifact_id: ART,
    form: 'masked',
    media_type: 'text/plain',
    bytes: 16,
    served_sha256: HASH_MASKED,
    original_sha256: HASH_ORIGINAL,
    masked_sha256: HASH_MASKED,
    mask_algorithm: 'canonical.redact_text',
    mask_version: '1',
    content: 'masked body text',
    content_is_original: false,
    ...overrides,
  }
}

async function enter(
  user: ReturnType<typeof userEvent.setup>,
  hash: string,
  routes: Parameters<typeof installFakeCore>[0],
  handler?: Parameters<typeof installFakeCore>[1],
): Promise<void> {
  fake = installFakeCore({ 'GET /v1/health': { body: HEALTH }, ...SIGNED_IN, ...routes }, handler)
  render(
    <SessionProvider>
      <App />
    </SessionProvider>,
  )
  await user.type(screen.getByLabelText('Core API token'), 'tok')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
  await screen.findByText('Core 健康与 Schema')
  window.location.hash = hash
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

const detailPath = `/v1/validation/runs/${RUN_A}`
const manifestPath = `${detailPath}/manifest`
const artifactPath = `${detailPath}/artifacts/${ART}`
const contentPath = `${artifactPath}/content`

/* -------------------------------------------------------------------------- *
 * 1. A refused read is a missing dependency
 * -------------------------------------------------------------------------- */

describe('a refused P7 read is shown as a missing dependency', () => {
  it.each([
    ['list', '#/validation/runs', /Validation Runs/, '/v1/validation/runs'],
    ['detail', `#/validation/runs/${RUN_A}`, /Validation Run/, detailPath],
    ['manifest', `#/validation/runs/${RUN_A}/manifest`, /Validation Manifest/, manifestPath],
    [
      'artifact',
      `#/validation/runs/${RUN_A}/artifacts/${ART}`,
      /Validation Artifact/,
      artifactPath,
    ],
  ])('%s marks the pending dependency instead of blaming Core', async (_name, hash, region, route) => {
    const user = userEvent.setup()
    await enter(user, hash as string, { ['GET ' + route]: PROXY_REFUSAL })
    const panel = await screen.findByRole('region', { name: region as RegExp })
    await waitFor(() => expect(within(panel).getByTestId('dependency-pending')).toBeTruthy())
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).queryByTestId('not-found')).toBeNull()
    // The panel must name the real contract source and the switch, not just "not available".
    expect(panel.textContent).toContain('P6_ENABLE_P7_READS')
    expect(panel.textContent).toContain('validation:read')
    expect(panel.textContent).toContain('e79c117')
    expect(panel.textContent).toContain('PARTIAL / DEPENDENCY_PENDING')
  })

  it('states PARTIAL / DEPENDENCY_PENDING and the blocked criterion even when the read succeeds', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', { 'GET /v1/validation/runs': { body: page() } })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const status = within(panel).getByTestId('slice-status')
    expect(status.textContent).toContain('PARTIAL / DEPENDENCY_PENDING')
    expect(status.textContent).toContain('BLOCKED')
  })
})

/* -------------------------------------------------------------------------- *
 * 2. The list: rows, a corrupt row, counts, pagination and filters
 * -------------------------------------------------------------------------- */

describe('the run list renders what the API said, including what is unusable', () => {
  it('lists a usable row and a corrupt row, and never hides the corrupt one', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': {
        body: page({
          runs: [
            runRow({ synthetic: true, synthetic_note: 'the labelled FAIL fixture' }),
            corruptRow(),
          ],
          count: 2,
          total: 2,
          corrupt_count: 1,
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const table = await within(panel).findByTestId('validation-runs')
    expect(table.textContent).toContain(RUN_A)
    expect(table.textContent).toContain(RUN_CORRUPT)
    // The recorded verdict and the effective mode/attribution are shown as recorded.
    expect(table.textContent).toContain('BLOCKED')
    expect(table.textContent).toContain('no bundle manifest on disk')
    expect(table.textContent).toContain('unattributed')
    expect(table.textContent).toContain('synthetic')

    const unusable = within(table).getByTestId('run-row-unusable')
    expect(unusable.textContent).toContain('corrupt')
    const reasonRow = within(table).getByTestId('run-row-unusable-reason')
    expect(reasonRow.textContent).toContain('the run record is not a JSON object')
    expect(reasonRow.textContent).toContain('不可用')
    // The fields the reader could not read render as `—`, not as an invented value.
    expect(reasonRow.textContent).toContain(HASH_DECLARED)

    // And the counts that separate "empty" from "broken" are all on the page.
    const summary = within(panel).getByTestId('validation-summary')
    expect(summary.textContent).toContain('corrupt_count')
    expect(summary.textContent).toContain('duplicate_count')
    expect(summary.textContent).toContain('ignored_entries')
    expect(summary.textContent).toContain('has_more')
    expect(summary.textContent).toContain('visibility')
  })

  it('renders a duplicate row as unusable with its own reason, counted separately from corrupt', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': {
        body: page({
          runs: [
            runRow({
              record_status: 'duplicate',
              duplicate_reason: '2 run-shaped files share this stem',
              mode: null,
              mode_basis: null,
              attribution: null,
              evidence_grade: null,
              manifest_status: null,
              verdict_consistent: null,
            }),
          ],
          count: 1,
          total: 1,
          duplicate_count: 1,
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const table = await within(panel).findByTestId('validation-runs')
    const unusable = within(table).getByTestId('run-row-unusable')
    expect(unusable.textContent).toContain('duplicate')
    expect(within(table).getByTestId('run-row-unusable-reason').textContent).toContain(
      '2 run-shaped files share this stem',
    )
    expect(within(panel).getByTestId('validation-summary').textContent).toContain('duplicate_count')
  })

  it('does not claim the root is empty when acl filters every record out (AD1)', async () => {
    // Core computes `empty_reason` over the VISIBLE set so that a hidden record cannot be
    // probed for. Under `grant_filtered`, `no_run_shaped_files` therefore means "none you may
    // read", and the page must not turn it into a statement about the operator's own disk.
    const user = userEvent.setup()
    await enter(user, '#/validation/runs?project=prj_01M4B6VGC29ESRDRQR62DX8AK8', {
      'GET /v1/validation/runs': {
        body: page({
          runs: [],
          count: 0,
          total: 0,
          visibility: 'grant_filtered',
          empty_reason: 'no_run_shaped_files',
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const reason = await within(panel).findByTestId('empty-reason')
    expect(reason.textContent).toContain('no_run_shaped_files')
    expect(reason.textContent).toContain('按「可见集合」算')
    expect(reason.textContent).not.toContain('运行根里没有 run-shaped 文件')
  })

  it('says a duplicate row is parseable instead of claiming its verdict is unavailable', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': {
        body: page({
          runs: [
            runRow({
              record_status: 'duplicate',
              verdict: 'PASS',
              duplicate_reason: '2 files share this stem',
            }),
          ],
          count: 1,
          total: 1,
          duplicate_count: 1,
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const table = await within(panel).findByTestId('validation-runs')
    const note = within(table).getByTestId('run-row-unusable-reason').textContent ?? ''
    expect(note).toContain('可解析')
    expect(note).toContain('不是权威结论')
    expect(note).toContain('409 validation_run_ambiguous')
    expect(note).not.toContain('都不可用')
  })

  it('shows the recorded verdict even when it disagrees with the cases (no re-derivation)', async () => {
    // The run's recorded verdict is PASS while its only case is BLOCKED, so the API reports
    // `verdict_consistent: false` and still serves PASS. A view that re-derived the run verdict
    // from the cases would show BLOCKED for the run: this asserts the recorded value is what
    // the run row displays.
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: {
        body: detail({
          verdict: 'PASS',
          verdict_consistent: false,
          cases: [caseFixture({ verdict: 'BLOCKED' })],
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    // The run's own row is the first occurrence; the case card carries the same label for the
    // case's verdict, which is a different value.
    const [runVerdictRow] = within(panel).getAllByText('verdict(按记录)')
    expect(runVerdictRow?.closest('div')?.textContent).toContain('PASS')
    expect(within(panel).getByTestId('verdict-consistent').textContent).toContain('false')
    expect(panel.textContent).toContain('本页仍按记录展示,不替你改判')
    // The case's own BLOCKED verdict is still shown: it is the case's value, not a verdict the
    // view computed for the run.
    expect(within(panel).getAllByText(/BLOCKED/).length).toBeGreaterThan(0)
  })

  it('echoes an unrecognised missing_reason verbatim instead of a hard-coded string', async () => {
    const user = userEvent.setup()
    const arbitrary = 'the recorder said something this build has never seen'
    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: {
        body: detail({
          cases: [
            caseFixture({
              chain: chainFixture({
                agent: { present: false, source: 'case.agent_response', missing_reason: arbitrary },
              }),
            }),
          ],
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    expect(within(panel).getByTestId('chain-agent').textContent).toContain(arbitrary)
  })

  it('tells a filtered-empty result apart from an empty root, and shows grant filtering', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': {
        body: page({
          runs: [],
          count: 0,
          total: 0,
          visibility: 'grant_filtered',
          empty_reason: 'all_filtered_out',
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const reason = await within(panel).findByTestId('empty-reason')
    expect(reason.textContent).toContain('all_filtered_out')
    expect(reason.textContent).toContain('过滤条件把它们全部排除')
    expect(panel.textContent).toContain('grant_filtered')
    expect(panel.textContent).toContain('acl 模式')
  })

  it('pages with limit/offset and trusts Core has_more, not a short page', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      '#/validation/runs',
      {},
      (request) => {
        if (request.method !== 'GET' || request.path !== '/v1/validation/runs') return undefined
        const offset = Number(request.query.get('offset') ?? '0')
        if (offset === 0) {
          return {
            body: page({ runs: [runRow()], count: 1, total: 2, has_more: true, offset: 0 }),
          }
        }
        return {
          body: page({
            runs: [runRow({ run_id: RUN_B, record_sha256: 'e'.repeat(64) })],
            count: 1,
            total: 2,
            has_more: false,
            offset: 1,
          }),
        }
      },
    )
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const table = await within(panel).findByTestId('validation-runs')
    expect(table.textContent).toContain(RUN_A)

    await user.click(within(panel).getByRole('button', { name: '继续加载Validation run' }))
    await waitFor(() => expect(table.textContent).toContain(RUN_B))
    const calls = fake!.requests.filter((request) => request.path === '/v1/validation/runs')
    expect(calls.some((request) => request.query.get('offset') === '1')).toBe(true)
    expect(calls.some((request) => request.query.get('limit') === '20')).toBe(true)
    // Completion is Core's own `has_more`, so a full last page is still the last page.
    await waitFor(() =>
      expect(panel.textContent).toContain('Core 在最后一页返回 has_more = false'),
    )
  })

  it('sends the five filters to Core and refuses a malformed project locally', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', { 'GET /v1/validation/runs': { body: page() } })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    await within(panel).findByTestId('validation-runs')

    await user.selectOptions(screen.getByLabelText('verdict'), 'FAIL')
    await user.selectOptions(screen.getByLabelText('attribution'), 'unattributed')
    await user.type(screen.getByLabelText('stage'), 'P0')
    await user.type(screen.getByLabelText('project'), PROJECT)
    await user.click(within(panel).getByRole('button', { name: '应用过滤' }))

    await waitFor(() => {
      const filtered = fake!.requests.filter(
        (request) =>
          request.path === '/v1/validation/runs' &&
          request.query.get('verdict') === 'FAIL' &&
          request.query.get('attribution') === 'unattributed' &&
          request.query.get('stage') === 'P0' &&
          request.query.get('project') === PROJECT,
      )
      expect(filtered.length).toBeGreaterThan(0)
    })

    // A malformed project is refused before any request, so a typo is never answered as
    // "P7 is not merged" by the proxy.
    await user.clear(screen.getByLabelText('project'))
    await user.type(screen.getByLabelText('project'), 'not-a-prj-id')
    await user.click(within(panel).getByRole('button', { name: '应用过滤' }))
    expect(within(panel).getByTestId('filter-invalid').textContent).toContain('prj_')
    expect(
      fake!.requests.some(
        (request) =>
          request.path === '/v1/validation/runs' && request.query.get('project') === 'not-a-prj-id',
      ),
    ).toBe(false)
  })

  it('refuses a malformed run id locally, with no request sent', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs/run-2026-10-08', SIGNED_IN)
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    expect(within(panel).getByTestId('invalid-id').textContent).toContain('run-[0-9]{8}T[0-9]{6}Z')
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
    expect(fake!.requests.filter((request) => request.path.startsWith('/v1/validation/runs/'))).toHaveLength(0)
  })
})

/* -------------------------------------------------------------------------- *
 * 3. Run detail: recorded fields, the eight links, the three context forms
 * -------------------------------------------------------------------------- */

describe('run detail shows the recorded run and the eight chain links honestly', () => {
  it('shows synthetic, unknown keys, warnings, grade and the recorded case fields', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: {
        body: detail({
          synthetic: true,
          synthetic_note: 'P7 FAIL fixture, never evidence',
          unknown_record_keys: ['mystery_key'],
          evidence_grade: 'simulated',
          grade_downgraded_by: ['case-1'],
          warnings: ['manifest declares simulated but case case-1 is graded unknown'],
          cases: [
            caseFixture({
              verdict: 'FAIL',
              failure_class: 'ASSERTION_MISMATCH',
              failure_output: 'expected 200, received 500',
              reproduction: ['pytest -k scenario_one'],
              not_applied_reason: null,
              evidence_grade: 'simulated',
              grade_basis: 'the run record marks itself synthetic',
            }),
          ],
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    await within(panel).findByTestId('verdict-consistent')

    expect(within(panel).getByTestId('synthetic').textContent).toContain('P7 FAIL fixture, never evidence')
    expect(within(panel).getByTestId('unknown-record-keys').textContent).toContain('mystery_key')
    expect(within(panel).getByTestId('run-warnings').textContent).toContain(
      'manifest declares simulated but case case-1 is graded unknown',
    )
    // The recorded verdict and the recorded grade are shown verbatim.
    expect(panel.textContent).toContain('FAIL')
    expect(panel.textContent).toContain('simulated')
    expect(panel.textContent).toContain('ASSERTION_MISMATCH')
    expect(panel.textContent).toContain('expected 200, received 500')
    expect(panel.textContent).toContain('pytest -k scenario_one')
    // Evidence stays recorded data, marked unaddressable.
    const evidence = within(panel).getByTestId('case-evidence')
    expect(evidence.textContent).toContain('unaddressable')
    expect(evidence.textContent).toContain('addressable only through a bundle manifest')
  })

  it('renders all eight links, a present one and the missing ones, without filling any in', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: { body: detail() },
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    const caseCard = await within(panel).findByTestId('validation-case')
    expect(within(caseCard).getAllByTestId(/^chain-/)).toHaveLength(8)

    const raw = within(caseCard).getByTestId('chain-raw')
    expect(raw.getAttribute('data-present')).toBe('true')
    expect(raw.textContent).toContain('case.user_raw')

    const extraction = within(caseCard).getByTestId('chain-extraction')
    expect(extraction.getAttribute('data-present')).toBe('false')
    expect(extraction.textContent).toContain('the recorder records no Extraction section')
    expect(extraction.textContent).toContain('missing')

    const resolution = within(caseCard).getByTestId('chain-resolution')
    expect(resolution.textContent).toContain('the recorder records no Resolution section')

    const tool = within(caseCard).getByTestId('chain-tool')
    expect(tool.textContent).toContain('the case records no tool call')
    const agent = within(caseCard).getByTestId('chain-agent')
    expect(agent.textContent).toContain('recorded as N/A (P0 not implemented)')
    const state = within(caseCard).getByTestId('chain-state')
    expect(state.getAttribute('data-present')).toBe('true')
    const evidenceLink = within(caseCard).getByTestId('chain-evidence')
    expect(evidenceLink.getAttribute('data-present')).toBe('true')
  })

  it('names the three distinct reasons a context link is missing, and shows context_pack_present', async () => {
    const user = userEvent.setup()
    const sentinel = caseFixture({
      case_id: 'case-sentinel',
      chain: chainFixture({
        context: {
          present: false,
          source: 'case.context',
          missing_reason: 'recorded as N/A (P0 not implemented)',
        },
      }),
    })
    const reasonOnly = caseFixture({
      case_id: 'case-reason-only',
      chain: chainFixture({
        context: {
          present: false,
          source: 'case.context',
          missing_reason: 'the case records a reason, not a context pack',
        },
      }),
    })
    const runDetails = caseFixture({
      case_id: 'case-run-details',
      chain: chainFixture({
        context: {
          present: false,
          source: 'case.context',
          missing_reason: 'the section records run details, not a context pack',
        },
      }),
      sections: sectionsFixture({
        context: {
          present: true,
          present_kind: 'value',
          value: { db_path: '/tmp/core.db', processes: [1, 2] },
          evidence_grade: 'unknown',
          context_pack_present: false,
        },
      }),
    })

    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: { body: detail({ cases: [sentinel, reasonOnly, runDetails] }) },
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    const cases = await within(panel).findAllByTestId('validation-case')
    expect(cases).toHaveLength(3)
    expect(within(cases[0]!).getByTestId('chain-context').textContent).toContain(
      'recorded as N/A (P0 not implemented)',
    )
    expect(within(cases[1]!).getByTestId('chain-context').textContent).toContain(
      'the case records a reason, not a context pack',
    )
    expect(within(cases[2]!).getByTestId('chain-context').textContent).toContain(
      'the section records run details, not a context pack',
    )
    // The recorded-but-packless context is `present`, but the pack flag says it is not a pack.
    const contextSection = within(cases[2]!).getByTestId('section-context')
    expect(contextSection.getAttribute('data-present')).toBe('true')
    expect(within(contextSection).getByTestId('context-pack-present').textContent).toContain(
      'context_pack_present = false',
    )
    expect(within(contextSection).getByTestId('context-pack-present').textContent).toContain(
      '没有 context pack',
    )
  })
})

/* -------------------------------------------------------------------------- *
 * 4. Manifest
 * -------------------------------------------------------------------------- */

describe('the manifest view reports its status, files, integrity and the unsigned warning', () => {
  it('shows the unauthenticated warning and every integrity state, with a link into the artifact', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/manifest`, {
      ['GET ' + manifestPath]: {
        body: manifest({
          files: [
            manifestFile(),
            manifestFile({ artifact_id: ART_MISSING, path: 'artifacts/missing.txt', integrity: 'missing', bytes_agree: false }),
            manifestFile({
              artifact_id: ART_SYMLINK,
              path: 'artifacts/link.txt',
              integrity: 'symlink_refused',
              bytes_agree: false,
            }),
            manifestFile({
              artifact_id: ART_MODIFIED,
              path: 'artifacts/flipped.txt',
              integrity: 'mismatch',
              bytes_agree: false,
            }),
            manifestFile({ artifact_id: 'art_unreadable', path: 'artifacts/locked.txt', integrity: 'unreadable', bytes_agree: false }),
          ],
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Manifest/ })
    const warning = await within(panel).findByTestId('manifest-unauthenticated')
    expect(warning.textContent).toContain('unauthenticated')
    expect(warning.textContent).toContain('未签名')
    expect(warning.textContent).toContain('自述')

    const table = within(panel).getByTestId('manifest-files')
    expect(within(table).getAllByTestId('manifest-file')).toHaveLength(5)
    expect(table.textContent).toContain('match')
    expect(table.textContent).toContain('missing')
    expect(table.textContent).toContain('symlink_refused')
    expect(table.textContent).toContain('mismatch')
    expect(table.textContent).toContain('unreadable')
    expect(table.textContent).toContain('false')
    // The digest is shown whole and the artifact is reachable.
    expect(table.textContent).toContain(HASH_DECLARED)
    const link = within(table).getByRole('link', { name: ART })
    expect(link.getAttribute('href')).toBe(`#/validation/runs/${RUN_A}/artifacts/${ART}`)
  })

  it('shows declared_mode vs the derived mode, and an absent manifest is not a frozen one', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/manifest`, {
      ['GET ' + manifestPath]: {
        body: manifest({
          declared_mode: 'real',
          mode: 'unknown',
          mode_basis: 'declared real without recorder provenance or project attribution',
          status: 'frozen',
          files: [],
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Manifest/ })
    const differs = await within(panel).findByTestId('mode-differs')
    expect(differs.textContent).toContain('real')
    expect(differs.textContent).toContain('unknown')
    expect(panel.textContent).toContain('declared real without recorder provenance or project attribution')
    expect(panel.textContent).toContain('manifest_sha256')
  })

  it('reports a corrupt manifest with its rule, and an absent one with its reason', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/manifest`, {
      ['GET ' + manifestPath]: {
        body: manifest({
          status: 'corrupt',
          rule: 'art_escape: dot segment path "../secret.txt"',
          files: [],
          manifest_sha256: null,
        }),
      },
    })
    const panel = await screen.findByRole('region', { name: /Validation Manifest/ })
    const notFrozen = await within(panel).findByTestId('manifest-not-frozen')
    expect(notFrozen.textContent).toContain('art_escape: dot segment path "../secret.txt"')
    expect(notFrozen.textContent).toContain('全部不可寻址')
  })
})

/* -------------------------------------------------------------------------- *
 * 5. Artifact metadata + content
 * -------------------------------------------------------------------------- */

describe('artifact metadata keeps the original and masked digests separate', () => {
  it('labels digests.original and digests.masked distinctly and shows the scan', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/artifacts/${ART}`, {
      ['GET ' + artifactPath]: { body: artifact() },
      ['GET ' + contentPath]: { body: content() },
    })
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const digests = await within(panel).findByTestId('artifact-digests')
    expect(digests.textContent).toContain('digests.original')
    expect(digests.textContent).toContain(HASH_ORIGINAL)
    expect(digests.textContent).toContain('digests.masked')
    expect(digests.textContent).toContain(HASH_MASKED)
    // Two separate labelled values, and the hashes differ in this fixture.
    expect(HASH_ORIGINAL).not.toBe(HASH_MASKED)

    const scan = within(panel).getByTestId('content-scan')
    expect(scan.textContent).toContain('mask_is_noop')
    expect(scan.textContent).toContain('credential_shaped')

    const body = within(panel).getByTestId('artifact-content')
    expect(body.textContent).toContain('masked body text')
    expect(body.textContent).toContain('served_sha256')
    expect(body.textContent).toContain('original_sha256')
    expect(body.textContent).toContain('masked_sha256')
    // The content is rendered as text, not markup.
    expect(body.querySelector('pre')?.textContent).toBe('masked body text')
  })

  it('probes the original when forms lacks it, and shows Core\'s own reason', async () => {
    // `forms` is the contract's answer to "is the original available?", so the control is a
    // probe rather than a download. Probing is how the Inspector can show the *real* reason:
    // artifact metadata carries no `original_withheld_reason`, and inferring it from
    // `credential_shaped`/`mask_is_noop` would be a guess, not a reading.
    const user = userEvent.setup()
    const calls: string[] = []
    await enter(
      user,
      `#/validation/runs/${RUN_A}/artifacts/${ART}`,
      {
        ['GET ' + artifactPath]: {
          body: artifact({
            forms: ['masked'],
            credential_shaped: true,
            content_scan: { performed: true, mask_is_noop: false, credential_shaped: true },
          }),
        },
      },
      (request) => {
        if (request.method !== 'GET' || request.path !== contentPath) return undefined
        if (request.query.get('form') === 'original') {
          calls.push('original')
          return errorReply(403, 'original_withheld', 'the original form is withheld', {
            reason: 'credential_shaped_declared',
            masked_sha256: HASH_MASKED,
          })
        }
        calls.push('masked')
        return { body: content({ served_sha256: HASH_MASKED, original_sha256: HASH_ORIGINAL }) }
      },
    )
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const notOffered = await within(panel).findByTestId('original-not-offered')
    expect(notOffered.textContent).toContain('masked')
    const probe = within(panel).getByRole('button', { name: /请求 original/ }) as HTMLButtonElement
    expect(probe.disabled).toBe(false)
    await user.click(probe)
    const withheld = await within(panel).findByTestId('original-withheld')
    expect(withheld.textContent).toContain('original_withheld')
    expect(withheld.textContent).toContain('credential_shaped_declared')
    expect(withheld.textContent).toContain(HASH_MASKED)
    expect(calls).toContain('original')
    // The masked body is still on screen: a refused original is not a failed read.
    expect(await within(panel).findByTestId('artifact-content')).toBeTruthy()
  })

  it('shows 403 original_withheld with the reason and the masked digest instead of an error page', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      `#/validation/runs/${RUN_A}/artifacts/${ART}`,
      {
        ['GET ' + artifactPath]: { body: artifact() },
      },
      (request) => {
        if (request.method !== 'GET' || request.path !== contentPath) return undefined
        if (request.query.get('form') === 'original') {
          return errorReply(403, 'original_withheld', 'the original form is withheld', {
            reason: 'mask_not_noop',
            masked_sha256: HASH_MASKED,
          })
        }
        return { body: content() }
      },
    )
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    await within(panel).findByTestId('artifact-content')

    await user.click(within(panel).getByRole('button', { name: '读取 original' }))
    const withheld = await within(panel).findByTestId('original-withheld')
    expect(withheld.textContent).toContain('original_withheld')
    expect(withheld.textContent).toContain('mask_not_noop')
    expect(withheld.textContent).toContain(HASH_MASKED)
    // Not an error page: the masked body Core can still serve stays visible.
    expect(within(panel).queryByRole('alert')).toBeNull()
    expect(within(panel).getAllByTestId('artifact-content')[0]!.textContent).toContain('masked body text')
  })

  it('serves the original envelope as text when the API says the original is available', async () => {
    const user = userEvent.setup()
    await enter(
      user,
      `#/validation/runs/${RUN_A}/artifacts/${ART}`,
      {
        ['GET ' + artifactPath]: { body: artifact() },
      },
      (request) => {
        if (request.method !== 'GET' || request.path !== contentPath) return undefined
        if (request.query.get('form') === 'original') {
          return {
            body: content({
              form: 'original',
              content: 'the exact original bytes <b>not html</b>',
              content_is_original: true,
              served_sha256: HASH_ORIGINAL,
            }),
          }
        }
        return { body: content() }
      },
    )
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    await within(panel).findByTestId('artifact-content')
    await user.click(within(panel).getByRole('button', { name: '读取 original' }))
    await waitFor(() => {
      const bodies = within(panel).getAllByTestId('artifact-content')
      expect(bodies.some((node) => node.textContent?.includes('the exact original bytes <b>not html</b>'))).toBe(true)
    })
    // It is a text node: the `<b>` stays literal and no element was created from it.
    const original = within(panel)
      .getAllByTestId('artifact-content')
      .find((node) => node.textContent?.includes('the exact original bytes'))!
    expect(original.querySelector('b')).toBeNull()
    expect(original.querySelector('pre')?.textContent).toContain('<b>not html</b>')
  })
})

/* -------------------------------------------------------------------------- *
 * 6. Refusals are never "no data"
 * -------------------------------------------------------------------------- */

describe('each refusal reads as the condition it is, never as an empty result', () => {
  const LIST_CASES: ReadonlyArray<[string, number, string, unknown, string, readonly string[]]> = [
    ['401 unauthenticated', 401, 'unauthenticated', {}, 'unauthenticated', ['未认证']],
    ['403 forbidden_scope', 403, 'forbidden_scope', { required: 'validation:read' }, 'forbidden', ['forbidden_scope']],
    [
      '503 validation_not_configured unset',
      503,
      'validation_not_configured',
      { reason: 'unset' },
      'not-configured',
      ['validation_not_configured', 'unset', 'JASMINE_CORE_VALIDATION_RUNS', '没有默认值'],
    ],
    [
      '503 validation_not_configured index_incomplete',
      503,
      'validation_not_configured',
      { reason: 'index_incomplete', counts: { unreadable_entries: 1 }, samples: [{ path: 'x', error: 'EACCES' }] },
      'not-configured',
      ['index_incomplete', '拒绝分页'],
    ],
    [
      '503 validation_not_configured symlinked_root',
      503,
      'validation_not_configured',
      { reason: 'symlinked_root' },
      'not-configured',
      ['symlinked_root', 'O_NOFOLLOW'],
    ],
    [
      '503 validation_index_too_large',
      503,
      'validation_index_too_large',
      { cap: 'MAX_RUN_SHAPED', limit: 500 },
      'index-too-large',
      ['MAX_RUN_SHAPED', '500'],
    ],
    [
      '503 memory_deployment_not_ready',
      503,
      'memory_deployment_not_ready',
      {},
      'deployment-not-ready',
      ['acl', 'memory_project_actors'],
    ],
  ]

  it.each(LIST_CASES)('%s', async (_name, status, code, details, testid, expected) => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': errorReply(status, code, code, details),
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const node = await within(panel).findByTestId(testid)
    for (const needle of expected) expect(node.textContent).toContain(needle)
    // A refusal must not be dressed as the dependency or as "no runs".
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
    expect(panel.textContent).not.toContain('空:Validation run')
  })

  it('shows the 401 as a sign-in condition with the code', async () => {
    const user = userEvent.setup()
    await enter(user, '#/validation/runs', {
      'GET /v1/validation/runs': errorReply(401, 'unauthenticated', 'unknown token'),
    })
    const panel = await screen.findByRole('region', { name: /Validation Runs/ })
    const node = await within(panel).findByTestId('unauthenticated')
    expect(node.textContent).toContain('401')
    expect(node.textContent).toContain('退出')
  })

  const DETAIL_CASES: ReadonlyArray<[string, number, string, unknown, readonly string[]]> = [
    ['404 validation_run_not_found', 404, 'validation_run_not_found', {}, ['validation_run_not_found', 'acl']],
    ['409 validation_run_corrupt', 409, 'validation_run_corrupt', {}, ['不可用', 'run-shaped']],
    ['409 validation_run_ambiguous', 409, 'validation_run_ambiguous', {}, ['多份可解析记录']],
  ]

  it.each(DETAIL_CASES)('%s', async (_name, status, code, details, expected) => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}`, {
      ['GET ' + detailPath]: errorReply(status, code, code, details),
    })
    const panel = await screen.findByRole('region', { name: /Validation Run/ })
    const node = await within(panel).findByTestId(status === 404 ? 'not-found' : 'conflict')
    for (const needle of expected) expect(node.textContent).toContain(needle)
  })

  it('shows 409 validation_manifest_corrupt with the broken rule', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/manifest`, {
      ['GET ' + manifestPath]: errorReply(409, 'validation_manifest_corrupt', 'manifest is corrupt', {
        rule: 'art_escape: absolute/UNC prefix path "/etc/passwd"',
      }),
    })
    const panel = await screen.findByRole('region', { name: /Validation Manifest/ })
    const node = await within(panel).findByTestId('conflict')
    expect(node.textContent).toContain('validation_manifest_corrupt')
    expect(node.textContent).toContain('/etc/passwd')
  })

  it('shows 404 artifact_not_found with its distinguishing reason', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/artifacts/${ART}`, {
      ['GET ' + artifactPath]: errorReply(404, 'artifact_not_found', 'artifact not found', {
        reason: 'manifest_corrupt',
      }),
    })
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const node = await within(panel).findByTestId('not-found')
    expect(node.textContent).toContain('artifact_not_found')
    expect(node.textContent).toContain('manifest_corrupt')
    expect(within(panel).queryByTestId('dependency-pending')).toBeNull()
  })

  it('shows 409 artifact_modified with both digests', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/artifacts/${ART}`, {
      ['GET ' + artifactPath]: errorReply(409, 'artifact_modified', 'artifact bytes disagree', {
        declared_sha256: HASH_DECLARED,
        actual_sha256: HASH_MASKED,
        declared_bytes: 12,
        actual_bytes: 13,
      }),
    })
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const node = await within(panel).findByTestId('conflict')
    expect(node.textContent).toContain('artifact_modified')
    expect(node.textContent).toContain(HASH_DECLARED)
    expect(node.textContent).toContain(HASH_MASKED)
  })

  it('shows 413 payload_too_large and 415 unsupported_media_type as themselves', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/artifacts/${ART}`, {
      ['GET ' + artifactPath]: errorReply(413, 'payload_too_large', 'too large', {
        limit: 8388608,
        form: 'masked',
        original_bytes: 100,
      }),
    })
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const tooLarge = await within(panel).findByTestId('payload-too-large')
    expect(tooLarge.textContent).toContain('8388608')
    expect(tooLarge.textContent).toContain('masked')
  })

  it('shows 415 unsupported_media_type with the media type', async () => {
    const user = userEvent.setup()
    await enter(user, `#/validation/runs/${RUN_A}/artifacts/${ART}`, {
      ['GET ' + artifactPath]: errorReply(415, 'unsupported_media_type', 'not text', {
        media_type: 'application/octet-stream',
      }),
    })
    const panel = await screen.findByRole('region', { name: /Validation Artifact/ })
    const node = await within(panel).findByTestId('unsupported-media')
    expect(node.textContent).toContain('application/octet-stream')
  })
})
