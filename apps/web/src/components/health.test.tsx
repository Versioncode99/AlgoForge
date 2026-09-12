import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ServiceHealth } from './ServiceHealth'

/* Source health, and the three things it must not say.
 *
 * A source nobody called must not read as healthy. A missing credential must
 * not read as a failure. A capability this build does not have must appear,
 * because an absent row reads as "fine".
 */

const HEALTH = {
  services: [
    {
      name: 'databento', tier: 'RESEARCH_GRADE', state: 'UNCONFIGURED',
      ok: 0, failed: 0, last_latency_ms: null, last_success: null,
      observed: 'since this process started',
      explain: { what: 'databento is not configured.', why: 'no credential is set', impact: 'Nothing from this source is available.', remedy: 'Supply what it needs.' },
    },
    {
      name: 'crypto-public', tier: 'INDICATIVE', state: 'NOT_OBSERVED',
      ok: 0, failed: 0, last_latency_ms: null, last_success: null,
      observed: 'since this process started',
      explain: { what: 'crypto-public has not been called since this process started.', why: 'No request has needed it yet.', impact: 'Its availability is unknown.', remedy: '' },
    },
    {
      name: 'archive', tier: 'RESEARCH_GRADE', state: 'HEALTHY',
      ok: 12, failed: 0, last_latency_ms: 41.2, last_success: '2026-09-12T10:00:00Z',
      observed: 'since this process started',
      explain: { what: 'archive is answering.', why: '12 successful call(s).', impact: 'None.', remedy: '' },
    },
  ],
  calendars: [
    { provider: 'manual', available: true, reason: '3 event(s) recorded locally', state: 'HEALTHY' },
    { provider: 'fred', available: false, reason: 'FRED_API_KEY is not set', state: 'UNCONFIGURED' },
  ],
  absent: [
    { capability: 'Headline news', state: 'UNCONFIGURED', what: 'There is no headline news feed in this build.', why: 'No licensed source is configured.', impact: 'No panel shows headlines.', remedy: 'Configure a licensed news provider.' },
  ],
  tiers: [
    { tier: 'EXECUTION_GRADE', rank: 4, means: 'Licensed, timestamped and revision-documented.' },
    { tier: 'INDICATIVE', rank: 2, means: 'Fit to look at, not to conclude from.' },
  ],
}

beforeEach(() => {
  globalThis.fetch = vi.fn(async () =>
    ({ ok: true, text: async () => JSON.stringify({ data: HEALTH }) }) as Response,
  ) as unknown as typeof fetch
})
afterEach(cleanup)

const show = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ServiceHealth /></QueryClientProvider>)
}

test('a source nobody has called does not read as healthy', async () => {
  show()
  const row = await screen.findByText('crypto-public')
  const summary = row.closest('summary')
  expect(summary).toHaveTextContent('NOT OBSERVED')
  expect(summary).toHaveTextContent(/no calls yet/i)
  expect(summary).not.toHaveTextContent('HEALTHY')
})

test('a missing credential reads as unconfigured, not as a failure', async () => {
  // The remedies are completely different, and an operator sent to check
  // connectivity for an absent API key has been sent to the wrong place.
  show()
  const summary = (await screen.findByText('databento')).closest('summary')
  expect(summary).toHaveTextContent('NOT CONFIGURED')
  expect(summary).not.toHaveTextContent('FAILING')
})

test('a row explains what, why, impact and remedy', async () => {
  show()
  // Scoped to the row: every row carries these labels, which is the point.
  const row = (await screen.findByText('databento')).closest('details') as HTMLElement
  const terms = within(row).getAllByRole('term').map(node => node.textContent)
  expect(terms).toEqual(['What', 'Why', 'Impact', 'Remedy'])
})

test('a healthy row has nothing to remedy and does not pretend otherwise', async () => {
  show()
  const row = (await screen.findByText('archive')).closest('details') as HTMLElement
  expect(within(row).getAllByRole('term').map(node => node.textContent)).toEqual(['What', 'Why', 'Impact'])
})

test('a capability this build lacks is named rather than omitted', async () => {
  show()
  const row = (await screen.findByText('Headline news')).closest('details') as HTMLElement
  expect(within(row).getAllByText(/no headline news feed in this build/i).length).toBeGreaterThan(0)
  expect(within(row).getByText(/configure a licensed news provider/i)).toBeInTheDocument()
})

test('counters say what window they cover', async () => {
  show()
  const summary = (await screen.findByText('archive')).closest('summary')
  expect(summary).toHaveTextContent(/since this process started/i)
})

test('the tier ladder says a lower tier is blocked, not substituted', async () => {
  show()
  expect(await screen.findByText(/blocked, not substituted/i)).toBeInTheDocument()
})

test('an unreadable calendar source explains what it stops', async () => {
  show()
  const row = (await screen.findByText('fred')).closest('details') as HTMLElement
  expect(within(row).getByText(/cannot be confirmed either way/i)).toBeInTheDocument()
})
