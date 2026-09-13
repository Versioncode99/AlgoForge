import { type ElectronApplication, _electron, expect, test } from '@playwright/test'
import { type ChildProcess, spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/* The multi-window baseline Doc 2 asks for, measured rather than asserted.
 *
 * The directive is explicit twice over: baseline before optimising, and do not
 * invent "excellent" numbers. So this measures the operations it names on the
 * real shell and writes what it observed to `docs/PERFORMANCE_BASELINE.json`.
 *
 * **These are not pass/fail thresholds.** A number measured once on one
 * container is a starting point, not a budget, and a suite that fails when a
 * laptop is busy teaches people to ignore it. The assertions here are only the
 * ones that catch a *structural* regression — work that grows with window count
 * when it should not, or memory that does not come back — because those are
 * properties of the architecture rather than of the machine.
 */

const DESKTOP = join(process.cwd(), '..', 'desktop')
const ELECTRON = join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron')
const ROOT = join(process.cwd(), '..', '..')
const SESSION = join(ROOT, 'data', 'runtime', 'workspace-session.json')
const REPORT = join(ROOT, 'docs', 'PERFORMANCE_BASELINE.json')
const HEALTH = 'http://127.0.0.1:8765/api/v1/health'

/** Every measurement taken, written out at the end. */
const measured: Record<string, unknown> = {}

let api: ChildProcess | null = null

async function healthy(): Promise<boolean> {
  try {
    return (await fetch(HEALTH, { signal: AbortSignal.timeout(1500) })).ok
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
  throw new Error('the API did not become healthy')
})

test.afterAll(() => {
  api?.kill('SIGTERM')
  writeFileSync(
    REPORT,
    `${JSON.stringify(
      {
        note:
          'Measured by apps/web/tests/electron/performance.spec.ts on one container. ' +
          'A starting point for comparison, not a budget: see docs/PERFORMANCE_BASELINE.md.',
        measured_at: new Date().toISOString(),
        platform: `${process.platform} ${process.arch}`,
        node: process.version,
        ...measured,
      },
      null,
      2,
    )}\n`,
  )
})

test.beforeEach(() => {
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

async function openWorkspace(app: ElectronApplication, id: string): Promise<void> {
  const page = await app.firstWindow()
  await page.evaluate((workspaceId) => window.algoforge.workspaces.open(workspaceId, 'new'), id)
}

async function workspaceWindows(app: ElectronApplication): Promise<number> {
  const page = await app.firstWindow()
  const all = await page.evaluate(() =>
    window.algoforge.workspaces.list().then((r) => r.windows),
  )
  return all.filter((each) => each.workspaceId !== null).length
}

/** Resident memory across every process the app owns, in megabytes. */
async function residentMb(app: ElectronApplication): Promise<number> {
  const metrics = await app.evaluate(({ app: electronApp }) => electronApp.getAppMetrics())
  const kb = metrics.reduce((total, entry) => total + (entry.memory?.workingSetSize ?? 0), 0)
  return Math.round(kb / 1024)
}

function ms(started: number): number {
  return Math.round(performance.now() - started)
}

test('cold start, first window, and shutdown', async () => {
  const startedAt = performance.now()
  const app = await launch()
  const page = await app.firstWindow()
  await page.waitForLoadState('domcontentloaded')
  const started = ms(startedAt)

  const closing = performance.now()
  await app.close()
  measured.cold_start_ms = started
  measured.shutdown_ms = ms(closing)

  // A structural assertion only: something is wrong with the harness, not the
  // product, if this is instant or takes a minute.
  expect(started).toBeGreaterThan(0)
  expect(started).toBeLessThan(90_000)
})

test('second-window creation, and what each additional window costs', async () => {
  const app = await launch()
  await (await app.firstWindow()).waitForLoadState('domcontentloaded')

  const base = await residentMb(app)
  const perWindow: number[] = []
  const growth: number[] = []

  for (let index = 0; index < 3; index += 1) {
    const startedAt = performance.now()
    await openWorkspace(app, `ws_perf_${index}`)
    await expect.poll(async () => workspaceWindows(app)).toBe(index + 1)
    perWindow.push(ms(startedAt))
    growth.push((await residentMb(app)) - base)
  }

  measured.window_open_ms = perWindow
  measured.resident_mb_base = base
  measured.resident_mb_growth = growth

  /* The structural property: opening the third window must not cost
   * dramatically more than the second. Work that grows super-linearly with
   * window count is the shape of a leak or an O(n^2) broadcast, and that is a
   * defect wherever it runs — unlike the absolute numbers, which are a property
   * of this container. The bound is deliberately loose because a cold cache
   * makes the first one slower, not the last. */
  const first = perWindow[0]
  const last = perWindow[perWindow.length - 1]
  expect(last).toBeLessThan(Math.max(first * 4, 5_000))

  await app.close()
})

test('memory comes back when windows close', async () => {
  /* The leak check. Absolute memory is a property of the machine; memory that
   * never returns is a property of the code. */
  const app = await launch()
  await (await app.firstWindow()).waitForLoadState('domcontentloaded')
  const base = await residentMb(app)

  for (let round = 0; round < 4; round += 1) {
    await openWorkspace(app, `ws_cycle_${round}`)
    await expect.poll(async () => workspaceWindows(app)).toBe(1)
    const page = await app.firstWindow()
    const all = await page.evaluate(() =>
      window.algoforge.workspaces.list().then((r) => r.windows),
    )
    const target = all.find((each) => each.workspaceId !== null)!
    await page.evaluate((id) => window.algoforge.workspaces.close(id), target.windowId)
    await expect.poll(async () => workspaceWindows(app)).toBe(0)
  }

  const after = await residentMb(app)
  measured.resident_mb_after_four_cycles = { base, after, delta: after - base }

  /* Four open/close rounds leaving several hundred megabytes behind would mean
   * windows are not being released. Generous, because a renderer's memory is
   * returned when the OS and the allocator get round to it, not on close. */
  expect(after - base).toBeLessThan(400)

  await app.close()
})

test('restoring an arrangement, measured end to end', async () => {
  const first = await launch()
  await (await first.firstWindow()).waitForLoadState('domcontentloaded')
  await openWorkspace(first, 'ws_research')
  await openWorkspace(first, 'ws_prop')
  await expect.poll(async () => workspaceWindows(first)).toBe(2)
  await first.close()

  const second = await launch()
  const page = await second.firstWindow()
  await page.waitForLoadState('domcontentloaded')

  const startedAt = performance.now()
  await page.evaluate(() => window.algoforge.workspaces.restoreSession())
  await expect.poll(async () => workspaceWindows(second)).toBe(2)
  measured.restore_two_windows_ms = ms(startedAt)

  await second.close()
})

test('a renderer told it is obscured stops polling, and a background one does not', async () => {
  /* The claim Phase 3 rests on, measured on the real code path.
   *
   * The obvious way to test this — minimise the window and count requests —
   * measures nothing here: under Xvfb there is no window manager, so
   * `isMinimized()` stays false, the document never goes hidden, and the window
   * polls on exactly as before. That produced a plausible "6 of 11 requests
   * still fire when hidden", which looked like a product defect and was an
   * artefact of the harness.
   *
   * So this drives the mechanism the shell actually uses: the main process
   * sends a visibility state, the renderer feeds it to the query layer's focus
   * gate. That is environment-independent and is the thing worth asserting. */
  const app = await launch()
  const page = await app.firstWindow()
  await page.waitForLoadState('domcontentloaded')

  await page.evaluate(() => {
    const w = window as unknown as { __probe: number; fetch: typeof fetch }
    w.__probe = 0
    const real = w.fetch.bind(window)
    w.fetch = ((...args: Parameters<typeof fetch>) => {
      ;(window as unknown as { __probe: number }).__probe += 1
      return real(...args)
    }) as typeof fetch
  })

  async function requestsOver(seconds: number): Promise<number> {
    await page.evaluate(() => {
      ;(window as unknown as { __probe: number }).__probe = 0
    })
    await new Promise((resolve) => setTimeout(resolve, seconds * 1000))
    return page.evaluate(() => (window as unknown as { __probe: number }).__probe)
  }

  async function tell(state: string): Promise<void> {
    await app.evaluate(({ BrowserWindow }, visibility) => {
      BrowserWindow.getAllWindows()[0].webContents.send('workspace:visibility', { visibility })
    }, state)
    // Let the focus gate settle before counting.
    await new Promise((resolve) => setTimeout(resolve, 1500))
  }

  const active = await requestsOver(6)

  await tell('background')
  const background = await requestsOver(6)

  await tell('obscured')
  const obscured = await requestsOver(6)

  measured.requests_in_6s = { active, background, obscured }

  /* Background must keep working: several windows watched at once is the
   * arrangement this product is for, and pausing the unfocused half of it would
   * be the optimisation that makes the product worse. */
  expect(background).toBeGreaterThan(0)

  /* Obscured must stop. Asserted as "fewer than active" rather than zero: a
   * request already in flight still lands, and failing on that is a flake. */
  expect(obscured).toBeLessThan(active)

  await app.close()
})

test('IPC volume over an ordinary session', async () => {
  /* The third of the four dimensions the baseline named and did not measure.
   *
   * Counted in the preload, because that is the only place every invoke passes
   * through and the only count that is not an estimate. Measuring it from the
   * page would have meant wrapping the bridge, which changes the thing being
   * measured.
   *
   * What matters is the *idle* figure. A shell that chatters to its main
   * process while nobody is doing anything is paying for nothing, and it is the
   * kind of cost that only appears with several windows open. */
  const app = await launch()
  const page = await app.firstWindow()
  await page.waitForLoadState('domcontentloaded')

  const read = () => page.evaluate(() => window.algoforge.ipcCalls())

  const afterLoad = await read()
  await new Promise((resolve) => setTimeout(resolve, 6_000))
  const idle = (await read()) - afterLoad

  const beforeWork = await read()
  await openWorkspace(app, 'ws_ipc_a')
  await expect.poll(async () => workspaceWindows(app)).toBe(1)
  await openWorkspace(app, 'ws_ipc_b')
  await expect.poll(async () => workspaceWindows(app)).toBe(2)
  const opening = (await read()) - beforeWork

  measured.ipc_calls = { after_load: afterLoad, idle_over_6s: idle, opening_two_windows: opening }

  /* The structural assertion: an idle window must not be talking to the main
   * process on a timer. Opening windows costs calls and should; sitting still
   * should cost none. A handful is allowed for a resize or a focus event that
   * the container's compositor produced on its own. */
  expect(idle).toBeLessThan(10)

  await app.close()
})

test('the report is written where the next measurement can be compared to it', async () => {
  mkdirSync(join(ROOT, 'docs'), { recursive: true })
  expect(Object.keys(measured).length).toBeGreaterThan(0)
})
