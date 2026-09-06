import { expect, test } from '@playwright/test'

/* End-to-end against a live API on 8765 and Vite on 5173. The config starts
 * neither — run both before this suite or every test fails on connection
 * refused rather than on anything about the application. */

const TABS = ['Research Lab', 'Strategies', 'Prop Firm', 'Console', 'Settings']
const REMOVED = ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Agents', 'Evolution']

test('every section is reachable and the truth label persists', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible()
  await expect(page.getByText(/PAPER ONLY · FILLS ARE MODELLED/)).toBeVisible()
  for (const tab of TABS) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { name: tab, level: 1 })).toBeVisible()
    await expect(page.getByText(/PAPER ONLY · FILLS ARE MODELLED/)).toBeVisible()
  }
})

test('the fixture-driven sections stay removed', async ({ page }) => {
  // Verdict, Regimes and Risk all rendered one seeded run, so they showed the
  // same numbers whatever was selected. Their absence is the feature.
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible()
  for (const gone of REMOVED) {
    await expect(page.getByRole('button', { name: gone, exact: true })).toHaveCount(0)
  }
})

test('overview lists the library rather than leaving the page empty', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.engine-state')).toContainText('AUTONOMOUS ENGINE')
  await expect(page.getByRole('button', { name: /Start engine/ })).toBeVisible()
  await expect(page.getByText('Library at a glance')).toBeVisible()
  const dataset = page.getByLabel('Dataset')
  await expect(dataset.locator('option')).toContainText([/databento/])
})

test('strategy code is visible and the guard rejects unsafe edits', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
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
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()

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
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()

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
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
  await page.getByRole('button', { name: 'Gates', exact: true }).click()
  await expect(page.getByText(/Not judged in this session/)).toBeVisible()
  await expect(page.getByText(/withholds the pass/)).toBeVisible()
})

test('prop firm runs a matrix over every strategy, not one at a time', async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto('/')
  await page.getByRole('button', { name: 'Prop Firm', exact: true }).click()
  await expect(page.getByRole('button', { name: /Run the matrix/ })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Account phase' })).toBeVisible()

  await page.getByRole('button', { name: /Run the matrix/ }).click()

  // Two outcomes are correct here and which one you get depends on what has
  // been backtested. Either a matrix comes back, or the run is refused with the
  // reason and the bar count that would lift it. What must never happen is
  // silence, so the test accepts both and rejects neither.
  const grid = page.getByText('Pass rate by strategy and rule')
  const refusal = page.getByText('Nothing could be simulated')
  await expect(grid.or(refusal)).toBeVisible({ timeout: 120_000 })

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
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await expect(page.getByText('Updates and provenance')).toBeVisible()
  await expect(page.getByText('Automatic live changes')).toBeVisible()
})

test('capture desktop evidence', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'desktop evidence only')
  await page.goto('/')
  await expect(page.locator('.engine-state')).toContainText('AUTONOMOUS ENGINE')
  await page.screenshot({ path: '../../artifacts/qa/overview-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
  await expect(page.getByText(/bars · about/)).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/strategies-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Prop Firm', exact: true }).click()
  await expect(page.getByRole('button', { name: /Run the matrix/ })).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/propfirm-desktop.png', fullPage: true })
})
