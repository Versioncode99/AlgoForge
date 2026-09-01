'use strict'

/**
 * AlgoForge desktop shell.
 *
 * Owns the Python API as a child process, so launching the app starts the engine
 * and closing the window shuts it down. No browser, no stray localhost servers
 * left running, no terminal window.
 */

const { app, BrowserWindow, Menu, dialog, shell } = require('electron')
const { spawn } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')

const ROOT = path.resolve(__dirname, '..', '..')
const API_PORT = 8765
const API_HEALTH = `http://127.0.0.1:${API_PORT}/api/v1/health`
const WEB_DIST = path.join(ROOT, 'apps', 'web', 'dist', 'index.html')
const APP_URL = `http://127.0.0.1:${API_PORT}/`
const DEV_URL = process.env.ALGOFORGE_DEV_URL // set to run against the Vite dev server

let apiProcess = null
let mainWindow = null

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
      // The renderer is our own local page; it needs no Node access.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  // External links open in the real browser, never inside the app shell.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.once('ready-to-show', () => mainWindow.show())
  mainWindow.on('closed', () => {
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
    createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('window-all-closed', () => {
    stopApi()
    app.quit()
  })
  app.on('before-quit', stopApi)
  process.on('exit', stopApi)
}
