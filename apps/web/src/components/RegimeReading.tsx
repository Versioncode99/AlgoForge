import { useState } from 'react'

/* What the grid says, with the numbers it said it from still attached.
 *
 * Every sentence here was produced by arithmetic in `forge.analytics.reading`,
 * not by a model. That matters for one specific reason: a model asked to
 * summarise a four-cell table produces fluent sentences in every case,
 * including the cases where the table supports nothing — and a sentence that
 * reads the same whether or not the evidence exists is worse than no sentence,
 * because it cannot be told apart from one that was earned.
 *
 * So this component renders findings; it does not compose them. It cannot add
 * one, drop one, or change a number, and the evidence behind each is one click
 * away rather than a separate page — which is what makes the claim checkable
 * instead of merely readable.
 */

export type Evidence = {
  label: string
  value: number
  display: string
  detail: string
}

export type Finding = {
  kind: string
  standing: 'MEASURED' | 'THIN' | 'DESCRIPTIVE'
  fact: string
  interpretation: string
  implication: string
  evidence: Evidence[]
  regimes: string[]
}

export type Reading = {
  findings: Finding[]
  standing: 'MEASURED' | 'THIN' | 'DESCRIPTIVE'
  total_trades: number
  classified_trades: number
  attribution: string
  series_fingerprint: string
}

/** What each standing means, spelled out where it is shown.
 *
 * Three states rather than a confidence percentage, because the distinctions
 * are categorical: a thin sample is not a weak measurement and a descriptive
 * label is not an uncertain one. A number would let them average.
 */
const STANDING: Record<Finding['standing'], { label: string; detail: string }> = {
  MEASURED: {
    label: 'Measured',
    detail:
      'Enough trades to estimate, on labels built only from bars before the one being labelled.',
  },
  THIN: {
    label: 'Thin sample',
    detail:
      'The arithmetic is exact. The sample behind it is too small to generalise from.',
  },
  DESCRIPTIVE: {
    label: 'Descriptive',
    detail:
      'Volatility thresholds came from the whole series, so the labels used information later ' +
      'than the bars they label. This describes results already produced; it is not evidence ' +
      'about what the strategy would have done.',
  },
}

const HEADING: Record<string, string> = {
  SOURCE: 'Where the money came from',
  DRAG: 'Where it was lost',
  MISMATCH: 'Time spent without being paid for it',
  PERSISTENCE: 'Whether the condition lasts',
  UNTESTED: 'Where this has not been tried',
  COVERAGE: 'What the classifier could not place',
}

function FindingCard({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false)
  const standing = STANDING[finding.standing]
  return (
    <article className={`rr-finding ${finding.standing.toLowerCase()}`}>
      <header>
        <h4>{HEADING[finding.kind] ?? finding.kind}</h4>
        <span className="rr-standing" title={standing.detail}>
          {standing.label}
        </span>
      </header>

      {/* Fact first and on its own line: it is the only part that contains no
          inference, and it should be readable without the rest. */}
      <p className="rr-fact">{finding.fact}</p>
      <p className="rr-interpretation">{finding.interpretation}</p>
      <p className="rr-implication">{finding.implication}</p>

      <button
        type="button"
        className="rr-toggle"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        {open ? 'Hide the numbers' : `The numbers (${finding.evidence.length})`}
      </button>

      {open && (
        <dl className="rr-evidence">
          {finding.evidence.map((item) => (
            <div key={`${item.label}-${item.display}`}>
              <dt>{item.label}</dt>
              <dd className="mono">{item.display}</dd>
              {item.detail && <dd className="rr-detail">{item.detail}</dd>}
            </div>
          ))}
        </dl>
      )}
    </article>
  )
}

export function RegimeReading({ reading }: { reading: Reading }) {
  if (!reading.findings.length) {
    return (
      <div className="state" role="status">
        This run supports no reading: there are not enough classified trades in any condition to
        say anything about where its result came from.
      </div>
    )
  }

  const worst = STANDING[reading.standing]
  return (
    <section className="regime-reading">
      <header className="rr-head">
        <h3>What the grid says</h3>
        <span className="rr-standing headline" title={worst.detail}>
          {worst.label}
        </span>
        <span className="mono rr-scope">
          {reading.classified_trades} of {reading.total_trades} trades placed · attributed on{' '}
          {reading.attribution}
        </span>
      </header>

      {/* The weakest standing travels with the summary, not only with the
          finding that earned it. A reader who stops at the headline has still
          been told what the headline can support. */}
      <p className="rr-standing-note">{worst.detail}</p>

      <div className="rr-list">
        {reading.findings.map((finding) => (
          <FindingCard key={finding.kind} finding={finding} />
        ))}
      </div>
    </section>
  )
}
