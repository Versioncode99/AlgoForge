/* The dossier, made readable.
 *
 * One question drives the whole view: why does AlgoForge trust or reject this
 * candidate? Everything shown is copied from GET /strategies/{id}/dossier — the
 * view computes nothing, because a screen that recalculated a number could
 * disagree with the Validation Lab and then neither would be believable.
 *
 * The design rule is that absence is shown, not hidden. A section the backend
 * marks unavailable renders its stated reason rather than disappearing or
 * showing zeroes: "never measured" and "measured as zero" are different facts
 * and the reader has to be able to tell them apart. */
import { useQuery } from '@tanstack/react-query'
import { CircleSlash, FileText, GitBranch, Scale, ShieldAlert, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getJson } from '../api'
import { PanelHead, Stat } from '../components/ui'
import type { StrategyListItem } from '../types'

type Section = { available: boolean; reason?: string; [key: string]: unknown }
type Dossier = {
  strategy_id: string
  scope: string
  strategy: Section
  backtests: Section
  verdict: Section
  validation: Section
  provenance: Section
  research_memory: Section
  dissent: Section
  limitations: string[]
  standing_caveats: string[]
}

const STANCE_TONE: Record<string, 'good' | 'bad' | undefined> = {
  SUPPORT: 'good',
  OPPOSE: 'bad',
  CAUTION: undefined,
}

/** An unavailable section states why, in the backend's own words. */
function Absent({ reason }: { reason?: string }) {
  return <div className="panel-body evidence-absent">
    <CircleSlash />
    <div>
      <strong>Not available</strong>
      <p>{reason ?? 'No reason recorded.'}</p>
    </div>
  </div>
}

function Gates({ gates }: { gates: Array<Record<string, unknown>> }) {
  return <div className="evidence-gates">
    {gates.map(gate => {
      const status = String(gate.status)
      return <div key={String(gate.gate)} className={`evidence-gate is-${status.toLowerCase()}`}>
        <div className="evidence-gate-head">
          <strong>{String(gate.gate)}</strong>
          <span>{String(gate.name)}</span>
          <em>{status}</em>
        </div>
        <p className="mono">{String(gate.observed)}</p>
        <small>{String(gate.rule)}</small>
      </div>
    })}
  </div>
}

export function EvidenceView() {
  const [selected, setSelected] = useState('')
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyListItem[]>('/strategies'),
  })
  useEffect(() => {
    if (!selected && strategies.data?.length) setSelected(strategies.data[0].strategy_id)
  }, [selected, strategies.data])

  const dossier = useQuery({
    queryKey: ['dossier', selected],
    enabled: !!selected,
    queryFn: () => getJson<Dossier>(`/strategies/${selected}/dossier`),
  })

  const d = dossier.data
  const verdict = d?.verdict
  const provenance = d?.provenance
  const dissent = d?.dissent
  const claims = (dissent?.claims ?? []) as Array<Record<string, unknown>>
  const ancestors = (provenance?.ancestors ?? []) as Array<Record<string, unknown>>

  return <section className="stack evidence-view">
    <div className="section-title">
      <p>PROVENANCE · VERDICT · DISSENT · LIMITATIONS</p>
      <h2>Why this candidate is trusted, or is not.</h2>
    </div>

    <div className="validation-toolbar panel">
      <label>Strategy<select value={selected} onChange={event => setSelected(event.target.value)}>
        {(strategies.data ?? []).map(item =>
          <option key={item.strategy_id} value={item.strategy_id}>{item.name}</option>)}
      </select></label>
    </div>

    {!strategies.isPending && !strategies.data?.length &&
      <div className="panel validation-empty"><FileText /><h3>NO STRATEGIES</h3>
        <p>Write one from a template, or start the engine, then come back.</p></div>}

    {d && <>
      {verdict?.available
        ? <div className="headline-row">
            <Stat label="Decision" value={<span className="mono">{String(verdict.decision)}</span>}
              tone={verdict.decision === 'PASS' ? 'good' : verdict.decision === 'FAIL' ? 'bad' : undefined}
              note={`grade ${String(verdict.grade)}`} />
            <Stat label="Gates failed" value={<span className="mono">{(verdict.failed_gates as string[]).length}</span>}
              tone={(verdict.failed_gates as string[]).length ? 'bad' : 'good'}
              note={(verdict.failed_gates as string[]).join(', ') || 'none'} />
            <Stat label="Never measured" value={<span className="mono">{(verdict.unmeasured_gates as string[]).length}</span>}
              note={(verdict.unmeasured_gates as string[]).join(', ') || 'none'} />
            <Stat label="Trades" value={<span className="mono">{String((verdict.metrics as Record<string, unknown>).trades)}</span>}
              note={`net ${String((verdict.metrics as Record<string, unknown>).net_pnl)}`} />
          </div>
        : <div className="panel"><PanelHead title="Verdict" meta="not available"><Scale /></PanelHead>
            <Absent reason={verdict?.reason} /></div>}

      <div className="grid-2">
        <div className="panel">
          <PanelHead title="Where it came from" meta="lineage"><GitBranch /></PanelHead>
          {provenance?.available
            ? <div className="panel-body stack">
                <div className="validation-metrics">
                  <div><span>Policy</span><strong>{String(provenance.policy ?? '—')}</strong></div>
                  <div><span>Depth</span><strong>{String(provenance.depth)}</strong></div>
                  <div><span>Descendants</span><strong>{String(provenance.descendant_count)}</strong></div>
                  <div><span>Seed</span><strong className="mono">{String(provenance.seed ?? '—')}</strong></div>
                </div>
                <ol className="evidence-lineage">
                  {[...ancestors].reverse().map(row =>
                    <li key={String(row.id)}>
                      <span className="mono">{String(row.template)}</span>
                      <small>{String(row.policy ?? 'root')} · {String(row.status ?? 'unknown')}</small>
                    </li>)}
                  <li className="is-current">
                    <span className="mono">{String((d.strategy as Record<string, unknown>).template)}</span>
                    <small>this candidate</small>
                  </li>
                </ol>
              </div>
            : <Absent reason={provenance?.reason} />}
        </div>

        <div className="panel">
          <PanelHead title="What the search already knows" meta="research memory"><ShieldAlert /></PanelHead>
          {d.research_memory.available
            ? <div className="panel-body stack">
                <div className="validation-metrics">
                  <div><span>Related failures</span><strong>{String(d.research_memory.related_failures)}</strong></div>
                  <div><span>Template</span><strong className="mono">{String(d.research_memory.template)}</strong></div>
                </div>
                <ul className="evidence-failures">
                  {Object.entries(d.research_memory.by_class as Record<string, number>).map(([name, count]) =>
                    <li key={name}><span className="mono">{name}</span><strong>{count}</strong></li>)}
                </ul>
              </div>
            : <Absent reason={d.research_memory.reason} />}
        </div>
      </div>

      <div className="panel">
        <PanelHead title="Gate ladder" meta="PASS · FAIL · INCONCLUSIVE"><Scale /></PanelHead>
        {verdict?.available
          ? <div className="panel-body"><Gates gates={verdict.gates as Array<Record<string, unknown>>} /></div>
          : <Absent reason={verdict?.reason} />}
      </div>

      <div className="panel">
        <PanelHead title="Specialist review" meta="disagreement preserved"><Users /></PanelHead>
        {dissent?.available
          ? <div className="panel-body evidence-claims">
              {claims.map(claim =>
                <div key={String(claim.role_id)} className={`evidence-claim is-${String(claim.stance).toLowerCase()}`}>
                  <div className="evidence-claim-head">
                    <strong>{String(claim.role_id)}</strong>
                    <em className={STANCE_TONE[String(claim.stance)] === 'bad' ? 'bad' : ''}>{String(claim.stance)}</em>
                    <span className="mono">{Number(claim.confidence).toFixed(2)}</span>
                  </div>
                  <p>{String(claim.statement)}</p>
                </div>)}
            </div>
          : <Absent reason={dissent?.reason} />}
      </div>

      <div className="panel">
        <PanelHead title="What these numbers cannot tell you" meta="stated limitations"><FileText /></PanelHead>
        <div className="panel-body stack">
          <ul className="evidence-limitations">
            {d.limitations.map(note => <li key={note}>{note}</li>)}
          </ul>
          <ul className="evidence-limitations is-standing">
            {d.standing_caveats.map(note => <li key={note}>{note}</li>)}
          </ul>
        </div>
      </div>
    </>}
  </section>
}
