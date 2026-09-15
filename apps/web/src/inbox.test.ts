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
    expect(destination(item())).toBe('#strategies?tab=library&strategy=s1&pane=summary')
  })

  it('sends a surface to its strategy without claiming a pane that does not exist', () => {
    expect(destination(item({ kind: 'sweep_surface' }))).toBe('#strategies?tab=library&strategy=s1')
  })

  it('sends a prop matrix to the screen that runs one', () => {
    /* This asserted `'#prop'`, and `prop` is a panel kind rather than a route:
     * the Open button on a finished matrix resolved to no destination and the
     * shell sent the reader to Home. The string was the whole assertion, so
     * nothing about it could fail. */
    expect(destination(item({ kind: 'prop_matrix', refs: {} }))).toBe('#propdesk?tab=simulation')
  })

  it('sends a mission to the missions screen, not to the action registry', () => {
    // `#actions` landed in Diagnostics, which lists the verbs an assistant may
    // call -- a different screen from the mission that just finished running.
    expect(destination(item({ kind: 'mission', refs: {} }))).toBe('#campaigns?tab=automation')
  })

  it('sends a specialist task to the screen that shows specialists', () => {
    expect(destination(item({ kind: 'agent', refs: {} }))).toBe('#settings?tab=diagnostics')
  })

  it('falls back to the strategy for a kind it has no rule for', () => {
    expect(destination(item({ kind: 'something_new' })))
      .toBe('#strategies?tab=library&strategy=s1')
  })

  it('uses an account when that is what the job was about', () => {
    expect(destination(item({ kind: 'something_new', refs: { account_id: 'a1' } })))
      .toBe('#propdesk?tab=accounts&account=a1')
  })

  it('is empty when the item names nothing to open', () => {
    expect(destination(item({ kind: 'something_new', refs: {} }))).toBe('')
  })

  it('never offers a link this product has to translate to understand', () => {
    /* Every destination names a current route and tab. A legacy spelling would
     * still arrive somewhere, which is exactly why the broken one went
     * unnoticed: `#prop` looked like the others and was not a route at all.
     * `tests/modes/test_manifest.py::test_every_place_the_inbox_can_send_somebody_exists`
     * resolves each of these against the shipped manifest. */
    const kinds = ['backtest', 'sweep_surface', 'surface', 'prop_matrix', 'mission', 'agent']
    for (const kind of kinds) {
      const link = destination(item({ kind, refs: { strategy_id: 's1' } }))
      expect(link, `${kind} opens nowhere`).not.toBe('')
      expect(link, `${kind} names no tab`).toMatch(/^#[a-z]+\?tab=[a-z]+/)
    }
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
