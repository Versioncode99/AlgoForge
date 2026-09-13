import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import {
  EMPTY,
  type Bar,
  type BarsResponse,
  type Direction,
  type LogicalRange,
  type Series,
  adopt,
  describes,
  merge,
  newerThan,
  olderThan,
  shift,
  wants,
} from '../bars'

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

/* The series types live in `../bars`, alongside the merge arithmetic that
 * maintains them, and are re-exported here because every existing caller
 * imports them from the component. */
export type { Bar, BarsResponse } from '../bars'

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

/** How many bars a first load asks for, and how many each pan adds.
 *
 * A window rather than a dataset. Sixteen years of one-minute NQ is millions of
 * candles, and the reason to page is that pulling them into a browser is not a
 * slow version of the right answer — it is a different, worse application. */
const PAGE = 1500

export function PriceChart({
  dataset,
  timeframe,
  limit = PAGE,
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

  const [series, setSeries] = useState<Series>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(true)
  /* `paging` is a ref rather than state on purpose: the visible-range handler
   * fires many times per drag, and a state flag would be a frame behind on
   * every one of them — which is how one pan becomes six identical requests. */
  const paging = useRef(false)
  const latest = useRef<Series>(EMPTY)
  latest.current = series
  /* Bumped whenever the instrument or timeframe changes, so a response for the
   * previous one is discarded on arrival rather than merged into the series
   * now on screen. Without it, switching symbol mid-request draws two markets
   * on one chart and reports no error at all. */
  const generation = useRef(0)

  const fetchPage = useCallback(
    async (cursor: { direction: Direction; at: string } | null) => {
      const query = new URLSearchParams({ dataset, timeframe, limit: String(limit) })
      if (cursor) query.set(cursor.direction, cursor.at)
      return getJson<BarsResponse>(`/bars?${query.toString()}`)
    },
    [dataset, timeframe, limit],
  )

  // The first window: the newest bars, and whatever the archive says sits
  // behind them.
  useEffect(() => {
    generation.current += 1
    const mine = generation.current
    setSeries(EMPTY)
    setError(null)
    setPending(true)
    paging.current = false
    let live = true

    fetchPage(null)
      .then((page) => {
        if (!live || generation.current !== mine) return
        if (!describes(page, dataset, timeframe)) return
        setSeries(adopt(page))
        setPending(false)
      })
      .catch((cause: unknown) => {
        if (!live || generation.current !== mine) return
        setError(cause instanceof Error ? cause.message : 'Unknown error')
        setPending(false)
      })

    return () => {
      live = false
    }
  }, [dataset, timeframe, fetchPage])

  /** Load one more window at whichever edge the viewport reached for.
   *
   * The viewport is captured before the merge and restored after it. Prepending
   * shifts every existing bar's index by the number of bars added, so a
   * viewport left alone would be looking that far further back — the chart
   * appearing to jump away at the instant it delivered what was asked for. */
  const extend = useCallback(
    async (direction: Direction) => {
      const held = latest.current
      const at = direction === 'before' ? olderThan(held) : newerThan(held)
      if (!at || paging.current) return
      paging.current = true
      const mine = generation.current
      const before = chart.current?.timeScale().getVisibleLogicalRange() ?? null

      try {
        const page = await fetchPage({ direction, at })
        if (generation.current !== mine) return
        if (!describes(page, dataset, timeframe)) return
        const grown = merge(held, page, direction)
        const added = grown.bars.length - held.bars.length
        setSeries(grown)
        if (direction === 'before' && added > 0 && before) {
          // Restored on the next frame: the series has to be on the chart
          // before a range over it means anything.
          requestAnimationFrame(() => {
            if (generation.current !== mine) return
            chart.current?.timeScale().setVisibleLogicalRange(shift(before, added))
          })
        }
      } catch (cause: unknown) {
        // A failed pan leaves the chart exactly as it was. It is not an empty
        // state: the bars already drawn are still real.
        if (generation.current === mine) {
          setError(cause instanceof Error ? cause.message : 'Unknown error')
        }
      } finally {
        paging.current = false
      }
    },
    [dataset, timeframe, fetchPage],
  )

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

  /* Dragging toward an edge is what asks for more history. Subscribed once,
   * reading the series through a ref, because re-subscribing on every merge
   * would tear down the handler in the middle of the drag that triggered it. */
  useEffect(() => {
    if (!ready || !chart.current) return
    const scale = chart.current.timeScale()
    const handler = (range: LogicalRange | null) => {
      const direction = wants(latest.current, range)
      if (direction) void extend(direction)
    }
    scale.subscribeVisibleLogicalRangeChange(handler)
    return () => scale.unsubscribeVisibleLogicalRangeChange(handler)
  }, [ready, extend])

  const bars = series.bars
  /* True until the first window has been drawn. Only that first draw may move
   * the viewport: calling `fitContent` after a pan would undo the pan. */
  const seeded = useRef(false)
  useEffect(() => {
    seeded.current = false
  }, [dataset, timeframe])

  useEffect(() => {
    if (!ready || !candles.current || !volume.current) return
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
    if (!seeded.current && bars.length) {
      chart.current?.timeScale().fitContent()
      seeded.current = true
    }
  }, [ready, bars])

  // The crosshair reads values out to whoever asked, so the readout can live
  // outside the canvas in real DOM that a screen reader can reach.
  useEffect(() => {
    if (!ready || !chart.current || !onHover) return
    const byTime = new Map(bars.map((bar) => [seconds(bar.time), bar]))
    const handler = (param: { time?: Time }) => {
      onHover(param.time ? (byTime.get(param.time as UTCTimestamp) ?? null) : null)
    }
    chart.current.subscribeCrosshairMove(handler)
    return () => chart.current?.unsubscribeCrosshairMove(handler)
  }, [ready, bars, onHover])

  if (error && bars.length === 0) {
    return (
      <div className="chart-state error" role="alert" style={{ height }}>
        <strong>Market data unavailable</strong>
        <p>{error}</p>
        <p className="chart-note">
          No substitute series is drawn. Import or download the archive and the chart will fill in.
        </p>
      </div>
    )
  }

  return (
    <div className="price-chart" style={{ height }}>
      <div ref={container} className="price-chart-canvas" />
      {pending && (
        <div className="chart-state loading" role="status">
          Loading bars…
        </div>
      )}
      {!pending && bars.length === 0 && (
        <div className="chart-state empty" role="status">
          No bars in this range.
        </div>
      )}
      {/* Stated rather than silent: a chart that has reached the start of its
          archive looks identical to one that simply stopped loading. */}
      {bars.length > 0 && !series.hasMoreBefore && (
        <div className="chart-edge" role="status">
          Start of archive
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
