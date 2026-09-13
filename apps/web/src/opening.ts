/* What a link asked a new conversation to start knowing.
 *
 * The conversation panel has accepted an opening subject since it was written
 * and, until the "Ask about this" control existed, no caller ever passed one --
 * so every thread started either blank or from whatever the workspace happened
 * to be pointed at, never from the thing somebody was actually looking at.
 *
 * Its own module rather than a closure inside the route table, because the rule
 * it encodes is worth testing: one subject, chosen deterministically, or none.
 */

import type { ChatOpening } from './components/ChatPanel'
import type { ContextKind } from './chat'

/** Which parameter names can name a subject, most specific first.
 *
 * Order is the tie-break and is deliberate: a link carrying both a strategy and
 * a dataset is about the strategy. A thread opened "about" several things at
 * once is a thread about nothing in particular, so exactly one wins.
 */
export const SUBJECTS: readonly [ContextKind, string][] = [
  ['strategy', 'strategy'],
  ['account', 'account'],
  ['dataset', 'dataset'],
  ['campaign', 'campaign'],
]

export function chatOpening(params: Record<string, string>): ChatOpening | undefined {
  for (const [kind, key] of SUBJECTS) {
    const ref = params[key]
    if (ref) return { kind, ref, label: params.label || ref }
  }
  return undefined
}
