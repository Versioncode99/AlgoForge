import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, Box, Check, FileCode2, Gavel, Play, Plus, ShieldCheck, Trash2,
  Waves,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { deleteJson, getJson, postJson, putJson } from '../api'
import { computeStats, byPeriod, equityFrom, filterSide, type Side, type Stats } from '../analytics'
import { CurveChart, SweepChart } from '../charts'
import { AnalysisChart, type AnalysisResult } from '../components/AnalysisChart'
import { JobBar } from '../components/JobBar'
import { Empty, PanelHead, Rolling, Stat, TierPill, VerdictPill } from '../components/ui'
import { useJob } from '../hooks/useJob'
import { StrategyCatalogue } from './StrategyCatalogue'
import { money, pct, shortHash, signed, stamp } from '../lib'
import type {
  BacktestJobResult, BacktestResult, DatasetInfo, Job, StrategyDetail, StrategyListItem,
  SweepResult, TemplateInfo, Trade, ValidationEvidence, Verdict,
} from '../types'

type Pane = 'summary' | 'trades' | 'periods' | 'gates' | 'validation' | 'code' | 'hypothesis'
const PANES: { key: Pane; label: string }[] = [
  { key: 'summary', label: 'Summary' },
  { key: 'trades', label: 'Trades' },
  { key: 'periods', label: 'Periods' },
  { key: 'gates', label: 'Gates' },
  { key: 'validation', label: 'Validation' },
  { key: 'code', label: 'Code' },
  { key: 'hypothesis', label: 'Hypothesis' },
]

export function StrategiesView() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [pane, setPane] = useState<Pane>('summary')
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const [dataset, setDataset] = useState('')
  const [years, setYears] = useState(1)
  const [cap, setCap] = useState(1_000_000)

  const [result, setResult] = useState<BacktestResult | null>(null)
  const [resultMeta, setResultMeta] = useState<BacktestJobResult['meta'] | null>(null)
  const [verdict, setVerdict] = useState<Verdict | null>(null)
  const [evidence, setEvidence] = useState<ValidationEvidence | null>(null)
  const [sweep, setSweep] = useState<SweepResult | null>(null)
  // The two-parameter landscape. Kept separate from the one-parameter sweep
  // because it is a different claim: a line says how one knob behaves, a
  // surface says whether the best point is standing on anything.
  const [surface, setSurface] = useState<AnalysisResult | null>(null)

  const job = useJob()

  const list = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => getJson<TemplateInfo[]>('/templates') })
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })

  // The catalogue is the landing surface. Auto-selecting the first record
  // hid four hundred others behind a detail pane nobody asked for.
  useEffect(() => {
    if (!dataset && datasets.data?.length) {
      // Prefer a local archive, but never land on a data set that cannot run.
      // `is_imported && available` describes one machine's setup, and where no
      // archive is present it fell through to `[0]` — which is the 16-year NQ
      // archive, rendered disabled in this very dropdown. A fresh install
      // therefore opened with an unrunnable data set selected and failed the
      // first backtest anyone tried, on a provider error rather than anything
      // about the strategy.
      const real =
        datasets.data.find((d) => d.is_imported && d.available)
        ?? datasets.data.find((d) => d.available)
        ?? datasets.data[0]
      setDataset(real.key)
    }
  }, [datasets.data, dataset])

  const detail = useQuery({
    queryKey: ['strategy', selected], enabled: !!selected,
    queryFn: () => getJson<StrategyDetail>(`/strategies/${selected}`),
  })

  const activeDataset = datasets.data?.find((d) => d.key === dataset)
  const ranges = activeDataset?.ranges ?? []
  const chosen = ranges.find((r) => r.years === years)
  const plannedBars = Math.min(chosen?.bars ?? 0, cap)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['strategies'] })
    qc.invalidateQueries({ queryKey: ['strategy', selected] })
    qc.invalidateQueries({ queryKey: ['activity'] })
    qc.invalidateQueries({ queryKey: ['summary'] })
  }
  const fail = (e: Error) => setBanner({ kind: 'err', text: e.message })

  const reset = () => {
    setResult(null); setResultMeta(null); setVerdict(null); setEvidence(null)
    setSweep(null); setSurface(null); job.clear()
  }

  const create = useMutation({
    mutationFn: (template: string) => postJson<{ strategy_id: string }>('/strategies', { template }),
    onSuccess: (data) => { setSelected(data.strategy_id); reset(); setBanner({ kind: 'ok', text: `Created ${data.strategy_id}.` }); refresh() },
    onError: fail,
  })

  const backtest = useMutation({
    mutationFn: () => postJson<Job>(`/strategies/${selected}/backtest/async`, {
      dataset, years, max_bars: cap,
    }),
    onSuccess: (started) => { reset(); job.start(started); setPane('summary') },
    onError: fail,
  })

  // The job carries the result home; nothing polls the strategy list for it.
  useEffect(() => {
    if (job.job?.status === 'DONE' && job.job.result) {
      const payload = job.job.result as BacktestJobResult
      setResult(payload.result)
      setResultMeta(payload.meta)
      setBanner({ kind: 'ok', text: `${payload.meta.trade_count} trades on ${payload.meta.bar_count.toLocaleString()} bars · net ${signed(payload.result.net_pnl)}` })
      refresh()
    }
    if (job.job?.status === 'FAILED') setBanner({ kind: 'err', text: job.job.error ?? 'Job failed.' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job?.status])

  const validate = useMutation({
    mutationFn: () => postJson<ValidationEvidence>(`/strategies/${selected}/validate`, { dataset, years: Math.min(years, 1) }),
    onSuccess: (data) => {
      setEvidence(data); setVerdict(null); setPane('validation')
      setBanner({ kind: data.probability_of_overfitting < 0.5 ? 'ok' : 'err', text: `PBO ${pct(data.probability_of_overfitting)} · walk-forward ${data.walk_forward_efficiency.toFixed(2)} · ${data.trial_count} trials` })
      refresh()
    },
    onError: fail,
  })

  const judge = useMutation({
    mutationFn: () => postJson<Verdict>(`/strategies/${selected}/judge`),
    onSuccess: (data) => {
      setVerdict(data); setPane('gates')
      setBanner({ kind: data.decision === 'PASS' ? 'ok' : 'err', text: `Judge: ${data.decision} (grade ${data.grade})` })
      refresh()
    },
    onError: fail,
  })

  const surfaceJob = useJob()
  const runSurface = useMutation({
    mutationFn: (names: [string, string]) =>
      postJson<Job>(`/strategies/${selected}/sweep-surface`, {
        x_parameter: names[0],
        y_parameter: names[1],
        x_steps: 6,
        y_steps: 6,
        dataset,
      }),
    onSuccess: (started) => { setSurface(null); setPane('summary'); surfaceJob.start(started) },
  })

  useEffect(() => {
    if (surfaceJob.job?.status === 'DONE' && surfaceJob.job.result) {
      setSurface(surfaceJob.job.result as AnalysisResult)
      refresh()
    }
  }, [surfaceJob.job?.status, surfaceJob.job?.result])

  const runSweep = useMutation({
    mutationFn: (parameter: string) => postJson<SweepResult>(`/strategies/${selected}/sweep`, { parameter, dataset }),
    onSuccess: (data) => { setSweep(data); setPane('summary'); refresh() },
    onError: fail,
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteJson(`/strategies/${id}`),
    onSuccess: () => { setSelected(null); reset(); refresh() },
    onError: fail,
  })

  const saveSource = useMutation({
    mutationFn: (source: string) => putJson(`/strategies/${selected}/source`, { source }),
    onSuccess: () => { setBanner({ kind: 'ok', text: 'Source saved — guard checks passed.' }); refresh() },
    onError: fail,
  })

  const busy =
    job.active ||
    validate.isPending ||
    judge.isPending ||
    runSweep.isPending ||
    surfaceJob.active
  const spec = detail.data?.spec

  return (
    <section className="strategies">
      {list.data?.some(item => item.latest?.calculation_version === 'legacy-price-points') && <p className="command-error">Older backtests used price points as dollar P&amp;L. Rerun them with corrected contract units before comparing results or simulating prop accounts.</p>}
      {banner && (
        <div className={banner.kind === 'ok' ? 'banner is-ok af-panel-in' : 'banner is-err af-panel-in'} role="status">
          {banner.kind === 'ok' ? <Check /> : <AlertTriangle />}
          <span>{banner.text}</span>
          <button onClick={() => setBanner(null)} aria-label="Dismiss message">×</button>
        </div>
      )}

      {job.job && <JobBar job={job.job} onCancel={job.cancel} onDismiss={job.clear} />}

      {!selected && (
        <>
          <StrategyCatalogue
            items={list.data ?? []}
            pending={list.isPending}
            onOpen={(id) => { setSelected(id); reset() }}
          />
          <div className="panel">
            <PanelHead title="New from template" meta={`${templates.data?.length ?? 0} registered templates`} />
            <div className="template-grid">
              {templates.data?.map((t) => (
                <button key={t.key} className="tmpl af-press" disabled={create.isPending} onClick={() => create.mutate(t.key)}>
                  <Plus />
                  <span>{t.name}</span>
                  <small>{t.line_count} lines</small>
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {selected && (
        <div className="strat-detail is-full">
          <button className="text-action back-link" onClick={() => { setSelected(null); reset() }}>
            <ArrowLeft aria-hidden="true" /> All strategies
          </button>
          {!spec && <div className="state" role="status">Opening strategy…</div>}
          {spec && (
            <>
              <div className="strat-head">
                <div>
                  <p className="eyebrow">{spec.family.toUpperCase()} · {spec.symbol} · {spec.bar_spec}</p>
                  <h3>{spec.name}</h3>
                  <small className="path">{detail.data?.path}</small>
                </div>
                <div className="actions">
                  <button className="btn primary af-press" disabled={busy} onClick={() => backtest.mutate()}>
                    <Play />{job.active ? 'Running…' : 'Run backtest'}
                  </button>
                  <button className="btn af-press" disabled={busy} onClick={() => validate.mutate()}
                    aria-label="Validate"
                    title="Walk-forward, CSCV and CPCV. The judge cannot pass a strategy without this.">
                    <ShieldCheck />{validate.isPending ? 'Validating…' : 'Validate'}
                  </button>
                  <button className="btn af-press" disabled={busy} onClick={() => judge.mutate()}>
                    <Gavel />{judge.isPending ? 'Judging…' : 'Judge'}
                  </button>
                  <button className="btn af-press" disabled={busy || !spec.parameters.length}
                    onClick={() => runSweep.mutate(spec.parameters[0].name)}>
                    <Waves />{runSweep.isPending ? 'Sweeping…' : 'Sweep'}
                  </button>
                  <button className="btn af-press" disabled={busy || spec.parameters.length < 2}
                    title={spec.parameters.length < 2
                      ? 'A surface needs two parameters; this strategy declares fewer.'
                      : 'Every cell is a real in-sample backtest. 36 of them.'}
                    onClick={() => runSurface.mutate([
                      spec.parameters[0].name, spec.parameters[1].name,
                    ])}>
                    <Box />{surfaceJob.active ? 'Mapping…' : 'Surface'}
                  </button>
                  <button className="btn danger af-press" aria-label="Delete strategy" onClick={() => remove.mutate(spec.strategy_id)}>
                    <Trash2 />
                  </button>
                </div>
              </div>

              <ControlStrip
                datasets={datasets.data ?? []}
                dataset={dataset} onDataset={(d) => { setDataset(d); setYears(1) }}
                ranges={ranges} years={years} onYears={setYears}
                cap={cap} onCap={setCap}
                plannedBars={plannedBars}
                capped={(chosen?.bars ?? 0) > cap}
                disabled={busy}
              />

              {/* Outside the panes on purpose. A parameter landscape is its own
                  artifact: it is produced by 36 backtests of its own and says
                  nothing about whichever run happens to be loaded, so hiding it
                  behind "no backtest in this session" made it unreachable. */}
              {surfaceJob.job && (
                <JobBar job={surfaceJob.job} onCancel={surfaceJob.cancel} onDismiss={surfaceJob.clear} />
              )}

              {surface && (
                <div className="panel af-panel-in">
                  <PanelHead
                    title={surface.title}
                    meta={`${surface.cells.length} configurations · exploratory · cannot promote`}
                  />
                  <div className="panel-body stack">
                    {/* The same renderer the research lab uses. A parameter
                        landscape is the most attractive picture this application
                        can draw and the most dangerous — a lone peak is what
                        overfitting looks like from above — so the findings sit
                        under it rather than beside it. */}
                    <AnalysisChart result={surface} onPick={() => undefined} height={420} />
                    {surface.findings.length > 0 && (
                      <ul className="lab-findings">
                        {surface.findings.map((finding) => (
                          <li key={finding}><span>{finding}</span></li>
                        ))}
                      </ul>
                    )}
                    {surface.warnings.filter(Boolean).map((warning) => (
                      <p key={warning} className="warning">{warning}</p>
                    ))}
                  </div>
                </div>
              )}

              <nav className="panes">
                {PANES.map((p) => (
                  <button key={p.key} className={pane === p.key ? 'active af-press' : 'af-press'} onClick={() => setPane(p.key)}>
                    {p.key === 'code' && <FileCode2 />}
                    {p.label}
                  </button>
                ))}
              </nav>

              <div className="pane-body">
                {pane === 'summary' && (
                  <SummaryPane
                    result={result}
                    meta={resultMeta}
                    sweep={sweep}
                    history={detail.data?.backtests ?? []}
                  />
                )}
                {pane === 'trades' && <TradesPane result={result} />}
                {pane === 'periods' && <PeriodsPane result={result} />}
                {pane === 'gates' && <GatesPane verdict={verdict} onJudge={() => judge.mutate()} busy={busy} />}
                {pane === 'validation' && <ValidationPane evidence={evidence} onValidate={() => validate.mutate()} busy={busy} />}
                {pane === 'code' && (
                  <CodePane
                    source={detail.data?.source ?? ''} tests={detail.data?.tests ?? ''}
                    codeHash={detail.data?.code_hash ?? ''} saving={saveSource.isPending}
                    onSave={(next) => saveSource.mutate(next)}
                  />
                )}
                {pane === 'hypothesis' && <HypothesisPane spec={spec} />}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}

/* ── control strip ───────────────────────────────────────────────────────── */

function ControlStrip({
  datasets, dataset, onDataset, ranges, years, onYears, cap, onCap, plannedBars, capped, disabled,
}: {
  datasets: DatasetInfo[]; dataset: string; onDataset: (d: string) => void
  ranges: { years: number; label: string; bars: number; available: boolean }[]
  years: number; onYears: (y: number) => void
  cap: number; onCap: (c: number) => void
  plannedBars: number; capped: boolean; disabled: boolean
}) {
  const active = datasets.find((d) => d.key === dataset)
  // 15k bars/second measured on the imported archive. An estimate the user can
  // see beats a spinner they cannot.
  const estimate = plannedBars / 15_000
  return (
    <div className="control-strip af-panel-in">
      <label className="ctl">
        <span>Data</span>
        <select aria-label="Data set" value={dataset} disabled={disabled} onChange={(e) => onDataset(e.target.value)}>
          {datasets.map((d) => (
            <option key={d.key} value={d.key} disabled={!d.available}>
              {d.label}{d.is_imported ? '' : ` · ${d.cost_note}`}
            </option>
          ))}
        </select>
      </label>

      <div className="ctl range-ctl">
        <span>Range</span>
        <div className="range-buttons" role="group" aria-label="History range">
          {ranges.map((r) => (
            <button
              key={r.years}
              className={r.years === years ? 'active af-press' : 'af-press'}
              disabled={disabled || !r.available}
              // The bar count belongs in the tooltip, but a bare title becomes the
              // accessible name and hides the label a screen reader needs.
              aria-label={r.available ? `${r.label}, ${r.bars.toLocaleString()} bars` : `${r.label}, longer than this dataset`}
              title={r.available ? `${r.bars.toLocaleString()} bars` : 'Longer than this dataset'}
              onClick={() => onYears(r.years)}
            >
              {r.label}
            </button>
          ))}
          {!ranges.length && <span className="sub">no ranges</span>}
        </div>
      </div>

      <label className="ctl">
        <span>Cap</span>
        <select aria-label="Bar cap" value={cap} disabled={disabled} onChange={(e) => onCap(Number(e.target.value))}>
          <option value={250_000}>250k bars</option>
          <option value={500_000}>500k bars</option>
          <option value={1_000_000}>1M bars</option>
          <option value={2_500_000}>2.5M bars</option>
          <option value={5_000_000}>No cap</option>
        </select>
      </label>

      <div className="ctl-readout">
        <b className="mono">{plannedBars.toLocaleString()}</b>
        <span>bars{capped ? ' · capped' : ''} · about {estimate < 60 ? `${Math.max(1, Math.round(estimate))}s` : `${Math.round(estimate / 60)}m`}</span>
        {active && active.span_years > 0 && (
          <span className="sub">{active.symbol} · {active.bar_count.toLocaleString()} available over {active.span_years}y</span>
        )}
      </div>
    </div>
  )
}

/* ── summary ─────────────────────────────────────────────────────────────── */

function SummaryPane({ result, meta, sweep, history }: {
  result: BacktestResult | null
  meta: BacktestJobResult['meta'] | null
  sweep: SweepResult | null
  history: { backtest_id: string; net_pnl: number; trade_count: number; finished_at: string }[]
}) {
  const [side, setSide] = useState<Side>('all')
  const trades = useMemo(() => (result ? filterSide(result.trades, side) : []), [result, side])
  const stats = useMemo(() => computeStats(trades), [trades])
  const all = useMemo(() => (result ? computeStats(result.trades) : null), [result])
  const longs = useMemo(() => (result ? computeStats(filterSide(result.trades, 'long')) : null), [result])
  const shorts = useMemo(() => (result ? computeStats(filterSide(result.trades, 'short')) : null), [result])

  if (!result) {
    return (
      <Empty
        title="No backtest in this session"
        detail={
          history.length
            ? `This strategy has ${history.length} stored run${history.length === 1 ? '' : 's'}. Pick a range above and press Run backtest to produce a fresh one with full statistics.`
            : 'Pick a data set and a range above, then press Run backtest. One year of NQ is about 300,000 bars and takes under a minute.'
        }
      />
    )
  }

  return (
    <div className="stack">
      <div className="headline-row af-panel-in">
        <Stat label="Net profit" tone={stats.netProfit >= 0 ? 'good' : 'bad'}
          value={<Rolling value={stats.netProfit} decimals={2} prefix="$" className="mono" />}
          note={`after ${money(stats.commission)} costs`} />
        <Stat label="Profit factor" tone={stats.profitFactor > 1.1 ? 'good' : 'bad'}
          value={<span className="mono">{Number.isFinite(stats.profitFactor) ? stats.profitFactor.toFixed(2) : '∞'}</span>}
          note="gross win / gross loss" />
        <Stat label="Max drawdown" tone={stats.maxDrawdown > Math.abs(stats.netProfit) ? 'bad' : 'plain'}
          value={<span className="mono">{money(stats.maxDrawdown)}</span>}
          note={`worst peak-to-trough · ${pct(stats.maxDrawdownPct)}`} />
        <Stat label="Trades" value={<Rolling value={stats.trades} className="mono" />}
          note={`${pct(stats.winRate)} profitable`} />
        <Stat label="Evidence" value={<TierPill tier={meta?.evidence_tier ?? result.evidence_tier} />}
          note={meta ? `${meta.bar_count.toLocaleString()} bars · ${meta.provider}` : 'stored run'} />
      </div>

      <div className="panel af-panel-in">
        <PanelHead title="Realised equity" meta={`${trades.length} trades · ${side}`}>
          <div className="side-switch" role="group" aria-label="Side">
            {(['all', 'long', 'short'] as Side[]).map((s) => (
              <button key={s} className={side === s ? 'active af-press' : 'af-press'} onClick={() => setSide(s)}>{s}</button>
            ))}
          </div>
        </PanelHead>
        <div className="panel-body">
          {trades.length ? <CurveChart equity={equityFrom(trades)} /> : <p className="sub">No {side} trades in this run.</p>}
        </div>
      </div>

      {all && longs && shorts && <StatsTable all={all} longs={longs} shorts={shorts} />}

      {meta?.development_trade_count !== undefined && (
        <p className="warning">
          In-sample pass took {meta.development_trade_count} trades for {signed(meta.development_net_pnl ?? 0)};
          the figures above are the out-of-sample partition only. A large gap between the two is the
          shape of a curve fit.
        </p>
      )}

      <p className="warning">
        Provenance — spec {shortHash(result.spec_hash)} · code {shortHash(result.code_hash)} ·
        data {shortHash(result.data_hash)}. Re-running these inputs reproduces this exact artifact.
      </p>

      {sweep && (
        <div className="panel af-panel-in">
          <PanelHead title={`Parameter sweep · ${sweep.parameter.name}`} meta="exploratory · cannot promote" />
          <div className="panel-body"><SweepChart points={sweep.points} label={sweep.parameter.name} /></div>
        </div>
      )}

    </div>
  )
}

const ROWS: { label: string; get: (s: Stats) => string; hint?: string }[] = [
  { label: 'Net profit', get: (s) => money(s.netProfit) },
  { label: 'Gross profit', get: (s) => money(s.grossProfit) },
  { label: 'Gross loss', get: (s) => money(-s.grossLoss) },
  { label: 'Commission & slippage', get: (s) => money(s.commission) },
  { label: 'Profit factor', get: (s) => (Number.isFinite(s.profitFactor) ? s.profitFactor.toFixed(3) : '∞') },
  { label: 'Expectancy per trade', get: (s) => money(s.expectancy) },
  { label: 'Total trades', get: (s) => String(s.trades) },
  { label: 'Winners', get: (s) => String(s.winners) },
  { label: 'Losers', get: (s) => String(s.losers) },
  { label: 'Percent profitable', get: (s) => pct(s.winRate) },
  { label: 'Average trade', get: (s) => money(s.avgTrade) },
  { label: 'Average winner', get: (s) => money(s.avgWinner) },
  { label: 'Average loser', get: (s) => money(s.avgLoser) },
  { label: 'Payoff ratio', get: (s) => s.payoffRatio.toFixed(2), hint: 'avg win / avg loss' },
  { label: 'Largest winner', get: (s) => money(s.largestWinner) },
  { label: 'Largest loser', get: (s) => money(s.largestLoser) },
  { label: 'Max consecutive winners', get: (s) => String(s.maxConsecWinners) },
  { label: 'Max consecutive losers', get: (s) => String(s.maxConsecLosers) },
  { label: 'Max drawdown', get: (s) => money(s.maxDrawdown) },
  { label: 'Max run-up', get: (s) => money(s.maxRunup) },
  { label: 'Sharpe (per trade)', get: (s) => s.sharpe.toFixed(4), hint: 'not annualised — trades are not evenly spaced' },
  { label: 'Sortino (per trade)', get: (s) => s.sortino.toFixed(4) },
  { label: 'Calmar', get: (s) => s.calmar.toFixed(3), hint: 'net profit / max drawdown' },
  { label: 'Average bars held', get: (s) => s.avgBarsHeld.toFixed(1) },
  { label: 'Average bars · winners', get: (s) => s.avgBarsWinner.toFixed(1) },
  { label: 'Average bars · losers', get: (s) => s.avgBarsLoser.toFixed(1) },
  { label: 'Longest flat stretch (bars)', get: (s) => String(s.longestFlatBars) },
]

function StatsTable({ all, longs, shorts }: { all: Stats; longs: Stats; shorts: Stats }) {
  return (
    <div className="panel af-panel-in">
      <PanelHead title="Performance summary" meta="all · long · short" />
      <table className="stats-table">
        <thead>
          <tr><th>Statistic</th><th>All</th><th>Long</th><th>Short</th></tr>
        </thead>
        <tbody>
          {ROWS.map((row, i) => (
            <tr key={row.label} className="af-row-in" style={{ animationDelay: `${Math.min(i, 20) * 10}ms` }}>
              <th scope="row" title={row.hint}>{row.label}{row.hint && <i className="hint">?</i>}</th>
              <td className="mono">{row.get(all)}</td>
              <td className="mono">{row.get(longs)}</td>
              <td className="mono">{row.get(shorts)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ── trades ──────────────────────────────────────────────────────────────── */

function TradesPane({ result }: { result: BacktestResult | null }) {
  const [side, setSide] = useState<Side>('all')
  const [onlyLosers, setOnlyLosers] = useState(false)
  if (!result) {
    return <Empty title="No trades to list" detail="Run a backtest and every fill lands here with its decision bar, so the no-lookahead invariant can be checked by eye." />
  }
  let rows: Trade[] = filterSide(result.trades, side)
  if (onlyLosers) rows = rows.filter((t) => t.net_pnl < 0)

  return (
    <div className="panel af-panel-in">
      <PanelHead title="Trades" meta={`${rows.length} of ${result.trades.length}`}>
        <div className="side-switch" role="group" aria-label="Side">
          {(['all', 'long', 'short'] as Side[]).map((s) => (
            <button key={s} className={side === s ? 'active af-press' : 'af-press'} onClick={() => setSide(s)}>{s}</button>
          ))}
          <button className={onlyLosers ? 'active af-press' : 'af-press'} onClick={() => setOnlyLosers(!onlyLosers)}>losers</button>
        </div>
      </PanelHead>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th><th>Side</th><th>Entry</th><th>Exit</th>
              <th className="num">Entry px</th><th className="num">Exit px</th>
              <th className="num">Bars</th><th className="num">Gross</th>
              <th className="num">Costs</th><th className="num">Net</th><th>Exit reason</th>
              <th className="num">Decision → fill</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 400).map((t, i) => (
              <tr key={t.trade_id} className="af-row-in" style={{ animationDelay: `${Math.min(i, 24) * 8}ms` }}>
                <td className="mono sub">{i + 1}</td>
                <td><span className={t.direction === 1 ? 'side-tag long' : 'side-tag short'}>{t.direction === 1 ? 'LONG' : 'SHORT'}</span></td>
                <td className="mono sub">{stamp(t.entry_time)}</td>
                <td className="mono sub">{stamp(t.exit_time)}</td>
                <td className="mono num">{t.entry_price.toFixed(2)}</td>
                <td className="mono num">{t.exit_price.toFixed(2)}</td>
                <td className="mono num">{t.bars_held}</td>
                <td className="mono num">{money(t.gross_pnl)}</td>
                <td className="mono num sub">{money(-t.costs)}</td>
                <td className={t.net_pnl >= 0 ? 'mono num good' : 'mono num bad'}>{signed(t.net_pnl)}</td>
                <td className="sub">{t.exit_reason}</td>
                <td className="mono num sub">{t.entry_decision_index} → {t.entry_index}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 400 && <p className="sub pad">Showing the first 400 of {rows.length}.</p>}
      <p className="warning">
        DECISION BAR ALWAYS PRECEDES FILL BAR — every row satisfies decision + 1 = fill by
        construction, which is what makes lookahead impossible rather than merely discouraged.
      </p>
    </div>
  )
}

/* ── periods ─────────────────────────────────────────────────────────────── */

function PeriodsPane({ result }: { result: BacktestResult | null }) {
  const [grain, setGrain] = useState<'day' | 'week' | 'month' | 'year'>('month')
  const rows = useMemo(() => (result ? byPeriod(result.trades, grain) : []), [result, grain])
  if (!result) {
    return <Empty title="No period breakdown yet" detail="Run a backtest to see whether the money arrived steadily or in one lucky quarter. That difference does not show in a net figure." />
  }
  const worst = Math.max(...rows.map((r) => Math.abs(r.net)), 1)
  const positive = rows.filter((r) => r.net > 0).length

  return (
    <div className="panel af-panel-in">
      <PanelHead title="Returns by period" meta={`${positive}/${rows.length} periods positive`}>
        <div className="side-switch" role="group" aria-label="Grain">
          {(['day', 'week', 'month', 'year'] as const).map((g) => (
            <button key={g} className={grain === g ? 'active af-press' : 'af-press'} onClick={() => setGrain(g)}>{g}</button>
          ))}
        </div>
      </PanelHead>
      <div className="table-scroll">
        <table className="data-table">
          <thead><tr><th>Period</th><th className="num">Net</th><th className="num">Trades</th><th className="num">Win rate</th><th>Shape</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.key} className="af-row-in" style={{ animationDelay: `${Math.min(i, 24) * 8}ms` }}>
                <td className="mono">{r.key}</td>
                <td className={r.net >= 0 ? 'mono num good' : 'mono num bad'}>{signed(r.net)}</td>
                <td className="mono num sub">{r.trades}</td>
                <td className="mono num sub">{pct(r.wins / Math.max(r.trades, 1))}</td>
                <td>
                  <div className="period-bar">
                    <i className={r.net >= 0 ? 'pos af-wipe-in' : 'neg af-wipe-in'} style={{ width: `${(Math.abs(r.net) / worst) * 100}%` }} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ── gates ───────────────────────────────────────────────────────────────── */

function GatesPane({ verdict, onJudge, busy }: { verdict: Verdict | null; onJudge: () => void; busy: boolean }) {
  if (!verdict) {
    return (
      <Empty
        title="Not judged in this session"
        detail="The judge runs fourteen deterministic gates over the most recent out-of-sample artifact. Gates whose evidence has never been measured return NOT MEASURED, and any of those withholds the pass."
        action={<button className="btn primary af-press" disabled={busy} onClick={onJudge}><Gavel />Run the judge</button>}
      />
    )
  }
  const failed = verdict.gates.filter((g) => g.status === 'FAIL').length
  const unknown = verdict.gates.filter((g) => g.status === 'INCONCLUSIVE').length

  return (
    <div className="stack">
      <div className="headline-row af-panel-in">
        <Stat label="Decision" value={<VerdictPill status={verdict.decision} />}
          tone={verdict.decision === 'PASS' ? 'good' : verdict.decision === 'INCONCLUSIVE' ? 'unknown' : 'bad'}
          note={`grade ${verdict.grade}`} />
        <Stat label="Deflated Sharpe" value={<span className="mono">{fmt(verdict.metrics.deflated_sharpe)}</span>}
          note={`vs best-of-${verdict.metrics.trial_count} noise hurdle ${fmt(verdict.metrics.expected_max_sharpe)}`} />
        <Stat label="Probabilistic Sharpe" value={<span className="mono">{fmt(verdict.metrics.probabilistic_sharpe)}</span>}
          note="undeflated — ignores how many things were tried" />
        <Stat label="Permutation p" value={<span className="mono">{fmt(verdict.metrics.permutation_p_value)}</span>}
          note="share of sign-flipped resamples that did better" />
        <Stat label="Gates" value={<span className="mono">{failed} fail · {unknown} unmeasured</span>}
          tone={failed ? 'bad' : unknown ? 'unknown' : 'good'} note={`of ${verdict.gates.length}`} />
      </div>

      {unknown > 0 && (
        <p className="warning">
          {unknown} gate{unknown === 1 ? '' : 's'} had no evidence behind {unknown === 1 ? 'it' : 'them'}.
          Absent evidence is never read as a pass — run <b>Validate</b> to measure them.
        </p>
      )}

      <div className="panel af-panel-in">
        <PanelHead title="Gate ladder" meta="G0 – G13 · deterministic, no model in the loop" />
        <div className="gates">
          {verdict.gates.map((g, i) => (
            <div className="gate af-row-in" key={g.gate} style={{ animationDelay: `${Math.min(i, 14) * 14}ms` }}>
              <span className="id">{g.gate}</span>
              <VerdictPill status={g.status} />
              <div>
                <span className="name">{g.name}</span>
                <p>{g.finding}</p>
                {g.rule && <p className="sub">Rule: {g.rule}</p>}
              </div>
              <span className="mono observed">{String(g.observed)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ── validation ──────────────────────────────────────────────────────────── */

function ValidationPane({ evidence, onValidate, busy }: {
  evidence: ValidationEvidence | null; onValidate: () => void; busy: boolean
}) {
  if (!evidence) {
    return (
      <Empty
        title="Validation has not run"
        detail="This sweeps the parameter grid, then re-selects parameters inside every walk-forward fold and every CPCV split, so the out-of-sample numbers include the cost of choosing. It is what gates G5 and G11–G13 read."
        action={<button className="btn primary af-press" disabled={busy} onClick={onValidate}><ShieldCheck />Run validation</button>}
      />
    )
  }
  return (
    <div className="stack">
      <div className="headline-row af-panel-in">
        <Stat label="Overfitting probability" value={<span className="mono">{pct(evidence.probability_of_overfitting)}</span>}
          tone={evidence.probability_of_overfitting < 0.5 ? 'good' : 'bad'}
          note="how often the in-sample winner ranks below the out-of-sample median" />
        <Stat label="Walk-forward efficiency" value={<span className="mono">{evidence.walk_forward_efficiency.toFixed(2)}</span>}
          tone={evidence.walk_forward_efficiency >= 0.5 ? 'good' : 'bad'}
          note={`${evidence.walk_forward.positive_folds}/${evidence.walk_forward_folds} folds positive`} />
        <Stat label="Worst CPCV path" value={<span className="mono">{evidence.path_sharpe_p05.toFixed(3)}</span>}
          tone={evidence.path_sharpe_p05 > 0 ? 'good' : 'bad'}
          note={`5th percentile across ${evidence.cpcv_paths} reconstructed paths`} />
        <Stat label="Selection stability" value={<span className="mono">{pct(evidence.selection_stability)}</span>}
          tone={evidence.selection_stability >= 0.5 ? 'good' : 'bad'}
          note="how often the search picks the same configuration as the window moves" />
        <Stat label="Trials" value={<span className="mono">{evidence.trial_count}</span>}
          note={`${evidence.cscv_splits} CSCV splits`} />
      </div>
      <p className="warning">
        Winner: <b>{Object.entries(evidence.best_parameters).map(([k, v]) => `${k}=${v}`).join(' · ') || 'defaults'}</b>.
        The recorded trial count is a floor — configurations tried by hand are invisible to the
        deflation, so the true hurdle is always higher than the one computed.
      </p>
    </div>
  )
}

const fmt = (v: number | undefined) => (v === undefined || v === -1 ? '—' : v.toFixed(3))

/* ── code + hypothesis ───────────────────────────────────────────────────── */

function CodePane({ source, tests, codeHash, onSave, saving }: {
  source: string; tests: string; codeHash: string; onSave: (s: string) => void; saving: boolean
}) {
  const [draft, setDraft] = useState(source)
  const [showTests, setShowTests] = useState(false)
  useEffect(() => setDraft(source), [source])
  const dirty = draft !== source

  return (
    <div className="panel af-panel-in">
      <PanelHead title={showTests ? 'Lookahead trap tests' : 'strategy.py'} meta={shortHash(codeHash)}>
        <div className="side-switch">
          <button className={!showTests ? 'active af-press' : 'af-press'} onClick={() => setShowTests(false)}>source</button>
          <button className={showTests ? 'active af-press' : 'af-press'} onClick={() => setShowTests(true)}>tests</button>
          {!showTests && (
            <button className="btn primary af-press" disabled={!dirty || saving} onClick={() => onSave(draft)}>
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          )}
        </div>
      </PanelHead>
      {showTests ? (
        <pre className="code readonly">{tests || 'No tests written for this strategy.'}</pre>
      ) : (
        <textarea className="code editor" value={draft} spellCheck={false}
          aria-label="Strategy source code" onChange={(e) => setDraft(e.target.value)} />
      )}
      <p className="warning">
        Edits pass the same static guard as generated code: no filesystem, network, subprocess or
        dynamic execution, and both signal functions must exist. Rejected code is never run.
      </p>
    </div>
  )
}

function HypothesisPane({ spec }: { spec: StrategyDetail['spec'] }) {
  return (
    <div className="stack">
      <div className="panel af-panel-in">
        <PanelHead title="Hypothesis" meta="frozen before the run" />
        <div className="panel-body prose">
          <p>{spec.hypothesis}</p>
        </div>
      </div>
      <div className="panel af-panel-in">
        <PanelHead title="Falsifiable prediction" meta="what would prove this wrong" />
        <div className="panel-body prose"><p>{spec.falsifiable_prediction}</p></div>
      </div>
      <div className="panel af-panel-in">
        <PanelHead title="Parameters" meta={`${spec.parameters.length} tunable`} />
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>Name</th><th className="num">Default</th><th className="num">Low</th><th className="num">High</th><th className="num">Step</th><th>Description</th></tr></thead>
            <tbody>
              {spec.parameters.map((p, i) => (
                <tr key={p.name} className="af-row-in" style={{ animationDelay: `${i * 14}ms` }}>
                  <td className="mono">{p.name}</td>
                  <td className="mono num">{p.default}</td>
                  <td className="mono num sub">{p.low}</td>
                  <td className="mono num sub">{p.high}</td>
                  <td className="mono num sub">{p.step}</td>
                  <td className="sub">{p.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel af-panel-in">
        <PanelHead title="Cost model" meta="applied to every fill" />
        <div className="headline-row">
          <Stat label="Commission per side" value={<span className="mono">{money(spec.commission_per_side)}</span>} note="round trip is twice this" />
          <Stat label="Slippage" value={<span className="mono">{spec.slippage_ticks} ticks</span>} note={`at ${money(spec.tick_value)} per tick`} />
          <Stat label="Warm-up" value={<span className="mono">{spec.warmup_bars} bars</span>} note="also the purge width in every split" />
        </div>
      </div>
    </div>
  )
}
