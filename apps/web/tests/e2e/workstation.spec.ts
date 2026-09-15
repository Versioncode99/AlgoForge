import { expect, test } from '@playwright/test'
import { API } from './authority'

/* End-to-end against a live API on 8765 and Vite on 5173. The config starts
 * neither — run both before this suite or every test fails on connection
 * refused rather than on anything about the application. */

/* The navigation comes from `forge.product.navigation`, one manifest served at
 * `/navigation`, so this suite reads that manifest instead of holding a second
 * copy of it. A destination added there is covered here the moment it exists,
 * and one removed stops being asserted rather than failing forever against a
 * list nobody updated.
 *
 * There is no mode to enter first. Every destination is in the rail at all
 * times, which is the whole point of the change: a section used to be absent
 * because of which mode was open, and a test then had to pick a mode before it
 * could navigate. `Agents` left the removed list — it is a real destination
 * now, and what was removed was the old fixture-driven screen of that name. */
const REMOVED = ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Evolution']

/** Whether any dataset on this machine holds bars something could run on.
 *
 * A backtest, a rule matrix and a chart all need the same thing, and an
 * installation without a market-data credential has none of it. The tests that
 * need bars skip here, naming the blocker, and each has a counterpart that runs
 * in exactly that case and asserts the refusal is what appears — so there is no
 * configuration in which this file checks nothing.
 */
async function archiveLoaded(
  request: import('@playwright/test').APIRequestContext,
): Promise<boolean> {
  const response = await request.get(`${API}/datasets`)
  if (!response.ok()) return false
  const rows = (await response.json()).data as { loaded?: boolean; bar_count?: number }[]
  return rows.some((row) => row.loaded && (row.bar_count ?? 0) > 0)
}

const NO_ARCHIVE = 'no dataset on this machine holds bars, so nothing can be run over them'

/** Strategies opens on the catalogue, so the detail pane is one row-click away.
 *  Returns false when the configured vault holds no strategies at all, which is
 *  a skip rather than a failure — the assertion is about the strategy surface,
 *  not about whether this machine happens to have records. */
async function openFirstStrategy(page: import('@playwright/test').Page): Promise<boolean> {
  await page.getByRole('link', { name: 'Strategies', exact: true }).click()
  const rows = page.locator('.catalogue-table tbody tr:not([aria-hidden="true"])')
  // The library is read from disk and this suite also drives real backtests on
  // the same process, so the list can legitimately take tens of seconds here.
  // A short wait made these tests fail on API load rather than on the UI.
  await expect(page.getByText('No strategies yet').or(rows.first())).toBeVisible({ timeout: 120_000 })
  if (await page.getByText('No strategies yet').isVisible()) return false
  await rows.first().click()
  await expect(page.getByRole('button', { name: 'Code', exact: true })).toBeVisible({ timeout: 30_000 })
  return true
}

test('every destination the manifest declares is reachable', async ({ page, request }) => {
  test.setTimeout(180_000)
  const manifest = await (await request.get(`${API}/navigation`)).json()
  const destinations = manifest.data.destinations as { label: string }[]
  expect(destinations.length, 'the navigation manifest is empty').toBeGreaterThan(0)

  await page.goto('/')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  for (const destination of destinations) {
    const rail = page.getByRole('navigation', { name: 'Sections' })
    await rail.getByRole('link', { name: destination.label, exact: true }).click()
    // On the rail, and `page`. The tab bar below marks the current *view* with
    // `aria-current="true"` — two elements claiming to be the current page is
    // one claim too many, so the values differ on purpose.
    await expect(
      rail.getByRole('link', { name: destination.label, exact: true }),
    ).toHaveAttribute('aria-current', 'page')
    // The paper-only label is chrome, so it must survive every navigation. It
    // is the one claim the application makes on every screen.
    await expect(page.getByText('PAPER ONLY').last()).toBeVisible()
  }
})

test('no destination is hidden behind a setting', async ({ page, request }) => {
  /* The mode chooser used to decide which sections existed, and Campaigns was
   * one of the things it could hide. Nothing hides a destination now, and this
   * is the assertion that says so: the rail carries the whole manifest on a
   * plain load, with no mode entered and nothing configured. */
  const manifest = await (await request.get(`${API}/navigation`)).json()
  await page.goto('/')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  for (const destination of manifest.data.destinations as { label: string }[]) {
    await expect(
      page.getByRole('link', { name: destination.label, exact: true }),
    ).toHaveCount(1)
  }
})

test('the fixture-driven sections stay removed', async ({ page }) => {
  // Verdict, Regimes and Risk all rendered one seeded run, so they showed the
  // same numbers whatever was selected. Their absence is the feature.
  await page.goto('/')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible()
  for (const gone of REMOVED) {
    await expect(page.getByRole('link', { name: gone, exact: true })).toHaveCount(0)
  }
})

test('overview lists the library rather than leaving the page empty', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.locator('.engine-state')).toContainText('AUTONOMOUS ENGINE')
  await expect(page.getByRole('button', { name: /Start engine/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Strongest candidates' })).toBeVisible()
  const dataset = page.getByLabel('Dataset')
  await expect(dataset).toBeVisible()
})

test('strategy code is visible and the guard rejects unsafe edits', async ({ page }) => {
  await page.goto('/')
  test.skip(!(await openFirstStrategy(page)), 'No strategy records in the current configured vault')
  await page.getByRole('button', { name: 'Code', exact: true }).click()

  const editor = page.getByLabel('Strategy source code')
  await expect(editor).toBeVisible()
  await expect(editor).toContainText('def entry_signal')

  await editor.fill('import os\ndef entry_signal(w, p):\n    return 1\ndef exit_signal(w, p, pos):\n    return None\n')
  await page.getByRole('button', { name: /Save changes/ }).click()
  await expect(page.getByText(/import of 'os' is not allowed/)).toBeVisible()
})

test('a data set and a history range are chosen before anything runs', async ({ page }) => {
  await page.goto('/')
  test.skip(!(await openFirstStrategy(page)), 'No strategy records in the current configured vault')

  const data = page.getByLabel('Data set')
  await expect(data).toBeVisible()
  await expect(data.locator('option')).toContainText([/16 years/])

  // The range buttons carry the bar count they resolve to, so the cost of the
  // run is visible before it is started.
  const ranges = page.getByRole('group', { name: 'History range' })
  await expect(ranges.getByRole('button', { name: /^1 year/ })).toBeVisible()
  await expect(ranges.getByRole('button', { name: /^Max/ })).toBeVisible()
  await expect(page.getByText(/bars · about/)).toBeVisible()
})

test('a backtest runs as a job with live progress and lands real trades', async ({
  page,
  request,
}) => {
  // A real backtest on real bars outlives the 30s default in the config.
  test.setTimeout(240_000)
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
  await page.goto('/')
  test.skip(!(await openFirstStrategy(page)), 'No strategy records in the current configured vault')

  // Smallest range, so the assertion is about the mechanism rather than a
  // multi-minute wait.
  await page.getByRole('group', { name: 'History range' })
    .getByRole('button', { name: /^3 months/ }).click()
  await page.getByRole('button', { name: /Run backtest/ }).click()

  // The job bar is the contract: it must appear while work is in flight.
  await expect(page.locator('.jobbar')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.jobbar')).toHaveAttribute('data-status', 'done', { timeout: 180_000 })
  await expect(page.getByText(/trades on .* bars · net/)).toBeVisible()

  await page.getByRole('button', { name: 'Summary', exact: true }).click()
  await expect(page.getByText('Performance summary')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Long' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Short' })).toBeVisible()

  await page.getByRole('button', { name: 'Trades', exact: true }).click()
  await expect(page.getByText('DECISION BAR ALWAYS PRECEDES FILL BAR')).toBeVisible()

  await page.getByRole('button', { name: 'Periods', exact: true }).click()
  await expect(page.getByText('Returns by period')).toBeVisible()
})

test('an unjudged strategy withholds the pass rather than granting it', async ({ page }) => {
  await page.goto('/')
  test.skip(!(await openFirstStrategy(page)), 'No strategy records in the current configured vault')
  await page.getByRole('button', { name: 'Gates', exact: true }).click()
  await expect(page.getByText(/Not judged in this session/)).toBeVisible()
  await expect(page.getByText(/withholds the pass/)).toBeVisible()
})

test('prop firm runs a matrix over every strategy, not one at a time', async ({
  page,
  request,
}) => {
  test.setTimeout(180_000)
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
  // Simulation is a tab of the Prop Desk now, not a destination of its own.
  await page.goto('/#propdesk?tab=simulation')
  await expect(page.getByRole('button', { name: /Run the matrix/ })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Account phase' })).toBeVisible()

  /* From the API, not from the chrome. The context bar used to carry a
   * STRATEGIES count and this test read it; the bar now carries only the three
   * facts that are safety-critical — paper-only, offline, degraded — so the
   * count is asked for where it actually lives. Reading a number off chrome
   * that no longer displays it is how a test hangs for three minutes on a
   * locator that will never resolve. */
  const library = await (await request.get(`${API}/strategies`)).json()
  const strategyCount = Array.isArray(library?.data) ? library.data.length : 0
  if (strategyCount === 0 || await page.getByRole('button', { name: /Run the matrix/ }).isDisabled()) {
    await expect(page.getByText('No matrix yet')).toBeVisible()
    return
  }

  await page.getByRole('button', { name: /Run the matrix/ }).click()

  // Three outcomes are correct here and which one you get depends on what has
  // been backtested. Either a matrix comes back, or the run is refused with the
  // reason and the bar count that would lift it, or the job is still working
  // and says so. What must never happen is silence, so the test accepts all
  // three and rejects none.
  //
  // The third case is not a concession. This workspace qualifies 267
  // strategies, so the matrix is 267 x 4 rules x 200 paths = 1,068 simulations,
  // measured at ~2.1/s: about eight and a half minutes. A test that waited for
  // completion would be asserting that the machine is fast, not that the
  // product is correct. What the product owes the operator at minute two is a
  // job bar with a real count on it, and that is what is checked.
  const grid = page.getByText('Pass rate by strategy and rule')
  const refusal = page.getByText('Nothing could be simulated')
  const working = page.locator('.jobbar[data-status="running"]')
  await expect(grid.or(refusal).or(working)).toBeVisible({ timeout: 120_000 })

  if (await working.isVisible()) {
    // Progress must be a real count against a real total, never a spinner.
    await expect(working.locator('.jobbar-label')).toContainText(/\d+ strategies/)
    await expect(working.locator('em')).toContainText(/\/\d/)
    return
  }

  if (await grid.isVisible()) {
    // Skipped strategies are reported with a reason, never dropped silently.
    // Scoped to the heading: 'Skipped' is also a stat-tile label above it.
    await expect(page.getByRole('heading', { name: 'Skipped' })).toBeVisible()
  } else {
    // The refusal must name the reason per strategy, not just decline.
    await expect(page.getByRole('columnheader', { name: 'What would fix it' })).toBeVisible()
    await expect(page.getByText(/insufficient_days/).first()).toBeVisible()
  }
})

test('updates live in settings, not on a tab of their own', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(page.getByText('Updates and provenance')).toBeVisible()
  await expect(page.getByText('Automatic live changes')).toBeVisible()
})

test('validation lab distinguishes selection paths from Monte Carlo', async ({ page }) => {
  // Validation is a tab of Research now, not a destination of its own: it is
  // one reading about a strategy rather than a place to go.
  await page.goto('/#research?tab=validation')
  await expect(page.getByText(/Selection risk, temporal stability and path risk/)).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByRole('button', { name: /Run WF \+ CSCV \+ CPCV/ })).toBeVisible()
  await expect(page.getByText(/it is not a Monte Carlo account simulation/)).toBeVisible()
})

test('capture desktop evidence', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'desktop evidence only')
  test.setTimeout(300_000)

  /* One screenshot per destination in the shipped manifest, at the width the
   * product is designed for. Driven by the manifest rather than a list, for the
   * same reason the navigation sweep is: a hand-written list captures the
   * screens somebody remembered, and the point of visual evidence is the ones
   * nobody looked at.
   *
   * The first shot used to be the mode chooser, which no longer exists. There
   * is no "way in" screen to capture now — the product opens on Home. */
  const manifest = await (await request.get(`${API}/navigation`)).json()
  const rows = manifest.data.destinations as { route: string; label: string }[]
  expect(rows.length, 'the navigation manifest is empty').toBeGreaterThan(0)

  await page.setViewportSize({ width: 1440, height: 900 })
  for (const destination of rows) {
    await page.goto(`/#${destination.route}`)
    await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
    // Past the lazy boundary and past the entry animation, so the capture is
    // the screen rather than a fade.
    await expect(page.locator('#main-content > div.state[role="status"]')).toHaveCount(0, {
      timeout: 30_000,
    })
    await page.waitForFunction(
      () => document.getAnimations().every((animation) => animation.playState !== 'running'),
      null,
      { timeout: 30_000 },
    )
    await page.screenshot({
      path: `../../artifacts/qa/${destination.route}-desktop.png`,
      fullPage: true,
    })
  }
})


test('a run with no archive is refused with the reason, not started and failed', async ({
  page,
  request,
}) => {
  /* The counterpart to the backtest test, and the one that runs on an
   * installation with no market data.
   *
   * What must not happen is a job that starts, works, and ends `failed` with
   * nothing said — which is what this build did before the guard above existed,
   * and what made the test above hang for three minutes watching a job bar go
   * red. A backtest over bars that are not there is knowable in advance, so it
   * is refused in advance, with the reason.
   */
  test.skip(
    await archiveLoaded(request),
    'this installation holds a loaded archive, so a run is started rather than refused',
  )

  await page.goto('/')
  test.skip(!(await openFirstStrategy(page)), 'No strategy records in the current configured vault')

  await page.getByRole('group', { name: 'History range' })
    .getByRole('button', { name: /^3 months/ }).click()
  await page.getByRole('button', { name: /Run backtest/ }).click()

  /* Either the run is refused before it starts, or the job bar ends `failed`
   * carrying the reason. Both are honest; silence is not, and neither is a bar
   * that goes red with no text. */
  const refusal = page.getByRole('alert')
  const failed = page.locator('.jobbar[data-status="failed"]')
  await expect(refusal.or(failed).first()).toBeVisible({ timeout: 60_000 })

  const said = await (await failed.isVisible() ? failed : refusal.first()).innerText()
  expect(
    said.trim().length,
    'the run stopped without saying why, which is the failure this test exists for',
  ).toBeGreaterThan(0)
  expect(said).toMatch(/unavailable|not set|no (bars|archive|data)|insufficient|refus/i)
})
