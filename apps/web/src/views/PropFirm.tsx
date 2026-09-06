import { useMutation, useQuery } from '@tanstack/react-query'
import { Grid3x3, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, getJson, postJson } from '../api'
import { JobBar } from '../components/JobBar'
import { Empty, PanelHead, Stat } from '../components/ui'
import { useJob } from '../hooks/useJob'
import { money, pct } from '../lib'
import type { Job, MatrixCell, MatrixSkip, PropMatrix, Rule } from '../types'

type Phase = 'ALL' | 'CHALLENGE' | 'FUNDED'
type SortKey = 'pass' | 'ruin' | 'name'

/** Every backtested strategy against every rule set, in one grid.
 *
 * One strategy against one rule answers almost nothing. Prop rule sets differ
 * in ways that reverse the ranking — a no-daily-loss-limit evaluation and a
 * trailing-drawdown one reward completely different P&L shapes on the same
 * strategy. The comparison is the product, so the grid is the page.
 */
export function PropFirmView() {
  const [phase, setPhase] = useState<Phase>('ALL')
  const [paths, setPaths] = useState(1000)
  const [sort, setSort] = useState<SortKey>('pass')
  const [matrix, setMatrix] = useState<PropMatrix | null>(null)
  const [picked, setPicked] = useState<MatrixCell | null>(null)
  const [error, setError] = useState<string | null>(null)
  // A refusal is a result too: it names which strategies were skipped and
  // what would lift the block. Throwing that away for a one-line banner is
  // how a useful answer becomes a dead end.
  const [refused, setRefused] = useState<MatrixSkip[] | null>(null)

  const job = useJob()
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })

  const run = useMutation({
    mutationFn: () => postJson<Job>('/prop/matrix', { paths, phase }),
    onSuccess: (started) => {
      setMatrix(null); setPicked(null); setError(null); setRefused(null); job.start(started)
    },
    onError: (e: Error) => {
      setError(e.message)
      const detail = e instanceof ApiError ? e.detail<{ skipped?: MatrixSkip[] }>() : undefined
      setRefused(detail?.skipped ?? null)
    },
  })

  useEffect(() => {
    if (job.job?.status === 'DONE' && job.job.result) setMatrix(job.job.result as PropMatrix)
    if (job.job?.status === 'FAILED') setError(job.job.error ?? 'Simulation failed.')
  }, [job.job?.status, job.job?.result, job.job?.error])

  const strategies = useMemo(() => {
    if (!matrix) return []
    const best = new Map<string, number>()
    for (const cell of matrix.cells) {
      best.set(cell.strategy_id, Math.max(best.get(cell.strategy_id) ?? 0, cell.pass_rate))
    }
    const rows = [...matrix.strategies]
    if (sort === 'pass') rows.sort((a, b) => (best.get(b.strategy_id) ?? 0) - (best.get(a.strategy_id) ?? 0))
    if (sort === 'name') rows.sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'ruin') {
      const ruin = new Map<string, number>()
      for (const cell of matrix.cells) {
        ruin.set(cell.strategy_id, Math.max(ruin.get(cell.strategy_id) ?? 0, cell.risk_of_ruin))
      }
      rows.sort((a, b) => (ruin.get(a.strategy_id) ?? 0) - (ruin.get(b.strategy_id) ?? 0))
    }
    return rows
  }, [matrix, sort])

  const lookup = useMemo(() => {
    const map = new Map<string, MatrixCell>()
    for (const cell of matrix?.cells ?? []) map.set(`${cell.strategy_id}::${cell.rule_id}`, cell)
    return map
  }, [matrix])

  const unverified = (rules.data ?? []).filter((r) => !r.verified).length

  return (
    <section className="prop-workspace stack">
      <div className="control-strip af-panel-in">
        <div className="ctl range-ctl">
          <span>Phase</span>
          <div className="range-buttons" role="group" aria-label="Account phase">
            {(['ALL', 'CHALLENGE', 'FUNDED'] as Phase[]).map((p) => (
              <button key={p} className={phase === p ? 'active af-press' : 'af-press'}
                disabled={job.active} onClick={() => setPhase(p)}>{p.toLowerCase()}</button>
            ))}
          </div>
        </div>
        <label className="ctl">
          <span>Paths per cell</span>
          <select aria-label="Paths per cell" value={paths} disabled={job.active} onChange={(e) => setPaths(Number(e.target.value))}>
            <option value={250}>250</option>
            <option value={500}>500</option>
            <option value={1000}>1,000</option>
            <option value={2500}>2,500</option>
            <option value={5000}>5,000</option>
          </select>
        </label>
        <button className="btn primary af-press" disabled={job.active} onClick={() => run.mutate()}>
          <Grid3x3 />{job.active ? 'Simulating…' : 'Run the matrix'}
        </button>
        <div className="ctl-readout">
          <span className="sub">
            Every cell is a full block-bootstrap simulation over the strategy's observed daily P&L.
          </span>
        </div>
      </div>

      {job.job && <JobBar job={job.job} onCancel={job.cancel} onDismiss={job.clear} />}

      {error && (
        <div className="banner is-err af-panel-in" role="status">
          <TriangleAlert /><span>{error}</span>
          <button onClick={() => setError(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      {refused && refused.length > 0 && (
        <div className="panel af-panel-in">
          <PanelHead title="Nothing could be simulated" meta={`${refused.length} skipped`} />
          <p className="pad sub">
            Every strategy was skipped. A prop evaluation runs for weeks, so a backtest has to
            contain at least 30 distinct trading days before an account simulation means anything.
          </p>
          <SkipTable rows={refused} />
        </div>
      )}

      {!matrix && !job.active && !refused && (
        <Empty
          title="No matrix yet"
          detail="Press Run the matrix above. It simulates every strategy that has a long enough backtest against every rule set you have, and lists the ones it skipped with the reason rather than dropping them silently."
        />
      )}

      {matrix && (
        <>
          <div className="headline-row af-panel-in">
            <Stat label="Cells simulated" value={<span className="mono">{matrix.cells.length}</span>}
              note={`${matrix.strategies.length} strategies × ${matrix.rules.length} rules`} />
            <Stat label="Best pass rate"
              value={<span className="mono">{pct(Math.max(...matrix.cells.map((c) => c.pass_rate), 0))}</span>}
              tone={Math.max(...matrix.cells.map((c) => c.pass_rate), 0) > 0.5 ? 'good' : 'bad'}
              note="the single most favourable pairing" />
            <Stat label="Paths per cell" value={<span className="mono">{matrix.paths.toLocaleString()}</span>}
              note="block bootstrap, fixed seed" />
            <Stat label="Skipped" value={<span className="mono">{matrix.skipped.length}</span>}
              tone={matrix.skipped.length ? 'unknown' : 'plain'} note="listed below with reasons" />
            <Stat label="Rule provenance" value={<span className="mono">{unverified} unverified</span>}
              tone={unverified ? 'unknown' : 'good'} note="sample fixtures, not scraped rulebooks" />
          </div>

          <div className="panel af-panel-in">
            <PanelHead title="Pass rate by strategy and rule" meta="click a cell for the detail">
              <div className="side-switch" role="group" aria-label="Sort">
                {(['pass', 'ruin', 'name'] as SortKey[]).map((s) => (
                  <button key={s} className={sort === s ? 'active af-press' : 'af-press'} onClick={() => setSort(s)}>{s}</button>
                ))}
              </div>
            </PanelHead>
            <div className="table-scroll">
              <table className="matrix-table">
                <thead>
                  <tr>
                    <th className="sticky-col">Strategy</th>
                    <th className="num">Days</th>
                    {matrix.rules.map((r) => (
                      <th key={r.rule_id} title={`${r.provider} · ${r.phase}`}>
                        <span className="rule-head">{r.display_name.replace(' (research fixture)', '')}</span>
                        <em>{r.phase.toLowerCase()}</em>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s, i) => (
                    <tr key={s.strategy_id} className="af-row-in" style={{ animationDelay: `${Math.min(i, 20) * 12}ms` }}>
                      <th scope="row" className="sticky-col">{s.name}</th>
                      <td className="mono num sub">{s.trading_days}</td>
                      {matrix.rules.map((r) => {
                        const cell = lookup.get(`${s.strategy_id}::${r.rule_id}`)
                        if (!cell) return <td key={r.rule_id} className="cell-empty">—</td>
                        return (
                          <td key={r.rule_id} className="matrix-cell">
                            <button
                              className={picked === cell ? 'cell active af-press' : 'cell af-press'}
                              style={{ '--fill': `${Math.round(cell.pass_rate * 100)}%` } as React.CSSProperties}
                              data-tone={cell.pass_rate >= 0.6 ? 'good' : cell.pass_rate >= 0.25 ? 'mid' : 'bad'}
                              onClick={() => setPicked(cell)}
                              title={`${pct(cell.pass_rate)} pass · ${pct(cell.risk_of_ruin)} ruin`}
                            >
                              <i className="cell-fill af-wipe-in" />
                              <span className="mono">{pct(cell.pass_rate)}</span>
                            </button>
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {picked && (
            <div className="panel af-panel-in">
              <PanelHead title={`${picked.strategy_name} · ${picked.rule_name}`} meta={`${picked.provider} · ${picked.phase.toLowerCase()}`} />
              <div className="headline-row">
                <Stat label="Pass rate" value={<span className="mono">{pct(picked.pass_rate)}</span>}
                  tone={picked.pass_rate >= 0.5 ? 'good' : 'bad'}
                  note={`interval [${pct(picked.interval_low)}, ${pct(picked.interval_high)}] — widens with a short record`} />
                <Stat label="Risk of ruin" value={<span className="mono">{pct(picked.risk_of_ruin)}</span>}
                  tone={picked.risk_of_ruin > 0.3 ? 'bad' : 'plain'} note="hit the maximum loss before the target" />
                <Stat label="Median terminal" value={<span className="mono">{money(picked.median_terminal)}</span>}
                  note="middle outcome across all paths" />
                <Stat label="CVaR 95" value={<span className="mono">{money(picked.cvar_95)}</span>}
                  note="average of the worst 5% of outcomes" />
                <Stat label="Observed days" value={<span className="mono">{picked.trading_days}</span>}
                  note={picked.verified ? 'rule marked verified' : 'UNVERIFIED rule fixture'} />
              </div>
              {!picked.verified && (
                <p className="warning">
                  This rule set is a sample fixture, not a scraped rulebook. Check the provider's
                  current terms before reading anything into the number above.
                </p>
              )}
            </div>
          )}

          {matrix.skipped.length > 0 && (
            <div className="panel af-panel-in">
              <PanelHead title="Skipped" meta={`${matrix.skipped.length} strategies`} />
              <SkipTable rows={matrix.skipped} />
            </div>
          )}
        </>
      )}
    </section>
  )
}

/** Why a strategy is not in the grid, and what would put it there. */
function SkipTable({ rows }: { rows: MatrixSkip[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Strategy</th><th>Reason</th><th className="num">Days</th><th>What would fix it</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 60).map((s, i) => (
            <tr key={s.strategy_id} className="af-row-in" style={{ animationDelay: `${Math.min(i, 20) * 8}ms` }}>
              <td>{s.name}</td>
              <td className="mono sub">{s.reason}</td>
              <td className="mono num sub">{s.days_observed ?? '—'}</td>
              <td className="sub">{s.detail ?? 'Run a backtest first.'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 60 && <p className="pad sub">Showing the first 60 of {rows.length}.</p>}
    </div>
  )
}
