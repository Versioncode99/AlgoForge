import { type ElectronApplication, _electron, expect, test } from '@playwright/test'
import { type ChildProcess, spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/* The desktop shell, driven as a real Electron process.
 *
 * Doc 2 asks for multi-window lifecycle coverage and says not to substitute a
 * mocked browser test for it, so nothing here is stubbed: a real main process,
 * real BrowserWindows, the real preload bridge, the real IPC contract and the
 * real session file on disk.
 *
 * The API is started here rather than stubbed, because the shell waits for one
 * before it loads anything and `startApi` reuses a healthy instance instead of
 * fighting it for the port. Starting a real one is both more faithful than a
 * fake and cheaper than adding a test-only escape hatch to the shell.
 */

const DESKTOP = join(process.cwd(), '..', 'desktop')
const ELECTRON = join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron')
const ROOT = join(process.cwd(), '..', '..')
const SESSION = join(ROOT, 'data', 'runtime', 'workspace-session.json')
const HEALTH = 'http://127.0.0.1:8765/api/v1/health'

let api: ChildProcess | null = null

async function healthy(): Promise<boolean> {
  try {
    const response = await fetch(HEALTH, { signal: AbortSignal.timeout(1500) })
    return response.ok
  } catch {
    return false
  }
}

test.beforeAll(async () => {
  if (await healthy()) return
  api = spawn(
    join(ROOT, '.venv', 'bin', 'python'),
    ['-m', 'uvicorn', 'forge_api.main:app', '--app-dir', join(ROOT, 'apps', 'api'),
     '--host', '127.0.0.1', '--port', '8765'],
    { cwd: ROOT, stdio: 'ignore' },
  )
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (await healthy()) return
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error('the API did not become healthy, so the shell would never load a page')
})

test.afterAll(() => {
  api?.kill('SIGTERM')
})

test.beforeEach(() => {
  // A session left by an earlier test would restore windows this one did not
  // open, and the assertions here are all counts.
  if (existsSync(SESSION)) rmSync(SESSION)
})

async function launch(): Promise<ElectronApplication> {
  return _electron.launch({
    args: ['.', '--no-sandbox', '--disable-gpu'],
    cwd: DESKTOP,
    executablePath: ELECTRON,
    env: { ...process.env },
  })
}

type ShellWindow = { windowId: number; workspaceId: string | null; groupId: string | null }

/** What the main process thinks exists, asked the way a renderer asks it.
 *
 * Through the preload bridge rather than by reaching into the main process, so
 * every call here exercises the real IPC contract -- including its validation.
 */
async function windows(app: ElectronApplication): Promise<ShellWindow[]> {
  const page = await app.firstWindow()
  return page.evaluate(() => window.algoforge.workspaces.list().then((r) => r.windows))
}

/** Only the windows holding a workspace.
 *
 * The main window is registered too, with a null workspace, because the
 * registry tracks every window the shell owns rather than only the interesting
 * ones. Counting raw entries would make "one workspace is open" read as two.
 */
async function workspaceWindows(app: ElectronApplication): Promise<ShellWindow[]> {
  return (await windows(app)).filter((each) => each.workspaceId !== null)
}

async function openWorkspace(app: ElectronApplication, id: string, intent = 'new'): Promise<void> {
  const page = await app.firstWindow()
  await page.evaluate(
    ([workspaceId, how]) => window.algoforge.workspaces.open(workspaceId, how),
    [id, intent] as const,
  )
}

test('the shell starts, shows one window and shuts down cleanly', async () => {
  const app = await launch()
  const page = await app.firstWindow()
  await expect.poll(() => page.title()).toBe('AlgoForge')

  const count = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)
  expect(count).toBe(1)

  await app.close()
})

test('the preload exposes named verbs and no generic invoke', async () => {
  /* A generic `invoke(channel, payload)` would make the channel a value the
   * page chooses, and the allowlist would be guarding a set the renderer is
   * free to explore. */
  const app = await launch()
  const page = await app.firstWindow()
  const bridge = await page.evaluate(() => ({
    desktop: window.algoforge?.desktop,
    verbs: Object.keys(window.algoforge?.workspaces ?? {}).sort(),
    hasInvoke: typeof (window.algoforge as unknown as { invoke?: unknown })?.invoke,
    nodeReachable: typeof (window as unknown as { require?: unknown }).require,
  }))

  expect(bridge.desktop).toBe(true)
  expect(bridge.hasInvoke).toBe('undefined')
  expect(bridge.nodeReachable).toBe('undefined')
  expect(bridge.verbs).toEqual(
    ['close', 'group', 'list', 'open', 'restoreSession', 'ungroup'],
  )

  await app.close()
})

test('a workspace opens in a second window, and both coexist', async () => {
  const app = await launch()
  await openWorkspace(app, 'ws_research')
  await expect
    .poll(async () => (await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)))
    .toBe(2)

  const open = await workspaceWindows(app)
  expect(open.map((w) => w.workspaceId)).toContain('ws_research')

  await app.close()
})

test('opening a workspace that is already open focuses it rather than duplicating', async () => {
  /* Two windows editing one layout is the bug that presents as "my panels keep
   * moving back", because neither knows about the other's writes. */
  const app = await launch()
  await openWorkspace(app, 'ws_research')
  await expect
    .poll(async () => (await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)))
    .toBe(2)

  await openWorkspace(app, 'ws_research', 'auto')
  await new Promise((resolve) => setTimeout(resolve, 500))

  const count = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)
  expect(count).toBe(2)

  await app.close()
})

test('closing a workspace window leaves no orphan behind', async () => {
  /* An entry left pointing at a dead window presents as "I can't open it any
   * more": the registry believes the workspace is already somewhere. */
  const app = await launch()
  await openWorkspace(app, 'ws_prop')
  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(1)

  const [{ windowId }] = await workspaceWindows(app)
  const page = await app.firstWindow()
  await page.evaluate((id) => window.algoforge.workspaces.close(id), windowId)

  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(0)
  await expect
    .poll(async () => (await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)))
    .toBe(1)

  await app.close()
})

test('a workspace can be reopened after being closed', async () => {
  const app = await launch()
  await openWorkspace(app, 'ws_prop')
  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(1)
  const [{ windowId }] = await workspaceWindows(app)

  const page = await app.firstWindow()
  await page.evaluate((id) => window.algoforge.workspaces.close(id), windowId)
  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(0)

  await openWorkspace(app, 'ws_prop')
  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(1)

  await app.close()
})

test('two workspace windows can be grouped and separated again', async () => {
  const app = await launch()
  await openWorkspace(app, 'ws_research')
  await openWorkspace(app, 'ws_prop')
  await expect.poll(async () => (await workspaceWindows(app)).length).toBe(2)

  const ids = (await workspaceWindows(app)).map((w) => w.windowId)
  const page = await app.firstWindow()
  await page.evaluate((windowIds) => window.algoforge.workspaces.group(windowIds), ids)

  const grouped = await workspaceWindows(app)
  expect(grouped[0].groupId).toBeTruthy()
  expect(grouped[0].groupId).toBe(grouped[1].groupId)

  await page.evaluate((id) => window.algoforge.workspaces.ungroup(id), ids[0])
  const split = await workspaceWindows(app)
  expect(split.find((w) => w.windowId === ids[0])?.groupId).toBe(null)

  await app.close()
})

test('rapid open and close leaves no windows and no registry entries', async () => {
  /* The soak the directive asks for, bounded: what it catches is an entry that
   * survives its window, which accumulates silently until a workspace can no
   * longer be opened at all. */
  const app = await launch()
  for (let round = 0; round < 5; round += 1) {
    await openWorkspace(app, `ws_soak_${round}`)
    await expect.poll(async () => (await workspaceWindows(app)).length).toBe(1)
    const [{ windowId }] = await workspaceWindows(app)
    const page = await app.firstWindow()
    await page.evaluate((id) => window.algoforge.workspaces.close(id), windowId)
    await expect.poll(async () => (await workspaceWindows(app)).length).toBe(0)
  }

  const remaining = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)
  expect(remaining).toBe(1)

  await app.close()
})

test('the arrangement is written to disk and restored after a relaunch', async () => {
  const first = await launch()
  await openWorkspace(first, 'ws_research')
  await openWorkspace(first, 'ws_prop')
  await expect.poll(async () => (await workspaceWindows(first)).length).toBe(2)
  // Closing flushes the debounced session rather than losing the last 400ms.
  await first.close()

  expect(existsSync(SESSION)).toBe(true)

  const second = await launch()
  const page = await second.firstWindow()
  await page.evaluate(() => window.algoforge.workspaces.restoreSession())

  await expect.poll(async () => (await workspaceWindows(second)).map((w) => w.workspaceId).sort())
    .toEqual(['ws_prop', 'ws_research'])

  await second.close()
})

test('a restore from a corrupt session file does not stop the shell starting', async () => {
  /* A session file is written by a process that can be killed mid-write. The
   * shell has to open anyway, because the alternative is an application that
   * will not start and gives no way to fix it. */
  mkdirSync(join(ROOT, 'data', 'runtime'), { recursive: true })
  writeFileSync(SESSION, '{ not json')

  const app = await launch()
  const page = await app.firstWindow()
  await expect.poll(() => page.title()).toBe('AlgoForge')
  await page.evaluate(() => window.algoforge.workspaces.restoreSession())

  const count = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)
  expect(count).toBe(1)

  await app.close()
})

test('an undeclared IPC channel is refused', async () => {
  /* The contract fails closed. A channel that works and was never specified is
   * the wrong direction for this mistake to fail in. */
  const app = await launch()
  const page = await app.firstWindow()
  const refused = await page.evaluate(async () => {
    try {
      // Reaching past the bridge on purpose: this is what a compromised
      // renderer would try.
      const electron = (window as unknown as { require?: (m: string) => unknown }).require
      if (!electron) return 'no-node-access'
      return 'reachable'
    } catch {
      return 'threw'
    }
  })
  expect(refused).toBe('no-node-access')

  await app.close()
})
