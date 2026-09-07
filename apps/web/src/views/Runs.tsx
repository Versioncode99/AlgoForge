import { useQuery } from '@tanstack/react-query'
import { Fingerprint, PlaySquare } from 'lucide-react'
import { getJson } from '../api'
import { PanelHead, TierPill } from '../components/ui'
import { stamp } from '../lib'
import type { Run } from '../types'

const short = (value: string) => value ? `${value.slice(0, 10)}…${value.slice(-6)}` : '—'

export function RunsView() {
  const query = useQuery({ queryKey: ['runs'], queryFn: () => getJson<Run[]>('/runs') })
  const rows = query.data ?? []
  if (query.isPending) return <div className="state" role="status">Loading immutable run records…</div>
  if (query.isError) return <div className="state error" role="alert">Run ledger unavailable: {query.error.message}</div>
  return <section className="stack records-view">
    <p className="view-note">Each run binds a preregistration to the source, data, cost model and engine version that produced it. A run is evidence only at its declared tier.</p>
    <div className="panel">
      <PanelHead title="Run ledger" meta={`${rows.length} final records`}><PlaySquare /></PanelHead>
      {!rows.length ? <div className="panel-body evidence-absent"><Fingerprint /><div><strong>No run records</strong><p>Freeze a preregistration and execute it; its hashes will be recorded here.</p></div></div> :
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Run</th><th>Tier</th><th>Preregistration</th><th>Source</th><th>Data</th><th>Cost</th><th>Engine</th><th>Created</th></tr></thead><tbody>
        {rows.map(row => <tr key={row.run_id}><td className="mono" title={row.run_id}>{short(row.run_id)}</td><td><TierPill tier={row.tier} /></td><td className="mono" title={row.preregistration_hash}>{short(row.preregistration_hash)}</td><td className="mono" title={row.source_hash}>{short(row.source_hash)}</td><td className="mono" title={row.data_hash}>{short(row.data_hash)}</td><td className="mono" title={row.cost_hash}>{short(row.cost_hash)}</td><td className="mono">{row.engine_version}</td><td className="mono">{stamp(row.created_at)}</td></tr>)}
      </tbody></table></div>}
    </div>
  </section>
}
