import { describe as group, expect, it } from 'vitest'
import { clampDay, describe, lastLiveDay, readingAt, seriesOf, type FanPoint } from './fan'

const point = (day: number, over: Partial<FanPoint> = {}): FanPoint => ({
  day,
  p05: 49000,
  p25: 49500,
  median: 50000,
  p75: 50600,
  p95: 51200,
  live: 100,
  resolved: 0,
  ...over,
})

const fan: FanPoint[] = [
  point(0, { p05: 50000, p25: 50000, median: 50000, p75: 50000, p95: 50000 }),
  point(1),
  point(2, { live: 90, resolved: 10 }),
  point(3, { live: 0, resolved: 100, p05: 47000, median: 50100 }),
]

group('clampDay', () => {
  it('never reads off either end', () => {
    expect(clampDay(fan, -5)).toBe(0)
    expect(clampDay(fan, 99)).toBe(3)
    expect(clampDay(fan, 2)).toBe(2)
  })

  it('rounds a fractional day rather than missing the point', () => {
    expect(clampDay(fan, 1.6)).toBe(2)
  })

  it('survives an empty fan and a NaN', () => {
    expect(clampDay([], 3)).toBe(0)
    expect(clampDay(fan, Number.NaN)).toBe(3)
  })
})

group('readingAt', () => {
  it('carries the total, the spread and the share still trading', () => {
    const reading = readingAt(fan, 2, 50000)!
    expect(reading.total).toBe(100)
    expect(reading.spread).toBe(51200 - 49000)
    expect(reading.fromStart).toBe(0)
    expect(reading.stillTrading).toBeCloseTo(0.9)
  })

  it('is null for a fan with nothing in it', () => {
    expect(readingAt([], 0, 50000)).toBeNull()
  })

  it('reports the sign of the median against the starting balance', () => {
    expect(readingAt(fan, 3, 50000)!.fromStart).toBe(100)
    expect(readingAt(fan, 3, 51000)!.fromStart).toBe(-900)
  })
})

group('seriesOf', () => {
  it('expresses each band as a height above the one below it', () => {
    const series = seriesOf([point(7)])
    expect(series.base).toEqual([49000])
    expect(series.lower).toEqual([500])
    expect(series.middle).toEqual([1100])
    expect(series.upper).toEqual([600])
    // Stacking must land exactly on p95, or the drawn band is not the band.
    expect(series.base[0] + series.lower[0] + series.middle[0] + series.upper[0]).toBe(51200)
  })

  it('keeps the live count alongside, so the chart can show attrition', () => {
    expect(seriesOf(fan).live).toEqual([100, 100, 90, 0])
  })
})

group('describe', () => {
  it('says so plainly while every account is still open', () => {
    expect(describe(readingAt(fan, 1, 50000)!)).toContain('still trading')
    expect(describe(readingAt(fan, 1, 50000)!)).not.toContain('narrows')
  })

  it('warns that a narrowing band is attrition once accounts have closed', () => {
    expect(describe(readingAt(fan, 2, 50000)!)).toContain('accounts leaving, not agreement')
  })

  it('says the band has stopped moving once nothing is live', () => {
    expect(describe(readingAt(fan, 3, 50000)!)).toContain('stops moving')
  })
})

group('lastLiveDay', () => {
  it('is the last day the fan holds', () => {
    expect(lastLiveDay(fan)).toBe(3)
    expect(lastLiveDay([])).toBe(0)
  })
})
