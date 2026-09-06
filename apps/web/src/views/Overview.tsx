import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { getJson } from '../api'
import { PanelHead, TierPill } from '../components/ui'
import { money, signed, stamp } from '../lib'
import type { EngineStatus, StrategyListItem } from '../types'
import { EnginePanel } from './Engine'

type Sort = 'net' | 'recent' | 'name'

/** The engine control, then the library it is working on.
 *
 * The engine counters alone left most of the page empty, and empty space with
 * nothing in it is a decision nobody made. What belongs underneath is the thing
 * the counters are counting: which strategies exist, what evidence each one
 * carries, and what it last did. That is also the fastest route to the work —
 * the rows are the shortlist you act on.
 */
export function OverviewView({ onAgents }: { onAgents?: () => void }) {
  const [sort, setSort] = useState<Sort>('net')
  const engine = useQuery({
    queryKey: ['engine'],
    queryFn: () => getJson<EngineStatus>('/engine'),
    refetchInterval: (q) => (q.state.data?.running ? 2000 : 8000),
  })
  // The global staleTime is 30s, which is right for a page you read and wrong
  // for one you watch: with the engine running the library changes every few
  // seconds, and a stale list said "nothing has been backtested" while the
  // counters above it climbed.
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyListItem[]>('/strategies'),
    staleTime: 0,
    refetchInterval: engine.data?.running ? 4000 : false,
  })

  const rows = useMemo(() => {
    const list = [...(strategies.data ?? [])].filter((s) => s.latest)
    if (sort === 'net') list.sort((a, b) => (b.latest?.net_pnl ?? 0) - (a.latest?.net_pnl ?? 0))
    if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'recent') {
      list.sort((a, b) => (b.latest?.finished_at ?? '').localeCompare(a.latest?.finished_at ?? ''))
    }
    return list
  }, [strategies.data, sort])

  const untested = (strategies.data ?? []).length - rows.length

  return (
    <div className="stack">
      <div className="overview-welcome">
        <div><p className="command-kicker">ALGOFORGE / RESEARCH WORKSTATION</p><h2>A clearer path<br />from signal to strategy.</h2><p>Research with a mechanism. Experiments with a memory. Every result traceable to its evidence.</p>
          <button className="btn primary" onClick={onAgents}>Open Agent Command <span aria-hidden="true">↗</span></button></div>
        <div className="overview-orbit" aria-hidden="true"><div /><div /><div /><span>08<small>SPECIALISTS</small></span><i /><i /><i /><i /></div>
      </div>
      <EnginePanel />

      <div className="panel af-panel-in">
        <PanelHead
          title="Library at a glance"
          meta={`${rows.length} with a result · ${untested} never run`}
        >
          <div className="side-switch" role="group" aria-label="Sort library">
            {(['net', 'recent', 'name'] as Sort[]).map((s) => (
              <button
                key={s}
                className={sort === s ? 'active af-press' : 'af-press'}
                onClick={() => setSort(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </PanelHead>

        {rows.length === 0 ? (
          <p className="pad sub">
            Nothing has been backtested yet. Open Strategies, pick a range, and run one.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Family</th>
                  <th>Evidence</th>
                  <th className="num">Net</th>
                  <th className="num">Trades</th>
                  <th className="num">Win rate</th>
                  <th className="num">Max DD</th>
                  <th className="num">Runs</th>
                  <th>Last run</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s, i) => (
                  <tr
                    key={s.strategy_id}
                    className="af-row-in"
                    style={{ animationDelay: `${Math.min(i, 22) * 10}ms` }}
                  >
                    <td>{s.name}</td>
                    <td className="sub">{s.family}</td>
                    <td>{s.latest?.calculation_version === 'legacy-price-points' ? <span className="warn">Rerun · old units</span> : <TierPill tier={s.latest?.evidence_tier} />}</td>
                    <td className={(s.latest?.net_pnl ?? 0) >= 0 ? 'mono num good' : 'mono num bad'}>
                      {signed(s.latest?.net_pnl ?? 0)}
                    </td>
                    <td className="mono num sub">{s.latest?.trade_count ?? 0}</td>
                    <td className="mono num sub">
                      {s.latest ? `${(s.latest.win_rate * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="mono num sub">{money(s.latest?.max_drawdown ?? 0)}</td>
                    <td className="mono num sub">{s.backtest_count}</td>
                    <td className="mono sub">{stamp(s.latest?.finished_at ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="warning">
          Net figures are whatever tier the last run produced. An in-sample number and an
          out-of-sample number are not comparable, which is why the evidence column sits beside
          them rather than under a footnote.
        </p>
      </div>
    </div>
  )
}
