import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'

/* Carrying a strategy to another platform, with the crossing accounted for.
 *
 * The surface is deliberately not "generate code". A button that produces a
 * file and says nothing else is the shape of the problem: somebody pastes it
 * into TradingView, it compiles, and they trade a strategy that is not the one
 * they validated. So what this shows first is the *ledger of differences* —
 * what mapped natively, what mapped with a caveat, what has no representation
 * at all — and the code comes after it.
 *
 * The status is the loudest thing on the panel, and it is capped by what the
 * machine could actually check. A strategy that maps perfectly to Pine reads
 * STRUCTURAL and not VERIFIED, because nothing ran it.
 */

type Fidelity = 'equivalent' | 'approximated' | 'unsupported'
type Status = 'verified' | 'structural' | 'approximate' | 'incomplete'

type Element = {
  part: string
  name: string
  fidelity: Fidelity
  detail: string
  rendered: string
}

type PortReport = {
  target: string
  definition_id: string
  definition_hash: string
  status: Status
  code: string
  elements: Element[]
  notes: string[]
  counts: { equivalent: number; approximated: number; unsupported: number }
}

type Target = {
  key: string
  label: string
  generates: boolean
  ceiling: Status
  language: string
  note: string
}

/** What a status means, in the words that keep it from being over-read.
 *
 * The gap between STRUCTURAL and VERIFIED is the whole point of the panel: one
 * says the pieces line up, the other says somebody ran both and compared the
 * trades. Rendered as adjacent green badges they would be read as the same
 * thing.
 */
const STATUS: Record<Status, { label: string; detail: string }> = {
  verified: {
    label: 'Verified',
    detail:
      'The generated code was executed against the same bars as the definition and the trade ' +
      'ledgers matched. This is the only status that means the logic survived.',
  },
  structural: {
    label: 'Structural',
    detail:
      'Every element maps to a native construct with the same meaning. Nothing executed it, so ' +
      'this is a claim about the pieces, not about a run.',
  },
  approximate: {
    label: 'Approximate',
    detail:
      'At least one element is expressible only with a difference that can change results. Each ' +
      'one is listed below with what the difference is.',
  },
  incomplete: {
    label: 'Incomplete',
    detail:
      'At least one element has no representation on this platform. Whatever was generated is a ' +
      'starting point and not the strategy.',
  },
}

const FIDELITY: Record<Fidelity, string> = {
  equivalent: 'Equivalent',
  approximated: 'Approximated',
  unsupported: 'Unsupported',
}

function Elements({ elements }: { elements: Element[] }) {
  const [showEquivalent, setShowEquivalent] = useState(false)
  const imperfect = elements.filter((element) => element.fidelity !== 'equivalent')
  const clean = elements.filter((element) => element.fidelity === 'equivalent')
  const shown = showEquivalent ? [...imperfect, ...clean] : imperfect

  return (
    <div className="port-elements">
      {/* What did not cross leads. The elements that mapped cleanly are the
          ones nobody needs to read, and putting them first would bury the
          three lines that decide whether this port is usable. */}
      {imperfect.length === 0 && (
        <p className="port-clean">Every element mapped natively.</p>
      )}
      <ul>
        {shown.map((element) => (
          <li key={`${element.part}:${element.name}`} className={element.fidelity}>
            <span className="pe-fidelity">{FIDELITY[element.fidelity]}</span>
            <span className="pe-part">{element.part}</span>
            <strong>{element.name}</strong>
            {element.detail && <p className="pe-detail">{element.detail}</p>}
          </li>
        ))}
      </ul>
      {clean.length > 0 && (
        <button
          type="button"
          className="port-toggle"
          aria-expanded={showEquivalent}
          onClick={() => setShowEquivalent((was) => !was)}
        >
          {showEquivalent
            ? 'Hide what crossed cleanly'
            : `Show the ${clean.length} that crossed cleanly`}
        </button>
      )}
    </div>
  )
}

export function PortPanel({ strategyId }: { strategyId: string }) {
  const [target, setTarget] = useState('pine')

  const targets = useQuery({
    queryKey: ['port-targets'],
    queryFn: () => getJson<{ targets: Target[] }>('/strategies/port-targets'),
    staleTime: Infinity,
  })
  const report = useQuery({
    queryKey: ['port', strategyId, target],
    queryFn: () => getJson<PortReport>(`/strategies/${strategyId}/port/${target}`),
    enabled: Boolean(strategyId),
    retry: false,
  })

  const chosen = targets.data?.targets.find((item) => item.key === target)

  return (
    <section className="port-panel">
      <div className="port-targets" role="group" aria-label="Port target">
        {(targets.data?.targets ?? []).map((item) => (
          <button
            key={item.key}
            type="button"
            className={item.key === target ? 'port-target active' : 'port-target'}
            aria-pressed={item.key === target}
            onClick={() => setTarget(item.key)}
          >
            <strong>{item.label}</strong>
            {/* Stated on the chooser, not discovered after picking: a target
                that is analysed rather than generated is a different offer. */}
            <span>{item.generates ? 'generated' : 'analysed only'}</span>
          </button>
        ))}
      </div>

      {chosen && <p className="port-note">{chosen.note}</p>}

      {report.isPending && (
        <p className="state" role="status">
          Analysing the crossing…
        </p>
      )}

      {report.isError && (
        <div className="state error" role="alert">
          {(report.error as Error).message}
        </div>
      )}

      {report.data && (
        <>
          <header className="port-head">
            <span className={`port-status ${report.data.status}`} title={STATUS[report.data.status].detail}>
              {STATUS[report.data.status].label}
            </span>
            <span className="port-counts mono">
              {report.data.counts.equivalent} equivalent · {report.data.counts.approximated}{' '}
              approximated · {report.data.counts.unsupported} unsupported
            </span>
          </header>
          <p className="port-status-note">{STATUS[report.data.status].detail}</p>

          <Elements elements={report.data.elements} />

          {report.data.notes.map((note) => (
            <p key={note} className="port-caveat">
              {note}
            </p>
          ))}

          {report.data.code ? (
            <details className="port-code">
              <summary>Generated {chosen?.label ?? report.data.target}</summary>
              <pre>
                <code>{report.data.code}</code>
              </pre>
            </details>
          ) : (
            <p className="port-nocode">
              No file is produced for this target. The report above is the analysis; a generator
              nobody can check is one nobody should trade from.
            </p>
          )}
        </>
      )}
    </section>
  )
}
