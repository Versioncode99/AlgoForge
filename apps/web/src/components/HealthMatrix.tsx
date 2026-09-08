import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'

/* What each archive actually contains.
 *
 * The rule this component exists to enforce: a dataset is never called healthy
 * because it was paid for, or because its name says "16 years". Every cell here
 * is a measurement, and every measurement can be opened to show the arithmetic
 * that produced it. A dataset that cannot be measured says so rather than
 * showing a tick.
 */

type Finding = {
  code: string
  severity: 'ok' | 'note' | 'warn' | 'fail'
  summary: string
  detail: Record<string, unknown>
}

type HealthRow = {
  dataset: string
  measurable: boolean
  reason?: string
  status?: 'ok' | 'note' | 'warn' | 'fail'
  rows?: number
  first?: string
  last?: string
  span_days?: number
  symbol?: string
  interval?: string
  findings?: Finding[]
}

const LABEL: Record<string, string> = {
  ok: 'measured clean',
  note: 'minor',
  warn: 'attention',
  fail: 'failed',
}

/** The checks, in the order a person would want to read them. */
const ORDER = [
  'gaps',
  'duplicate_timestamps',
  'monotonic_timestamps',
  'ohlc_consistency',
  'positive_prices',
  'complete_bars',
  'volume',
  'timezone',
]

function Cell({ finding }: { finding: Finding | undefined }) {
  if (!finding) return <td className="hm-cell hm-none">—</td>
  return (
    <td className={`hm-cell hm-${finding.severity}`} title={finding.summary}>
      {finding.severity === 'ok' ? '✓' : finding.severity === 'note' ? '·' : finding.severity === 'warn' ? '!' : '✕'}
    </td>
  )
}

export function HealthMatrix() {
  const [open, setOpen] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ['data-health'],
    queryFn: () => getJson<HealthRow[]>('/data-health'),
    // Measuring millions of rows is cached server-side against the archive's
    // mtime, but it is still not something to re-request on every focus.
    staleTime: 10 * 60_000,
  })

  if (query.isPending) {
    return <div className="state" role="status">Measuring the archives…</div>
  }
  if (query.isError) {
    return <div className="state error" role="alert">{(query.error as Error).message}</div>
  }

  const rows = query.data ?? []
  const measurable = rows.filter((row) => row.measurable)

  return (
    <div className="health-matrix">
      <div className="table-scroll">
        <table className="data-table hm-table">
          <thead>
            <tr>
              <th>Dataset</th>
              <th className="num">Bars</th>
              <th>Coverage</th>
              {ORDER.map((code) => (
                <th key={code} className="hm-head" title={code}>
                  {code.replace(/_/g, ' ').replace('timestamps', 'ts')}
                </th>
              ))}
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const findings = row.findings ?? []
              const gaps = findings.find((f) => f.code === 'gaps')
              const coverage = gaps?.detail?.coverage as number | undefined
              return (
                <tr
                  key={row.dataset}
                  onClick={() => setOpen(open === row.dataset ? null : row.dataset)}
                  className={open === row.dataset ? 'is-open' : undefined}
                >
                  <td>
                    <strong>{row.dataset}</strong>
                    {row.symbol && <small className="table-note mono">{row.symbol} {row.interval}</small>}
                  </td>
                  <td className="num mono">{row.rows ? row.rows.toLocaleString() : '—'}</td>
                  <td className="mono">
                    {coverage === undefined ? '—' : `${(coverage * 100).toFixed(2)}%`}
                  </td>
                  {row.measurable ? (
                    ORDER.map((code) => (
                      <Cell key={code} finding={findings.find((f) => f.code === code)} />
                    ))
                  ) : (
                    <td className="hm-cell hm-none" colSpan={ORDER.length}>
                      {row.reason}
                    </td>
                  )}
                  <td>
                    <span className={`status-badge hm-badge hm-${row.status ?? 'none'}`}>
                      {row.measurable ? LABEL[row.status ?? 'ok'] : 'not measurable'}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* The evidence behind a row, on demand. A status with no arithmetic
          behind it would be an assertion. */}
      {open && (
        <div className="hm-detail">
          {(rows.find((r) => r.dataset === open)?.findings ?? []).map((finding) => (
            <div key={finding.code} className={`hm-finding hm-${finding.severity}`}>
              <strong>{finding.code.replace(/_/g, ' ')}</strong>
              <p>{finding.summary}</p>
              {Array.isArray(finding.detail?.largest) && (finding.detail.largest as unknown[]).length > 0 && (
                <ul className="mono">
                  {(finding.detail.largest as { starts: string; hours: number }[])
                    .slice(0, 5)
                    .map((gap) => (
                      <li key={gap.starts}>
                        {gap.starts.slice(0, 16).replace('T', ' ')} — {gap.hours.toFixed(1)}h
                      </li>
                    ))}
                </ul>
              )}
              {typeof finding.detail?.session_breaks === 'number' && (
                <p className="hm-note mono">
                  excluded as routine: {String(finding.detail.session_breaks)} daily breaks,{' '}
                  {String(finding.detail.weekend_breaks)} weekends,{' '}
                  {String(finding.detail.full_session_closures)} full-session closures
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="hm-legend">
        {measurable.length} of {rows.length} datasets measured from the archive on disk. The rest
        are fetched on demand and have no file to inspect. Click a row for the arithmetic.
      </p>
    </div>
  )
}
