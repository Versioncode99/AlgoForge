import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { getJson } from '../api'
import type { DatasetInfo, ExperimentRecord, ResearchMemoryPayload, Run, StrategyListItem } from '../types'
import { playSound } from '../sound'

export type PaletteRoute = { id: string; label: string; group: string; detail: string }

type Row = { key: string; label: string; detail: string; route: string; group: string }

const GROUP_ORDER = ['Views', 'Strategies', 'Experiments', 'Runs', 'Constraints', 'Datasets']

/** One search field over every record the workstation holds.
 *
 * Grouped rather than ranked into a single list: "which datasets match NQ" and
 * "which strategies match NQ" are different questions, and a flat list makes
 * the reader re-sort the answer in their head. Record queries only run while
 * the palette is open, so the shortcut costs nothing when it is closed.
 */
export function CommandPalette({
  open, routes, strategies, onClose, onRoute,
}: {
  open: boolean
  routes: PaletteRoute[]
  strategies: StrategyListItem[]
  onClose: () => void
  onRoute: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  const datasets = useQuery({ queryKey: ['datasets'], enabled: open, queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  const runs = useQuery({ queryKey: ['runs'], enabled: open, queryFn: () => getJson<Run[]>('/runs') })
  const memory = useQuery({ queryKey: ['research-memory'], enabled: open, queryFn: () => getJson<ResearchMemoryPayload>('/memory?limit=250') })
  const experiments = useQuery({ queryKey: ['experiments'], enabled: open, queryFn: () => getJson<ExperimentRecord[]>('/experiments?limit=200') })

  useEffect(() => {
    if (!open) return
    setQuery('')
    setCursor(0)
    requestAnimationFrame(() => input.current?.focus())
    return undefined
  }, [open])

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const rows: Row[] = [
      ...routes.map(route => ({
        key: `route-${route.id}`, label: route.label,
        detail: `${route.group} · ${route.detail}`, route: route.id, group: 'Views',
      })),
      ...strategies.map(s => ({
        key: `strategy-${s.strategy_id}`, label: s.name,
        detail: `${s.family} · ${s.symbol} · ${s.latest ? s.latest.evidence_tier : 'never run'}`,
        route: 'strategies', group: 'Strategies',
      })),
      ...(experiments.data ?? []).map(e => ({
        key: `experiment-${e.id}`, label: e.id,
        detail: `${e.template} · ${e.status ?? 'reserved'}${e.failure_class ? ` · ${e.failure_class}` : ''}`,
        route: 'experiments', group: 'Experiments',
      })),
      ...(runs.data ?? []).map(r => ({
        key: `run-${r.run_id}`, label: r.run_id,
        detail: `${r.tier} · ${r.engine_version}`, route: 'runs', group: 'Runs',
      })),
      ...(memory.data?.constraints ?? []).map((c, i) => ({
        key: `constraint-${c.template}-${i}`, label: c.failure_class,
        detail: `${c.template} · ${c.reason}`, route: 'memory', group: 'Constraints',
      })),
      ...(datasets.data ?? []).map(d => ({
        key: `dataset-${d.key}`, label: d.label,
        detail: `${d.provider} · ${d.symbol} ${d.interval}`, route: 'data', group: 'Datasets',
      })),
    ]
    const matched = rows.filter(item => !needle || `${item.label} ${item.detail}`.toLowerCase().includes(needle))
    const grouped = GROUP_ORDER
      .map(group => ({ group, items: matched.filter(item => item.group === group).slice(0, 6) }))
      .filter(section => section.items.length > 0)
    return { grouped, flat: grouped.flatMap(section => section.items) }
  }, [query, routes, strategies, experiments.data, runs.data, memory.data, datasets.data])

  const flat = results.flat
  const active = flat[Math.min(cursor, flat.length - 1)]

  useEffect(() => {
    if (!open) return
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { onClose(); return }
      if (event.key === 'ArrowDown') { event.preventDefault(); setCursor(c => Math.min(c + 1, flat.length - 1)) }
      if (event.key === 'ArrowUp') { event.preventDefault(); setCursor(c => Math.max(c - 1, 0)) }
      if (event.key === 'Enter' && active) {
        event.preventDefault()
        // A command ran. Not a keystroke sound — this fires once, on the
        // Enter that actually executes something.
        playSound('command.success')
        onRoute(active.route)
        onClose()
      }
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [open, flat.length, active, onClose, onRoute])

  if (!open) return null
  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="command-palette af-glass" role="dialog" aria-modal="true" aria-label="Navigate AlgoForge" onMouseDown={event => event.stopPropagation()}>
        <label className="palette-search">
          <Search aria-hidden="true" />
          <span className="sr-only">Search views, strategies, experiments, runs, constraints and datasets</span>
          <input
            ref={input}
            value={query}
            onChange={event => { setQuery(event.target.value); setCursor(0) }}
            placeholder="Go to a view or find a record…"
          />
          <kbd>ESC</kbd>
        </label>
        <div className="palette-results">
          {results.grouped.map(section => (
            <div className="palette-group" key={section.group}>
              <h3>{section.group}</h3>
              {section.items.map(item => (
                <button
                  key={item.key}
                  data-active={item.key === active?.key ? 'yes' : undefined}
                  onMouseEnter={() => setCursor(flat.findIndex(row => row.key === item.key))}
                  onClick={() => { onRoute(item.route); onClose() }}
                >
                  <strong>{item.label}</strong><small>{item.detail}</small><span>↵</span>
                </button>
              ))}
            </div>
          ))}
          {!flat.length && <p>Nothing matches. Search covers views, strategies, experiments, runs, constraints and datasets.</p>}
        </div>
        <footer>
          <span><kbd>↑</kbd> <kbd>↓</kbd> move · <kbd>↵</kbd> open</span>
          <span>Search is local to loaded records</span>
        </footer>
      </section>
    </div>
  )
}
