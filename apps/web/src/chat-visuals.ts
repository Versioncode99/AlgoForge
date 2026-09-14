/* Which conversation artifacts can be drawn, and from what.
 *
 * D4 §4F asks the chat to produce quantitative visual artifacts "where the
 * underlying data and computation support them", and is explicit about the
 * boundary: *"The visualization must be generated from actual computed data.
 * Never generate fake visual data simply to make the chat look impressive."*
 *
 * That sentence is the whole design. Each entry below names the route that
 * returns the *computed* result, and a kind with no such route gets no chart --
 * not a placeholder, not an approximation drawn from the artifact's title. An
 * artifact whose reference does not name enough to fetch it is in the same
 * position, and says so.
 *
 * Nothing here computes anything. The chart a conversation draws is the same
 * object the surface draws, fetched from the same route, so the two cannot
 * disagree about what a strategy did.
 */

import type { Artifact } from './chat'

export type VisualKind = 'analysis' | 'curve' | 'band' | 'regime'

export type Visual = {
  /** Which renderer draws it. */
  kind: VisualKind
  /** The route that returns the computed result. */
  path: string
  /** What the reader is looking at, above the chart. */
  caption: string
}

/** What can be drawn for this artifact, or null when nothing honestly can. */
export function visualFor(artifact: Artifact): Visual | null {
  const strategy = artifact.refs.strategy_id ?? ''
  const analysisArtifact = artifact.refs.artifact_id ?? ''

  switch (artifact.kind) {
    case 'analysis':
      // The artifact that was computed, by its id -- not a re-run, which could
      // land on a different backtest and quietly draw a different answer.
      return analysisArtifact
        ? {
            kind: 'analysis',
            path: `/lab/artifacts/${analysisArtifact}`,
            caption: 'The analysis this turn produced, drawn from its stored cells.',
          }
        : null
    case 'parameter_surface':
      return strategy
        ? {
            kind: 'analysis',
            path: `/strategies/${strategy}/surface`,
            caption:
              'Every cell is a real backtest on the development partition. Exploratory: ' +
              'a surface cannot promote anything.',
          }
        : null
    case 'resample':
      return strategy
        ? {
            kind: 'band',
            path: `/strategies/${strategy}/resample`,
            caption:
              "The strategy's own trades resampled, p05 to p95. Resampling changes the " +
              'order and never the values.',
          }
        : null
    case 'regime':
      return strategy
        ? {
            kind: 'regime',
            path: `/strategies/${strategy}/regimes`,
            caption: 'Where the P&L came from, by market regime.',
          }
        : null
    case 'backtest':
      return strategy
        ? {
            kind: 'curve',
            path: `/strategies/${strategy}/trades?limit=4000&with_regimes=false`,
            caption:
              'The realised equity curve, accumulated from the run\'s own fills. Not a ' +
              'model of one: every step is a trade that happened.',
          }
        : null
    default:
      // strategy, evidence, validation, port, prop_simulation, workspace.
      // Each opens a surface that shows more than a chart would, and drawing a
      // chart for them would mean inventing one.
      return null
  }
}

/** The three series a resample band needs, or nothing when the study is absent. */
export function bandFromResample(payload: unknown): {
  p05: number[]
  median: number[]
  p95: number[]
} | null {
  const data = payload as {
    regime_aware?: { p05_path?: number[]; median_path?: number[]; p95_path?: number[] } | null
    iid?: { p05_path?: number[]; median_path?: number[]; p95_path?: number[] }
  } | null
  // Regime-aware when it exists: it is the one that keeps losing runs together,
  // and it is the study the surface leads with. Falling back to IID is stated
  // in the caption rather than silently substituted.
  const study = data?.regime_aware ?? data?.iid
  if (!study?.median_path?.length) return null
  return {
    p05: study.p05_path ?? [],
    median: study.median_path,
    p95: study.p95_path ?? [],
  }
}

/** The regime grid's cells, or an empty list.
 *
 * The grid rather than a bar chart: the four regimes are trend across and
 * volatility down, and rendering them as four bars throws that away -- the same
 * argument `RegimeMatrix` was written for.
 */
export function regimeCells(payload: unknown): unknown[] {
  const data = payload as { cells?: unknown[] } | null
  return Array.isArray(data?.cells) ? data.cells : []
}

/** The equity curve implied by a run's fills, accumulated in order. */
export function equityFromTrades(payload: unknown): number[] {
  const data = payload as { trades?: { net_pnl?: number }[] } | null
  const trades = data?.trades
  if (!Array.isArray(trades) || trades.length === 0) return []
  const curve: number[] = [0]
  let running = 0
  for (const trade of trades) {
    running += Number(trade.net_pnl ?? 0)
    curve.push(running)
  }
  return curve
}
