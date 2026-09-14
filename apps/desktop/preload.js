'use strict'

/**
 * The bridge, and the whole of what a renderer can reach in the main process.
 *
 * `contextIsolation` is on and `nodeIntegration` is off, so this file is the
 * only path from the page to the shell. It exposes named functions rather than
 * a generic `invoke(channel, payload)`: a generic one would make the channel
 * itself a value the page chooses, and the allowlist in `ipc-contract` would be
 * enforcing a set the renderer is free to explore.
 *
 * Nothing here reads a file, a database or a credential, and nothing here
 * returns one. Everything else the interface needs it gets over HTTP from the
 * local API, through the same action registry and the same permission policy as
 * every other surface — which is what stops the desktop shell being a second
 * way in with none of that.
 */

const { contextBridge, ipcRenderer } = require('electron')

/**
 * The visibility channel's name, written out rather than imported.
 *
 * This preload runs with `sandbox: true`, where `require` resolves only
 * `electron` and a few polyfilled builtins. Requiring a local module throws,
 * and because that happens before `exposeInMainWorld`, the failure is not a
 * missing constant -- it is the whole bridge silently absent, so every window
 * verb stops working and the interface decides it is running in a browser.
 *
 * `ipc-contract.js` owns the name; `ipc-contract.test.js` asserts this copy
 * still matches it, so the two cannot drift apart unnoticed.
 */
const VISIBILITY_CHANNEL = 'workspace:visibility'

/* Every invoke goes through here, so this is the only honest place to count
 * them. `docs/PERFORMANCE_BASELINE.md` named IPC volume as a dimension nobody
 * had measured, and there is no counter in Electron to read: instrumenting from
 * outside would have meant wrapping the bridge from the page, which changes the
 * thing being measured. A monotonic integer changes nothing and is readable. */
let invokes = 0

const call = (channel, payload) => {
  invokes += 1
  return ipcRenderer.invoke(channel, payload ?? {})
}

contextBridge.exposeInMainWorld('algoforge', {
  /** True only inside the shell, so the web build can tell and degrade. */
  desktop: true,

  /** How many IPC calls this window has made since it loaded.
   *
   * A number, and nothing else: it carries no payload, reaches nothing, and
   * cannot be reset from the page. It exists so IPC volume is a thing that can
   * be measured rather than estimated. */
  ipcCalls: () => invokes,

  /**
   * What this window is doing: 'active', 'background' or 'obscured'.
   *
   * A listener, not a getter, because the renderer needs to react rather than
   * ask -- and the shell is the only thing that knows the difference between a
   * window that is covered and one that is merely not frontmost.
   *
   * The callback receives the state name only. Returns an unsubscribe, so a
   * renderer that tears down does not leave a listener attached to a window
   * that is about to be reused: Electron reuses window ids.
   */
  onVisibilityChange: (callback) => {
    const listener = (_event, payload) => {
      const state = payload && payload.visibility
      if (typeof state === 'string') callback(state)
    }
    ipcRenderer.on(VISIBILITY_CHANNEL, listener)
    return () => ipcRenderer.removeListener(VISIBILITY_CHANNEL, listener)
  },

  workspaces: {
    /** Open a workspace. `intent` is 'auto' | 'current' | 'new'. */
    open: (workspaceId, intent = 'auto') =>
      call('workspace:open', { workspaceId, intent }),
    /** Every window the shell has, and what each is showing. */
    list: () => call('workspace:windows'),
    close: (windowId) => call('workspace:close-window', { windowId }),
    group: (windowIds) => call('workspace:group', { windowIds }),
    ungroup: (windowId) => call('workspace:ungroup', { windowId }),
    /** Reopen the arrangement from the previous session. */
    restoreSession: () => call('workspace:restore-session'),
  },
})
