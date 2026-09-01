import { expect, test } from '@playwright/test'

test('every research workspace is reachable and safety labels persist', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(page.getByText(/SAMPLE DATA · UNCALIBRATED/)).toBeVisible()
  for (const tab of ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Prop Firm', 'Agents', 'Evolution']) {
    await page.getByRole('button', { name: tab }).click()
    await expect(page.getByRole('heading', { name: tab, exact: true })).toBeVisible()
    await expect(page.getByText(/SAMPLE DATA · UNCALIBRATED/)).toBeVisible()
  }
  await expect(page.getByText('Automatic live changes: NEVER')).toBeVisible()
})

test('prop rule selection updates the simulation heading', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Prop Firm' }).click()
  const selector = page.getByLabel('Rule fixture')
  await expect(selector).toBeVisible()
  const options = selector.locator('option')
  expect(await options.count()).toBeGreaterThanOrEqual(4)
  await selector.selectOption({ index: 2 })
  await expect(page.getByText(/Challenge equity paths · 300 simulations/)).toBeVisible()
})

test('capture desktop release evidence', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'desktop evidence only')
  await page.goto('/')
  await expect(page.getByText(/research verdict you can audit/i)).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/overview-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Prop Firm' }).click()
  await expect(page.getByText(/Challenge equity paths · 300 simulations/)).toBeVisible()
  await page.screenshot({ path: '../../artifacts/qa/prop-desktop.png', fullPage: true })
})
