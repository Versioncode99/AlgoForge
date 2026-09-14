import { describe, expect, it } from 'vitest'
import type { Artifact } from './chat'
import {
  bandFromResample,
  equityFromTrades,
  regimeCells,
  visualFor,
} from './chat-visuals'

/* What a conversation may draw, and — more importantly — what it may not.
 *
 * §4F: "The visualization must be generated from actual computed data. Never
 * generate fake visual data simply to make the chat look impressive." Every
 * assertion below is about that sentence.
 */

const artifact = (over: Partial<Artifact> = {}): Artifact => ({
  artifact_id: 'a1',
  kind: 'analysis',
  title: 'Regime × volatility',
  refs: { strategy_id: 's1', artifact_id: 'lab_1' },
  provenance: 'deterministic',
  ...over,
})

describe('what can be drawn', () => {
  it('draws an analysis from the artifact that was computed, not a re-run', () => {
    const visual = visualFor(artifact())!
    expect(visual.kind).toBe('analysis')
    expect(visual.path).toBe('/lab/artifacts/lab_1')
  })

  it('refuses an analysis that does not name which artifact it was', () => {
    /* Re-running could land on a different backtest and quietly draw a
       different answer under the same title. */
    expect(visualFor(artifact({ refs: { strategy_id: 's1' } }))).toBeNull()
  })

  it('draws a parameter surface, and says it cannot promote anything', () => {
    const visual = visualFor(artifact({ kind: 'parameter_surface' }))!
    expect(visual.kind).toBe('analysis')
    expect(visual.path).toBe('/strategies/s1/surface')
    expect(visual.caption).toContain('cannot promote')
  })

  it('draws a resample as a band, and says resampling invents nothing', () => {
    const visual = visualFor(artifact({ kind: 'resample' }))!
    expect(visual.kind).toBe('band')
    expect(visual.caption).toContain('never the values')
  })

  it('draws regimes as the grid rather than as four bars', () => {
    expect(visualFor(artifact({ kind: 'regime' }))!.kind).toBe('regime')
  })

  it('draws a backtest from its own fills', () => {
    const visual = visualFor(artifact({ kind: 'backtest' }))!
    expect(visual.kind).toBe('curve')
    expect(visual.path).toContain('/strategies/s1/trades')
    expect(visual.caption).toContain('a trade that happened')
  })
})

describe('what must not be drawn', () => {
  it.each(['strategy', 'evidence', 'validation', 'port', 'workspace', 'prop_simulation'] as const)(
    'draws nothing for %s rather than inventing a chart',
    (kind) => {
      expect(visualFor(artifact({ kind }))).toBeNull()
    },
  )

  it('draws nothing when the artifact names no strategy to fetch from', () => {
    for (const kind of ['parameter_surface', 'resample', 'regime', 'backtest'] as const) {
      expect(visualFor(artifact({ kind, refs: {} }))).toBeNull()
    }
  })
})

describe('reading a payload', () => {
  it('prefers the regime-aware study, which is the one that keeps runs together', () => {
    const band = bandFromResample({
      regime_aware: { p05_path: [1], median_path: [2], p95_path: [3] },
      iid: { p05_path: [9], median_path: [9], p95_path: [9] },
    })
    expect(band).toEqual({ p05: [1], median: [2], p95: [3] })
  })

  it('falls back to IID when no chain could be fitted', () => {
    const band = bandFromResample({ regime_aware: null, iid: { median_path: [5] } })
    expect(band?.median).toEqual([5])
  })

  it('is null when there is no study at all, rather than an empty chart', () => {
    expect(bandFromResample({})).toBeNull()
    expect(bandFromResample(null)).toBeNull()
  })

  it('accumulates an equity curve from fills, starting at zero', () => {
    expect(equityFromTrades({ trades: [{ net_pnl: 10 }, { net_pnl: -4 }] })).toEqual([0, 10, 6])
  })

  it('returns nothing for a run with no trades', () => {
    expect(equityFromTrades({ trades: [] })).toEqual([])
    expect(equityFromTrades(null)).toEqual([])
  })

  it('reads regime cells, or none', () => {
    expect(regimeCells({ cells: [{ regime: 'BULL_LOW' }] })).toHaveLength(1)
    expect(regimeCells({})).toEqual([])
  })
})
