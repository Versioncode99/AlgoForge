import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from '../api'
import { AnalysisChart, type AnalysisResult, type Cell } from '../components/AnalysisChart'

/* Ask a question about a strategy's trades, and get back to the trades.
 *
 * The loop this surface exists for is: question → answer → the region of the
 * answer that looks wrong → the trades in it → one trade → the chart it
 * happened on. Every step is real data from a run that already exists; nothing
 * here executes a strategy, consumes a holdout or produces a verdict, and the
 * banner says so because a reader who mistook an analysis for evidence would be
 * laundering exploration into proof.
 */

type CatalogueItem = {
  key: string
  title: string
  question: string
  shape: string
  needs: string
}

type StrategyRow = { strategy_id: string; name?: string; backtest_count?: number }

type ArtifactRow = {
  artifact_id: string
  analysis: string
  strategy_id: string
  backtest_id: string
  title: string
  shape: string
  created_at: string
  created_by: string
}

type DrilldownRow = {
  sequence: number
  trade_id: string
  entry_time: string
  side: string
  regime_label: string | null
  exit_reason: string
  net_pnl: number
  mfe: number | null
  mae: number | null
}

type Drilldown = {
  cell: { labels: string[]; trade_count: number; value: number | null; net_pnl: number }
  strategy_id: string
  returned_trades: number
  capped: boolean
  trades: DrilldownRow[]
  note: string
}

const MEASURES = [
  { key: 'average_trade', label: 'Average trade' },
  { key: 'net_pnl', label: 'Net P&L' },
  { key: 'win_rate', label: 'Win rate' },
  { key: 'trade_count', label: 'Trade count' },
] as const

export function ResearchLabWorkbench() {
  const queryClient = useQueryClient()
  const [strategyId, setStrategyId] = useState('')
  const [analysis, setAnalysis] = useState('by_hour')
  const [measure, setMeasure] = useState<string>('average_trade')
  const [hourBucket, setHourBucket] = useState(2)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Cell | null>(null)

  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyRow[]>('/strategies'),
  })
  const catalogue = useQuery({
    queryKey: ['lab-catalogue'],
    queryFn: () => getJson<CatalogueItem[]>('/lab/analyses'),
  })
  const artifacts = useQuery({
    queryKey: ['lab-artifacts'],
    queryFn: () => getJson<ArtifactRow[]>('/lab/artifacts?limit=40'),
  })

  const active = strategyId || strategies.data?.[0]?.strategy_id || ''

  const run = useMutation({
    mutationFn: () =>
      postJson<AnalysisResult>('/lab/run', {
        analysis,
        strategy_id: active,
        measure,
        hour_bucket: hourBucket,
      }),
    onMutate: () => {
      setError(null)
      setPicked(null)
    },
    onSuccess: (data) => {
      setResult(data)
      queryClient.invalidateQueries({ queryKey: ['lab-artifacts'] })
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'Analysis failed'),
  })

  const open = useMutation({
    mutationFn: (artifactId: string) =>
      getJson<AnalysisResult>(`/lab/artifacts/${artifactId}`),
    onSuccess: (data) => {
      setResult(data)
      setPicked(null)
      setError(null)
    },
  })

  const drill = useQuery({
    queryKey: ['lab-drill', result?.artifact_id, picked?.coords.join(',')],
    queryFn: () =>
      getJson<Drilldown>(
        `/lab/artifacts/${result?.artifact_id}/trades?coords=${picked?.coords.join(',')}&limit=300`,
      ),
    enabled: Boolean(result?.artifact_id && picked && result?.is_evidence === false),
    retry: false,
  })

  const question = useMemo(
    () => catalogue.data?.find((item) => item.key === analysis),
    [catalogue.data, analysis],
  )

  const onPick = useCallback((cell: Cell) => setPicked(cell), [])

  if (strategies.isPending || catalogue.isPending) {
    return (
      <div className="state" role="status">
        Loading the research lab…
      </div>
    )
  }
  if (!strategies.data?.length) {
    return (
      <div className="state" role="status">
        No strategies have been created yet. A research question needs a run to ask it of.
      </div>
    )
  }

  return (
    <div className="lab-view">
      <header className="lab-bar">
        <label className="ctl">
          <span>Strategy</span>
          <select value={active} onChange={(event) => setStrategyId(event.target.value)}>
            {strategies.data.map((row) => (
              <option key={row.strategy_id} value={row.strategy_id}>
                {row.name ?? row.strategy_id}
              </option>
            ))}
          </select>
        </label>

        <label className="ctl lab-question">
          <span>Question</span>
          <select value={analysis} onChange={(event) => setAnalysis(event.target.value)}>
            {(catalogue.data ?? []).map((item) => (
              <option key={item.key} value={item.key}>
                {item.question}
              </option>
            ))}
          </select>
        </label>

        <label className="ctl">
          <span>Measure</span>
          <select value={measure} onChange={(event) => setMeasure(event.target.value)}>
            {MEASURES.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        {analysis === 'hour_by_volatility' && (
          <label className="ctl">
            <span>Hours per slot</span>
            <select
              value={hourBucket}
              onChange={(event) => setHourBucket(Number(event.target.value))}
            >
              {[1, 2, 3, 4, 6].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        )}

        <button
          type="button"
          className="lab-run"
          onClick={() => run.mutate()}
          disabled={run.isPending || !active}
        >
          {run.isPending ? 'Running…' : 'Ask'}
        </button>
      </header>

      {question && <p className="lab-needs">Needs: {question.needs}</p>}

      {error && (
        <div className="state error" role="alert">
          {error}
        </div>
      )}

      <div className={picked ? 'lab-body with-drill' : 'lab-body'}>
        <div className="lab-main">
          {!result && !run.isPending && (
            <div className="state" role="status">
              Pick a question and press Ask. Every answer is computed from a backtest
              that already exists — nothing here runs a strategy or touches a holdout.
            </div>
          )}

          {result && (
            <section className="lab-result">
              <header>
                <div>
                  <h3>{result.title}</h3>
                  <p className="lab-sub">{result.question}</p>
                </div>
                <span className="lab-badge" title={result.evidence_note}>
                  NOT EVIDENCE
                </span>
              </header>

              <AnalysisChart result={result} onPick={onPick} height={400} />

              {result.findings.length > 0 && (
                <ul className="lab-findings">
                  {result.findings.map((finding) => (
                    <li key={finding}>{finding}</li>
                  ))}
                </ul>
              )}

              {result.warnings.filter(Boolean).map((warning) => (
                <p key={warning} className="lab-warning" role="status">
                  {warning}
                </p>
              ))}

              <footer className="lab-provenance mono">
                <span>{result.covered_trades.toLocaleString()} of {result.total_trades.toLocaleString()} trades</span>
                <span>run {String(result.provenance.backtest_id ?? '').slice(0, 18)}</span>
                <span>{String(result.provenance.dataset_key ?? '—')}</span>
                <span>{String(result.provenance.evidence_tier ?? '—')}</span>
                <span title="Content hash over inputs and outputs together">
                  {result.content_hash.slice(0, 12)}
                </span>
              </footer>
            </section>
          )}

          <section className="lab-history">
            <h4>Questions already asked</h4>
            {artifacts.data?.length ? (
              <table className="panel-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Question</th>
                    <th>Strategy</th>
                    <th>By</th>
                  </tr>
                </thead>
                <tbody>
                  {artifacts.data.map((row) => (
                    <tr key={row.artifact_id} onClick={() => open.mutate(row.artifact_id)}>
                      <td className="mono">{row.created_at.replace('T', ' ').slice(0, 16)}</td>
                      <td>{row.title}</td>
                      <td className="mono">{row.strategy_id.slice(0, 26)}</td>
                      <td className="mono">{row.created_by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="lab-empty">Nothing has been asked yet.</p>
            )}
          </section>
        </div>

        {picked && result && (
          <aside className="lab-drill" aria-label="Trades behind this cell">
            <header>
              <div>
                <span className="lab-eyebrow mono">{picked.labels.join(' · ')}</span>
                <h4>
                  {picked.trade_count.toLocaleString()} trade
                  {picked.trade_count === 1 ? '' : 's'}
                </h4>
              </div>
              <button type="button" onClick={() => setPicked(null)} aria-label="Close">
                ×
              </button>
            </header>

            {picked.insufficient && (
              <p className="lab-warning">
                Fewer than 20 trades. The P&amp;L below is what happened; the rates are
                not an estimate of anything.
              </p>
            )}

            {drill.isPending && <p className="lab-empty">Loading trades…</p>}
            {drill.isError && (
              <p className="lab-warning">{(drill.error as Error).message}</p>
            )}

            {drill.data && (
              <>
                {drill.data.capped && (
                  <p className="lab-warning">
                    This cell holds more trades than it stored ids for, so this is a
                    sample of it rather than all of it.
                  </p>
                )}
                <table className="panel-table lab-drill-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Entry</th>
                      <th>Regime</th>
                      <th>Exit</th>
                      <th className="num">P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drill.data.trades.map((trade) => (
                      <tr key={trade.trade_id}>
                        <td className="mono">{trade.sequence}</td>
                        <td className="mono">
                          {trade.entry_time.replace('T', ' ').slice(0, 16)}
                        </td>
                        <td className="mono">{trade.regime_label ?? '—'}</td>
                        <td className="mono">{trade.exit_reason}</td>
                        <td className={`num mono ${trade.net_pnl >= 0 ? 'up' : 'down'}`}>
                          {trade.net_pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="lab-note">{drill.data.note}</p>
                <a className="lab-link" href="#trades">
                  Open this strategy on the chart →
                </a>
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
