import { describe, expect, it } from 'vitest'
import {
  EMPTY,
  PREFETCH_BARS,
  type Bar,
  type BarsResponse,
  type Series,
  adopt,
  describes,
  merge,
  newerThan,
  olderThan,
  shift,
  wants,
} from './bars'

/* Growing a chart's series is arithmetic, and this is the proof of it.
 *
 * Each failure mode here is one that produces no error and no warning — a
 * duplicated candle, a series that folds back on itself, a viewport that
 * leaps backwards by exactly the number of bars just loaded. They are only
 * visible to someone dragging the chart, or to a test that does the arithmetic.
 */

const hour = (index: number) =>
  new Date(Date.UTC(2026, 0, 1, index)).toISOString().replace('.000Z', '+00:00')

function bar(index: number): Bar {
  return { time: hour(index), open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }
}

function page(
  from: number,
  count: number,
  extra: Partial<BarsResponse> = {},
): BarsResponse {
  const bars = Array.from({ length: count }, (_, offset) => bar(from + offset))
  return {
    dataset: 'nq',
    symbol: 'NQ',
    timeframe: '1h',
    timeframe_label: 'Hourly',
    convention: 'UTC session close',
    authority: 'archive',
    is_real: true,
    bar_count: bars.length,
    total_bars: 1000,
    coverage_start: hour(0),
    coverage_end: hour(999),
    range_start: bars[0]?.time ?? null,
    range_end: bars[bars.length - 1]?.time ?? null,
    has_more_before: true,
    has_more_after: true,
    has_more: true,
    bars,
    ...extra,
  }
}

const times = (series: Series) => series.bars.map((b) => b.time)

// ── the first window ─────────────────────────────────────────────────────────

describe('adopt', () => {
  it('takes both edges from the page that established the series', () => {
    const series = adopt(page(900, 100, { has_more_before: true, has_more_after: false }))
    expect(series.bars).toHaveLength(100)
    expect(series.hasMoreBefore).toBe(true)
    expect(series.hasMoreAfter).toBe(false)
    expect(series.coverageStart).toBe(hour(0))
  })

  it('orders a page that arrived out of order', () => {
    const scrambled = page(10, 3)
    scrambled.bars.reverse()
    expect(times(adopt(scrambled))).toEqual([hour(10), hour(11), hour(12)])
  })
})

// ── merging ──────────────────────────────────────────────────────────────────

describe('merge', () => {
  it('puts an earlier page in front of the series, in time order', () => {
    const series = merge(adopt(page(100, 10)), page(90, 10), 'before')
    expect(times(series)).toEqual(Array.from({ length: 20 }, (_, i) => hour(90 + i)))
  })

  it('puts a later page after the series, in time order', () => {
    const series = merge(adopt(page(100, 10)), page(110, 10), 'after')
    expect(times(series)).toEqual(Array.from({ length: 20 }, (_, i) => hour(100 + i)))
  })

  it('drops a bar it already holds rather than drawing it twice', () => {
    // Overlapping pages are the normal case at a boundary, not an error, and
    // lightweight-charts rejects a series with a repeated timestamp outright.
    const series = merge(adopt(page(100, 10)), page(95, 10), 'before')
    expect(times(series)).toHaveLength(15)
    expect(new Set(times(series)).size).toBe(15)
  })

  it('is stable: the same pages in any order give the same series', () => {
    const forwards = merge(merge(adopt(page(100, 10)), page(90, 10), 'before'), page(110, 10), 'after')
    const backwards = merge(merge(adopt(page(100, 10)), page(110, 10), 'after'), page(90, 10), 'before')
    expect(times(forwards)).toEqual(times(backwards))
  })

  it('only settles the edge it travelled to', () => {
    /* A backward page reports has_more_after: true simply because it cut the
     * series above its window. Adopting that would resurrect a right-hand edge
     * an earlier page had already established as exhausted, and the chart would
     * keep asking the server for bars that do not exist. */
    const latest = adopt(page(900, 100, { has_more_after: false }))
    const grown = merge(latest, page(800, 100, { has_more_after: true }), 'before')
    expect(grown.hasMoreAfter).toBe(false)
    expect(grown.hasMoreBefore).toBe(true)
  })

  it('records reaching the start of history', () => {
    const grown = merge(adopt(page(100, 10)), page(0, 100, { has_more_before: false }), 'before')
    expect(grown.hasMoreBefore).toBe(false)
  })

  it('survives an empty page without losing what it holds', () => {
    // What the server sends once a pan reaches the archive's first bar.
    const held = adopt(page(0, 50))
    const grown = merge(
      held,
      page(0, 0, { has_more_before: false, range_start: null, range_end: null }),
      'before',
    )
    expect(times(grown)).toEqual(times(held))
    expect(grown.hasMoreBefore).toBe(false)
    expect(grown.coverageStart).toBe(hour(0))
  })

  it('keeps coverage when a page cannot report it', () => {
    const held = adopt(page(100, 10))
    const grown = merge(held, page(0, 0, { coverage_start: null, coverage_end: null }), 'before')
    expect(grown.coverageStart).toBe(hour(0))
    expect(grown.coverageEnd).toBe(hour(999))
  })
})

// ── which series a page belongs to ───────────────────────────────────────────

describe('describes', () => {
  it('refuses a page for a different instrument', () => {
    /* A page that arrives after the operator switched symbol would otherwise
     * merge into the series on screen — producing one chart showing two
     * instruments, with no error anywhere. */
    expect(describes(page(0, 1, { dataset: 'es' }), 'nq', '1h')).toBe(false)
    expect(describes(page(0, 1, { timeframe: '1d' }), 'nq', '1h')).toBe(false)
    expect(describes(page(0, 1), 'nq', '1h')).toBe(true)
  })
})

// ── cursors ──────────────────────────────────────────────────────────────────

describe('cursors', () => {
  it('pages from the bars actually held, at either end', () => {
    const series = adopt(page(100, 10))
    expect(olderThan(series)).toBe(hour(100))
    expect(newerThan(series)).toBe(hour(109))
  })

  it('has no cursor for an empty series', () => {
    expect(olderThan(EMPTY)).toBeNull()
    expect(newerThan(EMPTY)).toBeNull()
  })
})

// ── when to fetch ────────────────────────────────────────────────────────────

describe('wants', () => {
  const series = adopt(page(100, 500))

  it('asks for history as the viewport nears the left edge', () => {
    expect(wants(series, { from: 10, to: 210 })).toBe('before')
  })

  it('asks for history when the viewport is already past the first bar', () => {
    // `from` goes negative once the chart is showing empty space to the left.
    expect(wants(series, { from: -80, to: 120 })).toBe('before')
  })

  it('asks for nothing in the middle of a loaded series', () => {
    expect(wants(series, { from: 200, to: 300 })).toBeNull()
  })

  it('asks for nothing when the edge is already exhausted', () => {
    const exhausted = adopt(page(0, 500, { has_more_before: false, has_more_after: false }))
    expect(wants(exhausted, { from: -50, to: 150 })).toBeNull()
    expect(wants(exhausted, { from: 400, to: 600 })).toBeNull()
  })

  it('asks for newer bars as the viewport nears the right edge', () => {
    expect(wants(series, { from: 380, to: 520 })).toBe('after')
  })

  it('prefers history when the viewport spans the whole series', () => {
    /* Both edges are in reach at once when the chart is zoomed out past the
     * loaded range. Firing two requests would double the work and interleave
     * two prepends; history is the direction a person is actually dragging
     * toward, so it wins and the other follows on the next frame. */
    const short = adopt(page(100, 40))
    expect(wants(short, { from: -20, to: 60 })).toBe('before')
  })

  it('asks for nothing before any bars have arrived', () => {
    expect(wants(EMPTY, { from: 0, to: 100 })).toBeNull()
  })

  it('asks for nothing when the chart has not reported a range', () => {
    expect(wants(series, null)).toBeNull()
  })

  it('uses a threshold measured in bars, so zoom does not change it', () => {
    expect(PREFETCH_BARS).toBeGreaterThan(0)
    expect(wants(series, { from: PREFETCH_BARS - 1, to: 300 })).toBe('before')
    expect(wants(series, { from: PREFETCH_BARS + 1, to: 300 })).toBeNull()
  })
})

// ── keeping the viewport still ───────────────────────────────────────────────

describe('shift', () => {
  it('moves the viewport by exactly the bars inserted in front of it', () => {
    /* Without this the chart leaps backwards by the size of the page at the
     * moment it loads what the person reached for — which reads as the chart
     * fighting the drag. */
    expect(shift({ from: -30, to: 170 }, 500)).toEqual({ from: 470, to: 670 })
  })

  it('is a no-op when nothing was prepended', () => {
    const range = { from: 12, to: 212 }
    expect(shift(range, 0)).toEqual(range)
  })

  it('preserves the span, so zoom survives a load', () => {
    const range = { from: -30, to: 170 }
    const moved = shift(range, 240)
    expect(moved.to - moved.from).toBe(range.to - range.from)
  })
})
