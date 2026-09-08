import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { ChartPanel, type TimeframeKey } from './PriceChart'
import type { DatasetInfo } from '../types'

/* What goes inside a panel.
 *
 * Some kinds are backed by a real endpoint and show real records. Some are
 * surfaces the product intends to have and does not have yet. The rule is that
 * the second kind must *say so* — a DOM panel drawing plausible depth would be
 * indistinguishable from one wired to a broker, and that is the single most
 * dangerous thing this application could render. So an unbuilt panel says it is
 * unbuilt, and an empty one says it is empty, and neither pretends.
 */

type PanelProps = {
  kind: string
  settings: Record<string, unknown>
  datasets: DatasetInfo[]
  onSetting: (key: string, value: string) => void
}

/** A panel that does not exist yet, labelled as scaffolding rather than dressed up. */
function NotBuilt({ what, why }: { what: string; why: string }) {
  return (
    <div className="panel-state scaffold" role="status">
      <strong>{what} is not built yet</strong>
      <p>{why}</p>
    </div>
  )
}

function Loading() {
  return (
    <div className="panel-state" role="status">
      Loading…
    </div>
  )
}

function Failed({ error }: { error: unknown }) {
  return (
    <div className="panel-state error" role="alert">
      {error instanceof Error ? error.message : 'Request failed'}
    </div>
  )
}

function Empty({ message }: { message: string }) {
  return (
    <div className="panel-state" role="status">
      {message}
    </div>
  )
}

function StrategiesPanel() {
  const query = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<Record<string, unknown>[]>('/strategies') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No strategies have been created yet." />
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Strategy</th><th>Family</th><th className="num">Runs</th></tr>
      </thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.strategy_id)}>
            <td className="mono">{String(row.name ?? row.strategy_id)}</td>
            <td>{String(row.family ?? '—')}</td>
            <td className="num mono">{String(row.backtest_count ?? 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Run snapshots: the immutable execution ledger, not backtest results.
 *
 * `/runs` returns what was executed and under which tier — the provenance
 * record — so this shows that rather than P&L. Reading a net figure off this
 * endpoint would have meant inventing one. */
function RunsPanel() {
  const query = useQuery({ queryKey: ['runs'], queryFn: () => getJson<Record<string, unknown>[]>('/runs') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No runs have been recorded yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Run</th><th>Tier</th><th>Status</th></tr></thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.run_id)}>
            <td className="mono">{String(row.run_id ?? '—').slice(0, 22)}</td>
            <td className="mono">{String(row.tier ?? '—')}</td>
            <td>{String(row.status ?? '—')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ActivityPanel() {
  const query = useQuery({
    queryKey: ['activity'],
    queryFn: () => getJson<Record<string, unknown>[]>('/activity'),
    refetchInterval: 5000,
  })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="Nothing has happened yet." />
  return (
    <ul className="panel-log">
      {rows.slice(0, 120).map((row, index) => (
        <li key={index} className={`is-${String(row.level ?? 'info')}`}>
          <span className="stage mono">{String(row.stage ?? '')}</span>
          <span>{String(row.message ?? '')}</span>
        </li>
      ))}
    </ul>
  )
}

function DataHealthPanel({ datasets }: { datasets: DatasetInfo[] }) {
  if (!datasets.length) return <Empty message="No datasets are registered." />
  return (
    <table className="panel-table">
      <thead><tr><th>Dataset</th><th className="num">Bars</th><th>Source</th><th>State</th></tr></thead>
      <tbody>
        {datasets.map((row) => (
          <tr key={row.key}>
            <td className="mono">{row.label}</td>
            <td className="num mono">{row.bar_count ? row.bar_count.toLocaleString() : '—'}</td>
            <td>{row.provider}</td>
            {/* Three distinct states, never collapsed into a tick. */}
            <td className={row.available ? 'ok' : 'missing'}>
              {row.available ? (row.is_real ? 'available' : 'fixture') : 'not present'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MemoryPanel() {
  const query = useQuery({ queryKey: ['memory'], queryFn: () => getJson<{ counts: Record<string, number>; total: number }>('/memory') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const counts = query.data?.counts ?? {}
  const entries = Object.entries(counts)
  if (!entries.length) return <Empty message="Research memory is empty — nothing has failed yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Failure class</th><th className="num">Count</th></tr></thead>
      <tbody>
        {entries.sort((a, b) => b[1] - a[1]).map(([name, count]) => (
          <tr key={name}><td className="mono">{name}</td><td className="num mono">{count}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function ExperimentsPanel() {
  const query = useQuery({ queryKey: ['experiments'], queryFn: () => getJson<Record<string, unknown>[]>('/experiments') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No experiments have been recorded yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Experiment</th><th>Status</th></tr></thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.id)}>
            <td className="mono">{String(row.template ?? row.id)}</td>
            <td>{String(row.status ?? '—')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function WatchlistPanel({ settings, onSetting }: { settings: Record<string, unknown>; onSetting: (k: string, v: string) => void }) {
  // Stored on the panel itself, so it travels with the workspace and survives a
  // restart through the same store as everything else on the layout.
  const symbols = String(settings.symbols ?? '')
    .split(',')
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)

  return (
    <div className="panel-watchlist">
      <label className="watchlist-input">
        <span className="sr-only">Watchlist symbols, comma separated</span>
        <input
          aria-label="Watchlist symbols"
          defaultValue={symbols.join(', ')}
          placeholder="NQ, ES, GC"
          onBlur={(event) => onSetting('symbols', event.target.value)}
        />
      </label>
      {symbols.length === 0 ? (
        <Empty message="No symbols yet. Type them above." />
      ) : (
        <ul className="panel-symbols">
          {symbols.map((symbol) => (
            <li key={symbol} className="mono">
              {symbol}
              {/* No price: this build has no quote stream, and a number here
                  would be invented. */}
              <span className="no-quote">no live quote</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function PanelBody({ kind, settings, datasets, onSetting }: PanelProps) {
  switch (kind) {
    case 'chart': {
      // A panel can name either a dataset or a symbol, and templates name the
      // symbol because that is what a person says. Resolving symbol -> dataset
      // here is what stops an "ES" panel quietly drawing NQ because it fell
      // through to the first available archive.
      const wanted = String(settings.symbol ?? '').toUpperCase()
      const bySymbol = wanted
        ? datasets.find((d) => d.symbol.toUpperCase() === wanted && d.available)
        : undefined
      const dataset =
        String(settings.dataset ?? '') ||
        bySymbol?.key ||
        datasets.find((d) => d.available && d.is_real)?.key ||
        datasets[0]?.key ||
        ''
      if (wanted && !bySymbol && !settings.dataset) {
        return (
          <Empty
            message={`No archive for ${wanted}. Add one, or pick a different symbol above.`}
          />
        )
      }
      if (!dataset) return <Empty message="No datasets are registered." />
      return (
        <ChartPanel
          dataset={dataset}
          timeframe={(String(settings.timeframe ?? '1h') as TimeframeKey) || '1h'}
          onDataset={(key) => onSetting('dataset', key)}
          onTimeframe={(key) => onSetting('timeframe', key)}
          datasets={datasets.map((d) => ({
            key: d.key,
            label: d.label,
            available: d.available,
            is_real: d.is_real,
          }))}
          height={0}
        />
      )
    }
    case 'strategies':
      return <StrategiesPanel />
    case 'runs':
      return <RunsPanel />
    case 'experiments':
      return <ExperimentsPanel />
    case 'activity':
    case 'logs':
      return <ActivityPanel />
    case 'data_health':
      return <DataHealthPanel datasets={datasets} />
    case 'research_memory':
      return <MemoryPanel />
    case 'watchlist':
      return <WatchlistPanel settings={settings} onSetting={onSetting} />

    // Execution surfaces. Deliberately inert: this build is paper-only and has
    // no connector, so anything drawn here would be fiction about an account.
    case 'dom':
    case 'order_ticket':
    case 'positions':
    case 'orders':
    case 'account':
      return (
        <NotBuilt
          what={kind.replace('_', ' ')}
          why="No broker connector exists in this build, and AlgoForge is paper-only. Depth, orders and positions will appear here when a connector is added — never before."
        />
      )
    case 'risk':
      return (
        <NotBuilt
          what="Risk"
          why="Prop rule simulation lives in Prop Simulation today. A live risk panel needs positions, which needs a connector."
        />
      )
    case 'replay':
      return (
        <NotBuilt
          what="Replay"
          why="The bars and the aggregation are in place; the playback controls are not written yet."
        />
      )
    default:
      return (
        <NotBuilt
          what={kind.replace('_', ' ')}
          why="This panel kind is registered but has no view yet."
        />
      )
  }
}
