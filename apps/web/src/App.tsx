import { useQuery } from '@tanstack/react-query'
import {
  Activity, Bot, ChartNoAxesCombined, GitBranch, Layers, LockKeyhole, ScrollText,
  ShieldCheck, SlidersHorizontal, TriangleAlert,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson } from './api'
import { EquityChart, RegimeChart } from './charts'
import { Wordmark } from './components/Logo'
import { money, num, pct } from './lib'
import type {
  ActivityEvent, Analysis, Debate, Evolution, PropSimulation, Rule, Run,
  StrategyListItem, Summary,
} from './types'
import { StrategiesView } from './views/Strategies'

const tabs = [
  'Overview', 'Strategies', 'Verdict', 'Regimes', 'Risk & Monte Carlo',
  'Prop Firm', 'Agents', 'Evolution',
] as const
type Tab = (typeof tabs)[number]

const ICONS: Record<Tab, typeof Activity> = {
  Overview: ChartNoAxesCombined,
  Strategies: Layers,
  Verdict: ShieldCheck,
  Regimes: SlidersHorizontal,
  'Risk & Monte Carlo': Activity,
  'Prop Firm': ShieldCheck,
  Agents: Bot,
  Evolution: GitBranch,
}

export function App() {
  const [tab, setTab] = useState<Tab>('Overview')

  const health = useQuery({
    queryKey: ['health'], refetchInterval: 15_000,
    queryFn: () => getJson<{ status: string }>('/health'),
  })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const events = useQuery({
    queryKey: ['activity'], refetchInterval: 5_000,
    queryFn: () => getJson<ActivityEvent[]>('/activity?limit=80'),
  })

  const runs = useQuery({ queryKey: ['runs'], queryFn: () => getJson<Run[]>('/runs') })
  const runId = runs.data?.[0]?.run_id
  const analysis = useQuery({
    queryKey: ['analysis', runId], enabled: !!runId,
    queryFn: () => getJson<Analysis>(`/analysis/${runId}`),
  })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })
  const [ruleId, setRuleId] = useState('')
  useEffect(() => { if (!ruleId && rules.data?.length) setRuleId(rules.data[0].rule_id) }, [rules.data, ruleId])
  const prop = useQuery({
    queryKey: ['prop', runId, ruleId], enabled: !!runId && !!ruleId,
    queryFn: () => getJson<PropSimulation>(`/prop/simulations/${runId}?rule_id=${ruleId}`),
  })
  const agents = useQuery({
    queryKey: ['agents', runId], enabled: !!runId,
    queryFn: () => getJson<Debate>(`/agents/${runId}`),
  })
  const evolution = useQuery({ queryKey: ['evolution'], queryFn: () => getJson<Evolution>('/evolution/overview') })

  const online = health.isSuccess
  const tested = strategies.data?.filter((s) => s.latest).length ?? 0
  const profitable = strategies.data?.filter((s) => (s.latest?.net_pnl ?? 0) > 0).length ?? 0

  return (
    <div className="app">
      <header className="topbar">
        <Wordmark />
        <nav className="tabs" aria-label="Sections">
          {tabs.map((item) => {
            const Icon = ICONS[item]
            return (
              <button key={item} aria-label={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>
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
          <div className="rail-group">
            <div className="rail-head"><span>Library</span><span>{summary.data?.strategy_count ?? 0}</span></div>
            <div className="rail-row"><span>Strategies</span><b>{summary.data?.strategy_count ?? 0}</b></div>
            <div className="rail-row"><span>Backtested</span><b>{tested}</b></div>
            <div className="rail-row"><span>Net positive</span><b className={profitable ? 'good' : ''}>{profitable}</b></div>
            <div className="rail-row"><span>Templates</span><b>{summary.data?.template_count ?? 0}</b></div>
          </div>
          <div className="rail-group">
            <div className="rail-head"><span>Evidence</span></div>
            <div className="rail-row"><span>Backtest artifacts</span><b>{summary.data?.backtest_count ?? 0}</b></div>
            <div className="rail-row"><span>Ledger runs</span><b>{runs.data?.length ?? 0}</b></div>
            <div className="rail-row"><span>Prop rule sets</span><b>{rules.data?.length ?? 0}</b></div>
            <div className="rail-row"><span>Data gate</span><b className="bad">{summary.data?.data_gate ?? '—'}</b></div>
          </div>
          <div className="rail-group">
            <div className="rail-head"><span>Families</span></div>
            {(summary.data?.families ?? []).map((f) => (
              <div className="rail-row" key={f}>
                <span>{f}</span>
                <b>{strategies.data?.filter((s) => s.family === f).length ?? 0}</b>
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
              <p className="eyebrow">{tab === 'Strategies' ? 'BUILD · RUN · JUDGE' : 'MNQ.SYNTH · 1M BARS'}</p>
              <h1>{tab}</h1>
            </div>
            <div className="chips">
              <span className="chip is-locked"><LockKeyhole /> PAPER ONLY</span>
              <span className="chip">ENGINE 0.2</span>
            </div>
          </div>

          <div className="notice">
            <TriangleAlert />
            <span>
              SYNTHETIC DATA · UNCALIBRATED — bars come from a seeded generator with no edge
              deliberately embedded. Wire Databento before trusting any number here.
            </span>
          </div>

          {!online && <div className="state error">API unavailable. Start the AlgoForge API on port 8765.</div>}

          {online && tab === 'Strategies' && <StrategiesView />}
          {online && tab !== 'Strategies' && analysis.data && (
            <Workspace
              tab={tab} analysis={analysis.data} prop={prop.data} rules={rules.data ?? []}
              ruleId={ruleId} setRuleId={setRuleId} agents={agents.data} evolution={evolution.data}
              strategies={strategies.data ?? []}
            />
          )}
          {online && tab !== 'Strategies' && !analysis.data && <div className="state">Loading ledger…</div>}
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
        <button className="logbar-toggle" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? 'Show' : 'Hide'}
        </button>
      </div>
      <div className="logbar-body">
        {events.length === 0 && (
          <div className="logline"><time>--:--:--</time><span className="stage">IDLE</span>
            <span className="msg">Nothing has run yet. Create a strategy and run a backtest.</span></div>
        )}
        {events.map((e, i) => (
          <div className="logline" key={`${e.ts}-${i}`} data-level={e.level}>
            <time>{e.ts.slice(11, 19)}</time>
            <span className="stage">{e.stage}</span>
            <span className="msg">{e.message}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function Workspace({ tab, analysis, prop, rules, ruleId, setRuleId, agents, evolution, strategies }: {
  tab: Tab; analysis: Analysis; prop?: PropSimulation; rules: Rule[]
  ruleId: string; setRuleId: (id: string) => void
  agents?: Debate; evolution?: Evolution; strategies: StrategyListItem[]
}) {
  const v = analysis.verdict
  if (tab === 'Prop Firm') return <PropView prop={prop} rules={rules} ruleId={ruleId} setRuleId={setRuleId} />
  if (tab === 'Agents') return <AgentView agents={agents} />
  if (tab === 'Evolution') return <EvolutionView evolution={evolution} />

  if (tab === 'Regimes')
    return (
      <section>
        <SectionTitle kicker="MARKET STATES" title="Performance is conditional, not universal" />
        <div className="panel"><div className="panel-body"><RegimeChart items={analysis.regimes} /></div></div>
        <div className="grid-4">
          {analysis.regimes.map((r) => (
            <article className="card" key={r.name}>
              <span>{r.name}</span>
              <strong className={r.net_pnl >= 0 ? 'good' : 'bad'}>{money(r.net_pnl)}</strong>
              <small>{r.trade_count} trades · {r.confidence} confidence</small>
            </article>
          ))}
        </div>
      </section>
    )

  if (tab === 'Risk & Monte Carlo')
    return (
      <section>
        <SectionTitle kicker="FIXED-SEED STATIONARY BOOTSTRAP" title="Understand the range, not one backtest" />
        <Metrics items={[
          ['Paths', num(analysis.risk.path_count)],
          ['Median terminal', money(analysis.risk.terminal_median)],
          ['Loss probability', pct(analysis.risk.loss_probability)],
          ['CVaR 95', money(analysis.risk.cvar_95)],
        ]} />
        <div className="panel" style={{ marginTop: 12 }}>
          <header><h2>Equity paths</h2></header>
          <div className="panel-body"><EquityChart paths={analysis.risk.equity_paths} /></div>
        </div>
        <p className="warning">{analysis.risk.warnings.join(' · ')}</p>
      </section>
    )

  if (tab === 'Verdict')
    return (
      <section>
        <SectionTitle kicker="G0–G9 EVIDENCE GATES" title="Every decision exposes its failure path" />
        <div className="gates">
          {analysis.verdict.gates.map((g) => (
            <div className="gate" key={g.gate}>
              <span className="id">{g.gate}</span>
              <b className={`status ${g.status === 'PASS' ? 'good' : 'bad'}`}>{g.status}</b>
              <div><span className="name">{g.name}</span><p>{g.finding}</p></div>
            </div>
          ))}
        </div>
      </section>
    )

  return (
    <section>
      <SectionTitle kicker="DETERMINISTIC JUDGE" title="A verdict you can audit, line by line" />
      <div className="hero">
        <div className="grade">
          <span>GRADE</span>
          <strong>{v.grade}</strong>
          <p>{v.decision} on the ledger fixture</p>
        </div>
        <div className="dimensions">
          {Object.entries(v.dimensions).map(([name, score]) => (
            <div className="dimension" key={name}>
              <span>{name}</span><b>{score}</b>
              <div className="bar"><i style={{ width: `${score}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <Metrics items={[
          ['Net P&L', money(v.metrics.net_pnl)],
          ['Win rate', pct(v.metrics.win_rate)],
          ['Profit factor', v.metrics.profit_factor.toFixed(2)],
          ['Max drawdown', money(v.metrics.max_drawdown)],
        ]} />
      </div>
      <div className="grid-2">
        <div className="panel">
          <header><h2>Monte Carlo equity paths</h2></header>
          <div className="panel-body"><EquityChart paths={analysis.risk.equity_paths} /></div>
        </div>
        <div className="panel">
          <header><h2>Strategy library</h2></header>
          <div className="panel-body panel-scroll">
            <table className="tbl">
              <thead><tr><th>Strategy</th><th className="num">Net</th><th className="num">Trades</th></tr></thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.strategy_id}>
                    <td>{s.name}</td>
                    <td className={`num ${(s.latest?.net_pnl ?? 0) >= 0 ? 'good' : 'bad'}`}>
                      {s.latest ? s.latest.net_pnl.toFixed(2) : '—'}
                    </td>
                    <td className="num">{s.latest?.trade_count ?? '—'}</td>
                  </tr>
                ))}
                {strategies.length === 0 && <tr><td colSpan={3}>No strategies yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  )
}

function PropView({ prop, rules, ruleId, setRuleId }: {
  prop?: PropSimulation; rules: Rule[]; ruleId: string; setRuleId: (id: string) => void
}) {
  return (
    <section>
      <SectionTitle kicker="RULE-EXACT ACCOUNT SIMULATION" title="Challenge and funded paths stay separate" />
      <label className="field" style={{ marginBottom: 12 }}>
        Rule fixture
        <select value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
          {rules.map((r) => <option key={r.rule_id} value={r.rule_id}>{r.display_name}</option>)}
        </select>
      </label>
      {prop && (
        <>
          <div className="split">
            <div className="donut-wrap">
              <div className="donut" style={{ '--pass-deg': `${prop.pass_rate * 360}deg` } as React.CSSProperties}>
                <div><strong>{pct(prop.pass_rate)}</strong><span>pass / survive</span></div>
              </div>
            </div>
            <Metrics bare items={[
              ['Start', money(prop.rule.starting_balance)],
              ['Target', money(prop.rule.profit_target)],
              ['Max loss', money(prop.rule.maximum_loss)],
              ['95% interval', `${pct(prop.interval_low)}–${pct(prop.interval_high)}`],
            ]} />
          </div>
          <div className="panel" style={{ marginTop: 12 }}>
            <header><h2>Challenge equity paths · {prop.path_count} simulations</h2></header>
            <div className="panel-body"><EquityChart paths={prop.equity_paths} /></div>
          </div>
          <p className="warning">
            UNVERIFIED RULE FIXTURE — locked for research override only; verify the provider's
            current official rulebook before relying on this number.
          </p>
        </>
      )}
    </section>
  )
}

function AgentView({ agents }: { agents?: Debate }) {
  return (
    <section>
      <SectionTitle kicker="CITED ADVERSARIAL REVIEW" title="Agents explain; the judge decides" />
      <div className="grid-3">
        {agents?.claims.map((c) => (
          <article className="agent" key={c.role_id}>
            <header>
              <span>{c.role_id}</span>
              <b className={c.stance === 'OPPOSE' ? 'bad' : c.stance === 'SUPPORT' ? 'good' : 'warn'}>{c.stance}</b>
            </header>
            <p>{c.statement}</p>
            <small>{Math.round(c.confidence * 100)}% stated confidence · cited</small>
          </article>
        ))}
      </div>
      <p className="warning">
        Numeric verdict locked: {agents?.numeric_verdict_locked ? 'YES' : 'NO'} ·
        dissent present: {agents?.dissent_present ? 'YES' : 'NO'}
      </p>
    </section>
  )
}

function EvolutionView({ evolution }: { evolution?: Evolution }) {
  return (
    <section>
      <SectionTitle kicker="FORGEKEEPER" title="Updates arrive behind release gates" />
      <div className="flow">
        <span>SCAN</span><i>→</i><span>LICENCE</span><i>→</i><span>SANDBOX</span><i>→</i>
        <span>CANARY</span><i>→</i><span>HUMAN APPROVAL</span>
      </div>
      <div className="grid-2" style={{ gridTemplateColumns: '2fr 1fr' }}>
        <div className="card">
          <span>DISCOVERED CANDIDATE</span>
          <strong style={{ fontSize: 15 }}>{evolution?.candidate.repository}</strong>
          <small>Lane: {evolution?.candidate.lane}</small>
        </div>
        <div className="card">
          <span>ACTIVE RELEASES</span>
          <strong className="good">{evolution?.release_count ?? 0}</strong>
          <small>Automatic live changes: NEVER</small>
        </div>
      </div>
      <p className="warning">
        No repository code is adopted without provenance, licence classification, regression tests
        and explicit human activation.
      </p>
    </section>
  )
}

function SectionTitle({ kicker, title }: { kicker: string; title: string }) {
  return <div className="section-title"><p>{kicker}</p><h2>{title}</h2></div>
}

function Metrics({ items, bare }: { items: [string, string][]; bare?: boolean }) {
  return (
    <div className="metrics" style={bare ? { border: 0, borderRadius: 0 } : undefined}>
      {items.map(([label, value]) => (
        <div key={label}><span>{label}</span><strong>{value}</strong></div>
      ))}
    </div>
  )
}
