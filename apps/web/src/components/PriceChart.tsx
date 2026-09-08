import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { getJson } from '../api'

/* A price chart over real bars, or nothing.
 *
 * Every candle here came out of a purchased archive. There is no generated
 * fallback and no demo series: when a dataset is missing the chart says so and
 * draws nothing, because a chart that invents prices is worse than an empty one
 * — it is indistinguishable from a real one, and people trust charts.
 *
 * Rendering is lightweight-charts rather than the echarts already in the
 * bundle. echarts draws the analytical charts here well, but a price chart is a
 * different job: a million-bar series, a price scale that behaves the way a
 * trader expects, and a crosshair that reads values rather than decorating.
 */

export type Bar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export type BarsResponse = {
  dataset: string
  symbol: string
  timeframe: string
  timeframe_label: string
  convention: string
  authority: string
  is_real: boolean
  bar_count: number
  total_bars: number
  has_more: boolean
  bars: Bar[]
}

export const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as const
export type TimeframeKey = (typeof TIMEFRAMES)[number]

const seconds = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

/** Chart colours, read from the design tokens so one palette governs everything. */
function palette() {
  const read = (name: string, fallback: string) => {
    if (typeof window === 'undefined') return fallback
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return value || fallback
  }
  return {
    background: read('--bg-1', '#100f0e'),
    text: read('--fg-2', '#8b857a'),
    line: read('--line', '#292724'),
    up: read('--pass', '#8fce6a'),
    down: read('--fail', '#c1503f'),
  }
}

export function PriceChart({
  dataset,
  timeframe,
  limit = 1500,
  height = 420,
  onHover,
}: {
  dataset: string
  timeframe: TimeframeKey
  limit?: number
  height?: number
  onHover?: (bar: Bar | null) => void
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const chart = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volume = useRef<ISeriesApi<'Histogram'> | null>(null)
  const [ready, setReady] = useState(false)

  const query = useQuery({
    queryKey: ['bars', dataset, timeframe, limit],
    queryFn: () =>
      getJson<BarsResponse>(
        `/bars?dataset=${encodeURIComponent(dataset)}&timeframe=${timeframe}&limit=${limit}`,
      ),
    // Archives are immutable, so a series that has been fetched does not go
    // stale while the operator is looking at it.
    staleTime: 5 * 60_000,
  })

  // Create the chart once. Re-creating it on every data change would throw away
  // the pan and zoom the operator has set, which is the whole interaction.
  useEffect(() => {
    if (!container.current) return
    const colours = palette()
    const instance = createChart(container.current, {
      layout: {
        background: { color: colours.background },
        textColor: colours.text,
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: colours.line },
        horzLines: { color: colours.line },
      },
      rightPriceScale: { borderColor: colours.line, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderColor: colours.line, rightOffset: 4, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    })

    const candleSeries = instance.addSeries(CandlestickSeries, {
      upColor: colours.up,
      downColor: colours.down,
      borderUpColor: colours.up,
      borderDownColor: colours.down,
      wickUpColor: colours.up,
      wickDownColor: colours.down,
    })
    const volumeSeries = instance.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    // Volume sits in the bottom quarter, on its own invisible scale, so it
    // never competes with price for vertical room.
    instance.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    chart.current = instance
    candles.current = candleSeries
    volume.current = volumeSeries
    setReady(true)

    return () => {
      instance.remove()
      chart.current = null
      candles.current = null
      volume.current = null
      setReady(false)
    }
  }, [])

  const bars = query.data?.bars
  useEffect(() => {
    if (!ready || !bars || !candles.current || !volume.current) return
    const colours = palette()
    const candleData: CandlestickData<Time>[] = bars.map((bar) => ({
      time: seconds(bar.time),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }))
    const volumeData: HistogramData<Time>[] = bars.map((bar) => ({
      time: seconds(bar.time),
      value: bar.volume,
      color: bar.close >= bar.open ? `${colours.up}55` : `${colours.down}55`,
    }))
    candles.current.setData(candleData)
    volume.current.setData(volumeData)
    chart.current?.timeScale().fitContent()
  }, [ready, bars])

  // The crosshair reads values out to whoever asked, so the readout can live
  // outside the canvas in real DOM that a screen reader can reach.
  useEffect(() => {
    if (!ready || !chart.current || !onHover) return
    const byTime = new Map((bars ?? []).map((bar) => [seconds(bar.time), bar]))
    const handler = (param: { time?: Time }) => {
      onHover(param.time ? (byTime.get(param.time as UTCTimestamp) ?? null) : null)
    }
    chart.current.subscribeCrosshairMove(handler)
    return () => chart.current?.unsubscribeCrosshairMove(handler)
  }, [ready, bars, onHover])

  if (query.isError) {
    const message = query.error instanceof Error ? query.error.message : 'Unknown error'
    return (
      <div className="chart-state error" role="alert" style={{ height }}>
        <strong>Market data unavailable</strong>
        <p>{message}</p>
        <p className="chart-note">
          No substitute series is drawn. Import or download the archive and the chart will fill in.
        </p>
      </div>
    )
  }

  return (
    <div className="price-chart" style={{ height }}>
      <div ref={container} className="price-chart-canvas" />
      {query.isPending && (
        <div className="chart-state loading" role="status">
          Loading bars…
        </div>
      )}
      {query.data && query.data.bar_count === 0 && (
        <div className="chart-state empty" role="status">
          No bars in this range.
        </div>
      )}
    </div>
  )
}

/** The chart plus its own controls: symbol, timeframe, and an honest readout. */
export function ChartPanel({
  dataset,
  timeframe,
  onDataset,
  onTimeframe,
  datasets,
  height = 420,
}: {
  dataset: string
  timeframe: TimeframeKey
  onDataset: (key: string) => void
  onTimeframe: (key: TimeframeKey) => void
  datasets: { key: string; label: string; available: boolean; is_real: boolean }[]
  height?: number
}) {
  const [hovered, setHovered] = useState<Bar | null>(null)
  const query = useQuery({
    queryKey: ['bars-meta', dataset, timeframe],
    queryFn: () => getJson<BarsResponse>(`/bars?dataset=${dataset}&timeframe=${timeframe}&limit=1`),
    staleTime: 5 * 60_000,
  })

  const readout = useMemo(() => {
    if (!hovered) return null
    const change = hovered.close - hovered.open
    return { ...hovered, change, up: change >= 0 }
  }, [hovered])

  return (
    <section className="chart-panel">
      <header className="chart-toolbar">
        <label className="ctl">
          <span>Symbol</span>
          <select
            aria-label="Chart data set"
            value={dataset}
            onChange={(event) => onDataset(event.target.value)}
          >
            {datasets.map((item) => (
              <option key={item.key} value={item.key} disabled={!item.available}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

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

        <div className="chart-readout mono" aria-live="off">
          {readout ? (
            <>
              <span>O {readout.open.toFixed(2)}</span>
              <span>H {readout.high.toFixed(2)}</span>
              <span>L {readout.low.toFixed(2)}</span>
              <span>C {readout.close.toFixed(2)}</span>
              <span className={readout.up ? 'up' : 'down'}>
                {readout.up ? '+' : ''}
                {readout.change.toFixed(2)}
              </span>
              <span className="vol">V {readout.volume.toLocaleString()}</span>
            </>
          ) : (
            <span className="chart-hint">Hover for OHLC</span>
          )}
        </div>
      </header>

      <PriceChart
        dataset={dataset}
        timeframe={timeframe}
        height={height}
        onHover={setHovered}
      />

      {/* What the bars are, stated rather than implied. A daily bar's boundary
          is a convention, and an unlabelled one cannot be checked. */}
      {query.data && (
        <footer className="chart-provenance mono">
          <span>{query.data.total_bars.toLocaleString()} bars</span>
          <span>{query.data.timeframe_label}</span>
          {query.data.convention && <span>{query.data.convention}</span>}
          <span className={query.data.is_real ? 'real' : 'fixture'}>
            {query.data.is_real ? 'REAL DATA' : 'FIXTURE — cannot clear G0'}
          </span>
        </footer>
      )}
    </section>
  )
}
