import { useMutation, useQuery } from '@tanstack/react-query'
import { Gauge, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson, postJson } from '../api'
import { EquityChart, ReturnDrawdownChart, TargetReachChart, TerminalHistogram } from '../charts'
import { money, pct } from '../lib'
import type { PropResult, Rule, StrategyListItem } from '../types'

type PropPane = 'Prop Firm' | 'Verdict' | 'Regimes' | 'Risk & Monte Carlo'
const panes: PropPane[] = ['Prop Firm', 'Verdict', 'Regimes', 'Risk & Monte Carlo']

/** Rule-aware account survival analysis driven only by a selected strategy's
 * daily P&L. This is decision support, not a promise of passing a challenge. */
export function PropFirmView() {
  const [strategyId, setStrategyId] = useState('')
  const [ruleId, setRuleId] = useState('')
  const [pane, setPane] = useState<PropPane>('Prop Firm')
  const [phase, setPhase] = useState<'CHALLENGE' | 'FUNDED'>('CHALLENGE')
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PropResult | null>(null)

  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })
  const tested = strategies.data?.filter((s) => s.latest) ?? []
  const phaseRules = rules.data?.filter((r) => r.phase === phase) ?? []

  useEffect(() => { if (!strategyId && tested.length) setStrategyId(tested[0].strategy_id) }, [tested, strategyId])
  useEffect(() => {
    if (!phaseRules.some((rule) => rule.rule_id === ruleId)) {
      setRuleId(phaseRules[0]?.rule_id ?? '')
      setResult(null)
    }
  }, [phaseRules, ruleId])

  const run = useMutation({
    mutationFn: () => postJson<PropResult>(`/strategies/${strategyId}/prop`, { rule_id: ruleId, paths: 1000 }),
    onSuccess: (data) => { setResult(data); setError(null) },
    onError: (e: Error) => { setError(e.message); setResult(null) },
  })

  const rule = rules.data?.find((r) => r.rule_id === ruleId)
  const passRate = result?.pass_rate ?? 0
  const failRate = result ? result.fail_count / result.path_count : 0
  const timeoutRate = result ? result.timeout_count / result.path_count : 0

  return (
    <section className="prop-workspace stack">
      <div className="prop-subnav">
        <div role="tablist" aria-label="Prop analytics">
          {panes.map((item) => <button key={item} className={pane === item ? 'active' : ''} onClick={() => setPane(item)}>{item}</button>)}
        </div>
        <div className="phase-switch" aria-label="Account phase">
          <button className={phase === 'CHALLENGE' ? 'active' : ''} onClick={() => setPhase('CHALLENGE')}>Challenge</button>
          <button className={phase === 'FUNDED' ? 'active' : ''} onClick={() => setPhase('FUNDED')}>Funded</button>
        </div>
      </div>

      <div className="section-title prop-title">
        <div><p>BLOCK-BOOTSTRAP ACCOUNT SIMULATION</p><h2>{pane === 'Prop Firm' ? 'Race the target against the loss boundary' : pane}</h2></div>
        {result && <span className="evidence-badge">{result.trading_days} OBSERVED DAYS</span>}
      </div>

      <div className="prop-controls">
        <label className="field">Strategy
          <select value={strategyId} onChange={(e) => { setStrategyId(e.target.value); setResult(null) }}>
            {tested.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name} · {s.latest?.evidence_tier ?? 'UNKNOWN'}</option>)}
          </select>
        </label>
        <label className="field">Rule fixture
          <select value={ruleId} onChange={(e) => { setRuleId(e.target.value); setResult(null) }}>
            {phaseRules.map((r) => <option key={r.rule_id} value={r.rule_id}>{r.display_name}</option>)}
          </select>
        </label>
        <button className="btn primary" disabled={!strategyId || !ruleId || run.isPending} onClick={() => run.mutate()}>
          <ShieldCheck /> {run.isPending ? 'Simulating...' : 'Run 1,000 paths'}
        </button>
      </div>

      {tested.length === 0 && <div className="state">No backtested strategy yet. Run a backtest, then return here.</div>}
      {phaseRules.length === 0 && <div className="state">No {phase.toLowerCase()} rule fixture is configured.</div>}
      {error && <p className="warning bad">{error}</p>}
      {!result && tested.length > 0 && phaseRules.length > 0 && (
        <div className="prop-empty"><Gauge /><h3>No simulation selected</h3><p>Choose a strategy and rule set. The simulator preserves clustered losing periods and shows uncertainty rather than a single pass-rate headline.</p></div>
      )}

      {result && pane === 'Prop Firm' && (
        <>
          <div className="prop-hero">
            <div className="donut-wrap">
              <div className="donut" style={{ '--pass-deg': `${passRate * 360}deg` } as React.CSSProperties}>
                <div><strong>{pct(passRate)}</strong><span>{result.path_count.toLocaleString()} paths</span></div>
              </div>
              <ul className="donut-legend">
                <li><i className="dot good" />Pass {pct(passRate)}{result.avg_days_to_pass ? ` · ${result.avg_days_to_pass}d avg` : ''}</li>
                <li><i className="dot bad" />Fail {pct(failRate)}{result.avg_days_to_fail ? ` · ${result.avg_days_to_fail}d avg` : ''}</li>
                <li><i className="dot warn" />Timeout {pct(timeoutRate)}</li>
              </ul>
            </div>
            <div className="metrics prop-summary">
              <div><span>Pass probability</span><strong className={passRate >= .5 ? 'good' : 'bad'}>{pct(passRate)}</strong><small>95% CI {pct(result.interval_low)}–{pct(result.interval_high)}</small></div>
              <div><span>Risk of ruin</span><strong className={result.risk_of_ruin > .25 ? 'bad' : 'warn'}>{pct(result.risk_of_ruin)}</strong><small>loss boundary first</small></div>
              <div><span>Median terminal</span><strong>{money(result.median_terminal)}</strong><small>start {money(result.rule.starting_balance)}</small></div>
              <div><span>Mean payout</span><strong>{money(result.mean_payout)}</strong><small>target {money(result.rule.profit_target)}</small></div>
            </div>
          </div>
          <div className="panel"><header><h2>Challenge equity paths · {result.equity_paths.length} representative paths</h2><span>fixed seed</span></header><div className="panel-body"><EquityChart paths={result.equity_paths} start={result.rule.starting_balance} /></div></div>
          <div className="grid-2 prop-grid">
            <div className="panel"><header><h2>Reaching target</h2><span>cumulative probability</span></header><div className="panel-body"><TargetReachChart points={result.target_reach_curve} /></div></div>
            <BoundaryRace result={result} />
          </div>
        </>
      )}

      {result && pane === 'Verdict' && <PropVerdict result={result} strategy={tested.find((s) => s.strategy_id === strategyId)} rule={rule} />}

      {result && pane === 'Regimes' && (
        <div className="grid-2 prop-grid">
          <div className="panel"><header><h2>Observed daily sequence</h2><span>source material</span></header><div className="panel-body regime-sequence">{result.daily_pnl.map((value, index) => <i key={index} title={`${index + 1}: ${money(value)}`} style={{ '--magnitude': Math.min(1, Math.abs(value) / Math.max(...result.daily_pnl.map(Math.abs))) } as React.CSSProperties} className={value >= 0 ? 'up' : 'down'} />)}</div></div>
          <div className="panel"><header><h2>Regime caveat</h2></header><div className="panel-body"><p className="prose">The stationary block bootstrap preserves short runs of winning and losing days, but it does not create new market regimes. A strategy with one observed regime cannot be treated as regime-robust.</p><p className="warning">Use the main Regimes view for observed conditional performance. This tab shows only the sequence feeding the prop simulation.</p></div></div>
        </div>
      )}

      {result && pane === 'Risk & Monte Carlo' && (
        <>
          <div className="metrics risk-metrics">
            <div><span>VaR 95</span><strong className="bad">{money(result.tail_risk.var_95)}</strong><small>5th percentile terminal P&L</small></div>
            <div><span>CVaR 95</span><strong className="bad">{money(result.tail_risk.cvar_95)}</strong><small>mean beyond VaR</small></div>
            <div><span>Skew / excess kurtosis</span><strong>{result.tail_risk.skewness.toFixed(2)} / {result.tail_risk.excess_kurtosis.toFixed(2)}</strong><small>outcome shape</small></div>
            <div><span>P05 / P50 / P95</span><strong>{money(result.tail_risk.terminal_p05)}</strong><small>{money(result.tail_risk.terminal_median)} / {money(result.tail_risk.terminal_p95)}</small></div>
          </div>
          <div className="grid-2 risk-grid">
            <div className="panel"><header><h2>Outcome shape</h2><span>terminal P&L</span></header><div className="panel-body"><TerminalHistogram bins={result.terminal_histogram} /></div></div>
            <div className="panel"><header><h2>Return vs drawdown map</h2><span>each point is one path</span></header><div className="panel-body"><ReturnDrawdownChart points={result.return_drawdown_map} /></div></div>
          </div>
          <div className="panel"><header><h2>Monte Carlo equity paths</h2><span>block bootstrap</span></header><div className="panel-body"><EquityChart paths={result.equity_paths} start={result.rule.starting_balance} /></div></div>
        </>
      )}

      {result && <p className="warning"><TriangleAlert /> {rule?.verified ? 'Rule fixture is currently marked verified.' : 'UNVERIFIED RULE FIXTURE — check the provider’s current rulebook.'} This uses modelled daily settlement and {result.trading_days} observed days; it is not a funded-account forecast.</p>}
    </section>
  )
}

function BoundaryRace({ result }: { result: PropResult }) {
  const race = result.boundary_race
  return (
    <div className="panel boundary-panel"><header><h2>Race to the boundary</h2><span>first event wins</span></header><div className="panel-body">
      <div className="boundary-bar"><i className="target" style={{ width: `${race.target_first_probability * 100}%` }} /><i className="timeout" style={{ width: `${race.timeout_probability * 100}%` }} /><i className="loss" style={{ width: `${race.loss_first_probability * 100}%` }} /></div>
      <div className="boundary-labels"><span className="good">Target {pct(race.target_first_probability)}</span><span className="warn">Timeout {pct(race.timeout_probability)}</span><span className="bad">Loss {pct(race.loss_first_probability)}</span></div>
      <div className="race-times"><div><span>Target days P10 / P50 / P90</span><strong>{formatDays(race.target_days_p10)} / {formatDays(race.target_days_median)} / {formatDays(race.target_days_p90)}</strong></div><div><span>Loss days P10 / P50 / P90</span><strong>{formatDays(race.loss_days_p10)} / {formatDays(race.loss_days_median)} / {formatDays(race.loss_days_p90)}</strong></div></div>
    </div></div>
  )
}

function PropVerdict({ result, strategy, rule }: { result: PropResult; strategy?: StrategyListItem; rule?: Rule }) {
  const intervalRisk = result.interval_width > .25
  const sampleRisk = result.trading_days < 90
  const checks = [
    ['Evidence tier', strategy?.latest?.evidence_tier ?? 'UNKNOWN', strategy?.latest?.evidence_tier === 'HOLDOUT' ? 'PASS' : 'CAUTION'],
    ['Sample adequacy', `${result.trading_days} observed days`, sampleRisk ? 'CAUTION' : 'PASS'],
    ['Uncertainty', `${pct(result.interval_low)}–${pct(result.interval_high)} (${pct(result.interval_width)} wide)`, intervalRisk ? 'CAUTION' : 'PASS'],
    ['Ruin boundary', pct(result.risk_of_ruin), result.risk_of_ruin > .25 ? 'FAIL' : 'PASS'],
    ['Rule provenance', rule?.verified ? 'verified fixture' : 'unverified fixture', rule?.verified ? 'PASS' : 'CAUTION'],
  ]
  return (
    <div className="verdict-layout">
      <div className="verdict-grade"><span>PROP READINESS</span><strong>{result.pass_rate >= .6 && !sampleRisk && !intervalRisk ? 'B' : 'D'}</strong><p>A descriptive simulation grade, never a promotion gate.</p></div>
      <div className="gates">{checks.map(([name, finding, status], index) => <div className="gate" key={name}><span className="id">P{index}</span><b className={`status ${status === 'PASS' ? 'good' : status === 'FAIL' ? 'bad' : 'warn'}`}>{status}</b><div><span className="name">{name}</span><p>{finding}</p></div></div>)}</div>
    </div>
  )
}

const formatDays = (value: number | null) => value == null ? '--' : `${value.toFixed(1)}d`
