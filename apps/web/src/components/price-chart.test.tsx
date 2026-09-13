import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/* The wiring between a drag and a request.
 *
 * `bars.test.ts` proves the arithmetic — merge, dedupe, order, viewport shift.
 * It cannot prove that the component ever runs it. This file mounts the chart
 * against a fake charting library and drives the one callback the real one
 * fires while a person drags, then checks what reached the network and what was
 * handed back to the time scale.
 *
 * The bug being held closed is a chart that loads one window and stops. It
 * throws nothing, logs nothing, and looks correct in every screenshot.
 */

type RangeHandler = (range: { from: number; to: number } | null) => void

const timeScale = {
  fitContent: vi.fn(),
  getVisibleLogicalRange: vi.fn(() => ({ from: -40, to: 160 })),
  setVisibleLogicalRange: vi.fn(),
  subscribeVisibleLogicalRangeChange: vi.fn((handler: RangeHandler) => {
    handlers.push(handler)
  }),
  unsubscribeVisibleLogicalRangeChange: vi.fn((handler: RangeHandler) => {
    handlers.splice(handlers.indexOf(handler), 1)
  }),
}
let handlers: RangeHandler[] = []

const candleSeries = { setData: vi.fn() }
const volumeSeries = { setData: vi.fn() }

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles',
  HistogramSeries: 'volume',
  createChart: vi.fn(() => ({
    addSeries: vi.fn((kind: string) => (kind === 'candles' ? candleSeries : volumeSeries)),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => timeScale),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    remove: vi.fn(),
  })),
}))

const requested: string[] = []
vi.mock('../api', () => ({
  getJson: vi.fn(async (path: string) => {
    requested.push(path)
    const url = new URL(path, 'http://x')
    const before = url.searchParams.get('before')
    const after = url.searchParams.get('after')
    if (before) return window.__pages.before
    if (after) return window.__pages.after
    return window.__pages.first
  }),
}))

declare global {
  interface Window {
    __pages: { first: unknown; before: unknown; after: unknown }
  }
}

import { PriceChart } from './PriceChart'

const hour = (index: number) =>
  new Date(Date.UTC(2026, 0, 1, index)).toISOString().replace('.000Z', '+00:00')

function page(from: number, count: number, extra: Record<string, unknown> = {}) {
  const bars = Array.from({ length: count }, (_, offset) => ({
    time: hour(from + offset),
    open: 1,
    high: 2,
    low: 0.5,
    close: 1.5,
    volume: 10,
  }))
  return {
    dataset: 'nq',
    symbol: 'NQ',
    timeframe: '1h',
    timeframe_label: 'Hourly',
    convention: 'UTC',
    authority: 'archive',
    is_real: true,
    bar_count: bars.length,
    total_bars: 5000,
    coverage_start: hour(0),
    coverage_end: hour(4999),
    range_start: bars[0]?.time ?? null,
    range_end: bars[bars.length - 1]?.time ?? null,
    has_more_before: true,
    has_more_after: false,
    has_more: true,
    bars,
    ...extra,
  }
}

beforeEach(() => {
  handlers = []
  requested.length = 0
  timeScale.setVisibleLogicalRange.mockClear()
  timeScale.fitContent.mockClear()
  candleSeries.setData.mockClear()
  window.__pages = {
    first: page(400, 200),
    before: page(200, 200),
    after: page(600, 200, { has_more_after: false }),
  }
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => {
    fn(0)
    return 0
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The draws that actually put candles on the chart.
 *
 * A mount clears the canvas with an empty series before the first window
 * arrives, which is correct — it is how switching instrument blanks the old
 * one — and is not a draw anyone sees. Counting it would make every assertion
 * here off by one for a reason that has nothing to do with paging. */
function draws(): unknown[][] {
  return candleSeries.setData.mock.calls
    .map((call) => call[0] as unknown[])
    .filter((data) => data.length > 0)
}

async function mounted() {
  render(<PriceChart dataset="nq" timeframe="1h" limit={200} />)
  await waitFor(() => expect(draws().length).toBeGreaterThan(0))
}

/** Drive the callback lightweight-charts fires while a person drags. */
async function drag(range: { from: number; to: number }) {
  await act(async () => {
    handlers.forEach((fire) => fire(range))
    await Promise.resolve()
  })
}

describe('PriceChart', () => {
  it('draws the newest window on load and fits it once', async () => {
    await mounted()
    expect(requested[0]).toContain('dataset=nq')
    expect(requested[0]).not.toContain('before=')
    expect(timeScale.fitContent).toHaveBeenCalledTimes(1)
    expect(draws()[0]).toHaveLength(200)
  })

  it('asks for earlier bars when the viewport is dragged past the left edge', async () => {
    await mounted()
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(requested.some((path) => path.includes('before='))).toBe(true))
    // The cursor is the oldest bar actually held, not the range that was asked
    // for: the server is the authority on what it returned.
    expect(requested.find((path) => path.includes('before='))).toContain(
      `before=${encodeURIComponent(hour(400))}`,
    )
  })

  it('keeps the same candles under the cursor after a prepend', async () => {
    await mounted()
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(timeScale.setVisibleLogicalRange).toHaveBeenCalled())
    // 200 bars went in front, so the viewport moves 200 places to keep still.
    expect(timeScale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 160, to: 360 })
  })

  it('never refits after a pan, which would undo the pan', async () => {
    await mounted()
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(draws().length).toBe(2))
    expect(timeScale.fitContent).toHaveBeenCalledTimes(1)
  })

  it('merges the page into the series rather than replacing it', async () => {
    await mounted()
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(draws().length).toBe(2))
    expect(draws()[1]).toHaveLength(400)
  })

  it('collapses a burst of range events into one request', async () => {
    /* A drag fires this callback on every frame. Without the in-flight guard
     * each frame becomes a request for the same window, and the same bars are
     * merged a dozen times over. */
    await mounted()
    await act(async () => {
      for (let frame = 0; frame < 12; frame += 1) {
        handlers.forEach((fire) => fire({ from: -40 - frame, to: 160 - frame }))
      }
      await Promise.resolve()
    })
    await waitFor(() => expect(requested.some((path) => path.includes('before='))).toBe(true))
    expect(requested.filter((path) => path.includes('before=')).length).toBe(1)
  })

  it('stops asking once the archive has no more history', async () => {
    window.__pages.before = page(0, 200, { has_more_before: false })
    await mounted()
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(screen.getByText('Start of archive')).toBeInTheDocument())

    const asked = requested.filter((path) => path.includes('before=')).length
    await drag({ from: -60, to: 140 })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(requested.filter((path) => path.includes('before=')).length).toBe(asked)
  })

  it('does not claim the start of the archive while history remains', async () => {
    await mounted()
    expect(screen.queryByText('Start of archive')).not.toBeInTheDocument()
  })

  it('keeps the drawn bars when a pan fails', async () => {
    /* A failed request for older bars does not make the bars already on screen
     * any less real, and blanking the chart to an error state would throw away
     * what the operator was looking at. */
    await mounted()
    const { getJson } = await import('../api')
    vi.mocked(getJson).mockRejectedValueOnce(new Error('provider down'))
    await drag({ from: -40, to: 160 })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(draws().length).toBe(1)
  })

  it('refuses a page that arrives for an instrument no longer on screen', async () => {
    /* Switching symbol mid-request would otherwise merge one market's bars into
     * another's series — one chart, two instruments, and no error anywhere. */
    await mounted()
    window.__pages.before = page(200, 200, { dataset: 'es' })
    await drag({ from: -40, to: 160 })
    await waitFor(() => expect(requested.some((path) => path.includes('before='))).toBe(true))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(draws().length).toBe(1)
  })
})
