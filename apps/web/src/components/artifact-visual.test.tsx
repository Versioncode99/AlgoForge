import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Artifact } from '../chat'

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))

const { ArtifactVisual } = await import('./ArtifactVisual')

const artifact = (over: Partial<Artifact> = {}): Artifact => ({
  artifact_id: 'a1',
  kind: 'analysis',
  title: 'Regime × volatility',
  refs: { strategy_id: 's1', artifact_id: 'lab_1' },
  provenance: 'deterministic',
  ...over,
})

/** A real analysis payload, of the shape `/lab/artifacts/{id}` returns. */
const COMPUTED = {
  analysis: 'regime',
  title: 'Regime × volatility',
  question: 'where did the P&L come from?',
  shape: 'grid',
  measure: 'average_trade',
  measure_unit: 'USD',
  axes: [
    { name: 'trend', label: 'Trend', categories: ['bull', 'bear'] },
    { name: 'vol', label: 'Volatility', categories: ['low', 'high'] },
  ],
  cells: [
    {
      coords: [0, 0],
      labels: ['bull', 'low'],
      trade_count: 12,
      value: 4.2,
      net_pnl: 50,
      win_rate: 0.5,
      average_trade: 4.2,
      insufficient: false,
    },
  ],
  total_trades: 12,
  covered_trades: 12,
  warnings: [],
  findings: [],
  artifact_id: 'lab_1',
  content_hash: 'h',
}

let reply: { ok: boolean; body: unknown } = { ok: true, body: {} }
const fetched: string[] = []

beforeEach(() => {
  fetched.length = 0
  reply = { ok: true, body: COMPUTED }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    fetched.push(String(input))
    return new Response(JSON.stringify({ data: reply.body }), {
      status: reply.ok ? 200 : 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }))
})

afterEach(() => vi.unstubAllGlobals())

function mount(item: Artifact) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ArtifactVisual artifact={item} />
    </QueryClientProvider>,
  )
}

describe('a chart inside a turn', () => {
  it('fetches nothing until the reader asks', async () => {
    mount(artifact())
    expect(screen.getByText('Show the chart')).toBeInTheDocument()
    expect(fetched).toHaveLength(0)
  })

  it('draws from the route that computed it, once opened', async () => {
    mount(artifact())
    fireEvent.click(screen.getByText('Show the chart'))
    await waitFor(() =>
      expect(fetched.some((url) => url.includes('/lab/artifacts/lab_1'))).toBe(true),
    )
    // The real component, not a stand-in: this is the same chart the surface
    // draws, which is the property worth asserting.
    await waitFor(() => expect(document.querySelector('.analysis-chart')).not.toBeNull())
  })

  it('offers nothing at all for a kind with no honest chart', () => {
    mount(artifact({ kind: 'workspace', refs: { name: 'Desk' } }))
    expect(screen.queryByText('Show the chart')).not.toBeInTheDocument()
  })

  it('says so when the fetch fails, and draws nothing approximate', async () => {
    reply = { ok: false, body: { detail: 'gone' } }
    mount(artifact())
    fireEvent.click(screen.getByText('Show the chart'))
    expect(await screen.findByText(/could not be fetched/)).toBeInTheDocument()
    expect(document.querySelector('.analysis-chart')).toBeNull()
  })

  it('says so when the route answers with nothing to draw', async () => {
    reply = { ok: true, body: { ...COMPUTED, cells: [] } }
    mount(artifact())
    fireEvent.click(screen.getByText('Show the chart'))
    expect(await screen.findByText(/nothing to draw/)).toBeInTheDocument()
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('survives a payload of the wrong shape instead of taking the thread down', async () => {
    /* `AnalysisChart` reads `axes` without guarding it. Here the payload comes
       from whichever route an artifact named, so a malformed one must read as
       "nothing to draw" rather than as an exception. */
    reply = { ok: true, body: { cells: [{ coords: [0] }] } }
    mount(artifact())
    fireEvent.click(screen.getByText('Show the chart'))
    expect(await screen.findByText(/nothing to draw/)).toBeInTheDocument()
  })

  it('carries the caveat with the chart rather than beside it', async () => {
    mount(artifact({ kind: 'parameter_surface', refs: { strategy_id: 's1' } }))
    fireEvent.click(screen.getByText('Show the chart'))
    expect(await screen.findByText(/cannot promote anything/)).toBeInTheDocument()
  })
})
