/* The bar series a chart is panning through, and the rules for growing it.
 *
 * A chart that fetches one window and draws it is a picture of a market. To be
 * a navigation surface it has to hold a *series* that grows at either end as
 * the viewport reaches for bars it does not have — and growing a series is
 * where the real failures live:
 *
 *   - a page that overlaps the one before it draws the same candle twice, and
 *     lightweight-charts rejects a series with a repeated timestamp outright;
 *   - a page appended in arrival order rather than time order produces a chart
 *     that folds back on itself;
 *   - a prepend shifts every bar's index, so a viewport expressed in indices
 *     silently jumps backwards by exactly the number of bars just loaded —
 *     which reads to the person dragging as the chart snapping away from them.
 *
 * All three are arithmetic, none of them need a DOM, and so none of them live
 * in the component. This module is the whole of that arithmetic, and
 * `bars.test.ts` is the whole of its proof.
 */

/** One candle, as the `/bars` route serves it. */
export type Bar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** One window of bars, and where it sits in the archive behind it. */
export type BarsResponse = {
  dataset: string
  symbol: string
  timeframe: string
  timeframe_label: string
  convention: string
  authority: string
  is_real: boolean
  bar_count: number
  total_bars: number
  /** The archive's own bounds at this timeframe. Null when it holds nothing. */
  coverage_start: string | null
  coverage_end: string | null
  /** This window's bounds. Null when the window is empty. */
  range_start: string | null
  range_end: string | null
  has_more_before: boolean
  has_more_after: boolean
  /** Retained under its original name: "more history to the left". */
  has_more: boolean
  bars: Bar[]
}

/**
 * The series a chart is currently holding.
 *
 * `bars` is always ascending by time and free of duplicates — that is the
 * invariant every function here preserves and every test here checks.
 */
export type Series = {
  bars: Bar[]
  /** Whether a pan in each direction can still load anything. */
  hasMoreBefore: boolean
  hasMoreAfter: boolean
  /** The archive's bounds, carried so "exhausted" can be told from "empty". */
  coverageStart: string | null
  coverageEnd: string | null
}

export const EMPTY: Series = {
  bars: [],
  hasMoreBefore: false,
  hasMoreAfter: false,
  coverageStart: null,
  coverageEnd: null,
}

/**
 * Whether a merge should be attempted at all.
 *
 * A response for a different dataset or timeframe is not a page of this series,
 * it is a different series — and merging one into the other produces a chart
 * showing two instruments at once, silently, with no error anywhere.
 */
export function describes(page: BarsResponse, dataset: string, timeframe: string): boolean {
  return page.dataset === dataset && page.timeframe === timeframe
}

/** Which way a page was fetched. A page only settles the edge it travelled to. */
export type Direction = 'before' | 'after'

/** Merge one page into a series, preserving order and dropping repeats.
 *
 * Repeats are dropped rather than overwritten. Bars in an archive are
 * immutable, so two deliveries of the same timestamp carry the same candle;
 * preferring the existing one keeps the merge stable — the same pages applied
 * in any order produce the same series.
 *
 * Only the flag for `direction` is taken from the page. A request for the bars
 * *before* a window says nothing about whether more exist after the ones
 * already held — it reports `has_more_after: true` simply because it cut the
 * series — and adopting that would erase what an earlier page established
 * about the right-hand edge.
 */
export function merge(series: Series, page: BarsResponse, direction: Direction): Series {
  const known = new Set(series.bars.map((bar) => bar.time))
  const added = page.bars.filter((bar) => !known.has(bar.time))
  const bars = added.length ? [...series.bars, ...added] : series.bars
  if (added.length) bars.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))

  return {
    bars,
    hasMoreBefore: direction === 'before' ? page.has_more_before : series.hasMoreBefore,
    hasMoreAfter: direction === 'after' ? page.has_more_after : series.hasMoreAfter,
    // Coverage describes the archive rather than the window, so every page
    // carries the same bounds and the newest answer is as good as the first.
    coverageStart: page.coverage_start ?? series.coverageStart,
    coverageEnd: page.coverage_end ?? series.coverageEnd,
  }
}

/** The series a first page establishes, replacing anything held before it. */
export function adopt(page: BarsResponse): Series {
  const bars = [...page.bars].sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
  return {
    bars,
    hasMoreBefore: page.has_more_before,
    hasMoreAfter: page.has_more_after,
    coverageStart: page.coverage_start,
    coverageEnd: page.coverage_end,
  }
}

/** The cursor to page backwards from: the oldest bar currently held. */
export function olderThan(series: Series): string | null {
  return series.bars[0]?.time ?? null
}

/** The cursor to page forwards from: the newest bar currently held. */
export function newerThan(series: Series): string | null {
  return series.bars[series.bars.length - 1]?.time ?? null
}

/**
 * How close to an edge the viewport must come before more bars are fetched.
 *
 * Expressed in bars rather than pixels so it means the same thing at every zoom
 * level. Large enough that the request is usually in flight before the empty
 * space is reached, small enough that an idle chart does not page the whole
 * archive into the browser.
 */
export const PREFETCH_BARS = 120

/** A visible range in bar indices, as lightweight-charts reports it. */
export type LogicalRange = { from: number; to: number }

/** Which direction, if any, a viewport is reaching past the bars it has. */
export function wants(
  series: Series,
  range: LogicalRange | null,
  threshold: number = PREFETCH_BARS,
): 'before' | 'after' | null {
  if (!range || series.bars.length === 0) return null
  // `from` goes negative once the viewport is past the first bar, which is
  // exactly the state this exists to catch — so the comparison is against the
  // threshold rather than against zero.
  if (range.from < threshold && series.hasMoreBefore) return 'before'
  if (range.to > series.bars.length - threshold && series.hasMoreAfter) return 'after'
  return null
}

/**
 * The viewport to restore after bars were prepended.
 *
 * Indices are positions in the series, so inserting `count` bars at the front
 * moves every existing bar `count` places to the right. A viewport left alone
 * would therefore be looking `count` bars further back than the person dragging
 * put it — the chart appearing to leap away from the cursor at the exact moment
 * it loaded what they reached for. Shifting the range by the same count holds
 * the same candles under the same pixels.
 */
export function shift(range: LogicalRange, count: number): LogicalRange {
  return { from: range.from + count, to: range.to + count }
}
