import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ResearchControlView } from './ResearchControl'

/* Health, on the card an operator watches after pressing start.
 *
 * The scheduler scores a campaign with no history 1.0 so it is allocated the
 * workers it needs to earn a real score. That prior is right where it lives.
 * Rendered as a percentage it said "Health 100%" about a campaign that had run
 * no cycles at all — a fabricated measurement, shown at the one moment somebody
 * is looking to find out whether the thing they started is working.
 *
 * These render the real view rather than the helper, because the defect was in
 * the JSX and a test of the helper would not have caught it.
 */

const PROGRESS = {
  experiments: 0, hypotheses: 0, families_created: 0, templates_created: 0,
  mechanisms: 0, duplicates_rejected: 0, blocked_proposals: 0,
  consecutive_duplicates: 0, failures: 0, promising: 0, validation_candidates: 0,
  validated: 0, inconclusive: 0, sources_retrieved: 0, followups_generated: 0,
  compute_units: 0, spend: {},
}

const CAMPAIGN = {
  campaign_id: 'camp_1',
  name: 'NQ opening expansion',
  objective: 'Investigate whether overnight compression conditions the opening session.',
  dataset: 'nq_1m_16y', symbol: 'NQ', timeframe: '1m', status: 'running',
  progress: PROGRESS, exhausted: false, exhausted_reason: '', stopped_reason: '',
  created_at: '2026-09-12T10:00:00+00:00', updated_at: '2026-09-12T10:00:00+00:00',
  description: '', priority: 50, agent_target: 1, archived: false,
  parent_campaign_id: '', tags: [],
}

const AGENT_COUNTS = {
  total: 0, eligible: 0, stale: 0, experiments: 0, findings: 0, errors: 0,
  by_state: {}, by_role: {},
}

const runtime = (health: number, observed: number) => ({
  campaign_id: 'camp_1', workers: 1, health, health_observed: observed,
  barren_run: 0, stalled: false, last_progress_at: '', assigned: 1,
  recent_outcomes: {},
})

const centre = (rt: ReturnType<typeof runtime> | null) => ({
  totals: {
    campaigns: 1, running: 1, experiments: 0, hypotheses: 0, mechanisms: 0,
    families_created: 0, templates_created: 0, followups: 0, compute_units: 0,
  },
  campaigns: [CAMPAIGN],
  allocation: {
    engine_state: 'RUNNING',
    running_campaigns: rt
      ? [{
          campaign_id: 'camp_1', name: CAMPAIGN.name, priority: 50,
          dataset: 'nq_1m_16y', symbol: 'NQ', agent_target: 1,
          runtime: rt, agents: AGENT_COUNTS, progress: PROGRESS,
        }]
      : [],
    worker_plan: ['camp_1'], stalled: [],
  },
  agents: AGENT_COUNTS,
  skips: { total: 0, distinct: 0, useful: 0, wasted: 0, neutral: 0, by_kind: {}, by_level: {} },
  validation: { attempts: 0, passed: 0, failed: 0, inconclusive: 0, blocked: 0, pending: 0 },
  frontier: {}, capacity: { max_agents: 8, ceiling: 8 },
})

let payload: unknown = centre(runtime(1.0, 0))

beforeEach(() => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const data = url.includes('/control-center') ? payload
      : url.includes('/engine') ? { state: 'RUNNING', workers: 1 }
      : {}
    return new Response(JSON.stringify({ data, meta: {} }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
})

afterEach(cleanup)

const draw = async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ResearchControlView />
    </QueryClientProvider>,
  )
  return within(await screen.findByText(CAMPAIGN.name).then((n) => n.closest('article')!))
}

const healthCell = (card: ReturnType<typeof within>) =>
  card.getByText('Health').parentElement!.querySelector('dd')!

test('a running campaign that has completed no cycle is not 100% healthy', async () => {
  payload = centre(runtime(1.0, 0))
  const cell = healthCell(await draw())
  expect(cell).toHaveTextContent('no cycles yet')
  expect(cell).not.toHaveTextContent('100%')
  expect(cell.title).toMatch(/no cycle has completed/i)
})

test('once cycles are observed the share is shown, with the sample behind it', async () => {
  payload = centre(runtime(0.75, 40))
  const cell = healthCell(await draw())
  expect(cell).toHaveTextContent('75%')
  expect(cell.title).toMatch(/last 40 cycles/)
})

test('a campaign nobody is observing shows no health at all', async () => {
  payload = centre(null)
  const cell = healthCell(await draw())
  expect(cell).toHaveTextContent('—')
  expect(cell.title).toMatch(/not running/i)
})

test('one observed cycle is reported in the singular', async () => {
  payload = centre(runtime(1.0, 1))
  const cell = healthCell(await draw())
  expect(cell).toHaveTextContent('100%')
  expect(cell.title).toMatch(/last 1 cycle\b/)
})
