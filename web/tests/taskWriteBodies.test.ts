/**
 * P6-02b write bodies, asserted against the frozen route contract (spec §2/§3).
 *
 * Core validates each of the seven routes with an exact allow-list, so a body is either exactly
 * what the route accepts or a `400 invalid_request` naming the offender. The builders exist to
 * make the two expensive mistakes loud in the suite:
 *
 * * sending `expected_revision` on Task create, which Core explicitly refuses; and
 * * forgetting `expected_revision` on a state route, which turns a read basis into a blind write.
 *
 * `event_id` is added by `useWriteAction`, not by these builders, so a retry can reuse it.
 */

import { describe, expect, it } from 'vitest'

import {
  ALLOWED,
  acceptBody,
  criteriaBody,
  only,
  stepCreateBody,
  stepCriteriaBody,
  stepTransitionBody,
  taskCreateBody,
  transitionBody,
} from '../src/api/taskWrites'

describe('the builders can only emit fields the route lists', () => {
  it('Task create has the spec’s allowed set and never sends expected_revision', () => {
    expect([...ALLOWED.task_create].sort()).toEqual(
      [
        'acceptance_criteria',
        'actor_id',
        'description',
        'event_id',
        'host_id',
        'occurred_at',
        'project_id',
        'source_event_id',
        'source_system',
        'title',
      ].sort(),
    )
    const body = taskCreateBody({ title: 't', project_id: 'prj_x', host_id: 'hst_x' })
    expect(Object.keys(body).sort()).toEqual(['host_id', 'project_id', 'title'])
    expect(body).not.toHaveProperty('expected_revision')
    // A caller trying to add one is a bug, not a value to drop silently.
    expect(() =>
      taskCreateBody({
        title: 't',
        project_id: 'prj_x',
        host_id: 'hst_x',
        expected_revision: 1,
      } as unknown as Parameters<typeof taskCreateBody>[0]),
    ).toThrow(/does not accept expected_revision/)
  })

  it('every state route carries expected_revision and host_id', () => {
    const bodies = [
      stepCreateBody({ title: 's', expected_revision: 3, host_id: 'hst_x' }),
      stepTransitionBody({ status: 'IN_PROGRESS', expected_revision: 3, host_id: 'hst_x' }),
      transitionBody({ status: 'BLOCKED', reason: 'r', expected_revision: 12, host_id: 'hst_x' }),
      criteriaBody({
        acceptance_criteria: { requirements: [] },
        expected_revision: 12,
        host_id: 'hst_x',
      }),
      acceptBody({ expected_revision: 12, host_id: 'hst_x' }),
      stepCriteriaBody({
        acceptance_criteria: { requirements: [] },
        expected_revision: 3,
        host_id: 'hst_x',
      }),
    ]
    for (const body of bodies) {
      expect(body['expected_revision']).toBeTypeOf('number')
      expect(body['host_id']).toBe('hst_x')
    }
  })

  it('Task accept accepts no reason; a reason on it is refused by the builder', () => {
    expect(Object.keys(acceptBody({ expected_revision: 1, host_id: 'hst_x' })).sort()).toEqual([
      'expected_revision',
      'host_id',
    ])
    expect(() =>
      acceptBody({ expected_revision: 1, host_id: 'hst_x', reason: 'x' } as unknown as Parameters<
        typeof acceptBody
      >[0]),
    ).toThrow(/does not accept reason/)
  })

  it('drops absent optionals rather than sending null, and keeps present ones', () => {
    const sparse = taskCreateBody({ title: 't', project_id: 'prj_x', host_id: 'hst_x', description: undefined })
    expect(sparse).not.toHaveProperty('description')
    const full = taskCreateBody({
      title: 't',
      project_id: 'prj_x',
      host_id: 'hst_x',
      description: 'd',
      acceptance_criteria: { requirements: [] },
      source_system: 'src',
      source_event_id: 'src-1',
    })
    expect(Object.keys(full).sort()).toEqual(
      ['acceptance_criteria', 'description', 'host_id', 'project_id', 'source_event_id', 'source_system', 'title'].sort(),
    )
  })

  it('`only` refuses an unknown route name and an unknown field', () => {
    expect(() => only('task_create', { surprise: 1 })).toThrow(/does not accept surprise/)
    expect(() => only('not_a_route' as never, {})).toThrow()
  })

  it('the seven allowed sets are exactly the frozen field sets', () => {
    expect(Object.keys(ALLOWED).sort()).toEqual(
      [
        'step_create',
        'step_criteria',
        'step_transition',
        'task_accept',
        'task_create',
        'task_criteria',
        'task_transition',
      ].sort(),
    )
    expect([...ALLOWED.step_create].sort()).toEqual(
      ['acceptance_criteria', 'description', 'event_id', 'expected_revision', 'host_id', 'title'].sort(),
    )
    expect([...ALLOWED.step_transition].sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id', 'reason', 'status'].sort(),
    )
    expect([...ALLOWED.task_transition].sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id', 'reason', 'status'].sort(),
    )
    expect([...ALLOWED.task_criteria].sort()).toEqual(
      ['acceptance_criteria', 'event_id', 'expected_revision', 'host_id'].sort(),
    )
    expect([...ALLOWED.task_accept].sort()).toEqual(
      ['event_id', 'expected_revision', 'host_id'].sort(),
    )
    expect([...ALLOWED.step_criteria].sort()).toEqual(
      ['acceptance_criteria', 'event_id', 'expected_revision', 'host_id'].sort(),
    )
    // `idempotency_key` is unknown on all seven: the replay key is `event_id`.
    for (const allowed of Object.values(ALLOWED)) {
      expect(allowed).not.toContain('idempotency_key')
    }
  })
})
