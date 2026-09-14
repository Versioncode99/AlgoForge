/* The equity fan: the percentile band of account balances, day by day.
 *
 * Everything here is pure so it can be tested without a chart. The component
 * draws what these functions return and does not compute anything of its own,
 * which is the only way the number under the scrubber and the shape on screen
 * are guaranteed to be the same number.
 */

export type FanPoint = {
  day: number
  p05: number
  p25: number
  median: number
  p75: number
  p95: number
  /** Accounts still trading on this day. */
  live: number
  /** Accounts that had already passed, failed or timed out. */
  resolved: number
}

export type FanReading = {
  point: FanPoint
  /** Total accounts, which is constant across every day by construction. */
  total: number
  /** p95 − p05: how far apart the outer fifths are on this day. */
  spread: number
  /** Median minus the starting balance, so the sign is readable directly. */
  fromStart: number
  /** Share of accounts still trading, 0–1. */
  stillTrading: number
}

/** Clamp a day to one the fan actually has, so a scrubber can never read off the end. */
export function clampDay(points: readonly FanPoint[], day: number): number {
  if (points.length === 0) return 0
  const first = points[0].day
  const last = points[points.length - 1].day
  if (!Number.isFinite(day)) return last
  return Math.min(last, Math.max(first, Math.round(day)))
}

/** The point for a day, or the nearest one the fan holds. */
export function readingAt(points: readonly FanPoint[], day: number, start: number): FanReading | null {
  if (points.length === 0) return null
  const wanted = clampDay(points, day)
  const point = points.find((p) => p.day === wanted) ?? points[points.length - 1]
  const total = point.live + point.resolved
  return {
    point,
    total,
    spread: point.p95 - point.p05,
    fromStart: point.median - start,
    stillTrading: total === 0 ? 0 : point.live / total,
  }
}

/** The day on which the fewest accounts are still trading — where attrition has done its work. */
export function lastLiveDay(points: readonly FanPoint[]): number {
  if (points.length === 0) return 0
  return points[points.length - 1].day
}

/* ── what the chart draws ──────────────────────────────────────────────────
 *
 * ECharts draws a band as a transparent baseline plus stacked areas on top of
 * it, so each band is expressed as a *height* above the one below rather than
 * as an absolute value. Getting that wrong silently draws a band in the wrong
 * place, so it is computed once here and tested.
 */

export type FanSeries = {
  days: number[]
  /** The invisible floor the bands stack on. */
  base: number[]
  /** p05 → p25. */
  lower: number[]
  /** p25 → p75, the interquartile band. */
  middle: number[]
  /** p75 → p95. */
  upper: number[]
  median: number[]
  live: number[]
}

export function seriesOf(points: readonly FanPoint[]): FanSeries {
  return {
    days: points.map((p) => p.day),
    base: points.map((p) => p.p05),
    lower: points.map((p) => p.p25 - p.p05),
    middle: points.map((p) => p.p75 - p.p25),
    upper: points.map((p) => p.p95 - p.p75),
    median: points.map((p) => p.median),
    live: points.map((p) => p.live),
  }
}

/** One sentence saying what the band on this day is and is not.
 *
 * The attrition caveat is not decoration: once accounts start closing, a
 * narrowing band means fewer accounts are moving, not that the outcome became
 * more certain. A reader who is not told that will read it the other way.
 */
export function describe(reading: FanReading): string {
  const { point, total } = reading
  if (point.resolved === 0) {
    return `All ${total.toLocaleString()} accounts were still trading on day ${point.day}.`
  }
  if (point.live === 0) {
    return (
      `Every account had closed by day ${point.day}. The band is their final balances, ` +
      `so it stops moving from here.`
    )
  }
  return (
    `${point.live.toLocaleString()} of ${total.toLocaleString()} accounts were still trading on ` +
    `day ${point.day}; the other ${point.resolved.toLocaleString()} are held at the balance they ` +
    `closed at. A band that narrows from here is accounts leaving, not agreement.`
  )
}
