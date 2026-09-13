import { describe, expect, it } from 'vitest'
import { AGEING_DAYS, RECENT_DAYS, archiveAge } from './freshness'

const now = new Date('2026-09-13T12:00:00Z')
const daysAgo = (days: number) =>
  new Date(now.getTime() - days * 86_400_000).toISOString()

describe('how old an archive is', () => {
  it('reads an archive that runs to today as current', () => {
    const age = archiveAge(daysAgo(0), now)
    expect(age.currency).toBe('current')
    expect(age.label).toBe('through today')
  })

  it('is still current at the edge of the recent window', () => {
    expect(archiveAge(daysAgo(RECENT_DAYS), now).currency).toBe('current')
    expect(archiveAge(daysAgo(RECENT_DAYS + 1), now).currency).toBe('ageing')
  })

  it('is ageing up to the far edge, and old past it', () => {
    expect(archiveAge(daysAgo(AGEING_DAYS), now).currency).toBe('ageing')
    expect(archiveAge(daysAgo(AGEING_DAYS + 1), now).currency).toBe('old')
  })

  it('says why an old archive matters rather than only that it is old', () => {
    const age = archiveAge(daysAgo(400), now)
    expect(age.currency).toBe('old')
    expect(age.detail).toContain('months to change')
    expect(age.label).toBe('13 months behind')
  })

  it('counts whole days and never a negative one', () => {
    // A bar timestamped slightly in the future — a clock skew, not a prophecy.
    expect(archiveAge(daysAgo(-2), now).days).toBe(0)
    expect(archiveAge(daysAgo(3.7), now).days).toBe(3)
  })

  it('reads a missing date as unknown, which is not the same as current', () => {
    const age = archiveAge(null, now)
    expect(age.currency).toBe('unknown')
    expect(age.days).toBeNull()
    expect(age.detail).toContain('Unknown is not the same as')
  })

  it('reads an unparseable date as unknown rather than as very old', () => {
    expect(archiveAge('not a date', now).currency).toBe('unknown')
  })
})
