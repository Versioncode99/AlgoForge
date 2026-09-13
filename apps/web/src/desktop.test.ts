import { afterEach, describe, expect, it, vi } from 'vitest'
import { canOpenWindows, openWorkspaceWindow, shell, shellWindows } from './desktop'

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
      list: vi.fn(async () => ({ windows: [{ windowId: 1, workspaceId: 'ws_a', groupId: null }] })),
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
    await expect(shellWindows()).resolves.toEqual([])
  })

  it('returns what the shell has', async () => {
    install()
    await expect(shellWindows()).resolves.toEqual([
      { windowId: 1, workspaceId: 'ws_a', groupId: null },
    ])
  })

  it('is empty when the shell fails rather than propagating', async () => {
    install({
      list: vi.fn(async () => {
        throw new Error('gone')
      }),
    })
    await expect(shellWindows()).resolves.toEqual([])
  })
})
