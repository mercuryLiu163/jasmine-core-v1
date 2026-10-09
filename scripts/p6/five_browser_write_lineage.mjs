/** Acceptance-only predicates; no Core or Dashboard product implementation. */
export function sourceApplicationBound(applications, sourceId, resolutionId, resolution) {
  if (!Array.isArray(applications) || !sourceId || !resolutionId || !resolution) return false
  if (resolution.resolution_id !== resolutionId || resolution.source_event_id !== sourceId) return false
  const rows = applications.filter(row => row?.source_event_id === sourceId)
  return rows.length === 1 && rows[0].canonical_resolution_id === resolutionId
}

// Core review commands name the source in payload; their top-level source_event_id is null.
export function reviewRuleOriginBound(rule, command, sourceId, reviewId, resolution, resolutionId) {
  return Boolean(rule && command && sourceId && reviewId && resolution && resolutionId &&
    command.event_id && command.event_type === 'review.approved' &&
    rule.origin_event_id === command.event_id && command.source_event_id === null &&
    command.payload?.source_event_id === sourceId && command.payload?.review_id === reviewId &&
    resolution.resolution_id === resolutionId && resolution.source_event_id === sourceId &&
    rule.scope?.project_id === command.project_id && rule.scope?.task_id === command.task_id)
}
