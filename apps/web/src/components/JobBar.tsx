import { CircleSlash, Loader } from 'lucide-react'
import { duration } from '../hooks/useJob'
import type { Job } from '../types'
import { Rolling } from './ui'

/** The one place this interface performs.
 *
 * A sixteen-year backtest is 4.8 million bars and several minutes. Without
 * this, the app is indistinguishable from hung. So the bar carries what is
 * actually happening — bars processed, the rate achieved, elapsed, and the
 * remaining estimate that rate implies — and a sweep runs across it while work
 * is genuinely in flight. It stops the moment the work does.
 */
export function JobBar({
  job,
  onCancel,
  onDismiss,
}: {
  job: Job
  onCancel: () => void
  onDismiss: () => void
}) {
  const running = job.status === 'RUNNING' || job.status === 'QUEUED'
  const rate = job.elapsed_seconds > 0 ? job.done / job.elapsed_seconds : 0
  const pct = Math.round(job.fraction * 100)

  return (
    <section className="jobbar" data-status={job.status.toLowerCase()} aria-live="polite">
      <div className="jobbar-fill" style={{ width: `${Math.max(pct, running ? 1.5 : 100)}%` }}>
        {running && <i className="jobbar-sweep" />}
      </div>

      <div className="jobbar-body">
        <span className="jobbar-icon">
          {running ? <Loader className="af-spin" size={13} /> : <CircleSlash size={13} />}
        </span>

        <span className="jobbar-label">{job.label}</span>

        <span className="jobbar-note">{job.note || job.status.toLowerCase()}</span>

        <span className="spacer" />

        <span className="jobbar-metric">
          <Rolling value={pct} suffix="%" className="mono" />
        </span>
        <span className="jobbar-metric" title="Bars processed of the requested window">
          <Rolling value={job.done} className="mono" />
          <em>/{job.total.toLocaleString()}</em>
        </span>
        {rate > 0 && (
          <span className="jobbar-metric" title="Bars per second achieved so far">
            <Rolling value={Math.round(rate)} className="mono" /> <em>bar/s</em>
          </span>
        )}
        <span className="jobbar-metric" title="Elapsed">
          <span className="mono">{duration(job.elapsed_seconds)}</span>
        </span>
        {running && job.eta_seconds !== null && (
          <span className="jobbar-metric" title="Estimated from the rate achieved so far">
            <em>eta</em> <span className="mono">{duration(job.eta_seconds)}</span>
          </span>
        )}

        {running ? (
          <button className="jobbar-btn af-press" onClick={onCancel}>
            Stop
          </button>
        ) : (
          <button className="jobbar-btn af-press" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>

      {job.status === 'FAILED' && job.error && <p className="jobbar-error">{job.error}</p>}
    </section>
  )
}
