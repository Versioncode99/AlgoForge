import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, FileCode2, FlaskConical, Gavel, Play, Plus, ShieldCheck, Trash2, Waves } from 'lucide-react'
import { useEffect, useState } from 'react'
import { deleteJson, getJson, postJson, putJson } from '../api'
import { CurveChart, SweepChart } from '../charts'
import { clock, money, pct, shortHash, signed } from '../lib'
import type {
  BacktestResult, StrategyDetail, StrategyListItem, SweepResult, TemplateInfo,
  ValidationEvidence, Verdict,
} from '../types'

type Pane = 'code' | 'hypothesis' | 'results' | 'trades'

export function StrategiesView() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [pane, setPane] = useState<Pane>('code')
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const list = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => getJson<TemplateInfo[]>('/templates') })

  useEffect(() => {
    if (!selected && list.data?.length) setSelected(list.data[0].strategy_id)
  }, [list.data, selected])

  const detail = useQuery({
    queryKey: ['strategy', selected],
    enabled: !!selected,
    queryFn: () => getJson<StrategyDetail>(`/strategies/${selected}`),
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['strategies'] })
    qc.invalidateQueries({ queryKey: ['strategy', selected] })
    qc.invalidateQueries({ queryKey: ['activity'] })
    qc.invalidateQueries({ queryKey: ['summary'] })
  }
  const fail = (e: Error) => setBanner({ kind: 'err', text: e.message })

  const create = useMutation({
    mutationFn: (template: string) => postJson<{ strategy_id: string }>('/strategies', { template }),
    onSuccess: (data) => {
      setSelected(data.strategy_id)
      setBanner({ kind: 'ok', text: `Created ${data.strategy_id} — files written to disk.` })
      refresh()
    },
    onError: fail,
  })

  const [result, setResult] = useState<BacktestResult | null>(null)
  const [verdict, setVerdict] = useState<Verdict | null>(null)
  const [sweep, setSweep] = useState<SweepResult | null>(null)
  const [evidence, setEvidence] = useState<ValidationEvidence | null>(null)

  const backtest = useMutation({
    // No bar_count: 4,000 one-minute bars is under three sessions, which can
    // never reach the 30 distinct trading days the prop gate needs. The
    // server default is sized against that requirement.
    mutationFn: () => postJson<BacktestResult>(`/strategies/${selected}/backtest`, {}),
    onSuccess: (data) => {
      setResult(data); setVerdict(null); setPane('results')
      setBanner({ kind: 'ok', text: `${data.trades.length} trades · net ${signed(data.net_pnl)}` })
      refresh()
    },
    onError: fail,
  })

  const judge = useMutation({
    mutationFn: () => postJson<Verdict>(`/strategies/${selected}/judge`),
    onSuccess: (data) => {
      setVerdict(data); setPane('results')
      setBanner({ kind: data.decision === 'PASS' ? 'ok' : 'err', text: `Judge: ${data.decision} (grade ${data.grade})` })
      refresh()
    },
    onError: fail,
  })

  const validate = useMutation({
    mutationFn: () => postJson<ValidationEvidence>(`/strategies/${selected}/validate`, {}),
    onSuccess: (data) => {
      setEvidence(data); setVerdict(null); setPane('results')
      setBanner({
        kind: data.probability_of_overfitting < 0.5 ? 'ok' : 'err',
        text: `Validation: PBO ${pct(data.probability_of_overfitting)} · `
          + `walk-forward efficiency ${data.walk_forward_efficiency.toFixed(2)} · `
          + `${data.trial_count} trials`,
      })
      refresh()
    },
    onError: fail,
  })

  const runSweep = useMutation({
    mutationFn: (parameter: string) => postJson<SweepResult>(`/strategies/${selected}/sweep`, { parameter }),
    onSuccess: (data) => { setSweep(data); setPane('results'); refresh() },
    onError: fail,
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteJson(`/strategies/${id}`),
    onSuccess: () => { setSelected(null); setResult(null); setVerdict(null); setSweep(null); setEvidence(null); refresh() },
    onError: fail,
  })

  const saveSource = useMutation({
    mutationFn: (source: string) => putJson(`/strategies/${selected}/source`, { source }),
    onSuccess: () => { setBanner({ kind: 'ok', text: 'Source saved — guard checks passed.' }); refresh() },
    onError: fail,
  })

  const busy = backtest.isPending || judge.isPending || runSweep.isPending || validate.isPending
  const spec = detail.data?.spec

  return (
    <section className="strategies">
      <div className="section-title">
        <p>STRATEGY LIBRARY</p>
        <h2>The code this system runs, on disk and editable</h2>
      </div>

      {banner && (
        <div className={banner.kind === 'ok' ? 'banner is-ok' : 'banner is-err'} role="status">
          {banner.kind === 'ok' ? <Check /> : <AlertTriangle />}
          <span>{banner.text}</span>
          <button onClick={() => setBanner(null)} aria-label="Dismiss message">×</button>
        </div>
      )}

      <div className="strat-layout">
        <aside className="strat-list">
          <header>
            <span>{list.data?.length ?? 0} STRATEGIES</span>
          </header>
          <div className="strat-items">
            {list.data?.map((item) => (
              <button
                key={item.strategy_id}
                className={item.strategy_id === selected ? 'strat-item active' : 'strat-item'}
                onClick={() => { setSelected(item.strategy_id); setResult(null); setVerdict(null); setSweep(null); setEvidence(null) }}
              >
                <b>{item.name}</b>
                <small>{item.family} · {item.symbol}</small>
                <em className={item.latest ? (item.latest.net_pnl >= 0 ? 'good' : 'bad') : ''}>
                  {item.latest ? `${signed(item.latest.net_pnl)} · ${item.latest.trade_count}t` : 'never run'}
                </em>
              </button>
            ))}
            {list.data?.length === 0 && <p className="empty">No strategies yet. Create one below.</p>}
          </div>
          <footer>
            <span className="new-label">NEW FROM TEMPLATE</span>
            {templates.data?.map((t) => (
              <button key={t.key} className="tmpl" disabled={create.isPending} onClick={() => create.mutate(t.key)}>
                <Plus />
                <span>{t.name}</span>
                <small>{t.line_count} lines</small>
              </button>
            ))}
          </footer>
        </aside>

        <div className="strat-detail">
          {!spec && <div className="state">Select a strategy.</div>}
          {spec && (
            <>
              <div className="strat-head">
                <div>
                  <p className="eyebrow">{spec.family.toUpperCase()} · {spec.symbol} · {spec.bar_spec}</p>
                  <h3>{spec.name}</h3>
                  <small className="path">{detail.data?.path}</small>
                </div>
                <div className="actions">
                  <button className="btn primary" disabled={busy} onClick={() => backtest.mutate()}>
                    <Play />{backtest.isPending ? 'Running…' : 'Run backtest'}
                  </button>
                  <button className="btn" disabled={busy} onClick={() => validate.mutate()}
                    title="Walk-forward, CSCV and CPCV. The judge cannot pass a strategy without this.">
                    <ShieldCheck />{validate.isPending ? 'Validating…' : 'Validate'}
                  </button>
                  <button className="btn" disabled={busy} onClick={() => judge.mutate()}>
                    <Gavel />{judge.isPending ? 'Judging…' : 'Judge'}
                  </button>
                  <button className="btn" disabled={busy} onClick={() => runSweep.mutate(spec.parameters[0].name)}>
                    <Waves />{runSweep.isPending ? 'Sweeping…' : `Sweep ${spec.parameters[0].name}`}
                  </button>
                  <button className="btn danger" aria-label="Delete strategy" onClick={() => remove.mutate(spec.strategy_id)}>
                    <Trash2 />
                  </button>
                </div>
              </div>

              <nav className="panes">
                {(['code', 'hypothesis', 'results', 'trades'] as Pane[]).map((p) => (
                  <button key={p} className={pane === p ? 'active' : ''} onClick={() => setPane(p)}>
                    {p === 'code' && <FileCode2 />}{p === 'results' && <FlaskConical />}
                    {p}
                  </button>
                ))}
              </nav>

              {pane === 'code' && (
                <CodePane
                  source={detail.data?.source ?? ''}
                  tests={detail.data?.tests ?? ''}
                  codeHash={detail.data?.code_hash ?? ''}
                  onSave={(s) => saveSource.mutate(s)}
                  saving={saveSource.isPending}
                />
              )}

              {pane === 'hypothesis' && (
                <div className="stack">
                  <div className="panel">
                    <header><h2>Hypothesis</h2></header>
                    <div className="panel-body prose">{spec.hypothesis}</div>
                  </div>
                  <div className="panel">
                    <header><h2>Falsifiable prediction</h2></header>
                    <div className="panel-body prose">{spec.falsifiable_prediction}</div>
                  </div>
                  <div className="panel">
                    <header><h2>Parameters</h2></header>
                    <div className="panel-body">
                      <table className="tbl">
                        <thead><tr><th>Name</th><th className="num">Default</th><th className="num">Range</th><th>Meaning</th></tr></thead>
                        <tbody>
                          {spec.parameters.map((p) => (
                            <tr key={p.name}>
                              <td>{p.name}</td>
                              <td className="num">{p.default}</td>
                              <td className="num">{p.low} – {p.high} / {p.step}</td>
                              <td>{p.description}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <p className="warning">
                    Costs applied per round trip: {money(spec.commission_per_side * 2)} commission plus{' '}
                    {spec.slippage_ticks} tick slippage per side at {money(spec.tick_value)} per tick.
                  </p>
                </div>
              )}

              {pane === 'results' && (
                <ResultsPane result={result} verdict={verdict} evidence={evidence} sweep={sweep} history={detail.data?.backtests ?? []} />
              )}

              {pane === 'trades' && <TradesPane result={result} />}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function CodePane({ source, tests, codeHash, onSave, saving }: {
  source: string; tests: string; codeHash: string
  onSave: (s: string) => void; saving: boolean
}) {
  const [draft, setDraft] = useState(source)
  const [showTests, setShowTests] = useState(false)
  useEffect(() => setDraft(source), [source])
  const dirty = draft !== source

  return (
    <div className="stack">
      <div className="panel">
        <header>
          <h2>{showTests ? 'test_strategy.py' : 'strategy.py'} · {shortHash(codeHash)}</h2>
          <div className="panel-actions">
            <button className="btn tiny" onClick={() => setShowTests(!showTests)}>
              {showTests ? 'Show strategy' : 'Show tests'}
            </button>
            {!showTests && (
              <button className="btn tiny primary" disabled={!dirty || saving} onClick={() => onSave(draft)}>
                {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
              </button>
            )}
          </div>
        </header>
        {showTests ? (
          <pre className="code readonly">{tests || 'No tests written for this strategy.'}</pre>
        ) : (
          <textarea
            className="code editor"
            value={draft}
            spellCheck={false}
            aria-label="Strategy source code"
            onChange={(e) => setDraft(e.target.value)}
          />
        )}
      </div>
      <p className="warning">
        Edits pass the same static guard as generated code: no filesystem, network, subprocess or
        dynamic execution, and both signal functions must exist. Rejected code is never run.
      </p>
    </div>
  )
}

function ResultsPane({ result, verdict, evidence, sweep, history }: {
  result: BacktestResult | null; verdict: Verdict | null; evidence: ValidationEvidence | null
  sweep: SweepResult | null; history: { backtest_id: string; net_pnl: number; trade_count: number; finished_at: string }[]
}) {
  if (!result && !sweep && !evidence && history.length === 0) {
    return <div className="state">No results yet. Run a backtest to produce them.</div>
  }
  return (
    <div className="stack">
      {result && (
        <>
          <div className="metrics">
            <div><span>Net P&L</span><strong className={result.net_pnl >= 0 ? 'good' : 'bad'}>{money(result.net_pnl)}</strong><small>after {money(result.total_costs)} costs</small></div>
            <div><span>Trades</span><strong>{result.trades.length}</strong><small>{result.bar_count.toLocaleString()} bars</small></div>
            <div><span>Win rate</span><strong>{pct(result.win_rate)}</strong><small>gross {money(result.gross_pnl)}</small></div>
            <div><span>Max drawdown</span><strong className="bad">{money(result.max_drawdown)}</strong><small>peak to trough</small></div>
          </div>
          <div className="panel">
            <header>
              <h2>Realised equity</h2>
              <span className={result.lookahead_clean ? 'chip is-good' : 'chip is-locked'}>
                {result.lookahead_clean ? 'LOOKAHEAD CLEAN' : 'LOOKAHEAD FAILED'}
              </span>
            </header>
            <div className="panel-body"><CurveChart equity={result.equity} /></div>
          </div>
          <p className="warning">
            Provenance — spec {shortHash(result.spec_hash)} · code {shortHash(result.code_hash)} ·
            data {shortHash(result.data_hash)}. Re-running these inputs reproduces this exact artifact.
          </p>
        </>
      )}

      {verdict && (
        <div className="panel">
          <header>
            <h2>Judge verdict · {verdict.decision} · grade {verdict.grade}</h2>
            {verdict.decision === 'INCONCLUSIVE' && (
              <span className="chip is-locked">EVIDENCE MISSING</span>
            )}
          </header>
          <div className="stat-row">
            <Stat label="Deflated Sharpe" value={fmt(verdict.metrics.deflated_sharpe)}
              note={`vs best-of-${verdict.metrics.trial_count} hurdle ${fmt(verdict.metrics.expected_max_sharpe)}`} />
            <Stat label="Probabilistic Sharpe" value={fmt(verdict.metrics.probabilistic_sharpe)}
              note="undeflated — ignores how many things were tried" />
            <Stat label="Permutation p" value={fmt(verdict.metrics.permutation_p_value)}
              note="share of sign-flipped resamples that did better" />
            <Stat label="Calmar" value={fmt(verdict.metrics.calmar)}
              note={`max drawdown ${money(verdict.metrics.max_drawdown)}`} />
          </div>
          {verdict.decision === 'INCONCLUSIVE' && (
            <p className="warning">
              Gates below marked NOT MEASURED have no evidence behind them. Absent evidence is
              never read as a pass — run <b>Validate</b> to measure them.
            </p>
          )}
          <div className="gates">
            {verdict.gates.map((g) => (
              <div className="gate" key={g.gate}>
                <span className="id">{g.gate}</span>
                <b className={`status ${g.status === 'PASS' ? 'good' : g.status === 'INCONCLUSIVE' ? 'warn' : 'bad'}`}>
                  {g.status === 'INCONCLUSIVE' ? 'NOT MEASURED' : g.status}
                </b>
                <div>
                  <span className="name">{g.name}</span>
                  <p>{g.finding}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {evidence && (
        <div className="panel">
          <header>
            <h2>Validation evidence</h2>
            <span className={`chip ${evidence.probability_of_overfitting < 0.5 ? 'is-good' : 'is-locked'}`}>
              {evidence.trial_count} TRIALS · {evidence.cscv_splits} CSCV SPLITS
            </span>
          </header>
          <div className="stat-row">
            <Stat label="Overfitting probability"
              value={pct(evidence.probability_of_overfitting)}
              bad={evidence.probability_of_overfitting >= 0.5}
              note="how often the in-sample winner ranks below the out-of-sample median" />
            <Stat label="Walk-forward efficiency"
              value={evidence.walk_forward_efficiency.toFixed(2)}
              bad={evidence.walk_forward_efficiency < 0.5}
              note={`${evidence.walk_forward.positive_folds}/${evidence.walk_forward_folds} folds positive`} />
            <Stat label="Worst CPCV path"
              value={evidence.path_sharpe_p05.toFixed(3)}
              bad={evidence.path_sharpe_p05 <= 0}
              note={`5th percentile Sharpe across ${evidence.cpcv_paths} reconstructed paths`} />
            <Stat label="Selection stability"
              value={pct(evidence.selection_stability)}
              bad={evidence.selection_stability < 0.5}
              note="how often the search picks the same configuration as the window moves" />
          </div>
          <p className="warning">
            Parameters were re-selected inside every fold and every split, so the out-of-sample
            numbers include the cost of choosing. Winner:{' '}
            <b>{Object.entries(evidence.best_parameters).map(([k, val]) => `${k}=${val}`).join(' · ') || 'defaults'}</b>.
          </p>
        </div>
      )}

      {sweep && (
        <div className="panel">
          <header>
            <h2>Parameter sweep · {sweep.parameter.name}</h2>
            <span className="chip">EXPLORATORY · CANNOT PROMOTE</span>
          </header>
          <div className="panel-body">
            <SweepChart points={sweep.points} label={sweep.parameter.name} />
            <p className="warning">
              {sweep.points.length} trials counted against this lineage. A wider sweep raises the
              statistical bar a result must clear — searching harder makes passing harder, by design.
            </p>
          </div>
        </div>
      )}

      {history.length > 0 && (
        <div className="panel">
          <header><h2>Backtest history · {history.length}</h2></header>
          <div className="panel-body panel-scroll">
            <table className="tbl">
              <thead><tr><th>Artifact</th><th className="num">Net</th><th className="num">Trades</th><th>Finished</th></tr></thead>
              <tbody>
                {history.map((b) => (
                  <tr key={b.backtest_id}>
                    <td>{shortHash(b.backtest_id)}</td>
                    <td className={`num ${b.net_pnl >= 0 ? 'good' : 'bad'}`}>{signed(b.net_pnl)}</td>
                    <td className="num">{b.trade_count}</td>
                    <td>{b.finished_at.slice(0, 19).replace('T', ' ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function TradesPane({ result }: { result: BacktestResult | null }) {
  if (!result) return <div className="state">Run a backtest to see its trades.</div>
  return (
    <div className="panel">
      <header>
        <h2>{result.trades.length} trades</h2>
        <span className="chip">DECISION BAR ALWAYS PRECEDES FILL BAR</span>
      </header>
      <div className="panel-body panel-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th><th>Dir</th><th>Entry</th><th className="num">In</th><th className="num">Out</th>
              <th className="num">Bars</th><th className="num">Gross</th><th className="num">Costs</th>
              <th className="num">Net</th><th>Exit</th><th className="num">Decide→Fill</th>
            </tr>
          </thead>
          <tbody>
            {result.trades.map((t, i) => (
              <tr key={t.trade_id}>
                <td>{i + 1}</td>
                <td className={t.direction === 1 ? 'good' : 'bad'}>{t.direction === 1 ? 'LONG' : 'SHORT'}</td>
                <td>{clock(t.entry_time)}</td>
                <td className="num">{t.entry_price.toFixed(2)}</td>
                <td className="num">{t.exit_price.toFixed(2)}</td>
                <td className="num">{t.bars_held}</td>
                <td className="num">{signed(t.gross_pnl)}</td>
                <td className="num">{t.costs.toFixed(2)}</td>
                <td className={`num ${t.net_pnl >= 0 ? 'good' : 'bad'}`}>{signed(t.net_pnl)}</td>
                <td>{t.exit_reason}</td>
                <td className="num">{t.entry_decision_index}→{t.entry_index}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const fmt = (value: number | undefined) =>
  value === undefined || value === -1 ? '—' : value.toFixed(3)

function Stat({ label, value, note, bad }: {
  label: string; value: string; note: string; bad?: boolean
}) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <b className={bad ? 'stat-value bad' : 'stat-value'}>{value}</b>
      <p className="stat-note">{note}</p>
    </div>
  )
}
