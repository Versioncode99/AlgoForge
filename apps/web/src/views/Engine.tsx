import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, Database, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson, postJson } from '../api'
import type { DatasetInfo, EngineStatus } from '../types'

/** Start/stop control for the autonomous engine, plus its live counters.
 *  This is the answer to "nothing is happening": once started it creates
 *  strategies, backtests them and judges them on its own. */
export function EnginePanel() {
  const qc = useQueryClient()
  const [dataset, setDataset] = useState('mnq_1m_3mo')
  const [error, setError] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ['engine'],
    queryFn: () => getJson<EngineStatus>('/engine'),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : 6000),
  })
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })

  useEffect(() => {
    const configured = status.data?.config?.dataset
    if (configured) setDataset(configured)
  }, [status.data?.config?.dataset])

  const refresh = () => {
    for (const key of ['engine', 'strategies', 'summary', 'activity', 'datasets']) {
      qc.invalidateQueries({ queryKey: [key] })
    }
  }

  const start = useMutation({
    mutationFn: () => postJson<EngineStatus>('/engine/start', { dataset, cycle_seconds: 6, max_strategies: 60 }),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const stop = useMutation({
    mutationFn: () => postJson<EngineStatus>('/engine/stop'),
    onSuccess: refresh,
    onError: (e: Error) => setError(e.message),
  })

  const s = status.data
  const running = !!s?.running
  const active = datasets.data?.find((d) => d.key === dataset)

  return (
    <div className="engine">
      <div className="engine-bar">
        <div className="engine-state">
          <i className={running ? 'pulse' : 'pulse is-off'} />
          <div>
            <span>AUTONOMOUS ENGINE</span>
            <strong>{running ? s?.current_stage.toUpperCase() : 'STOPPED'}</strong>
          </div>
        </div>

        <label className="field">
          <Database size={11} /> Dataset
          <select value={dataset} disabled={running} onChange={(e) => setDataset(e.target.value)}>
            {datasets.data?.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label} · {d.is_real ? d.provider : 'synthetic'} · {d.cost_note}
              </option>
            ))}
          </select>
        </label>

        {running ? (
          <button className="btn danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
            <CircleStop /> Stop
          </button>
        ) : (
          <button className="btn primary" onClick={() => start.mutate()} disabled={start.isPending}>
            <Play /> {start.isPending ? 'Starting…' : 'Start engine'}
          </button>
        )}
      </div>

      {active && !active.is_real && (
        <p className="warning">
          Synthetic dataset selected — the judge's G0 data gate cannot pass, by design. Pick a real
          provider dataset to produce evidence that counts.
        </p>
      )}
      {error && <p className="warning bad">{error}</p>}

      <div className="engine-counters">
        <Counter label="Cycles" value={s?.cycles ?? 0} />
        <Counter label="Created" value={s?.created ?? 0} />
        <Counter label="Backtested" value={s?.backtested ?? 0} />
        <Counter label="Judged" value={s?.judged ?? 0} />
        <Counter label="Validation pass" value={s?.validation_passed ?? 0} tone={s?.validation_passed ? 'good' : undefined} />
        <Counter label="Holdout pass" value={s?.holdout_passed ?? 0} tone={s?.holdout_passed ? 'good' : undefined} />
        <Counter label="Rejected" value={s?.rejected ?? 0} tone="bad" />
        <Counter label="Retired lineages" value={s?.lineages_retired ?? 0} tone="warn" />
        <Counter label="Engine errors" value={s?.engine_errors ?? 0} tone={s?.engine_errors ? 'bad' : undefined} />
      </div>

      {s?.last_error && <p className="warning bad">Last error: {s.last_error}</p>}
    </div>
  )
}

function Counter({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="counter">
      <span>{label}</span>
      <strong className={tone}>{value.toLocaleString()}</strong>
    </div>
  )
}
