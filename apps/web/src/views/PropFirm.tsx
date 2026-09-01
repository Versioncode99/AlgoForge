import { useMutation, useQuery } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson, postJson } from '../api'
import { EquityChart } from '../charts'
import { money, pct } from '../lib'
import type { PropResult, Rule, StrategyListItem } from '../types'

/** Prop Firm, driven by a real strategy's real daily P&L.
 *  Previously this scored a fixture regardless of what you had built, which is
 *  why it read as inert. */
export function PropFirmView() {
  const [strategyId, setStrategyId] = useState('')
  const [ruleId, setRuleId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PropResult | null>(null)

  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })

  const tested = strategies.data?.filter((s) => s.latest) ?? []
  useEffect(() => { if (!strategyId && tested.length) setStrategyId(tested[0].strategy_id) }, [tested, strategyId])
  useEffect(() => { if (!ruleId && rules.data?.length) setRuleId(rules.data[0].rule_id) }, [rules.data, ruleId])

  const run = useMutation({
    mutationFn: () => postJson<PropResult>(`/strategies/${strategyId}/prop`, { rule_id: ruleId, paths: 1000 }),
    onSuccess: (data) => { setResult(data); setError(null) },
    onError: (e: Error) => { setError(e.message); setResult(null) },
  })

  const rule = rules.data?.find((r) => r.rule_id === ruleId)
  const passPct = result ? result.pass_rate : 0
  const failPct = result ? result.fail_count / result.path_count : 0

  return (
    <section>
      <div className="section-title">
        <p>RULE-EXACT ACCOUNT SIMULATION</p>
        <h2>Would this strategy have passed an evaluation?</h2>
      </div>

      <div className="prop-controls">
        <label className="field">
          Strategy
          <select value={strategyId} onChange={(e) => { setStrategyId(e.target.value); setResult(null) }}>
            {tested.map((s) => (
              <option key={s.strategy_id} value={s.strategy_id}>
                {s.name} · {s.latest ? `${s.latest.net_pnl >= 0 ? '+' : ''}${s.latest.net_pnl.toFixed(0)}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Rule fixture
          <select value={ruleId} onChange={(e) => { setRuleId(e.target.value); setResult(null) }}>
            {rules.data?.map((r) => <option key={r.rule_id} value={r.rule_id}>{r.display_name}</option>)}
          </select>
        </label>
        <button className="btn primary" disabled={!strategyId || !ruleId || run.isPending} onClick={() => run.mutate()}>
          <ShieldCheck /> {run.isPending ? 'Simulating…' : 'Simulate 1,000 accounts'}
        </button>
      </div>

      {tested.length === 0 && (
        <div className="state">
          No backtested strategy yet. Run a backtest — or start the engine — then come back.
        </div>
      )}
      {error && <p className="warning bad">{error}</p>}

      {result && (
        <>
          <div className="split">
            <div className="donut-wrap">
              <div className="donut" style={{ '--pass-deg': `${passPct * 360}deg` } as React.CSSProperties}>
                <div>
                  <strong>{pct(passPct)}</strong>
                  <span>{result.path_count.toLocaleString()} sims</span>
                </div>
              </div>
              <ul className="donut-legend">
                <li><i className="dot good" />Pass {pct(passPct)}{result.avg_days_to_pass ? ` · avg ${result.avg_days_to_pass}d` : ''}</li>
                <li><i className="dot bad" />Fail {pct(failPct)}{result.avg_days_to_fail ? ` · avg ${result.avg_days_to_fail}d` : ''}</li>
                <li><i className="dot warn" />Timeout {pct(result.timeout_count / result.path_count)}</li>
              </ul>
            </div>
            <div className="metrics">
              <div>
                <span>Pass rate</span>
                <strong className={passPct > 0.3 ? 'good' : 'bad'}>{pct(passPct)}</strong>
                <small>95% CI {pct(result.interval_low)}–{pct(result.interval_high)}</small>
              </div>
              <div>
                <span>Trading days used</span>
                <strong>{result.trading_days}</strong>
                <small>real daily P&L from the backtest</small>
              </div>
              <div>
                <span>Median terminal</span>
                <strong>{money(result.median_terminal)}</strong>
                <small>start {money(result.rule.starting_balance)}</small>
              </div>
              <div>
                <span>Mean payout</span>
                <strong>{money(result.mean_payout)}</strong>
                <small>target {money(result.rule.profit_target)}</small>
              </div>
            </div>
          </div>

          <div className="grid-2">
            <div className="panel">
              <header><h2>Account equity paths · {result.equity_paths.length} shown</h2></header>
              <div className="panel-body"><EquityChart paths={result.equity_paths} /></div>
            </div>
            <div className="panel">
              <header><h2>Why accounts died</h2></header>
              <div className="panel-body">
                {Object.keys(result.failure_reasons).length === 0 && <p className="prose">No hard failures recorded.</p>}
                <table className="tbl">
                  <tbody>
                    {Object.entries(result.failure_reasons).map(([reason, count]) => (
                      <tr key={reason}>
                        <td>{reason.replace(/_/g, ' ')}</td>
                        <td className="num bad">{count}</td>
                      </tr>
                    ))}
                    <tr><td>timeout (never reached target)</td><td className="num warn">{result.timeout_count}</td></tr>
                  </tbody>
                </table>
                <p className="warning" style={{ marginTop: 12 }}>
                  The failure mix is the actionable part. Consistency failures call for a daily
                  profit cap; drawdown failures call for smaller size. Those changes usually cost
                  expectancy and raise pass rate.
                </p>
              </div>
            </div>
          </div>

          <p className="warning">
            {rule?.verified
              ? 'Rules verified from the firm’s own terms.'
              : 'UNVERIFIED RULE FIXTURE — check the provider’s current rulebook before relying on this.'}{' '}
            Pass rate is a simulation of modelled rules, not a real evaluation.
          </p>
        </>
      )}
    </section>
  )
}
