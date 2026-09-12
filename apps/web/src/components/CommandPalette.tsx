import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getJson } from '../api'
import type { DatasetInfo, ExperimentRecord, ResearchMemoryPayload, Run, StrategyListItem } from '../types'
import { playSound } from '../sound'
import { runAction, useCampaigns, useInstruments } from '../workstation'

export type PaletteRoute = { id: string; label: string; group: string; detail: string }

/** What pressing Enter on a row does.
 *
 * A row either goes somewhere or runs something. Both are represented, because
 * the palette that only navigated was half a palette: an operator who can find
 * a campaign in two keystrokes and then has to mouse to a button to start it
 * has not been given a command palette, they have been given a search box.
 */
type Row = {
  key: string
  label: string
  detail: string
  group: string
  /** The hash to navigate to, when that is what Enter means. */
  route?: string
  /** The registered verb to call, when Enter runs something. */
  action?: { name: string; args: Record<string, unknown> }
  /** What the row does, shown at the right edge. */
  verb: string
}

const GROUP_ORDER = [
  'Commands',
  'Instruments',
  'Views',
  'Campaigns',
  'Strategies',
  'Experiments',
  'Runs',
  'Constraints',
  'Datasets',
]

/** Campaign lifecycle offered by status. A stopped campaign has no "pause". */
function campaignCommands(campaign: { campaign_id: string; name: string; status: string }): Row[] {
  const id = campaign.campaign_id
  const base = { group: 'Commands', detail: `${campaign.name} · ${campaign.status}` }
  if (campaign.status === 'running') {
    return [
      { ...base, key: `cmd-pause-${id}`, label: `Pause campaign · ${campaign.name}`, action: { name: 'pause_campaign', args: { campaign_id: id } }, verb: 'pause' },
      { ...base, key: `cmd-stop-${id}`, label: `Stop campaign · ${campaign.name}`, action: { name: 'stop_campaign', args: { campaign_id: id } }, verb: 'stop' },
    ]
  }
  if (campaign.status === 'paused') {
    return [{ ...base, key: `cmd-resume-${id}`, label: `Resume campaign · ${campaign.name}`, action: { name: 'resume_campaign', args: { campaign_id: id } }, verb: 'resume' }]
  }
  if (campaign.status === 'archived') return []
  return [{ ...base, key: `cmd-start-${id}`, label: `Start campaign · ${campaign.name}`, action: { name: 'start_campaign', args: { campaign_id: id } }, verb: 'start' }]
}

/** One search field over every record the workstation holds, and every verb it can run.
 *
 * Grouped rather than ranked into a single list: "which datasets match NQ" and
 * "which strategies match NQ" are different questions, and a flat list makes
 * the reader re-sort the answer in their head. Record queries only run while
 * the palette is open, so the shortcut costs nothing when it is closed.
 *
 * Commands are executed through the action registry — the same surface the
 * assistant calls, evaluated against the same mode permissions. Only verbs the
 * registry will actually run without a separate confirmation are offered here:
 * a palette entry for something that would always refuse is a control that
 * does not work, drawn as one that does.
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
  const [running, setRunning] = useState('')
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const datasets = useQuery({ queryKey: ['datasets'], enabled: open, queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  const runs = useQuery({ queryKey: ['runs'], enabled: open, queryFn: () => getJson<Run[]>('/runs') })
  const memory = useQuery({ queryKey: ['research-memory'], enabled: open, queryFn: () => getJson<ResearchMemoryPayload>('/memory?limit=250') })
  const experiments = useQuery({ queryKey: ['experiments'], enabled: open, queryFn: () => getJson<ExperimentRecord[]>('/experiments?limit=200') })
  const instruments = useInstruments(open)
  const campaigns = useCampaigns(open)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setCursor(0)
    setOutcome(null)
    requestAnimationFrame(() => input.current?.focus())
    return undefined
  }, [open])

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const rows: Row[] = [
      ...(campaigns.data ?? []).flatMap(campaignCommands),
      /* Selecting an instrument establishes context rather than navigating.
       * The specification tag travels with it because an unverified multiplier
       * is the number a position would be sized from, and a picker that does
       * not say so is where a sizing error enters. */
      ...(instruments.data ?? []).map(item => ({
        key: `instrument-${item.root}`,
        label: item.root,
        detail: `${item.description} · ${item.exchange}${item.specification === 'unverified' ? ' · spec unverified' : ''}`,
        group: 'Instruments',
        action: { name: 'set_context', args: { instrument: item.root } },
        verb: 'set context',
      })),
      ...routes.map(route => ({
        key: `route-${route.id}`, label: route.label,
        detail: `${route.group} · ${route.detail}`, route: route.id, group: 'Views', verb: 'open',
      })),
      ...(campaigns.data ?? []).map(campaign => ({
        key: `campaign-${campaign.campaign_id}`, label: campaign.name,
        detail: `${campaign.status} · priority ${campaign.priority}`,
        route: 'campaigns', group: 'Campaigns', verb: 'open',
      })),
      ...strategies.map(s => ({
        key: `strategy-${s.strategy_id}`, label: s.name,
        detail: `${s.family} · ${s.symbol} · ${s.latest ? s.latest.evidence_tier : 'never run'}`,
        route: 'strategies', group: 'Strategies', verb: 'open',
      })),
      ...(experiments.data ?? []).map(e => ({
        key: `experiment-${e.id}`, label: e.id,
        detail: `${e.template} · ${e.status ?? 'reserved'}${e.failure_class ? ` · ${e.failure_class}` : ''}`,
        route: 'experiments', group: 'Experiments', verb: 'open',
      })),
      ...(runs.data ?? []).map(r => ({
        key: `run-${r.run_id}`, label: r.run_id,
        detail: `${r.tier} · ${r.engine_version}`, route: 'runs', group: 'Runs', verb: 'open',
      })),
      ...(memory.data?.constraints ?? []).map((c, i) => ({
        key: `constraint-${c.template}-${i}`, label: c.failure_class,
        detail: `${c.template} · ${c.reason}`, route: 'memory', group: 'Constraints', verb: 'open',
      })),
      ...(datasets.data ?? []).map(d => ({
        key: `dataset-${d.key}`, label: d.label,
        detail: `${d.provider} · ${d.symbol} ${d.interval}`, route: 'data', group: 'Datasets', verb: 'open',
      })),
    ]
    const matched = rows.filter(item => !needle || `${item.label} ${item.detail}`.toLowerCase().includes(needle))
    /* An exact label match sorts first within its group.
     *
     * Found in visual QA: typing "NQ" listed MNQ above NQ, because a plain
     * substring filter has no opinion and MNQ happens to come first in the
     * catalogue. Somebody typing NQ means NQ, and the row they meant was the
     * second one — which on a keyboard-first surface is a wrong instrument one
     * Enter away. */
    const rank = (item: Row) => {
      const label = item.label.toLowerCase()
      if (label === needle) return 0
      if (label.startsWith(needle)) return 1
      return 2
    }
    const grouped = GROUP_ORDER
      .map(group => ({
        group,
        items: matched
          .filter(item => item.group === group)
          .sort((a, b) => rank(a) - rank(b))
          .slice(0, 6),
      }))
      .filter(section => section.items.length > 0)
    /* Groups are ordered by their best match, not by a fixed list.
     *
     * Found in QA on the running application: with Commands pinned first,
     * typing "NQ" put "Start campaign · NQ Momentum" at the top, because the
     * campaign's name contains NQ. The reader meant the instrument, and Enter
     * would have started a research campaign instead. A fixed order is right
     * only while nothing is typed; once there is a query, the best match wins
     * and GROUP_ORDER is the tie-break. */
    if (needle) {
      grouped.sort((a, b) => {
        const best = rank(a.items[0]) - rank(b.items[0])
        return best !== 0 ? best : GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group)
      })
    }
    return { grouped, flat: grouped.flatMap(section => section.items) }
  }, [query, routes, strategies, experiments.data, runs.data, memory.data, datasets.data, instruments.data, campaigns.data])

  const flat = results.flat
  const active = flat[Math.min(cursor, flat.length - 1)]

  const choose = useCallback(async (row: Row) => {
    if (row.route) {
      playSound('command.success')
      onRoute(row.route)
      onClose()
      return
    }
    if (!row.action || running) return
    setRunning(row.key)
    setOutcome(null)
    try {
      await runAction(row.action.name, row.action.args)
      playSound('command.success')
      /* Whatever the verb touched is now stale everywhere. Invalidating
       * broadly rather than naming each key: the palette can run any of
       * several verbs and does not know what each one moved, and a stale
       * campaign row that still says "running" after it was stopped is worse
       * than a refetch. */
      await queryClient.invalidateQueries()
      setOutcome({ ok: true, text: `${row.label} — done.` })
      onClose()
    } catch (error) {
      // Shown verbatim. The registry's refusals name what was wrong and what
      // would fix it, and summarising them here would throw that away.
      setOutcome({ ok: false, text: error instanceof Error ? error.message : String(error) })
    } finally {
      setRunning('')
    }
  }, [onClose, onRoute, queryClient, running])

  /* Keep the keyboard cursor on screen.
   *
   * Arrowing moves a highlight, not focus, so the browser does nothing to
   * follow it. Six rows per group across nine groups is fifty-four results in a
   * 420px list: pressing Down past the tenth moved the selection somewhere the
   * reader could not see, and Enter then acted on a row that had never been on
   * screen. `nearest` so a cursor already visible does not scroll. */
  const list = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open || !active) return
    const row = list.current?.querySelector(`[data-key="${CSS.escape(active.key)}"]`)
    // jsdom has no layout, so it does not implement `scrollIntoView`. Keeping
    // the cursor visible is a nicety; throwing out of an effect and taking the
    // palette down with it is not.
    if (row instanceof HTMLElement && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'nearest' })
    }
  }, [open, active])

  useEffect(() => {
    if (!open) return
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { onClose(); return }
      if (event.key === 'ArrowDown') { event.preventDefault(); setCursor(c => Math.min(c + 1, flat.length - 1)) }
      if (event.key === 'ArrowUp') { event.preventDefault(); setCursor(c => Math.max(c - 1, 0)) }
      if (event.key === 'Enter' && active) {
        event.preventDefault()
        void choose(active)
      }
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [open, flat.length, active, onClose, choose])

  if (!open) return null
  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="command-palette af-glass" role="dialog" aria-modal="true" aria-label="Search and run commands" onMouseDown={event => event.stopPropagation()}>
        <label className="palette-search">
          <Search aria-hidden="true" />
          <span className="sr-only">Search commands, instruments, views, campaigns, strategies, experiments, runs, constraints and datasets</span>
          <input
            ref={input}
            value={query}
            onChange={event => { setQuery(event.target.value); setCursor(0) }}
            placeholder="Run a command, open an instrument, or find a record…"
          />
          <kbd>ESC</kbd>
        </label>
        {outcome && (
          <p className={`palette-outcome${outcome.ok ? '' : ' is-error'}`} role={outcome.ok ? 'status' : 'alert'}>
            {outcome.text}
          </p>
        )}
        <div className="palette-results" ref={list}>
          {results.grouped.map(section => (
            <div className="palette-group" key={section.group}>
              <h3>{section.group}</h3>
              {section.items.map(item => (
                <button
                  key={item.key}
                  data-key={item.key}
                  data-active={item.key === active?.key ? 'yes' : undefined}
                  disabled={Boolean(running) && running !== item.key}
                  onMouseEnter={() => setCursor(flat.findIndex(row => row.key === item.key))}
                  onClick={() => { void choose(item) }}
                >
                  <strong>{item.label}</strong><small>{item.detail}</small>
                  <span>{running === item.key ? '…' : item.verb}</span>
                </button>
              ))}
            </div>
          ))}
          {!flat.length && <p>Nothing matches. Search covers commands, instruments, views, campaigns, strategies, experiments, runs, constraints and datasets.</p>}
        </div>
        <footer>
          <span><kbd>↑</kbd> <kbd>↓</kbd> move · <kbd>↵</kbd> run</span>
          <span>Commands go through the action registry</span>
        </footer>
      </section>
    </div>
  )
}
