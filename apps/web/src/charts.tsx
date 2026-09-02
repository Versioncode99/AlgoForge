import ReactECharts from 'echarts-for-react'

const AXIS = {
  axisLine: { lineStyle: { color: '#2a3037' } },
  axisTick: { show: false },
  axisLabel: { color: '#616a73', fontFamily: 'IBM Plex Mono', fontSize: 9 },
  splitLine: { lineStyle: { color: '#1a1e22' } },
}

const BASE = {
  animation: false,
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis' as const,
    backgroundColor: '#131619',
    borderColor: '#303740',
    textStyle: { color: '#e6eaee', fontFamily: 'IBM Plex Mono', fontSize: 11 },
  },
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
      color: index === 0 ? '#3ddc97' : index % 4 === 0 ? '#f2615c' : '#39c5cf',
    },
  }))
  return (
    <ReactECharts
      style={{ height: 300 }}
      option={{
        ...BASE,
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', ...AXIS },
        yAxis: { type: 'value', ...AXIS },
        series,
      }}
    />
  )
}

export function RegimeChart({ items }: { items: { name: string; net_pnl: number }[] }) {
  return (
    <ReactECharts
      style={{ height: 240 }}
      option={{
        ...BASE,
        grid: { left: 54, right: 12, top: 14, bottom: 34 },
        xAxis: { type: 'category', data: items.map((x) => x.name), ...AXIS },
        yAxis: { type: 'value', ...AXIS },
        series: [
          {
            type: 'bar',
            barWidth: '46%',
            data: items.map((x) => ({
              value: x.net_pnl,
              itemStyle: { color: x.net_pnl >= 0 ? '#3ddc97' : '#f2615c', borderRadius: [2, 2, 0, 0] },
            })),
          },
        ],
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
        ...BASE,
        grid: { left: 58, right: 18, top: 16, bottom: 32 },
        xAxis: { type: 'category', data: equity.map((_, i) => i), ...AXIS },
        yAxis: { type: 'value', ...AXIS },
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
        ...BASE,
        grid: { left: 58, right: 18, top: 16, bottom: 40 },
        xAxis: {
          type: 'category',
          name: label,
          nameLocation: 'middle',
          nameGap: 24,
          nameTextStyle: { color: '#444c54', fontFamily: 'IBM Plex Mono', fontSize: 9 },
          data: points.map((p) => p.value),
          ...AXIS,
        },
        yAxis: { type: 'value', ...AXIS },
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
        ...BASE,
        grid: { left: 58, right: 18, top: 16, bottom: 34 },
        xAxis: { type: 'category', data: points.map((p) => p.day), name: 'day', ...AXIS },
        yAxis: {
          type: 'value', min: 0, max: 1, ...AXIS,
          axisLabel: { ...AXIS.axisLabel, formatter: (value: number) => `${Math.round(value * 100)}%` },
        },
        series: [{
          type: 'line', data: points.map((p) => p.probability), showSymbol: false,
          lineStyle: { width: 1.8, color: '#3ddc97' },
          areaStyle: { color: 'rgba(61,220,151,.12)' },
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
        ...BASE,
        grid: { left: 58, right: 18, top: 16, bottom: 48 },
        xAxis: {
          type: 'category', data: bins.map((b) => Math.round((b.lower + b.upper) / 2)),
          name: 'terminal P&L', nameLocation: 'middle', nameGap: 32, ...AXIS,
        },
        yAxis: { type: 'value', ...AXIS },
        series: [{
          type: 'bar', barWidth: '88%',
          data: bins.map((b) => ({
            value: b.count,
            itemStyle: { color: (b.lower + b.upper) / 2 >= 0 ? '#3ddc97' : '#f2615c' },
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
        ...BASE,
        tooltip: {
          ...BASE.tooltip, trigger: 'item',
          formatter: (item: { data: [number, number, string] }) =>
            `${item.data[2]}<br/>terminal ${item.data[0].toFixed(0)}<br/>drawdown ${item.data[1].toFixed(0)}`,
        },
        grid: { left: 62, right: 18, top: 18, bottom: 48 },
        xAxis: { type: 'value', name: 'terminal P&L', nameLocation: 'middle', nameGap: 30, ...AXIS },
        yAxis: { type: 'value', name: 'max drawdown', ...AXIS },
        series: [{
          type: 'scatter', symbolSize: 5,
          data: points.slice(0, 1200).map((p) => ({
            value: [p.terminal_pnl, p.max_drawdown, p.outcome],
            itemStyle: {
              color: p.outcome === 'PASS' ? '#3ddc97' : p.outcome === 'FAIL' ? '#f2615c' : '#d5a84b',
              opacity: .55,
            },
          })),
        }],
      }}
    />
  )
}
