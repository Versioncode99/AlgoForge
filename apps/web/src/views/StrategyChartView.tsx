import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { StrategyChart, type LedgerResponse, type TradeRow } from '../components/StrategyChart'
import { TradeInspector } from '../components/TradeInspector'
import type { TimeframeKey } from '../components/PriceChart'

/* Open a strategy on the chart and see what it actually did.
 *
 * This is a research and replay surface. Nothing here is live, nothing here
 * places an order, and every number is read from a backtest artifact — which is
 * why the header states which run it is showing rather than leaving the reader
 * to assume it is the latest.
 *
 * The stepper is the replay: it walks the ledger a trade at a time, and the
 * chart re-anchors its window on each one, so "show me the worst ten trades and
 * walk me through them" is a filter plus the arrow keys.
 */

type StrategyRow = { strategy_id: string; name?: string; family?: string; backtest_count?: number }

type RegimeCell = {
  regime: string
  label: string
  trade_count: number
  net_pnl: number
  win_rate: number | null
  average_trade: number | null
  bar_exposure: number
  trade_share: number
  insufficient: boolean
  note: string
}

type RegimeReport = {
  attribution: string
  total_trades: number
  classified_trades: number
  unclassified_trades: number
  coverage: number
  cells: RegimeCell[]
  transitions: number[][]
  transition_labels: string[]
  concentration: number | null
  concentration_regime: string | null
  warnings: string[]
}

const OUTCOMES = ['all', 'win', 'loss'] as const
const SIDES = ['all', 'long', 'short'] as const

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="state" role="status">
      {children}
    </div>
  )
}

export function StrategyChartView({ initialStrategy }: { initialStrategy?: string }) {
  const [strategyId, setStrategyId] = useState(initialStrategy ?? '')
  const [timeframe, setTimeframe] = useState<TimeframeKey>('15m')
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>('all')
  const [side, setSide] = useState<(typeof SIDES)[number]>('all')
  const [regimeFilter, setRegimeFilter] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showRegimes, setShowRegimes] = useState(false)

  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyRow[]>('/strategies'),
  })

  const active = strategyId || strategies.data?.[0]?.strategy_id || ''

  const ledger = useQuery({
    queryKey: ['ledger', active, outcome, side, regimeFilter],
    queryFn: () => {
      const query = new URLSearchParams({ outcome, side, regime: regimeFilter, limit: '2000' })
      return getJson<LedgerResponse>(`/strategies/${active}/trades?${query.toString()}`)
    },
    enabled: Boolean(active),
    retry: false,
  })

  const regimes = useQuery({
    queryKey: ['regimes', active],
    queryFn: () => getJson<RegimeReport>(`/strategies/${active}/regimes`),
    enabled: Boolean(active) && showRegimes,
    retry: false,
  })

  const trades = useMemo(() => ledger.data?.trades ?? [], [ledger.data])
  const selectedIndex = trades.findIndex((trade) => trade.trade_id === selectedId)
  const selected = selectedIndex >= 0 ? trades[selectedIndex] : null

  const step = useCallback(
    (delta: number) => {
      if (!trades.length) return
      const next = selectedIndex < 0 ? 0 : Math.min(trades.length - 1, Math.max(0, selectedIndex + delta))
      setSelectedId(trades[next].trade_id)
    },
    [trades, selectedIndex],
  )

  // Arrow keys walk the ledger, which is what makes this a replay rather than a
  // table. Ignored while a form control has focus so the filters stay usable.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) return
      if (event.key === 'ArrowRight' || event.key === 'j') step(1)
      else if (event.key === 'ArrowLeft' || event.key === 'k') step(-1)
      else if (event.key === 'Escape') setSelectedId(null)
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step])

  // Changing strategy must not carry a selection across, or the inspector would
  // show one strategy's trade under another's name.
  useEffect(() => {
    setSelectedId(null)
  }, [active, outcome, side, regimeFilter])

  if (strategies.isPending) return <Empty>Loading strategies…</Empty>
  if (strategies.isError)
    return (
      <div className="state error" role="alert">
        {(strategies.error as Error).message}
      </div>
    )
  if (!strategies.data?.length)
    return <Empty>No strategies have been created yet. Create one and run a backtest.</Empty>

  return (
    <div className="strategy-chart-view">
      <header className="scv-bar">
        <label className="ctl">
          <span>Strategy</span>
          <select value={active} onChange={(event) => setStrategyId(event.target.value)}>
            {strategies.data.map((row) => (
              <option key={row.strategy_id} value={row.strategy_id}>
                {row.name ?? row.strategy_id}
                {row.backtest_count ? ` · ${row.backtest_count} run(s)` : ''}
              </option>
            ))}
          </select>
        </label>

        <label className="ctl">
          <span>Outcome</span>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value as typeof outcome)}>
            {OUTCOMES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="ctl">
          <span>Side</span>
          <select value={side} onChange={(e) => setSide(e.target.value as typeof side)}>
            {SIDES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="ctl">
          <span>Regime</span>
          <select value={regimeFilter} onChange={(e) => setRegimeFilter(e.target.value)}>
            <option value="all">all</option>
            <option value="BULL_LOW">Bull · Low vol</option>
            <option value="BULL_HIGH">Bull · High vol</option>
            <option value="BEAR_LOW">Bear · Low vol</option>
            <option value="BEAR_HIGH">Bear · High vol</option>
          </select>
        </label>

        <button
          type="button"
          className={showRegimes ? 'chip active' : 'chip'}
          aria-pressed={showRegimes}
          onClick={() => setShowRegimes((value) => !value)}
        >
          Regime breakdown
        </button>
      </header>

      {ledger.isError && (
        <div className="state error" role="alert">
          {(ledger.error as Error).message}
        </div>
      )}

      {ledger.isPending && active && <Empty>Loading the trade ledger…</Empty>}

      {ledger.data && (
        <>
          {ledger.data.warnings.map((warning) => (
            <p key={warning} className="scv-warning" role="status">
              {warning}
            </p>
          ))}

          <div className={selected ? 'scv-body with-inspector' : 'scv-body'}>
            <div className="scv-main">
              <StrategyChart
                ledger={ledger.data}
                dataset={ledger.data.dataset_key ?? ''}
                timeframe={timeframe}
                onTimeframe={setTimeframe}
                selected={selected}
                onSelect={(trade) => setSelectedId(trade?.trade_id ?? null)}
              />

              {showRegimes && <RegimePanel query={regimes} onPick={setRegimeFilter} />}

              <div className="scv-table-wrap">
                <table className="panel-table scv-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Entry</th>
                      <th>Side</th>
                      <th>Regime</th>
                      <th>Exit</th>
                      <th className="num">P&amp;L</th>
                      <th className="num">MFE</th>
                      <th className="num">MAE</th>
                      <th className="num">Bars</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((trade) => (
                      <tr
                        key={trade.trade_id}
                        className={trade.trade_id === selectedId ? 'is-selected' : undefined}
                        onClick={() => setSelectedId(trade.trade_id)}
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedId(trade.trade_id)
                          }
                        }}
                      >
                        <td className="mono">{trade.sequence}</td>
                        <td className="mono">{trade.entry_time.replace('T', ' ').slice(0, 16)}</td>
                        <td className={trade.side}>{trade.side}</td>
                        <td className="mono">{trade.regime_label ?? '—'}</td>
                        <td className="mono">{trade.exit_reason}</td>
                        <td className={`num mono ${trade.net_pnl >= 0 ? 'up' : 'down'}`}>
                          {trade.net_pnl.toFixed(2)}
                        </td>
                        <td className="num mono">{trade.mfe === null ? '—' : trade.mfe.toFixed(0)}</td>
                        <td className="num mono">{trade.mae === null ? '—' : trade.mae.toFixed(0)}</td>
                        <td className="num mono">{trade.bars_held}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {trades.length === 0 && (
                  <Empty>
                    No trades match this filter. The run recorded {ledger.data.total_trades} in
                    total.
                  </Empty>
                )}
              </div>
            </div>

            {selected && (
              <TradeInspector
                strategyId={active}
                backtestId={ledger.data.backtest_id}
                trade={selected}
                onClose={() => setSelectedId(null)}
                onStep={step}
                position={{ index: selectedIndex, total: trades.length }}
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}

function RegimePanel({
  query,
  onPick,
}: {
  query: { isPending: boolean; isError: boolean; error: unknown; data: RegimeReport | undefined }
  onPick: (regime: string) => void
}) {
  if (query.isPending) return <Empty>Classifying the bars…</Empty>
  if (query.isError)
    return (
      <div className="state error" role="alert">
        {(query.error as Error).message}
      </div>
    )
  const report = query.data
  if (!report) return null

  return (
    <section className="regime-panel">
      <header>
        <h3>Where the P&amp;L came from</h3>
        <span className="mono">
          {report.classified_trades} of {report.total_trades} trades placed ·{' '}
          {(report.coverage * 100).toFixed(1)}% of bars classified
        </span>
      </header>

      <div className="regime-grid">
        {report.cells.map((cell) => (
          <button
            key={cell.regime}
            type="button"
            className={cell.insufficient ? 'regime-cell thin' : 'regime-cell'}
            onClick={() => onPick(cell.regime)}
            title={cell.note || `Filter the ledger to ${cell.label}`}
          >
            <span className="rc-label">{cell.label}</span>
            <span className={`rc-pnl mono ${cell.net_pnl >= 0 ? 'up' : 'down'}`}>
              {cell.net_pnl >= 0 ? '+' : '−'}$
              {Math.abs(cell.net_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
            <dl className="rc-stats">
              <div>
                <dt>Exposure</dt>
                <dd className="mono">{(cell.bar_exposure * 100).toFixed(0)}%</dd>
              </div>
              <div>
                <dt>Trades</dt>
                <dd className="mono">{cell.trade_count}</dd>
              </div>
              <div>
                <dt>Win rate</dt>
                <dd className="mono">
                  {cell.win_rate === null ? '—' : `${(cell.win_rate * 100).toFixed(0)}%`}
                </dd>
              </div>
              <div>
                <dt>Avg trade</dt>
                <dd className="mono">
                  {cell.average_trade === null ? '—' : cell.average_trade.toFixed(0)}
                </dd>
              </div>
            </dl>
            {cell.insufficient && <span className="rc-note">{cell.note}</span>}
          </button>
        ))}
      </div>

      <div className="regime-matrix">
        <h4>How the regimes follow each other</h4>
        <table className="panel-table">
          <thead>
            <tr>
              <th>from \ to</th>
              {report.transition_labels.map((label) => (
                <th key={label} className="num">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {report.transitions.map((row, index) => {
              const total = row.reduce((sum, value) => sum + value, 0)
              return (
                <tr key={report.transition_labels[index]}>
                  <th scope="row">{report.transition_labels[index]}</th>
                  {row.map((value, column) => (
                    <td key={column} className="num mono">
                      {total ? `${((value / total) * 100).toFixed(0)}%` : '—'}
                      <span className="obs">{value.toLocaleString()}</span>
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="scv-note">
          Percentages with their observation counts, because a 33% built on three observations and
          a 33% built on three thousand are different claims.
        </p>
      </div>

      {report.warnings.map((warning) => (
        <p key={warning} className="scv-warning" role="status">
          {warning}
        </p>
      ))}
    </section>
  )
}
