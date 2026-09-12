import { useQuery } from '@tanstack/react-query'
import { Database, Radio, ShieldAlert } from 'lucide-react'
import { getJson } from '../api'
import { PanelHead, Stat } from '../components/ui'
import { HealthMatrix } from '../components/HealthMatrix'
import { ServiceHealth } from '../components/ServiceHealth'
import type { DatasetInfo } from '../types'

export function DataWorkspaceView() {
  const query = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  const rows = query.data ?? []
  const real = rows.filter(row => row.is_real && row.available).length
  const loaded = rows.filter(row => row.loaded).length
  if (query.isPending) return <div className="state" role="status">Inspecting dataset capabilities…</div>
  if (query.isError) return <div className="state error" role="alert">Dataset registry unavailable: {query.error.message}</div>
  return <section className="stack records-view">
    <p className="view-note">What each dataset covers, where it came from, and whether it can carry evidence. A synthetic set can never clear the judge's data gate.</p>
    <div className="headline-row"><Stat label="Registered" value={<span className="mono">{rows.length}</span>} note="dataset definitions" /><Stat label="Loaded" value={<span className="mono">{loaded}</span>} note="available this session" /><Stat label="Real available" value={<span className="mono">{real}</span>} tone={real ? 'good' : undefined} note="provider-backed, not synthetic" /></div>
    <div className="panel"><PanelHead title="Dataset registry" meta="absence is reported, never coerced to zero"><Database /></PanelHead>
      {!rows.length ? <div className="panel-body evidence-absent"><ShieldAlert /><div><strong>No datasets registered</strong><p>Configure a provider or import a dataset before starting evidence-producing work.</p></div></div> :
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Dataset</th><th>Symbol</th><th>Interval</th><th>Provider</th><th>Authority</th><th className="num">Bars</th><th className="num">Span</th><th>Status</th></tr></thead><tbody>{rows.map(row => <tr key={row.key}><td><strong>{row.label}</strong><small className="table-note mono">{row.key}</small></td><td className="mono">{row.symbol}</td><td className="mono">{row.interval}</td><td>{row.provider}</td><td>{row.authority}</td><td className="mono num">{row.loaded ? row.bar_count.toLocaleString() : '—'}</td><td className="mono num">{row.span_years ? `${row.span_years.toFixed(1)}y` : '—'}</td><td><span className={`status-badge ${row.is_real && row.available ? 'is-passed' : row.available ? 'is-reserved' : 'is-failed'}`}>{row.loaded ? 'LOADED' : row.available ? row.is_real ? 'REAL READY' : 'SYNTHETIC' : 'BLOCKED DATA'}</span></td></tr>)}</tbody></table></div>}
    </div>
    <div className="panel"><PanelHead title="Integrity, measured" meta="computed from the archive on disk, not from the dataset's name"><ShieldAlert /></PanelHead>
      <div className="panel-body"><HealthMatrix /></div>
    </div>
    {/* An archive can be sound while the source that feeds it is refusing, and
      * the two were never shown together. Each row opens to what is wrong, why,
      * what it stops, and the remedy. */}
    <div className="panel"><PanelHead title="Sources" meta="whether each one answered, and what this build does not have at all"><Radio /></PanelHead>
      <div className="panel-body"><ServiceHealth /></div>
    </div>
  </section>
}
