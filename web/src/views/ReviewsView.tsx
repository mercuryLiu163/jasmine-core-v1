/**
 * Pending review queue (P6-02 milestone A).
 *
 * `GET /v1/reviews/pending` is the only review collection and it only ever
 * returns `PENDING` rows: there is no "all reviews" endpoint, so this view never
 * claims to show history. It pages by `after_id` and returns `next_after_id` with
 * no total, so the footer states the loaded count and nothing more.
 *
 * When Core cannot produce a preview for a pending review it returns
 * `preview_error` on the row itself; that is shown as a distinct badge rather
 * than as an empty plan, because a review whose plan can no longer be produced is
 * exactly the case an operator must not mistake for "nothing to do".
 */

import { useMemo } from 'react'

import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Review } from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { afterIdLoader, usePagedResource } from '../app/usePagedResource'
import { Pager } from '../components/Pager'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner, Unavailable } from '../components/States'
import { SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'

const LIMIT = DEFAULT_OBJECT_LIMIT

export function ReviewsView({
  projectId,
  taskId,
}: {
  projectId: string | null
  taskId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const reviews = usePagedResource(
    afterIdLoader<Review>({
      limit: LIMIT,
      fetch: async (afterId, limit, signal) => {
        const page = await endpoints.listPendingReviews(
          { project_id: projectId, task_id: taskId, after_id: afterId, limit },
          signal,
        )
        return { items: page.items, nextAfterId: page.next_after_id }
      },
    }),
    [projectId, taskId],
  )

  const scope = useMemo(() => {
    const parts: string[] = []
    if (taskId) parts.push(`task ${taskId}`)
    if (projectId) parts.push(`project ${projectId}`)
    return parts.length > 0 ? parts.join(' · ') : '全部(无过滤)'
  }, [projectId, taskId])

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Pending Reviews</span>
      </nav>

      <Panel
        title={`待审批 Review(已载入 ${reviews.items.length} 条)`}
        actions={
          <button type="button" onClick={reviews.reload} disabled={reviews.loading}>
            重新读取
          </button>
        }
      >
        <p className="state state--meta">
          读取范围:<strong>{scope}</strong>;<code>GET /v1/reviews/pending</code> 只返回 PENDING,
          按 <code>after_id</code> 游标分页。Core 不返回总数,所以这里只声明已载入条数,
          不会把「没有更多待审批」写成「全部已处理」。
        </p>
        <div className="filters">
          <a href={hrefFor({ name: 'reviews', projectId: null, taskId: null })}>全部</a>
          {projectId ? (
            <a href={hrefFor({ name: 'reviews', projectId, taskId })}>仅当前 Project</a>
          ) : null}
        </div>

        {reviews.loading && reviews.items.length === 0 ? <Loading what="Review" /> : null}
        {reviews.error && reviews.items.length === 0 ? (
          <ErrorPanel error={reviews.error} what="Review" onRetry={reviews.reload} />
        ) : null}
        {!reviews.loading && reviews.error === null && reviews.items.length === 0 ? (
          <Empty
            what="待审批 Review"
            hint="在该范围内没有待审批项;这不等于没有 Review 历史,也不等于解释链健康。"
          />
        ) : null}

        {reviews.items.length > 0 ? (
          <ul className="cards">
            {reviews.items.map((review) => (
              <li className="card" key={review.review_id}>
                <h3>
                  <a href={sourceHref.review(review.review_id)}>
                    <code>{review.review_id}</code>
                  </a>
                </h3>
                <p>
                  <StatusBadge kind="review" status={review.status} /> revision {review.revision} ·{' '}
                  {formatTimestamp(review.created_at)}
                </p>
                <p>
                  <strong>计划</strong>{' '}
                  {review.preview ? (
                    <>
                      <StatusBadge kind="disposition" status={review.preview.plan.disposition} /> ·{' '}
                      {review.preview.plan.items.length} 项
                    </>
                  ) : review.preview_error ? (
                    <span className="badge badge--bad" title="Core 未能给出可审批的计划">
                      ? 预览冲突 <code>{review.preview_error.code}</code>
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </p>
                <p className="muted">
                  {review.resolution.result.reasons.join(' · ') || '—'}
                </p>
                <p>
                  <SourceLink
                    kind="event"
                    id={review.resolution.source_event_id}
                    label="source Event"
                  />{' '}
                  <a href={sourceHref.interpretation(review.resolution.interpretation_id)}>
                    <code>{review.resolution.interpretation_id}</code>
                  </a>{' '}
                  <a href={sourceHref.resolution(review.resolution.resolution_id)}>
                    <code>{review.resolution.resolution_id}</code>
                  </a>
                </p>
              </li>
            ))}
          </ul>
        ) : null}

        <Pager paged={reviews} noun="Review" limit={LIMIT} />
        {reviews.loadedAt ? (
          <StaleBanner loadedAt={reviews.loadedAt} scope={`Pending Reviews(${scope})`} />
        ) : null}
        <p className="state state--meta">
          读取详情时会经过 Core 的 <code>maintenance.recover</code>(可能完成待处理的 rerun),
          因此「GET」不保证零副作用;本页只发 GET,不由前端推断审批状态。
        </p>
      </Panel>

      <Panel title="本切片未交付">
        <Unavailable
          feature="Approve / Reject 与人工 reapply"
          owner="P6-02 里程碑 B"
          pending="reviews:manage 写入契约、真实 human 身份与审计读回 Gate"
          tone="forbidden"
          note="本页不提供审批按钮;按钮点击不能替代 Core 对 human actor 与来源链的校验。"
        />
      </Panel>
    </div>
  )
}
