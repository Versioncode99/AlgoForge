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

/** What the shell says this window is doing. */
export type Visibility = 'active' | 'background' | 'obscured'

type ShellBridge = {
  desktop: true
  onVisibilityChange?: (callback: (visibility: Visibility) => void) => () => void
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

/**
 * Stop periodic work in a window nobody can see, and only then.
 *
 * The query layer already gates interval refetching on
 * `document.visibilityState`, which covers a minimised window for free. What it
 * cannot see is the difference between a window that is covered and one that is
 * simply not frontmost -- and those must be treated differently. Several
 * windows watched at once is the arrangement this product is for, so pausing
 * the unfocused half of a chart-beside-risk layout would make the product worse
 * to save requests nobody was short of.
 *
 * So only `obscured` stands a renderer down, via the query layer's own focus
 * gate rather than a second mechanism competing with it.
 *
 * Nothing here touches monitoring: risk, accounts and execution are watched by
 * the backend, which does not know or care whether a renderer is on screen.
 * Safety must never depend on what a window is doing.
 *
 * Returns an unsubscribe, or null outside the shell.
 */
export function followVisibility(
  setFocused: (focused: boolean) => void,
): (() => void) | null {
  const bridge = shell()
  if (!bridge?.onVisibilityChange) return null
  return bridge.onVisibilityChange((visibility) => setFocused(visibility !== 'obscured'))
}
