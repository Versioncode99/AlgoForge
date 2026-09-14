/* How current an archive is, and whether that matters for what is on screen.
 *
 * `forge.data.freshness` is the backend's envelope: a value arrives carrying
 * the provider, the tier it answers at and how fresh it is, and crossing down a
 * tier *blocks* a consumer that declared the higher one rather than warning it.
 * D1 §35 asks whether that abstraction is actually enforced, and says plainly
 * not to wire it into every subsystem -- only where freshness changes a
 * decision.
 *
 * On a chart it changes one. AlgoForge draws local archives, so a chart is
 * never stale in the market-data sense; what it can be is *old*, and a strategy
 * built by eye on an archive that stops eight months ago was built on a market
 * that may no longer exist. The archive already reports `coverage_end` and the
 * interface rendered it nowhere. This is the reading.
 *
 * It deliberately does not block anything. A chart of an old archive is a
 * legitimate thing to look at -- the history is the point -- so this is a label,
 * not a gate. The gate lives where evidence is produced.
 */

/** How old an archive is allowed to get before the label changes tone. */
export const RECENT_DAYS = 7
export const AGEING_DAYS = 90

export type Currency = 'unknown' | 'current' | 'ageing' | 'old'

export type ArchiveAge = {
  currency: Currency
  /** Whole days between the newest bar and now. Null when nothing is known. */
  days: number | null
  label: string
  detail: string
}

const DAY_MS = 86_400_000

/** The reading for an archive whose newest bar is at `coverageEnd`. */
export function archiveAge(coverageEnd: string | null, now: Date = new Date()): ArchiveAge {
  if (!coverageEnd) {
    return {
      currency: 'unknown',
      days: null,
      label: 'age unknown',
      detail:
        'This archive did not report when its newest bar is. Unknown is not the same as ' +
        'current, so it reads as unknown.',
    }
  }
  const end = new Date(coverageEnd)
  if (Number.isNaN(end.getTime())) {
    return {
      currency: 'unknown',
      days: null,
      label: 'age unknown',
      detail: 'The archive reported a date that could not be read.',
    }
  }
  const days = Math.max(0, Math.floor((now.getTime() - end.getTime()) / DAY_MS))
  if (days <= RECENT_DAYS) {
    return {
      currency: 'current',
      days,
      label: days === 0 ? 'through today' : `${days}d behind`,
      detail: `The newest bar is ${coverageEnd.slice(0, 10)}.`,
    }
  }
  if (days <= AGEING_DAYS) {
    return {
      currency: 'ageing',
      days,
      label: `${days}d behind`,
      detail:
        `The newest bar is ${coverageEnd.slice(0, 10)}. Recent regimes are not in this ` +
        'archive, so anything read off the right-hand edge is older than it looks.',
    }
  }
  return {
    currency: 'old',
    days,
    label: `${Math.round(days / 30)} months behind`,
    detail:
      `The newest bar is ${coverageEnd.slice(0, 10)}. A strategy shaped by eye on this ` +
      'archive is shaped on a market that has had months to change.',
  }
}

/** The tone a surface should render the reading in. */
export const AGE_TONE: Record<Currency, 'good' | 'plain' | 'unknown' | 'bad'> = {
  current: 'good',
  ageing: 'plain',
  old: 'bad',
  unknown: 'unknown',
}
