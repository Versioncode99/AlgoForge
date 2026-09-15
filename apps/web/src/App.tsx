import { useQuery } from '@tanstack/react-query'
import {
  BadgeCheck, ChevronLeft, ChevronRight, Grid2x2, Inbox, LockKeyhole, Menu, RotateCw, Search,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { getJson } from './api'
import { CommandPalette } from './components/CommandPalette'
import { ContextBar } from './components/ContextBar'
import { InboxDrawer, useInbox } from './components/InboxDrawer'
import { Wordmark } from './components/Logo'
import { WorkspaceSidebar } from './components/Sidebar'
import { ViewErrorBoundary } from './components/ViewErrorBoundary'
import { WorkspaceSwitcher } from './components/WorkspaceSwitcher'
import { sectionIcon } from './components/icons'
import { format, locate, railGroups, useNavigation, type Destination } from './navigation'
import { chatOpening } from './opening'
import { sound } from './sound'
import { loadAppearance } from './theme'
import type { ActivityEvent, Summary } from './types'
import { useActiveWorkspace } from './workspaces'

/* The shell. One product, one navigation.
 *
 * Two things changed here and both are structural.
 *
 * **There is no mode any more, and so no state before the product.** The shell
 * used to open on a choice — Normal, Prop Firm or AI — and every one of them
 * drew its own twenty-odd-row rail from its own manifest. A person had to
 * decide what kind of user they were before they could see what the thing did,
 * and the same word meant different things depending on which one they picked.
 * Now the rail is nine rows from one manifest, and the first screen is the
 * product.
 *
 * **What the mode also decided has not gone anywhere.** It carried authority as
 * well as navigation, and those are now separate: `forge.product.authority` is
 * set explicitly in Settings, defaults to exactly the posture the product
 * already had, and is the only thing the permission evaluator reads.
 *
 * A destination owns its tabs. That is the whole of the simplification —
 * Validation and Evidence are still one click away, and no longer competing
 * with Campaigns for a row in the rail.
 */

const ChatView = lazy(() => import('./views/Chat').then(m => ({ default: m.ChatView })))
const PropFirmView = lazy(() => import('./views/PropFirm').then(m => ({ default: m.PropFirmView })))
const ResearchLibraryView = lazy(() => import('./views/ResearchLab').then(m => ({ default: m.ResearchLabView })))
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
const ResearchCampaignView = lazy(() => import('./views/ResearchCampaign').then(m => ({ default: m.ResearchCampaignView })))
const ResearchControlView = lazy(() => import('./views/ResearchControl').then(m => ({ default: m.ResearchControlView })))
const WorkspaceView = lazy(() => import('./views/Workspace').then(m => ({ default: m.WorkspaceView })))
const OverviewView = lazy(() => import('./views/Overview').then(m => ({ default: m.OverviewView })))
const PropAccountView = lazy(() => import('./views/PropAccount').then(m => ({ default: m.PropAccountView })))
const PropDeskView = lazy(() => import('./views/PropDesk').then(m => ({ default: m.PropDeskView })))
const ActionsView = lazy(() => import('./views/Actions').then(m => ({ default: m.ActionsView })))
const OperatingLogView = lazy(() => import('./views/OperatingLog').then(m => ({ default: m.OperatingLogView })))
const BookView = lazy(() => import('./views/Book').then(m => ({ default: m.BookView })))
const FundView = lazy(() => import('./views/Fund').then(m => ({ default: m.FundView })))

/* The id of the main landmark, and the fragment the skip link targets.
 *
 * Exported so a test can assert it is not also a route id. It used to be
 * "workspace", which is both the landmark and a destination: the skip link set
 * the hash, the hash listener read it as a route, and the one control built for
 * keyboard and screen-reader users navigated them somewhere else instead of
 * moving focus to the content. */
export const MAIN_LANDMARK_ID = 'main-content'

/** Which component answers `(destination, tab)`.
 *
 * Keyed by `route/tab` rather than by mode. The collisions the mode used to
 * resolve — "Risk" meaning the book's exposure in one environment and an
 * account's cushion in another — are resolved by the destination instead:
 * `trading/risk` and `propdesk/risk` are two different screens with two
 * different names, and a link to either says which it meant.
 */
function viewFor(route: string, tab: string, params: Record<string, string>): React.ReactNode {
  const key = tab ? `${route}/${tab}` : route
  const views: Record<string, React.ReactNode> = {
    // workspace
    'home/summary': <OverviewView onRoute={(id) => { window.location.hash = id }} />,
    'home/workspace': <WorkspaceView workspaceId={params.workspace ?? ''} />,
    chat: <ChatView opening={chatOpening(params)} />,

    // research
    'research/workbench': <ResearchLabWorkbench />,
    'research/findings': <ResearchLibraryView />,
    'research/experiments': <ExperimentsView mode="experiments" />,
    'research/validation': <ValidationLabView />,
    'research/evidence': <EvidenceView />,

    // campaigns
    'campaigns/all': <ResearchCampaignView />,
    'campaigns/control': <ResearchControlView />,
    'campaigns/lineage': <ExperimentsView mode="lineage" />,
    'campaigns/memory': <ResearchMemoryView />,
    'campaigns/pipeline': <PipelineView />,
    'campaigns/automation': <MissionsView />,

    // strategies
    'strategies/library': <StrategiesView open={params.strategy ?? ''} pane={params.pane ?? ''} />,
    'strategies/runs': <RunsView />,
    'strategies/trades': <StrategyChartView />,

    // markets
    'markets/charts': <ChartsView />,
    'markets/data': <DataWorkspaceView />,

    // trading
    'trading/overview': <FundView section="fund" />,
    'trading/book': <BookView />,
    'trading/portfolio': <FundView section="portfolio" />,
    'trading/risk': <FundView section="risk" />,
    'trading/gate': <FundView section="gate" />,
    'trading/execution': <FundView section="execution" />,
    'trading/performance': <FundView section="performance" />,
    'trading/operations': <FundView section="operations" />,

    // prop desk
    'propdesk/accounts': <PropDeskView section="desk" />,
    'propdesk/status': <PropAccountView section="account" />,
    'propdesk/rules': <PropAccountView section="rules" />,
    'propdesk/drawdown': <PropAccountView section="drawdown" />,
    'propdesk/daily': <PropAccountView section="daily" />,
    'propdesk/target': <PropAccountView section="target" />,
    'propdesk/allocation': <PropDeskView section="allocation" />,
    'propdesk/copy': <PropDeskView section="copy" />,
    'propdesk/risk': <PropDeskView section="risk_management" />,
    'propdesk/limits': <PropDeskView section="limits" />,
    'propdesk/news': <PropDeskView section="news" />,
    'propdesk/simulation': <PropFirmView />,
    'propdesk/activity': <PropDeskView section="desk_activity" />,
    'propdesk/ai': <PropDeskView section="ai_management" />,

    // settings, and the machinery under it
    'settings/general': <SettingsView section="general" />,
    'settings/models': <SettingsView section="models" />,
    'settings/data': <SettingsView section="data" />,
    'settings/connections': <SettingsView section="connections" />,
    'settings/permissions': <SettingsView section="permissions" />,
    'settings/approvals': <FundView section="approvals" />,
    'settings/audit': <FundView section="audit" />,
    'settings/diagnostics': <DiagnosticsView />,
  }
  return views[key] ?? null
}

/** The machinery, in one place, behind a disclosure.
 *
 * Orchestrator, Actions and Activity were three rail rows; two of them rendered
 * the same component. None of them is a destination — they are how somebody
 * inspects a run that has already gone wrong — so they are tabs inside
 * Diagnostics rather than competing with Strategies for attention. Nothing
 * underneath them changed: the event ledger is the same ledger, the action
 * registry is the same registry, and the permission policy is the same policy.
 */
function DiagnosticsView() {
  const [pane, setPane] = useState<'events' | 'operating' | 'actions' | 'agents'>('events')
  const panes = [
    ['events', 'Event stream'],
    ['operating', 'Operating log'],
    ['actions', 'Actions'],
    ['agents', 'Agents'],
  ] as const
  return (
    <section className="stack">
      <div className="section-title">
        <p>Diagnostics</p>
        <h2>What the machine did, and what it was refused</h2>
      </div>
      <div className="subtabs" role="tablist" aria-label="Diagnostics">
        {panes.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={pane === id} onClick={() => setPane(id)}>
            {label}
          </button>
        ))}
      </div>
      {pane === 'events' && <EventStream />}
      {pane === 'operating' && <OperatingLogView />}
      {pane === 'actions' && <ActionsView />}
      {pane === 'agents' && <AgentCommandView />}
    </section>
  )
}

/** The durable event ledger, in full.
 *
 * It used to be a drawer behind a footer that sat on every screen in the
 * product carrying the latest line and a count of recent ones. That is a
 * permanent strip of information nobody was looking for, and it competed with
 * the work for attention. The ledger did not move an inch — this renders the
 * same `/activity` records the footer read, in the one place somebody goes when
 * they want them. Failures and required actions reach the reader through the
 * inbox, which is where a thing that needs them is supposed to arrive.
 */
function EventStream() {
  const events = useQuery({
    queryKey: ['activity', 'diagnostics'],
    refetchInterval: 10_000,
    queryFn: () => getJson<ActivityEvent[]>('/activity?limit=200'),
  })
  const rows = events.data ?? []
  if (events.isPending) return <p className="state" role="status">Reading the ledger…</p>
  if (!rows.length) {
    return <p className="state">Nothing recorded yet. Run a backtest or start a campaign.</p>
  }
  return (
    <div className="event-stream">
      {rows.map((event, index) => (
        <article key={`${event.ts}-${index}`} data-level={event.level}>
          <time dateTime={event.ts}>{event.ts.slice(11, 19)}</time>
          <strong>{event.stage}</strong>
          <span>{event.message}</span>
          {event.ref && <code>{event.ref}</code>}
        </article>
      ))}
    </div>
  )
}

export function App() {
  const nav = useNavigation()
  const [hash, setHash] = useState(() => window.location.hash)
  const [railCollapsed, setRailCollapsed] = useState(false)
  // Separate from `railCollapsed`: they are different controls for different
  // shapes. Collapsed is the docked rail at icon width; open is the overlay
  // rail on a narrow viewport.
  const [railOpen, setRailOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [switcherOpen, setSwitcherOpen] = useState(false)

  const activeWorkspace = useActiveWorkspace()
  const railWorkspace = activeWorkspace.data ?? null
  const unread = useInbox().data?.unread ?? 0

  const here = useMemo(() => locate(hash, nav.data), [hash, nav.data])
  const groups = useMemo(() => railGroups(nav.data), [nav.data])
  const byRoute = useMemo(
    () => new Map((nav.data?.destinations ?? []).map((d) => [d.route, d])),
    [nav.data],
  )

  useEffect(() => {
    const sync = () => setHash(window.location.hash)
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  /* Rewriting the hash is its own effect, and it only runs once the manifest
   * has arrived. A link this product no longer has is *translated* rather than
   * dropped — `#evidence` still opens Evidence, at `#research?tab=evidence` —
   * and the rewrite is what lets the reader see where they landed. Doing it
   * before the manifest loaded is how a deep link became an overview on every
   * refresh, which looked deliberate and explained nothing. */
  useEffect(() => {
    if (!here) return
    const target = format(here.route, here.tab, here.params)
    if (here.redirected || (hash && hash !== target) || !hash) {
      if (window.location.hash !== target) {
        window.history.replaceState(null, '', target)
        setHash(target)
      }
    }
  }, [here, hash])

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
  useEffect(() => { setRailOpen(false) }, [hash])

  const navigate = useCallback((link: string) => {
    window.location.hash = link.startsWith('#') ? link.slice(1) : link
    setSwitcherOpen(false)
  }, [])

  /* Appearance is loaded here rather than in Settings. `main.tsx` applies a
   * cached copy before the first paint so the theme does not flash, but a cache
   * is not the record: loading it only when Settings happened to be open meant
   * every other page ran on whatever the browser last remembered. */
  const appearance = useQuery({ queryKey: ['appearance'], queryFn: loadAppearance })
  useEffect(() => {
    if (appearance.data) {
      sound.configure({
        enabled: appearance.data.sound_enabled,
        volume: appearance.data.sound_volume,
      })
    }
  }, [appearance.data])

  /* Two shell-wide queries, and each one earns it.
   *
   * `health` decides whether anything renders at all. `summary` is the only
   * number in the header. What used to be here as well — every strategy, the
   * whole activity feed, the inbox — is fetched by the screens that show it.
   * Opening Chat used to cost the entire strategy library first; see
   * `docs/HORIZON_PERFORMANCE.md`. */
  const health = useQuery({
    queryKey: ['health'],
    refetchInterval: 15_000,
    queryFn: () => getJson<{ status: string; data_gate: string; engine_running: boolean }>('/health'),
  })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })

  const online = health.isSuccess
  const degraded = online && !health.data?.data_gate?.startsWith('REAL')

  if (nav.isPending) {
    return <div className="state" role="status">Opening AlgoForge…</div>
  }
  if (nav.isError || !here) {
    return <div className="state error" role="alert">
      <span>AlgoForge could not read its own navigation. The API on port 8765 may not be running.</span>
      <button onClick={() => nav.refetch()}><RotateCw aria-hidden="true" />Retry</button>
    </div>
  }

  const destination = byRoute.get(here.route)
  const activeTab = destination?.tabs.find((t) => t.tab === here.tab)
  const paletteRoutes = (nav.data?.destinations ?? []).flatMap((d) =>
    d.tabs.length
      ? d.tabs.map((t) => ({
          id: format(d.route, t.tab).slice(1),
          label: d.tabs.length > 1 ? `${d.label} · ${t.label}` : d.label,
          group: d.group,
          detail: t.detail,
        }))
      : [{ id: d.route, label: d.label, group: d.group, detail: d.detail }],
  )

  return <div className={`app-shell${railCollapsed ? ' is-rail-collapsed' : ''}`} data-rail-open={railOpen} data-route={here.route}>
    <a className="skip-link" href={`#${MAIN_LANDMARK_ID}`}>Skip to content</a>
    <aside className="workstation-rail">
      <div className="rail-brand">
        <Wordmark />
        <button className="rail-collapse" aria-label={railCollapsed ? 'Expand navigation' : 'Collapse navigation'} onClick={() => setRailCollapsed(value => !value)}>{railCollapsed ? <ChevronRight /> : <ChevronLeft />}</button>
      </div>
      {/* The rail comes from the *workspace* when it has one of its own, and
        * from the product manifest otherwise. That fallback is what keeps every
        * existing arrangement working: a workspace saved before sidebars were
        * ownable still opens with the navigation it always had. */}
      {railWorkspace?.sidebar_is_custom ? (
        <WorkspaceSidebar
          workspace={railWorkspace}
          route={here.route}
          collapsed={railCollapsed}
          onRoute={navigate}
        />
      ) : (
        <nav aria-label="Sections">
          {groups.map(entry => <section key={entry.group}><h2>{entry.group}</h2>{entry.items.map(item => <RailRow key={item.route} item={item} active={here.route === item.route} />)}</section>)}
        </nav>
      )}
      <button
        className="rail-workspaces"
        aria-expanded={switcherOpen}
        onClick={() => setSwitcherOpen(open => !open)}
        title="Switch workspace, or build one. A workspace is an arrangement of panels, not a separate product."
      >
        <Grid2x2 aria-hidden="true" /><span>Workspaces</span>
      </button>
      <button className="rail-search" onClick={() => setPaletteOpen(true)}><Search aria-hidden="true" /><span>Search</span><kbd>Ctrl K</kbd></button>
    </aside>
    {railOpen && <button className="rail-scrim" aria-label="Close navigation" onClick={() => setRailOpen(false)} />}

    <header className="context-bar" aria-label="Workspace status">
      <button className="rail-mobile-toggle" aria-label="Toggle navigation" aria-expanded={railOpen} onClick={() => setRailOpen(value => !value)}><Menu /></button>
      <div className="context-title">
        <span>{destination?.group}</span>
        <strong>{destination?.label ?? 'AlgoForge'}</strong>
      </div>
      <ContextBar />
      {/* Two facts, not eleven. Paper-only is safety-critical and never moves;
        * the rest of what used to live here — strategy counts, tested counts,
        * OOS counts, the latest event — is on the screens that own it. The
        * connection reads only when it is *not* nominal, because "API
        * CONNECTED" in the header of a working application is a status light
        * that is on all the time and therefore says nothing. */}
      <div className="context-facts">
        {!online && <span className="is-bad" role="status">{health.isPending ? 'Connecting…' : 'API offline'}</span>}
        {degraded && <span className="is-warn" role="status">Data {health.data?.data_gate}</span>}
        <span className="is-paper"><LockKeyhole aria-hidden="true" /> Paper only</span>
      </div>
      <ApprovalsBadge onOpen={() => navigate(format('settings', 'approvals'))} />
      <button
        className="context-inbox"
        aria-expanded={inboxOpen}
        aria-label={unread ? `Finished work, ${unread} unread` : 'Finished work'}
        onClick={() => setInboxOpen(value => !value)}
      >
        <Inbox aria-hidden="true" />
        <span>Finished</span>
        {unread > 0 && <b className="context-inbox-count">{unread > 99 ? '99+' : unread}</b>}
      </button>
      <button className="context-search" onClick={() => setPaletteOpen(true)}><Search /><span>Find</span><kbd>Ctrl K</kbd></button>
    </header>

    {destination && destination.tabs.length > 1 && (
      <nav className="destination-tabs" aria-label={`${destination.label} views`}>
        {destination.tabs.map((tab) => (
          <a
            key={tab.tab}
            href={format(destination.route, tab.tab)}
            /* `true`, not `page`: the rail link for this destination is already
               `aria-current="page"`, and two elements both claiming to be the
               current *page* is one claim too many. The tab is the current view
               within the page the rail already named. */
            aria-current={here.tab === tab.tab ? 'true' : undefined}
            data-advanced={tab.advanced || undefined}
            title={tab.detail}
          >{tab.label}</a>
        ))}
      </nav>
    )}

    {/* `tabIndex={0}`, not `-1`. It is the skip link's focus target, which `-1`
        would satisfy — but it is also the page's scroll container, and a view
        whose content happens to hold no focusable element (a report, a table of
        readings) would then be scrollable by mouse and unreachable by keyboard.
        The cost is one tab stop, immediately after the skip link that aims at
        it, which is where the keyboard was headed anyway. */}
    <main className="workstation-main" id={MAIN_LANDMARK_ID} tabIndex={0}>
      {!online && !health.isPending && <div className="state error" role="alert">
        <span>The AlgoForge API on port 8765 is not answering. Nothing here is lost — the workspace reappears as soon as it does.</span>
        <button onClick={() => health.refetch()}><RotateCw aria-hidden="true" />Retry now</button>
      </div>}
      <ViewErrorBoundary view={activeTab?.label ?? destination?.label ?? 'AlgoForge'} onOverview={() => navigate('home')}>
        <Suspense fallback={<div className="state" role="status">Opening {activeTab?.label ?? destination?.label}…</div>}>
          <div className="view-content" key={`${here.route}/${here.tab}`}>
            {online && viewFor(here.route, here.tab, here.params)}
          </div>
        </Suspense>
      </ViewErrorBoundary>
    </main>

    {switcherOpen && (
      <div className="ws-switcher-layer" role="dialog" aria-label="Workspaces">
        <button className="ws-switcher-scrim" aria-label="Close workspaces" onClick={() => setSwitcherOpen(false)} />
        <WorkspaceSwitcher onOpened={() => setSwitcherOpen(false)} />
      </div>
    )}
    <InboxDrawer open={inboxOpen} onClose={() => setInboxOpen(false)} />
    <CommandPalette open={paletteOpen} routes={paletteRoutes} onClose={() => setPaletteOpen(false)} onRoute={navigate} />
  </div>
}

function RailRow({ item, active }: { item: Destination; active: boolean }) {
  const Icon = sectionIcon(item.route)
  return (
    <a
      href={format(item.route)}
      aria-current={active ? 'page' : undefined}
      data-label={item.label}
      title={item.detail}
    >
      <Icon aria-hidden="true" /><span>{item.label}</span>
    </a>
  )
}

/** Approvals, where they belong: absent until there is a queue.
 *
 * A permanent "Approvals (0)" row was one of the three rail entries that told
 * the reader about the machine rather than about their work. A consequential
 * action waiting for a person is not routine, so it appears exactly when it is
 * true and is unmissable when it is.
 */
function ApprovalsBadge({ onOpen }: { onOpen: () => void }) {
  const pending = useQuery({
    queryKey: ['approvals', 'pending'],
    refetchInterval: 20_000,
    queryFn: () => getJson<{ pending: { id: string }[] }>('/approvals?state=pending'),
    retry: false,
  })
  const count = pending.data?.pending?.length ?? 0
  if (!count) return null
  return (
    <button className="context-approvals" onClick={onOpen}>
      <BadgeCheck aria-hidden="true" />
      <span>{count === 1 ? '1 approval' : `${count} approvals`}</span>
    </button>
  )
}
