import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ContextBar } from './ContextBar'

/* The subject line, and the two things it must not do.
 *
 * It must not render placeholders for facets nobody has set — six dashes is a
 * row of controls that look broken, and an unset context is not a fault. And
 * it must not conflate facets: §7 asks that mode, workspace, instrument,
 * campaign, strategy and account stay six different facts, which is the
 * category error the previous phase found in this very bar.
 */

let context: Record<string, string> = {}
let posts: { url: string; body: unknown }[] = []

beforeEach(() => {
  posts = []
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST') {
      posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null })
      if (url.endsWith('/actions/describe_context')) {
        return {
          ok: true,
          text: async () => JSON.stringify({
            data: { workspace_id: 'w1', context, groups: {}, panel_groups: [], panels: [] },
          }),
        } as Response
      }
      return { ok: true, text: async () => JSON.stringify({ data: {} }) } as Response
    }
    return { ok: true, text: async () => JSON.stringify({ data: [] }) } as Response
  }) as unknown as typeof fetch
})
afterEach(cleanup)

const EMPTY = { instrument: '', timeframe: '', dataset: '', campaign: '', strategy: '', account: '' }

const show = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ContextBar /></QueryClientProvider>)
}

test('an unset context renders nothing at all', async () => {
  context = { ...EMPTY }
  const { container } = show()
  await waitFor(() => expect(posts.length).toBeGreaterThan(0))
  expect(container.querySelector('.context-subject')).toBeNull()
})

test('only the facets that are set appear', async () => {
  context = { ...EMPTY, instrument: 'NQ', timeframe: '1m' }
  show()
  expect(await screen.findByText('NQ')).toBeInTheDocument()
  expect(screen.queryByLabelText(/clear campaign context/i)).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/clear account context/i)).not.toBeInTheDocument()
})

test('each facet is labelled as itself rather than run together', async () => {
  context = { ...EMPTY, instrument: 'NQ', campaign: 'NQ Momentum', strategy: 'ORB', account: 'apex-1' }
  show()
  await screen.findByText('NQ')
  for (const facet of ['instrument', 'campaign', 'strategy', 'account']) {
    expect(screen.getByLabelText(new RegExp(`clear ${facet} context`, 'i'))).toBeInTheDocument()
  }
})

test('the timeframe rides with the instrument rather than taking a chip of its own', async () => {
  // "1m" on its own means nothing.
  context = { ...EMPTY, instrument: 'NQ', timeframe: '5m' }
  const { container } = show()
  await screen.findByText('NQ')
  expect(container.querySelectorAll('.context-chip')).toHaveLength(1)
  expect(screen.getByText('5m')).toBeInTheDocument()
})

test('clearing one facet calls the registry for that facet only', async () => {
  context = { ...EMPTY, instrument: 'NQ', campaign: 'NQ Momentum' }
  show()
  fireEvent.click(await screen.findByLabelText(/clear instrument context/i))
  await waitFor(() =>
    expect(posts.some(p => p.url.endsWith('/actions/clear_context'))).toBe(true),
  )
  const call = posts.find(p => p.url.endsWith('/actions/clear_context'))
  expect(call?.body).toEqual({ arguments: { facet: 'instrument' } })
})


test('a payload without a context does not take the shell header down', async () => {
  // The bar sits in the shell header, so a malformed or empty response would
  // otherwise break every screen rather than one chip row.
  globalThis.fetch = vi.fn(async () =>
    ({ ok: true, text: async () => JSON.stringify({ data: {} }) }) as Response,
  ) as unknown as typeof fetch
  const { container } = show()
  await waitFor(() => expect(container.querySelector('.context-subject')).toBeNull())
})
