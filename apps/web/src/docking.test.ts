import { describe, expect, it } from 'vitest'
import { CENTRE, type Rect, dropAt, inCentre, stackTarget } from './docking'

/* The docking gesture's one decision: did that drop mean "put it here" or
 * "make it a tab of that".
 *
 * The failure worth guarding is not a crash. It is a panel quietly swallowing
 * another because a drop near an edge counted as its middle — the operator
 * meant alongside and got on top of, and the only evidence is a panel that
 * vanished into a tab bar they were not looking at.
 */

const chart: Rect = { panel_id: 'chart', x: 0, y: 0, width: 8, height: 8 }
const risk: Rect = { panel_id: 'risk', x: 8, y: 0, width: 4, height: 8 }

describe('dropAt', () => {
  it('a drop on open grid is a move', () => {
    expect(dropAt(3, 20, [chart, risk])).toEqual({ kind: 'move', x: 3, y: 20 })
  })

  it('a drop in the middle of a panel is a tab', () => {
    expect(dropAt(4, 4, [chart])).toEqual({ kind: 'stack', onto: 'chart' })
  })

  it('a drop on a panel edge is a move, not a tab', () => {
    /* The ring is the "alongside" target. If it stacked, tabbing would be the
     * outcome of any sloppy drop. */
    expect(dropAt(0, 0, [chart]).kind).toBe('move')
    expect(dropAt(7, 7, [chart]).kind).toBe('move')
  })

  it('picks the panel drawn on top when rectangles overlap', () => {
    const under: Rect = { panel_id: 'under', x: 0, y: 0, width: 8, height: 8 }
    const over: Rect = { panel_id: 'over', x: 0, y: 0, width: 8, height: 8 }
    expect(dropAt(4, 4, [under, over])).toEqual({ kind: 'stack', onto: 'over' })
  })

  it('with nothing to land on, every drop is a move', () => {
    expect(dropAt(4, 4, []).kind).toBe('move')
  })

  it('a drop past every panel is a move', () => {
    expect(dropAt(11, 40, [chart, risk]).kind).toBe('move')
  })

  it('reaches the second panel as readily as the first', () => {
    expect(dropAt(10, 4, [chart, risk])).toEqual({ kind: 'stack', onto: 'risk' })
  })
})

describe('inCentre', () => {
  it('is false outside the rectangle entirely', () => {
    expect(inCentre(chart, 20, 20)).toBe(false)
    expect(inCentre(chart, -1, 4)).toBe(false)
  })

  it('treats the far edges as outside, so neighbours do not both claim a point', () => {
    /* chart spans x 0..7 and risk starts at 8. If chart claimed x=8 the two
     * would disagree about who owns the boundary column. */
    expect(inCentre({ ...chart, width: 8 }, 8, 4)).toBe(false)
  })

  it('widens and narrows with the ratio', () => {
    expect(inCentre(chart, 2, 4, 0.9)).toBe(true)
    expect(inCentre(chart, 2, 4, 0.1)).toBe(false)
  })

  it('the default ratio puts the boundary a quarter in from each edge', () => {
    expect(CENTRE).toBe(0.5)
    // width 8, so the middle runs from x=2 to x=6.
    expect(inCentre(chart, 1, 4)).toBe(false)
    expect(inCentre(chart, 2, 4)).toBe(true)
    expect(inCentre(chart, 5, 4)).toBe(true)
    expect(inCentre(chart, 6, 4)).toBe(false)
  })

  it('a panel one unit across has no middle to speak of and refuses tabs', () => {
    /* Better than letting a sliver swallow a panel dropped anywhere near it. */
    const sliver: Rect = { panel_id: 'sliver', x: 0, y: 0, width: 1, height: 1 }
    expect(inCentre(sliver, 0, 0)).toBe(false)
  })
})

describe('stackTarget', () => {
  it('names the panel a drop would tab onto', () => {
    expect(stackTarget(4, 4, [chart])).toBe('chart')
  })

  it('is null where the drop would be a move', () => {
    expect(stackTarget(3, 20, [chart])).toBe(null)
  })

  it('never disagrees with the drop it previews', () => {
    /* The indicator and the outcome read one rule. Two rules drift. */
    for (let x = 0; x < 12; x += 1) {
      for (let y = 0; y < 10; y += 1) {
        const drop = dropAt(x, y, [chart, risk])
        const preview = stackTarget(x, y, [chart, risk])
        expect(preview).toBe(drop.kind === 'stack' ? drop.onto : null)
      }
    }
  })
})
