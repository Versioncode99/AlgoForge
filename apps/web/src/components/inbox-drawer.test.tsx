import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InboxItem } from '../inbox'
import { InboxDrawer } from './InboxDrawer'

/* What the drawer must and must not say.
 *
 * The one it must not: anything about what the work found. A result lives in
 * its artifact, and a summary here would be a second record able to disagree
 * with it -- so an item says that work finished, and where to go and look.
 */

const item = (over: Partial<InboxItem> = {}): InboxItem => ({
  item_id: 'inb_1',
  job_id: 'job_1',
  kind: 'backtest',
  label: 'momentum · 60,000 bars',
  outcome: 'done',
  error: '',
  refs: { strategy_id: 's1' },
  seconds: 42,
  arrived_at: '2026-09-13T12:00:00+00:00',
  read_at: '',
  dismissed_at: '',
  ...over,
})

let items: InboxItem[] = []
const calls: { url: string; method: string }[] = []

beforeEach(() => {
  items = [item()]
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET' })
    const unread = items.filter((each) => !each.read_at).length
    const body = url.includes('/inbox?') || url.endsWith('/inbox')
      ? { data: { items, unread } }
      : { data: { changed: true, unread } }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function mount(open = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <InboxDrawer open={open} onClose={() => {}} />
    </QueryClientProvider>,
  )
}

describe('the finished-work drawer', () => {
  it('lists what finished, and how long it took', async () => {
    mount()
    expect(await screen.findByText('momentum · 60,000 bars')).toBeInTheDocument()
    expect(screen.getByText('Finished in 42s.')).toBeInTheDocument()
  })

  it('says nothing about what the work found', async () => {
    mount()
    await screen.findByText('momentum · 60,000 bars')
    // Nothing resembling a result: no P&L, no verdict, no pass rate.
    expect(screen.queryByText(/P&L|PASS|FAIL|Sharpe/)).not.toBeInTheDocument()
  })

  it('offers the way back to what the work was about', async () => {
    mount()
    const open = await screen.findByRole('link', { name: 'Open' })
    expect(open).toHaveAttribute('href', '#strategies?strategy=s1&pane=summary')
  })

  it('says there is no destination rather than guessing one', async () => {
    items = [item({ kind: 'something_new', refs: {} })]
    mount()
    expect(await screen.findByText('No destination')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open' })).not.toBeInTheDocument()
  })

  it('keeps a failure, with its reason', async () => {
    items = [item({ outcome: 'failed', error: 'ValueError: no bars in that window' })]
    mount()
    expect(await screen.findByText(/no bars in that window/)).toBeInTheDocument()
  })

  it('marks one read', async () => {
    mount()
    fireEvent.click(await screen.findByLabelText('Mark momentum · 60,000 bars read'))
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/inbox/inb_1/read') && c.method === 'POST'))
        .toBe(true),
    )
  })

  it('offers no mark-read for something already read', async () => {
    items = [item({ read_at: '2026-09-13T12:01:00+00:00' })]
    mount()
    await screen.findByText('momentum · 60,000 bars')
    expect(screen.queryByLabelText(/Mark .* read/)).not.toBeInTheDocument()
  })

  it('dismisses', async () => {
    mount()
    fireEvent.click(await screen.findByLabelText('Dismiss momentum · 60,000 bars'))
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/inbox/inb_1') && c.method === 'DELETE')).toBe(true),
    )
  })

  it('cannot mark all read when nothing is unread', async () => {
    items = [item({ read_at: '2026-09-13T12:01:00+00:00' })]
    mount()
    await screen.findByText('momentum · 60,000 bars')
    expect(screen.getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  })

  it('says failures land here too, when nothing has yet', async () => {
    items = []
    mount()
    expect(await screen.findByText(/including the runs that fail/)).toBeInTheDocument()
  })

  it('is inert when closed, so its buttons are not in the tab order', async () => {
    mount(false)
    const drawer = screen.getByLabelText('Finished work')
    expect(drawer).toHaveAttribute('inert')
  })
})
