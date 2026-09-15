import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { SettingsPayload } from '../types'
import { BudgetPanel, ModelRoutingPanel, ResearchPanel } from './ModelRouting'

/* The settings screen, and whether it is telling the truth.
 *
 * Three failures these are written against, all of the same kind: a control
 * that appeared to work and did not, or a number that appeared to mean one
 * thing and meant another.
 *
 * - A role table covering nine workflow roles while the engine ran ten research
 *   agents of its own, so "a model per role" chose the model for none of the
 *   research.
 * - A substitution the operator never saw: the assignment was displayed and a
 *   different model answered.
 * - A budget that could not be turned off, on an installation where the dollar
 *   figures mean nothing.
 *
 * These render the real panels rather than helpers, because every one of those
 * defects was in what got drawn.
 */

const MODELS = [
  { id: 'big-1', label: 'Big', tier: 'frontier', status: 'ok', note: '' },
  { id: 'mid-1', label: 'Mid', tier: 'fast', status: 'ok', note: '' },
  { id: 'small-1', label: 'Small', tier: 'economy', status: 'needs_credit', note: '' },
]

const ROLES = [
  {
    key: 'chat', label: 'Console chat', detail: 'The assistant you talk to in the app',
    kind: 'workflow' as const, demand: 'balanced', optional: false,
  },
  {
    key: 'agent_discovery', label: 'Discovery agent',
    detail: 'Proposes mechanisms nothing on record already claims',
    kind: 'research' as const, demand: 'reasoning', optional: false,
  },
  {
    key: 'agent_reviewer', label: 'Review agent',
    detail: 'Critiques lineage and evidence quality rather than results',
    kind: 'research' as const, demand: 'reasoning', optional: true,
  },
]

function payload(overrides: Partial<SettingsPayload> = {}): SettingsPayload {
  const base = {
    ai: {
      enabled: true,
      provider: 'opencode_go',
      base_url: 'https://opencode.ai/zen/go/v1',
      routing: { chat: 'mid-1' },
      model_routing: {
        mode: 'hybrid',
        default_model: 'mid-1',
        fallback_model: 'mid-1',
        allowed: [],
        features: { research: 'big-1' },
        flat_migrated: true,
        roles: {
          chat: { model: 'mid-1', recommended: 'mid-1', fallback: '', enabled: true },
          agent_discovery: {
            model: 'big-1', recommended: 'big-1', fallback: 'mid-1', enabled: true,
          },
          agent_reviewer: { model: 'big-1', recommended: 'big-1', fallback: '', enabled: true },
        },
      },
      budget: {
        mode: 'ENFORCED',
        enforced: true,
        daily_usd_hard: 12, daily_usd_soft: 9, monthly_usd_hard: 300,
        per_session_usd: 1.5, halt_on_breach: true,
        campaign_experiments: 0, model_calls_per_day: 0, wall_clock_minutes: 0,
        backtests_per_campaign: 0, external_research_per_day: 0,
      },
      gateway: {
        provider: 'opencode_go', connected: true, status_code: 200, latency_ms: 40,
        model_count: 3, credential_present: true, credential_source: 'environment',
        base_url: 'https://opencode.ai/zen/go/v1', error: null,
      },
    },
    default_dataset: 'nq_1m_16y',
    engine_cycle_seconds: 6,
    engine_max_strategies: 60,
    databento_max_cost_usd: 2.5,
    research_loop: {
      enabled: true, interval_minutes: 60, topics: ['momentum'],
      categories: ['preprints', 'journals'], freshness: 'recent', depth: 'standard',
    },
    models: MODELS,
    roles: [],
    credentials: [],
    providers: [{ id: 'opencode_go', label: 'OpenCode Go', detail: 'A provider' }],
    routing_roles: ROLES,
    routing_features: [
      { key: 'chat', label: 'Chat', detail: 'The conversation', roles: ['chat'] },
      {
        key: 'research', label: 'Research', detail: 'Hypotheses and falsification',
        roles: ['agent_discovery', 'agent_reviewer'],
      },
      {
        key: 'strategy', label: 'Strategy generation', detail: 'Writing the strategy',
        roles: ['strategy_code'],
      },
      { key: 'fast', label: 'Fast tasks', detail: 'Tagging and triage', roles: ['bulk'] },
    ],
    routing_modes: [
      { key: 'manual', label: 'Manual', detail: 'Only the model you assign, never a substitute.' },
      { key: 'hybrid', label: 'Hybrid', detail: 'Your assignment first, then your fallback.' },
      { key: 'smart', label: 'Smart', detail: 'AlgoForge chooses from the models you allow.' },
    ],
    routing_preview: [
      {
        role: 'chat', provider: 'opencode_go', model: 'mid-1', source: 'assigned',
        reason: 'Console chat is assigned mid-1 in settings.',
        substituted: false, considered: [],
      },
      {
        role: 'agent_discovery', provider: 'opencode_go', model: 'mid-1', source: 'fallback',
        reason: "'big-1' is not served by the opencode_go provider, so the fallback answered.",
        substituted: true, considered: ['big-1'],
      },
      {
        role: 'agent_reviewer', provider: 'opencode_go', model: 'big-1', source: 'assigned',
        reason: 'Review agent is assigned big-1 in settings.',
        substituted: false, considered: [],
      },
    ],
    budget_modes: [
      { key: 'ENFORCED', label: 'Enforced', detail: 'The ceilings apply from the first call.' },
      {
        key: 'UNLIMITED_WITH_SAFETY_LIMITS',
        label: 'Unlimited, with safety limits',
        detail: 'No research ceiling.',
      },
      { key: 'ADAPTIVE', label: 'Adaptive', detail: 'Applies once the soft threshold is passed.' },
    ],
    safety_limits: [
      {
        key: 'model_concurrency', label: 'Concurrent model requests', value: '2',
        why: 'More in flight than the provider accepts returns errors, not answers.',
      },
      {
        key: 'permissions', label: 'Action permissions', value: 'unchanged',
        why: 'No budget setting is an input to what an assistant may do.',
      },
    ],
    research_options: {
      categories: [
        { key: 'preprints', label: 'Preprints', detail: 'arXiv q-fin' },
        { key: 'journals', label: 'Journals', detail: 'Crossref DOI metadata' },
      ],
      freshness: [
        { key: 'any', label: 'Any', detail: 'Age is not weighed.' },
        { key: 'recent', label: 'Prefer recent', detail: 'Newer ranks higher.' },
      ],
      depths: [
        { key: 'shallow', label: 'Shallow', detail: 'Three results per topic.' },
        { key: 'standard', label: 'Standard', detail: 'Six results per topic.' },
      ],
    },
  } as unknown as SettingsPayload
  return { ...base, ...overrides }
}

function draw(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

afterEach(cleanup)

/* ── the roles that do the research are reachable ─────────────────────────── */

test('the research agents appear beside the workflow roles', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  expect(screen.getByText('Discovery agent')).toBeInTheDocument()
  expect(screen.getByText('Review agent')).toBeInTheDocument()
  expect(screen.getByText('Console chat')).toBeInTheDocument()
})

test('each role has a model selector that reports the change', () => {
  const patch = vi.fn()
  draw(<ModelRoutingPanel settings={payload()} patch={patch} />)
  fireEvent.change(screen.getByLabelText('Model for Discovery agent'), {
    target: { value: 'small-1' },
  })
  expect(patch).toHaveBeenCalledWith({
    role_routing: { agent_discovery: { model: 'small-1' } },
  })
})

test('the research agents can be hidden when only the workflow matters', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  fireEvent.click(screen.getByText('Research agents shown'))
  expect(screen.queryByText('Discovery agent')).not.toBeInTheDocument()
  expect(screen.getByText('Console chat')).toBeInTheDocument()
})

/* ── a substitution is visible ────────────────────────────────────────────── */

test('a role not using its assigned model says so on the row and at the top', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  expect(screen.getByText(/1 job\(s\) are not using the model they are set to/))
    .toBeInTheDocument()
  const row = screen.getByText('Discovery agent').closest('tr')!
  expect(within(row).getByText(/not served by the opencode_go provider/)).toBeInTheDocument()
})

test('a role using what it was assigned is not reported as a substitution', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  const row = screen.getByText('Review agent').closest('tr')!
  expect(within(row).getByText(/assigned big-1/)).toBeInTheDocument()
})

test('fallback and enable are behind progressive disclosure', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  expect(screen.queryByLabelText('Fallback for Discovery agent')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Fallback and enable'))
  expect(screen.getByLabelText('Fallback for Discovery agent')).toBeInTheDocument()
})

test('a role campaigns cannot run without is not offered a switch', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  fireEvent.click(screen.getByText('Fallback and enable'))
  const required = screen.getByText('Discovery agent').closest('tr')!
  expect(within(required).getByText('always on')).toBeInTheDocument()
  const optional = screen.getByText('Review agent').closest('tr')!
  expect(within(optional).getByText('On')).toBeInTheDocument()
})

test('the routing mode is a control and carries its own explanation', () => {
  const patch = vi.fn()
  draw(<ModelRoutingPanel settings={payload()} patch={patch} />)
  expect(screen.getByText(/Your assignment first, then your fallback/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Routing mode'), { target: { value: 'manual' } })
  expect(patch).toHaveBeenCalledWith({ routing_mode: 'manual' })
})

/* ── budget is three modes, and safety is not budget ──────────────────────── */

test('each of the three modes can be chosen', () => {
  const patch = vi.fn()
  draw(<BudgetPanel settings={payload()} patch={patch} />)
  const group = screen.getByRole('group', { name: 'Budget mode' })
  expect(within(group).getAllByRole('button')).toHaveLength(3)

  fireEvent.click(within(group).getByText('Adaptive'))
  expect(patch).toHaveBeenCalledWith({ budget_mode: 'ADAPTIVE' })

  fireEvent.click(within(group).getByText('Unlimited, with safety limits'))
  expect(patch).toHaveBeenCalledWith({ budget_mode: 'UNLIMITED_WITH_SAFETY_LIMITS' })
})

test('the modes are rendered from the server list, not from a copy here', () => {
  /* A screen with its own list can offer a mode the code no longer implements. */
  const custom = payload()
  custom.budget_modes = [{ key: 'ENFORCED', label: 'Only this one', detail: 'because' }]
  draw(<BudgetPanel settings={custom} patch={vi.fn()} />)
  const group = screen.getByRole('group', { name: 'Budget mode' })
  expect(within(group).getAllByRole('button')).toHaveLength(1)
  expect(within(group).getByText('Only this one')).toBeInTheDocument()
})

test('with no ceiling enforced the screen says so, and the numbers lock', () => {
  const off = payload()
  off.ai.budget.mode = 'UNLIMITED_WITH_SAFETY_LIMITS'
  off.ai.budget.enforced = false
  draw(<BudgetPanel settings={off} patch={vi.fn()} />)
  expect(screen.getByText(/No research ceiling is being enforced/)).toBeInTheDocument()
  expect(screen.getByLabelText('Model calls per day')).toBeDisabled()
})

test('adaptive says when the ceilings start applying, and keeps them editable', () => {
  /* Locking them would make the mode unconfigurable until it started biting. */
  const adaptive = payload()
  adaptive.ai.budget.mode = 'ADAPTIVE'
  adaptive.ai.budget.enforced = true
  draw(<BudgetPanel settings={adaptive} patch={vi.fn()} />)
  expect(screen.getByText(/once today's spend passes/)).toBeInTheDocument()
  expect(screen.getByText(/not the same as knowing it is low/)).toBeInTheDocument()
  expect(screen.getByLabelText('Model calls per day')).not.toBeDisabled()
})

test('the limits that are not budget are listed beside the modes', () => {
  const off = payload()
  off.ai.budget.mode = 'UNLIMITED_WITH_SAFETY_LIMITS'
  off.ai.budget.enforced = false
  draw(<BudgetPanel settings={off} patch={vi.fn()} />)
  expect(screen.getByText('In force whatever mode is chosen')).toBeInTheDocument()
  expect(screen.getByText('Concurrent model requests')).toBeInTheDocument()
  expect(screen.getByText('Action permissions')).toBeInTheDocument()
})

/* ── external research ────────────────────────────────────────────────────── */

test('external research is shown as enabled and can be paused', () => {
  const patch = vi.fn()
  draw(<ResearchPanel settings={payload()} patch={patch} />)
  fireEvent.click(screen.getByText('Enabled'))
  expect(patch).toHaveBeenCalledWith({ research_loop_enabled: false })
})

test('a source category can be turned off and the change names the rest', () => {
  const patch = vi.fn()
  draw(<ResearchPanel settings={payload()} patch={patch} />)
  fireEvent.click(screen.getByText('Preprints'))
  expect(patch).toHaveBeenCalledWith({ research_categories: ['journals'] })
})

test('selecting no source says retrieval will find nothing rather than implying all', () => {
  const none = payload()
  none.research_loop.categories = []
  draw(<ResearchPanel settings={none} patch={vi.fn()} />)
  expect(screen.getByText(/retrieval will find nothing/)).toBeInTheDocument()
})

test('the panel states that a retrieved claim is not evidence', () => {
  draw(<ResearchPanel settings={payload()} patch={vi.fn()} />)
  expect(screen.getByText(/never evidence for an answer/)).toBeInTheDocument()
})

// ── one default, four overrides ──────────────────────────────────────────────
//
// Routing was already rich. What it offered first was a nineteen-row matrix of
// role names, which is the right granularity for the engine and the wrong one
// for a person: nobody thinks "the falsification agent and the review agent
// should use a reasoning model", they think "research should use the good one".

test('the screen leads with a default and the four overrides, not the matrix', () => {
  render(<ModelRoutingPanel settings={payload()} patch={() => undefined} />)
  expect(screen.getByLabelText('Default model')).toBeInTheDocument()
  for (const label of ['Chat', 'Research', 'Strategy generation', 'Fast tasks']) {
    expect(screen.getByLabelText(`Model for ${label}`)).toBeInTheDocument()
  }
  // The matrix is behind a disclosure, closed. Not removed: the engine runs all
  // nineteen roles and hiding them for good would cost a capability.
  const advanced = screen.getByText(/per-role routing, fallbacks and mode/i)
  expect(advanced.closest('details')).not.toHaveAttribute('open')
})

test('each override says which part of the product it changes', () => {
  render(<ModelRoutingPanel settings={payload()} patch={() => undefined} />)
  expect(screen.getByText('Hypotheses and falsification')).toBeInTheDocument()
  expect(screen.getByText('Tagging and triage')).toBeInTheDocument()
})

test('setting a feature override patches that feature and nothing else', () => {
  const patch = vi.fn()
  render(<ModelRoutingPanel settings={payload()} patch={patch} />)
  fireEvent.change(screen.getByLabelText('Model for Chat'), { target: { value: 'big-1' } })
  expect(patch).toHaveBeenCalledWith({ feature_routing: { chat: 'big-1' } })
})

test('clearing a feature override sends an empty value rather than a model named ""', () => {
  const patch = vi.fn()
  render(<ModelRoutingPanel settings={payload()} patch={patch} />)
  fireEvent.change(screen.getByLabelText('Model for Research'), { target: { value: '' } })
  expect(patch).toHaveBeenCalledWith({ feature_routing: { research: '' } })
})

test('a blank field says what it falls through to rather than saying "none"', () => {
  render(<ModelRoutingPanel settings={payload()} patch={() => undefined} />)
  // "none" would read as "nothing will answer". A feature left blank follows
  // the default; a role left blank follows its feature.
  expect(
    within(screen.getByLabelText('Model for Chat')).getByText(/follow the default/i),
  ).toBeInTheDocument()
})

test('the role matrix shows the shipped recommendation as the placeholder', () => {
  render(<ModelRoutingPanel settings={payload()} patch={() => undefined} />)
  fireEvent.click(screen.getByText(/per-role routing, fallbacks and mode/i))
  const chat = screen.getByLabelText('Model for Console chat')
  expect(within(chat).getByText(/recommended/i)).toBeInTheDocument()
})
