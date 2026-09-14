import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  type Visibility,
  canOpenWindows,
  followVisibility,
  openWorkspaceWindow,
  shell,
  groupWindows,
  shellWindows,
  ungroupWindow,
} from './desktop'

/* Running inside the shell, or not, and the difference being detected rather
 * than assumed.
 *
 * The failure of assuming is an "Open in new window" button that does nothing
 * in a browser and offers no explanation. So the bridge is probed, its absence
 * is a first-class answer, and a refusal from the main process is reported as
 * "not opened" rather than swallowed — otherwise a caller believes a window
 * exists and stops offering the fallback.
 */

afterEach(() => {
  delete (window as { algoforge?: unknown }).algoforge
})

function install(overrides: Record<string, unknown> = {}) {
  const bridge = {
    desktop: true,
    workspaces: {
      open: vi.fn(async () => ({ placement: 'new', windowId: 3 })),
      list: vi.fn(async () => ({
        self: 1,
        windows: [{ windowId: 1, workspaceId: 'ws_a', groupId: null }],
      })),
      close: vi.fn(),
      group: vi.fn(),
      ungroup: vi.fn(),
      restoreSession: vi.fn(),
      ...overrides,
    },
  }
  ;(window as { algoforge?: unknown }).algoforge = bridge
  return bridge
}

describe('detecting the shell', () => {
  it('finds no bridge in a browser', () => {
    expect(shell()).toBeNull()
    expect(canOpenWindows()).toBe(false)
  })

  it('refuses something that is merely shaped like the bridge', () => {
    /* `desktop: true` is set by the preload and by nothing else. Accepting any
     * object at `window.algoforge` would let a page script claim to be the
     * shell. */
    ;(window as { algoforge?: unknown }).algoforge = { workspaces: {} }
    expect(shell()).toBeNull()
  })

  it('finds the bridge inside the shell', () => {
    install()
    expect(canOpenWindows()).toBe(true)
  })
})

describe('opening a window', () => {
  it('asks the shell for a new window by default', async () => {
    const bridge = install()
    await expect(openWorkspaceWindow('ws_research')).resolves.toBe(true)
    expect(bridge.workspaces.open).toHaveBeenCalledWith('ws_research', 'new')
  })

  it('passes the intent through when one is given', async () => {
    const bridge = install()
    await openWorkspaceWindow('ws_research', 'current')
    expect(bridge.workspaces.open).toHaveBeenCalledWith('ws_research', 'current')
  })

  it('reports that nothing opened in a browser, so a caller can fall back', async () => {
    await expect(openWorkspaceWindow('ws_research')).resolves.toBe(false)
  })

  it('reports a refusal from the main process rather than swallowing it', async () => {
    /* The IPC contract refuses an unknown workspace or a malformed payload.
     * Reported as "not opened" so the caller opens it in place instead of
     * believing a window exists. */
    install({
      open: vi.fn(async () => {
        throw new Error("'workspace:open' got an invalid workspaceId")
      }),
    })
    await expect(openWorkspaceWindow('')).resolves.toBe(false)
  })
})

describe('listing windows', () => {
  it('is empty in a browser rather than throwing', async () => {
    await expect(shellWindows()).resolves.toEqual({ self: null, windows: [] })
  })

  it('returns what the shell has, and which window is asking', async () => {
    install()
    await expect(shellWindows()).resolves.toEqual({
      self: 1,
      windows: [{ windowId: 1, workspaceId: 'ws_a', groupId: null }],
    })
  })

  it('is empty when the shell fails rather than propagating', async () => {
    install({
      list: vi.fn(async () => {
        throw new Error('gone')
      }),
    })
    await expect(shellWindows()).resolves.toEqual({ self: null, windows: [] })
  })

  it('survives an older shell that does not say which window is asking', async () => {
    /* The renderer and the main process are versioned together, but a stale
       window left open across an update is a real state. A missing `self`
       must read as "not known" rather than crash the list it came with. */
    install({
      list: vi.fn(async () => ({ windows: [{ windowId: 4, workspaceId: 'ws_b', groupId: null }] })),
    })
    await expect(shellWindows()).resolves.toEqual({
      self: null,
      windows: [{ windowId: 4, workspaceId: 'ws_b', groupId: null }],
    })
  })
})

describe('composing windows into a group', () => {
  it('does nothing in a browser', async () => {
    await expect(groupWindows([1, 2])).resolves.toBeNull()
  })

  it('refuses fewer than two without troubling the shell', async () => {
    const bridge = install()
    await expect(groupWindows([1])).resolves.toBeNull()
    expect(bridge.workspaces.group).not.toHaveBeenCalled()
  })

  it('returns the group the shell minted', async () => {
    install({ group: vi.fn(async () => ({ groupId: 'wsg_2', windowIds: [1, 2] })) })
    await expect(groupWindows([1, 2])).resolves.toBe('wsg_2')
  })

  it('reports a refusal as no group rather than propagating it', async () => {
    install({
      group: vi.fn(async () => {
        throw new Error('a group needs at least two windows')
      }),
    })
    await expect(groupWindows([1, 2])).resolves.toBeNull()
  })
})

describe('taking a window out of its group', () => {
  it('is false in a browser', async () => {
    await expect(ungroupWindow(1)).resolves.toBe(false)
  })

  it('is true when the shell acted', async () => {
    install({ ungroup: vi.fn(async () => ({ groupId: 'wsg_1' })) })
    await expect(ungroupWindow(1)).resolves.toBe(true)
  })

  it('is false when the shell refused', async () => {
    install({
      ungroup: vi.fn(async () => {
        throw new Error('gone')
      }),
    })
    await expect(ungroupWindow(9)).resolves.toBe(false)
  })
})

describe('followVisibility', () => {
  /* The expensive mistake here is pausing a window somebody is reading. A
   * workstation is several windows watched at once, so "not frontmost" must
   * keep working; only a window nobody can see stands down. */

  /** A shell bridge with just the parts this behaviour touches. */
  function bridge(
    onVisibilityChange?: (cb: (v: Visibility) => void) => () => void,
  ): void {
    window.algoforge = {
      desktop: true,
      onVisibilityChange,
      workspaces: {
        open: async () => ({}),
        list: async () => ({ self: null, windows: [] }),
        close: async () => ({}),
        group: async () => ({}),
        ungroup: async () => ({}),
        restoreSession: async () => ({}),
      },
    }
  }

  it('does nothing in a browser', () => {
    expect(followVisibility(() => {})).toBe(null)
  })

  it('does nothing in a shell too old to report visibility', () => {
    bridge(undefined)
    expect(followVisibility(() => {})).toBe(null)
  })

  it('keeps working when the window is merely not frontmost', () => {
    const seen: boolean[] = []
    let emit: ((v: Visibility) => void) | undefined
    bridge((cb) => {
      emit = cb
      return () => {}
    })
    followVisibility((focused) => seen.push(focused))
    emit?.('background')
    expect(seen).toEqual([true])
  })

  it('stands down only when nobody can see the window', () => {
    const seen: boolean[] = []
    let emit: ((v: Visibility) => void) | undefined
    bridge((cb) => {
      emit = cb
      return () => {}
    })
    followVisibility((focused) => seen.push(focused))
    emit?.('obscured')
    emit?.('active')
    expect(seen).toEqual([false, true])
  })

  it('hands back the unsubscribe so a teardown does not leak a listener', () => {
    const off = vi.fn()
    bridge(() => off)
    followVisibility(() => {})?.()
    expect(off).toHaveBeenCalledOnce()
  })
})
