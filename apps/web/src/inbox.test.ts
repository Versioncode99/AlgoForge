import { describe, expect, it } from 'vitest'
import { describeItem, destination, took, type InboxItem } from './inbox'

const item = (over: Partial<InboxItem> = {}): InboxItem => ({
  item_id: 'inb_1',
  job_id: 'job_1',
  kind: 'backtest',
  label: 'momentum · 60,000 bars · synthetic',
  outcome: 'done',
  error: '',
  refs: { strategy_id: 's1' },
  seconds: 12.5,
  arrived_at: '2026-09-13T12:00:00+00:00',
  read_at: '',
  dismissed_at: '',
  ...over,
})

describe('where an item opens', () => {
  it('sends a backtest to the strategy it ran on', () => {
    expect(destination(item())).toBe('#strategies?strategy=s1&pane=summary')
  })

  it('sends a surface to its strategy without claiming a pane that does not exist', () => {
    expect(destination(item({ kind: 'sweep_surface' }))).toBe('#strategies?strategy=s1')
  })

  it('sends a prop matrix to the grid rather than to one strategy', () => {
    expect(destination(item({ kind: 'prop_matrix', refs: {} }))).toBe('#prop')
  })

  it('falls back to the strategy for a kind it has no rule for', () => {
    expect(destination(item({ kind: 'something_new' }))).toBe('#strategies?strategy=s1')
  })

  it('uses an account when that is what the job was about', () => {
    expect(destination(item({ kind: 'something_new', refs: { account_id: 'a1' } })))
      .toBe('#desk?account=a1')
  })

  it('is empty when the item names nothing to open', () => {
    expect(destination(item({ kind: 'something_new', refs: {} }))).toBe('')
  })
})

describe('saying how long it took', () => {
  it('rounds to the unit a person would use', () => {
    expect(took(0.4)).toBe('under a second')
    expect(took(12.5)).toBe('13s')
    expect(took(300)).toBe('5 min')
    expect(took(7200)).toBe('2.0 h')
  })
})

describe('what an item says', () => {
  it('says it finished, and not what it found', () => {
    const line = describeItem(item())
    expect(line).toBe('Finished in 13s.')
    expect(line).not.toContain('momentum')
  })

  it('carries the reason a failure failed', () => {
    expect(describeItem(item({ outcome: 'failed', error: 'ValueError: no bars' })))
      .toContain('ValueError: no bars')
  })

  it('does not invent a reason when there is none', () => {
    expect(describeItem(item({ outcome: 'failed', error: '' }))).toBe('Failed.')
  })

  it('distinguishes cancelled from failed', () => {
    expect(describeItem(item({ outcome: 'cancelled' }))).toBe('Cancelled after 13s.')
  })
})
