import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { getJson } from '../api'
import type { BarsResponse, TimeframeKey } from './PriceChart'
import { TIMEFRAMES } from './PriceChart'

/* A strategy's historical trades, drawn over the candles they happened on.
 *
 * Everything here comes out of a backtest artifact. There is no simulated
 * marker, no interpolated fill and no level recomputed from today's ATR: a
 * stop line is drawn only where the run recorded one, and a trade written
 * before AlgoForge recorded levels shows "not recorded" rather than a line at
 * a plausible-looking price.
 *
 * Markers are placed by timestamp, not by bar index. The backtest ran on the
 * 1-minute archive and this chart may be drawing hourly candles; an index would
 * land the marker somewhere confidently wrong, which is worse than not drawing
 * it. lightweight-charts snaps a marker to the nearest bar it holds, so a
 * 14:32 entry sits on the 14:00 candle at hourly resolution — correct, and the
 * inspector shows the real minute.
 */

export type TradeRow = {
  sequence: number
  trade_id: string
  direction: 1 | -1
  side: 'long' | 'short'
  entry_time: string
  exit_time: string
  entry_price: number
  exit_price: number
  net_pnl: number
  gross_pnl: number
  costs: number
  bars_held: number
  exit_reason: string
  mfe: number | null
  mae: number | null
  stop_price: number | null
  target_price: number | null
  trailing_stop_price: number | null
  entry_context: Record<string, number>
  regime: string | null
  regime_label: string | null
  regime_dominant: string | null
  entry_volatility: number | null
  entry_trend_strength: number | null
}

export type LedgerResponse = {
  strategy_id: string
  backtest_id: string
  dataset_key: string | null
  evidence_tier: string
  partition_name: string | null
  parameters: Record<string, number>
  spec_hash: string
  code_hash: string
  data_hash: string
  finished_at: string
  total_trades: number
  matched_trades: number
  returned_trades: number
  truncated: boolean
  trades: TradeRow[]
  warnings: string[]
}

const seconds = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

function token(name: string, fallback: string) {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function StrategyChart({
  ledger,
  dataset,
  timeframe,
  onTimeframe,
  selected,
  onSelect,
  height = 460,
}: {
  ledger: LedgerResponse
  dataset: string
  timeframe: TimeframeKey
  onTimeframe: (key: TimeframeKey) => void
  selected: TradeRow | null
  onSelect: (trade: TradeRow | null) => void
  height?: number
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const chart = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const lines = useRef<IPriceLine[]>([])
  const [ready, setReady] = useState(false)

  // Anchor the window on the trades, not on the latest bars. A strategy
  // validated on last July has no trades in this week's candles, and a chart
  // showing this week draws every marker clamped against its left edge — which
  // looks like 550 trades in one minute. The selection wins when there is one,
  // so stepping through the ledger walks the chart with it.
  const lastTrade = ledger.trades.length ? ledger.trades[ledger.trades.length - 1] : null
  const anchor = selected?.exit_time ?? lastTrade?.exit_time
  const bars = useQuery({
    queryKey: ['strategy-bars', dataset, timeframe, anchor ?? 'latest'],
    queryFn: () => {
      const query = new URLSearchParams({ dataset, timeframe, limit: '900' })
      if (anchor) {
        // A window ending a little after the anchor, so it sits inside the
        // frame rather than against the right edge. `before` pages backwards,
        // which is the only direction the bars endpoint needs to support.
        const after = new Date(new Date(anchor).getTime() + 12 * 3600_000).toISOString()
        query.set('before', after)
      }
      return getJson<BarsResponse>(`/bars?${query.toString()}`)
    },
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (!container.current) return
    const instance = createChart(container.current, {
      layout: {
        background: { color: token('--bg-1', '#100f0e') },
        textColor: token('--fg-2', '#8b857a'),
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: token('--line', '#292724') },
        horzLines: { color: token('--line', '#292724') },
      },
      rightPriceScale: { borderColor: token('--line', '#292724') },
      timeScale: { borderColor: token('--line', '#292724'), secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    })
    const series = instance.addSeries(CandlestickSeries, {
      upColor: token('--pass', '#8fce6a'),
      downColor: token('--fail', '#c1503f'),
      borderUpColor: token('--pass', '#8fce6a'),
      borderDownColor: token('--fail', '#c1503f'),
      wickUpColor: token('--pass', '#8fce6a'),
      wickDownColor: token('--fail', '#c1503f'),
    })
    chart.current = instance
    candles.current = series
    markerApi.current = createSeriesMarkers(series, [])
    setReady(true)
    return () => {
      instance.remove()
      chart.current = null
      candles.current = null
      markerApi.current = null
      lines.current = []
      setReady(false)
    }
  }, [])

  const rows = bars.data?.bars
  useEffect(() => {
    if (!ready || !rows || !candles.current) return
    const data: CandlestickData<Time>[] = rows.map((bar) => ({
      time: seconds(bar.time),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }))
    candles.current.setData(data)
    chart.current?.timeScale().fitContent()
  }, [ready, rows])

  // Which trades the drawn bars actually cover. A marker outside the loaded
  // range is not omitted by the chart library — it is clamped to the nearest
  // edge, so five hundred trades from July pile up on the first candle of
  // September and read as five hundred trades in one minute. Placing a marker
  // where the trade was not is worse than not placing it, so the range is
  // computed and the ones outside it are counted instead of drawn.
  const drawn = useMemo(() => {
    if (!rows || rows.length === 0) return { inside: [] as TradeRow[], outside: 0 }
    const first = seconds(rows[0].time)
    const last = seconds(rows[rows.length - 1].time)
    const inside = ledger.trades.filter((trade) => {
      const at = seconds(trade.entry_time)
      const out = seconds(trade.exit_time)
      return out >= first && at <= last
    })
    return { inside, outside: ledger.trades.length - inside.length }
  }, [rows, ledger.trades])

  useEffect(() => {
    if (!ready || !markerApi.current) return
    const up = token('--pass', '#8fce6a')
    const down = token('--fail', '#c1503f')
    const dim = token('--fg-3', '#5f5a52')
    const items: SeriesMarker<Time>[] = []
    for (const trade of drawn.inside) {
      const isSelected = selected?.trade_id === trade.trade_id
      const won = trade.net_pnl > 0
      items.push({
        time: seconds(trade.entry_time),
        position: trade.direction === 1 ? 'belowBar' : 'aboveBar',
        shape: trade.direction === 1 ? 'arrowUp' : 'arrowDown',
        color: isSelected ? token('--accent', '#c8b48a') : dim,
        text: isSelected ? `#${trade.sequence} ${trade.side}` : '',
        size: isSelected ? 2 : 1,
      })
      items.push({
        time: seconds(trade.exit_time),
        position: trade.direction === 1 ? 'aboveBar' : 'belowBar',
        shape: 'square',
        color: won ? up : down,
        text: isSelected ? `${won ? '+' : ''}${trade.net_pnl.toFixed(2)}` : '',
        size: isSelected ? 2 : 1,
      })
    }
    // lightweight-charts requires markers in ascending time order.
    items.sort((a, b) => (a.time as number) - (b.time as number))
    markerApi.current.setMarkers(items)
  }, [ready, drawn, selected])

  // Levels for the selected trade only. Drawing every trade's stop would be a
  // wall of lines, and drawing a level a run never recorded would be a lie.
  useEffect(() => {
    if (!ready || !candles.current) return
    for (const line of lines.current) candles.current.removePriceLine(line)
    lines.current = []
    if (!selected) return
    const draw = (price: number | null, title: string, colour: string, dashed = true) => {
      if (price === null || !candles.current) return
      lines.current.push(
        candles.current.createPriceLine({
          price,
          color: colour,
          lineWidth: 1,
          lineStyle: dashed ? 2 : 0,
          axisLabelVisible: true,
          title,
        }),
      )
    }
    draw(selected.entry_price, 'entry', token('--accent', '#c8b48a'), false)
    draw(selected.exit_price, 'exit', token('--fg-2', '#8b857a'), false)
    draw(selected.stop_price, 'stop', token('--fail', '#c1503f'))
    draw(selected.target_price, 'target', token('--pass', '#8fce6a'))
    if (selected.trailing_stop_price !== selected.stop_price) {
      draw(selected.trailing_stop_price, 'trail', token('--warn', '#d9a441'))
    }
  }, [ready, selected])

  // Clicking a candle selects the trade that was open at that moment, which is
  // how the chart becomes a way *into* the ledger rather than a picture of it.
  useEffect(() => {
    if (!ready || !chart.current) return
    const handler = (param: { time?: Time }) => {
      if (!param.time) return
      const at = param.time as number
      const hit = drawn.inside.find(
        (trade) => seconds(trade.entry_time) <= at && at <= seconds(trade.exit_time),
      )
      onSelect(hit ?? null)
    }
    chart.current.subscribeClick(handler)
    return () => chart.current?.unsubscribeClick(handler)
  }, [ready, drawn, onSelect])

  // The readout describes the trades on screen, and the footer says how many
  // are not. Summing the whole ledger under a chart showing a fortnight of it
  // would be a number that does not belong to the picture.
  const summary = useMemo(() => {
    const shown = drawn.inside
    const net = shown.reduce((total, trade) => total + trade.net_pnl, 0)
    const wins = shown.filter((trade) => trade.net_pnl > 0).length
    return { net, wins, count: shown.length }
  }, [drawn])

  return (
    <section className="strategy-chart">
      <header className="chart-toolbar">
        <div className="ctl" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((key) => (
            <button
              key={key}
              type="button"
              className={key === timeframe ? 'tf active' : 'tf'}
              aria-pressed={key === timeframe}
              onClick={() => onTimeframe(key)}
            >
              {key}
            </button>
          ))}
        </div>
        <div className="chart-readout mono">
          <span>{summary.count} shown</span>
          <span>
            {summary.wins}W · {summary.count - summary.wins}L
          </span>
          <span className={summary.net >= 0 ? 'up' : 'down'}>
            {summary.net >= 0 ? '+' : ''}
            {summary.net.toFixed(2)}
          </span>
        </div>
      </header>

      {bars.isError ? (
        <div className="chart-state error" role="alert" style={{ height }}>
          <strong>Market data unavailable</strong>
          <p>{(bars.error as Error).message}</p>
          <p className="chart-note">
            The trades are real and listed below. No substitute candles are drawn.
          </p>
        </div>
      ) : (
        <div className="price-chart" style={{ height }}>
          <div ref={container} className="price-chart-canvas" />
          {bars.isPending && (
            <div className="chart-state loading" role="status">
              Loading bars…
            </div>
          )}
        </div>
      )}

      <footer className="chart-provenance mono">
        <span>run {ledger.backtest_id.slice(0, 18)}</span>
        <span>{ledger.dataset_key ?? 'dataset not recorded'}</span>
        <span>{ledger.evidence_tier}</span>
        {bars.data?.convention && <span>{bars.data.convention}</span>}
        {drawn.outside > 0 && (
          <span className="warn">
            {drawn.outside.toLocaleString()} trade(s) outside this window — step or
            filter to reach them
          </span>
        )}
        {ledger.truncated && (
          <span className="warn">
            showing {ledger.returned_trades} of {ledger.matched_trades} matched
          </span>
        )}
      </footer>
    </section>
  )
}
