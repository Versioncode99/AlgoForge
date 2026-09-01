import { useQuery } from '@tanstack/react-query'
import { Activity, Bot, Braces, ChartNoAxesCombined, GitBranch, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson } from './api'
import { EquityChart, RegimeChart } from './charts'
import type { Analysis, Debate, Evolution, PropSimulation, Rule, Run } from './types'

const tabs = ['Overview', 'Verdict', 'Regimes', 'Risk & Monte Carlo', 'Prop Firm', 'Agents', 'Evolution'] as const
type Tab = typeof tabs[number]
const fmt = (value: number, style: 'money' | 'percent' | 'number' = 'number') => style === 'money' ? `$${value.toLocaleString(undefined, {maximumFractionDigits: 0})}` : style === 'percent' ? `${(value * 100).toFixed(1)}%` : value.toLocaleString()

export function App() {
  const [tab, setTab] = useState<Tab>('Overview')
  const runs = useQuery({queryKey: ['runs'], queryFn: () => getJson<Run[]>('/runs')})
  const runId = runs.data?.[0]?.run_id
  const analysis = useQuery({queryKey: ['analysis', runId], enabled: !!runId, queryFn: () => getJson<Analysis>(`/analysis/${runId}`)})
  const rules = useQuery({queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules')})
  const [ruleId, setRuleId] = useState('')
  useEffect(() => { if (!ruleId && rules.data?.length) setRuleId(rules.data[0].rule_id) }, [rules.data, ruleId])
  const prop = useQuery({queryKey: ['prop', runId, ruleId], enabled: !!runId && !!ruleId, queryFn: () => getJson<PropSimulation>(`/prop/simulations/${runId}?rule_id=${ruleId}`)})
  const agents = useQuery({queryKey: ['agents', runId], enabled: !!runId, queryFn: () => getJson<Debate>(`/agents/${runId}`)})
  const evolution = useQuery({queryKey: ['evolution'], queryFn: () => getJson<Evolution>('/evolution/overview')})
  const pending = runs.isPending || analysis.isPending
  const error = runs.error || analysis.error

  return <div className="shell">
    <aside>
      <div className="brand"><Braces size={18}/><span>ALGOFORGE</span><small>RESEARCH OS</small></div>
      <nav aria-label="Primary">{tabs.map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item === 'Prop Firm' ? <ShieldCheck/> : item === 'Agents' ? <Bot/> : item === 'Evolution' ? <GitBranch/> : <ChartNoAxesCombined/>}<span>{item}</span></button>)}</nav>
      <div className="rail-status"><i/><span>Paper runtime</span><strong>LOCAL</strong></div>
    </aside>
    <main>
      <header><div><p className="eyebrow">MNQ / SAMPLE FIXTURE</p><h1>{tab}</h1></div><div className="header-flags"><span><LockKeyhole/>NO LIVE ORDERS</span><span><Activity/>ENGINE 0.1</span></div></header>
      <div className="notice">SAMPLE DATA · UNCALIBRATED · RESEARCH ONLY — import real point-in-time OOS trades before relying on any result.</div>
      {pending && <div className="state">Loading deterministic research ledger…</div>}
      {error && <div className="state error">API unavailable. Start the local AlgoForge API on port 8765.</div>}
      {analysis.data && <Workspace tab={tab} analysis={analysis.data} prop={prop.data} rules={rules.data ?? []} ruleId={ruleId} setRuleId={setRuleId} agents={agents.data} evolution={evolution.data}/>} 
    </main>
  </div>
}

function Workspace({tab, analysis, prop, rules, ruleId, setRuleId, agents, evolution}: {tab: Tab; analysis: Analysis; prop?: PropSimulation; rules: Rule[]; ruleId: string; setRuleId: (id: string) => void; agents?: Debate; evolution?: Evolution}) {
  const v = analysis.verdict
  if (tab === 'Prop Firm') return <PropView prop={prop} rules={rules} ruleId={ruleId} setRuleId={setRuleId}/>
  if (tab === 'Agents') return <AgentView agents={agents}/>
  if (tab === 'Evolution') return <EvolutionView evolution={evolution}/>
  if (tab === 'Regimes') return <section><SectionTitle kicker="MARKET STATES" title="Performance is conditional, not universal"/><div className="panel"><RegimeChart items={analysis.regimes}/></div><div className="regime-grid">{analysis.regimes.map(r => <article key={r.name}><span>{r.name}</span><strong className={r.net_pnl >= 0 ? 'good' : 'bad'}>{fmt(r.net_pnl, 'money')}</strong><small>{r.trade_count} trades · {r.confidence} confidence</small></article>)}</div></section>
  if (tab === 'Risk & Monte Carlo') return <section><SectionTitle kicker="FIXED-SEED STATIONARY BOOTSTRAP" title="Understand the range, not one backtest"/><Metrics items={[['Paths', fmt(analysis.risk.path_count)], ['Median terminal', fmt(analysis.risk.terminal_median, 'money')], ['Loss probability', fmt(analysis.risk.loss_probability, 'percent')], ['CVaR 95', fmt(analysis.risk.cvar_95, 'money')]]}/><div className="panel"><EquityChart paths={analysis.risk.equity_paths}/></div><p className="warning">{analysis.risk.warnings.join(' · ')}</p></section>
  if (tab === 'Verdict') return <VerdictView analysis={analysis}/>
  return <section><SectionTitle kicker="DETERMINISTIC JUDGE" title="A research verdict you can audit"/><div className="hero-grid"><div className="grade"><span>GRADE</span><strong>{v.grade}</strong><p>{v.decision} under sample-only gates</p></div><div className="dimensions">{Object.entries(v.dimensions).map(([name, score]) => <div key={name}><span>{name}</span><strong>{score}</strong><meter min="0" max="100" value={score}/></div>)}</div></div><Metrics items={[['Net P&L', fmt(v.metrics.net_pnl, 'money')], ['Win rate', fmt(v.metrics.win_rate, 'percent')], ['Profit factor', v.metrics.profit_factor.toFixed(2)], ['Max drawdown', fmt(v.metrics.max_drawdown, 'money')]]}/><div className="two-col"><div className="panel"><h2>Monte Carlo equity paths</h2><EquityChart paths={analysis.risk.equity_paths}/></div><div className="panel"><h2>Regime attribution</h2><RegimeChart items={analysis.regimes}/></div></div></section>
}

function VerdictView({analysis}: {analysis: Analysis}) { return <section><SectionTitle kicker="G0–G9 EVIDENCE GATES" title="Every decision exposes its failure path"/><div className="gate-list">{analysis.verdict.gates.map(g => <article key={g.gate}><b className={g.status === 'PASS' ? 'good' : 'bad'}>{g.status}</b><div><span>{g.gate} · {g.name}</span><p>{g.finding}</p></div></article>)}</div></section> }
function PropView({prop, rules, ruleId, setRuleId}: {prop?: PropSimulation; rules: Rule[]; ruleId: string; setRuleId: (id: string) => void}) { return <section><SectionTitle kicker="RULE-EXACT ACCOUNT SIMULATION" title="Challenge and funded paths stay separate"/><label className="select-label">Rule fixture<select value={ruleId} onChange={e => setRuleId(e.target.value)}>{rules.map(r => <option key={r.rule_id} value={r.rule_id}>{r.display_name}</option>)}</select></label>{prop && <><div className="split"><div className="donut" style={{'--pass': `${prop.pass_rate * 360}deg`} as React.CSSProperties}><div><strong>{fmt(prop.pass_rate, 'percent')}</strong><span>pass / survive</span></div></div><Metrics items={[['Start', fmt(prop.rule.starting_balance, 'money')], ['Target', fmt(prop.rule.profit_target, 'money')], ['Max loss', fmt(prop.rule.maximum_loss, 'money')], ['95% interval', `${fmt(prop.interval_low, 'percent')}–${fmt(prop.interval_high, 'percent')}`]]}/></div><div className="panel"><h2>Challenge equity paths · {prop.path_count} simulations</h2><EquityChart paths={prop.equity_paths} start={prop.rule.starting_balance}/></div><p className="warning">UNVERIFIED RULE FIXTURE — locked for research override only; verify the provider’s current official rulebook before use.</p></>}</section> }
function AgentView({agents}: {agents?: Debate}) { return <section><SectionTitle kicker="CITED ADVERSARIAL REVIEW" title="Agents explain; the judge decides"/><div className="agent-grid">{agents?.claims.map(c => <article key={c.role_id}><span>{c.role_id}</span><b className={c.stance === 'OPPOSE' ? 'bad' : c.stance === 'SUPPORT' ? 'good' : 'warn'}>{c.stance}</b><p>{c.statement}</p><small>{Math.round(c.confidence * 100)}% stated confidence · cited</small></article>)}</div><p className="warning">Numeric verdict locked: {agents?.numeric_verdict_locked ? 'YES' : 'NO'} · dissent present: {agents?.dissent_present ? 'YES' : 'NO'}</p></section> }
function EvolutionView({evolution}: {evolution?: Evolution}) { return <section><SectionTitle kicker="FORGEKEEPER" title="Research updates behind release gates"/><div className="pipeline"><span>SCAN</span><i>→</i><span>LICENCE</span><i>→</i><span>SANDBOX</span><i>→</i><span>CANARY</span><i>→</i><span>HUMAN APPROVAL</span></div><div className="panel evolution"><div><small>DISCOVERED CANDIDATE</small><h2>{evolution?.candidate.repository}</h2><p>Lane: <b>{evolution?.candidate.lane}</b></p></div><div><small>ACTIVE RELEASES</small><strong>{evolution?.release_count ?? 0}</strong><p>Automatic live changes: NEVER</p></div></div><p className="warning">No repository code is adopted without provenance, licence classification, regression tests, and explicit activation.</p></section> }
function SectionTitle({kicker, title}: {kicker: string; title: string}) { return <div className="section-title"><p>{kicker}</p><h2>{title}</h2></div> }
function Metrics({items}: {items: [string, string][]}) { return <div className="metrics">{items.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div> }
