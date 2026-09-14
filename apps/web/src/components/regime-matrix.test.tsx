import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RegimeMatrix, fillFor, step, type RegimeCell } from './RegimeMatrix'
import { RegimeReading, type Reading } from './RegimeReading'

/* Two things are being held down here, and neither is "does it render".
 *
 * The grid: that shade means magnitude on a scale anchored at zero, and that a
 * cell nobody traded never gets painted as a measured one. A heatmap anchored
 * at the extremes present is the common implementation and it lies — on an
 * all-losing strategy it paints the least-bad cell green.
 *
 * The reading: that the component renders findings and does not compose them.
 * Every sentence is produced by arithmetic on the server; a surface that wrote
 * its own prose from the cells would be a second, unversioned interpretation of
 * the same evidence, and the two would drift.
 */

function cell(regime: string, over: Partial<RegimeCell> = {}): RegimeCell {
  return {
    regime,
    label: regime,
    trade_count: 60,
    net_pnl: 0,
    gross_pnl: 0,
    win_rate: 0.5,
    average_trade: 0,
    bar_exposure: 0.25,
    trade_share: 0.25,
    insufficient: false,
    note: '',
    ...over,
  }
}

const GRID = [
  cell('BULL_LOW', { net_pnl: 10_000, average_trade: 166 }),
  cell('BULL_HIGH', { net_pnl: -2_000, average_trade: -33 }),
  cell('BEAR_LOW', { net_pnl: 500, average_trade: 8 }),
  cell('BEAR_HIGH', { net_pnl: -6_000, average_trade: -100 }),
]

// ── the scale ────────────────────────────────────────────────────────────────

describe('step', () => {
  it('anchors at zero, not at the smallest value present', () => {
    /* Anchoring at the minimum would make the least-bad losing cell neutral,
     * and on an all-losing strategy it would paint one of them green. Zero is
     * the only anchor that means the same thing in every dataset. */
    expect(step(-100, 1000)).toBeLessThan(0)
    expect(step(-1000, 1000)).toBeLessThan(0)
    expect(step(0, 1000)).toBe(0)
  })

  it('gives the strongest cell the darkest step, in either direction', () => {
    expect(step(1000, 1000)).toBe(5)
    expect(step(-1000, 1000)).toBe(-5)
  })

  it('ranks by magnitude between the ends', () => {
    const near = step(900, 1000) ?? 0
    const middle = step(500, 1000) ?? 0
    const far = step(100, 1000) ?? 0
    expect(near).toBeGreaterThan(middle)
    expect(middle).toBeGreaterThan(far)
    expect(far).toBeGreaterThan(0)
  })

  it('has no step for a cell with no value', () => {
    // Distinct from zero: "nothing happened here" and "this netted zero" are
    // different facts and must not paint the same.
    expect(step(null, 1000)).toBeNull()
    expect(fillFor(null)).not.toBe(fillFor(0))
  })

  it('paints nothing when every cell is zero', () => {
    expect(step(0, 0)).toBe(0)
    expect(fillFor(step(0, 0))).toBe('var(--heat-zero)')
  })

  it('resolves every step to a token rather than a literal', () => {
    for (const rank of [-5, -3, -1, 0, 1, 3, 5]) {
      expect(fillFor(rank)).toMatch(/^var\(--heat-/)
    }
  })
})

// ── the grid ─────────────────────────────────────────────────────────────────

describe('RegimeMatrix', () => {
  it('lays the regimes out on their axes rather than in a row', () => {
    render(<RegimeMatrix cells={GRID} />)
    // Trend across, volatility down: the fact that high volatility hurts in
    // both directions is a row, not four labels to match up.
    expect(screen.getByRole('columnheader', { name: 'Uptrend' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Downtrend' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Low vol' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'High vol' })).toBeInTheDocument()
  })

  it('shows the sample on every cell, not only the doubtful ones', () => {
    /* A reader should not have to notice the absence of a warning to know a
     * number is trustworthy. */
    render(<RegimeMatrix cells={GRID} />)
    expect(screen.getAllByText('60 trades')).toHaveLength(4)
  })

  it('marks a cell whose sample cannot estimate anything', () => {
    const thin = [cell('BULL_LOW', { trade_count: 3, net_pnl: 40, insufficient: true }), ...GRID.slice(1)]
    render(<RegimeMatrix cells={thin} />)
    expect(screen.getByText('not an estimate')).toBeInTheDocument()
  })

  it('never paints an untraded cell as a measured zero', () => {
    const empty = [cell('BEAR_HIGH', { trade_count: 0, net_pnl: 0, average_trade: null }), ...GRID.slice(0, 3)]
    const { container } = render(<RegimeMatrix cells={empty} />)
    const painted = Array.from(container.querySelectorAll('.rm-cell')).map(
      (node) => (node as HTMLElement).style.background,
    )
    expect(painted).toContain('var(--heat-empty)')
  })

  it('switches what is being measured without changing the layout', () => {
    render(<RegimeMatrix cells={GRID} />)
    expect(screen.getByText('+$10,000')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Exposure' }))
    expect(screen.queryByText('+$10,000')).not.toBeInTheDocument()
    expect(screen.getAllByText('25%')).toHaveLength(4)
    // The axes do not move when the measure does.
    expect(screen.getByRole('rowheader', { name: 'High vol' })).toBeInTheDocument()
  })

  it('rescales to the measure on screen', () => {
    /* Net P&L and per-trade have different magnitudes; a scale that stayed on
     * the first would render the second almost uniformly pale. */
    const { container } = render(<RegimeMatrix cells={GRID} />)
    const darkest = () =>
      Array.from(container.querySelectorAll('.rm-cell')).map(
        (node) => (node as HTMLElement).style.background,
      )
    expect(darkest()).toContain('var(--heat-pos-5)')
    fireEvent.click(screen.getByRole('button', { name: 'Per trade' }))
    expect(darkest()).toContain('var(--heat-pos-5)')
  })

  it('hands the regime back when a cell is chosen', () => {
    const picked = vi.fn()
    render(<RegimeMatrix cells={GRID} onPick={picked} />)
    const row = screen.getByRole('rowheader', { name: 'High vol' }).closest('tr')!
    fireEvent.click(within(row).getAllByRole('button')[0])
    expect(picked).toHaveBeenCalledWith('BULL_HIGH')
  })

  it('renders a grid a regime is missing from without inventing the cell', () => {
    render(<RegimeMatrix cells={GRID.slice(0, 2)} />)
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
})

// ── the reading ──────────────────────────────────────────────────────────────

const READING: Reading = {
  standing: 'THIN',
  total_trades: 200,
  classified_trades: 190,
  attribution: 'entry',
  series_fingerprint: 'abc',
  findings: [
    {
      kind: 'SOURCE',
      standing: 'MEASURED',
      fact: '90.0% of gross profit came from Bull·Lo, over 90 trades.',
      interpretation: 'The edge is concentrated in one market condition.',
      implication: 'A result that lives in one regime is a bet that the regime recurs.',
      evidence: [
        { label: 'gross profit share', value: 0.9, display: '90.0%', detail: 'of gross profit' },
        { label: 'trades in regime', value: 90, display: '90', detail: '' },
      ],
      regimes: ['BULL_LOW'],
    },
    {
      kind: 'UNTESTED',
      standing: 'THIN',
      fact: 'Bear·Hi holds fewer than 20 trades.',
      interpretation: 'Not observed enough for any rate from it to be an estimate.',
      implication: 'An unmeasured corner is a risk that has not been priced.',
      evidence: [{ label: 'trades in Bear·Hi', value: 3, display: '3', detail: '' }],
      regimes: ['BEAR_HIGH'],
    },
  ],
}

describe('RegimeReading', () => {
  it('shows the fact before anything drawn from it', () => {
    render(<RegimeReading reading={READING} />)
    const card = screen.getByText(/90.0% of gross profit/).closest('.rr-finding')!
    const text = Array.from(card.querySelectorAll('p')).map((p) => p.textContent ?? '')
    expect(text[0]).toContain('90.0% of gross profit')
    expect(text[1]).toContain('concentrated')
    expect(text[2]).toContain('bet that the regime recurs')
  })

  it('keeps the numbers one click away rather than a page away', () => {
    render(<RegimeReading reading={READING} />)
    expect(screen.queryByText('gross profit share')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'The numbers (2)' }))
    expect(screen.getByText('gross profit share')).toBeInTheDocument()
    expect(screen.getByText('90.0%')).toBeInTheDocument()
  })

  it('carries the weakest standing to the summary, not only to the finding', () => {
    /* A reader who stops at the headline has still been told what the headline
     * can support. */
    render(<RegimeReading reading={READING} />)
    expect(screen.getByText(/too small to generalise from/)).toBeInTheDocument()
  })

  it('says a descriptive reading is not evidence', () => {
    render(<RegimeReading reading={{ ...READING, standing: 'DESCRIPTIVE' }} />)
    expect(screen.getByText(/not evidence about what the strategy would have done/)).toBeInTheDocument()
  })

  it('says nothing rather than filling space when the run supports nothing', () => {
    render(<RegimeReading reading={{ ...READING, findings: [] }} />)
    expect(screen.getByText(/supports no reading/)).toBeInTheDocument()
  })

  it('renders exactly the findings it was given', () => {
    /* The one property that makes the prose checkable: the surface cannot add
     * a finding, and it cannot drop one. */
    render(<RegimeReading reading={READING} />)
    expect(document.querySelectorAll('.rr-finding')).toHaveLength(READING.findings.length)
  })
})
