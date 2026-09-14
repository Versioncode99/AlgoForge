import { describe, expect, it } from 'vitest'
import { INTENT, PLACEMENT, VISIBILITY, WindowRegistry } from './workspace-windows.js'

/* The main process's answer to "where is this workspace open".
 *
 * Two bugs live in this state machine and both present as something other than
 * what they are:
 *
 *   a workspace opened into a second window   -> "my panels keep moving back",
 *                                                because two windows are editing
 *                                                one layout with no merge;
 *   an entry left pointing at a dead window   -> "I can't open it any more",
 *                                                because it is already open, in
 *                                                a window that does not exist.
 *
 * Neither throws. Both are arithmetic over ids, which is why this is a tested
 * module rather than logic inline in a window handler.
 */

describe('placement', () => {
  it('focuses the window that already has the workspace', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    registry.register(2, 'ws_prop')
    expect(registry.placeOpen('ws_research', { from: 2 })).toEqual({
      placement: PLACEMENT.FOCUS,
      windowId: 1,
      workspaceId: 'ws_research',
    })
  })

  it('loads into the asking window when the workspace is open nowhere', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_prop')
    expect(registry.placeOpen('ws_research', { from: 1 }).placement).toBe(PLACEMENT.CURRENT)
  })

  it('opens a window when there is nowhere to put it', () => {
    const registry = new WindowRegistry()
    expect(registry.placeOpen('ws_research', { from: null }).placement).toBe(PLACEMENT.NEW)
  })

  it('honours an explicit request for a new window even when it is already open', () => {
    /* The operator asked. Overriding that to be clever is how a product stops
     * being one the user controls. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    expect(registry.placeOpen('ws_research', { intent: INTENT.NEW }).placement).toBe(PLACEMENT.NEW)
  })

  it('raises rather than reloads when the workspace is already in this window', () => {
    /* Reloading would discard panel positions the operator has moved and not
     * yet saved. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    expect(registry.placeOpen('ws_research', { from: 1 })).toEqual({
      placement: PLACEMENT.FOCUS,
      windowId: 1,
      workspaceId: 'ws_research',
    })
  })

  it('does not focus a window that is on its way out', () => {
    /* `close` and `closed` are separate events with real time between them.
     * Focusing in that gap shows a flash of a workspace and then nothing. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    registry.closing(1)
    expect(registry.placeOpen('ws_research', { from: null }).placement).toBe(PLACEMENT.NEW)
  })

  it('refuses an open request with no workspace', () => {
    expect(() => new WindowRegistry().placeOpen('')).toThrow(/workspace id/)
  })
})

describe('lifecycle', () => {
  it('registering the same window twice keeps one entry', () => {
    /* Electron reuses window ids. A registry that appended would hold two
     * entries for one window, the second of which marks a workspace open
     * forever. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.register(1, 'ws_b')
    expect(registry.windowIds).toEqual([1])
    expect(registry.workspaceIn(1)).toBe('ws_b')
  })

  it('releases the workspace when the window actually goes', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    registry.closing(1)
    registry.closed(1)
    expect(registry.windowFor('ws_research')).toBeNull()
    expect(registry.openCount).toBe(0)
    // And it can be opened again, which is the whole point.
    expect(registry.placeOpen('ws_research').placement).toBe(PLACEMENT.NEW)
  })

  it('survives a close event for a window it never saw', () => {
    const registry = new WindowRegistry()
    expect(registry.closing(99)).toBe(false)
    expect(registry.closed(99)).toBe(false)
  })

  it('counts only windows that are staying', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.register(2, 'ws_b')
    registry.closing(2)
    expect(registry.openCount).toBe(1)
  })

  it('re-registering a closing window brings it back', () => {
    // A close that the operator cancelled.
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.closing(1)
    registry.register(1)
    expect(registry.openCount).toBe(1)
    expect(registry.workspaceIn(1)).toBe('ws_a')
  })

  it('repeated open and close leaves nothing behind', () => {
    /* The soak test in miniature: orphaned entries are how a long session ends
     * up unable to open anything. */
    const registry = new WindowRegistry()
    for (let round = 0; round < 50; round += 1) {
      registry.register(round, `ws_${round % 3}`)
      registry.closing(round)
      registry.closed(round)
    }
    expect(registry.windowIds).toEqual([])
    expect(registry.openCount).toBe(0)
    expect(registry.windowFor('ws_0')).toBeNull()
  })
})

describe('groups', () => {
  it('composes windows into a group', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.register(2, 'ws_b')
    const groupId = registry.group([1, 2])
    expect(registry.windowsInGroup(groupId)).toEqual([1, 2])
    expect(registry.groupOf(1)).toBe(groupId)
  })

  it('refuses a group of one', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    expect(() => registry.group([1])).toThrow(/at least two/)
    expect(() => registry.group([1, 99])).toThrow(/at least two/)
  })

  it('dissolves a group that drops to one window', () => {
    /* A "group" of one is a window with a label nobody can see, and it would
     * survive into the next session as an arrangement that is not one. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.register(2, 'ws_b')
    const groupId = registry.group([1, 2])
    registry.ungroup(1)
    expect(registry.groupOf(2)).toBeNull()
    expect(registry.windowsInGroup(groupId)).toEqual([])
  })

  it('keeps a group of three when one leaves', () => {
    const registry = new WindowRegistry()
    for (const id of [1, 2, 3]) registry.register(id, `ws_${id}`)
    const groupId = registry.group([1, 2, 3])
    registry.ungroup(1)
    expect(registry.windowsInGroup(groupId)).toEqual([2, 3])
  })
})

describe('the session', () => {
  it('remembers where each window was', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_research')
    registry.remember(1, { x: 10, y: 20, width: 1200, height: 800 })
    expect(registry.snapshot().windows[0].bounds).toEqual({
      x: 10,
      y: 20,
      width: 1200,
      height: 800,
    })
  })

  it('does not save the windows being closed', () => {
    /* A session written during shutdown would otherwise restore exactly the
     * windows the operator was in the middle of closing. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_a')
    registry.register(2, 'ws_b')
    registry.closing(2)
    expect(registry.snapshot().windows.map((w) => w.workspaceId)).toEqual(['ws_a'])
  })

  it('does not save an empty window', () => {
    const registry = new WindowRegistry()
    registry.register(1, null)
    expect(registry.snapshot().windows).toEqual([])
  })

  it('plans one window per workspace, however the snapshot names them', () => {
    const registry = new WindowRegistry()
    const plan = registry.planRestore({
      version: 1,
      windows: [
        { workspaceId: 'ws_a', groupId: 'wsg_1', bounds: null },
        { workspaceId: 'ws_a', groupId: 'wsg_1', bounds: null },
        { workspaceId: 'ws_b', groupId: 'wsg_1', bounds: null },
      ],
    })
    expect(plan.map((item) => item.workspaceId)).toEqual(['ws_a', 'ws_b'])
    // Windows that were grouped stay grouped.
    expect(plan[0].groupId).toBe(plan[1].groupId)
  })

  it('does not reuse a group id the registry has already handed out', () => {
    /* Ids in a snapshot are the *previous* session's. Restoring into a registry
     * that has since minted its own would otherwise merge two arrangements that
     * have nothing to do with each other, because they happened to be the first
     * group created in each.
     */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_live_a')
    registry.register(2, 'ws_live_b')
    const existing = registry.group([1, 2])

    const plan = registry.planRestore({
      version: 1,
      windows: [
        { workspaceId: 'ws_old_a', groupId: 'wsg_1', bounds: null },
        { workspaceId: 'ws_old_b', groupId: 'wsg_1', bounds: null },
      ],
    })
    expect(plan[0].groupId).toBe(plan[1].groupId)
    expect(plan[0].groupId).not.toBe(existing)
  })

  it('drops a partial rectangle rather than positioning by it', () => {
    const registry = new WindowRegistry()
    const plan = registry.planRestore({
      version: 1,
      windows: [
        { workspaceId: 'ws_a', bounds: { x: 0, y: 0, width: 0, height: 800 } },
        { workspaceId: 'ws_b', bounds: { x: 0, y: 0 } },
        { workspaceId: 'ws_c', bounds: { x: 5, y: 5, width: 900, height: 600 } },
      ],
    })
    expect(plan[0].bounds).toBeNull()
    expect(plan[1].bounds).toBeNull()
    expect(plan[2].bounds).toEqual({ x: 5, y: 5, width: 900, height: 600 })
  })

  it('restores nothing from a snapshot it cannot read', () => {
    const registry = new WindowRegistry()
    expect(registry.planRestore(null)).toEqual([])
    expect(registry.planRestore({})).toEqual([])
    expect(registry.planRestore({ windows: [{ workspaceId: '' }, {}, null] })).toEqual([])
  })

  it('round-trips a whole arrangement', () => {
    const first = new WindowRegistry()
    first.register(1, 'ws_research')
    first.register(2, 'ws_prop')
    first.group([1, 2])
    first.remember(1, { x: 0, y: 0, width: 1000, height: 700 })
    first.remember(2, { x: 1000, y: 0, width: 900, height: 700 })

    const second = new WindowRegistry()
    const plan = second.planRestore(first.snapshot())
    expect(plan).toHaveLength(2)
    expect(plan[0].groupId).toBe(plan[1].groupId)
    expect(plan[1].bounds.x).toBe(1000)
  })
})

describe('visibility', () => {
  /* The point of this state is deciding whether a renderer keeps polling, and
   * the expensive mistake is pausing a window the operator is still reading.
   * A trading workstation is several windows watched at once -- that is the
   * arrangement the product is for -- so "not frontmost" must not mean "stop". */

  it('a new window is assumed watched', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    expect(registry.visibilityOf(1)).toBe(VISIBILITY.ACTIVE)
    expect(registry.shouldWork(1)).toBe(true)
  })

  it('a window that is merely not frontmost keeps working', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.setVisibility(1, VISIBILITY.BACKGROUND)
    expect(registry.shouldWork(1)).toBe(true)
  })

  it('a window nobody can see stands down', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.setVisibility(1, VISIBILITY.OBSCURED)
    expect(registry.shouldWork(1)).toBe(false)
  })

  it('reports whether the state actually moved', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    expect(registry.setVisibility(1, VISIBILITY.OBSCURED)).toBe(true)
    expect(registry.setVisibility(1, VISIBILITY.OBSCURED)).toBe(false)
  })

  it('refuses a state it does not define', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    expect(registry.setVisibility(1, 'somewhere-else')).toBe(false)
    expect(registry.visibilityOf(1)).toBe(VISIBILITY.ACTIVE)
  })

  it('ignores an event for a window that is gone', () => {
    /* Electron listeners can outlive the window they were attached to.
     * Registering one here would mark a workspace open forever. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.closing(1)
    expect(registry.setVisibility(1, VISIBILITY.OBSCURED)).toBe(false)
    expect(registry.visibilityOf(1)).toBe(null)
  })

  it('ignores an event for a window that never existed', () => {
    expect(new WindowRegistry().setVisibility(99, VISIBILITY.ACTIVE)).toBe(false)
  })

  it('counts only the windows somebody can read', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.register(2, 'ws_research')
    registry.register(3, 'ws_risk')
    registry.setVisibility(2, VISIBILITY.BACKGROUND)
    registry.setVisibility(3, VISIBILITY.OBSCURED)
    expect(registry.watched()).toEqual([1, 2])
  })

  it('a closing window is not watched', () => {
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.closing(1)
    expect(registry.watched()).toEqual([])
  })

  it('visibility does not disturb the arrangement that gets restored', () => {
    /* What a window was doing when the app closed is not part of the layout. */
    const registry = new WindowRegistry()
    registry.register(1, 'ws_nq')
    registry.remember(1, { x: 0, y: 0, width: 800, height: 600 })
    registry.setVisibility(1, VISIBILITY.OBSCURED)
    const snapshot = registry.snapshot()
    expect(snapshot.windows).toHaveLength(1)
    expect(snapshot.windows[0]).not.toHaveProperty('visibility')
  })
})
