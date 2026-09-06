import { useQuery } from '@tanstack/react-query'
import {
  ChartNoAxesCombined, Layers, LockKeyhole, MessageSquare, Microscope,
  ScrollText, ShieldCheck, SlidersHorizontal, TriangleAlert, Network,
} from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { getJson } from './api'
import { Wordmark } from './components/Logo'
import type { ActivityEvent, StrategyListItem, Summary } from './types'
import { OverviewView } from './views/Overview'
const PropFirmView = lazy(() => import('./views/PropFirm').then(m => ({ default: m.PropFirmView })))
const ResearchLabView = lazy(() => import('./views/ResearchLab').then(m => ({ default: m.ResearchLabView })))
const ConsoleView = lazy(() => import('./views/Settings').then(m => ({ default: m.ConsoleView })))
const SettingsView = lazy(() => import('./views/Settings').then(m => ({ default: m.SettingsView })))
const StrategiesView = lazy(() => import('./views/Strategies').then(m => ({ default: m.StrategiesView })))
const AgentCommandView = lazy(() => import('./views/AgentCommand').then(m => ({ default: m.AgentCommandView })))

/* Five working sections and a console.
 *
 * Verdict, Regimes, Risk & Monte Carlo, Agents and Evolution were removed. The
 * first three rendered one seeded fixture run, so they showed identical numbers
 * whatever was selected — nothing on them could be acted on. The judge's gates
 * now live inside Strategies, against the real strategy they judge. Evolution
 * moved into Settings, where an update check belongs. */
const tabs = ['Overview', 'Agent Command', 'Research Lab', 'Strategies', 'Prop Firm', 'Console', 'Settings'] as const
type Tab = (typeof tabs)[number]

const ICONS: Record<Tab, typeof Layers> = {
  Overview: ChartNoAxesCombined,
  'Agent Command': Network,
  'Research Lab': Microscope,
  Strategies: Layers,
  'Prop Firm': ShieldCheck,
  Console: MessageSquare,
  Settings: SlidersHorizontal,
}

const EYEBROW: Record<Tab, string> = {
  Overview: 'AUTONOMOUS ENGINE',
  'Agent Command': 'YOUR QUANT RESEARCH TEAM',
  'Research Lab': 'EVIDENCE INVENTORY',
  Strategies: 'BUILD · RUN · JUDGE',
  'Prop Firm': 'ACCOUNT SURVIVAL',
  Console: 'ASK THE LEDGER',
  Settings: 'PROVIDERS · DATA · UPDATES',
}

export function App() {
  const [tab, setTab] = useState<Tab>('Overview')

  const health = useQuery({
    queryKey: ['health'], refetchInterval: 15_000,
    queryFn: () => getJson<{ status: string; data_gate: string; engine_running: boolean }>('/health'),
  })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const events = useQuery({
    queryKey: ['activity'], refetchInterval: 5_000,
    queryFn: () => getJson<ActivityEvent[]>('/activity?limit=80'),
  })

  const online = health.isSuccess
  const engineRunning = !!health.data?.engine_running
  const list = strategies.data ?? []
  const tested = list.filter((s) => s.latest).length
  const realEvidence = list.filter(
    (s) => s.latest?.evidence_tier === 'VALIDATION_OOS' || s.latest?.evidence_tier === 'HOLDOUT',
  ).length

  return (
    <div className="app">
      <a className="skip-link" href="#workspace">Skip to workspace</a>
      <header className="topbar">
        <Wordmark />
        <nav className="tabs" aria-label="Sections">
          {tabs.map((item) => {
            const Icon = ICONS[item]
            return (
              <button
                key={item}
                aria-label={item}
                aria-current={tab === item ? 'page' : undefined}
                className={tab === item ? 'active af-press' : 'af-press'}
                onClick={() => setTab(item)}
              >
                <Icon />
                <span>{item}</span>
              </button>
            )
          })}
        </nav>
        <div className="topstats">
          <span className={online ? 'topstat is-live' : 'topstat'}>
            <i className={online ? 'pulse' : 'pulse is-off'} />
            {online ? (engineRunning ? 'ENGINE RUNNING' : 'API CONNECTED') : 'API OFFLINE'}
          </span>
          <span className="topstat">STRATEGIES <b>{summary.data?.strategy_count ?? 0}</b></span>
          <span className="topstat">TESTED <b>{tested}</b></span>
          <span className="topstat" title="Runs on a held-out slice, the only kind that counts">
            OOS <b className={realEvidence ? 'good' : ''}>{realEvidence}</b>
          </span>
          <span className="topstat">
            DATA <b className={health.data?.data_gate?.startsWith('REAL') ? 'good' : 'bad'}>
              {health.data?.data_gate ?? '—'}
            </b>
          </span>
          <span className="topstat"><LockKeyhole size={11} /> NO LIVE ORDERS</span>
        </div>
      </header>

      <div className="body is-full">

        <main className="main" id="workspace">
          <div className="view-head">
            <div>
              <p className="eyebrow">{EYEBROW[tab]}</p>
              <h1>{tab}</h1>
            </div>
          </div>

          <div className="notice">
            <TriangleAlert />
            <span>
              PAPER ONLY · FILLS ARE MODELLED — walk-forward, CSCV and CPCV gate every verdict,
              holdout is burn-once, and NinjaTrader calibration is still required.
            </span>
          </div>

          {!online && (
            <div className="state error">
              API unavailable. Start the AlgoForge API on port 8765.
            </div>
          )}

          <Suspense fallback={<div className="state" role="status">Opening workspace…</div>}>
          <div className="view-content" key={tab}>
          {online && tab === 'Overview' && <OverviewView onAgents={() => setTab('Agent Command')} />}
          {online && tab === 'Agent Command' && <AgentCommandView />}
          {online && tab === 'Research Lab' && <ResearchLabView />}
          {online && tab === 'Strategies' && <StrategiesView />}
          {online && tab === 'Prop Firm' && <PropFirmView />}
          {online && tab === 'Console' && <ConsoleView />}
          {online && tab === 'Settings' && <SettingsView />}
          </div>
          </Suspense>
        </main>
      </div>

      <OrchestratorLog events={events.data ?? []} />
    </div>
  )
}

function OrchestratorLog({ events }: { events: ActivityEvent[] }) {
  const [collapsed, setCollapsed] = useState(true)
  return (
    <section className="logbar" data-collapsed={collapsed} aria-label="Orchestrator log">
      <div className="logbar-head">
        <ScrollText size={11} />
        <span>Orchestrator log</span>
        <span className="spacer" />
        <span>{events.length} events</span>
        <button className="logbar-toggle af-press" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? 'Show' : 'Hide'}
        </button>
      </div>
      <div className="logbar-body">
        {events.length === 0 && (
          <div className="logline"><time>--:--:--</time><span className="stage">IDLE</span>
            <span className="msg">Nothing has run yet. Create a strategy and run a backtest.</span></div>
        )}
        {events.map((e, i) => (
          <div
            className="logline af-row-in"
            key={`${e.ts}-${i}`}
            data-level={e.level}
            style={{ animationDelay: `${Math.min(i, 12) * 18}ms` }}
          >
            <time>{e.ts.slice(11, 19)}</time>
            <span className="stage">{e.stage}</span>
            <span className="msg">{e.message}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
