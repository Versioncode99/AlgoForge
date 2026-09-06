import { useQuery } from '@tanstack/react-query'
import {
  ChartNoAxesCombined, Layers, LockKeyhole, MessageSquare, Microscope,
  ScrollText, ShieldCheck, SlidersHorizontal, TriangleAlert,
} from 'lucide-react'
import { useState } from 'react'
import { getJson } from './api'
import { Wordmark } from './components/Logo'
import type { ActivityEvent, Rule, StrategyListItem, Summary } from './types'
import { OverviewView } from './views/Overview'
import { PropFirmView } from './views/PropFirm'
import { ResearchLabView } from './views/ResearchLab'
import { ConsoleView, SettingsView } from './views/Settings'
import { StrategiesView } from './views/Strategies'

/* Five working sections and a console.
 *
 * Verdict, Regimes, Risk & Monte Carlo, Agents and Evolution were removed. The
 * first three rendered one seeded fixture run, so they showed identical numbers
 * whatever was selected — nothing on them could be acted on. The judge's gates
 * now live inside Strategies, against the real strategy they judge. Evolution
 * moved into Settings, where an update check belongs. */
const tabs = ['Overview', 'Research Lab', 'Strategies', 'Prop Firm', 'Console', 'Settings'] as const
type Tab = (typeof tabs)[number]

const ICONS: Record<Tab, typeof Layers> = {
  Overview: ChartNoAxesCombined,
  'Research Lab': Microscope,
  Strategies: Layers,
  'Prop Firm': ShieldCheck,
  Console: MessageSquare,
  Settings: SlidersHorizontal,
}

const EYEBROW: Record<Tab, string> = {
  Overview: 'AUTONOMOUS ENGINE',
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
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })
  const events = useQuery({
    queryKey: ['activity'], refetchInterval: 5_000,
    queryFn: () => getJson<ActivityEvent[]>('/activity?limit=80'),
  })

  const online = health.isSuccess
  const list = strategies.data ?? []
  const tested = list.filter((s) => s.latest).length
  const profitable = list.filter((s) => (s.latest?.net_pnl ?? 0) > 0).length
  const realEvidence = list.filter(
    (s) => s.latest?.evidence_tier === 'VALIDATION_OOS' || s.latest?.evidence_tier === 'HOLDOUT',
  ).length

  return (
    <div className="app">
      <header className="topbar">
        <Wordmark />
        <nav className="tabs" aria-label="Sections">
          {tabs.map((item) => {
            const Icon = ICONS[item]
            return (
              <button
                key={item}
                aria-label={item}
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
            {online ? 'ENGINE LIVE' : 'ENGINE DOWN'}
          </span>
          <span className="topstat">STRATEGIES <b>{summary.data?.strategy_count ?? 0}</b></span>
          <span className="topstat">BACKTESTS <b>{summary.data?.backtest_count ?? 0}</b></span>
          <span className="topstat"><LockKeyhole size={11} /> NO LIVE ORDERS</span>
        </div>
      </header>

      <div className="body">
        <aside className="rail" aria-label="Live counters">
          <div className="rail-group af-panel-in">
            <div className="rail-head"><span>Library</span><span>{summary.data?.strategy_count ?? 0}</span></div>
            <div className="rail-row"><span>Strategies</span><b>{summary.data?.strategy_count ?? 0}</b></div>
            <div className="rail-row"><span>Backtested</span><b>{tested}</b></div>
            <div className="rail-row"><span>Net positive</span><b className={profitable ? 'good' : ''}>{profitable}</b></div>
            <div className="rail-row"><span>Templates</span><b>{summary.data?.template_count ?? 0}</b></div>
          </div>
          <div className="rail-group af-panel-in">
            <div className="rail-head"><span>Evidence</span></div>
            <div className="rail-row"><span>Out-of-sample runs</span><b className={realEvidence ? 'good' : ''}>{realEvidence}</b></div>
            <div className="rail-row"><span>Backtest artifacts</span><b>{summary.data?.backtest_count ?? 0}</b></div>
            <div className="rail-row"><span>Prop rule sets</span><b>{rules.data?.length ?? 0}</b></div>
            <div className="rail-row">
              <span>Data gate</span>
              <b className={health.data?.data_gate?.startsWith('REAL') ? 'good' : 'bad'}>
                {health.data?.data_gate ?? '—'}
              </b>
            </div>
          </div>
          <div className="rail-group af-panel-in">
            <div className="rail-head"><span>Families</span></div>
            {(summary.data?.families ?? []).map((f) => (
              <div className="rail-row" key={f}>
                <span>{f}</span>
                <b>{list.filter((s) => s.family === f).length}</b>
              </div>
            ))}
            {!summary.data?.families.length && <div className="rail-row"><span className="sub">none yet</span></div>}
          </div>
          <div className="rail-group">
            <div className="rail-head"><span>On disk</span></div>
            <div className="rail-row"><span className="sub">{summary.data?.strategies_path ?? ''}</span></div>
          </div>
        </aside>

        <main className="main">
          <div className="view-head">
            <div>
              <p className="eyebrow">{EYEBROW[tab]}</p>
              <h1>{tab}</h1>
            </div>
            <div className="chips">
              <span className="chip is-locked"><LockKeyhole /> PAPER ONLY</span>
              <span className={health.data?.data_gate?.startsWith('REAL') ? 'chip is-good' : 'chip'}>
                DATA {health.data?.data_gate ?? '—'}
              </span>
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

          {online && tab === 'Overview' && <OverviewView />}
          {online && tab === 'Research Lab' && <ResearchLabView />}
          {online && tab === 'Strategies' && <StrategiesView />}
          {online && tab === 'Prop Firm' && <PropFirmView />}
          {online && tab === 'Console' && <ConsoleView />}
          {online && tab === 'Settings' && <SettingsView />}
        </main>
      </div>

      <OrchestratorLog events={events.data ?? []} />
    </div>
  )
}

function OrchestratorLog({ events }: { events: ActivityEvent[] }) {
  const [collapsed, setCollapsed] = useState(false)
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
