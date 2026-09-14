/* The resample comparison, as the interface reads it.
 *
 * The whole point of this surface is one subtraction: the p95 drawdown a
 * regime-aware resample produces, minus the one an IID resample produces. The
 * arithmetic is trivial and the *reading* is not, so the reading lives here and
 * is tested, rather than being assembled inside a component where it cannot be.
 */

export type PathStatistics = {
  method: string
  paths: number
  trades_per_path: number
  seed: number
  median_final: number
  p05_final: number
  p95_final: number
  median_max_drawdown: number
  p95_max_drawdown: number
  loss_probability: number
  underwater_probability: number
  var_95: number
  cvar_95: number
  sample_paths: number[][]
  median_path: number[]
  p05_path: number[]
  p95_path: number[]
}

export type ResampleComparison = {
  strategy_id: string
  backtest_id: string | null
  regime_aware: PathStatistics | null
  iid: PathStatistics
  drawdown_gap: number | null
  pooled: string[]
  warnings: string[]
  trade_count: number
  provenance: Record<string, string>
}

/** What the gap between the two resamplers means, in one sentence. */
export type GapReading = {
  /** Regime-aware p95 drawdown minus the IID one. Null when no chain could be fitted. */
  gap: number | null
  /** Relative to the IID figure, so a gap can be compared across strategies. */
  share: number | null
  tone: 'good' | 'bad' | 'plain' | 'unknown'
  headline: string
  detail: string
}

/** How large a gap has to be, relative to the IID drawdown, before it is worth a warning. */
export const MATERIAL = 0.1

export function readGap(comparison: ResampleComparison): GapReading {
  const iid = comparison.iid.p95_max_drawdown
  const regime = comparison.regime_aware
  if (!regime || comparison.drawdown_gap === null) {
    return {
      gap: null,
      share: null,
      tone: 'unknown',
      headline: 'Not measured',
      detail:
        'No regime chain could be fitted to this backtest, so there is nothing to compare the ' +
        'IID study against. That is missing evidence, not a clean result.',
    }
  }
  const gap = comparison.drawdown_gap
  const share = iid > 0 ? gap / iid : null
  if (share !== null && share >= MATERIAL) {
    return {
      gap,
      share,
      tone: 'bad',
      headline: 'Clustering makes it worse',
      detail:
        'Keeping the regime structure produces a deeper worst-case drawdown than shuffling the ' +
        'trades independently. This strategy’s risk lives in its runs, and an IID study ' +
        'would have understated it by exactly this much.',
    }
  }
  if (share !== null && share <= -MATERIAL) {
    return {
      gap,
      share,
      tone: 'good',
      headline: 'Clustering is not the risk here',
      detail:
        'The regime-aware study is the kinder of the two, which means the losses are not the ' +
        'ones that arrive in runs. Read the IID figure as the conservative one.',
    }
  }
  return {
    gap,
    share,
    tone: 'plain',
    headline: 'The two agree',
    detail:
      'Both resamplers land within ' +
      `${Math.round(MATERIAL * 100)}% of each other, so the ordering of this strategy’s ` +
      'trades is not what drives its drawdown.',
  }
}

/** The rows a side-by-side table renders, so the two methods cannot drift apart. */
export type Row = { key: string; label: string; iid: number; regime: number | null; kind: 'money' | 'share' }

export function rows(comparison: ResampleComparison): Row[] {
  const r = comparison.regime_aware
  const at = (pick: (s: PathStatistics) => number) => (r ? pick(r) : null)
  return [
    { key: 'p95_dd', label: 'Worst-case drawdown (p95)', iid: comparison.iid.p95_max_drawdown, regime: at((s) => s.p95_max_drawdown), kind: 'money' },
    { key: 'median_dd', label: 'Typical drawdown (median)', iid: comparison.iid.median_max_drawdown, regime: at((s) => s.median_max_drawdown), kind: 'money' },
    { key: 'median_final', label: 'Median outcome', iid: comparison.iid.median_final, regime: at((s) => s.median_final), kind: 'money' },
    { key: 'p05_final', label: 'Bad fifth percentile', iid: comparison.iid.p05_final, regime: at((s) => s.p05_final), kind: 'money' },
    { key: 'cvar', label: 'CVaR 95', iid: comparison.iid.cvar_95, regime: at((s) => s.cvar_95), kind: 'money' },
    { key: 'loss', label: 'Ends below zero', iid: comparison.iid.loss_probability, regime: at((s) => s.loss_probability), kind: 'share' },
    { key: 'underwater', label: 'Spends time underwater', iid: comparison.iid.underwater_probability, regime: at((s) => s.underwater_probability), kind: 'share' },
  ]
}
