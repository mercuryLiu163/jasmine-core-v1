import type { ContextPack } from '../api/types'
import { SourceLink } from '../components/Trace'
import { isCoreId } from '../lib/ids'

function integer(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function omission(value: unknown): value is {
  section: string; item_id: string; reason: string; standalone_tokens: number; source_ids: string[]
} {
  if (value === null || typeof value !== 'object') return false
  const row = value as Record<string, unknown>
  return typeof row.section === 'string' && typeof row.item_id === 'string' &&
    typeof row.reason === 'string' && integer(row.standalone_tokens) &&
    Array.isArray(row.source_ids) && row.source_ids.length > 0 &&
    row.source_ids.every(id => typeof id === 'string' && isCoreId(id, 'evt'))
}

/** Historical pack metadata describes rendered bytes, never current admission/injection. */
export function ContextBudget({ pack }: { pack: ContextPack }): JSX.Element {
  const count = integer(pack.token_count) ? pack.token_count : null
  const limit = integer(pack.tokenizer?.maximum_tokens) && pack.tokenizer.maximum_tokens > 0
    ? pack.tokenizer.maximum_tokens : null
  const known = count !== null && limit !== null
  const state = !known ? 'unknown' : count > limit ? 'over' : 'within'
  const rows = Array.isArray(pack.omissions) && pack.omissions.every(omission) ? pack.omissions : null
  const budgetRows = rows?.filter(row => row.reason === 'optional_budget')
  return (
    <div className="stack" data-testid="context-budget" data-status={state}>
      <p data-testid="context-budget-count">
        构建记录 token: {count ?? '未知'} / {limit ?? '未知'}。
        仅表示已渲染 pack 的编码计数，不表示模型完整提示额度。
      </p>
      <p className="state state--meta" data-testid="context-injection-boundary">
        这是构建时的历史记录；重新读取不证明当前可注入。本页没有 Agent 实际接收回执，构建不等于注入。
      </p>
      {state === 'unknown' ? (
        <div className="state state--error" role="alert" data-testid="context-budget-warning">
          预算计数或上限记录缺失／异常，无法判断预算状态；不能当作 0 或预算正常。
        </div>
      ) : state === 'over' ? (
        <div className="state state--error" role="alert" data-testid="context-budget-warning">
          记录超预算：{count} &gt; {limit}。这份记录不能作为当前可注入或构建成功的证明。
        </div>
      ) : null}
      {rows === null ? (
        <div className="state state--error" role="alert" data-testid="context-omissions-warning">
          省略记录缺失／异常，无法判定；请核对 Core 的原始 omissions。
        </div>
      ) : budgetRows && budgetRows.length > 0 ? (
        <div className="state state--meta" role="alert" data-testid="context-budget-omissions">
          <p>可选内容预算不足，已按整项省略 {budgetRows.length} 项。原因可能为分区上限或整包额度；必需内容不因可选内容预算而截断。</p>
          <ul>{budgetRows.map((row, index) => (
            <li key={index} data-testid="context-budget-omission">
              <code>{row.item_id}</code> · {row.section} · {row.reason} · standalone_tokens = {row.standalone_tokens}
              {' '}来源：{row.source_ids.map(id => <SourceLink key={id} kind="event" id={id} />)}
            </li>
          ))}</ul>
        </div>
      ) : <p data-testid="context-no-budget-omissions">Core 记录中没有 optional_budget 省略项；其他原因见 omissions 原件。</p>}
    </div>
  )
}
