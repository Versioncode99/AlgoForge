import { useQuery } from '@tanstack/react-query'
import { ArchiveX, BrainCircuit } from 'lucide-react'
import { getJson } from '../api'
import { PanelHead, Stat } from '../components/ui'
import { stamp } from '../lib'
import type { ResearchMemoryPayload } from '../types'

export function ResearchMemoryView() {
  const query = useQuery({ queryKey: ['research-memory'], queryFn: () => getJson<ResearchMemoryPayload>('/memory?limit=250') })
  const data = query.data
  if (query.isPending) return <div className="state" role="status">Reading append-only research memory…</div>
  if (query.isError || !data) return <div className="state error" role="alert">Research memory unavailable{query.error ? `: ${query.error.message}` : '.'}</div>
  const counts = Object.entries(data.counts)
  return <section className="stack records-view">
    <p className="view-note">A judged failure that generalises prunes the region around it, so no compute is spent there twice. Absence of evidence never prunes anything.</p>
    <div className="headline-row"><Stat label="Recorded constraints" value={<span className="mono">{data.total}</span>} note={data.scope} />{counts.slice(0, 3).map(([label, value]) => <Stat key={label} label={label.replaceAll('_', ' ')} value={<span className="mono">{value}</span>} note="classified failures" />)}</div>
    <div className="panel"><PanelHead title="Search constraints" meta={`${data.constraints.length} most recent`}><BrainCircuit /></PanelHead>
      {!data.constraints.length ? <div className="panel-body evidence-absent"><ArchiveX /><div><strong>No learned constraints</strong><p>Judged failures will appear here and stop nearby candidates consuming compute.</p></div></div> :
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Template</th><th>Class</th><th>Gate</th><th>Reason</th><th>Strategy</th><th>Recorded</th></tr></thead><tbody>{data.constraints.map((row, index) => <tr key={`${row.template}-${row.created_at}-${index}`}><td className="mono">{row.template}</td><td><span className="status-badge is-failed">{row.failure_class}</span></td><td className="mono">{row.gate ?? '—'}</td><td>{row.reason}</td><td className="mono">{row.strategy_id ?? '—'}</td><td className="mono">{row.created_at ? stamp(row.created_at) : '—'}</td></tr>)}</tbody></table></div>}
    </div>
  </section>
}
