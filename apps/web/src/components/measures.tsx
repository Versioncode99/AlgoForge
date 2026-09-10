import { PanelHead } from './ui'

/* The parts every measured screen is built from.
 *
 * Three ideas, shared so that the prop rule ladder, the risk measures and the
 * pre-trade gate all say the same thing the same way:
 *
 * A **measure** is a value, a ceiling and how it was arrived at. The method is
 * not a tooltip: "historical, 250 observations" and "parametric normal" are
 * different numbers and the reader has to be able to tell which one they are
 * looking at.
 *
 * **Absent is not zero.** `value === null` renders as an em dash with the reason
 * beside it, never as 0.00. Every component here takes `null` seriously because
 * every engine behind them reports it deliberately.
 *
 * A **buffer bar** shows headroom, not progress. It fills as the allowance is
 * *consumed*, so a wide bar is a problem and an empty one is comfort — the
 * opposite of a progress bar, which is why it is drawn differently.
 */

export type Tone = 'good' | 'warn' | 'bad' | 'unknown' | 'plain'

export function StatusPill({ label, tone, title }: { label: string; tone: Tone; title?: string }) {
  return (
    <span className="measure-pill" data-tone={tone} title={title}>
      {label}
    </span>
  )
}

export function Figure({
  value,
  decimals = 2,
  percent = false,
  currency = false,
  absent = '—',
}: {
  value: number | string | null | undefined
  decimals?: number
  percent?: boolean
  currency?: boolean
  absent?: string
}) {
  if (value === null || value === undefined || value === '') {
    return <span className="figure is-absent">{absent}</span>
  }
  if (typeof value === 'string') return <span className="figure">{value}</span>
  const shown = percent ? value * 100 : value
  return (
    <span className="figure">
      {currency && !percent ? '' : ''}
      {shown.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {percent ? '%' : ''}
    </span>
  )
}

/** How much of an allowance is used, drawn so that full means trouble. */
export function BufferBar({ headroom }: { headroom: number | null }) {
  if (headroom === null) return <div className="buffer-bar is-absent" aria-hidden="true" />
  const used = Math.max(0, Math.min(1, 1 - headroom))
  const tone = used >= 1 ? 'bad' : used >= 0.75 ? 'warn' : used >= 0.5 ? 'caution' : 'good'
  return (
    <div
      className="buffer-bar"
      data-tone={tone}
      role="img"
      aria-label={`${Math.round(used * 100)} percent of the allowance used`}
    >
      <span style={{ width: `${used * 100}%` }} />
    </div>
  )
}

export type MeasureRowProps = {
  label: string
  status?: { label: string; tone: Tone }
  observed: number | string | null
  limit?: number | string | null
  headroom?: number | null
  detail?: string
  method?: string
  decimals?: number
  percent?: boolean
}

export function MeasureRow({
  label,
  status,
  observed,
  limit,
  headroom,
  detail,
  method,
  decimals = 2,
  percent = false,
}: MeasureRowProps) {
  return (
    <tr className="measure-row">
      <th scope="row">
        <span>{label}</span>
        {method && <em>{method}</em>}
      </th>
      <td className="measure-value">
        <Figure value={observed} decimals={decimals} percent={percent} />
      </td>
      <td className="measure-limit">
        {limit === null || limit === undefined ? (
          <span className="figure is-absent">no limit</span>
        ) : (
          <Figure value={limit} decimals={decimals} percent={percent} />
        )}
      </td>
      <td className="measure-buffer">
        {headroom === undefined ? null : <BufferBar headroom={headroom} />}
      </td>
      <td className="measure-status">
        {status && <StatusPill label={status.label} tone={status.tone} />}
      </td>
      <td className="measure-detail">{detail}</td>
    </tr>
  )
}

export function MeasureTable({
  title,
  meta,
  children,
  head = true,
}: {
  title?: string
  meta?: React.ReactNode
  children: React.ReactNode
  head?: boolean
}) {
  return (
    <section className="measure-panel">
      {title && <PanelHead title={title} meta={meta} />}
      <table className="measure-table">
        {head && (
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Observed</th>
              <th scope="col">Limit</th>
              <th scope="col">Used</th>
              <th scope="col">Status</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
        )}
        <tbody>{children}</tbody>
      </table>
    </section>
  )
}

/** What a screen cannot tell you, printed where it would otherwise be assumed.
 *
 * Rendered as a list rather than a paragraph so that adding one is cheap: the
 * moment stating a limitation costs a rewrite, limitations stop being stated.
 */
export function Limitations({ items, title = 'What this does not tell you' }: { items: string[]; title?: string }) {
  if (!items.length) return null
  return (
    <section className="limitations">
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  )
}

/** A section with nothing behind it yet, saying what would fill it. */
export function NotMeasured({ what, why, action }: { what: string; why: string; action?: React.ReactNode }) {
  return (
    <div className="not-measured">
      <b>{what}</b>
      <p>{why}</p>
      {action}
    </div>
  )
}
