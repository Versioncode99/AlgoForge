import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronDown, Grid3x3, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, getJson, postJson } from '../api'
import { EquityChart, ReturnDrawdownChart, TargetReachChart, TerminalHistogram } from '../charts'
import { JobBar } from '../components/JobBar'
import { Empty, PanelHead, Stat } from '../components/ui'
import { useJob } from '../hooks/useJob'
import { money, pct } from '../lib'
import type { Job, MatrixCell, MatrixSkip, PropMatrix, PropResult, Rule } from '../types'

type Phase = 'ALL' | 'CHALLENGE' | 'FUNDED'
type SortKey = 'pass' | 'ruin' | 'name'


/* ── the full simulation for one pairing ───────────────────────────────────── */

/** What each label on a simulation actually commits to.
 *
 * The brief asks for the assumptions to be explicit, and a bare token like
 * `DAILY_SETTLEMENT_APPROXIMATION` is not explicit — it is a word that looks
 * like it was checked. Anything the backend sends that is not listed here is
 * still shown, verbatim, rather than dropped. */
const ASSUMPTION: Record<string, string> = {
  RESEARCH_ONLY: 'A simulation of modelled rules. It is not an evaluation and nothing here is an account.',
  BLOCK_BOOTSTRAP:
    "Accounts are drawn by resampling the strategy's own observed days in blocks of five, so losing streaks survive the resampling. No day is invented.",
  DAILY_SETTLEMENT_APPROXIMATION:
    'Rules are applied once per day against the settled balance. A real evaluation checks a trailing drawdown intraday, which fails some accounts this simulation passes.',
  UNVERIFIED_RULES:
    "This rule set is a sample fixture, not a scraped rulebook. Check the provider's current terms before reading anything into a number here.",
  REAL_DATA: "The daily series came from a backtest over a real market archive.",
  SYNTHETIC_DATA:
    'The daily series came from a backtest over generated bars. The account outcome describes the generator, not a market.',
  UNDECLARED_SOURCE: 'The caller did not state what the daily series is.',
}

function describe(label: string): string {
  if (ASSUMPTION[label]) return ASSUMPTION[label]
  if (label.startsWith('EVIDENCE_TIER:')) {
    const tier = label.slice('EVIDENCE_TIER:'.length)
    return tier === 'VALIDATION_OOS' || tier === 'TRUTH_OOS'
      ? `The trades are out of sample (${tier}).`
      : `The trades are ${tier.toLowerCase().replace(/_/g, ' ')}, which is exploration rather than out-of-sample evidence.`
  }
  return 'No description recorded for this label.'
}

/** The whole distribution for one strategy against one rule set.
 *
 * Every figure is read from the strategy's actual trade ledger — daily P&L
 * derived from real fills, resampled. The route refuses outright when the
 * ledger does not span enough trading days, so there is no path here that
 * reaches a pass rate from a series nobody traded. */
function PropReport({ result }: { result: PropResult }) {
  const days = result.boundary_race
  return (
    <div className="stack">
      <div className="headline-row">
        <Stat label="Passes" value={<span className="mono">{pct(result.pass_rate)}</span>}
          tone={result.pass_rate >= 0.5 ? 'good' : 'bad'}
          note={`${result.pass_count.toLocaleString()} of ${result.path_count.toLocaleString()} accounts`} />
        <Stat label="Fails" value={<span className="mono">{pct(days.loss_first_probability)}</span>}
          tone={days.loss_first_probability > 0.3 ? 'bad' : 'plain'}
          note={`${result.fail_count.toLocaleString()} hit the maximum loss first`} />
        <Stat label="Runs out of time" value={<span className="mono">{pct(days.timeout_probability)}</span>}
          note={`${result.timeout_count.toLocaleString()} neither passed nor failed`} />
        <Stat label="Expected payout" value={<span className="mono">{money(result.mean_payout)}</span>}
          note="mean across every account, passing and failing alike" />
        <Stat label="Expected terminal P&L"
          value={<span className="mono">{money(result.tail_risk.terminal_median)}</span>}
          tone={result.tail_risk.terminal_median >= 0 ? 'good' : 'bad'}
          note={`p05 ${money(result.tail_risk.terminal_p05)} · p95 ${money(result.tail_risk.terminal_p95)}`} />
      </div>

      <div className="headline-row">
        <Stat label="Days to pass"
          value={<span className="mono">{days.target_days_median ?? '—'}</span>}
          note={days.target_days_median === null
            ? 'no account reached the target'
            : `p10 ${days.target_days_p10} · p90 ${days.target_days_p90}`} />
        <Stat label="Days to fail"
          value={<span className="mono">{days.loss_days_median ?? '—'}</span>}
          note={days.loss_days_median === null
            ? 'no account hit the maximum loss'
            : `p10 ${days.loss_days_p10} · p90 ${days.loss_days_p90}`} />
        <Stat label="Risk of ruin" value={<span className="mono">{pct(result.risk_of_ruin)}</span>}
          tone={result.risk_of_ruin > 0.3 ? 'bad' : 'plain'} note="maximum loss before the target" />
        {/* Both arrive as positive loss magnitudes. Rendering 2,817 as "$2,817"
            in a risk column reads as money made, so the sign is put back. */}
        <Stat label="CVaR 95" value={<span className="mono">{money(-result.tail_risk.cvar_95)}</span>}
          tone={result.tail_risk.cvar_95 > 0 ? 'bad' : 'plain'}
          note={`VaR 95 ${money(-result.tail_risk.var_95)} — mean of the worst 5%`} />
        <Stat label="Observed days" value={<span className="mono">{result.trading_days}</span>}
          note={`interval width ${pct(result.interval_width)} — narrower needs more days, not more paths`} />
      </div>

      <div className="panel">
        {/* A sample, and it says so. The statistics above are over every path. */}
        <PanelHead title="Account equity, path by path"
          meta={`${Math.min(result.equity_paths.length, 48)} of ${result.path_count.toLocaleString()} drawn`} />
        <div className="panel-body"><EquityChart paths={result.equity_paths} start={result.rule.starting_balance} /></div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <PanelHead title="Probability of having passed by day N" meta="cumulative" />
          <div className="panel-body"><TargetReachChart points={result.target_reach_curve} /></div>
        </div>
        <div className="panel">
          <PanelHead title="Terminal P&L" meta="every account" />
          <div className="panel-body"><TerminalHistogram bins={result.terminal_histogram} /></div>
        </div>
      </div>

      <div className="panel">
        <PanelHead title="What each account gave back to get there" meta="terminal P&L against its own worst drawdown" />
        <div className="panel-body"><ReturnDrawdownChart points={result.return_drawdown_map} /></div>
      </div>

      {Object.keys(result.failure_reasons).length > 0 && (
        <div className="panel">
          <PanelHead title="Why the failures failed"
            meta={`${result.fail_count.toLocaleString()} accounts`} />
          <ul className="prop-reasons">
            {Object.entries(result.failure_reasons)
              .sort((a, b) => b[1] - a[1])
              .map(([reason, count]) => (
                <li key={reason}>
                  <span className="mono">{reason.replace(/_/g, ' ').toLowerCase()}</span>
                  <strong>{count.toLocaleString()}</strong>
                  <em>{pct(count / result.path_count)}</em>
                </li>
              ))}
          </ul>
        </div>
      )}

      <div className="panel">
        <PanelHead title="What this simulation assumes" meta={`${result.labels.length} stated`} />
        <ul className="prop-assumptions">
          {result.labels.map((label) => (
            <li key={label}>
              <span className="mono">{label}</span>
              <p>{describe(label)}</p>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

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
  // The matrix cell is a summary; the full distribution is a second request,
  // because reading every path for 399 strategies would make the grid unusable
  // to answer a question nobody asked yet.
  const [detail, setDetail] = useState<PropResult | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  const job = useJob()
  const drill = useMutation({
    mutationFn: (cell: MatrixCell) =>
      postJson<PropResult>(`/strategies/${cell.strategy_id}/prop`, {
        rule_id: cell.rule_id,
        paths: Math.max(250, paths),
      }),
    onSuccess: (r) => { setDetail(r); setDetailError(null) },
    onError: (e: Error) => { setDetail(null); setDetailError(e.message) },
  })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => getJson<Rule[]>('/prop/rules') })

  const run = useMutation({
    mutationFn: () => postJson<Job>('/prop/matrix', { paths, phase }),
    onSuccess: (started) => {
      setMatrix(null); setPicked(null); setError(null); setRefused(null)
      setDetail(null); setDetailError(null); job.start(started)
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
                              onClick={() => {
                                setPicked(cell)
                                if (detail && detail.rule.rule_id !== cell.rule_id) setDetail(null)
                                if (detail && detail.strategy_id !== cell.strategy_id) setDetail(null)
                                setDetailError(null)
                              }}
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
                <Stat label="CVaR 95" value={<span className="mono">{money(-picked.cvar_95)}</span>}
                  tone={picked.cvar_95 > 0 ? 'bad' : 'plain'}
                  note="mean of the worst 5% of outcomes — a loss, not a balance" />
                <Stat label="Observed days" value={<span className="mono">{picked.trading_days}</span>}
                  note={picked.verified ? 'rule marked verified' : 'UNVERIFIED rule fixture'} />
              </div>
              {!picked.verified && (
                <p className="warning">
                  This rule set is a sample fixture, not a scraped rulebook. Check the provider's
                  current terms before reading anything into the number above.
                </p>
              )}
              <div className="pad">
                <button
                  className="btn af-press"
                  disabled={drill.isPending}
                  onClick={() => drill.mutate(picked)}
                >
                  <ChevronDown />
                  {drill.isPending
                    ? 'Simulating…'
                    : detail
                      ? 'Re-run the full simulation'
                      : 'Open the full simulation'}
                </button>
                <span className="sub">
                  {' '}Equity paths, days to pass, the terminal distribution and every stated
                  assumption — over this strategy's own trade ledger.
                </span>
              </div>
              {detailError && (
                <p className="warning" role="status">{detailError}</p>
              )}
              {detail && <div className="pad"><PropReport result={detail} /></div>}
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
