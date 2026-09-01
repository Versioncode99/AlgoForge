import { expect, test } from '@playwright/test'

const TABS = ['Strategies', 'Verdict', 'Regimes', 'Risk & Monte Carlo', 'Prop Firm', 'Agents', 'Evolution']

test('every section is reachable and the truth label persists', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(page.getByText(/SYNTHETIC DATA · UNCALIBRATED/)).toBeVisible()
  for (const tab of TABS) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { name: tab, exact: true })).toBeVisible()
    await expect(page.getByText(/SYNTHETIC DATA · UNCALIBRATED/)).toBeVisible()
  }
  await page.getByRole('button', { name: 'Evolution', exact: true }).click()
  await expect(page.getByText('Automatic live changes: NEVER')).toBeVisible()
})

test('strategy code is visible and the guard rejects unsafe edits', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
  await expect(page.getByText(/The code this system runs/)).toBeVisible()

  // The actual executable source must be on screen, not a description of it.
  const editor = page.getByLabel('Strategy source code')
  await expect(editor).toBeVisible()
  await expect(editor).toContainText('def entry_signal')

  await editor.fill('import os\ndef entry_signal(w, p):\n    return 1\ndef exit_signal(w, p, pos):\n    return None\n')
  await page.getByRole('button', { name: /Save changes/ }).click()
  await expect(page.getByText(/import of 'os' is not allowed/)).toBeVisible()
})

test('a backtest runs real strategy code and produces real trades', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
  await page.getByRole('button', { name: /Run backtest/ }).click()
  await expect(page.getByText(/trades · net/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('LOOKAHEAD CLEAN')).toBeVisible()

  await page.getByRole('button', { name: /^trades$/i }).click()
  await expect(page.getByText('DECISION BAR ALWAYS PRECEDES FILL BAR')).toBeVisible()
})

test('prop rule selection updates the simulation heading', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Prop Firm', exact: true }).click()
  const selector = page.getByLabel('Rule fixture')
  await expect(selector).toBeVisible()
  expect(await selector.locator('option').count()).toBeGreaterThanOrEqual(4)
  await selector.selectOption({ index: 2 })
  await expect(page.getByText(/Challenge equity paths · 300 simulations/)).toBeVisible()
})

test('capture desktop evidence', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'desktop evidence only')
  await page.goto('/')
  await expect(page.getByText(/a verdict you can audit/i)).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/overview-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Strategies', exact: true }).click()
  await expect(page.getByLabel('Strategy source code')).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/strategies-desktop.png', fullPage: true })
})
