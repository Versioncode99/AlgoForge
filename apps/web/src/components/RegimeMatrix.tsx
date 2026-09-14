import { useMemo, useState } from 'react'

/* The 2x2 a strategy's P&L actually lives on.
 *
 * The four regimes are not a list. They are a grid — trend across, volatility
 * down — and rendering them as four cards in a row throws that away: laid out
 * flat, "this strategy is fine until volatility rises, in either direction" is
 * a fact a reader has to reconstruct by matching four labels. Laid out as the
 * grid it is, it is a row that is red.
 *
 * Three encodings, each carrying one thing:
 *
 *   position   which condition — fixed, so the grid means the same every time
 *   colour     how much, diverging from zero, in five ordinal steps
 *   hatching   this cell's sample is too small for its rates to be an estimate
 *
 * Anchored at zero rather than at the extremes present, because a scale
 * anchored at the minimum paints the least-bad losing cell as neutral, and on
 * an all-losing strategy paints one of them green.
 */

export type RegimeCell = {
  regime: string
  label: string
  trade_count: number
  net_pnl: number
  gross_pnl: number
  win_rate: number | null
  average_trade: number | null
  bar_exposure: number
  trade_share: number
  insufficient: boolean
  note: string
}

/** Where each regime sits. Trend runs across, volatility down. */
const ROWS = [
  { key: 'LOW', label: 'Low vol' },
  { key: 'HIGH', label: 'High vol' },
] as const
const COLUMNS = [
  { key: 'BULL', label: 'Uptrend' },
  { key: 'BEAR', label: 'Downtrend' },
] as const

/** The measure a cell is painted by. Each answers a different question. */
export const MEASURES = {
  net_pnl: { label: 'Net P&L', hint: 'What this condition produced in total.' },
  average_trade: { label: 'Per trade', hint: 'What one trade in this condition was worth.' },
  bar_exposure: { label: 'Exposure', hint: 'Share of classified bars spent here.' },
} as const
export type MeasureKey = keyof typeof MEASURES

const STEPS = 5

/**
 * Which of the five ordinal steps a value falls in, on a scale anchored at zero.
 *
 * `extent` is the largest magnitude anywhere in the grid, so the darkest step
 * is always reached by exactly one cell and the grid is self-scaling. Returns
 * null for a cell with no observations at all: an empty cell and a measured
 * zero are different facts, and painting both neutral would merge them.
 */
export function step(value: number | null, extent: number): number | null {
  if (value === null || !Number.isFinite(value)) return null
  if (extent <= 0 || value === 0) return 0
  const share = Math.min(1, Math.abs(value) / extent)
  const rank = Math.ceil(share * STEPS)
  return value > 0 ? rank : -rank
}

/** The token a step paints with. */
export function fillFor(rank: number | null): string {
  if (rank === null) return 'var(--heat-empty)'
  if (rank === 0) return 'var(--heat-zero)'
  return rank > 0 ? `var(--heat-pos-${rank})` : `var(--heat-neg-${-rank})`
}

function measureOf(cell: RegimeCell, measure: MeasureKey): number | null {
  if (measure === 'net_pnl') return cell.trade_count ? cell.net_pnl : null
  if (measure === 'average_trade') return cell.average_trade
  return cell.bar_exposure
}

function show(value: number | null, measure: MeasureKey): string {
  if (value === null) return '—'
  if (measure === 'bar_exposure') return `${(value * 100).toFixed(0)}%`
  const sign = value < 0 ? '−' : value > 0 ? '+' : ''
  return `${sign}$${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

export function RegimeMatrix({
  cells,
  onPick,
  selected,
}: {
  cells: RegimeCell[]
  onPick?: (regime: string) => void
  selected?: string | null
}) {
  const [measure, setMeasure] = useState<MeasureKey>('net_pnl')
  const byRegime = useMemo(
    () => new Map(cells.map((cell) => [cell.regime, cell])),
    [cells],
  )
  const extent = useMemo(() => {
    const values = cells
      .map((cell) => measureOf(cell, measure))
      .filter((value): value is number => value !== null)
    return values.reduce((most, value) => Math.max(most, Math.abs(value)), 0)
  }, [cells, measure])

  return (
    <div className="regime-matrix-surface">
      <div className="rm-controls" role="group" aria-label="Measure">
        {(Object.keys(MEASURES) as MeasureKey[]).map((key) => (
          <button
            key={key}
            type="button"
            className={key === measure ? 'rm-measure active' : 'rm-measure'}
            aria-pressed={key === measure}
            title={MEASURES[key].hint}
            onClick={() => setMeasure(key)}
          >
            {MEASURES[key].label}
          </button>
        ))}
      </div>

      <table className="rm-grid">
        <caption className="rm-caption">
          {MEASURES[measure].hint} Shade is magnitude against the strongest cell; a hatched
          cell has too few trades for its rates to estimate anything.
        </caption>
        <thead>
          <tr>
            <td className="rm-corner" />
            {COLUMNS.map((column) => (
              <th key={column.key} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              {COLUMNS.map((column) => {
                const regime = `${column.key}_${row.key}`
                const cell = byRegime.get(regime)
                if (!cell) {
                  return (
                    <td key={regime} className="rm-cell missing">
                      <span className="rm-value mono">—</span>
                    </td>
                  )
                }
                const value = measureOf(cell, measure)
                const rank = cell.trade_count === 0 ? null : step(value, extent)
                const classes = [
                  'rm-cell',
                  cell.insufficient ? 'thin' : '',
                  selected === regime ? 'picked' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
                return (
                  <td key={regime} className={classes} style={{ background: fillFor(rank) }}>
                    <button
                      type="button"
                      className="rm-hit"
                      onClick={() => onPick?.(regime)}
                      aria-pressed={selected === regime}
                      title={
                        cell.note ||
                        `${cell.label}: ${cell.trade_count} trades, ` +
                          `${(cell.bar_exposure * 100).toFixed(0)}% of bars`
                      }
                    >
                      <span className="rm-value mono">{show(value, measure)}</span>
                      <span className="rm-sub mono">
                        {cell.trade_count} {cell.trade_count === 1 ? 'trade' : 'trades'}
                      </span>
                      {/* The sample is shown on every cell, not only the thin
                          ones. A reader should not have to notice the absence
                          of a warning to know a number is trustworthy. */}
                      {cell.insufficient && (
                        <span className="rm-flag">not an estimate</span>
                      )}
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
