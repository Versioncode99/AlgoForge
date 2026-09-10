import { useState } from 'react'
import { StatusPill, type Tone } from '../components/measures'
import { NotMeasured } from '../components/measures'
import { PanelHead, Stat } from '../components/ui'
import { useAudit, type AuditEntry } from '../fund'

/* The AI operating log.
 *
 * Not a chat window. §27's point, and the right one: an assistant that occupies
 * the screen stops being a tool and starts being the application. What a person
 * actually needs from an assistant running in the background is a legible record
 * of what it did — one line per action, in order, with refusals in it.
 *
 *   04:12  ai   backtest_strategy      OK       preparatory
 *   04:15  ai   construct_portfolio    OK       preparatory
 *   04:15  ai   submit_orders          HELD     reaches the book
 *   04:16  ai   set_fund_config        DENIED   protected control
 *
 * Refusals are the important half. A log holding only successes would show an
 * assistant that tried forty times to raise a limit as an assistant that did
 * nothing, which is the opposite of what happened.
 */

const OUTCOME_LABEL: Record<string, string> = {
  ok: 'OK',
  error: 'FAILED',
  denied: 'DENIED',
  pending_approval: 'HELD',
  blocked: 'BLOCKED',
}

const OUTCOME_TONE: Record<string, Tone> = {
  ok: 'good',
  error: 'bad',
  denied: 'bad',
  pending_approval: 'warn',
  blocked: 'bad',
}

export function OperatingLogView() {
  const [actorFilter, setActorFilter] = useState<'all' | 'ai' | 'human'>('all')
  const audit = useAudit(200)

  if (audit.isPending) return <div className="state" role="status">Reading the operating log…</div>

  const entries = audit.data?.entries ?? []
  const shown = actorFilter === 'all' ? entries : entries.filter((entry) => entry.actor === actorFilter)
  const summary = audit.data?.summary ?? {}
  const byAi = entries.filter((entry) => entry.actor === 'ai').length

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Recorded" value={entries.length} note="most recent first" />
        <Stat label="By assistant" value={byAi} />
        <Stat label="Refused" value={(summary.denied ?? 0) + (summary.blocked ?? 0)} tone={(summary.denied ?? 0) ? 'bad' : 'plain'} />
        <Stat label="Held for you" value={summary.pending_approval ?? 0} tone={(summary.pending_approval ?? 0) ? 'warn' : 'plain'} />
        <Stat label="Failed" value={summary.error ?? 0} tone={(summary.error ?? 0) ? 'bad' : 'plain'} />
      </section>

      <section className="measure-panel">
        <PanelHead title="Operating log" meta="what ran, what was held, and what was refused">
          <div className="log-filter" role="group" aria-label="Filter by actor">
            {(['all', 'ai', 'human'] as const).map((option) => (
              <button
                key={option}
                data-selected={actorFilter === option ? 'yes' : undefined}
                onClick={() => setActorFilter(option)}
              >
                {option === 'all' ? 'Everything' : option === 'ai' ? 'Assistant' : 'You'}
              </button>
            ))}
          </div>
        </PanelHead>

        {shown.length === 0 ? (
          <NotMeasured
            what="Nothing recorded yet"
            why="Every action call lands here — the ones that ran, the ones held for you, and the ones the policy refused."
          />
        ) : (
          <ol className="operating-log">
            {shown.map((entry) => (
              <LogLine key={entry.entry_id} entry={entry} />
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

function LogLine({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false)
  const detail = entry.error || entry.ruling_reason
  return (
    <li className="log-line" data-actor={entry.actor} data-outcome={entry.outcome}>
      <button onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <time className="mono">{entry.at.slice(11, 19)}</time>
        <span className="log-actor">{entry.actor === 'ai' ? 'assistant' : 'you'}</span>
        <span className="log-action mono">{entry.action}</span>
        <StatusPill
          label={OUTCOME_LABEL[entry.outcome] ?? entry.outcome.toUpperCase()}
          tone={OUTCOME_TONE[entry.outcome] ?? 'plain'}
        />
        <span className="log-reason">{detail}</span>
      </button>
      {open && (
        <div className="log-detail">
          <dl>
            <dt>Origin</dt>
            <dd>{entry.origin || 'not stated'}</dd>
            <dt>Mode</dt>
            <dd>
              {entry.mode}
              {entry.stance ? ` · ${entry.stance.replace(/_/g, ' ')}` : ''}
            </dd>
            <dt>Ruling</dt>
            <dd>{entry.ruling}</dd>
            {entry.approval_id && (
              <>
                <dt>Approval</dt>
                <dd className="mono">{entry.approval_id}</dd>
              </>
            )}
          </dl>
          {entry.arguments && (
            <>
              <h4>Arguments</h4>
              <pre className="fund-pre">{entry.arguments}</pre>
            </>
          )}
          {entry.result && (
            <>
              <h4>Result</h4>
              <pre className="fund-pre">{entry.result}</pre>
            </>
          )}
        </div>
      )}
    </li>
  )
}
