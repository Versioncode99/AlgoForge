import { useQuery } from '@tanstack/react-query'
import {
  Activity, Archive, BookOpen, BrainCircuit, ChevronLeft, ChevronRight, Database,
  FileCheck2, FlaskConical, GitBranch, Layers3, LockKeyhole, Menu, MessageSquare,
  Network, PanelBottomOpen, PlaySquare, Radar, ScrollText, Search, Settings,
  ShieldCheck, TestTubes, Workflow,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { getJson } from './api'
import { CommandPalette, type PaletteRoute } from './components/CommandPalette'
import { Wordmark } from './components/Logo'
import { ViewErrorBoundary } from './components/ViewErrorBoundary'
import type { ActivityEvent, StrategyListItem, Summary } from './types'
import { OverviewView } from './views/Overview'

const PropFirmView = lazy(() => import('./views/PropFirm').then(m => ({ default: m.PropFirmView })))
const ResearchLabView = lazy(() => import('./views/ResearchLab').then(m => ({ default: m.ResearchLabView })))
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
const ResearchMemoryView = lazy(() => import('./views/ResearchMemory').then(m => ({ default: m.ResearchMemoryView })))
const DataWorkspaceView = lazy(() => import('./views/DataWorkspace').then(m => ({ default: m.DataWorkspaceView })))

type RouteId = 'overview' | 'missions' | 'experiments' | 'strategies' | 'runs' | 'validation' |
  'evidence' | 'memory' | 'lineage' | 'data' | 'research' | 'pipeline' | 'agents' |
  'prop' | 'console' | 'settings'
type NavItem = PaletteRoute & { id: RouteId; icon: typeof Activity }

const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Research', items: [
    { id: 'overview', label: 'Overview', group: 'Research', detail: 'Current state', icon: Radar },
    { id: 'missions', label: 'Missions', group: 'Research', detail: 'Objectives and declared steps', icon: PlaySquare },
    { id: 'experiments', label: 'Experiments', group: 'Research', detail: 'What was tried', icon: FlaskConical },
    { id: 'strategies', label: 'Strategies', group: 'Research', detail: 'Build, run and judge', icon: Layers3 },
    { id: 'runs', label: 'Runs', group: 'Research', detail: 'Immutable execution ledger', icon: Archive },
  ]},
  { group: 'Validation', items: [
    { id: 'validation', label: 'Validation Lab', group: 'Validation', detail: 'Walk-forward, CSCV and CPCV', icon: TestTubes },
    { id: 'evidence', label: 'Evidence', group: 'Validation', detail: 'Dossiers and decisions', icon: FileCheck2 },
  ]},
  { group: 'Research Memory', items: [
    { id: 'memory', label: 'Memory', group: 'Research Memory', detail: 'Classified failures', icon: BrainCircuit },
    { id: 'lineage', label: 'Lineage', group: 'Research Memory', detail: 'Experiment ancestry', icon: GitBranch },
  ]},
  { group: 'Data', items: [
    { id: 'data', label: 'Data Health', group: 'Data', detail: 'Coverage and provenance', icon: Database },
    { id: 'research', label: 'Research Library', group: 'Data', detail: 'Sources and replication gaps', icon: BookOpen },
  ]},
  { group: 'Autonomous', items: [
    { id: 'pipeline', label: 'Engine Pipeline', group: 'Autonomous', detail: 'Graph and live counts', icon: Workflow },
    { id: 'agents', label: 'Agent Command', group: 'Autonomous', detail: 'Specialist operations', icon: Network },
  ]},
  { group: 'System', items: [
    { id: 'prop', label: 'Prop Simulation', group: 'System', detail: 'Deployment risk model', icon: ShieldCheck },
    { id: 'console', label: 'Console', group: 'System', detail: 'Ask the ledger', icon: MessageSquare },
    { id: 'settings', label: 'Settings', group: 'System', detail: 'Providers, data and storage', icon: Settings },
  ]},
]
const ROUTES = NAV.flatMap(group => group.items)
const routeFromHash = (): RouteId => {
  const id = window.location.hash.slice(1) as RouteId
  return ROUTES.some(route => route.id === id) ? id : 'overview'
}

export function App() {
  const [route, setRoute] = useState<RouteId>(routeFromHash)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [eventsOpen, setEventsOpen] = useState(false)

  useEffect(() => {
    const sync = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', sync)
    if (!window.location.hash) window.history.replaceState(null, '', '#overview')
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(open => !open)
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [])
  const navigate = useCallback((id: string) => { window.location.hash = id }, [])

  const health = useQuery({ queryKey: ['health'], refetchInterval: 15_000, queryFn: () => getJson<{ status: string; data_gate: string; engine_running: boolean }>('/health') })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const events = useQuery({ queryKey: ['activity'], refetchInterval: 5_000, queryFn: () => getJson<ActivityEvent[]>('/activity?limit=80') })

  const activeRoute = ROUTES.find(item => item.id === route) ?? ROUTES[0]
  const list = strategies.data ?? []
  const tested = list.filter(item => item.latest).length
  const oos = list.filter(item => ['VALIDATION_OOS', 'HOLDOUT'].includes(item.latest?.evidence_tier ?? '')).length
  const online = health.isSuccess
  const latest = events.data?.[0]
  const paletteRoutes = useMemo(() => ROUTES.map(({ id, label, group, detail }) => ({ id, label, group, detail })), [])

  return <div className={`app-shell${railCollapsed ? ' is-rail-collapsed' : ''}`}>
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <aside className="workstation-rail">
      <div className="rail-brand"><Wordmark /><button aria-label={railCollapsed ? 'Expand navigation' : 'Collapse navigation'} onClick={() => setRailCollapsed(value => !value)}>{railCollapsed ? <ChevronRight /> : <ChevronLeft />}</button></div>
      <nav aria-label="AlgoForge workspaces">
        {NAV.map(section => <section key={section.group}><h2>{section.group}</h2>{section.items.map(item => { const Icon = item.icon; return <a key={item.id} href={`#${item.id}`} aria-current={route === item.id ? 'page' : undefined} title={railCollapsed ? item.label : undefined}><Icon aria-hidden="true" /><span>{item.label}</span></a> })}</section>)}
      </nav>
      <button className="rail-search" onClick={() => setPaletteOpen(true)}><Search aria-hidden="true" /><span>Search workspace</span><kbd>Ctrl K</kbd></button>
    </aside>

    <header className="context-bar">
      <button className="rail-mobile-toggle" aria-label="Toggle navigation" onClick={() => setRailCollapsed(value => !value)}><Menu /></button>
      <div className="context-title"><span>{activeRoute.group}</span><strong>{activeRoute.label}</strong></div>
      <div className="context-facts">
        <span><i className={online ? 'pulse' : 'pulse is-off'} />{online ? health.data.engine_running ? 'ENGINE RUNNING' : 'API CONNECTED' : 'API OFFLINE'}</span>
        <span>DATA <b className={health.data?.data_gate?.startsWith('REAL') ? 'good' : 'warn'}>{health.data?.data_gate ?? 'UNKNOWN'}</b></span>
        <span>STRATEGIES <b>{summary.data?.strategy_count ?? 0}</b></span>
        <span>TESTED <b>{tested}</b></span><span>OOS <b className={oos ? 'good' : ''}>{oos}</b></span>
        <span><LockKeyhole /> PAPER ONLY</span>
      </div>
      <button className="context-search" onClick={() => setPaletteOpen(true)}><Search /><span>Find</span><kbd>Ctrl K</kbd></button>
    </header>

    <main className="workstation-main" id="workspace">
      {!online && !health.isPending && <div className="state error" role="alert">API unavailable. Start the AlgoForge API on port 8765.</div>}
      <ViewErrorBoundary view={activeRoute.label} onOverview={() => navigate('overview')}>
        <Suspense fallback={<div className="state" role="status">Opening {activeRoute.label}…</div>}>
          <div className="view-content" key={route}>
            {online && route === 'overview' && <OverviewView onRoute={navigate} />}
            {online && route === 'missions' && <MissionsView />}
            {online && (route === 'experiments' || route === 'lineage') && <ExperimentsView mode={route === 'lineage' ? 'lineage' : 'experiments'} />}
            {online && route === 'strategies' && <StrategiesView />}
            {online && route === 'runs' && <RunsView />}
            {online && route === 'validation' && <ValidationLabView />}
            {online && route === 'evidence' && <EvidenceView />}
            {online && route === 'memory' && <ResearchMemoryView />}
            {online && route === 'data' && <DataWorkspaceView />}
            {online && route === 'research' && <ResearchLabView />}
            {online && route === 'pipeline' && <PipelineView />}
            {online && route === 'agents' && <AgentCommandView />}
            {online && route === 'prop' && <PropFirmView />}
            {online && route === 'console' && <ConsoleView />}
            {online && route === 'settings' && <SettingsView />}
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
  return <section className="event-drawer" data-open={open} aria-label="System event drawer" aria-hidden={!open}>
    <header><ScrollText /><strong>System events</strong><span>{events.length} records</span><button onClick={onClose}>Close</button></header>
    <div>{!events.length && <p>No events yet. Start a bounded mission or backtest to create a trail.</p>}{events.map((event, index) => <article key={`${event.ts}-${index}`} data-level={event.level}><time>{event.ts.slice(11, 19)}</time><strong>{event.stage}</strong><span>{event.message}</span>{event.ref && <code>{event.ref}</code>}</article>)}</div>
  </section>
}
