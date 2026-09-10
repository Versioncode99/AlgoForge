import { useQuery } from '@tanstack/react-query'
import { ArrowRight, BrainCircuit, ListTree } from 'lucide-react'
import { useMemo } from 'react'
import { getJson } from '../api'
import { PanelHead, TierPill } from '../components/ui'
import { money, signed, stamp } from '../lib'
import type {
  ActivityEvent, EngineStatus, MissionSnapshot, ResearchLoopStatus, ResearchMemoryPayload,
  ResearchOverview, StrategyListItem, Summary,
} from '../types'
import { EnginePanel } from './Engine'

/** Evidence rank. A strong in-sample number is not a better candidate than a
 *  weaker out-of-sample one, so the leaderboard sorts by tier first and only
 *  then by result. Ranking purely on net P&L would put the least trustworthy
 *  rows on top, which is the specific mistake this product exists to avoid. */
const TIER_RANK: Record<string, number> = {
  HOLDOUT: 4, TRUTH_OOS: 3, VALIDATION_OOS: 3, FORWARD: 2,
  DEVELOPMENT_IN_SAMPLE: 1, LEGACY_IN_SAMPLE: 1, SYNTHETIC: 0,
}
const rank = (tier?: string | null) => (tier ? TIER_RANK[tier] ?? 0 : -1)

/** Rank a row as the evidence it actually carries.
 *
 * A run computed under the old price-point units is not a weaker holdout, it is
 * a number in the wrong denomination. Left on its declared tier it sorted above
 * genuine holdout evidence, which is precisely the misreading the tier column
 * exists to prevent. It ranks below every current measurement until rerun. */
const evidenceRank = (item: StrategyListItem) =>
  item.latest?.calculation_version === 'legacy-price-points' ? -1 : rank(item.latest?.evidence_tier)

export function OverviewView({ onRoute }: { onRoute?: (id: string) => void }) {
  const engine = useQuery({
    queryKey: ['engine'],
    queryFn: () => getJson<EngineStatus>('/engine'),
    refetchInterval: (q) => (q.state.data?.running ? 2000 : 8000),
  })
  // Fast, cheap counters render immediately; the 400-record library below
  // resolves separately so the page is readable before it arrives.
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => getJson<Summary>('/summary') })
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyListItem[]>('/strategies'),
    staleTime: 0,
    refetchInterval: engine.data?.running ? 4000 : false,
  })
  const missions = useQuery({ queryKey: ['missions'], queryFn: () => getJson<MissionSnapshot>('/missions'), refetchInterval: 5000 })
  const researchLoop = useQuery({ queryKey: ['research-loop'], queryFn: () => getJson<ResearchLoopStatus>('/research-loop'), refetchInterval: 5000 })
  const research = useQuery({ queryKey: ['research-overview'], queryFn: () => getJson<ResearchOverview>('/research/overview') })
  const memory = useQuery({ queryKey: ['research-memory'], queryFn: () => getJson<ResearchMemoryPayload>('/memory?limit=6') })
  const events = useQuery({ queryKey: ['activity'], queryFn: () => getJson<ActivityEvent[]>('/activity?limit=14'), refetchInterval: 5000 })

  const list = strategies.data ?? []
  const tiers = useMemo(() => {
    const counts = { holdout: 0, oos: 0, insample: 0, untested: 0 }
    for (const item of list) {
      const tier = item.latest?.evidence_tier
      if (!item.latest) counts.untested++
      else if (tier === 'HOLDOUT') counts.holdout++
      else if (rank(tier) >= 2) counts.oos++
      else counts.insample++
    }
    return counts
  }, [list])

  const leaders = useMemo(() => [...list]
    .filter(item => item.latest)
    .sort((a, b) => evidenceRank(b) - evidenceRank(a)
      || (b.latest?.net_pnl ?? 0) - (a.latest?.net_pnl ?? 0))
    .slice(0, 10), [list])

  const activeMission = missions.data?.missions.find(m => m.id === missions.data?.current) ?? missions.data?.missions[0]
  const coverage = research.data?.matrix
  const loading = strategies.isPending

  return (
    <div className="stack">
      <div className="mission-strip">
        <section>
          <span>Active mission</span>
          <strong title={activeMission?.objective}>{activeMission?.objective ?? 'No mission running'}</strong>
          <small>{activeMission ? `${activeMission.status} · ${activeMission.steps.length} declared steps` : 'Define a bounded objective in Missions'}</small>
        </section>
        <section>
          <span>Continuous research</span>
          <strong>{researchLoop.data?.enabled ? (researchLoop.data.in_flight ? 'Scanning' : 'Armed') : 'Paused'}</strong>
          <small>{researchLoop.data ? `${researchLoop.data.cycles} cycles · ${researchLoop.data.sources_found} sources · ${researchLoop.data.downstream_tasks} hand-offs` : 'Status unavailable'}</small>
        </section>
        <section>
          <span>Dataset</span>
          <strong>{engine.data?.config.dataset ?? summary.data?.data_gate ?? '—'}</strong>
          <small>{summary.data ? `${summary.data.template_count} templates · ${summary.data.families.length} families` : 'Loading catalogue'}</small>
        </section>
      </div>

      <div className="counter-band" role="group" aria-label="Research activity">
        <Counter label="Strategies" value={summary.data?.strategy_count} note="written to disk" onClick={() => onRoute?.('strategies')} />
        <Counter label="Backtests" value={summary.data?.backtest_count} note="runs recorded" />
        <Counter label="Holdout" value={tiers.holdout} note="strongest evidence" tone={tiers.holdout ? 'good' : undefined} pending={loading} />
        <Counter label="Out of sample" value={tiers.oos} note="validated tier" tone={tiers.oos ? 'good' : undefined} pending={loading} />
        <Counter label="In sample" value={tiers.insample} note="not yet evidence" tone="warn" pending={loading} />
        <Counter label="Never run" value={tiers.untested} note="no measurement" pending={loading} />
        <Counter label="Constraints" value={memory.data?.total} note="regions ruled out" onClick={() => onRoute?.('memory')} />
        <Counter label="Engine errors" value={engine.data?.engine_errors} note="this session" tone={engine.data?.engine_errors ? 'bad' : undefined} />
      </div>

      <EnginePanel />

      <div className="overview-split">
        <div className="panel">
          <PanelHead title="Strongest candidates" meta="ranked by evidence tier, then result">
            <button className="text-action" onClick={() => onRoute?.('strategies')}>
              All {summary.data?.strategy_count ?? ''} strategies <ArrowRight aria-hidden="true" />
            </button>
          </PanelHead>
          {loading ? <SkeletonRows /> : leaders.length === 0 ? (
            <div className="panel-body evidence-absent">
              <ListTree aria-hidden="true" />
              <div>
                <strong>No measured candidates</strong>
                <p>Every strategy on disk is still unrun. Open Strategies, choose a data range and run one, or start the engine to search continuously.</p>
              </div>
            </div>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead><tr>
                  <th>Strategy</th><th>Family</th><th>Evidence</th>
                  <th className="num">Net</th><th className="num">Trades</th>
                  <th className="num">Win rate</th><th className="num">Max DD</th><th>Last run</th>
                </tr></thead>
                <tbody>
                  {leaders.map(s => (
                    <tr key={s.strategy_id}>
                      <td>{s.name}</td>
                      <td className="sub">{s.family}</td>
                      <td>{s.latest?.calculation_version === 'legacy-price-points'
                        ? <span className="warn">Rerun · old units</span>
                        : <TierPill tier={s.latest?.evidence_tier} />}</td>
                      <td className={`mono num ${(s.latest?.net_pnl ?? 0) >= 0 ? 'good' : 'bad'}`}>{signed(s.latest?.net_pnl ?? 0)}</td>
                      <td className="mono num sub">{s.latest?.trade_count ?? 0}</td>
                      <td className="mono num sub">{s.latest ? `${(s.latest.win_rate * 100).toFixed(1)}%` : '—'}</td>
                      <td className="mono num sub">{money(s.latest?.max_drawdown ?? 0)}</td>
                      <td className="mono sub">{stamp(s.latest?.finished_at ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="warning">
            A net figure is only as good as the tier beside it. In-sample and out-of-sample
            numbers are not comparable, which is why the ranking reads the tier first.
          </p>
        </div>

        <div className="panel">
          <PanelHead title="Research activity" meta="most recent first" />
          {!events.data?.length ? (
            <div className="panel-body evidence-absent">
              <ListTree aria-hidden="true" />
              <div><strong>Nothing has happened yet</strong><p>Events appear as the engine invents, backtests and judges candidates.</p></div>
            </div>
          ) : (
            <ol className="research-timeline">
              {events.data.map((event, i) => (
                <li key={`${event.ts}-${i}`} data-level={event.level}>
                  <time className="mono">{event.ts.slice(11, 19)}</time>
                  <div><strong>{event.stage}</strong><p>{event.message}</p></div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <div className="overview-split">
        <div className="panel">
          <PanelHead title="Research memory" meta={memory.data ? `${memory.data.total} constraints` : 'loading'}>
            <button className="text-action" onClick={() => onRoute?.('memory')}>Open memory <ArrowRight aria-hidden="true" /></button>
          </PanelHead>
          {!memory.data?.constraints?.length ? (
            <div className="panel-body evidence-absent">
              <BrainCircuit aria-hidden="true" />
              <div>
                <strong>Nothing ruled out yet</strong>
                <p>When a judged candidate fails for a reason that generalises, the region around it is pruned and recorded here so no compute is spent on it twice.</p>
              </div>
            </div>
          ) : (
            <ul className="constraint-list">
              {memory.data.constraints.slice(0, 6).map((c, i) => (
                <li key={`${c.template}-${i}`}>
                  <span className="status-badge is-failed">{c.failure_class}</span>
                  <div><strong className="mono">{c.template}</strong><p>{c.reason}</p></div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {coverage && (
          <div className="panel">
            <PanelHead title="Family × market coverage" meta="observed expectancy, not a verdict" />
            <div className="coverage-matrix" style={{ gridTemplateColumns: `minmax(120px, 1fr) repeat(${coverage.markets.length}, minmax(74px, .7fr))` }}>
              <span />{coverage.markets.map(m => <strong key={m}>{m}</strong>)}
              {coverage.rows.flatMap(row => [
                <b key={`${row.key}-name`}>{row.name}</b>,
                ...row.cells.map(cell => (
                  <span key={`${row.key}-${cell.market}`} data-status={cell.status} title={`${row.name} / ${cell.market}: ${cell.status}`}>
                    <i style={{ opacity: cell.expectancy == null ? .08 : Math.min(.9, .2 + Math.abs(cell.expectancy) / 250) }} />
                    {cell.expectancy == null ? cell.status.replaceAll('_', ' ') : signed(cell.expectancy)}
                  </span>
                )),
              ])}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Counter({ label, value, note, tone, pending, onClick }: {
  label: string; value?: number; note: string
  tone?: 'good' | 'bad' | 'warn'; pending?: boolean; onClick?: () => void
}) {
  const body = <>
    <span className="counter-label">{label}</span>
    <b className="counter-value mono" data-tone={tone}>{pending || value == null ? <i className="skeleton-num" /> : value.toLocaleString()}</b>
    <small className="counter-note">{note}</small>
  </>
  return onClick
    ? <button type="button" className="counter is-link" onClick={onClick}>{body}</button>
    : <div className="counter">{body}</div>
}

function SkeletonRows() {
  return (
    <div className="skeleton-rows" role="status" aria-label="Loading strategy library">
      {Array.from({ length: 6 }, (_, i) => <span key={i} className="skeleton-row" style={{ animationDelay: `${i * 60}ms` }} />)}
    </div>
  )
}
