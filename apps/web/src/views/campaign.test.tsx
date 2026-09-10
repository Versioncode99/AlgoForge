import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ResearchCampaignView } from './ResearchCampaign'

/* The campaign surface, exercised on the shape the API actually returns.
 *
 * The assertions are about the one thing this screen exists to say: a search
 * that ran two hundred experiments against three mechanisms is narrow, and the
 * interface has to make that visible rather than reporting two hundred
 * discoveries. Everything else here holds the states that mean "no evidence
 * either way" apart from the state that means "disproven".
 */

const counts = (overrides: Record<string, number> = {}) => ({
  UNKNOWN: 0, UNTESTED: 0, PARTIALLY_EXPLORED: 0, INCONCLUSIVE: 0, PROMISING: 0,
  VALIDATED: 0, FAILED: 0, BLOCKED_BY_DATA: 0, EXHAUSTED: 0, ...overrides,
})

const OVERVIEW = {
  campaign: {
    campaign_id: 'camp_1',
    name: 'NQ Intraday Alpha Discovery',
    objective: 'Discover intraday alpha on NQ one-minute bars across the available history.',
    dataset: 'nq_1m_16y',
    symbol: 'NQ',
    timeframe: '1m',
    status: 'running',
    web_research: false,
    allocation: {
      EXPLORE_HYPOTHESIS: 0.35, DISCOVER_FAMILY: 0.25, ADVANCE_PROMISING: 0.2,
      REFINE_PARAMETERS: 0.1, ROBUSTNESS: 0.1,
    },
    stopping: { max_experiments: 500 },
    allowed_capabilities: ['BARS', 'VOLUME', 'SESSION_CLOCK'],
    exhausted: false,
    exhausted_reason: '',
    stopped_reason: '',
    progress: {
      experiments: 214, hypotheses: 19, families_created: 3, templates_created: 21,
      mechanisms: 11, duplicates_rejected: 7, blocked_proposals: 2, failures: 160,
      promising: 6, validation_candidates: 4, validated: 1, inconclusive: 12,
      sources_retrieved: 0, followups_generated: 9, compute_units: 410,
      spend: {
        EXPLORE_HYPOTHESIS: 60, DISCOVER_FAMILY: 30, ADVANCE_PROMISING: 40,
        REFINE_PARAMETERS: 70, ROBUSTNESS: 14,
      },
    },
  },
  frontier: {
    counts: counts({ UNTESTED: 12, PROMISING: 6, FAILED: 22, BLOCKED_BY_DATA: 4, VALIDATED: 1 }),
    kinds: { PARAMETER: 30, STRUCTURAL: 8, HYPOTHESIS: 12, MECHANISM: 4, FAMILY: 3 },
    open: 12, blocked: 4, settled: 23,
  },
  hypotheses: { counts: {}, mechanisms: 11 },
  validation: { pending: 3, outcomes: { PASS: 1, FAIL: 2, INCONCLUSIVE: 5 } },
  sources: 0,
  journal_head: 812,
  generated_templates: {},
}

const EVENTS = {
  head: 812,
  events: [
    {
      event_id: 810, kind: 'HYPOTHESIS_PROPOSED', level: 'pass',
      message: 'hypothesis: A break of the opening range resolves directionally for the session.',
      subject: 'hyp_1', detail: {}, at: '2026-09-10T19:42:19+00:00',
    },
    {
      event_id: 811, kind: 'HYPOTHESIS_REJECTED', level: 'warn',
      message: "the same claim and the same mechanism as 'hyp_0' (statement 95%, mechanism 88%).",
      subject: null, detail: {}, at: '2026-09-10T19:42:21+00:00',
    },
    {
      event_id: 812, kind: 'FAMILY_CREATED', level: 'pass',
      message: "created family 'discovered_volatility_expansion'",
      subject: 'discovered_volatility_expansion', detail: {}, at: '2026-09-10T19:42:23+00:00',
    },
  ],
}

const FRONTIER = [
  {
    item_id: 'fr_1',
    question: 'Is the edge conditional on volatility expansion rather than volatility level?',
    family: 'volatility', mechanism: 'variance is persistent', state: 'UNTESTED',
    reason: 'derived from a walk-forward failure', search_kind: 'HYPOTHESIS',
    novelty: 0.82, experiments: 0, missing_data: [], schedulable: true,
    updated_at: '2026-09-10T19:42:19+00:00',
  },
  {
    item_id: 'fr_2',
    question: 'Does resting depth imbalance predict the next aggressive trade direction?',
    family: 'microstructure', mechanism: 'queue position', state: 'BLOCKED_BY_DATA',
    reason: 'requires L2_MBP, which this installation cannot serve', search_kind: 'MECHANISM',
    novelty: 0.91, experiments: 0, missing_data: ['L2_MBP'], schedulable: false,
    updated_at: '2026-09-10T19:40:00+00:00',
  },
]

let overview: unknown = OVERVIEW

beforeEach(() => {
  overview = OVERVIEW
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const data = url.includes('/campaigns/active') ? overview
      : url.includes('/events') ? EVENTS
      : url.includes('/frontier') ? FRONTIER
      : url.includes('/hypotheses') ? []
      : url.includes('/validation') ? { queue: [], pending: 3, outcomes: {} }
      : url.includes('/sources') ? { sources: [], queries: [] }
      : url.endsWith('/campaigns') ? []
      : url.endsWith('/datasets') ? []
      : {}
    return new Response(JSON.stringify({ data, meta: {} }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
})

afterEach(cleanup)

const draw = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ResearchCampaignView />
    </QueryClientProvider>,
  )
}

test('mechanisms and hypotheses are reported separately from experiments', async () => {
  const { container } = draw()
  // The whole point: 214 trials against 11 mechanisms is not 214 discoveries,
  // and all four figures are on screen at once so the shape is visible.
  await screen.findByText('Experiments')
  const band = within(container.querySelector('.campaign-scale') as HTMLElement)
  for (const [label, value] of [
    ['Experiments', '214'], ['Mechanisms', '11'], ['Hypotheses', '19'], ['Families', '3'],
  ]) {
    expect(band.getByText(label).parentElement).toHaveTextContent(value)
  }
})

test('an untested question is not drawn as a failure', async () => {
  draw()
  const untested = await screen.findByTitle(/^Untested/)
  const failed = await screen.findByTitle(/^Failed/)
  expect(untested.closest('li')?.dataset.tone).toBe('open')
  expect(failed.closest('li')?.dataset.tone).toBe('failed')
  expect(untested.closest('li')?.dataset.tone).not.toBe(failed.closest('li')?.dataset.tone)
})

test('blocked-on-data has its own state and names what is missing', async () => {
  draw()
  const blocked = await screen.findByTitle(/^Blocked on data/)
  expect(blocked.closest('li')?.dataset.tone).toBe('blocked')
  expect(await screen.findByText(/needs L2_MBP/)).toBeInTheDocument()
})

test('the frontier legend says untested is not failed', async () => {
  draw()
  expect(await screen.findByText(/Untested is not failed/)).toBeInTheDocument()
})

test('the budget shows what was intended against what was consumed', async () => {
  draw()
  const label = await screen.findByText('Parameter refinement')
  const row = label.closest('li')
  // Intended 10%, actually consumed 70/214 of the drawn cycles — the gap is the
  // number worth seeing, so both are printed.
  expect(row).toHaveTextContent('10%')
  expect(row).toHaveTextContent('33%')
})

test('the event stream renders real recorded events in the order they happened', async () => {
  draw()
  const stream = (await screen.findByText(/created family/)).closest('ol')
  expect(stream).not.toBeNull()
  const entries = within(stream as HTMLElement).getAllByRole('listitem')
  expect(entries).toHaveLength(3)
  // Proposed, then refused, then created: a narrative reads forwards.
  expect(entries[0]).toHaveTextContent('hypothesis:')
  expect(entries[entries.length - 1]).toHaveTextContent('created family')
  // A refused duplicate is shown rather than hidden: it is what the gate did.
  expect(stream).toHaveTextContent(/same claim and the same mechanism/)
})

test('a campaign with web research off says so instead of showing nothing', async () => {
  draw()
  expect(
    await screen.findByText(/no source is retrieved and none is invented/i),
  ).toBeInTheDocument()
})

test('with no campaign it explains what one is rather than showing an engine toggle', async () => {
  overview = null
  draw()
  expect(await screen.findByText('No campaign is running')).toBeInTheDocument()
  expect(screen.getByText(/an objective with a budget/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /new campaign/i })).toBeInTheDocument()
})
