import { Activity, AlertTriangle, CircleSlash, Clock, Cpu, Pause, PlugZap } from 'lucide-react'
import type { EngineStatus, RuntimeSnapshot, RuntimeState } from '../types'

/* What the engine is actually doing.
 *
 * This component exists because of one defect. The interface used to render a
 * boolean called `running`, and that boolean meant "worker threads exist". A
 * search that had exhausted its campaign, condemned every template it could
 * reach, or was refusing every proposal as a duplicate said RUNNING for as long
 * as it was left alone, with a number beside it — "Skipped by memory: 1,290" —
 * that summed five unrelated situations and could diagnose none of them.
 *
 * So nothing here reads `running`. Everything reads the derived state, and the
 * derived state is only RUNNING when a worker has made progress inside the
 * no-progress window. When it is not, this says which of the eleven other
 * things it is, why, and what to do about it. */

type Tone = 'good' | 'warn' | 'bad' | 'plain'

/** How each state should read. Deliberately not a traffic light: an exhausted
 *  campaign is a *result*, not a fault, and colouring it red teaches operators
 *  to treat finishing as failing. */
export const RUNTIME_META: Record<RuntimeState, { label: string; tone: Tone; note: string }> = {
  STARTING: { label: 'Starting', tone: 'plain', note: 'Workers are loading data.' },
  RUNNING: { label: 'Running', tone: 'good', note: 'Workers are processing work.' },
  PAUSED: { label: 'Paused', tone: 'plain', note: 'Held by you. Nothing is lost.' },
  IDLE: { label: 'Idle', tone: 'warn', note: 'Alive, but nothing has progressed.' },
  WAITING_FOR_WORK: {
    label: 'Waiting for work', tone: 'warn',
    note: 'Asking for work and getting nothing back.',
  },
  WAITING_FOR_DATA: {
    label: 'Waiting for data', tone: 'warn', note: 'Blocked on a dataset.',
  },
  WAITING_FOR_AGENT: {
    label: 'Waiting for an agent', tone: 'warn', note: 'Blocked on a model or specialist.',
  },
  BLOCKED: {
    label: 'Blocked', tone: 'bad', note: 'Something is stopping work that will not clear itself.',
  },
  EXHAUSTED: {
    label: 'Exhausted', tone: 'plain',
    note: 'The research reachable from here has been searched out.',
  },
  STOPPING: { label: 'Stopping', tone: 'plain', note: 'Finishing the current cycle.' },
  STOPPED: { label: 'Stopped', tone: 'plain', note: 'Not running.' },
  ERROR: { label: 'Error', tone: 'bad', note: 'The engine could not continue.' },
  RECOVERING: { label: 'Recovering', tone: 'warn', note: 'Reclaiming work left by a failed run.' },
}

const ICONS: Record<RuntimeState, typeof Activity> = {
  STARTING: PlugZap, RUNNING: Activity, PAUSED: Pause, IDLE: Clock,
  WAITING_FOR_WORK: Clock, WAITING_FOR_DATA: Clock, WAITING_FOR_AGENT: Clock,
  BLOCKED: CircleSlash, EXHAUSTED: CircleSlash, STOPPING: Pause, STOPPED: Pause,
  ERROR: AlertTriangle, RECOVERING: Cpu,
}

export function runtimeStateOf(engine: EngineStatus | undefined): RuntimeState {
  return engine?.runtime?.state ?? engine?.runtime_state ?? (engine?.running ? 'STARTING' : 'STOPPED')
}

/** The badge. Small, and never says RUNNING unless the engine is working. */
export function RuntimeBadge({ engine, compact = false }: {
  engine: EngineStatus | undefined
  compact?: boolean
}) {
  const state = runtimeStateOf(engine)
  const meta = RUNTIME_META[state]
  const Icon = ICONS[state]
  const reason = engine?.runtime?.reason ?? engine?.runtime_reason ?? ''
  return (
    <span className="runtime-badge" data-tone={meta.tone} data-state={state} title={reason || meta.note}>
      <Icon aria-hidden="true" />
      <strong>{meta.label}</strong>
      {!compact && reason && <span className="runtime-badge-reason">{reason}</span>}
    </span>
  )
}

/** The full panel: state, why, what to do, and the heartbeat of every worker.
 *
 *  This is the answer to "why isn't my research running?" without a terminal. */
export function RuntimeDiagnostics({ engine, runtime }: {
  engine?: EngineStatus
  runtime?: RuntimeSnapshot
}) {
  const snapshot = runtime ?? engine?.runtime
  if (!snapshot) {
    return (
      <section className="runtime-panel is-empty">
        <p>Runtime diagnostics are not available. The API has not reported a state yet.</p>
      </section>
    )
  }
  const meta = RUNTIME_META[snapshot.state]
  const Icon = ICONS[snapshot.state]
  const blockers = Object.entries(snapshot.blockers ?? {})
  const outcomes = Object.entries(snapshot.outcome_counts ?? {}).filter(([, n]) => n > 0)

  return (
    <section className="runtime-panel" aria-label="Engine runtime diagnostics">
      <header className="runtime-head" data-tone={meta.tone}>
        <Icon aria-hidden="true" />
        <div>
          <strong>{meta.label}</strong>
          <p>{snapshot.reason || meta.note}</p>
        </div>
        {snapshot.seconds_without_progress != null && (
          <dl className="runtime-clock">
            <dt>No progress for</dt>
            <dd>{formatSeconds(snapshot.seconds_without_progress)}</dd>
          </dl>
        )}
      </header>

      {snapshot.remedy && (
        <p className="runtime-remedy"><strong>What to try:</strong> {snapshot.remedy}</p>
      )}

      {blockers.length > 0 && (
        <ul className="runtime-blockers">
          {blockers.map(([key, reason]) => (
            <li key={key}><code>{key}</code><span>{reason}</span></li>
          ))}
        </ul>
      )}

      {outcomes.length > 0 && (
        <div className="runtime-outcomes">
          <h4>Recent cycle outcomes</h4>
          <ul>
            {outcomes.map(([outcome, count]) => (
              <li key={outcome} data-outcome={outcome}>
                <span>{outcome.replace(/_/g, ' ').toLowerCase()}</span>
                <strong>{count}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="runtime-workers">
        <h4>Workers <span>{snapshot.workers.length}</span></h4>
        {!snapshot.workers.length && <p className="muted">No worker has reported yet.</p>}
        {snapshot.workers.length > 0 && (
          <table>
            <thead>
              <tr>
                <th scope="col">Worker</th><th scope="col">Stage</th>
                <th scope="col">Cycles</th><th scope="col">Progressed</th>
                <th scope="col">Barren</th><th scope="col">Last beat</th>
                <th scope="col">Health</th>
              </tr>
            </thead>
            <tbody>
              {snapshot.workers.map((worker) => (
                <tr key={worker.worker_id} data-stale={worker.stale || undefined} data-dead={worker.dead || undefined}>
                  <th scope="row">{worker.worker_id}</th>
                  <td>{worker.stage}</td>
                  <td className="num">{worker.cycles}</td>
                  <td className="num">{worker.progressed}</td>
                  <td className="num">{worker.barren}</td>
                  <td className="num">{formatSeconds(worker.seconds_since_beat)}</td>
                  <td>
                    {worker.dead ? <b className="bad">dead</b>
                      : worker.stale ? <b className="warn">stale</b>
                      : worker.paused ? <b>paused</b>
                      : <b className="good">alive</b>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

/** The skip breakdown, which replaces a single misleading number.
 *
 *  `useful` declined an experiment that would otherwise have run: real compute
 *  saved. `wasted` refused nothing, because there was nothing to refuse — those
 *  cycles are a symptom, and the old figure added them to the saved total. */
export function SkipAccounting({ engine }: { engine: EngineStatus | undefined }) {
  const skips = engine?.skips
  const rows: { label: string; value: number; tone: Tone; note: string }[] = [
    {
      // Not "duplicates". Most refusals in this bucket are restatements of
      // existing research rather than byte-identical repeats, and calling nine
      // hundred of them duplicates when nineteen are exact is the same kind of
      // overclaim the old single counter made. The novelty breakdown beside
      // this says which is which.
      label: 'Already answered', value: skips?.useful ?? engine?.compute_saved ?? 0,
      tone: 'good',
      note: 'Proposals refused because the research already exists — an identical experiment, a disproven region, or a restatement of a claim on the frontier. Compute genuinely saved. See the novelty breakdown for how close each one was.',
    },
    {
      label: 'Cycles with nothing to do', value: skips?.wasted ?? engine?.skipped_without_work ?? 0,
      tone: 'warn',
      note: 'The frontier returned nothing, a capability was missing, or a proposal raised. These saved no compute and mean the search needs redirecting.',
    },
    {
      label: 'Ended on a stopping rule', value: skips?.by_kind?.CAMPAIGN_EXHAUSTED ?? engine?.skipped_exhausted ?? 0,
      tone: 'plain',
      note: 'The campaign reached a budget or target. A result, not a fault.',
    },
  ]
  return (
    <dl className="skip-accounting">
      {rows.map((row) => (
        <div key={row.label} data-tone={row.tone}>
          <dt title={row.note}>{row.label}</dt>
          <dd>{row.value.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  )
}

function formatSeconds(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)}s`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}
