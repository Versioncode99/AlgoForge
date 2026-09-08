import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { ChartPanel, type TimeframeKey } from '../components/PriceChart'
import type { DatasetInfo } from '../types'

/* Charts over the archives AlgoForge owns.
 *
 * One chart today, and a layout that already assumes there will be several:
 * the panel is a self-contained component with its own symbol and timeframe, so
 * multi-chart is adding more of them to a grid rather than rewriting this. That
 * is deliberate — a chart bolted into a page has to be torn out again when the
 * workspace engine starts placing it.
 */
export function ChartsView() {
  const datasets = useQuery({
    queryKey: ['datasets'],
    queryFn: () => getJson<DatasetInfo[]>('/datasets'),
  })

  const rows = datasets.data ?? []
  // Charting needs bars that exist. An unavailable archive stays listed and
  // disabled rather than hidden, so "we do not have this" is visible.
  const first = rows.find((row) => row.available && row.is_real) ?? rows[0]
  const [dataset, setDataset] = useState<string>('')
  const [timeframe, setTimeframe] = useState<TimeframeKey>('1h')
  const active = dataset || first?.key || ''

  if (datasets.isPending) {
    return (
      <div className="state" role="status">
        Loading the dataset registry…
      </div>
    )
  }

  if (datasets.isError) {
    return (
      <div className="state error" role="alert">
        Dataset registry unavailable: {(datasets.error as Error).message}
      </div>
    )
  }

  if (!active) {
    return (
      <div className="state" role="status">
        No datasets are registered yet.
      </div>
    )
  }

  return (
    <div className="charts-view">
      <ChartPanel
        dataset={active}
        timeframe={timeframe}
        onDataset={setDataset}
        onTimeframe={setTimeframe}
        datasets={rows.map((row) => ({
          key: row.key,
          label: row.label,
          available: row.available,
          is_real: row.is_real,
        }))}
        height={560}
      />
    </div>
  )
}
