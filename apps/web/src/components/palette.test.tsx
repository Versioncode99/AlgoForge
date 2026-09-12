import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { CommandPalette } from './CommandPalette'

/* The palette as a command surface.
 *
 * The thing being pinned is that a command *runs through the action registry*.
 * Before this the palette could only navigate, so "start the NQ campaign"
 * meant finding the campaign, opening the campaign screen and clicking a
 * button — and an assistant asked to do the same thing had no verb for it at
 * all. These tests assert the call actually reaches `/actions/<name>` with the
 * right arguments, because a palette that looked like it ran commands and
 * quietly navigated instead would pass every rendering assertion.
 */

let posts: { url: string; body: unknown }[] = []
let failWith = ''

const CAMPAIGNS = [
  { campaign_id: 'c-running', name: 'NQ Momentum', status: 'running', objective: 'o', priority: 70 },
  { campaign_id: 'c-stopped', name: 'NQ Reversion', status: 'created', objective: 'o', priority: 40 },
  { campaign_id: 'c-paused', name: 'Opening Range', status: 'paused', objective: 'o', priority: 50 },
  { campaign_id: 'c-archived', name: 'Old Work', status: 'archived', objective: 'o', priority: 10 },
]

const INSTRUMENTS = [
  { root: 'NQ', exchange: 'CME', description: 'E-mini Nasdaq-100', multiplier: 20, tick_size: 0.25, tick_value: 5, product_group: 'NASDAQ100', specification: 'verified', source_note: '' },
  { root: 'RTY', exchange: 'CME', description: 'E-mini Russell 2000', multiplier: 50, tick_size: 0.1, tick_value: 5, product_group: 'RUSSELL2000', specification: 'unverified', source_note: '' },
]

const onRoute = vi.fn()
const onClose = vi.fn()

beforeEach(() => {
  posts = []
  failWith = ''
  // Module-level spies accumulate across tests, and "was it called" is the
  // assertion in two of them.
  onRoute.mockClear()
  onClose.mockClear()
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST') {
      posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null })
      if (url.endsWith('/actions/list_instruments')) {
        return { ok: true, text: async () => JSON.stringify({ data: { instruments: INSTRUMENTS } }) } as Response
      }
      if (url.endsWith('/actions/list_campaigns')) {
        return { ok: true, text: async () => JSON.stringify({ data: { campaigns: CAMPAIGNS } }) } as Response
      }
      if (failWith) {
        return {
          ok: false, status: 422,
          text: async () => JSON.stringify({ detail: { code: 'action_refused', detail: failWith } }),
        } as Response
      }
      return { ok: true, text: async () => JSON.stringify({ data: { ok: true } }) } as Response
    }
    return { ok: true, text: async () => JSON.stringify({ data: [] }) } as Response
  }) as unknown as typeof fetch
})
afterEach(cleanup)

function open() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CommandPalette open routes={[{ id: 'strategies', label: 'Strategies', group: 'Strategy', detail: 'd' }]} strategies={[]} onClose={onClose} onRoute={onRoute} />
    </QueryClientProvider>,
  )
}

const type = (value: string) =>
  fireEvent.change(screen.getByPlaceholderText(/run a command/i), { target: { value } })

const actionCalls = () => posts.filter(p => !/list_(instruments|campaigns)$/.test(p.url))


test('starting a campaign calls the registry with the campaign id', async () => {
  open()
  type('NQ Reversion')
  const row = await screen.findByRole('button', { name: /Start campaign · NQ Reversion/i })
  fireEvent.click(row)
  await waitFor(() => expect(actionCalls()).toHaveLength(1))
  expect(actionCalls()[0].url).toMatch(/\/actions\/start_campaign$/)
  expect(actionCalls()[0].body).toEqual({ arguments: { campaign_id: 'c-stopped' } })
})

test('the lifecycle offered matches the campaign’s state', async () => {
  open()
  type('campaign')
  // A running campaign can be paused or stopped, and cannot be started again.
  expect(await screen.findByRole('button', { name: /Pause campaign · NQ Momentum/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Stop campaign · NQ Momentum/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Start campaign · NQ Momentum/i })).not.toBeInTheDocument()
  // A paused one resumes.
  expect(screen.getByRole('button', { name: /Resume campaign · Opening Range/i })).toBeInTheDocument()
  // An archived one offers nothing: it cannot run without being restored.
  expect(screen.queryByRole('button', { name: /campaign · Old Work/i })).not.toBeInTheDocument()
})

test('choosing an instrument sets the context rather than navigating', async () => {
  open()
  type('Nasdaq')
  fireEvent.click(await screen.findByRole('button', { name: /^NQ/ }))
  await waitFor(() => expect(actionCalls()).toHaveLength(1))
  expect(actionCalls()[0].url).toMatch(/\/actions\/set_context$/)
  expect(actionCalls()[0].body).toEqual({ arguments: { instrument: 'NQ' } })
  expect(onRoute).not.toHaveBeenCalled()
})

test('an unverified contract specification is said so in the picker', async () => {
  // The multiplier is the number a position would be sized from.
  open()
  type('Russell')
  expect(await screen.findByRole('button', { name: /RTY.*spec unverified/i })).toBeInTheDocument()
})

test('a refusal is shown verbatim and the palette stays open', async () => {
  failWith = "'start_campaign' is a confirm action and needs explicit confirmation from the operator."
  open()
  type('NQ Reversion')
  fireEvent.click(await screen.findByRole('button', { name: /Start campaign/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/needs explicit confirmation/i)
  expect(onClose).not.toHaveBeenCalled()
})

test('a navigation row still navigates and does not call the registry', async () => {
  open()
  type('Strategies')
  fireEvent.click(await screen.findByRole('button', { name: /Strategies/i }))
  expect(onRoute).toHaveBeenCalledWith('strategies')
  expect(actionCalls()).toHaveLength(0)
})


test('an exact match sorts above a longer one that merely contains it', async () => {
  // Found in visual QA: typing NQ listed MNQ first, because a substring filter
  // has no opinion. On a keyboard-first surface that is a wrong instrument one
  // Enter away.
  open()
  type('NQ')
  const rows = await screen.findAllByRole('button')
  const instruments = rows
    .map(row => row.querySelector('strong')?.textContent)
    .filter(label => label === 'NQ' || label === 'MNQ')
  expect(instruments[0]).toBe('NQ')
})


test('an instrument ticker outranks a command that merely mentions it', async () => {
  /* Found in QA on the running application, and the reason group order is not
   * fixed once something is typed: with Commands pinned first, typing "NQ"
   * put "Start campaign · NQ Momentum" at the top because the campaign's name
   * contains NQ. Enter would have started a research campaign when the reader
   * meant to look at an instrument. */
  open()
  type('NQ')
  const rows = await screen.findAllByRole('button')
  expect(rows[0].querySelector('strong')?.textContent).toBe('NQ')
})

test('with nothing typed, commands still lead', async () => {
  open()
  // Wait for the async campaign list, not merely for the first button to
  // exist: routes render synchronously, so findAllByRole would resolve on the
  // navigation rows before any command had arrived.
  await screen.findByRole('button', { name: /Pause campaign/i })
  const rows = screen.getAllByRole('button')
  expect(rows[0].querySelector('strong')?.textContent).toMatch(/campaign/i)
})
