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
        roles: {
          chat: { model: 'mid-1', fallback: '', critic: '', enabled: true },
          agent_discovery: { model: 'big-1', fallback: 'mid-1', critic: '', enabled: true },
          agent_reviewer: { model: 'big-1', fallback: '', critic: '', enabled: true },
        },
      },
      budget: {
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
    routing_modes: [
      { key: 'manual', label: 'Manual', detail: 'Only the model you assign, never a substitute.' },
      { key: 'hybrid', label: 'Hybrid', detail: 'Your assignment first, then your fallback.' },
      { key: 'smart', label: 'Smart', detail: 'AlgoForge chooses from the models you allow.' },
    ],
    routing_preview: [
      {
        role: 'chat', provider: 'opencode_go', model: 'mid-1', source: 'assigned',
        reason: 'Console chat is assigned mid-1 in settings.',
        substituted: false, considered: [], critic: '',
      },
      {
        role: 'agent_discovery', provider: 'opencode_go', model: 'mid-1', source: 'fallback',
        reason: "'big-1' is not served by the opencode_go provider, so the fallback answered.",
        substituted: true, considered: ['big-1'], critic: '',
      },
      {
        role: 'agent_reviewer', provider: 'opencode_go', model: 'big-1', source: 'assigned',
        reason: 'Review agent is assigned big-1 in settings.',
        substituted: false, considered: [], critic: '',
      },
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
  expect(screen.getByText(/1 role\(s\) are not using the model they are assigned/))
    .toBeInTheDocument()
  const row = screen.getByText('Discovery agent').closest('tr')!
  expect(within(row).getByText(/not served by the opencode_go provider/)).toBeInTheDocument()
})

test('a role using what it was assigned is not reported as a substitution', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  const row = screen.getByText('Review agent').closest('tr')!
  expect(within(row).getByText(/assigned big-1/)).toBeInTheDocument()
})

test('fallback and critic are behind progressive disclosure', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  expect(screen.queryByLabelText('Fallback for Discovery agent')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Fallback and critic'))
  expect(screen.getByLabelText('Fallback for Discovery agent')).toBeInTheDocument()
  expect(screen.getByLabelText('Critic for Discovery agent')).toBeInTheDocument()
})

test('a role campaigns cannot run without is not offered a switch', () => {
  draw(<ModelRoutingPanel settings={payload()} patch={vi.fn()} />)
  fireEvent.click(screen.getByText('Fallback and critic'))
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

/* ── budget enforcement is a switch, and safety is not budget ─────────────── */

test('budget enforcement can be turned off', () => {
  const patch = vi.fn()
  draw(<BudgetPanel settings={payload()} patch={patch} />)
  fireEvent.click(screen.getByText('Enforcement ON'))
  expect(patch).toHaveBeenCalledWith({ budget_enforced: false })
})

test('with enforcement off the screen says no ceiling is applied', () => {
  const off = payload()
  off.ai.budget.enforced = false
  draw(<BudgetPanel settings={off} patch={vi.fn()} />)
  expect(screen.getByText(/No research ceiling is being enforced/)).toBeInTheDocument()
  expect(screen.getByLabelText('Model calls per day')).toBeDisabled()
})

test('the limits that are not budget are listed beside the switch', () => {
  const off = payload()
  off.ai.budget.enforced = false
  draw(<BudgetPanel settings={off} patch={vi.fn()} />)
  expect(screen.getByText('In force whatever this switch says')).toBeInTheDocument()
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
