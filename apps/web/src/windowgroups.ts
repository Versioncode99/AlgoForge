/* Reading a set of shell windows as an arrangement.
 *
 * A group in the main process is an identity, not a geometry: it records which
 * windows belong together so closing and restoring treat them as a unit. That
 * makes the interface's job entirely about *reading* — which windows are
 * grouped with which, what a selection would do if the operator grouped it, and
 * why a control is unavailable. All of that is arithmetic over a list, so it
 * lives here where it can be tested without a shell.
 */

import type { ShellWindow } from './desktop'

export type Grouping = {
  groupId: string
  windows: ShellWindow[]
}

/** The grouped windows, in a stable order, with singletons excluded.
 *
 * A group of one is not a group: the registry dissolves one when a member
 * leaves, and showing it as a group here would let the operator "ungroup"
 * something that is not grouped.
 */
export function groupings(windows: readonly ShellWindow[]): Grouping[] {
  const byGroup = new Map<string, ShellWindow[]>()
  for (const window of windows) {
    if (!window.groupId) continue
    const existing = byGroup.get(window.groupId)
    if (existing) existing.push(window)
    else byGroup.set(window.groupId, [window])
  }
  return [...byGroup.entries()]
    .filter(([, members]) => members.length > 1)
    .map(([groupId, members]) => ({ groupId, windows: members }))
    .sort((a, b) => a.groupId.localeCompare(b.groupId))
}

/** Windows belonging to no group of two or more. */
export function loose(windows: readonly ShellWindow[]): ShellWindow[] {
  const grouped = new Set(groupings(windows).flatMap((g) => g.windows.map((w) => w.windowId)))
  return windows.filter((window) => !grouped.has(window.windowId))
}

export type SelectionState = {
  /** Whether grouping the selection is something the shell would accept. */
  canGroup: boolean
  /** Why not, when it would not. Empty when it would. */
  reason: string
  /** Ids in the order the shell should receive them. */
  ids: number[]
}

/** What a selection can do, and what to say when it cannot.
 *
 * The main process refuses a group of fewer than two, and a refusal an operator
 * cannot see coming is a button that does nothing. So the reason is computed
 * here, next to the rule, rather than being a sentence someone remembered to
 * write beside the control.
 */
export function readSelection(
  windows: readonly ShellWindow[],
  selected: ReadonlySet<number>,
): SelectionState {
  const live = windows.filter((window) => selected.has(window.windowId))
  const ids = live.map((window) => window.windowId).sort((a, b) => a - b)
  if (ids.length === 0) {
    return { canGroup: false, reason: 'Select two or more windows to group them.', ids }
  }
  if (ids.length === 1) {
    return { canGroup: false, reason: 'A group needs at least two windows.', ids }
  }
  const already = live[0].groupId
  if (already && live.every((window) => window.groupId === already)) {
    return {
      canGroup: false,
      reason: 'These windows are already one group.',
      ids,
    }
  }
  return { canGroup: true, reason: '', ids }
}

/** How a group's membership reads in one line. */
export function describeGroup(group: Grouping, names: Map<string, string>): string {
  const labelled = group.windows.map(
    (window) => names.get(window.workspaceId ?? '') ?? window.workspaceId ?? 'an empty window',
  )
  return labelled.join(' · ')
}
