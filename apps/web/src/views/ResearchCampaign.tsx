/* The research campaign: what it is doing, why, and what it has learned.
 *
 * The old engine screen answered one question — is it running — with nine
 * counters that all measured the same thing at different stages. This answers
 * the questions a researcher actually has, and it is built around one
 * distinction the counters could not make: **a hundred trials of one idea is
 * one piece of research**. Experiments, hypotheses, mechanisms and families are
 * four separate figures here because they are four separate things, and a
 * campaign that ran two hundred experiments against three mechanisms should
 * look narrow at a glance.
 *
 * Everything rendered is state the backend actually holds. The event stream is
 * the campaign journal, not a simulated ticker; the frontier counts come from
 * the frontier store; a hypothesis card shows the novelty score the gate
 * computed and names what it collided with. Nothing here is generated to make
 * the screen look busy — that is the whole difference between an event stream
 * and an animation.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, Play, Plus } from 'lucide-react'
import { useState } from 'react'
import { API, getJson } from '../api'
import { Empty, PanelHead, Stat } from '../components/ui'
import type { DatasetInfo } from '../types'

type Counts = Record<string, number>

type Campaign = {
  campaign_id: string
  name: string
  objective: string
  dataset: string
  symbol: string
  timeframe: string
  status: string
  web_research: boolean
  allocation: Record<string, number>
  stopping: Record<string, number>
  allowed_capabilities: string[]
  exhausted: boolean
  exhausted_reason: string
  stopped_reason: string
  progress: {
    experiments: number
    hypotheses: number
    families_created: number
    templates_created: number
    mechanisms: number
    duplicates_rejected: number
    blocked_proposals: number
    failures: number
    promising: number
    validation_candidates: number
    validated: number
    inconclusive: number
    sources_retrieved: number
    followups_generated: number
    compute_units: number
    spend: Counts
  }
}

type Overview = {
  campaign: Campaign
  frontier: { counts: Counts; kinds: Counts; open: number; blocked: number; settled: number }
  hypotheses: { counts: Counts; mechanisms: number }
  validation: { pending: number; outcomes: Counts }
  sources: number
  journal_head: number
  generated_templates: Record<string, { archetype: string; definition_hash: string }>
}

type FrontierItem = {
  item_id: string
  question: string
  family: string
  mechanism: string
  state: string
  reason: string
  search_kind: string
  novelty: number
  experiments: number
  missing_data: string[]
  schedulable: boolean
  updated_at: string
}

type Hypothesis = {
  hypothesis_id: string
  statement: string
  falsifiable_prediction: string
  mechanism: string
  family: string
  status: string
  origin: string
  search_kind: string
  novelty: number
  parent_id: string | null
  research_sources: string[]
  created_at: string
}

type JournalEvent = {
  event_id: number
  kind: string
  level: string
  message: string
  subject: string | null
  detail: Record<string, unknown>
  at: string
}

type QueueEntry = {
  entry_id: number
  strategy_id: string
  state: string
  outcome: string | null
  reasons: string[]
  queued_at: string
}

/* The nine frontier states, in the order the map reads: what is unknown first,
 * what is settled last. The order is presentational and carries no arithmetic. */
const STATE_ORDER = [
  'UNKNOWN',
  'UNTESTED',
  'PARTIALLY_EXPLORED',
  'INCONCLUSIVE',
  'PROMISING',
  'VALIDATED',
  'FAILED',
  'BLOCKED_BY_DATA',
  'EXHAUSTED',
] as const

const STATE_LABEL: Record<string, string> = {
  UNKNOWN: 'Unknown',
  UNTESTED: 'Untested',
  PARTIALLY_EXPLORED: 'Partly explored',
  INCONCLUSIVE: 'Inconclusive',
  PROMISING: 'Promising',
  VALIDATED: 'Validated',
  FAILED: 'Failed',
  BLOCKED_BY_DATA: 'Blocked on data',
  EXHAUSTED: 'Exhausted',
}

/* Tone, not colour: three of these states mean "no evidence either way" and
 * must not read as failure. That is the point the whole frontier exists to
 * make, so it has to survive into the palette. */
const STATE_TONE: Record<string, string> = {
  UNKNOWN: 'open',
  UNTESTED: 'open',
  PARTIALLY_EXPLORED: 'partial',
  INCONCLUSIVE: 'partial',
  PROMISING: 'promising',
  VALIDATED: 'validated',
  FAILED: 'failed',
  BLOCKED_BY_DATA: 'blocked',
  EXHAUSTED: 'exhausted',
}

const KIND_LABEL: Record<string, string> = {
  PARAMETER: 'Parameter',
  STRUCTURAL: 'Structural',
  HYPOTHESIS: 'Hypothesis',
  MECHANISM: 'Mechanism',
  FAMILY: 'Family',
}

const BUCKET_LABEL: Record<string, string> = {
  EXPLORE_HYPOTHESIS: 'New hypotheses',
  DISCOVER_FAMILY: 'New families',
  ADVANCE_PROMISING: 'Advancing promising',
  REFINE_PARAMETERS: 'Parameter refinement',
  ROBUSTNESS: 'Robustness',
}

const EVENT_LABEL: Record<string, string> = {
  CAMPAIGN_STARTED: 'Started',
  CAMPAIGN_STOPPED: 'Stopped',
  ALLOCATION_ADAPTED: 'Budget re-weighted',
  BUDGET_DRAWN: 'Budget',
  LITERATURE_SEARCHED: 'Literature',
  SOURCE_FOUND: 'Source',
  HYPOTHESIS_PROPOSED: 'Hypothesis',
  HYPOTHESIS_REJECTED: 'Duplicate',
  NOVELTY_CHECKED: 'Novelty',
  DATA_BLOCKED: 'Blocked',
  FAMILY_CREATED: 'Family',
  TEMPLATE_CREATED: 'Template',
  TEMPLATE_REJECTED: 'Template refused',
  EXPERIMENT_FINISHED: 'Experiment',
  FRONTIER_UPDATED: 'Frontier',
  FOLLOWUP_GENERATED: 'Follow-up',
  VALIDATION_QUEUED: 'Validation',
  VALIDATION_DECIDED: 'Validation',
  ERROR: 'Error',
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(String(payload?.detail?.reason ?? payload?.detail?.code ?? response.statusText))
  }
  return payload.data as T
}

/** Keep a scrolling stream pinned to its newest line. */
function scrollToFoot(node: HTMLOListElement | null) {
  if (node) node.scrollTop = node.scrollHeight
}

export function ResearchCampaignView() {
  const client = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [tab, setTab] = useState<'frontier' | 'hypotheses' | 'validation' | 'sources'>('frontier')
  const [stateFilter, setStateFilter] = useState<string>('')

  const active = useQuery({
    queryKey: ['campaign-active'],
    queryFn: () => getJson<Overview | null>('/campaigns/active'),
    refetchInterval: (q) => (q.state.data?.campaign?.status === 'running' ? 2000 : 10000),
  })
  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => getJson<Campaign[]>('/campaigns'),
  })

  const overview = active.data
  const campaignId = overview?.campaign.campaign_id
  const running = overview?.campaign.status === 'running'

  const events = useQuery({
    queryKey: ['campaign-events', campaignId],
    queryFn: () => getJson<{ events: JournalEvent[]; head: number }>(
      `/campaigns/${campaignId}/events?limit=120`,
    ),
    enabled: !!campaignId,
    refetchInterval: running ? 2000 : false,
  })
  const frontier = useQuery({
    queryKey: ['campaign-frontier', campaignId, stateFilter],
    queryFn: () => getJson<FrontierItem[]>(
      `/campaigns/${campaignId}/frontier${stateFilter ? `?state=${stateFilter}` : ''}`,
    ),
    enabled: !!campaignId && tab === 'frontier',
    refetchInterval: running ? 5000 : false,
  })
  const hypotheses = useQuery({
    queryKey: ['campaign-hypotheses', campaignId],
    queryFn: () => getJson<Hypothesis[]>(`/campaigns/${campaignId}/hypotheses`),
    enabled: !!campaignId && tab === 'hypotheses',
    refetchInterval: running ? 5000 : false,
  })
  const validation = useQuery({
    queryKey: ['campaign-validation', campaignId],
    queryFn: () => getJson<{ queue: QueueEntry[]; pending: number; outcomes: Counts }>(
      `/campaigns/${campaignId}/validation`,
    ),
    enabled: !!campaignId && tab === 'validation',
    refetchInterval: running ? 5000 : false,
  })
  const sources = useQuery({
    queryKey: ['campaign-sources', campaignId],
    queryFn: () => getJson<{
      sources: { id: string; title: string; url: string; source: string; published: string
        retrieved_at: string; claims: { text: string }[]; relevance: number }[]
      queries: { query: string; count: number; error: string; at: string }[]
    }>(`/campaigns/${campaignId}/sources`),
    enabled: !!campaignId && tab === 'sources',
  })

  const refresh = () => {
    for (const key of ['campaign-active', 'campaigns', 'campaign-events', 'engine']) {
      client.invalidateQueries({ queryKey: [key] })
    }
  }

  const start = useMutation({
    mutationFn: (id: string) => send<Overview>(`/campaigns/${id}/start`, 'POST', {
      workers: 4, cycle_seconds: 4, max_strategies: 400, max_bars: 250000,
    }),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const stop = useMutation({
    mutationFn: (id: string) => send<Overview>(`/campaigns/${id}/stop`, 'POST'),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })

  if (active.isLoading) return <p className="muted">Loading the campaign…</p>

  if (!overview) {
    return (
      <div className="campaign">
        <PanelHead title="Research campaign" meta="Nothing running" />
        {composing ? (
          <CampaignForm
            onCancel={() => setComposing(false)}
            onCreated={() => { setComposing(false); refresh() }}
          />
        ) : (
          <>
            <Empty
              title="No campaign is running"
              detail={
                'A campaign is an objective with a budget: what to research, on which data, ' +
                'how much compute may be spent, and what would count as finished. Starting ' +
                'the engine without one searches parameters; starting one gives it a question.'
              }
              action={
                <button className="btn primary" onClick={() => setComposing(true)}>
                  <Plus size={13} /> New campaign
                </button>
              }
            />
            {!!campaigns.data?.length && (
              <section className="campaign-past">
                <PanelHead title="Earlier campaigns" meta={`${campaigns.data.length}`} />
                <ul className="campaign-list">
                  {campaigns.data.map((row) => (
                    <li key={row.campaign_id}>
                      <div>
                        <b>{row.name}</b>
                        <p className="muted">{row.objective}</p>
                        <span className="mono muted">
                          {row.progress.experiments} experiments ·{' '}
                          {row.progress.hypotheses} hypotheses ·{' '}
                          {row.progress.mechanisms} mechanisms
                          {row.stopped_reason ? ` · ${row.stopped_reason}` : ''}
                        </span>
                      </div>
                      <button className="btn" onClick={() => start.mutate(row.campaign_id)}>
                        <Play size={12} /> Resume
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
        {error && <p className="warning bad">{error}</p>}
      </div>
    )
  }

  const { campaign, frontier: map, hypotheses: graph, validation: queue } = overview
  const progress = campaign.progress
  const spent = Object.values(progress.spend ?? {}).reduce((a, b) => a + b, 0)

  return (
    <div className="campaign">
      <header className="campaign-head">
        <div className="campaign-title">
          <i className={running ? 'pulse' : 'pulse is-off'} />
          <div>
            <span className="mono muted">{campaign.dataset} · {campaign.symbol} · {campaign.timeframe}</span>
            <h2>{campaign.name}</h2>
            <p>{campaign.objective}</p>
          </div>
        </div>
        <div className="campaign-controls">
          {running ? (
            <button
              className="btn danger"
              onClick={() => stop.mutate(campaign.campaign_id)}
              disabled={stop.isPending}
            >
              <CircleStop size={13} /> Stop
            </button>
          ) : (
            <button
              className="btn primary"
              onClick={() => start.mutate(campaign.campaign_id)}
              disabled={start.isPending || campaign.exhausted}
            >
              <Play size={13} /> {campaign.exhausted ? 'Finished' : 'Start'}
            </button>
          )}
        </div>
      </header>

      {campaign.exhausted && (
        <p className="warning">Campaign finished — {campaign.exhausted_reason}.</p>
      )}
      {!campaign.web_research && (
        <p className="muted campaign-note">
          External research is off for this campaign, so no source is retrieved and none is
          invented. Turn it on when creating a campaign to let the researcher read.
        </p>
      )}
      {error && <p className="warning bad">{error}</p>}

      {/* The four figures that separate breadth from depth. Experiments is
          deliberately not the headline: it is the one that inflates. */}
      <div className="campaign-scale">
        <Stat label="Mechanisms" value={graph.mechanisms} note="distinct explanations" />
        <Stat label="Hypotheses" value={progress.hypotheses} note="falsifiable claims" />
        <Stat label="Families" value={progress.families_created} note="discovered" />
        <Stat label="Templates" value={progress.templates_created} note="composed" />
        <Stat label="Experiments" value={progress.experiments} note="trials run" />
        <Stat
          label="Validation"
          value={progress.validated}
          note={`${queue.pending} queued`}
          tone={progress.validated ? 'good' : 'plain'}
        />
      </div>

      <div className="campaign-grid">
        <section className="campaign-map af-panel">
          <PanelHead title="Research frontier" meta={`${map.open} open · ${map.blocked} blocked`} />
          <ul className="frontier-bar">
            {STATE_ORDER.map((state) => {
              const count = map.counts[state] ?? 0
              return (
                <li
                  key={state}
                  data-tone={STATE_TONE[state]}
                  data-active={stateFilter === state ? 'yes' : undefined}
                >
                  <button
                    onClick={() => { setTab('frontier'); setStateFilter(stateFilter === state ? '' : state) }}
                    title={`${STATE_LABEL[state]} — click to filter`}
                  >
                    <b>{count}</b>
                    <span>{STATE_LABEL[state]}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="frontier-legend muted">
            Untested is not failed. Blocked means the data does not exist here, and says
            nothing about whether the claim is true.
          </p>
        </section>

        <section className="campaign-budget af-panel">
          <PanelHead title="Research budget" meta={`${spent} cycles spent`} />
          <ul className="budget-rows">
            {Object.entries(campaign.allocation).map(([bucket, weight]) => {
              const used = progress.spend?.[bucket] ?? 0
              const share = spent ? used / spent : 0
              return (
                <li key={bucket}>
                  <span className="budget-label">{BUCKET_LABEL[bucket] ?? bucket}</span>
                  <span className="budget-track" aria-hidden>
                    <i className="budget-intent" style={{ width: `${weight * 100}%` }} />
                    <i className="budget-actual" style={{ width: `${share * 100}%` }} />
                  </span>
                  <span className="mono budget-figures">
                    {Math.round(weight * 100)}% / {Math.round(share * 100)}%
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="muted">Intended share, then what it actually consumed.</p>
        </section>
      </div>

      <section className="campaign-stream af-panel">
        <PanelHead
          title="What it is doing"
          meta={events.data ? `${events.data.events.length} events` : undefined}
        />
        {events.data?.events.length ? (
          /* Chronological, the way the journal serves it. These read as a
             narrative — proposed, checked, created, run — and a narrative in
             reverse is not one. The container scrolls to the foot so the most
             recent line is the one in view. */
          <ol className="event-stream" ref={scrollToFoot}>
            {events.data.events.map((event) => (
              <li key={event.event_id} data-level={event.level}>
                <time className="mono">{event.at.slice(11, 19)}</time>
                <span className="event-kind">{EVENT_LABEL[event.kind] ?? event.kind}</span>
                <p>{event.message}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">Nothing recorded yet.</p>
        )}
      </section>

      <nav className="campaign-tabs" role="tablist">
        {(['frontier', 'hypotheses', 'validation', 'sources'] as const).map((key) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
          >
            {key === 'frontier' ? 'Frontier' : key === 'hypotheses' ? 'Hypotheses'
              : key === 'validation' ? 'Validation queue' : 'Sources'}
          </button>
        ))}
      </nav>

      {tab === 'frontier' && (
        <FrontierTable
          items={frontier.data ?? []}
          filter={stateFilter}
          onClear={() => setStateFilter('')}
        />
      )}
      {tab === 'hypotheses' && <HypothesisList items={hypotheses.data ?? []} />}
      {tab === 'validation' && (
        <ValidationQueue entries={validation.data?.queue ?? []} outcomes={validation.data?.outcomes ?? {}} />
      )}
      {tab === 'sources' && (
        <SourceList
          sources={sources.data?.sources ?? []}
          queries={sources.data?.queries ?? []}
          enabled={campaign.web_research}
        />
      )}
    </div>
  )
}

function FrontierTable({
  items, filter, onClear,
}: { items: FrontierItem[]; filter: string; onClear: () => void }) {
  if (!items.length) {
    return (
      <Empty
        title={filter ? `Nothing is ${STATE_LABEL[filter] ?? filter}` : 'The frontier is empty'}
        detail={
          filter
            ? 'No question is in this state yet.'
            : 'Questions appear here as the researcher proposes them.'
        }
        action={filter ? <button className="btn" onClick={onClear}>Show all</button> : undefined}
      />
    )
  }
  return (
    <table className="data-table frontier-table">
      <thead>
        <tr>
          <th>State</th><th>Question</th><th>Kind</th><th>Novelty</th>
          <th>Trials</th><th>Why</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.item_id}>
            <td>
              <span className="state-pill" data-tone={STATE_TONE[item.state]}>
                {STATE_LABEL[item.state] ?? item.state}
              </span>
            </td>
            <td className="frontier-question">
              <p>{item.question}</p>
              <span className="mono muted">{item.family}</span>
            </td>
            <td>{KIND_LABEL[item.search_kind] ?? item.search_kind}</td>
            <td className="mono">{Math.round(item.novelty * 100)}%</td>
            <td className="mono">{item.experiments}</td>
            <td className="frontier-reason">
              {item.reason}
              {!!item.missing_data.length && (
                <span className="mono muted"> — needs {item.missing_data.join(', ')}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function HypothesisList({ items }: { items: Hypothesis[] }) {
  if (!items.length) {
    return <Empty title="No hypothesis yet" detail="Claims appear here as they are proposed." />
  }
  return (
    <ul className="hypothesis-list">
      {items.map((node) => (
        <li key={node.hypothesis_id} className="af-panel">
          <header>
            <span className="state-pill" data-tone={node.status === 'VALIDATED' ? 'validated'
              : node.status === 'REFUTED' ? 'failed'
              : node.status === 'SUPPORTED' ? 'promising'
              : node.status === 'BLOCKED_BY_DATA' ? 'blocked' : 'open'}>
              {node.status.replace(/_/g, ' ').toLowerCase()}
            </span>
            <span className="mono muted">
              {KIND_LABEL[node.search_kind] ?? node.search_kind} · {Math.round(node.novelty * 100)}% novel
              {node.parent_id ? ' · derived' : ''}
              {node.origin === 'failure-derived' ? ' from a failure' : ''}
            </span>
          </header>
          <p className="hypothesis-claim">{node.statement}</p>
          <p className="hypothesis-prediction">
            <b>Abandoned if:</b> {node.falsifiable_prediction}
          </p>
          <p className="hypothesis-mechanism muted">{node.mechanism}</p>
        </li>
      ))}
    </ul>
  )
}

function ValidationQueue({ entries, outcomes }: { entries: QueueEntry[]; outcomes: Counts }) {
  if (!entries.length) {
    return (
      <Empty
        title="Nothing has earned validation yet"
        detail={
          'A candidate reaches this queue when it has enough trades, a positive ' +
          'development result, a grid wide enough for the overfitting test, and both ' +
          'conformance and determinism measured.'
        }
      />
    )
  }
  return (
    <>
      <div className="campaign-scale">
        <Stat label="Passed" value={outcomes.PASS ?? 0} tone={outcomes.PASS ? 'good' : 'plain'} />
        <Stat label="Failed" value={outcomes.FAIL ?? 0} tone={outcomes.FAIL ? 'bad' : 'plain'} />
        <Stat
          label="Not measured"
          value={outcomes.INCONCLUSIVE ?? 0}
          tone="unknown"
          note="the evidence was never produced"
        />
      </div>
      <table className="data-table">
        <thead>
          <tr><th>Strategy</th><th>State</th><th>Outcome</th><th>Why</th><th>Queued</th></tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.entry_id}>
              <td className="mono">{entry.strategy_id}</td>
              <td>{entry.state.toLowerCase()}</td>
              <td>{entry.outcome ?? '—'}</td>
              <td>{entry.reasons.join('; ')}</td>
              <td className="mono muted">{entry.queued_at.slice(0, 16).replace('T', ' ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function SourceList({
  sources, queries, enabled,
}: {
  sources: { id: string; title: string; url: string; source: string; published: string
    retrieved_at: string; claims: { text: string }[]; relevance: number }[]
  queries: { query: string; count: number; error: string; at: string }[]
  enabled: boolean
}) {
  if (!enabled) {
    return (
      <Empty
        title="External research is off"
        detail={
          'This campaign was created with web research disabled, so nothing was retrieved. ' +
          'No source is ever invented to fill the gap.'
        }
      />
    )
  }
  return (
    <>
      {!!queries.length && (
        <section className="af-panel">
          <PanelHead title="Searches run" meta={`${queries.length}`} />
          <ul className="query-list">
            {queries.map((query, index) => (
              <li key={`${query.at}-${index}`}>
                <span className="mono">{query.query}</span>
                <span className={query.error ? 'warning bad' : 'muted'}>
                  {query.error ? query.error : `${query.count} result(s)`}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">
            A search that found nothing is recorded too: "we looked and the literature had
            nothing" is a research fact, and an absence of records is not.
          </p>
        </section>
      )}
      {sources.length ? (
        <ul className="source-list">
          {sources.map((item) => (
            <li key={item.id} className="af-panel">
              <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
              <span className="mono muted">
                {item.source}{item.published ? ` · ${item.published}` : ''} · retrieved{' '}
                {item.retrieved_at.slice(0, 10)}
              </span>
              {!!item.claims.length && (
                <ul className="claim-list">
                  {item.claims.map((claim, index) => (
                    <li key={index}>“{claim.text}”</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Empty title="Nothing retrieved yet" detail="Sources appear here as they are found." />
      )}
    </>
  )
}

function CampaignForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const [name, setName] = useState('NQ Intraday Alpha Discovery')
  const [objective, setObjective] = useState(
    'Discover intraday alpha on NQ one-minute bars across the full available history, ' +
    'preferring mechanisms that can be stated and falsified.',
  )
  const [dataset, setDataset] = useState('nq_1m_16y')
  const [symbol, setSymbol] = useState('NQ')
  const [webResearch, setWebResearch] = useState(false)
  const [maxExperiments, setMaxExperiments] = useState(500)
  const [error, setError] = useState<string | null>(null)

  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  const create = useMutation({
    mutationFn: () => send<Campaign>('/campaigns', 'POST', {
      name,
      objective,
      dataset,
      symbol,
      web_research: webResearch,
      stopping: { max_experiments: maxExperiments },
    }),
    onSuccess: onCreated,
    onError: (e: Error) => setError(e.message),
  })

  const chosen = datasets.data?.find((d) => d.key === dataset)

  return (
    <form
      className="campaign-form af-panel"
      onSubmit={(event) => { event.preventDefault(); create.mutate() }}
    >
      <PanelHead title="New research campaign" />
      <label className="field-block">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
      </label>
      <label className="field-block">
        Objective
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          rows={3}
          minLength={20}
          required
        />
        <small className="muted">
          What is being researched, on what, and what would count. "Find alpha" is not an
          objective and is refused.
        </small>
      </label>
      <div className="campaign-form-row">
        <label className="field-block">
          Dataset
          <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
            {datasets.data?.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label} · {d.is_real ? d.provider : 'synthetic'} · {d.cost_note}
              </option>
            ))}
          </select>
        </label>
        <label className="field-block">
          Symbol
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} maxLength={16} />
        </label>
        <label className="field-block">
          Experiment budget
          <input
            type="number"
            min={10}
            max={100000}
            value={maxExperiments}
            onChange={(e) => setMaxExperiments(Number(e.target.value))}
          />
        </label>
      </div>
      <label className="field-inline">
        <input type="checkbox" checked={webResearch} onChange={(e) => setWebResearch(e.target.checked)} />
        Search arXiv and Crossref for mechanisms
        <small className="muted">
          Only what a fetch returns is stored, with its URL and retrieval time. Nothing is
          invented when the network is unavailable.
        </small>
      </label>
      {chosen && !chosen.is_real && (
        <p className="warning">
          The synthetic dataset is edge-free by design and cannot clear the judge's data gate,
          so nothing run on it can be validated. Useful for exercising the machinery, never for
          a discovery.
        </p>
      )}
      {error && <p className="warning bad">{error}</p>}
      <div className="campaign-form-actions">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary" disabled={create.isPending}>
          {create.isPending ? 'Creating…' : 'Create campaign'}
        </button>
      </div>
    </form>
  )
}
