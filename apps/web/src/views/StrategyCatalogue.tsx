import { ArrowDown, ArrowUp, GitCompare, Search, X } from 'lucide-react'
import { useDeferredValue, useMemo, useRef, useState } from 'react'
import { TierPill } from '../components/ui'
import { money, signed, stamp } from '../lib'
import type { StrategyListItem } from '../types'

type SortKey = 'name' | 'family' | 'tier' | 'net' | 'trades' | 'win' | 'dd' | 'runs' | 'run_at'
type Dir = 'asc' | 'desc'

const TIER_RANK: Record<string, number> = {
  HOLDOUT: 4, TRUTH_OOS: 3, VALIDATION_OOS: 3, FORWARD: 2,
  DEVELOPMENT_IN_SAMPLE: 1, LEGACY_IN_SAMPLE: 1, SYNTHETIC: 0,
}

const COLUMNS: { key: SortKey; label: string; num?: boolean }[] = [
  { key: 'name', label: 'Strategy' },
  { key: 'family', label: 'Family' },
  { key: 'tier', label: 'Evidence' },
  { key: 'net', label: 'Net', num: true },
  { key: 'trades', label: 'Trades', num: true },
  { key: 'win', label: 'Win rate', num: true },
  { key: 'dd', label: 'Max DD', num: true },
  { key: 'runs', label: 'Runs', num: true },
  { key: 'run_at', label: 'Last run' },
]

const ROW_H = 30
const OVERSCAN = 8

const tierRank = (tier?: string | null) => (tier ? TIER_RANK[tier] ?? 0 : 0)

const value = (item: StrategyListItem, key: SortKey): number | string => {
  switch (key) {
    case 'name': return item.name.toLowerCase()
    case 'family': return item.family
    // A legacy price-point run ranks below every current measurement: it is a
    // number in the wrong units, not weaker evidence of the same kind.
    case 'tier': return !item.latest ? -1
      : item.latest.calculation_version === 'legacy-price-points' ? -0.5
      : tierRank(item.latest.evidence_tier)
    case 'net': return item.latest?.net_pnl ?? Number.NEGATIVE_INFINITY
    case 'trades': return item.latest?.trade_count ?? -1
    case 'win': return item.latest?.win_rate ?? -1
    case 'dd': return item.latest?.max_drawdown ?? -1
    case 'runs': return item.backtest_count
    case 'run_at': return item.latest?.finished_at ?? ''
  }
}

/** The library as a catalogue rather than a scrolling list of cards.
 *
 * Four hundred records is a table, not a feed: the questions asked of it are
 * comparative ("which momentum strategies cleared out-of-sample, ranked by
 * drawdown"), and a card list can answer none of them. Rows are windowed so the
 * DOM holds roughly thirty of them however long the library grows.
 */
export function StrategyCatalogue({ items, pending, onOpen }: {
  items: StrategyListItem[]
  pending: boolean
  onOpen: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const [family, setFamily] = useState('')
  const [tier, setTier] = useState('')
  const [sort, setSort] = useState<SortKey>('tier')
  const [dir, setDir] = useState<Dir>('desc')
  const [compare, setCompare] = useState<string[]>([])
  const [scroll, setScroll] = useState(0)
  const viewport = useRef<HTMLDivElement>(null)
  const deferred = useDeferredValue(query)

  const families = useMemo(
    () => [...new Set(items.map(i => i.family))].sort(),
    [items],
  )

  const rows = useMemo(() => {
    const needle = deferred.trim().toLowerCase()
    const filtered = items.filter(item => {
      if (family && item.family !== family) return false
      if (tier === 'untested' && item.latest) return false
      if (tier && tier !== 'untested') {
        if (!item.latest) return false
        if (tier === 'oos' && tierRank(item.latest.evidence_tier) < 2) return false
        if (tier === 'insample' && tierRank(item.latest.evidence_tier) >= 2) return false
      }
      if (!needle) return true
      return item.name.toLowerCase().includes(needle)
        || item.strategy_id.toLowerCase().includes(needle)
        || item.family.toLowerCase().includes(needle)
    })
    const sign = dir === 'asc' ? 1 : -1
    return filtered.sort((a, b) => {
      const left = value(a, sort)
      const right = value(b, sort)
      if (left === right) return a.name.localeCompare(b.name)
      return (left > right ? 1 : -1) * sign
    })
  }, [items, deferred, family, tier, sort, dir])

  const height = (viewport.current?.clientHeight ?? 600)
  const first = Math.max(0, Math.floor(scroll / ROW_H) - OVERSCAN)
  const last = Math.min(rows.length, Math.ceil((scroll + height) / ROW_H) + OVERSCAN)
  const visible = rows.slice(first, last)
  const selected = useMemo(
    () => compare.map(id => items.find(i => i.strategy_id === id)).filter(Boolean) as StrategyListItem[],
    [compare, items],
  )

  const toggleSort = (key: SortKey) => {
    if (key === sort) setDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSort(key); setDir(key === 'name' || key === 'family' ? 'asc' : 'desc') }
  }
  const toggleCompare = (id: string) => setCompare(current =>
    current.includes(id) ? current.filter(x => x !== id) : current.length >= 4 ? current : [...current, id])

  return (
    <section className="catalogue">
      <header className="catalogue-bar">
        <div className="field-search">
          <Search aria-hidden="true" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Filter by name, id or family"
            aria-label="Filter strategies"
          />
          {query && <button onClick={() => setQuery('')} aria-label="Clear filter"><X /></button>}
        </div>
        <label className="field-select">
          <span>Family</span>
          <select value={family} onChange={e => setFamily(e.target.value)}>
            <option value="">All</option>
            {families.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <label className="field-select">
          <span>Evidence</span>
          <select value={tier} onChange={e => setTier(e.target.value)}>
            <option value="">Any</option>
            <option value="oos">Out of sample or better</option>
            <option value="insample">In sample only</option>
            <option value="untested">Never run</option>
          </select>
        </label>
        <p className="catalogue-count">
          <b className="mono">{rows.length.toLocaleString()}</b> of {items.length.toLocaleString()}
          {compare.length > 0 && <> · <b className="mono">{compare.length}</b> to compare</>}
        </p>
      </header>

      {pending ? (
        <div className="state" role="status">Reading the strategy library…</div>
      ) : items.length === 0 ? (
        <div className="panel-body evidence-absent">
          <Search aria-hidden="true" />
          <div>
            <strong>No strategies yet</strong>
            <p>A strategy is written to disk as real Python before it can be measured. Create one from a template below, or start the engine to have it search for candidates on its own.</p>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <div className="panel-body evidence-absent">
          <Search aria-hidden="true" />
          <div>
            <strong>No strategy matches this filter</strong>
            <p>{items.length.toLocaleString()} strategies exist. Widen the family or evidence filter, or clear the search.</p>
          </div>
        </div>
      ) : (
        <div className="catalogue-viewport" ref={viewport} onScroll={e => setScroll(e.currentTarget.scrollTop)}>
          <table className="data-table catalogue-table">
            <thead>
              <tr>
                <th className="col-pick"><GitCompare aria-label="Compare" /></th>
                {COLUMNS.map(col => (
                  <th key={col.key} className={col.num ? 'num' : undefined} aria-sort={sort === col.key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <button onClick={() => toggleSort(col.key)}>
                      {col.label}
                      {sort === col.key && (dir === 'asc' ? <ArrowUp aria-hidden="true" /> : <ArrowDown aria-hidden="true" />)}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {first > 0 && <tr aria-hidden="true" style={{ height: first * ROW_H }}><td colSpan={COLUMNS.length + 1} /></tr>}
              {visible.map(item => (
                <tr
                  key={item.strategy_id}
                  className={compare.includes(item.strategy_id) ? 'is-picked' : undefined}
                  onClick={() => onOpen(item.strategy_id)}
                  style={{ height: ROW_H }}
                >
                  <td className="col-pick" onClick={e => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={compare.includes(item.strategy_id)}
                      onChange={() => toggleCompare(item.strategy_id)}
                      aria-label={`Compare ${item.name}`}
                    />
                  </td>
                  <td className="col-name"><span>{item.name}</span></td>
                  <td className="sub">{item.family}</td>
                  <td>{item.latest?.calculation_version === 'legacy-price-points'
                    ? <span className="warn">Old units</span>
                    : <TierPill tier={item.latest?.evidence_tier} />}</td>
                  <td className={`mono num ${item.latest ? (item.latest.net_pnl >= 0 ? 'good' : 'bad') : 'sub'}`}>
                    {item.latest ? signed(item.latest.net_pnl) : '—'}
                  </td>
                  <td className="mono num sub">{item.latest?.trade_count ?? '—'}</td>
                  <td className="mono num sub">{item.latest ? `${(item.latest.win_rate * 100).toFixed(1)}%` : '—'}</td>
                  <td className="mono num sub">{item.latest ? money(item.latest.max_drawdown) : '—'}</td>
                  <td className="mono num sub">{item.backtest_count}</td>
                  <td className="mono sub">{stamp(item.latest?.finished_at ?? '')}</td>
                </tr>
              ))}
              {last < rows.length && <tr aria-hidden="true" style={{ height: (rows.length - last) * ROW_H }}><td colSpan={COLUMNS.length + 1} /></tr>}
            </tbody>
          </table>
        </div>
      )}

      {selected.length >= 2 && (
        <div className="panel compare-panel">
          <header className="panel-head">
            <h2>Comparison</h2>
            <span className="panel-meta">{selected.length} strategies · like-for-like only where the evidence tier matches</span>
            <button className="text-action" onClick={() => setCompare([])}>Clear</button>
          </header>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Measure</th>{selected.map(s => <th key={s.strategy_id} className="num">{s.name}</th>)}</tr></thead>
              <tbody>
                <CompareRow label="Family" cells={selected.map(s => s.family)} />
                <CompareRow label="Evidence tier" cells={selected.map(s => s.latest?.evidence_tier ?? 'never run')} />
                <CompareRow label="Net" cells={selected.map(s => (s.latest ? signed(s.latest.net_pnl) : 'not measured'))} numeric />
                <CompareRow label="Trades" cells={selected.map(s => (s.latest ? String(s.latest.trade_count) : 'not measured'))} numeric />
                <CompareRow label="Win rate" cells={selected.map(s => (s.latest ? `${(s.latest.win_rate * 100).toFixed(1)}%` : 'not measured'))} numeric />
                <CompareRow label="Max drawdown" cells={selected.map(s => (s.latest ? money(s.latest.max_drawdown) : 'not measured'))} numeric />
                <CompareRow label="Runs recorded" cells={selected.map(s => String(s.backtest_count))} numeric />
              </tbody>
            </table>
          </div>
          <p className="warning">
            Rows compare what each strategy last produced. Where the evidence tiers differ the
            figures are not equivalent — an in-sample net is not a weaker version of an
            out-of-sample net, it is a different claim.
          </p>
        </div>
      )}
    </section>
  )
}

function CompareRow({ label, cells, numeric }: { label: string; cells: string[]; numeric?: boolean }) {
  return (
    <tr>
      <td className="sub">{label}</td>
      {cells.map((cell, i) => (
        <td key={i} className={numeric ? 'mono num' : undefined} data-absent={cell === 'not measured' ? 'yes' : undefined}>
          {cell}
        </td>
      ))}
    </tr>
  )
}
