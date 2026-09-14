import { describe, expect, it } from 'vitest'
import { MATERIAL, readGap, rows, type PathStatistics, type ResampleComparison } from './resample'

const stats = (over: Partial<PathStatistics> = {}): PathStatistics => ({
  method: 'iid',
  paths: 2000,
  trades_per_path: 120,
  seed: 1,
  median_final: 4000,
  p05_final: -900,
  p95_final: 9000,
  median_max_drawdown: 1200,
  p95_max_drawdown: 2000,
  loss_probability: 0.12,
  underwater_probability: 0.8,
  var_95: 900,
  cvar_95: 1400,
  sample_paths: [],
  median_path: [],
  p05_path: [],
  p95_path: [],
  ...over,
})

const comparison = (over: Partial<ResampleComparison> = {}): ResampleComparison => ({
  strategy_id: 's1',
  backtest_id: 'b1',
  iid: stats(),
  regime_aware: stats({ method: 'regime_aware' }),
  drawdown_gap: 0,
  pooled: [],
  warnings: [],
  trade_count: 120,
  provenance: {},
  ...over,
})

describe('reading the gap between the two resamplers', () => {
  it('calls a materially deeper regime-aware drawdown what it is', () => {
    const reading = readGap(comparison({
      regime_aware: stats({ method: 'regime_aware', p95_max_drawdown: 2600 }),
      drawdown_gap: 600,
    }))
    expect(reading.tone).toBe('bad')
    expect(reading.share).toBeCloseTo(0.3)
    expect(reading.detail).toContain('understated')
  })

  it('does not call a small difference a finding', () => {
    const reading = readGap(comparison({ drawdown_gap: 2000 * (MATERIAL / 2) }))
    expect(reading.tone).toBe('plain')
    expect(reading.headline).toBe('The two agree')
  })

  it('reports a kinder regime-aware study without calling it safe', () => {
    const reading = readGap(comparison({ drawdown_gap: -600 }))
    expect(reading.tone).toBe('good')
    expect(reading.detail).toContain('conservative')
  })

  it('is unknown, not fine, when no chain could be fitted', () => {
    const reading = readGap(comparison({ regime_aware: null, drawdown_gap: null }))
    expect(reading.tone).toBe('unknown')
    expect(reading.gap).toBeNull()
    expect(reading.detail).toContain('missing evidence')
  })

  it('does not divide by a zero IID drawdown', () => {
    const reading = readGap(comparison({
      iid: stats({ p95_max_drawdown: 0 }),
      drawdown_gap: 50,
    }))
    expect(reading.share).toBeNull()
    expect(reading.tone).toBe('plain')
  })
})

describe('the side-by-side rows', () => {
  it('pairs every figure so one method cannot be shown without the other', () => {
    const table = rows(comparison())
    expect(table).toHaveLength(7)
    for (const row of table) expect(row.regime).not.toBeNull()
  })

  it('leaves the regime column empty rather than repeating the IID number', () => {
    const table = rows(comparison({ regime_aware: null }))
    for (const row of table) expect(row.regime).toBeNull()
  })
})
