/* The search, as a record rather than a feed.
 *
 * The engine's activity log answers "what is happening now". This answers the
 * two questions that outlive a session: what has been tried, and where did any
 * particular candidate come from.
 *
 * Failures are first-class here, not an error state. Most candidates are
 * rejected — that is the system working — so the list leads with the failure
 * class rather than hiding it, and INCONCLUSIVE is shown as its own outcome
 * because "nothing was disproven" and "this failed" are different results. */
import { useQuery } from '@tanstack/react-query'
import { FlaskConical, GitBranch, Layers } from 'lucide-react'
import { useState } from 'react'
import { getJson } from '../api'
import { PanelHead, Stat } from '../components/ui'
import type { ExperimentRecord } from '../types'

// Shared so the global search can index experiments from the same shape.
type Experiment = ExperimentRecord

type Lineage = {
  experiment: Experiment | null
  ancestors: Experiment[]
  children: Experiment[]
  descendants: Experiment[]
}

const OUTCOME_TONE: Record<string, 'good' | 'bad' | undefined> = {
  passed: 'good',
  rejected: 'bad',
  no_trades: 'bad',
}

const settings = (row: Experiment) =>
  Object.entries(row.parameters ?? {})
    .map(([name, value]) => `${name}=${value}`)
    .join(' · ')

function Row({
  row,
  selected,
  onSelect,
}: {
  row: Experiment
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={`experiment-row${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
    >
      <div className="experiment-row-head">
        <strong className="mono">{row.template}</strong>
        {row.policy && <em>{row.policy}</em>}
        <span className={`experiment-status is-${(row.status ?? 'reserved').toLowerCase()}`}>
          {row.status ?? 'reserved'}
        </span>
      </div>
      <small className="mono">{settings(row) || 'no parameters'}</small>
      {row.failure_class && (
        <small className="experiment-failure">
          {row.failure_class}
          {row.failure_gate ? ` · ${row.failure_gate}` : ''}
        </small>
      )}
    </button>
  )
}

export function ExperimentsView({ mode = 'experiments' }: { mode?: 'experiments' | 'lineage' }) {
  const [selected, setSelected] = useState('')
  const [rootsOnly, setRootsOnly] = useState(false)

  const experiments = useQuery({
    queryKey: ['experiments', rootsOnly],
    queryFn: () =>
      getJson<Experiment[]>(`/experiments?limit=200${rootsOnly ? '&roots_only=true' : ''}`),
  })
  const lineage = useQuery({
    queryKey: ['lineage', selected],
    enabled: !!selected,
    queryFn: () => getJson<Lineage>(`/experiments/${selected}/lineage`),
  })

  const rows = experiments.data ?? []
  const line = lineage.data
  const subject = line?.experiment

  const byStatus = rows.reduce<Record<string, number>>((counts, row) => {
    const key = row.status ?? 'reserved'
    counts[key] = (counts[key] ?? 0) + 1
    return counts
  }, {})

  return (
    <section className="stack experiments-view">
      <p className="view-note">{mode === 'lineage'
        ? 'Each experiment records the one it was derived from, so a result can be traced back to the question that produced it.'
        : 'Every candidate the engine forms is recorded here with its hypothesis, its verdict and the line it came from — failures included.'}</p>

      <div className="headline-row">
        <Stat label="Experiments" value={<span className="mono">{rows.length}</span>}
          note={rootsOnly ? 'roots only' : 'most recent first'} />
        <Stat label="Rejected" value={<span className="mono">{byStatus.rejected ?? 0}</span>}
          note="most candidates are rejected; that is the system working" />
        <Stat label="Inconclusive" value={<span className="mono">{byStatus.inconclusive ?? 0}</span>}
          note="nothing disproven — evidence never produced" />
        <Stat label="Passed" value={<span className="mono">{byStatus.passed ?? 0}</span>}
          tone={byStatus.passed ? 'good' : undefined} note="qualified, not profitable" />
      </div>

      <div className="validation-toolbar panel">
        <label className="experiment-toggle">
          <input
            type="checkbox"
            checked={rootsOnly}
            onChange={event => setRootsOnly(event.target.checked)}
          />
          Roots only — where each line of enquiry started
        </label>
      </div>

      {!experiments.isPending && rows.length === 0 && (
        <div className="panel validation-empty">
          <FlaskConical />
          <h3>NO EXPERIMENTS</h3>
          <p>
            The engine records one experiment per candidate it forms. Start it from Overview
            and they will appear here.
          </p>
        </div>
      )}

      {rows.length > 0 && (
        <div className="grid-2">
          <div className="panel">
            <PanelHead title="Experiments" meta={`${rows.length} in this dataset`}>
              <Layers />
            </PanelHead>
            <div className="panel-body experiment-list">
              {rows.map(row => (
                <Row
                  key={row.id}
                  row={row}
                  selected={row.id === selected}
                  onSelect={() => setSelected(row.id)}
                />
              ))}
            </div>
          </div>

          <div className="panel">
            <PanelHead title="Lineage" meta={subject ? subject.template : 'select an experiment'}>
              <GitBranch />
            </PanelHead>
            {!subject ? (
              <div className="panel-body evidence-absent">
                <div>
                  <strong>Nothing selected</strong>
                  <p>Choose an experiment to see what it came from and what followed it.</p>
                </div>
              </div>
            ) : (
              <div className="panel-body stack">
                <div className="validation-metrics">
                  <div><span>Policy</span><strong>{subject.policy ?? '—'}</strong></div>
                  <div><span>Status</span>
                    <strong className={OUTCOME_TONE[subject.status ?? ''] === 'bad' ? 'bad' : ''}>
                      {subject.status ?? 'reserved'}
                    </strong>
                  </div>
                  <div><span>Seed</span><strong className="mono">{subject.seed ?? '—'}</strong></div>
                  <div><span>Descendants</span>
                    <strong>{line?.descendants.length ?? 0}</strong>
                  </div>
                </div>

                {subject.failure_reason && (
                  <p className="warning">
                    {subject.failure_class ?? 'FAILED'}: {subject.failure_reason}
                  </p>
                )}

                <ol className="evidence-lineage">
                  {[...(line?.ancestors ?? [])].reverse().map(row => (
                    <li key={row.id}>
                      <span className="mono">{settings(row) || row.template}</span>
                      <small>{row.policy ?? 'root'} · {row.status ?? 'reserved'}</small>
                    </li>
                  ))}
                  <li className="is-current">
                    <span className="mono">{settings(subject) || subject.template}</span>
                    <small>this experiment</small>
                  </li>
                  {(line?.children ?? []).map(row => (
                    <li key={row.id}>
                      <span className="mono">{settings(row) || row.template}</span>
                      <small>derived · {row.status ?? 'reserved'}</small>
                    </li>
                  ))}
                </ol>

                {(line?.ancestors.length ?? 0) === 0 && (
                  <p className="experiment-note">
                    A root: nothing derived this one. It began a line of enquiry rather than
                    continuing one.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
