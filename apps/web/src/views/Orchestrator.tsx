import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot, Check, ChevronRight, CircleAlert, ListChecks, Play, Rocket, Sparkles, Terminal, X,
} from 'lucide-react'
import { useState } from 'react'
import { getJson, postJson } from '../api'
import type { Mission, MissionSnapshot, MissionStep } from '../types'

/* The orchestrator.
 *
 * Every other specialist answers one question and stops, which left the
 * operator as the integration layer between "find a paper" and "test the idea
 * it suggests". A mission is that integration written down: an objective, a
 * plan, and steps that run in order and can read each other's results.
 *
 * The plan is shown before it runs, and can be run verbatim after it is read.
 * That matters more here than anywhere else in the app — these steps write
 * strategies to disk and spend compute, so approving *the plan you saw* rather
 * than a second, differently-planned one is the difference between a tool and a
 * slot machine. */

const EXAMPLES = [
  'Find literature on intraday volatility seasonality and build one testable candidate from it',
  'Register a session-structure template and measure it against the reserved validation slice',
  'Explain why the last ten candidates were rejected, then design one controlled ablation',
  'Survey which families have no templates yet and propose the cheapest one to implement',
]

const STATUS_TONE: Record<string, string> = {
  completed: 'good', partial: 'warn', running: 'live', planned: 'plain', failed: 'bad',
}

export function OrchestratorView() {
  const qc = useQueryClient()
  const [objective, setObjective] = useState('')
  const [draft, setDraft] = useState<Mission | null>(null)
  const [openMission, setOpenMission] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const snapshot = useQuery({
    queryKey: ['missions'], refetchInterval: 2500,
    queryFn: () => getJson<MissionSnapshot>('/missions'),
  })

  const plan = useMutation({
    mutationFn: () => postJson<Mission>('/missions', { objective, dry_run: true }),
    onSuccess: (m) => { setDraft(m); setError(null) },
    onError: (e: Error) => setError(e.message),
  })
  const launch = useMutation({
    mutationFn: (steps?: MissionStep[]) =>
      postJson<Mission>('/missions', {
        objective,
        steps: steps?.map((s) =>
          s.kind === 'agent'
            ? { agent: s.role, task: s.task, why: s.why }
            : { action: s.action, arguments: s.arguments ?? {}, why: s.why },
        ),
      }),
    onSuccess: (m) => {
      setDraft(null); setObjective(''); setError(null); setOpenMission(m.id)
      qc.invalidateQueries({ queryKey: ['missions'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const data = snapshot.data
  const missions = data?.missions ?? []
  const active = missions.find((m) => m.status === 'running')

  return (
    <section className="orch">
      <header className="orch-hero">
        <div>
          <p className="command-kicker"><span className="signal-dot" /> ORCHESTRATION</p>
          <h2>One objective.<br /><span>The whole team.</span></h2>
          <p>
            The orchestrator plans a sequence of actions and specialist assignments, then runs
            them in order. Later steps read earlier results, so "find a paper, build from it,
            measure it" is one instruction instead of four.
          </p>
        </div>
        <div className="orch-vitals">
          <div><Rocket /><strong>{missions.length}</strong><p>missions recorded</p></div>
          <div><ListChecks /><strong>{data?.actions.length ?? 0}</strong><p>actions available</p></div>
          <div><Bot /><strong>{data?.roles.length ?? 0}</strong><p>specialists dispatchable</p></div>
        </div>
      </header>

      {active && (
        <div className="orch-running af-panel-in">
          <span className="pulse" /> Mission running — <b>{active.objective}</b>
          <span className="spacer" />
          {active.steps.filter((s) => s.status === 'completed').length} / {active.steps.length} steps
        </div>
      )}

      <div className="orch-launch">
        <label htmlFor="objective">What should the team do?</label>
        <textarea
          id="objective" rows={3} maxLength={600} value={objective}
          placeholder="State an objective, not a step. The plan is yours to read before anything runs."
          onChange={(e) => setObjective(e.target.value)}
        />
        <div className="orch-launch-row">
          <button className="btn af-press" disabled={objective.trim().length < 8 || plan.isPending}
            onClick={() => plan.mutate()}>
            <Sparkles size={13} /> {plan.isPending ? 'Planning…' : 'Plan it'}
          </button>
          <button className="btn primary af-press"
            disabled={objective.trim().length < 8 || launch.isPending || !!active}
            onClick={() => launch.mutate(undefined)}>
            <Play size={13} /> {launch.isPending ? 'Launching…' : 'Plan and run'}
          </button>
          <span className="muted">
            {active ? 'A mission is already running; missions are sequential.' : 'Nothing runs until you press run.'}
          </span>
        </div>
        <div className="orch-examples">
          {EXAMPLES.map((example) => (
            <button key={example} className="btn tiny af-press" onClick={() => setObjective(example)}>
              {example}
            </button>
          ))}
        </div>
        {error && <p className="command-error" role="alert">{error}</p>}
      </div>

      {draft && (
        <div className="orch-plan af-panel-in">
          <header>
            <div>
              <p className="command-kicker">PROPOSED PLAN</p>
              <h3>{draft.steps.length} steps, planned by {draft.plan_source}
                {draft.plan_model ? ` · ${draft.plan_model}` : ''}</h3>
            </div>
            <button className="icon-btn af-press" aria-label="Discard plan" onClick={() => setDraft(null)}>
              <X size={13} />
            </button>
          </header>
          <p className="orch-rationale">{draft.plan_rationale}</p>
          {draft.plan_note && <p className="warning">{draft.plan_note}</p>}
          <ol className="plan-steps">
            {draft.steps.map((step) => (
              <li key={step.index} className="af-row-in" style={{ animationDelay: `${step.index * 40}ms` }}>
                <span className="plan-index">{String(step.index).padStart(2, '0')}</span>
                <div>
                  <strong>
                    {step.kind === 'agent' ? `${step.role} specialist` : step.action}
                    <span className="chip">{step.kind}</span>
                  </strong>
                  <p>{step.why || '—'}</p>
                  <code>{step.kind === 'agent' ? step.task : JSON.stringify(step.arguments ?? {})}</code>
                </div>
              </li>
            ))}
          </ol>
          <div className="orch-launch-row">
            <button className="btn primary af-press" disabled={launch.isPending || !!active}
              onClick={() => launch.mutate(draft.steps)}>
              <Check size={13} /> Run this exact plan
            </button>
            <span className="muted">Running it again from the objective would produce a different plan.</span>
          </div>
        </div>
      )}

      <section className="orch-history">
        <div className="command-section-head">
          <div><p className="command-kicker">MISSION LOG</p><h3>What was asked, and what came back.</h3></div>
        </div>
        {!missions.length && (
          <div className="command-empty">
            <Rocket /><h4>No missions yet.</h4>
            <p>State an objective above. The plan appears before anything runs.</p>
          </div>
        )}
        {missions.map((mission) => (
          <MissionCard
            key={mission.id} mission={mission}
            open={openMission === mission.id}
            onToggle={() => setOpenMission(openMission === mission.id ? null : mission.id)}
          />
        ))}
      </section>

      <ActionRegistry snapshot={data} />
    </section>
  )
}

function MissionCard({
  mission, open, onToggle,
}: { mission: Mission; open: boolean; onToggle: () => void }) {
  const done = mission.steps.filter((s) => s.status === 'completed').length
  const failed = mission.steps.filter((s) => s.status === 'failed').length
  return (
    <article className="mission-card af-panel-in" data-open={open ? 'yes' : undefined}>
      <button className="mission-head af-press" onClick={onToggle} aria-expanded={open}>
        <ChevronRight className="mission-caret" size={14} />
        <span className={`mission-status tone-${STATUS_TONE[mission.status] ?? 'plain'}`}>
          {mission.status}
        </span>
        <b>{mission.objective}</b>
        <span className="spacer" />
        <small className="mono">{done}/{mission.steps.length} steps{failed ? ` · ${failed} failed` : ''}</small>
        <small className="muted">{mission.plan_source}</small>
      </button>
      {open && (
        <div className="mission-body">
          <p className="orch-rationale">{mission.plan_rationale}</p>
          {mission.plan_note && <p className="warning">{mission.plan_note}</p>}
          <ol className="plan-steps">
            {mission.steps.map((step) => (
              <li key={step.index} data-status={step.status}>
                <span className="plan-index">{String(step.index).padStart(2, '0')}</span>
                <div>
                  <strong>
                    {step.kind === 'agent' ? `${step.role} specialist` : step.action}
                    <span className={`chip ${step.status === 'failed' ? 'warn' : step.status === 'completed' ? 'good' : ''}`}>
                      {step.status}
                    </span>
                  </strong>
                  <p>{step.why || '—'}</p>
                  {step.status !== 'pending' && (
                    <code className={step.error ? 'bad' : undefined}>
                      {step.error ?? step.summary ?? '—'}
                    </code>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {mission.outcome && <p className="mission-outcome">{mission.outcome}</p>}
        </div>
      )}
    </article>
  )
}

function ActionRegistry({ snapshot }: { snapshot?: MissionSnapshot }) {
  const [open, setOpen] = useState(false)
  const actions = snapshot?.actions ?? []
  const recent = snapshot?.recent_actions ?? []
  return (
    <section className="action-registry">
      <div className="command-section-head">
        <div>
          <p className="command-kicker">THE VERB SET</p>
          <h3>Everything the orchestrator is allowed to do.</h3>
        </div>
        <button className="btn af-press" onClick={() => setOpen(!open)}>
          <Terminal size={13} /> {open ? 'Hide' : 'Show'} {actions.length} actions
        </button>
      </div>
      <p className="muted">
        The set is fixed. A plan cannot introduce a verb, and each action validates its own
        arguments — which is why a refusal here carries a reason instead of a stack trace.
      </p>
      {open && (
        <div className="action-grid">
          {actions.map((action, i) => (
            <article key={action.name} className="action-card af-row-in"
              style={{ animationDelay: `${Math.min(i, 12) * 25}ms` }}>
              <header>
                <code>{action.name}</code>
                {action.mutating && <span className="chip warn">writes</span>}
              </header>
              <p>{action.description}</p>
              <footer>
                {Object.keys(action.parameters.properties).length === 0
                  ? <small className="muted">no arguments</small>
                  : Object.entries(action.parameters.properties).map(([key, spec]) => (
                    <small key={key} className={action.parameters.required.includes(key) ? 'req' : undefined}>
                      {key}: {String((spec as { type?: string }).type ?? '?')}
                    </small>
                  ))}
              </footer>
            </article>
          ))}
        </div>
      )}
      {!!recent.length && (
        <ul className="action-log">
          {recent.slice(0, 12).map((row, i) => (
            <li key={`${row.at}-${i}`} data-ok={row.ok ? 'yes' : 'no'}>
              {row.ok ? <Check size={11} /> : <CircleAlert size={11} />}
              <code>{row.action}</code>
              <span>{row.ok ? JSON.stringify(row.result).slice(0, 180) : row.error}</span>
              <small className="mono">{row.elapsed_seconds.toFixed(2)}s</small>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
