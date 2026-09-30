import { describe, expect, it } from 'vitest'

import { hrefFor, parseHash, sourceHref } from '../src/app/router'

describe('parseHash', () => {
  it('treats an empty hash as the overview', () => {
    expect(parseHash('')).toEqual({ name: 'overview' })
    expect(parseHash('#/')).toEqual({ name: 'overview' })
    expect(parseHash('#/overview')).toEqual({ name: 'overview' })
  })

  it('parses object routes', () => {
    expect(parseHash('#/project/prj_1')).toEqual({ name: 'project', projectId: 'prj_1' })
    expect(parseHash('#/task/tsk_1')).toEqual({ name: 'task', taskId: 'tsk_1', tab: 'board' })
    expect(parseHash('#/task/tsk_1/evidence')).toEqual({
      name: 'task',
      taskId: 'tsk_1',
      tab: 'evidence',
    })
    expect(parseHash('#/event/evt_1')).toEqual({ name: 'event', eventId: 'evt_1' })
  })

  it('parses query-scoped routes', () => {
    expect(parseHash('#/conversation?project_id=prj_1&session_id=ses_1')).toEqual({
      name: 'conversation',
      projectId: 'prj_1',
      taskId: null,
      sessionId: 'ses_1',
    })
    expect(parseHash('#/evidence?evidence_id=evd_1&task_id=tsk_1')).toEqual({
      name: 'evidence',
      evidenceId: 'evd_1',
      taskId: 'tsk_1',
    })
  })

  it('decodes percent-encoded ids', () => {
    expect(parseHash('#/project/prj%5F1')).toEqual({ name: 'project', projectId: 'prj_1' })
  })

  it('reports an unknown route instead of silently showing the overview', () => {
    const route = parseHash('#/nonsense/thing')
    expect(route.name).toBe('unknown')
    expect(parseHash('#/task')).toMatchObject({ name: 'unknown' })
    expect(parseHash('#/task/tsk_1/not-a-tab')).toMatchObject({ name: 'unknown' })
  })
})

describe('hrefFor', () => {
  it('round-trips every route shape', () => {
    const routes = [
      { name: 'overview' as const },
      { name: 'project' as const, projectId: 'prj_1' },
      { name: 'task' as const, taskId: 'tsk_1', tab: 'history' as const },
      { name: 'conversation' as const, projectId: 'prj_1', taskId: null, sessionId: 'ses_1' },
      { name: 'rules' as const, projectId: null, taskId: null, ruleId: 'rul_1' },
      { name: 'evidence' as const, evidenceId: 'evd_1', taskId: 'tsk_1' },
      { name: 'event' as const, eventId: 'evt_1' },
      { name: 'pending' as const, key: 'memory' },
    ]
    for (const route of routes) {
      expect(parseHash(hrefFor(route))).toEqual(route)
    }
  })

  it('omits the default board tab and empty query values', () => {
    expect(sourceHref.task('tsk_1')).toBe('#/task/tsk_1')
    expect(sourceHref.conversation(null)).toBe('#/conversation')
  })
})
