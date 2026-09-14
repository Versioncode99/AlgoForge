'use strict'

/**
 * Which workspace is open in which window, held in the main process.
 *
 * The rule Electron makes easy to break is that a renderer is not allowed to be
 * the authority on global state. Each window knows what it is showing; only the
 * main process can know what *every* window is showing, and without that there
 * is no answer to the two questions that decide whether multi-window works:
 *
 *   - is this workspace already open somewhere?
 *   - did the window that had it actually go away?
 *
 * Getting the first wrong opens a second window onto one workspace, and two
 * windows editing one layout is a lost-update bug that presents as "my panels
 * moved back". Getting the second wrong leaves an entry pointing at a destroyed
 * window, so the workspace can never be opened again — it is "already open", in
 * a window that does not exist.
 *
 * This module is deliberately free of any Electron import. It is a state
 * machine over ids, which is where both of those bugs live, and which is
 * therefore the part worth testing rather than mocking.
 */

/** How an open request should be satisfied. */
const PLACEMENT = {
  /** Load it into the window that asked. */
  CURRENT: 'current',
  /** Create a window for it. */
  NEW: 'new',
  /** It is already open: focus the window that has it. */
  FOCUS: 'focus',
}

/**
 * What a window is doing, for the purpose of deciding whether its renderer
 * should keep doing periodic work.
 *
 * The distinction that matters is **not** focus. A trading workstation is
 * several windows open at once and watched at once -- a risk panel beside a
 * chart is the arrangement Doc 2 asks for, and pausing the unfocused half of it
 * would make the product worse to save requests nobody was short of. So
 * `BACKGROUND` is a first-class state that keeps working.
 *
 * Only surfaces the operator genuinely cannot see are told to stand down.
 */
const VISIBILITY = {
  /** On screen and frontmost. */
  ACTIVE: 'active',
  /** On screen, not frontmost. Still watched, so still working. */
  BACKGROUND: 'background',
  /** Minimised, hidden, or fully covered. Nobody can read it. */
  OBSCURED: 'obscured',
}

/** What the operator asked for. `auto` lets the registry decide. */
const INTENT = {
  AUTO: 'auto',
  CURRENT: 'current',
  NEW: 'new',
}

class WindowRegistry {
  constructor() {
    /** @type {Map<number, {workspaceId: string|null, groupId: string|null, closing: boolean, bounds: object|null, visibility: string}>} */
    this._windows = new Map()
    this._nextGroup = 1
  }

  /** Every live window, in insertion order. Closing windows are included. */
  get windowIds() {
    return [...this._windows.keys()]
  }

  /** How many windows are open and not on their way out. */
  get openCount() {
    let count = 0
    for (const entry of this._windows.values()) if (!entry.closing) count += 1
    return count
  }

  /**
   * Record a window. Idempotent by id.
   *
   * Electron reuses window ids after a window is destroyed, and a registry that
   * appended on every `register` would accumulate two entries for one window —
   * the second of which would keep a workspace marked open forever.
   */
  register(windowId, workspaceId = null) {
    const existing = this._windows.get(windowId)
    if (existing) {
      existing.closing = false
      if (workspaceId !== null) existing.workspaceId = workspaceId
      return existing
    }
    const entry = {
      workspaceId,
      groupId: null,
      closing: false,
      bounds: null,
      // A window is assumed watched until the shell says otherwise. Assuming
      // OBSCURED would stall a renderer whose first visibility event has not
      // arrived yet, which is a blank panel on startup.
      visibility: VISIBILITY.ACTIVE,
    }
    this._windows.set(windowId, entry)
    return entry
  }

  /** Which window is showing a workspace, ignoring windows on their way out. */
  windowFor(workspaceId) {
    if (!workspaceId) return null
    for (const [windowId, entry] of this._windows) {
      if (entry.workspaceId === workspaceId && !entry.closing) return windowId
    }
    return null
  }

  /** What a window is showing, or null. */
  workspaceIn(windowId) {
    const entry = this._windows.get(windowId)
    return entry && !entry.closing ? entry.workspaceId : null
  }

  /**
   * Decide where an open request should land.
   *
   * An explicit "new window" is honoured even when the workspace is already
   * open somewhere — the operator asked. Everything else prefers focusing the
   * existing window, because a second window onto one layout is two editors of
   * one document with no merge.
   */
  placeOpen(workspaceId, { from = null, intent = INTENT.AUTO } = {}) {
    if (!workspaceId) throw new Error('an open request needs a workspace id')
    const already = this.windowFor(workspaceId)

    if (intent === INTENT.NEW) {
      return { placement: PLACEMENT.NEW, windowId: null, workspaceId }
    }
    if (already !== null && already !== from) {
      return { placement: PLACEMENT.FOCUS, windowId: already, workspaceId }
    }
    if (already !== null && already === from) {
      // Already here. Not a no-op to the caller: it still wants the window
      // raised, and reloading it would discard unsaved panel positions.
      return { placement: PLACEMENT.FOCUS, windowId: from, workspaceId }
    }
    if (from !== null && this._windows.has(from) && !this._windows.get(from).closing) {
      return { placement: PLACEMENT.CURRENT, windowId: from, workspaceId }
    }
    return { placement: PLACEMENT.NEW, windowId: null, workspaceId }
  }

  /** Put a workspace in a window, releasing whatever it held. */
  load(windowId, workspaceId) {
    const entry = this._windows.get(windowId)
    if (!entry) throw new Error(`window ${windowId} is not registered`)
    entry.workspaceId = workspaceId
    return entry
  }

  /**
   * Mark a window as on its way out.
   *
   * Separate from `closed` because Electron's `close` and `closed` are separate
   * events with real time between them, and during that gap the window is still
   * enumerable. Treating it as open lets an open request focus a window that is
   * disappearing, which shows the operator a flash of a workspace and then
   * nothing.
   */
  closing(windowId) {
    const entry = this._windows.get(windowId)
    if (entry) entry.closing = true
    return Boolean(entry)
  }

  /** Forget a window. The workspace it held becomes openable again. */
  closed(windowId) {
    return this._windows.delete(windowId)
  }

  /** Remember where a window sits, for the next session. */
  remember(windowId, bounds) {
    const entry = this._windows.get(windowId)
    if (!entry) return false
    entry.bounds = bounds ? { ...bounds } : null
    return true
  }

  // ── groups ────────────────────────────────────────────────────────────────

  /**
   * Compose windows into one group.
   *
   * A group is an identity, not a geometry: the main process needs to know
   * which windows belong together so closing or restoring treats them as a
   * unit. How they are arranged on screen is the renderer's business.
   */
  group(windowIds) {
    const live = windowIds.filter((id) => this._windows.has(id))
    if (live.length < 2) throw new Error('a group needs at least two windows')
    const groupId = `wsg_${this._nextGroup}`
    this._nextGroup += 1
    for (const id of live) this._windows.get(id).groupId = groupId
    return groupId
  }

  /** Remove one window from its group, dissolving a group of one. */
  ungroup(windowId) {
    const entry = this._windows.get(windowId)
    if (!entry || !entry.groupId) return null
    const groupId = entry.groupId
    entry.groupId = null
    const remaining = this.windowsInGroup(groupId)
    if (remaining.length === 1) this._windows.get(remaining[0]).groupId = null
    return groupId
  }

  windowsInGroup(groupId) {
    if (!groupId) return []
    return this.windowIds.filter((id) => this._windows.get(id).groupId === groupId)
  }

  groupOf(windowId) {
    const entry = this._windows.get(windowId)
    return entry ? entry.groupId : null
  }

  // ── the session ───────────────────────────────────────────────────────────

  /**
   * What to reopen next time.
   *
   * Closing windows are excluded: a session saved during shutdown would
   * otherwise restore the windows the operator was in the middle of closing.
   * Windows showing nothing are excluded too — an empty window is not an
   * arrangement worth reconstructing.
   */
  /**
   * Record what a window is doing. Returns true when the state actually moved,
   * so the caller can avoid sending a renderer a message telling it nothing.
   *
   * An unknown or closing window is ignored rather than registered: visibility
   * events arrive from Electron listeners that can outlive the window they were
   * attached to, and resurrecting a closed window here would leave a workspace
   * marked open forever.
   */
  setVisibility(windowId, visibility) {
    if (!Object.values(VISIBILITY).includes(visibility)) return false
    const entry = this._windows.get(windowId)
    if (!entry || entry.closing) return false
    if (entry.visibility === visibility) return false
    entry.visibility = visibility
    return true
  }

  /** What one window is doing, or null if it is not a live window. */
  visibilityOf(windowId) {
    const entry = this._windows.get(windowId)
    return entry && !entry.closing ? entry.visibility : null
  }

  /**
   * Whether a renderer should keep doing periodic work.
   *
   * Named for the question the caller is asking rather than for the state,
   * because the caller should not have to know that BACKGROUND counts as yes.
   */
  shouldWork(windowId) {
    return this.visibilityOf(windowId) !== VISIBILITY.OBSCURED
  }

  /**
   * Every window an operator can currently read.
   *
   * The count this returns is the honest denominator for "how many copies of
   * this poll are in flight" -- windows nobody can see are not part of the
   * answer.
   */
  watched() {
    const ids = []
    for (const [windowId, entry] of this._windows) {
      if (!entry.closing && entry.visibility !== VISIBILITY.OBSCURED) ids.push(windowId)
    }
    return ids
  }

  snapshot() {
    const windows = []
    for (const [windowId, entry] of this._windows) {
      if (entry.closing || !entry.workspaceId) continue
      windows.push({
        workspaceId: entry.workspaceId,
        groupId: entry.groupId,
        bounds: entry.bounds ? { ...entry.bounds } : null,
      })
    }
    return { version: 1, windows }
  }

  /**
   * What a restore should create, from a snapshot.
   *
   * Returns a plan rather than creating anything: the registry has no Electron,
   * and a plan is inspectable in a test in a way a side effect is not. Group
   * ids are re-minted so a restored arrangement cannot collide with a group
   * created in the meantime.
   */
  planRestore(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.windows)) return []
    const remap = new Map()
    const plan = []
    const seen = new Set()
    for (const item of snapshot.windows) {
      if (!item || typeof item.workspaceId !== 'string' || !item.workspaceId) continue
      // One window per workspace. A snapshot naming the same workspace twice
      // would otherwise restore two editors of one layout.
      if (seen.has(item.workspaceId)) continue
      seen.add(item.workspaceId)
      let groupId = null
      if (item.groupId) {
        if (!remap.has(item.groupId)) {
          remap.set(item.groupId, `wsg_${this._nextGroup}`)
          this._nextGroup += 1
        }
        groupId = remap.get(item.groupId)
      }
      plan.push({
        workspaceId: item.workspaceId,
        groupId,
        bounds: _bounds(item.bounds),
      })
    }
    return plan
  }
}

/** Geometry, or nothing. A partial rectangle is not a position. */
function _bounds(value) {
  if (!value || typeof value !== 'object') return null
  const { x, y, width, height } = value
  const numbers = [x, y, width, height]
  if (!numbers.every((n) => typeof n === 'number' && Number.isFinite(n))) return null
  if (width <= 0 || height <= 0) return null
  return { x, y, width, height }
}

module.exports = { VISIBILITY, WindowRegistry, PLACEMENT, INTENT }
