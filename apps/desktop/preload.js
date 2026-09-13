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

const call = (channel, payload) => ipcRenderer.invoke(channel, payload ?? {})

contextBridge.exposeInMainWorld('algoforge', {
  /** True only inside the shell, so the web build can tell and degrade. */
  desktop: true,

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
