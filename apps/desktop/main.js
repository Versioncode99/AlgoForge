'use strict'

/**
 * AlgoForge desktop shell.
 *
 * Owns the Python API as a child process, so launching the app starts the engine
 * and closing the window shuts it down. No browser, no stray localhost servers
 * left running, no terminal window.
 */

const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron')
const { spawn } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')

const { WindowRegistry, PLACEMENT } = require('./workspace-windows')
const { CHANNEL_NAMES, validate } = require('./ipc-contract')

const ROOT = path.resolve(__dirname, '..', '..')
const API_PORT = 8765
const API_HEALTH = `http://127.0.0.1:${API_PORT}/api/v1/health`
const WEB_DIST = path.join(ROOT, 'apps', 'web', 'dist', 'index.html')
const APP_URL = `http://127.0.0.1:${API_PORT}/`
const DEV_URL = process.env.ALGOFORGE_DEV_URL // set to run against the Vite dev server

let apiProcess = null
let mainWindow = null

/* Which workspace is open in which window, held here rather than in a renderer.
 *
 * A renderer knows what it is showing; only this process can know what every
 * window is showing, and without that there is no answer to "is this workspace
 * already open somewhere" -- so a second window opens onto one layout and two
 * windows edit it with no merge. The state machine is in
 * `workspace-windows.js`, tested there, and free of any Electron import. */
const windows = new WindowRegistry()

/* Where the arrangement is written between sessions. Debounced rather than
 * written per event: dragging a window emits `move` continuously, and a
 * synchronous write per frame is how a window manager comes to feel heavy. */
const SESSION_FILE = path.join(ROOT, 'data', 'runtime', 'workspace-session.json')
const SESSION_DEBOUNCE_MS = 400
let sessionTimer = null

function pythonPath() {
  const candidates = [
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(ROOT, '.venv', 'bin', 'python'),
  ]
  return candidates.find((candidate) => fs.existsSync(candidate)) || null
}

function probe(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume()
      resolve(response.statusCode === 200)
    })
    request.on('error', () => resolve(false))
    request.on('timeout', () => {
      request.destroy()
      resolve(false)
    })
  })
}

async function waitFor(url, deadlineMs) {
  const until = Date.now() + deadlineMs
  while (Date.now() < until) {
    if (await probe(url)) return true
    await new Promise((resolve) => setTimeout(resolve, 400))
  }
  return false
}

async function startApi() {
  // Reuse an API that is already running rather than fighting it for the port.
  if (await probe(API_HEALTH)) return true

  const python = pythonPath()
  if (!python) {
    dialog.showErrorBox(
      'AlgoForge',
      'Python environment not found.\n\nRun "uv sync --all-groups" in the AlgoForge folder, then reopen.',
    )
    return false
  }

  apiProcess = spawn(
    python,
    [
      '-m', 'uvicorn', 'forge_api.main:app',
      '--app-dir', path.join(ROOT, 'apps', 'api'),
      '--host', '127.0.0.1', '--port', String(API_PORT),
    ],
    { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true },
  )

  const logDir = path.join(ROOT, 'data', 'runtime', 'logs')
  fs.mkdirSync(logDir, { recursive: true })
  const logStream = fs.createWriteStream(path.join(logDir, 'desktop-api.log'), { flags: 'a' })
  apiProcess.stdout.pipe(logStream)
  apiProcess.stderr.pipe(logStream)
  apiProcess.on('exit', (code) => {
    apiProcess = null
    if (code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
      dialog.showErrorBox('AlgoForge', `The API stopped unexpectedly (exit ${code}).\nSee ${logDir}.`)
    }
  })

  return waitFor(API_HEALTH, 60_000)
}

function stopApi() {
  if (!apiProcess) return
  const child = apiProcess
  apiProcess = null
  try {
    child.kill()
  } catch {
    /* already gone */
  }
}

/** Persist the arrangement, at most once per debounce window. */
function rememberSession() {
  if (sessionTimer) clearTimeout(sessionTimer)
  sessionTimer = setTimeout(() => {
    sessionTimer = null
    try {
      fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true })
      fs.writeFileSync(SESSION_FILE, JSON.stringify(windows.snapshot(), null, 2))
    } catch {
      // A layout that could not be saved is not worth failing a session over,
      // and the operator loses an arrangement rather than any work.
    }
  }, SESSION_DEBOUNCE_MS)
}

/** Flush immediately. Called on the way out, where a debounce would lose it. */
function flushSession() {
  if (sessionTimer) {
    clearTimeout(sessionTimer)
    sessionTimer = null
  }
  try {
    fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true })
    fs.writeFileSync(SESSION_FILE, JSON.stringify(windows.snapshot(), null, 2))
  } catch {
    /* as above */
  }
}

function readSession() {
  try {
    return JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'))
  } catch {
    return null
  }
}

/**
 * The IPC surface, registered once.
 *
 * Registered in a loop over the contract's own channel list so a channel added
 * to the table without a handler fails loudly here rather than silently at the
 * call site, and so the reverse -- a handler for a channel nobody declared --
 * is not expressible.
 *
 * Every payload goes through `validate` before a handler sees it, and handlers
 * receive the *rebuilt* payload, so a field that was not declared cannot reach
 * one even if the table and the handler drift apart.
 */
function installIpc() {
  const handlers = {
    'workspace:open': (event, payload) => {
      const from = windowIdOf(event)
      const decision = windows.placeOpen(payload.workspaceId, {
        from,
        intent: payload.intent ?? 'auto',
      })
      if (decision.placement === PLACEMENT.FOCUS) {
        const target = BrowserWindow.fromId(decision.windowId)
        if (target && !target.isDestroyed()) {
          if (target.isMinimized()) target.restore()
          target.focus()
        }
        return decision
      }
      if (decision.placement === PLACEMENT.CURRENT) {
        windows.load(from, payload.workspaceId)
        rememberSession()
        return decision
      }
      const created = createWorkspaceWindow(payload.workspaceId)
      return { ...decision, windowId: created ? created.id : null }
    },
    'workspace:close-window': (_event, payload) => {
      const target = BrowserWindow.fromId(payload.windowId)
      if (target && !target.isDestroyed()) target.close()
      return { closed: Boolean(target) }
    },
    'workspace:remember-bounds': (_event, payload) => {
      windows.remember(payload.windowId, payload.bounds)
      rememberSession()
      return { remembered: true }
    },
    'workspace:group': (_event, payload) => {
      const groupId = windows.group(payload.windowIds)
      rememberSession()
      return { groupId, windowIds: windows.windowsInGroup(groupId) }
    },
    'workspace:ungroup': (_event, payload) => {
      const groupId = windows.ungroup(payload.windowId)
      rememberSession()
      return { groupId }
    },
    'workspace:windows': () => ({
      windows: windows.windowIds.map((id) => ({
        windowId: id,
        workspaceId: windows.workspaceIn(id),
        groupId: windows.groupOf(id),
      })),
    }),
    'workspace:restore-session': () => {
      const plan = windows.planRestore(readSession())
      const opened = []
      for (const item of plan) {
        if (windows.windowFor(item.workspaceId) !== null) continue
        const created = createWorkspaceWindow(item.workspaceId, item.bounds)
        if (created) opened.push({ windowId: created.id, workspaceId: item.workspaceId })
      }
      return { restored: opened }
    },
  }

  for (const channel of CHANNEL_NAMES) {
    const handler = handlers[channel]
    if (!handler) throw new Error(`no handler for declared channel '${channel}'`)
    ipcMain.handle(channel, (event, payload) => {
      const checked = validate(channel, payload)
      if (!checked.ok) throw new Error(checked.reason)
      return handler(event, checked.payload)
    })
  }
}

function windowIdOf(event) {
  const sender = BrowserWindow.fromWebContents(event.sender)
  return sender ? sender.id : null
}

/**
 * A window for one workspace.
 *
 * The workspace id travels in the URL fragment rather than over a second
 * channel, so a reload lands on the same workspace instead of on whatever was
 * globally active — a window that forgets what it is showing when it refreshes
 * is a window the operator cannot trust to keep their arrangement.
 */
function createWorkspaceWindow(workspaceId, bounds = null) {
  const window = new BrowserWindow({
    ...(bounds ?? { width: 1360, height: 900 }),
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#0a0b0d',
    title: 'AlgoForge',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })

  windows.register(window.id, workspaceId)
  window.webContents.setWindowOpenHandler(({ url }) => openExternally(url))
  holdOrigin(window, DEV_URL || APP_URL)
  window.once('ready-to-show', () => window.show())

  // Geometry is recorded through the debounced path: `move` and `resize` fire
  // continuously while dragging, and a write per event is hundreds per second.
  const record = () => {
    if (!window.isDestroyed()) windows.remember(window.id, window.getBounds())
    rememberSession()
  }
  window.on('move', record)
  window.on('resize', record)

  // `close` and `closed` are separate events with real time between them. A
  // window is enumerable in that gap, so it is marked as leaving before it is
  // forgotten -- otherwise an open request can focus a window that is
  // disappearing and the operator sees a flash of a workspace and then nothing.
  window.on('close', () => {
    windows.closing(window.id)
    flushSession()
  })
  window.on('closed', () => {
    // Listeners go with the window. Left attached they would fire against a
    // destroyed handle, which is the leak a soak test surfaces as growth.
    window.removeAllListeners('move')
    window.removeAllListeners('resize')
    windows.closed(window.id)
  })

  const target = DEV_URL || APP_URL
  window.loadURL(`${target}#workspace?workspace=${encodeURIComponent(workspaceId)}`)
  return window
}

/**
 * Hand a link to the operating system, or refuse it.
 *
 * `shell.openExternal` will hand anything to the OS handler for its scheme,
 * and a page is what supplies the URL. On a local, first-party page that is a
 * small surface; it is not one worth leaving open, because the cost of closing
 * it is four lines and the cost of it being wrong is an arbitrary protocol
 * handler invoked from a renderer.
 *
 * http and https only. Everything else is dropped rather than passed on --
 * silently, because a page that just tried `file://` is not a page to put a
 * dialog in front of the operator about.
 */
const EXTERNAL_SCHEMES = new Set(['http:', 'https:'])

function openExternally(url) {
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return { action: 'deny' }
  }
  if (EXTERNAL_SCHEMES.has(parsed.protocol)) shell.openExternal(parsed.toString())
  return { action: 'deny' }
}

/**
 * Keep a window on the page it was given.
 *
 * A renderer that navigates the window itself bypasses the open-handler above
 * entirely: nothing is "opened", the existing window simply becomes something
 * else, with the preload still attached. The app is served from one origin, so
 * anything leaving it is refused.
 */
function holdOrigin(window, allowed) {
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(allowed)) event.preventDefault()
  })
}

function buildMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: 'AlgoForge',
        submenu: [
          { role: 'reload' },
          { role: 'forceReload' },
          { role: 'toggleDevTools' },
          { type: 'separator' },
          {
            label: 'Open strategies folder',
            click: () => shell.openPath(path.join(ROOT, 'strategies')),
          },
          {
            label: 'Open API docs',
            click: () => shell.openExternal(`http://127.0.0.1:${API_PORT}/docs`),
          },
          { type: 'separator' },
          { role: 'quit' },
        ],
      },
      { role: 'editMenu' },
      {
        label: 'View',
        submenu: [
          { role: 'resetZoom' },
          { role: 'zoomIn' },
          { role: 'zoomOut' },
          { type: 'separator' },
          { role: 'togglefullscreen' },
        ],
      },
    ]),
  )
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1560,
    height: 960,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#0a0b0d',
    title: 'AlgoForge',
    icon: path.join(ROOT, 'apps', 'web', 'public', 'algoforge.ico'),
    autoHideMenuBar: true,
    webPreferences: {
      // The renderer is our own local page; it needs no Node access. The
      // preload is the only path from it to this process, and it exposes named
      // window-management functions rather than a generic invoke.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })
  windows.register(mainWindow.id, null)

  // External links open in the real browser, never inside the app shell — and
  // only http(s), so a page cannot reach an arbitrary OS protocol handler.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => openExternally(url))
  holdOrigin(mainWindow, DEV_URL || APP_URL)

  mainWindow.once('ready-to-show', () => mainWindow.show())
  const recordMain = () => {
    if (mainWindow && !mainWindow.isDestroyed()) windows.remember(mainWindow.id, mainWindow.getBounds())
    rememberSession()
  }
  mainWindow.on('move', recordMain)
  mainWindow.on('resize', recordMain)
  mainWindow.on('close', () => {
    windows.closing(mainWindow.id)
    flushSession()
  })
  mainWindow.on('closed', () => {
    mainWindow.removeAllListeners('move')
    mainWindow.removeAllListeners('resize')
    windows.closed(mainWindow.id)
    mainWindow = null
  })

  const started = await startApi()
  if (!started) {
    dialog.showErrorBox('AlgoForge', 'The API did not answer within 60 seconds. See data/runtime/logs.')
    app.quit()
    return
  }

  if (DEV_URL) {
    await mainWindow.loadURL(DEV_URL)
  } else if (fs.existsSync(WEB_DIST)) {
    // Served by the API, so the page is same-origin with the endpoints it calls.
    await mainWindow.loadURL(APP_URL)
  } else {
    dialog.showErrorBox(
      'AlgoForge',
      'The interface has not been built.\n\nRun "npm run build:web" in the AlgoForge folder, then reopen.',
    )
    app.quit()
  }
}

// One window, one API. A second launch focuses the existing window.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(() => {
    buildMenu()
    installIpc()
    createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('window-all-closed', () => {
    flushSession()
    stopApi()
    app.quit()
  })
  app.on('before-quit', stopApi)
  process.on('exit', stopApi)
}
