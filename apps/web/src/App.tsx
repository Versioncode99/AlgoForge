import { useQuery } from '@tanstack/react-query'
import {
  Activity, Grid2x2, LockKeyhole, Menu, PanelBottomOpen, RotateCw, ScrollText, Search,
  ChevronLeft, ChevronRight,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { getJson } from './api'
import { CommandPalette } from './components/CommandPalette'
import { Wordmark } from './components/Logo'
import { ViewErrorBoundary } from './components/ViewErrorBoundary'
import { sectionIcon } from './components/icons'
import { STANCE_LABEL, useLeaveMode, useModeSession, type ModeKey, type Section } from './modes'
import { sound } from './sound'
import { loadAppearance } from './theme'
import type { ActivityEvent, StrategyListItem, Summary } from './types'
import { ModeSelect } from './views/ModeSelect'
import { OverviewView } from './views/Overview'

/* The shell, scoped to a mode.
 *
 * Two things changed when the four modes arrived and both are structural.
 *
 * The navigation is no longer a constant in this file. It comes from the mode
 * manifest the API serves, which is the same declaration `forge.modes` uses to
 * answer an agent asking what is available. One source, so the rail cannot
 * offer a destination the backend does not know about, and a section added
 * there appears here without an edit.
 *
 * And there is now a state before any of it: no mode chosen. That is a real
 * state, not a missing one — the product opens on the four choices, and
 * defaulting to one of them would mean nobody ever sees the others.
 */

const PropFirmView = lazy(() => import('./views/PropFirm').then(m => ({ default: m.PropFirmView })))
const ResearchLibraryView = lazy(() => import('./views/ResearchLab').then(m => ({ default: m.ResearchLabView })))
const ConsoleView = lazy(() => import('./views/Settings').then(m => ({ default: m.ConsoleView })))
const SettingsView = lazy(() => import('./views/Settings').then(m => ({ default: m.SettingsView })))
const StrategiesView = lazy(() => import('./views/Strategies').then(m => ({ default: m.StrategiesView })))
const AgentCommandView = lazy(() => import('./views/AgentCommand').then(m => ({ default: m.AgentCommandView })))
const PipelineView = lazy(() => import('./views/Pipeline').then(m => ({ default: m.PipelineView })))
const MissionsView = lazy(() => import('./views/Orchestrator').then(m => ({ default: m.OrchestratorView })))
const ValidationLabView = lazy(() => import('./views/ValidationLab').then(m => ({ default: m.ValidationLabView })))
const EvidenceView = lazy(() => import('./views/Evidence').then(m => ({ default: m.EvidenceView })))
const ExperimentsView = lazy(() => import('./views/Experiments').then(m => ({ default: m.ExperimentsView })))
const RunsView = lazy(() => import('./views/Runs').then(m => ({ default: m.RunsView })))
const DataWorkspaceView = lazy(() => import('./views/DataWorkspace').then(m => ({ default: m.DataWorkspaceView })))
const ChartsView = lazy(() => import('./views/Charts').then(m => ({ default: m.ChartsView })))
const StrategyChartView = lazy(() => import('./views/StrategyChartView').then(m => ({ default: m.StrategyChartView })))
const ResearchLabWorkbench = lazy(() => import('./views/ResearchLabView').then(m => ({ default: m.ResearchLabWorkbench })))
const ResearchMemoryView = lazy(() => import('./views/ResearchMemory').then(m => ({ default: m.ResearchMemoryView })))
const WorkspaceView = lazy(() => import('./views/Workspace').then(m => ({ default: m.WorkspaceView })))
const PropAccountView = lazy(() => import('./views/PropAccount').then(m => ({ default: m.PropAccountView })))
const ActionsView = lazy(() => import('./views/Actions').then(m => ({ default: m.ActionsView })))
const OperatingLogView = lazy(() => import('./views/OperatingLog').then(m => ({ default: m.OperatingLogView })))
const BookView = lazy(() => import('./views/Book').then(m => ({ default: m.BookView })))
const FundView = lazy(() => import('./views/Fund').then(m => ({ default: m.FundView })))

/* The id of the main landmark, and the fragment the skip link targets.
 *
 * It is exported so a test can assert it is not also a route id. It used to be
 * "workspace", which is both the landmark and a destination in the rail: the
 * skip link set the hash, the hash listener read it as a route, and the one
 * control built for keyboard and screen-reader users navigated them somewhere
 * else instead of moving focus to the content. The suffix makes a collision
 * awkward to reintroduce, and `test_skip_target_is_not_a_route` makes it
 * impossible to reintroduce quietly. */
export const MAIN_LANDMARK_ID = 'main-content'

/** Which component answers `(mode, route)`.
 *
 * Keyed by mode first so the same word can mean different things in different
 * environments without either one being renamed into something worse: "Risk" in
 * Prop Firm is an account's exposure against its contract, and in Hedge Fund it
 * is the book against the fund's limits. Falling through to the shared map is
 * what keeps Strategies, Validation and Evidence a single implementation in all
 * four.
 */
function viewFor(mode: ModeKey, route: string): React.ReactNode {
  const perMode: Record<string, React.ReactNode> = {
    'prop_firm/account': <PropAccountView section="account" />,
    'prop_firm/rules': <PropAccountView section="rules" />,
    'prop_firm/drawdown': <PropAccountView section="drawdown" />,
    'prop_firm/daily': <PropAccountView section="daily" />,
    'prop_firm/target': <PropAccountView section="target" />,
    'prop_firm/risk': <PropAccountView section="risk" />,
    'prop_firm/simulation': <PropFirmView />,
    'ai/assistant': <ConsoleView />,
    'ai/actions': <ActionsView />,
    'ai/activity': <OperatingLogView />,
    'hedge_fund/fund': <FundView section="fund" />,
    'hedge_fund/data': <DataWorkspaceView />,
    'hedge_fund/research': <ExperimentsView mode="experiments" />,
    'hedge_fund/alpha': <FundView section="alpha" />,
    'hedge_fund/portfolio': <FundView section="portfolio" />,
    'hedge_fund/risk': <FundView section="risk" />,
    'hedge_fund/gate': <FundView section="gate" />,
    'hedge_fund/execution': <FundView section="execution" />,
    'hedge_fund/operations': <FundView section="operations" />,
    'hedge_fund/performance': <FundView section="performance" />,
    'hedge_fund/orchestrator': <OperatingLogView />,
    'hedge_fund/approvals': <FundView section="approvals" />,
    'hedge_fund/audit': <FundView section="audit" />,
  }
  const shared: Record<string, React.ReactNode> = {
    overview: <OverviewView onRoute={(id) => { window.location.hash = id }} />,
    workspace: <WorkspaceView />,
    charts: <ChartsView />,
    trades: <StrategyChartView />,
    strategies: <StrategiesView />,
    runs: <RunsView />,
    validation: <ValidationLabView />,
    evidence: <EvidenceView />,
    positions: <BookView />,
    book: <BookView />,
    performance: <FundView section="performance" />,
    data: <DataWorkspaceView />,
    research: <ResearchLibraryView />,
    lab: <ResearchLabWorkbench />,
    experiments: <ExperimentsView mode="experiments" />,
    lineage: <ExperimentsView mode="lineage" />,
    memory: <ResearchMemoryView />,
    assistant: <ConsoleView />,
    agents: <AgentCommandView />,
    missions: <MissionsView />,
    pipeline: <PipelineView />,
    activity: <OperatingLogView />,
    settings: <SettingsView />,
  }
  return perMode[`${mode}/${route}`] ?? shared[route] ?? null
}

export function App() {
  const session = useModeSession()
  const mode = session.data?.session.mode ?? null

  /* The manifest decides which routes exist, so the hash cannot be validated
   * until it has loaded. Until then `route` is whatever the URL says and the
   * shell renders the loading state, rather than rewriting the hash to
   * something the mode may not have — which is how a deep link becomes an
   * overview every time the page is refreshed. */
  const [route, setRoute] = useState(() => window.location.hash.slice(1))
  const [railCollapsed, setRailCollapsed] = useState(false)
  // Separate from `railCollapsed`, because they are different controls for
  // different shapes: collapsed is the docked rail at icon width, open is the
  // overlay rail on a narrow viewport.
  const [railOpen, setRailOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [eventsOpen, setEventsOpen] = useState(false)

  const sections = useMemo<Section[]>(() => session.data?.descriptor.sections ?? [], [session.data])
  const groups = useMemo(() => {
    const ordered: { group: string; items: Section[] }[] = []
    for (const section of sections) {
      const existing = ordered.find((entry) => entry.group === section.group)
      if (existing) existing.items.push(section)
      else ordered.push({ group: section.group, items: [section] })
    }
    return ordered
  }, [sections])

  useEffect(() => {
    const sync = () => setRoute(window.location.hash.slice(1))
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  // Correcting the hash is a separate effect, and it only runs once the
  // manifest is known. A hash that is not a route in *this* mode is replaced
  // with the mode's first section rather than left pointing at a blank screen.
  useEffect(() => {
    if (!sections.length) return
    const known = sections.some((section) => section.route === route)
    if (!known) {
      const first = sections[0].route
      window.history.replaceState(null, '', `#${first}`)
      setRoute(first)
    }
  }, [sections, route])

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(open => !open)
      }
      if (event.key === 'Escape') setRailOpen(false)
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [])
  // An overlay rail that stays open over the screen it just navigated to is a
  // menu the reader has to dismiss before seeing what they asked for.
  useEffect(() => { setRailOpen(false) }, [route])
  const navigate = useCallback((id: string) => { window.location.hash = id }, [])

  /* The appearance is loaded here rather than in Settings.
   *
   * `main.tsx` applies a cached copy before the first paint so the theme does
   * not flash, but a cache is not the record. Loading it only when the Settings
   * screen happened to be open meant every other page ran on whatever the
   * browser last remembered — change the theme, navigate away, and the old one
   * came back. */
  const appearance = useQuery({ queryKey: ['appearance'], queryFn: loadAppearance })
  useEffect(() => {
    if (appearance.data) {
      sound.configure({
        enabled: appearance.data.sound_enabled,
        volume: appearance.data.sound_volume,
      })
    }
  }, [appearance.data])

  const health = useQuery({ queryKey: ['health'], refetchInterval: 15_000, queryFn: () => getJson<{ status: string; data_gate: string; engine_running: boolean }>('/health') })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const events = useQuery({ queryKey: ['activity'], refetchInterval: 5_000, queryFn: () => getJson<ActivityEvent[]>('/activity?limit=80') })
  const leave = useLeaveMode()

  const online = health.isSuccess
  const list = strategies.data ?? []
  const tested = list.filter(item => item.latest).length
  const oos = list.filter(item => ['VALIDATION_OOS', 'HOLDOUT'].includes(item.latest?.evidence_tier ?? '')).length
  /* A count of zero and a count that was never fetched are different facts, and
   * they used to render identically. `counted` is what separates them. */
  const counted = strategies.isSuccess
  const latest = events.data?.[0]

  if (session.isPending) {
    return <div className="state" role="status">Opening AlgoForge…</div>
  }
  if (!mode) {
    return <ModeSelect />
  }

  const descriptor = session.data!.descriptor
  const stance = session.data!.session.stance
  const active = sections.find((section) => section.route === route) ?? sections[0]
  const paletteRoutes = sections.map(({ route: id, label, group, detail }) => ({ id, label, group, detail }))

  return <div className={`app-shell${railCollapsed ? ' is-rail-collapsed' : ''}`} data-rail-open={railOpen} data-mode={mode}>
    <a className="skip-link" href={`#${MAIN_LANDMARK_ID}`}>Skip to workspace</a>
    <aside className="workstation-rail">
      <div className="rail-brand">
        <Wordmark />
        <button className="rail-collapse" aria-label={railCollapsed ? 'Expand navigation' : 'Collapse navigation'} onClick={() => setRailCollapsed(value => !value)}>{railCollapsed ? <ChevronRight /> : <ChevronLeft />}</button>
      </div>
      <nav aria-label={`${descriptor.name} workspace`}>
        {groups.map(section => <section key={section.group}><h2>{section.group}</h2>{section.items.map(item => { const Icon = sectionIcon(item.route); return <a key={item.route} href={`#${item.route}`} aria-current={route === item.route ? 'page' : undefined} data-label={item.label}><Icon aria-hidden="true" /><span>{item.label}</span></a> })}</section>)}
      </nav>
      <button className="rail-search" onClick={() => setPaletteOpen(true)}><Search aria-hidden="true" /><span>Search workspace</span><kbd>Ctrl K</kbd></button>
    </aside>
    {railOpen && <button className="rail-scrim" aria-label="Close navigation" onClick={() => setRailOpen(false)} />}

    <header className="context-bar">
      <button className="rail-mobile-toggle" aria-label="Toggle navigation" aria-expanded={railOpen} onClick={() => setRailOpen(value => !value)}><Menu /></button>
      <div className="mode-badge">
        <b>{descriptor.name}</b>
        {stance && <span className="mode-stance-tag" data-stance={stance}>{STANCE_LABEL[stance]}</span>}
        <button className="mode-switch" onClick={() => leave.mutate()} title="Return to the workspace chooser. Nothing is lost — each mode keeps its own layout.">
          <Grid2x2 aria-hidden="true" /><span>Switch</span>
        </button>
      </div>
      <div className="context-title"><span>{active?.group}</span><strong>{active?.label}</strong></div>
      <div className="context-facts">
        <span><i className={online ? 'pulse' : 'pulse is-off'} />{online ? health.data?.engine_running ? 'ENGINE RUNNING' : 'API CONNECTED' : health.isPending ? 'CONNECTING' : 'API OFFLINE'}</span>
        <span>DATA <b className={!online ? 'unknown' : health.data?.data_gate?.startsWith('REAL') ? 'good' : 'warn'}>{health.data?.data_gate ?? 'UNKNOWN'}</b></span>
        <span>STRATEGIES <b className={summary.isSuccess ? undefined : 'unknown'}>{summary.isSuccess ? summary.data.strategy_count : '—'}</b></span>
        <span>TESTED <b className={counted ? undefined : 'unknown'}>{counted ? tested : '—'}</b></span>
        <span>OOS <b className={!counted ? 'unknown' : oos ? 'good' : undefined}>{counted ? oos : '—'}</b></span>
        <span><LockKeyhole /> PAPER ONLY</span>
      </div>
      <button className="context-search" onClick={() => setPaletteOpen(true)}><Search /><span>Find</span><kbd>Ctrl K</kbd></button>
    </header>

    <main className="workstation-main" id={MAIN_LANDMARK_ID} tabIndex={-1}>
      {!online && !health.isPending && <div className="state error" role="alert">
        <span>The AlgoForge API on port 8765 is not answering. Nothing here is lost — the workspace reappears as soon as it does.</span>
        <button onClick={() => health.refetch()}><RotateCw aria-hidden="true" />Retry now</button>
      </div>}
      <ViewErrorBoundary view={active?.label ?? 'Workspace'} onOverview={() => navigate(sections[0]?.route ?? '')}>
        <Suspense fallback={<div className="state" role="status">Opening {active?.label}…</div>}>
          <div className="view-content" key={`${mode}/${route}`}>
            {online && viewFor(mode, route)}
          </div>
        </Suspense>
      </ViewErrorBoundary>
    </main>

    <footer className="global-status">
      <span className={latest?.level ? `is-${latest.level}` : ''}><Activity />{latest ? `${latest.stage}: ${latest.message}` : 'No activity recorded'}</span>
      <span>{events.data?.length ?? 0} recent events</span>
      <button aria-expanded={eventsOpen} onClick={() => setEventsOpen(value => !value)}><PanelBottomOpen />{eventsOpen ? 'Close event drawer' : 'Open event drawer'}</button>
    </footer>
    <EventDrawer events={events.data ?? []} open={eventsOpen} onClose={() => setEventsOpen(false)} />
    <CommandPalette open={paletteOpen} routes={paletteRoutes} strategies={list} onClose={() => setPaletteOpen(false)} onRoute={navigate} />
  </div>
}

function EventDrawer({ events, open, onClose }: { events: ActivityEvent[]; open: boolean; onClose: () => void }) {
  /* `inert` rather than `aria-hidden`. The drawer holds a close button and a
   * scrollable list, and `aria-hidden` on a subtree that still contains
   * focusable controls hides them from assistive technology while leaving them
   * in the tab order — the one combination the specification calls out. */
  return <section className="event-drawer" data-open={open} aria-label="System event drawer" inert={!open}>
    <header><ScrollText /><strong>System events</strong><span>{events.length} records</span><button onClick={onClose}>Close</button></header>
    <div>{!events.length && <p>No events yet. Start a bounded mission or backtest to create a trail.</p>}{events.map((event, index) => <article key={`${event.ts}-${index}`} data-level={event.level}><time>{event.ts.slice(11, 19)}</time><strong>{event.stage}</strong><span>{event.message}</span>{event.ref && <code>{event.ref}</code>}</article>)}</div>
  </section>
}
