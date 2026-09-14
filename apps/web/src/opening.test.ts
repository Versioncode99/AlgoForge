import { describe, expect, it } from 'vitest'
import { chatOpening } from './opening'
import { parse } from './route'

/* Which subject a link gives a new conversation, and why exactly one.
 *
 * A thread opened "about" three things at once is a thread about nothing in
 * particular. The panel already inherits the workspace's own facets when a link
 * names nothing, so the only job here is to pick one deterministically.
 */

describe('the subject a link carries', () => {
  it('takes the strategy, with its readable label', () => {
    expect(chatOpening({ strategy: 's_abc', label: 'NQ ORB' })).toEqual({
      kind: 'strategy',
      ref: 's_abc',
      label: 'NQ ORB',
    })
  })

  it('falls back to the id when the link carries no label', () => {
    expect(chatOpening({ account: 'a1' })).toEqual({ kind: 'account', ref: 'a1', label: 'a1' })
  })

  it('is nothing when the link names nothing, so the workspace fallback stands', () => {
    expect(chatOpening({})).toBeUndefined()
    expect(chatOpening({ pane: 'gates' })).toBeUndefined()
  })

  it('ignores an empty value rather than attaching a chip that names nothing', () => {
    expect(chatOpening({ strategy: '' })).toBeUndefined()
  })

  it('picks one subject, most specific first', () => {
    expect(chatOpening({ strategy: 's1', dataset: 'nq', account: 'a1' })?.kind).toBe('strategy')
    expect(chatOpening({ dataset: 'nq', account: 'a1' })?.kind).toBe('account')
    expect(chatOpening({ dataset: 'nq', campaign: 'c1' })?.kind).toBe('dataset')
  })

  it('reads what the Ask-about-this link actually produces', () => {
    const route = parse('#assistant?strategy=s1&label=ES%20breakout%20%C2%B7%205m')
    expect(route.path).toBe('assistant')
    expect(chatOpening(route.params)).toEqual({
      kind: 'strategy',
      ref: 's1',
      label: 'ES breakout · 5m',
    })
  })
})
