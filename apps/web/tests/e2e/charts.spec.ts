import { expect, test } from '@playwright/test'

/* The chart, against whatever archives this machine actually holds.
 *
 * These assertions are about honesty as much as rendering. A chart is where
 * most people form their belief about a strategy, so the two things that must
 * never happen are drawing a series that is not real, and drawing a daily bar
 * without saying where its boundary falls. */

test('charts draw real candles and say what they are', async ({ page }) => {
  await page.goto('/#charts')

  const toolbar = page.getByLabel('Chart data set')
  await expect(toolbar).toBeVisible({ timeout: 30_000 })

  const provenance = page.locator('.chart-provenance')
  await expect(provenance).toBeVisible({ timeout: 60_000 })

  // Real or fixture, but never silent about which.
  await expect(provenance).toContainText(/REAL DATA|FIXTURE/)

  // A bar count that came from an archive, not a placeholder.
  await expect(provenance).toContainText(/[\d,]+ bars/)

  // The canvas is present and has been given room.
  const canvas = page.locator('.price-chart-canvas canvas').first()
  await expect(canvas).toBeVisible()
  const box = await canvas.boundingBox()
  expect(box?.height ?? 0).toBeGreaterThan(100)
})

test('the daily timeframe states the session it bucketed on', async ({ page }) => {
  await page.goto('/#charts')
  await expect(page.getByLabel('Chart data set')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('group', { name: 'Timeframe' }).getByRole('button', { name: '1d' }).click()

  // A daily futures bar is a session, not a calendar day, and an unlabelled
  // boundary is a number nobody can check.
  await expect(page.locator('.chart-provenance')).toContainText(/exchange session/i, {
    timeout: 60_000,
  })
  await expect(page.locator('.chart-provenance')).toContainText(/America\/New_York/)
})

test('timeframes are switchable and report which is active', async ({ page }) => {
  await page.goto('/#charts')
  const group = page.getByRole('group', { name: 'Timeframe' })
  await expect(group).toBeVisible({ timeout: 30_000 })

  for (const key of ['1h', '4h']) {
    await group.getByRole('button', { name: key, exact: true }).click()
    await expect(group.getByRole('button', { name: key, exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  }
})

test('no console errors while charting', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await page.goto('/#charts')
  await expect(page.locator('.chart-provenance')).toBeVisible({ timeout: 60_000 })
  await page.getByRole('group', { name: 'Timeframe' }).getByRole('button', { name: '1d' }).click()
  await page.waitForTimeout(1500)
  expect(errors).toEqual([])
})
