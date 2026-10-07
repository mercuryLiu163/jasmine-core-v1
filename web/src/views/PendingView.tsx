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
    title: 'Interpretation / Review 写入',
    feature: 'Correct / Reject / Re-run、Approve / Reject、人工 reapply',
    owner: 'P6-02 里程碑 B',
    pending:
      '写入通过真实 human actor/来源链、expected_revision 冲突重审、幂等 command Event 与审计读回的独立 Gate',
    note:
      'P2 的读取契约已合并,解释列表/详情与待审批 Review 已是只读视图;这里只列仍未交付的写入动作。' +
      'pending 条数一律来自 Core 的 /v1/reviews/pending,不用关键词或文案自行计算。',
  },
  checkpoint: {
    title: 'Checkpoint Compare',
    feature: 'Checkpoint 列表、task_revision/current/fingerprint、freshness/integrity 与比较',
    owner: 'P3 主线(Checkpoint/Resume)',
    pending: 'checkpoint 读取 API 与服务器侧 compare 结果',
    note: '旧 checkpoint 只作历史;本页不提供回滚 Current State 的入口。',
  },
  context: {
    title: 'Context Inspector',
    feature: '实际注入的 Context Pack、分区/总 token、rule versions、memory IDs',
    owner: 'P3 主线(Context 生命周期)',
    pending: 'persisted pack 读取、exact rendered content、注入 attempt/receipt 契约',
    note: 'built/persisted 不等于 injected;没有真实 receipt 时显示“注入未验证”,不会在 UI 里重新 build 一份冒充实际文本。',
  },
  memory: {
    title: 'Memory Jobs / Recall',
    feature: 'Memory jobs、retry、bank mapping、recall trace、obsolete/pin/delete',
    owner: 'P4 独立线(Memory Adapter/Jobs/Recall)',
    pending: 'jobs/detail/retry、bank 映射、管理权限与审计契约',
    note: 'Memory 是 NON_AUTHORITATIVE;它不会修改 Rule/State,浏览器也不会接触 Hindsight 凭据。',
  },
  validation: {
    title: 'Validation Inspector',
    feature: 'Validation run 列表、run 详情、bundle manifest、安全 artifact 读取、FAIL 分类',
    owner: 'P7 owner(P7-01 最小读取切片)',
    pending: '/v1/validation/runs 与 run 详情/verdict、受权 artifact 读取',
    note: '当前 recorder 不等于完整 API;本页不会生成占位 PASS 或伪 Agent 响应。',
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
