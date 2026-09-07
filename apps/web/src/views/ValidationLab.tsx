import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Grid3X3, Play, Route, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson, postJson } from '../api'
import { PanelHead, Stat } from '../components/ui'
import { pct } from '../lib'
import type { DatasetInfo, StrategyListItem, ValidationEvidence } from '../types'

type StoredValidation = ValidationEvidence & {
  calculation_version?: string; code_hash?: string; split_id?: string
}

const colour = (value: number, low: number, high: number) => {
  const span = Math.max(high - low, 1e-9)
  const t = Math.max(0, Math.min(1, (value - low) / span))
  if (t < .5) return `rgba(193,80,63,${.22 + (.5 - t) * .7})`
  return `rgba(162,230,93,${.18 + (t - .5) * .9})`
}

function HeatStrip({ values, label }: { values: number[]; label: string }) {
  const low = Math.min(...values, 0)
  const high = Math.max(...values, 0)
  return <div className="validation-heat">
    <div><span>{label}</span><small>{values.length} observations · red weak, lime strong</small></div>
    <div className="heat-cells" role="img" aria-label={`${label}, ${values.length} values`}>
      {values.map((value, index) => <i key={index} style={{ background: colour(value, low, high) }} title={`${index + 1}: ${value.toFixed(3)}`} />)}
    </div>
  </div>
}

export function ValidationLabView() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState('')
  const [dataset, setDataset] = useState('')
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  useEffect(() => { if (!selected && strategies.data?.length) setSelected(strategies.data[0].strategy_id) }, [selected, strategies.data])
  useEffect(() => {
    if (!dataset && datasets.data?.length) setDataset((datasets.data.find(item => item.is_real && item.available) ?? datasets.data[0]).key)
  }, [dataset, datasets.data])
  const evidence = useQuery({
    queryKey: ['validation-evidence', selected], enabled: !!selected,
    queryFn: () => getJson<StoredValidation | null>(`/strategies/${selected}/validation`),
  })
  const run = useMutation({
    mutationFn: () => postJson<StoredValidation>(`/strategies/${selected}/validate`, { dataset, years: 1 }),
    onSuccess: data => {
      qc.setQueryData(['validation-evidence', selected], data)
      qc.invalidateQueries({ queryKey: ['activity'] })
    },
  })
  const e = evidence.data
  const wf = e?.walk_forward
  const paths = e?.paths
  return <section className="stack validation-lab">
    <p className="view-note">
      Selection risk, temporal stability and path risk, each shown as the estimate it is.
      Below eight distinct configurations the deflated Sharpe and the probability of
      backtest overfitting report nothing rather than a confident number.
    </p>
    <div className="validation-toolbar panel">
      <label>Strategy<select value={selected} onChange={event => setSelected(event.target.value)}>
        {(strategies.data ?? []).map(item => <option key={item.strategy_id} value={item.strategy_id}>{item.name}</option>)}
      </select></label>
      <label>Real dataset<select value={dataset} onChange={event => setDataset(event.target.value)}>
        {(datasets.data ?? []).map(item => <option key={item.key} value={item.key} disabled={!item.is_real || !item.available}>{item.label}{!item.is_real ? ' · synthetic blocked' : ''}</option>)}
      </select></label>
      <button className="btn primary af-press" disabled={!selected || !dataset || run.isPending} onClick={() => run.mutate()}><Play />{run.isPending ? 'Validating…' : 'Run WF + CSCV + CPCV'}</button>
    </div>
    <p className="warning">CPCV reconstructs alternate out-of-sample paths from chronological groups; it is not a Monte Carlo account simulation. The Prop Firm workspace runs the separate five-day block-bootstrap Monte Carlo and shows pass, ruin, drawdown and terminal-distribution risk.</p>
    {run.error && <p className="command-error" role="alert">{run.error.message}</p>}
    {!e && !evidence.isPending && <div className="panel validation-empty"><ShieldCheck /><h3>NOT_TESTED</h3><p>This strategy has no stored selection-bias receipt. Run validation on real data; synthetic bars are refused.</p></div>}
    {e && wf && paths && <>
      <div className="headline-row">
        <Stat label="PBO" value={<span className="mono">{pct(e.probability_of_overfitting)}</span>} tone={e.probability_of_overfitting < .5 ? 'good' : 'bad'} note={`${e.cscv_splits} symmetric splits`} />
        <Stat label="Walk-forward efficiency" value={<span className="mono">{wf.efficiency.toFixed(2)}</span>} tone={wf.efficiency >= .5 ? 'good' : 'bad'} note={`${wf.positive_folds}/${wf.fold_count} folds positive`} />
        <Stat label="CPCV path p05" value={<span className="mono">{paths.sharpe_p05.toFixed(3)}</span>} tone={paths.sharpe_p05 > 0 ? 'good' : 'bad'} note={`${paths.paths} reconstructed paths`} />
        <Stat label="Selection stability" value={<span className="mono">{pct(e.selection_stability)}</span>} tone={e.selection_stability >= .5 ? 'good' : 'bad'} note={`${e.trial_count} parameter trials`} />
      </div>
      <div className="grid-2">
        <div className="panel"><PanelHead title="Walk-forward" meta="chronological · purged"><div className="validation-icon"><Route /></div></PanelHead><div className="panel-body validation-metrics">
          <div><span>In-sample Sharpe</span><strong>{wf.in_sample_sharpe.toFixed(3)}</strong></div><div><span>Out-of-sample Sharpe</span><strong>{wf.out_of_sample_sharpe.toFixed(3)}</strong></div><div><span>Consistency</span><strong>{pct(wf.consistency)}</strong></div><div><span>Degradation</span><strong>{wf.degradation.toFixed(3)}</strong></div>
        </div></div>
        <div className="panel"><PanelHead title="Path robustness" meta="CPCV · not Monte Carlo"><div className="validation-icon"><Activity /></div></PanelHead><div className="panel-body validation-metrics">
          <div><span>Median Sharpe</span><strong>{paths.median_sharpe.toFixed(3)}</strong></div><div><span>5–95% Sharpe</span><strong>{paths.sharpe_p05.toFixed(2)} → {paths.sharpe_p95.toFixed(2)}</strong></div><div><span>Positive paths</span><strong>{pct(paths.positive_share)}</strong></div><div><span>Dispersion</span><strong>{paths.dispersion.toFixed(3)}</strong></div>
        </div></div>
      </div>
      <div className="panel"><PanelHead title="Selection heatmaps" meta="hover for exact values"><Grid3X3 /></PanelHead><div className="panel-body stack">
        <HeatStrip label="Trial Sharpe distribution" values={e.trial_sharpes ?? []} /><HeatStrip label="CSCV out-of-sample rank logits" values={e.cscv_logits ?? []} />
      </div></div>
    </>}
  </section>
}
