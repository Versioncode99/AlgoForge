import { expect, test } from '@playwright/test'
import { API, enterMode, leaveMode } from './mode'

/* End-to-end against a live API on 8765 and Vite on 5173. The config starts
 * neither — run both before this suite or every test fails on connection
 * refused rather than on anything about the application. */

/* The navigation now comes from the mode manifest rather than from a constant
 * in the shell, so this suite reads the same manifest instead of holding a
 * second copy of it. A section added in `forge.modes` is covered here the
 * moment it exists, and one removed stops being asserted rather than failing
 * forever against a list nobody updated.
 *
 * `Agents` left the removed list: it is a real section in AI mode now, and the
 * thing that was removed was the old fixture-driven screen of the same name. */
const REMOVED = ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Evolution']

/* AI mode carries the widest section list, so it is where the tests that are
 * about the shell rather than about one mode do their work. */
test.beforeEach(async ({ request }) => {
  await enterMode(request, 'ai')
})

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

test('every section every mode declares is reachable, in every mode', async ({ page, request }) => {
  test.setTimeout(180_000)
  const manifest = await (await request.get(`${API}/modes`)).json()
  for (const mode of manifest.data.modes) {
    await enterMode(request, mode.mode, mode.stances[0] ?? undefined)
    await page.goto('/')
    await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
    for (const section of mode.sections) {
      await page.getByRole('link', { name: section.label, exact: true }).click()
      await expect(
        page.locator(`a[href="#${await page.evaluate(() => location.hash.slice(1))}"]`),
      ).toHaveAttribute('aria-current', 'page')
      // The paper-only label is chrome, so it must survive every navigation in
      // every mode. It is the one claim the application makes on every screen.
      await expect(page.getByText('PAPER ONLY').last()).toBeVisible()
    }
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
  await enterMode(request, 'normal')
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

test('a backtest runs as a job with live progress and lands real trades', async ({ page }) => {
  // A real backtest on real bars outlives the 30s default in the config.
  test.setTimeout(240_000)
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

test('prop firm runs a matrix over every strategy, not one at a time', async ({ page, request }) => {
  test.setTimeout(180_000)
  await enterMode(request, 'prop_firm')
  await page.goto('/')
  await page.getByRole('link', { name: 'Rule Simulation', exact: true }).click()
  await expect(page.getByRole('button', { name: /Run the matrix/ })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Account phase' })).toBeVisible()

  const strategyCount = Number(await page.locator('.context-facts > span').filter({ hasText: 'STRATEGIES' }).locator('b').textContent())
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
  await page.goto('/')
  await page.getByRole('link', { name: 'Validation', exact: true }).click()
  await expect(page.getByText(/Selection risk, temporal stability and path risk/)).toBeVisible()
  await expect(page.getByRole('button', { name: /Run WF \+ CSCV \+ CPCV/ })).toBeVisible()
  await expect(page.getByText(/it is not a Monte Carlo account simulation/)).toBeVisible()
})

test('capture desktop evidence', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'desktop evidence only')

  // The chooser first: it is the screen the product opens on, so it is the one
  // screenshot that has to exist before any of the others mean anything.
  await leaveMode(request)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /choose your workspace/i })).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/mode-select.png', fullPage: true })

  await enterMode(request, 'normal')
  await page.goto('/')
  await expect(page.getByText('Active mission')).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/overview-desktop.png', fullPage: true })
  await page.getByRole('link', { name: 'Strategies', exact: true }).click()
  await expect(page.getByLabel('Filter strategies')).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/strategies-desktop.png', fullPage: true })
  await page.getByRole('link', { name: 'Validation', exact: true }).click()
  await expect(page.getByRole('button', { name: /Run WF \+ CSCV \+ CPCV/ })).toBeVisible()
  await page.waitForTimeout(500)
  await page.screenshot({ path: '../../artifacts/qa/validation-lab-desktop.png', fullPage: true })

  await enterMode(request, 'prop_firm')
  await page.goto('/')
  await page.getByRole('link', { name: 'Rule Simulation', exact: true }).click()
  await expect(page.getByRole('button', { name: /Run the matrix/ })).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/propfirm-desktop.png', fullPage: true })

  await enterMode(request, 'hedge_fund', 'human_in_the_loop')
  await page.goto('/')
  await expect(page.getByText('NAV')).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: '../../artifacts/qa/fund-command-desktop.png', fullPage: true })
})
