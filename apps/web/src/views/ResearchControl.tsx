import { useQuery } from '@tanstack/react-query'
import {
  Activity, AlertTriangle, Bot, Copy, Gauge, Layers, Pause, Play, Plus,
  Square, TrendingUp,
} from 'lucide-react'
import { useState } from 'react'
import { getJson } from '../api'
import { RuntimeBadge, RuntimeDiagnostics } from '../components/RuntimeState'
import {
  AGENT_TONE, useCampaignAgents, useCampaignControl, useCampaignSkips, useControlCenter,
  type Campaign, type ResearchAgent,
} from '../research'
import type { EngineStatus } from '../types'

/* The Research Control Center.
 *
 * What it replaces: a screen that showed one campaign, a template count as a
 * proxy for research diversity, "Validation: 0" whether nothing was eligible or
 * everything failed, and a number called "Skipped by memory" that summed five
 * unrelated situations.
 *
 * What it shows instead is the fabric: every campaign, how the engine's workers
 * are divided between them, which are producing and which have stalled, the
 * agents on each, and an accounting of refusals that separates compute genuinely
 * saved from cycles that had nothing to do. Nothing here is computed on the
 * client — every figure is served by the store that owns it.
 */

export function ResearchControlView() {
  const [selected, setSelected] = useState<string | null>(null)
  const centre = useControlCenter()
  const engine = useQuery({
    queryKey: ['engine'],
    refetchInterval: 5_000,
    queryFn: () => getJson<EngineStatus>('/engine'),
  })
  const control = useCampaignControl()

  const data = centre.data
  const stalled = new Set(data?.allocation.stalled ?? [])
  const running = data?.campaigns.filter((c) => c.status === 'running') ?? []
  const plan = data?.allocation.worker_plan ?? []

  return (
    <section className="rcc">
      <header className="rcc-head">
        <div>
          <p className="command-kicker"><span className="signal-dot" /> RESEARCH</p>
          <h2>Every campaign, every agent, and what they are actually doing.</h2>
          <p className="rcc-sub">
            Workers are divided between running campaigns by priority and by observed
            health. A campaign that stops producing yields share to one that has not,
            and none is ever starved to zero.
          </p>
        </div>
        <div className="rcc-vitals">
          <div><span>Engine</span><RuntimeBadge engine={engine.data} compact /></div>
          <div><span>Campaigns</span><strong>{data?.totals.running ?? 0}<small> / {data?.totals.campaigns ?? 0}</small></strong></div>
          <div><span>Agents</span><strong>{data?.agents.eligible ?? 0}<small> / {data?.agents.total ?? 0}</small></strong></div>
          <div><span>Capacity</span><strong>{data?.capacity.max_agents ?? '—'}<small> max</small></strong></div>
        </div>
      </header>

      {centre.isError && (
        <p className="state error" role="alert">The research fabric could not be read.</p>
      )}

      {/* The metrics that replace a template count. Each is a different thing:
        * a new mechanism is not a new parameter set, and a dashboard that
        * cannot tell them apart cannot say whether research is happening. */}
      <div className="rcc-metrics">
        <Metric label="Experiments" value={data?.totals.experiments} note="Candidates written, backtested and judged." />
        <Metric label="Hypotheses" value={data?.totals.hypotheses} note="Falsifiable claims on record." />
        <Metric label="Mechanisms" value={data?.totals.mechanisms} note="Distinct economic explanations, not parameter sets." />
        <Metric label="New families" value={data?.totals.families_created} note="Research families the director proposed and registered." />
        <Metric label="New constructions" value={data?.totals.templates_created} note="Signal constructions composed and admitted to the catalogue." />
        <Metric label="Follow-ups" value={data?.totals.followups} note="Questions derived from what failed." />
      </div>

      <div className="rcc-columns">
        <section className="rcc-accounting">
          <h3>Refusals</h3>
          <p className="rcc-note">
            One number used to cover all of this. These are different facts: a refusal
            that declined a real experiment saved compute; one that happened because
            there was nothing to do saved nothing and means the search needs redirecting.
            An exact repeat and a restatement are also different, which is what the
            novelty breakdown is for — “already answered” is not a count of duplicates.
          </p>
          <dl>
            <div data-tone="good" title="An identical experiment, a disproven parameter region, or a restatement of a claim already on the frontier. Compute genuinely saved — the breakdown below says how close each one was.">
              <dt>Already answered</dt>
              <dd>{(data?.skips.useful ?? 0).toLocaleString()}</dd>
            </div>
            <div data-tone="warn">
              <dt>Cycles with nothing to do</dt>
              <dd>{(data?.skips.wasted ?? 0).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Ended on a stopping rule</dt>
              <dd>{(data?.skips.neutral ?? 0).toLocaleString()}</dd>
            </div>
          </dl>
          <NoveltyBreakdown levels={data?.skips.by_level} />
        </section>

        <section className="rcc-accounting">
          <h3>Validation</h3>
          <p className="rcc-note">
            Attempts, passes, failures and blocked, separately. A single figure cannot
            distinguish “nothing was eligible” from “everything was tried and failed”.
          </p>
          <dl>
            <div><dt>Attempts</dt><dd>{data?.validation.attempts ?? 0}</dd></div>
            <div data-tone="good"><dt>Passed</dt><dd>{data?.validation.passed ?? 0}</dd></div>
            <div data-tone="warn"><dt>Failed</dt><dd>{data?.validation.failed ?? 0}</dd></div>
            <div><dt>Inconclusive</dt><dd>{data?.validation.inconclusive ?? 0}</dd></div>
            <div><dt>Blocked</dt><dd>{data?.validation.blocked ?? 0}</dd></div>
            <div><dt>Queued</dt><dd>{data?.validation.pending ?? 0}</dd></div>
          </dl>
          <p className="rcc-note rcc-note-tight">
            G0–G13 decide every one of these. Nothing on this screen can move a gate.
          </p>
        </section>
      </div>

      {engine.data?.runtime && !engine.data.runtime.working
        && engine.data.runtime.state !== 'STOPPED' && (
        <RuntimeDiagnostics engine={engine.data} />
      )}

      <section className="rcc-campaigns">
        <header>
          <h3><Layers aria-hidden="true" /> Campaigns</h3>
          <span>{running.length} running of {data?.campaigns.length ?? 0}</span>
        </header>

        {!data?.campaigns.length && (
          <p className="rcc-empty">
            No campaigns yet. A campaign is a research programme with its own objective,
            budget, frontier and crew — and several can run at once.
          </p>
        )}

        <div className="rcc-grid">
          {data?.campaigns.map((campaign) => (
            <CampaignCard
              key={campaign.campaign_id}
              campaign={campaign}
              workers={plan.filter((id) => id === campaign.campaign_id).length}
              stalled={stalled.has(campaign.campaign_id)}
              health={data.allocation.running_campaigns
                .find((r) => r.campaign_id === campaign.campaign_id)?.runtime.health}
              selected={selected === campaign.campaign_id}
              onSelect={() => setSelected(
                selected === campaign.campaign_id ? null : campaign.campaign_id,
              )}
              onStart={() => control.start.mutate({ campaign_id: campaign.campaign_id })}
              onPause={() => control.pause.mutate(campaign.campaign_id)}
              onStop={() => control.stop.mutate(campaign.campaign_id)}
              onDuplicate={() => control.duplicate.mutate({ campaign_id: campaign.campaign_id })}
              onPrioritise={(priority) =>
                control.prioritise.mutate({ campaign_id: campaign.campaign_id, priority })}
            />
          ))}
        </div>
      </section>

      {selected && <AgentMonitor campaignId={selected} maxAgents={data?.capacity.max_agents ?? 8} />}
      {selected && <SkipLedger campaignId={selected} />}
    </section>
  )
}

function Metric({ label, value, note }: { label: string; value: number | undefined; note: string }) {
  return (
    <div className="rcc-metric" title={note}>
      <span>{label}</span>
      <strong>{value == null ? '—' : value.toLocaleString()}</strong>
      <small>{note}</small>
    </div>
  )
}

/** The novelty hierarchy, which is what replaces a template count as a measure
 *  of research diversity. A new mechanism and a new parameter set are not the
 *  same discovery and must not read as the same number. */
function NoveltyBreakdown({ levels }: { levels: Record<string, number> | undefined }) {
  const rows = Object.entries(levels ?? {}).filter(([, n]) => n > 0)
  if (!rows.length) return null
  return (
    <div className="rcc-levels">
      <h4>By novelty</h4>
      <ul>
        {rows.map(([level, count]) => (
          <li key={level}>
            <span>{level.replace(/_/g, ' ').toLowerCase()}</span>
            <strong>{count.toLocaleString()}</strong>
          </li>
        ))}
      </ul>
    </div>
  )
}

function CampaignCard({
  campaign, workers, stalled, health, selected, onSelect,
  onStart, onPause, onStop, onDuplicate, onPrioritise,
}: {
  campaign: Campaign
  workers: number
  stalled: boolean
  health: number | undefined
  selected: boolean
  onSelect: () => void
  onStart: () => void
  onPause: () => void
  onStop: () => void
  onDuplicate: () => void
  onPrioritise: (priority: number) => void
}) {
  const p = campaign.progress
  return (
    <article className="rcc-card" data-status={campaign.status} data-selected={selected || undefined}>
      <header>
        <button className="rcc-card-title" onClick={onSelect} aria-expanded={selected}>
          <strong>{campaign.name}</strong>
          <span>{campaign.symbol} · {campaign.dataset}</span>
        </button>
        <span className="rcc-status" data-status={campaign.status}>{campaign.status}</span>
      </header>

      <p className="rcc-objective">{campaign.description || campaign.objective}</p>

      {campaign.exhausted && (
        <p className="rcc-exhausted">
          <AlertTriangle aria-hidden="true" /> {campaign.exhausted_reason}
        </p>
      )}
      {stalled && !campaign.exhausted && (
        <p className="rcc-stalled">
          <AlertTriangle aria-hidden="true" /> Producing nothing. Broaden the objective,
          allow another mechanism domain, or add a discovery agent.
        </p>
      )}

      <dl className="rcc-card-figures">
        <div><dt>Experiments</dt><dd>{p.experiments}</dd></div>
        <div><dt>Hypotheses</dt><dd>{p.hypotheses}</dd></div>
        <div><dt>Mechanisms</dt><dd>{p.mechanisms}</dd></div>
        <div><dt>Validated</dt><dd>{p.validated}</dd></div>
        <div><dt>Workers</dt><dd>{workers}</dd></div>
        <div>
          <dt>Health</dt>
          <dd>{health == null ? '—' : `${Math.round(health * 100)}%`}</dd>
        </div>
      </dl>

      <label className="rcc-priority">
        <span><Gauge aria-hidden="true" /> Priority</span>
        <input
          type="range"
          min={0}
          max={100}
          defaultValue={campaign.priority}
          aria-label={`Priority for ${campaign.name}`}
          onMouseUp={(event) => onPrioritise(Number(event.currentTarget.value))}
          onKeyUp={(event) => onPrioritise(Number(event.currentTarget.value))}
        />
        <b>{campaign.priority}</b>
      </label>
      <p className="rcc-note rcc-note-tight">
        Priority allocates workers. It cannot relax a gate or lower a threshold.
      </p>

      <footer className="rcc-actions">
        {campaign.status !== 'running' && !campaign.exhausted && (
          <button onClick={onStart}><Play aria-hidden="true" />Start</button>
        )}
        {campaign.status === 'running' && (
          <>
            <button onClick={onPause}><Pause aria-hidden="true" />Pause</button>
            <button onClick={onStop}><Square aria-hidden="true" />Stop</button>
          </>
        )}
        <button onClick={onDuplicate}><Copy aria-hidden="true" />Duplicate</button>
      </footer>
    </article>
  )
}

/** One campaign's crew. Clicking an agent shows what it is holding.
 *
 *  The capacity note is shown rather than swallowed: asking for 32 agents on a
 *  machine that can serve 8 is a reasonable thing to ask, and the honest answer
 *  is "here are 8, and here is why" — not 32 rows that never do anything. */
function AgentMonitor({ campaignId, maxAgents }: { campaignId: string; maxAgents: number }) {
  const [note, setNote] = useState('')
  const agents = useCampaignAgents(campaignId)
  const control = useCampaignControl()
  const roster = agents.data?.agents ?? []
  const counts = agents.data?.counts

  const deploy = (count: number) =>
    control.deployAgents.mutate(
      { campaign_id: campaignId, count },
      { onSuccess: (result) => setNote(result.capacity_note) },
    )

  return (
    <section className="rcc-agents">
      <header>
        <h3><Bot aria-hidden="true" /> Agents</h3>
        <span>
          {counts?.eligible ?? 0} working · {counts?.total ?? 0} deployed · {maxAgents} servable
        </span>
        <div className="rcc-deploy">
          {[1, 2, 4, 8, 16, 32].map((count) => (
            <button key={count} onClick={() => deploy(count)} title={`Add ${count} agent(s)`}>
              <Plus aria-hidden="true" />{count}
            </button>
          ))}
        </div>
      </header>

      {note && <p className="rcc-capacity" role="status">{note}</p>}

      {!roster.length && (
        <p className="rcc-empty">
          No agents on this campaign. It still researches — an agent biases <em>what</em>
          {' '}is proposed, it is not a precondition for work.
        </p>
      )}

      {roster.length > 0 && (
        <div className="rcc-agent-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Agent</th><th scope="col">Role</th><th scope="col">State</th>
                <th scope="col">Current task</th><th scope="col">Holding</th>
                <th scope="col">Experiments</th><th scope="col">Findings</th>
                <th scope="col">Errors</th><th scope="col"><span className="sr-only">Remove</span></th>
              </tr>
            </thead>
            <tbody>
              {roster.map((agent) => <AgentRow
                key={agent.agent_id}
                agent={agent}
                onRemove={() => control.removeAgent.mutate({
                  campaign_id: campaignId, agent_id: agent.agent_id,
                })}
              />)}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function AgentRow({ agent, onRemove }: { agent: ResearchAgent; onRemove: () => void }) {
  return (
    <tr data-stale={agent.stale || undefined}>
      <th scope="row" title={agent.objective}>{agent.name}</th>
      <td><span className="rcc-role" title={agent.role_purpose}>{agent.role.toLowerCase()}</span></td>
      <td>
        <span className="rcc-agent-state" data-tone={AGENT_TONE[agent.state]}>
          {agent.state.toLowerCase()}
        </span>
        {agent.stale && <em className="rcc-stale" title="No heartbeat recently"> stale</em>}
      </td>
      <td className="rcc-task">{agent.current_task || '—'}</td>
      <td className="rcc-task">{agent.current_claim || '—'}</td>
      <td className="num">{agent.experiments}</td>
      <td className="num">{agent.findings}</td>
      <td className="num">{agent.errors}</td>
      <td><button className="rcc-remove" onClick={onRemove} aria-label={`Remove ${agent.name}`}>×</button></td>
    </tr>
  )
}

/** What the campaign refused, with the thing it collided with named.
 *
 *  This is the drill-down that makes the headline number checkable. A skip row
 *  says what was proposed, what it matched, how close, and whether a retry is
 *  permitted — which is how a legitimate follow-up stops being silently barred
 *  by a failed ancestor. */
function SkipLedger({ campaignId }: { campaignId: string }) {
  const skips = useCampaignSkips(campaignId)
  const rows = skips.data?.skips ?? []
  const retryable = skips.data?.retryable ?? []

  return (
    <section className="rcc-skips">
      <header>
        <h3><Activity aria-hidden="true" /> Refusals</h3>
        <span>{skips.data?.counts.distinct ?? 0} distinct · {skips.data?.counts.total ?? 0} total</span>
      </header>

      {!rows.length && <p className="rcc-empty">Nothing has been refused on this campaign.</p>}

      {rows.length > 0 && (
        <div className="rcc-agent-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Proposal</th><th scope="col">Why</th>
                <th scope="col">Matched</th><th scope="col">Novelty</th>
                <th scope="col">Times</th><th scope="col">Retry</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 60).map((row) => (
                <tr key={row.skip_id}>
                  <th scope="row" className="rcc-task">{row.subject}</th>
                  <td className="rcc-task">{row.reason}</td>
                  <td className="rcc-task">{row.matched ?? '—'}</td>
                  <td>
                    <span className="rcc-level">{row.level.replace(/_/g, ' ').toLowerCase()}</span>
                    {row.similarity != null && <em> {row.similarity.toFixed(2)}</em>}
                  </td>
                  <td className="num">{row.occurrences}</td>
                  <td title={row.retry_condition}>{row.retry_permitted ? 'permitted' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {retryable.length > 0 && (
        <div className="rcc-retryable">
          <h4><TrendingUp aria-hidden="true" /> Open again under the right conditions</h4>
          <ul>
            {retryable.slice(0, 8).map((row, index) => (
              <li key={`${row.subject}-${index}`}>
                <strong>{row.subject}</strong>
                <span>{row.retry_condition || 'No condition — may be proposed again.'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
