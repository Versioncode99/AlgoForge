/* The desktop shell, as the interface sees it — or its absence.
 *
 * AlgoForge runs in two places: inside the Electron shell, where a workspace
 * can become its own OS window, and in a browser, where it cannot. The
 * difference has to be *detected* rather than assumed, because the failure of
 * assuming is an "Open in new window" button that does nothing and no
 * explanation of why.
 *
 * The bridge is exposed by `apps/desktop/preload.js` and is deliberately narrow:
 * named window-management functions, no generic invoke, nothing that reads a
 * file or a credential. Everything else the interface needs it gets over HTTP,
 * through the same action registry and the same permission policy as every
 * other surface.
 */

export type WindowIntent = 'auto' | 'current' | 'new'

export type ShellWindow = {
  windowId: number
  workspaceId: string | null
  groupId: string | null
}

type ShellBridge = {
  desktop: true
  workspaces: {
    open: (workspaceId: string, intent?: WindowIntent) => Promise<unknown>
    list: () => Promise<{ windows: ShellWindow[] }>
    close: (windowId: number) => Promise<unknown>
    group: (windowIds: number[]) => Promise<unknown>
    ungroup: (windowId: number) => Promise<unknown>
    restoreSession: () => Promise<unknown>
  }
}

declare global {
  interface Window {
    algoforge?: ShellBridge
  }
}

/** The bridge, or null in a browser. Read per call rather than cached: the
 *  preload runs before the page and cannot appear later, but caching a global
 *  at module load makes the module untestable without reloading it. */
export function shell(): ShellBridge | null {
  if (typeof window === 'undefined') return null
  const bridge = window.algoforge
  return bridge && bridge.desktop === true ? bridge : null
}

/** Whether a workspace can be given its own OS window here. */
export function canOpenWindows(): boolean {
  return shell() !== null
}

/**
 * Open a workspace, in a window if the shell can give it one.
 *
 * Returns false when there is no shell, so a caller can fall back to opening it
 * in place rather than silently doing nothing.
 */
export async function openWorkspaceWindow(
  workspaceId: string,
  intent: WindowIntent = 'new',
): Promise<boolean> {
  const bridge = shell()
  if (!bridge) return false
  try {
    await bridge.workspaces.open(workspaceId, intent)
    return true
  } catch {
    // The main process refused — an unknown workspace, a malformed payload.
    // Reported as "not opened" so the caller falls back rather than believing
    // a window exists.
    return false
  }
}

/** Every window the shell has, or an empty list in a browser. */
export async function shellWindows(): Promise<ShellWindow[]> {
  const bridge = shell()
  if (!bridge) return []
  try {
    return (await bridge.workspaces.list()).windows
  } catch {
    return []
  }
}
