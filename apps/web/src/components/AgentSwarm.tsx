import { Brain, FlaskConical, Gavel, Moon, ShieldCheck, Skull } from 'lucide-react'
import type { EngineStatus } from '../types'

/** What each search worker is doing, right now.
 *
 * The engine runs several threads that each invent a strategy, backtest it,
 * judge it and — if it survives — race it against every prop rule set. Without
 * this the only evidence any of that is happening is a scrolling log, which is
 * why the app read as inert even while it was working.
 *
 * The animation is not decoration: a lane pulses only while its worker is in a
 * stage, and the pulse period matches the stage's real cost. Backtesting a
 * quarter-million bars takes tens of seconds and looks like it; the memory gate
 * rejects in microseconds and barely registers. Watching it is a rough profiler.
 */

const STAGES = [
  { key: 'creating', label: 'Invent', icon: Brain, note: 'draw a template and parameters' },
  { key: 'backtesting', label: 'Backtest', icon: FlaskConical, note: 'in-sample, then out-of-sample' },
  { key: 'judging', label: 'Judge', icon: Gavel, note: 'fourteen deterministic gates' },
  { key: 'prop', label: 'Prop', icon: ShieldCheck, note: 'race every rule set' },
] as const

type StageKey = (typeof STAGES)[number]['key']

const INDEX: Record<string, number> = Object.fromEntries(STAGES.map((s, i) => [s.key, i]))

export function AgentSwarm({ status }: { status: EngineStatus }) {
  const stages = status.worker_stages ?? {}
  const workers = Object.keys(stages).sort((a, b) => Number(a) - Number(b))
  const running = status.running

  if (!running && workers.length === 0) {
    return (
      <div className="swarm is-idle af-panel-in">
        <Moon size={14} />
        <span>
          The engine is stopped. Start it and each worker will invent, backtest, judge and
          prop-test candidates continuously — it is built to be left running for days.
        </span>
      </div>
    )
  }

  return (
    <div className="swarm af-panel-in">
      <div className="swarm-head">
        <span className="swarm-title">
          {workers.length} worker{workers.length === 1 ? '' : 's'}
        </span>
        <span className="swarm-legend">
          {STAGES.map((stage) => (
            <span key={stage.key} title={stage.note}>
              <stage.icon size={10} /> {stage.label}
            </span>
          ))}
        </span>
      </div>

      <div className="swarm-lanes">
        {workers.map((id) => {
          const stage = stages[id] ?? 'idle'
          const at = INDEX[stage] ?? -1
          return (
            <div className="lane" key={id} data-active={running && at >= 0 ? 'yes' : undefined}>
              <span className="lane-id mono">w{id}</span>
              <div className="lane-track">
                {STAGES.map((step, index) => {
                  const state =
                    at < 0 ? 'idle' : index < at ? 'done' : index === at ? 'active' : 'ahead'
                  return (
                    <div className="lane-step" key={step.key} data-state={state} title={step.note}>
                      <step.icon size={11} />
                      <span>{step.label}</span>
                    </div>
                  )
                })}
              </div>
              <span className="lane-stage mono">{stage}</span>
            </div>
          )
        })}
      </div>

      <div className="swarm-foot">
        <Stat label="cycles" value={status.cycles} />
        <Stat label="created" value={status.created} />
        <Stat label="judged" value={status.judged} />
        <Stat label="rejected" value={status.rejected} tone="bad" />
        <Stat label="pruned" value={status.pruned ?? 0} />
        <Stat label="prop runs" value={status.prop_tested ?? 0} />
        {status.best_strategy ? (
          <span className="swarm-best" title={status.best_rule ?? ''}>
            <ShieldCheck size={11} />
            best {(100 * (status.best_pass_rate ?? 0)).toFixed(1)}% · {status.best_strategy}
          </span>
        ) : (
          <span className="swarm-best is-none">
            <Skull size={11} />
            nothing has survived to a prop simulation yet
          </span>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'bad' }) {
  return (
    <span className="swarm-stat">
      <b className={tone === 'bad' && value > 0 ? 'bad' : undefined}>{value.toLocaleString()}</b>
      <em>{label}</em>
    </span>
  )
}

export type { StageKey }
