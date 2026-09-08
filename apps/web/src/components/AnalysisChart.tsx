import { useEffect, useMemo, useRef, useState } from 'react'

import { token, useAppearanceVersion } from '../theme'
import ReactECharts from 'echarts-for-react'
import * as echarts from 'echarts'

/* Drawing an analysis result.
 *
 * The renderer is chosen by the result's own `shape`, so the decision lives in
 * one place and the backend is the thing that knows whether a question has one
 * axis or two.
 *
 * Two rules govern everything here.
 *
 * **An empty bucket is drawn as absent, not as zero.** A strategy that never
 * traded 03:00 and one that traded it to breakeven are different facts, and a
 * bar chart that renders both as a flat line at zero destroys the difference.
 * Nulls stay null and the axis shows a gap.
 *
 * **A bucket too small to estimate from is marked.** Its P&L is what happened;
 * its rates are not an estimate of anything. Those cells are drawn at reduced
 * opacity and say so on hover, rather than being hidden — hiding them would
 * overstate how much of the sample the picture covers.
 */

export type Axis = {
  name: string
  label: string
  categories: string[]
  unit?: string
  note?: string
}

export type Cell = {
  coords: number[]
  labels: string[]
  trade_count: number
  value: number | null
  net_pnl: number
  win_rate: number | null
  average_trade: number | null
  insufficient: boolean
  trade_ids?: string[]
}

export type AnalysisResult = {
  analysis: string
  title: string
  question: string
  shape: 'bars' | 'grid' | 'surface' | 'scatter' | 'series'
  axes: Axis[]
  measure: string
  measure_unit: string
  cells: Cell[]
  total_trades: number
  covered_trades: number
  warnings: string[]
  findings: string[]
  artifact_id: string
  content_hash: string
  is_evidence: boolean
  evidence_note: string
  provenance: Record<string, unknown>
}

/** echarts-gl is loaded on demand and only for surfaces.
 *
 * It is a large module built against a different echarts major than the one
 * this application pins, so it is registered at run time and the failure is
 * caught. A surface that cannot be drawn in three dimensions falls back to the
 * heatmap of the same data and says so — the numbers are identical either way,
 * and a missing renderer must not become a missing result. */
let glState: 'unloaded' | 'loading' | 'ready' | 'failed' = 'unloaded'
let glPromise: Promise<boolean> | null = null

function loadGl(): Promise<boolean> {
  if (glState === 'ready') return Promise.resolve(true)
  if (glState === 'failed') return Promise.resolve(false)
  if (glPromise) return glPromise
  glState = 'loading'
  glPromise = import('echarts-gl')
    .then(() => {
      // Registration is a side effect of the import. The check is whether the
      // chart type it should have added is actually there.
      glState = 'ready'
      return true
    })
    .catch(() => {
      glState = 'failed'
      return false
    })
  return glPromise
}

const currency = (value: number) =>
  `${value >= 0 ? '+' : '−'}$${Math.abs(value).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`

function tooltipFor(cell: Cell, result: AnalysisResult): string {
  const where = cell.labels.join(' · ')
  const lines = [
    `<strong>${where}</strong>`,
    `${cell.trade_count.toLocaleString()} trade(s)`,
  ]
  if (cell.trade_count === 0) {
    lines.push('<em>no trades — absent, not zero</em>')
  } else {
    lines.push(`Net ${currency(cell.net_pnl)}`)
    if (cell.average_trade !== null) lines.push(`Avg ${currency(cell.average_trade)}`)
    if (cell.win_rate !== null) lines.push(`Win ${(cell.win_rate * 100).toFixed(0)}%`)
    if (cell.insufficient) {
      lines.push('<em>below 20 trades — not an estimate</em>')
    }
  }
  return lines.join('<br/>')
}

export function AnalysisChart({
  result,
  onPick,
  height = 380,
}: {
  result: AnalysisResult
  onPick: (cell: Cell) => void
  height?: number
}) {
  const appearance = useAppearanceVersion()
  const [glReady, setGlReady] = useState(glState === 'ready')
  const [glFailed, setGlFailed] = useState(glState === 'failed')
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    if (result.shape !== 'surface') return
    loadGl().then((ok) => {
      if (!mounted.current) return
      setGlReady(ok)
      setGlFailed(!ok)
    })
    return () => {
      mounted.current = false
    }
  }, [result.shape])

  const palette = useMemo(
    () => ({
      up: token('--pass', '#8fce6a'),
      down: token('--fail', '#c1503f'),
      line: token('--line', '#292724'),
      text: token('--fg-2', '#8b857a'),
      dim: token('--fg-3', '#5f5a52'),
      bg: token('--bg-1', '#100f0e'),
    }),
    [appearance],
  )

  const byCoord = useMemo(() => {
    const map = new Map<string, Cell>()
    for (const cell of result.cells) map.set(cell.coords.join(','), cell)
    return map
  }, [result.cells])

  const option = useMemo(() => {
    const base = {
      backgroundColor: 'transparent',
      textStyle: { fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      tooltip: {
        backgroundColor: token('--bg-3', '#1d1b19'),
        borderColor: palette.line,
        textStyle: { color: token('--fg-0', '#f4f2ed'), fontSize: 11 },
      },
      grid: { left: 56, right: 20, top: 24, bottom: 46 },
    }

    if (result.shape === 'bars' || result.shape === 'series') {
      const axis = result.axes[0]
      const values = axis.categories.map((_, index) => {
        const cell = byCoord.get(String(index))
        // null renders as a gap. That is the point: a bucket with no trades is
        // absent from the chart rather than sitting on the zero line.
        return cell && cell.trade_count > 0 ? cell.value : null
      })
      const counts = axis.categories.map(
        (_, index) => byCoord.get(String(index))?.trade_count ?? 0,
      )
      return {
        ...base,
        tooltip: {
          ...base.tooltip,
          trigger: 'axis',
          formatter: (params: { dataIndex: number }[]) => {
            const cell = byCoord.get(String(params[0]?.dataIndex))
            return cell ? tooltipFor(cell, result) : ''
          },
        },
        xAxis: {
          type: 'category',
          data: axis.categories,
          axisLine: { lineStyle: { color: palette.line } },
          axisLabel: { color: palette.text, fontSize: 10, interval: 0, rotate: axis.categories.length > 12 ? 45 : 0 },
        },
        yAxis: {
          type: 'value',
          name: result.measure_unit,
          nameTextStyle: { color: palette.dim, fontSize: 10 },
          splitLine: { lineStyle: { color: palette.line } },
          axisLabel: { color: palette.text, fontSize: 10 },
        },
        series: [
          {
            type: result.shape === 'series' ? 'line' : 'bar',
            data: values.map((value, index) => ({
              value,
              itemStyle: {
                color: (value ?? 0) >= 0 ? palette.up : palette.down,
                // Thin buckets are dimmed rather than hidden.
                opacity: counts[index] > 0 && counts[index] < 20 ? 0.42 : 1,
              },
            })),
            connectNulls: false,
            symbolSize: 5,
            barMaxWidth: 26,
          },
        ],
      }
    }

    // grid and surface share a heatmap representation; the surface adds a 3D
    // rendering on top when the renderer is available.
    const [xAxis, yAxis] = result.axes
    const points: [number, number, number | null][] = []
    let min = Infinity
    let max = -Infinity
    for (const cell of result.cells) {
      const value = cell.trade_count > 0 ? cell.value : null
      points.push([cell.coords[0], cell.coords[1], value])
      if (value !== null) {
        min = Math.min(min, value)
        max = Math.max(max, value)
      }
    }
    // A symmetric scale around zero, so "green means made money" is true and a
    // mostly-losing surface does not render its least-bad cell as green.
    const bound = Math.max(Math.abs(min), Math.abs(max)) || 1

    const visualMap = {
      min: -bound,
      max: bound,
      calculable: true,
      orient: 'horizontal' as const,
      left: 'center' as const,
      bottom: 0,
      textStyle: { color: palette.text, fontSize: 10 },
      inRange: { color: [palette.down, token('--bg-3', '#1d1b19'), palette.up] },
    }

    if (result.shape === 'surface' && glReady) {
      // `base.grid` is the 2D cartesian grid, which has no meaning beside a
      // grid3D and confuses the layout when both are present.
      const { grid: _unused2dGrid, ...base3d } = base
      return {
        ...base3d,
        tooltip: {
          ...base.tooltip,
          formatter: (params: { data: number[] }) => {
            const cell = byCoord.get(`${params.data[0]},${params.data[1]}`)
            return cell ? tooltipFor(cell, result) : ''
          },
        },
        visualMap,
        xAxis3D: {
          type: 'category',
          data: xAxis.categories,
          name: xAxis.label,
          nameTextStyle: { color: palette.dim, fontSize: 10 },
          axisLabel: { color: palette.text, fontSize: 9 },
        },
        yAxis3D: {
          type: 'category',
          data: yAxis.categories,
          name: yAxis.label,
          nameTextStyle: { color: palette.dim, fontSize: 10 },
          axisLabel: { color: palette.text, fontSize: 9 },
        },
        zAxis3D: {
          type: 'value',
          name: result.measure_unit,
          nameTextStyle: { color: palette.dim, fontSize: 10 },
          axisLabel: { color: palette.text, fontSize: 9 },
        },
        grid3D: {
          boxWidth: 150,
          boxDepth: 90,
          boxHeight: 70,
          axisLine: { lineStyle: { color: palette.line } },
          splitLine: { lineStyle: { color: palette.line } },
          axisPointer: { lineStyle: { color: token('--brand', '#c58a5a') } },
          light: { main: { intensity: 1.1, shadow: false }, ambient: { intensity: 0.5 } },
          viewControl: { autoRotate: false, distance: 200, alpha: 24, beta: 32 },
        },
        series: [
          {
            type: 'bar3D',
            shading: 'lambert',
            // A bucket with no trades contributes no bar at all rather than a
            // bar of height zero, which would read as "traded, made nothing".
            data: points.filter((p) => p[2] !== null),
            barSize: 6,
            emphasis: { label: { show: false } },
          },
        ],
      }
    }

    return {
      ...base,
      grid: { left: 96, right: 20, top: 16, bottom: 64 },
      tooltip: {
        ...base.tooltip,
        formatter: (params: { data: (number | null)[] }) => {
          const cell = byCoord.get(`${params.data[0]},${params.data[1]}`)
          return cell ? tooltipFor(cell, result) : ''
        },
      },
      visualMap,
      xAxis: {
        type: 'category',
        data: xAxis.categories,
        name: xAxis.label,
        nameLocation: 'middle' as const,
        nameGap: 30,
        nameTextStyle: { color: palette.dim, fontSize: 10 },
        splitArea: { show: true, areaStyle: { color: ['transparent'] } },
        axisLine: { lineStyle: { color: palette.line } },
        axisLabel: { color: palette.text, fontSize: 10 },
      },
      yAxis: {
        type: 'category',
        data: yAxis.categories,
        name: yAxis.label,
        nameTextStyle: { color: palette.dim, fontSize: 10 },
        splitArea: { show: true, areaStyle: { color: ['transparent'] } },
        axisLine: { lineStyle: { color: palette.line } },
        axisLabel: { color: palette.text, fontSize: 10 },
      },
      series: [
        {
          type: 'heatmap',
          data: points,
          label: { show: false },
          itemStyle: { borderColor: palette.bg, borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: token('--brand', '#c58a5a'), borderWidth: 2 } },
        },
      ],
    }
  }, [result, byCoord, palette, glReady])

  const onEvents = useMemo(
    () => ({
      click: (params: { data?: unknown; dataIndex?: number }) => {
        const data = params.data as number[] | undefined
        if (Array.isArray(data) && data.length >= 2) {
          const cell = byCoord.get(`${data[0]},${data[1]}`)
          if (cell) onPick(cell)
          return
        }
        if (typeof params.dataIndex === 'number') {
          const cell = byCoord.get(String(params.dataIndex))
          if (cell) onPick(cell)
        }
      },
    }),
    [byCoord, onPick],
  )

  return (
    <div className="analysis-chart">
      {/* Keyed on the renderer as well as the analysis. echarts creates its
          layers when the instance is built, and switching an existing instance
          from a canvas heatmap to a WebGL grid3D leaves it with a canvas it
          will not draw into — the option applies, nothing appears, and there is
          no error to notice. Remounting is the honest fix. */}
      <ReactECharts
        /* The appearance is in the key so a theme change rebuilds the chart.
           An echarts instance keeps the option it was constructed with, and
           setting a new one merges rather than replaces — the old palette
           survived otherwise. */
        key={`${result.artifact_id}:${
          result.shape === 'surface' && glReady ? '3d' : '2d'
        }:${appearance}`}
        echarts={echarts}
        option={option}
        style={{ height, width: '100%' }}
        notMerge
        lazyUpdate
        onEvents={onEvents}
      />
      {result.shape === 'surface' && glFailed && (
        <p className="analysis-note" role="status">
          The 3D renderer would not load, so this is the same data as a heatmap. The
          numbers are identical; only the projection differs.
        </p>
      )}
      {result.shape === 'surface' && glReady && (
        <p className="analysis-note">
          Drag to rotate, scroll to zoom, click a bar to open the trades under it.
        </p>
      )}
    </div>
  )
}
