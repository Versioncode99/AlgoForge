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
import { CircleSlash, FileText, GitBranch, ListChecks, Scale, ShieldAlert, Users } from 'lucide-react'
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
  findings: Section
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

type Finding = {
  gate: string; name: string; dimension: string; severity: string
  status: string; headline: string; rule: string; observed: string | number
  why_it_matters: string; what_would_help: string
}

/* Ordered by how much it should change what you do next, which is not the same
 * as ordered by how bad it sounds. NOT_MEASURED sits below every real shortfall
 * and above nothing at all: a gate that measured nothing has found nothing
 * wrong, and painting it like a failure is exactly how INCONCLUSIVE stops
 * meaning what the architecture needs it to mean. */
const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: 'Decides it',
  HIGH: 'Real shortfall',
  NOT_MEASURED: 'Not measured',
  INFO: 'For information',
}

function Findings({ findings }: { findings: Finding[] }) {
  const [open, setOpen] = useState<string | null>(findings[0]?.gate ?? null)
  return <ol className="evidence-findings">
    {findings.map(item => {
      const expanded = open === item.gate
      return <li key={item.gate} className={`evidence-finding is-${item.severity.toLowerCase()}`}>
        <button
          type="button"
          className="evidence-finding-head"
          aria-expanded={expanded}
          onClick={() => setOpen(expanded ? null : item.gate)}
        >
          <span className="evidence-finding-sev">{SEVERITY_LABEL[item.severity] ?? item.severity}</span>
          <strong className="mono">{item.gate}</strong>
          <span className="evidence-finding-headline">{item.headline}</span>
          <em>{item.dimension.replace(/_/g, ' ')}</em>
        </button>
        {expanded && <div className="evidence-finding-body">
          <p><span>Why it matters</span>{item.why_it_matters}</p>
          {item.what_would_help && <p>
            <span>{item.status === 'INCONCLUSIVE' ? 'What would measure it' : 'What would help'}</span>
            {item.what_would_help}
          </p>}
          <small className="mono">{item.rule}</small>
        </div>}
      </li>
    })}
  </ol>
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
  const findings = d?.findings
  const provenance = d?.provenance
  const dissent = d?.dissent
  const claims = (dissent?.claims ?? []) as Array<Record<string, unknown>>
  const ancestors = (provenance?.ancestors ?? []) as Array<Record<string, unknown>>

  return <section className="stack evidence-view">
    <p className="view-note">
      One dossier per candidate: the verdict and its gate ladder, the lineage behind it, the
      specialist positions with their disagreement intact, and every limitation attached to a
      number. Sections with no data say so and give the reason.
    </p>

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

      <div className="panel">
        <PanelHead
          title="What decides it"
          meta={findings?.available
            ? `${String(findings.failed)} failed · ${String(findings.unmeasured)} unmeasured`
            : 'not available'}
        ><ListChecks /></PanelHead>
        {findings?.available
          ? <div className="panel-body stack">
              {/* The judge's own sentence, copied. Nothing on this screen
                  recomputes a decision, so nothing on it can be softer than
                  the gate ladder below. */}
              <p className="evidence-headline">{String(findings.headline)}</p>
              {(findings.findings as Finding[]).length
                ? <Findings findings={findings.findings as Finding[]} />
                : <p className="evidence-none">Every gate was measured and every one held. The
                    limitations below still apply.</p>}
            </div>
          : <Absent reason={findings?.reason} />}
      </div>

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
