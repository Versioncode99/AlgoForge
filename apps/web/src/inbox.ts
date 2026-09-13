/* Finished work, and how to get back to what it was about.
 *
 * The arrival rule lives in the backend and is deliberately mechanical: a job
 * reaching a terminal state arrives, and nothing else does. This module is the
 * reading half -- what an item says, and where it opens -- kept pure so both
 * are testable without a server.
 */

import { format } from './route'

export type InboxOutcome = 'done' | 'failed' | 'cancelled'

export type InboxItem = {
  item_id: string
  job_id: string
  /** The job kind: `backtest`, `prop_matrix`, `sweep_surface`, `mission`, … */
  kind: string
  label: string
  outcome: InboxOutcome
  error: string
  refs: Record<string, string>
  seconds: number
  arrived_at: string
  read_at: string
  dismissed_at: string
}

export type InboxPayload = { items: InboxItem[]; unread: number }

/** Where an item opens, or empty when it names nothing a surface can show.
 *
 * Empty is a real answer and is rendered as such. A button that goes somewhere
 * unrelated is worse than no button: it moves the operator away from what they
 * were doing and looks deliberate doing it.
 */
export function destination(item: InboxItem): string {
  const strategy = item.refs.strategy_id ?? ''
  const account = item.refs.account_id ?? ''
  switch (item.kind) {
    case 'backtest':
      return strategy ? format('strategies', { strategy, pane: 'summary' }) : ''
    case 'sweep_surface':
    case 'surface':
      return strategy ? format('strategies', { strategy }) : ''
    case 'prop_matrix':
      return '#prop'
    case 'mission':
      return '#actions'
    case 'agent':
      return '#agents'
    default:
      return strategy ? format('strategies', { strategy }) : account ? format('desk', { account }) : ''
  }
}

/** How long it took, in the unit a person would say it in. */
export function took(seconds: number): string {
  if (seconds < 1) return 'under a second'
  if (seconds < 90) return `${Math.round(seconds)}s`
  const minutes = seconds / 60
  if (minutes < 90) return `${Math.round(minutes)} min`
  return `${(minutes / 60).toFixed(1)} h`
}

/** One line saying what happened. Never a summary of the result.
 *
 * The result lives in its artifact. Restating it here would create a second
 * record able to disagree with the first, which is the thing this codebase
 * spends most of its care avoiding -- so an item says that work finished and
 * how to go and look, and nothing about what it found.
 */
export function describeItem(item: InboxItem): string {
  if (item.outcome === 'failed') {
    return item.error ? `Failed after ${took(item.seconds)} — ${item.error}` : 'Failed.'
  }
  if (item.outcome === 'cancelled') return `Cancelled after ${took(item.seconds)}.`
  return `Finished in ${took(item.seconds)}.`
}

export const OUTCOME_TONE: Record<InboxOutcome, 'good' | 'bad' | 'plain'> = {
  done: 'good',
  failed: 'bad',
  cancelled: 'plain',
}
