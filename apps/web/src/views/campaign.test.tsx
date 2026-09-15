import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
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
    start_date: '2024-01-01',
    end_date: '2026-01-01',
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

/* What `campaign_scope` answers. The real shape, because the chooser reads
 * every one of these and a fixture missing one would let a blank readout pass
 * here and be blank in the product. */
const SCOPE = {
  dataset: 'nq_1m_16y',
  available_start: '2009-11-05T00:00:00+00:00',
  available_end: '2026-01-01T00:00:00+00:00',
  available_years: 16.16,
  selected: true,
  selected_start: '2024-01-01T00:00:00+00:00',
  selected_end: '2026-01-01T00:00:00+00:00',
  selected_days: 731,
  selected_years: 2,
  approximate_bars: 720_000,
  bars_per_year: 360_000,
  warmup_bars: 200,
  required_bars: 1_015,
  sufficient: true,
  clamped: false,
  method: 'FIXED_DATE_RANGE',
  fingerprint: 'a1b2c3d4e5f60718',
  timezone: 'UTC. Every timestamp in the archive and in this window is UTC.',
  warning: '',
  note: 'Warm-up is loaded from before the window and purged out of the scored partitions, '
    + 'so the evaluation range is the window itself.',
}

const DATASETS = [{
  key: 'nq_1m_16y', label: 'NQ 1m, 16 years', symbol: 'NQ', interval: '1m',
  provider: 'databento', authority: 'ARCHIVE', is_real: true, cost_note: 'local',
  loaded: true, bar_count: 15_300_000, is_imported: true, available: true,
  span_years: 16.16, bars_per_year: 360_000, ranges: [],
}]

let overview: unknown = OVERVIEW

//: Campaigns the list shows beside the active one. Empty by default so the
//: existing tests see what they always saw.
let others: unknown[] = []
//: Every request the view made, so a test can assert what it sent rather than
//: only what it rendered.
let sent: { method: string; url: string }[] = []

beforeEach(() => {
  overview = OVERVIEW
  others = []
  sent = []
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    sent.push({ method: (init?.method ?? 'GET').toUpperCase(), url })
    const data = url.includes('/campaigns/active') ? overview
      : url.includes('/events') ? EVENTS
      : url.includes('/frontier') ? FRONTIER
      : url.includes('/hypotheses') ? []
      : url.includes('/validation') ? { queue: [], pending: 3, outcomes: {} }
      : url.includes('/sources') ? { sources: [], queries: [] }
      : url.includes('/campaigns/scope') ? SCOPE
      : url.endsWith('/campaigns') ? others
      : url.endsWith('/datasets') ? DATASETS
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

// ── the research window ──────────────────────────────────────────────────────
//
// `start_date` and `end_date` were on the model, stored, and returned by the
// API. The form never sent them and the detail never showed them, so every
// campaign silently ran on `tail(250_000)` — about nine months, always the most
// recent nine months, whatever the hypothesis was about.

test('the campaign states the window it researched, beside its objective', async () => {
  draw()
  const window = await screen.findByText(/research window/i)
  expect(window.parentElement).toHaveTextContent('2024-01-01')
  expect(window.parentElement).toHaveTextContent('2026-01-01')
})

test('a campaign with no window says so rather than implying one was chosen', async () => {
  overview = {
    ...OVERVIEW,
    campaign: { ...(OVERVIEW as { campaign: Record<string, unknown> }).campaign, start_date: '', end_date: '' },
  }
  draw()
  expect(await screen.findByText(/whatever the engine last loaded/i)).toBeInTheDocument()
})

test('creating a campaign offers every required preset and a custom range', async () => {
  draw()
  fireEvent.click(await screen.findByRole('button', { name: /new campaign/i }))
  const group = await screen.findByRole('radiogroup', { name: /research window/i })
  for (const label of [
    '1 month', '3 months', '6 months', '1 year', '2 years', '3 years', '5 years',
    '10 years', 'Full dataset', 'Custom range',
  ]) {
    expect(within(group).getByRole('radio', { name: label })).toBeInTheDocument()
  }
})

test('the chooser states the window, the bars, the warm-up and the fingerprint', async () => {
  draw()
  fireEvent.click(await screen.findByRole('button', { name: /new campaign/i }))
  await screen.findByRole('radiogroup', { name: /research window/i })
  // Every figure comes from `campaign_scope`, which calls the same
  // `Campaign.time_scope` the engine calls. A form doing its own arithmetic
  // would be a second answer to "what will this run on".
  const window = (await screen.findByText(/^Selected window$/)).parentElement as HTMLElement
  expect(within(window).getByText(/2024-01-01 — 2026-01-01/)).toBeInTheDocument()
  expect(screen.getByText('720,000')).toBeInTheDocument()
  expect(screen.getByText(/purged out of the scored partitions/i)).toBeInTheDocument()
  expect(screen.getByText('a1b2c3d4e5f60718')).toBeInTheDocument()
  expect(screen.getByText(/UTC/)).toBeInTheDocument()
})

test('choosing a custom range reveals the two date fields', async () => {
  draw()
  fireEvent.click(await screen.findByRole('button', { name: /new campaign/i }))
  const group = await screen.findByRole('radiogroup', { name: /research window/i })
  fireEvent.click(within(group).getByRole('radio', { name: 'Custom range' }))
  expect(await screen.findByLabelText(/^From$/)).toBeInTheDocument()
  expect(screen.getByLabelText(/^To$/)).toBeInTheDocument()
})

test('a window too small to partition blocks creation rather than warning after the fact', async () => {
  draw()
  fireEvent.click(await screen.findByRole('button', { name: /new campaign/i }))
  await screen.findByRole('radiogroup', { name: /research window/i })
  await screen.findByText(/^Selected window$/)
  expect(screen.getByRole('button', { name: /create campaign/i })).toBeEnabled()

  cleanup()
  const insufficient = { ...SCOPE, sufficient: false, warning: 'About 900 bars. … cannot be split …' }
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const data = url.includes('/campaigns/scope') ? insufficient
      : url.includes('/campaigns/active') ? overview
      : url.endsWith('/datasets') ? DATASETS
      : url.endsWith('/campaigns') ? others
      : {}
    return new Response(JSON.stringify({ data, meta: {} }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch

  draw()
  fireEvent.click(await screen.findByRole('button', { name: /new campaign/i }))
  expect(await screen.findByText(/cannot be split/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /create campaign/i })).toBeDisabled()
})

/* ── deleting a campaign ──────────────────────────────────────────────────────
 *
 * The route has existed since campaigns were written and nothing in the product
 * could reach it: a campaign could be created, started and resumed, and never
 * removed. These hold the control that now reaches it, and the sentence beside
 * it — deleting a campaign removes the programme and keeps what it found, and a
 * delete button that did not say so would read as "delete my research".
 */
const EARLIER = {
  campaign_id: 'camp_old',
  name: 'An earlier programme',
  objective: 'Something that has finished.',
  status: 'stopped',
  start_date: '',
  end_date: '',
  stopped_reason: 'experiment budget reached',
  progress: { experiments: 12, hypotheses: 3, mechanisms: 2 },
}

test('a finished campaign can be deleted, and the control says what survives', async () => {
  overview = null
  others = [EARLIER]
  draw()

  fireEvent.click(await screen.findByRole('button', { name: 'Delete An earlier programme' }))
  // The consequence is on the confirming button, where the decision is made,
  // not in a paragraph above the list.
  const confirm = await screen.findByRole('button', { name: /findings kept/i })
  fireEvent.click(confirm)

  await screen.findByText(/An earlier programme/)
  expect(sent.some((row) => row.method === 'DELETE' && row.url.endsWith('/campaigns/camp_old')))
    .toBe(true)
})

test('deleting takes two clicks, and the first one can be taken back', async () => {
  overview = null
  others = [EARLIER]
  draw()

  fireEvent.click(await screen.findByRole('button', { name: 'Delete An earlier programme' }))
  fireEvent.click(await screen.findByRole('button', { name: /^Cancel$/ }))
  expect(screen.queryByRole('button', { name: /findings kept/i })).not.toBeInTheDocument()
  expect(sent.every((row) => row.method !== 'DELETE')).toBe(true)
})

test('a campaign is not deleted by the first click', async () => {
  /* The failure this guards against is a list where "Delete" beside "Resume"
   * removes a programme on a mis-click. */
  overview = null
  others = [EARLIER]
  draw()

  fireEvent.click(await screen.findByRole('button', { name: 'Delete An earlier programme' }))
  expect(sent.every((row) => row.method !== 'DELETE')).toBe(true)
})
