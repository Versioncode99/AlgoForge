import { describe, expect, it } from 'vitest'
import type { ShellWindow } from './desktop'
import { describeGroup, groupings, loose, readSelection } from './windowgroups'

const w = (windowId: number, workspaceId: string | null, groupId: string | null): ShellWindow => ({
  windowId,
  workspaceId,
  groupId,
})

const windows = [
  w(1, 'ws_desk', 'wsg_1'),
  w(2, 'ws_lab', 'wsg_1'),
  w(3, 'ws_charts', null),
  // A group the registry would have dissolved. Treated as loose here too, or
  // the operator is offered an "ungroup" for something that is not grouped.
  w(4, 'ws_alone', 'wsg_9'),
]

describe('reading the arrangement', () => {
  it('groups the windows that are actually grouped', () => {
    const found = groupings(windows)
    expect(found).toHaveLength(1)
    expect(found[0].groupId).toBe('wsg_1')
    expect(found[0].windows.map((x) => x.windowId)).toEqual([1, 2])
  })

  it('does not call a group of one a group', () => {
    expect(groupings(windows).some((g) => g.groupId === 'wsg_9')).toBe(false)
    expect(loose(windows).map((x) => x.windowId)).toEqual([3, 4])
  })

  it('is empty rather than throwing on no windows at all', () => {
    expect(groupings([])).toEqual([])
    expect(loose([])).toEqual([])
  })
})

describe('what a selection can do', () => {
  it('refuses nothing selected, and says what to do instead', () => {
    const state = readSelection(windows, new Set())
    expect(state.canGroup).toBe(false)
    expect(state.reason).toContain('two or more')
  })

  it('refuses one window with the rule the shell will apply', () => {
    const state = readSelection(windows, new Set([3]))
    expect(state.canGroup).toBe(false)
    expect(state.reason).toBe('A group needs at least two windows.')
  })

  it('allows two loose windows', () => {
    const state = readSelection(windows, new Set([3, 4]))
    expect(state.canGroup).toBe(true)
    expect(state.ids).toEqual([3, 4])
    expect(state.reason).toBe('')
  })

  it('refuses a selection that is already exactly one group', () => {
    const state = readSelection(windows, new Set([1, 2]))
    expect(state.canGroup).toBe(false)
    expect(state.reason).toContain('already one group')
  })

  it('allows adding a loose window to an existing group', () => {
    const state = readSelection(windows, new Set([1, 2, 3]))
    expect(state.canGroup).toBe(true)
    expect(state.ids).toEqual([1, 2, 3])
  })

  it('ignores a selected id that is no longer a window', () => {
    const state = readSelection(windows, new Set([3, 99]))
    expect(state.ids).toEqual([3])
    expect(state.canGroup).toBe(false)
  })
})

describe('describing a group', () => {
  it('uses workspace names when it has them', () => {
    const names = new Map([['ws_desk', 'NQ Prop Desk'], ['ws_lab', 'Research Lab']])
    expect(describeGroup(groupings(windows)[0], names)).toBe('NQ Prop Desk · Research Lab')
  })

  it('falls back to the id rather than showing nothing', () => {
    expect(describeGroup(groupings(windows)[0], new Map())).toBe('ws_desk · ws_lab')
  })

  it('names an empty window as empty rather than as blank', () => {
    const group = { groupId: 'g', windows: [w(7, null, 'g'), w(8, 'ws_lab', 'g')] }
    expect(describeGroup(group, new Map())).toBe('an empty window · ws_lab')
  })
})
