import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComponentProps } from 'react'

import { seriesOf, type FanPoint } from './fan'
import { token, useAppearanceVersion } from './theme'

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])
/* `key` on the appearance is what makes a theme change repaint a canvas. An
 * echarts instance keeps the option object it was built with; setting a new
 * one merges rather than replaces, which left old colours behind. */
function ReactECharts(props: Omit<ComponentProps<typeof ReactEChartsCore>, 'echarts'>) {
  const appearance = useAppearanceVersion()
  return <ReactEChartsCore key={appearance} {...props} echarts={echarts} />
}

/* These were module constants, evaluated once when the bundle loaded. That made
 * every chart in the application permanently the colour of whichever theme
 * happened to be compiled in — changing the theme repainted the interface
 * around a chart that stayed dark. They are functions now, called on each
 * render, and the components carry the appearance in their React `key` so a
 * theme change rebuilds them. */
function axis() {
  return {
    axisLine: { lineStyle: { color: token('--chart-axis', '#2a3037') } },
    axisTick: { show: false },
    axisLabel: {
      color: token('--chart-text', '#616a73'),
      fontFamily: 'IBM Plex Mono',
      fontSize: 9,
    },
    splitLine: { lineStyle: { color: token('--chart-grid', '#1a1e22') } },
  }
}

function base() {
  return {
    animation: false,
    backgroundColor: token('--chart-bg', 'transparent'),
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: token('--bg-3', '#131619'),
      borderColor: token('--line-strong', '#303740'),
      textStyle: {
        color: token('--fg-0', '#e6eaee'),
        fontFamily: 'IBM Plex Mono',
        fontSize: 11,
      },
    },
  }
}

/** The percentile band of account balances, day by day, over every path.
 *
 * The bands are drawn as a transparent floor at p05 with three stacked areas on
 * top of it, which is how echarts expresses a band. The arithmetic for that is
 * in `fan.ts` and tested there, because a band drawn one stack out of place is
 * wrong in a way that still looks like a chart.
 *
 * `marked` is the scrubbed day and is drawn as a vertical rule, so the number
 * being read in the panel below has a visible position in the shape above it.
 */
export function FanBands({
  points,
  marked,
  onScrub,
  height = 300,
}: {
  points: FanPoint[]
  marked: number
  onScrub?: (day: number) => void
  height?: number
}) {
  const s = seriesOf(points)
  const band = (data: number[], opacity: number) => ({
    type: 'line' as const,
    stack: 'fan',
    data,
    showSymbol: false,
    silent: true,
    lineStyle: { width: 0 },
    areaStyle: { color: token('--s3', '#39c5cf'), opacity },
  })
  return (
    <ReactECharts
      style={{ height }}
      onEvents={
        onScrub
          ? {
              updateAxisPointer: (event: { dataIndex?: number }) => {
                if (typeof event.dataIndex === 'number') onScrub(s.days[event.dataIndex])
              },
            }
          : undefined
      }
      option={{
        ...base(),
        tooltip: { ...base().tooltip, axisPointer: { type: 'line' } },
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', data: s.days, ...axis() },
        yAxis: { type: 'value', scale: true, ...axis() },
        series: [
          /* The floor. Stacked under the bands and never drawn. */
          {
            type: 'line' as const,
            stack: 'fan',
            data: s.base,
            showSymbol: false,
            silent: true,
            lineStyle: { width: 0 },
            areaStyle: { opacity: 0 },
            tooltip: { show: false },
          },
          band(s.lower, 0.14),
          band(s.middle, 0.3),
          band(s.upper, 0.14),
          {
            type: 'line' as const,
            name: 'median',
            data: s.median,
            showSymbol: false,
            lineStyle: { width: 1.6, color: token('--chart-up', '#3ddc97') },
            markLine: {
              silent: true,
              symbol: 'none',
              label: { show: false },
              lineStyle: { color: token('--fg-2', '#8b949e'), width: 1, type: 'solid' },
              data: [{ xAxis: String(marked) }],
            },
          },
        ],
      }}
    />
  )
}

/** A p05-median-p95 band over a resampled equity curve.
 *
 * Three lines rather than the five of `FanBands`, because three is what a
 * resample comparison actually produces. Padding it out to five by repeating
 * the outer pair would draw an interquartile band that was never computed --
 * a shape carrying a claim nothing measured.
 */
export function PathBand({
  p05,
  median,
  p95,
  height = 240,
}: {
  p05: number[]
  median: number[]
  p95: number[]
  height?: number
}) {
  const span = median.map((_, index) => (p95[index] ?? 0) - (p05[index] ?? 0))
  return (
    <ReactECharts
      style={{ height }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', data: median.map((_, index) => index), ...axis() },
        yAxis: { type: 'value', scale: true, ...axis() },
        series: [
          {
            type: 'line' as const,
            stack: 'band',
            data: p05,
            showSymbol: false,
            silent: true,
            lineStyle: { width: 0 },
            areaStyle: { opacity: 0 },
            tooltip: { show: false },
          },
          {
            type: 'line' as const,
            stack: 'band',
            name: 'p05–p95',
            data: span,
            showSymbol: false,
            silent: true,
            lineStyle: { width: 0 },
            areaStyle: { color: token('--s3', '#39c5cf'), opacity: 0.2 },
          },
          {
            type: 'line' as const,
            name: 'median',
            data: median,
            showSymbol: false,
            lineStyle: { width: 1.6, color: token('--chart-up', '#3ddc97') },
            markLine: {
              silent: true,
              symbol: 'none',
              label: { show: false },
              lineStyle: { color: token('--chart-axis', '#2a3037'), width: 1 },
              data: [{ yAxis: 0 }],
            },
          },
        ],
      }}
    />
  )
}

export function EquityChart({ paths, start = 0 }: { paths: number[][]; start?: number }) {
  const series = paths.slice(0, 48).map((path, index) => ({
    type: 'line' as const,
    data: path.map((v) => v + start),
    showSymbol: false,
    silent: true,
    lineStyle: {
      width: index === 0 ? 1.6 : 0.6,
      opacity: index === 0 ? 1 : 0.26,
      color:
        index === 0
          ? token('--chart-up', '#3ddc97')
          : index % 4 === 0
            ? token('--chart-down', '#f2615c')
            : token('--s3', '#39c5cf'),
    },
  }))
  return (
    <ReactECharts
      style={{ height: 300 }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', ...axis() },
        yAxis: { type: 'value', ...axis() },
        series,
      }}
    />
  )
}

/** A single realised equity curve with the zero line drawn, because the sign of
 *  the result is the first thing anyone needs to read. */
export function CurveChart({ equity, height = 230 }: { equity: number[]; height?: number }) {
  const positive = (equity.at(-1) ?? 0) >= 0
  const stroke = positive ? '#3ddc97' : '#f2615c'
  return (
    <ReactECharts
      style={{ height }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', data: equity.map((_, i) => i), ...axis() },
        yAxis: { type: 'value', ...axis() },
        series: [
          {
            type: 'line',
            data: equity,
            showSymbol: false,
            lineStyle: { width: 1.6, color: stroke },
            areaStyle: { color: positive ? 'rgba(61,220,151,.09)' : 'rgba(242,97,92,.09)' },
            markLine: {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#303740', width: 1, type: 'dashed' },
              data: [{ yAxis: 0 }],
            },
          },
        ],
      }}
    />
  )
}

/** Parameter sweep surface. Rendered deliberately in a muted palette and always
 *  labelled exploratory — a sweep result must never read as a headline. */
export function SweepChart({
  points,
  label,
}: {
  points: { value: number; net_pnl: number }[]
  label: string
}) {
  return (
    <ReactECharts
      style={{ height: 240 }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 40 },
        xAxis: {
          type: 'category',
          name: label,
          nameLocation: 'middle',
          nameGap: 24,
          nameTextStyle: { color: '#444c54', fontFamily: 'IBM Plex Mono', fontSize: 9 },
          data: points.map((p) => p.value),
          ...axis(),
        },
        yAxis: { type: 'value', ...axis() },
        series: [
          {
            type: 'bar',
            barWidth: '62%',
            data: points.map((p) => ({
              value: p.net_pnl,
              itemStyle: { color: p.net_pnl >= 0 ? '#39c5cf' : '#6e7681', borderRadius: [2, 2, 0, 0] },
            })),
          },
        ],
      }}
    />
  )
}

export function TargetReachChart({ points }: { points: { day: number; probability: number }[] }) {
  return (
    <ReactECharts
      style={{ height: 230 }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 34 },
        xAxis: { type: 'category', data: points.map((p) => p.day), name: 'day', ...axis() },
        yAxis: {
          type: 'value', min: 0, max: 1, ...axis(),
          axisLabel: { ...axis().axisLabel, formatter: (value: number) => `${Math.round(value * 100)}%` },
        },
        series: [{
          type: 'line', data: points.map((p) => p.probability), showSymbol: false,
          lineStyle: { width: 1.8, color: token('--chart-up', '#3ddc97') },
          areaStyle: { color: `color-mix(in srgb, ${token('--chart-up', '#3ddc97')} 12%, transparent)` },
        }],
      }}
    />
  )
}

export function TerminalHistogram({ bins }: { bins: { lower: number; upper: number; count: number }[] }) {
  return (
    <ReactECharts
      style={{ height: 230 }}
      option={{
        ...base(),
        grid: { left: 58, right: 18, top: 16, bottom: 48 },
        xAxis: {
          type: 'category', data: bins.map((b) => Math.round((b.lower + b.upper) / 2)),
          name: 'terminal P&L', nameLocation: 'middle', nameGap: 32, ...axis(),
        },
        yAxis: { type: 'value', ...axis() },
        series: [{
          type: 'bar', barWidth: '88%',
          data: bins.map((b) => ({
            value: b.count,
            itemStyle: {
              color:
                (b.lower + b.upper) / 2 >= 0
                  ? token('--chart-up', '#3ddc97')
                  : token('--chart-down', '#f2615c'),
            },
          })),
        }],
      }}
    />
  )
}

export function ReturnDrawdownChart({ points }: {
  points: { terminal_pnl: number; max_drawdown: number; outcome: string }[]
}) {
  return (
    <ReactECharts
      style={{ height: 280 }}
      option={{
        ...base(),
        tooltip: {
          ...base().tooltip, trigger: 'item',
          formatter: (item: { data: [number, number, string] }) =>
            `${item.data[2]}<br/>terminal ${item.data[0].toFixed(0)}<br/>drawdown ${item.data[1].toFixed(0)}`,
        },
        grid: { left: 62, right: 18, top: 18, bottom: 48 },
        xAxis: { type: 'value', name: 'terminal P&L', nameLocation: 'middle', nameGap: 30, ...axis() },
        yAxis: { type: 'value', name: 'max drawdown', ...axis() },
        series: [{
          type: 'scatter', symbolSize: 5,
          data: points.slice(0, 1200).map((p) => ({
            value: [p.terminal_pnl, p.max_drawdown, p.outcome],
            itemStyle: {
              color:
                p.outcome === 'PASS'
                  ? token('--chart-up', '#3ddc97')
                  : p.outcome === 'FAIL'
                    ? token('--chart-down', '#f2615c')
                    : token('--warn', '#d5a84b'),
              opacity: .55,
            },
          })),
        }],
      }}
    />
  )
}
