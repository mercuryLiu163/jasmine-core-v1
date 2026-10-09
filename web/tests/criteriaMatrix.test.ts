/**
 * The criteria↔Evidence candidate predicate (P6-02 item 5, frozen contract §3.2).
 *
 * These tests pin the browser-readable half against the code it copies
 * (`src/jasmine_core/evidence.py:846-958`): F3–F7 and F9a–F9c decide, F1/F2/F8/F9d/F9e never do.
 * The point of the suite is negative as much as positive: a row that passes everything readable is
 * still only a *candidate*, and `INFO` can never satisfy a `PASS` requirement.
 */

import { describe, expect, it } from 'vitest'

import type { AcceptanceRequirement, Evidence, RawEvent } from '../src/api/types'
import {
  cycleCutoff,
  evaluateRequirement,
  matchesRequirementFields,
  readableFreshnessFailures,
  ruleApplies,
  UNREADABLE_FRESHNESS,
  type MatrixContext,
} from '../src/lib/criteriaMatrix'

const TASK = 'tsk_01M4B6VGC29ESRDRQR62DX8AK8'
const STEP = 'stp_01M4B6VGC29ESRDRQR62DX8AK8'
const PROJECT = 'prj_01M4B6VGC29ESRDRQR62DX8AK8'
const AT = '2026-10-07T12:00:00Z'

function rawEvent(overrides: Partial<RawEvent> = {}): RawEvent {
  return {
    event_id: 'evt_source',
    seq: 20,
    schema_version: 8,
    event_type: 'tool.result',
    source_system: 'codex-posttool',
    source_event_id: null,
    occurred_at: AT,
    recorded_at: AT,
    actor_id: 'act_1',
    actor_kind: 'system',
    host_id: 'hst_1',
    session_id: null,
    project_id: PROJECT,
    task_id: TASK,
    payload: {},
    body_sha256: 'a'.repeat(64),
    ...overrides,
  }
}

function row(overrides: Partial<Evidence> = {}): Evidence {
  return {
    evidence_id: 'evd_01M4B6VGC29ESRDRQR62DX8AK8',
    task_id: TASK,
    step_id: STEP,
    kind: 'TEST',
    status: 'PASS',
    source_event_id: 'evt_source',
    change_event_id: 'evt_change',
    fingerprint_sha256: 'b'.repeat(64),
    artifact_uri: null,
    artifact_sha256: null,
    actor_id: 'act_1',
    host_id: 'hst_1',
    session_id: null,
    turn_id: null,
    tool_use_id: null,
    tool_name: null,
    command_sha256: null,
    producer_config_sha256: null,
    confirmed_rule_id: null,
    confirmed_rule_version: null,
    task_revision: 12,
    step_revision: 3,
    created_at: AT,
    related_posttool_event_id: null,
    result: {},
    reference_status: 'NONE',
    ...overrides,
  }
}

const REQUIREMENT: AcceptanceRequirement = {
  key: 'build',
  kind: 'TEST',
  required_result: 'PASS',
}

/** A context in which the default row is fully a candidate. */
function context(overrides: Partial<MatrixContext> = {}): MatrixContext {
  return {
    projectId: PROJECT,
    taskRevision: 12,
    expectedRevision: 3,
    stepId: STEP,
    // §3.1 R-TH: a Step cycle that started at seq 5; the change Event at seq 20 is newer.
    commandEvents: [
      rawEvent({
        event_id: 'evt_cycle',
        seq: 5,
        event_type: 'step.transitioned',
        actor_kind: 'human',
        payload: { step_id: STEP, to: 'IN_PROGRESS' },
      }),
    ],
    eventsById: new Map([
      ['evt_source', rawEvent()],
      ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20, event_type: 'evidence.recorded' })],
    ]),
    ...overrides,
  }
}

describe('the candidate predicate is exactly F3–F7 + F9a–F9c', () => {
  it('marks a row that passes every readable condition as a candidate with the full residual', () => {
    const result = evaluateRequirement(REQUIREMENT, [row()], context())
    expect(result.state).toBe('candidate')
    expect(result.candidates).toHaveLength(1)
    expect(result.stale).toHaveLength(0)
    // The five unreadable conditions are named, not assumed away.
    expect(result.residualUnknown).toEqual([...UNREADABLE_FRESHNESS])
    expect(result.residualUnknown).toContain('F1')
    expect(result.residualUnknown).toContain('F9e')
  })

  it('is missing when no row has the requirement kind and status — and lists the wrong kind', () => {
    const other = row({ kind: 'BUILD', status: 'PASS' })
    const result = evaluateRequirement(REQUIREMENT, [other], context())
    expect(result.state).toBe('missing')
    expect(result.candidates).toHaveLength(0)
    expect(result.residualUnknown).toEqual([])
    expect(result.wrongKind.map((item) => item.evidence_id)).toEqual([other.evidence_id])
  })

  it('proves staleness from F3 (task_revision beyond the form revision)', () => {
    const result = evaluateRequirement(REQUIREMENT, [row({ task_revision: 99 })], context())
    expect(result.state).toBe('stale-proven')
    expect(result.stale[0]?.failures.map((failure) => failure.condition)).toContain('F3')
  })

  it('proves staleness from F5 (change Event not newer than the cycle cutoff)', () => {
    const result = evaluateRequirement(
      REQUIREMENT,
      [row()],
      context({
        eventsById: new Map([
          ['evt_source', rawEvent()],
          ['evt_change', rawEvent({ event_id: 'evt_change', seq: 4 })],
        ]),
      }),
    )
    expect(result.state).toBe('stale-proven')
    expect(result.stale[0]?.failures.map((failure) => failure.condition)).toContain('F5')
  })

  it('proves staleness from F7 (BROKEN_REFERENCE)', () => {
    const result = evaluateRequirement(
      REQUIREMENT,
      [row({ reference_status: 'BROKEN_REFERENCE' })],
      context(),
    )
    expect(result.state).toBe('stale-proven')
    expect(result.stale[0]?.failures.map((failure) => failure.condition)).toContain('F7')
  })

  it('proves staleness from F9a (source Event absent or from another project)', () => {
    const missingSource = evaluateRequirement(REQUIREMENT, [row()], context({
      eventsById: new Map([
        ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20, event_type: 'evidence.recorded' })],
      ]),
    }))
    expect(missingSource.state).toBe('stale-proven')
    expect(missingSource.stale[0]?.failures.map((failure) => failure.condition)).toContain('F9a')

    const wrongProject = evaluateRequirement(
      REQUIREMENT,
      [row()],
      context({
        eventsById: new Map([
          ['evt_source', rawEvent({ project_id: 'prj_other' })],
          ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })],
        ]),
      }),
    )
    expect(wrongProject.state).toBe('stale-proven')
    expect(wrongProject.stale[0]?.failures.map((failure) => failure.condition)).toContain('F9a')
  })

  it('proves staleness from F9c (source is not this actor system tool.result)', () => {
    const result = evaluateRequirement(
      REQUIREMENT,
      [row()],
      context({
        eventsById: new Map([
          ['evt_source', rawEvent({ actor_kind: 'human', event_type: 'user.prompt' })],
          ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })],
        ]),
      }),
    )
    expect(result.state).toBe('stale-proven')
    expect(result.stale[0]?.failures.map((failure) => failure.condition)).toContain('F9c')
  })

  it('checks F9b for a USER_CONFIRMATION and F6 for its exact revision', () => {
    const confirmation: AcceptanceRequirement = {
      key: 'human-ok',
      kind: 'USER_CONFIRMATION',
      required_result: 'INFO',
    }
    const humanPrompt = rawEvent({
      event_id: 'evt_source',
      event_type: 'user.prompt',
      actor_kind: 'human',
      actor_id: 'act_1',
    })
    const good = evaluateRequirement(
      confirmation,
      [row({ kind: 'USER_CONFIRMATION', status: 'INFO', step_revision: 3 })],
      context({ eventsById: new Map([['evt_source', humanPrompt], ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })]]) }),
    )
    expect(good.state).toBe('candidate')

    const wrongActor = evaluateRequirement(
      confirmation,
      [row({ kind: 'USER_CONFIRMATION', status: 'INFO', step_revision: 3, actor_id: 'act_2' })],
      context({ eventsById: new Map([['evt_source', humanPrompt], ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })]]) }),
    )
    expect(wrongActor.state).toBe('stale-proven')
    expect(wrongActor.stale[0]?.failures.map((failure) => failure.condition)).toContain('F9b')

    const staleRevision = evaluateRequirement(
      confirmation,
      [row({ kind: 'USER_CONFIRMATION', status: 'INFO', step_revision: 2 })],
      context({ eventsById: new Map([['evt_source', humanPrompt], ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })]]) }),
    )
    expect(staleRevision.state).toBe('stale-proven')
    expect(staleRevision.stale[0]?.failures.map((failure) => failure.condition)).toContain('F6')
  })

  it('reports a required field mismatch as its own state, never as a candidate', () => {
    const withTool: AcceptanceRequirement = { ...REQUIREMENT, tool_name: 'pytest' }
    const result = evaluateRequirement(withTool, [row({ tool_name: 'jest' })], context())
    expect(result.state).toBe('field-mismatch')
    expect(result.fieldMismatch).toHaveLength(1)
    expect(result.candidates).toHaveLength(0)
  })

  it('binds a Rule-scoped USER_CONFIRMATION to the rule version and requires evidence after activation', () => {
    const scoped: AcceptanceRequirement = {
      key: 'rule-ok',
      kind: 'USER_CONFIRMATION',
      required_result: 'INFO',
    }
    const base = context({
      ruleId: 'rul_1',
      ruleVersion: 2,
      afterSeq: 15,
      eventsById: new Map([
        ['evt_source', rawEvent({ event_type: 'user.prompt', actor_kind: 'human' })],
        ['evt_change', rawEvent({ event_id: 'evt_change', seq: 20 })],
      ]),
    })
    const bound = row({
      kind: 'USER_CONFIRMATION',
      status: 'INFO',
      confirmed_rule_id: 'rul_1',
      confirmed_rule_version: 2,
    })
    expect(evaluateRequirement(scoped, [bound], base).state).toBe('candidate')

    const wrongVersion = row({
      kind: 'USER_CONFIRMATION',
      status: 'INFO',
      confirmed_rule_id: 'rul_1',
      confirmed_rule_version: 1,
    })
    const mismatch = evaluateRequirement(scoped, [wrongVersion], base)
    expect(mismatch.state).toBe('field-mismatch')
  })

  it('INFO can never satisfy a PASS requirement — only missing/wrong-kind', () => {
    const result = evaluateRequirement(
      REQUIREMENT,
      [row({ status: 'INFO' })],
      context(),
    )
    expect(result.state).toBe('missing')
    expect(result.candidates).toHaveLength(0)
    expect(result.wrongKind[0]?.status).toBe('INFO')
  })
})

describe('readable helpers copy Core exactly', () => {
  it('cycleCutoff uses the Step owner for a Step form and the Task owner otherwise', () => {
    const events = [
      rawEvent({ event_id: 'e1', seq: 4, event_type: 'task.criteria_updated', payload: {} }),
      rawEvent({
        event_id: 'e2',
        seq: 7,
        event_type: 'step.criteria_updated',
        payload: { step_id: STEP },
      }),
      rawEvent({
        event_id: 'e3',
        seq: 9,
        event_type: 'step.transitioned',
        payload: { step_id: STEP, to: 'IN_PROGRESS' },
      }),
      rawEvent({
        event_id: 'e4',
        seq: 11,
        event_type: 'step.transitioned',
        payload: { step_id: 'stp_other', to: 'IN_PROGRESS' },
      }),
    ]
    expect(cycleCutoff(events, STEP, null)).toBe(9)
    // A Task-scoped row on a Task form (stepId null, rowStepId null) uses the task-level query.
    expect(cycleCutoff(events, null, null)).toBe(11)
    // A Step-scoped row on a Task form still uses that Step's cycle.
    expect(cycleCutoff(events, null, STEP)).toBe(9)
  })

  it('matches only the optional fields the requirement actually states', () => {
    expect(matchesRequirementFields(REQUIREMENT, row({ tool_name: 'jest' }), null, null)).toBe(true)
    expect(
      matchesRequirementFields({ ...REQUIREMENT, tool_name: 'pytest' }, row({ tool_name: 'jest' }), null, null),
    ).toBe(false)
  })

  it('never treats a missing change Event as fresh', () => {
    const failures = readableFreshnessFailures(
      row(),
      context({ eventsById: new Map([['evt_source', rawEvent()]]) }),
    )
    expect(failures.map((failure) => failure.condition)).toContain('F5')
  })

  it('copies the applicable-Rule WHERE clause', () => {
    const rule = {
      scope: { kind: 'project', project_id: PROJECT, task_id: null },
      kind: 'ACCEPTANCE',
      enforcement: 'ADVISORY',
    }
    expect(ruleApplies(rule, TASK, PROJECT)).toBe(true)
    expect(ruleApplies(rule, TASK, 'prj_other')).toBe(false)
    expect(
      ruleApplies(
        { scope: { kind: 'global', project_id: null, task_id: null }, kind: 'POLICY', enforcement: 'VERIFY' },
        TASK,
        PROJECT,
      ),
    ).toBe(true)
    expect(
      ruleApplies(
        { scope: { kind: 'global', project_id: null, task_id: null }, kind: 'POLICY', enforcement: 'ADVISORY' },
        TASK,
        PROJECT,
      ),
    ).toBe(false)
  })
})
