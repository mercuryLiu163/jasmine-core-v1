/**
 * Placeholders for inspector domains whose read contract is not frozen yet.
 *
 * These pages exist so that navigation does not dead-end, and so that the UI
 * states the missing dependency instead of approximating it. Each entry names
 * the owning phase and the specific frozen API it waits for; none of them calls
 * a guessed endpoint, and none renders sample rows.
 */

import { Panel, Unavailable } from '../components/States'

interface PendingSpec {
  title: string
  feature: string
  owner: string
  pending: string
  note?: string
}

const SPECS: Record<string, PendingSpec> = {
  interpretation: {
    title: 'Interpretation / Review 人工 reapply',
    feature: '人工 reapply(把已应用的 Resolution 重新应用一次)',
    owner: 'P6-02 后续',
    pending:
      'POST /v1/resolutions/{id}/manual-reapply 自己的载荷/权限表,以及操作者对上一次应用的明确决策',
    note:
      'Correct / Reject / Re-run、Approve / Reject 与 Rule 管理已交付,入口在各自的详情页;' +
      '这里只列仍未交付的写入动作。' +
      'pending 条数一律来自 Core 的 /v1/reviews/pending,不用关键词或文案自行计算。',
  },
  checkpoint: {
    title: 'Checkpoint Compare',
    feature: 'Checkpoint 列表、task_revision/current/fingerprint、freshness/integrity 与比较',
    owner: 'P3 主线(Checkpoint/Resume)',
    pending: '本机版已提供按 Task 的最新 Checkpoint 与按 ID 读取；入口在 Task 页。全局列表尚未提供。',
    note: '旧 checkpoint 只作历史;本页不提供回滚 Current State 的入口。',
  },
  context: {
    title: 'Context Inspector',
    feature: '实际注入的 Context Pack、分区/总 token、rule versions、memory IDs',
    owner: 'P3 主线(Context 生命周期)',
    pending: '已提供按 ID 的 persisted pack 与原文读取；自动 Agent 注入接线尚未开放。',
    note: 'built/persisted 不等于 injected;没有真实 receipt 时显示“注入未验证”,不会在 UI 里重新 build 一份冒充实际文本。',
  },
  memory: {
    title: 'Memory Jobs / Recall',
    feature: 'Memory jobs、retry、bank mapping、recall trace、obsolete/pin/delete',
    owner: 'P4 独立线(Memory Adapter/Jobs/Recall)',
    // The read slice exists (P6-03b1: health/jobs/items/traces) but its dependency is
    // unmerged, so it stays under this heading rather than in the nav. The writes and the
    // management contract are genuinely still missing.
    pending: 'Memory 读取与管理代码已整合，但本机发布关闭引擎、worker 和代理入口。收益验收未完成，P4 实验保持停止。',
    note: 'Memory 是 NON_AUTHORITATIVE;它不会修改 Rule/State,浏览器也不会接触 Hindsight 凭据。',
  },
  validation: {
    title: 'Validation Inspector',
    feature: 'Validation run 列表、run 详情、bundle manifest、安全 artifact 读取、FAIL 分类',
    owner: 'P7 owner(P7-01 最小读取切片)',
    // The read slice is implemented as a deep link (#/validation/runs) but its dependency is
    // unmerged, so it stays under this heading rather than in the nav — exactly like Memory.
    pending: 'Validation 读取已整合，入口在主导航；完整 Scenario Runner 与发布验收尚未完成。',
    note:
      '当前 recorder 不等于完整 API;本页不生成占位 PASS 或伪 Agent 响应。Inspector 按记录原样展示 verdict,' +
      '缺失的 chain 段只标注 missing_reason,不会补写;artifact 内容只作为文本渲染,永不作为 HTML。',
  },
  registry: {
    title: 'Registry 在线状态',
    feature: 'heartbeat、online/capabilities',
    owner: 'P5(Registry 在线能力)',
    pending: '真实心跳戳与能力读取契约',
    note: '静态 hosts/actors 列表不能当作在线状态;超时与未知必须区别于离线。',
  },
  backup: {
    title: 'Backup / Storage / Devices',
    feature: 'Backup、Devices、Storage 统计',
    owner: 'P5 / P7',
    pending: 'Dashboard 读取契约与实际健康戳',
    note: '在契约交付前保持 unavailable,不造统计数字。',
  },
}

export function PendingView({ pendingKey }: { pendingKey: string }): JSX.Element {
  const spec = SPECS[pendingKey]
  if (spec === undefined) {
    return (
      <Panel title="未知的未接入页面">
        <p>
          没有名为 <code>{pendingKey}</code> 的占位页面。可用键:
          {Object.keys(SPECS).map((key) => (
            <code key={key}>{key}</code>
          ))}
        </p>
      </Panel>
    )
  }
  return (
    <div className="stack">
      <Panel title={spec.title}>
        <Unavailable
          feature={spec.feature}
          owner={spec.owner}
          pending={spec.pending}
          note={spec.note}
        />
      </Panel>
    </div>
  )
}

export function pendingKeys(): string[] {
  return Object.keys(SPECS)
}
