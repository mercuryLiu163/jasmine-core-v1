import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ContextPack } from '../src/api/types'
import { ContextBudget } from '../src/views/ContextBudget'

const SOURCE = 'evt_01M4B6VGC29ESRDRQR62DX8AK8'
// Explicit component inputs, never real build or injection receipts.
function component(values: Record<string, unknown> = {}): ContextPack {
  return { token_count: 400, tokenizer: { maximum_tokens: 2500 }, omissions: [], ...values } as unknown as ContextPack
}
describe('Context historical budget metadata', () => {
  it('shows record counts without claiming current admission or injection', () => {
    render(<ContextBudget pack={component()} />)
    expect(screen.getByTestId('context-budget').getAttribute('data-status')).toBe('within')
    expect(screen.getByTestId('context-budget-count').textContent).toContain('400 / 2500')
    expect(screen.getByTestId('context-injection-boundary').textContent).toContain('构建不等于注入')
    expect(screen.getByTestId('context-injection-boundary').textContent).toContain('不证明当前可注入')
  })
  it('shows an omission with the exact source instead of inferring truncation or injection', () => {
    render(<ContextBudget pack={component({ omissions: [{ section: 'RESUME', item_id: 'note:cp_component', reason: 'optional_budget', standalone_tokens: 450, source_ids: [SOURCE] }] })} />)
    const row = screen.getByTestId('context-budget-omission')
    expect(row.textContent).toContain('optional_budget')
    expect(row.textContent).toContain('450')
    expect(row.querySelector('a')?.getAttribute('href')).toBe('#/event/' + SOURCE)
    expect(screen.getByTestId('context-budget-omissions').textContent).toContain('分区上限或整包额度')
  })
  it('flags an over-limit historical component without presenting it as a successful current pack', () => {
    render(<ContextBudget pack={component({ token_count: 2501 })} />)
    expect(screen.getByTestId('context-budget').getAttribute('data-status')).toBe('over')
    expect(screen.getByTestId('context-budget-warning').textContent).toContain('2501 > 2500')
  })
  it.each([null, undefined, -1, '400', Number.NaN, Number.POSITIVE_INFINITY, .5])('refuses malformed count %s', value => {
    render(<ContextBudget pack={component({ token_count: value })} />)
    expect(screen.getByTestId('context-budget').getAttribute('data-status')).toBe('unknown')
    expect(screen.getByTestId('context-budget-count').textContent).toContain('未知 / 2500')
  })
  it.each([null, 0, -1, '2500', Number.NaN, .5])('refuses malformed limit %s', value => {
    render(<ContextBudget pack={component({ tokenizer: { maximum_tokens: value } })} />)
    expect(screen.getByTestId('context-budget').getAttribute('data-status')).toBe('unknown')
  })
  it.each([null, {}, [null], [{ reason: 'optional_budget' }]])('reports unknown omission metadata %s', value => {
    render(<ContextBudget pack={component({ omissions: value })} />)
    expect(screen.getByTestId('context-omissions-warning')).toBeTruthy()
    expect(screen.queryByTestId('context-no-budget-omissions')).toBeNull()
  })
})
