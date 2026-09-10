import { expect, test } from '@playwright/test'
import { enterMode } from './mode'

// The specialists live in AI mode's navigation.
test.beforeEach(async ({ request }) => {
  await enterMode(request, 'ai')
})

test('agent register exposes specialists and bounded controls', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await page.getByRole('link', { name: 'Agents', exact: true }).click()
  await expect(page.locator('.agent-register > button')).toHaveCount(8)
  await expect(page.locator('.agent-register-coordinator')).toContainText('ORCHESTRATOR')
  await expect(page.locator('.compute-worker')).toHaveCount(8)
  await page.locator('.agent-register > button').filter({ hasText: 'Risk officer' }).click()
  await expect(page.locator('.agent-inspector h3')).toHaveText('Risk officer')
  await expect(page.locator('.agent-inspector').getByRole('button', { name: 'Pause', exact: true })).toBeVisible()
  await page.getByLabel('Direct this specialist').fill('Explain the current risk evidence boundary.')
  await expect(page.getByRole('button', { name: 'Run task', exact: true })).toBeEnabled()
  await expect(page.locator('.agent-summary')).toBeVisible()
  expect(errors).toEqual([])
})

test('research brain shows sources and replication gaps', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Agents', exact: true }).click()
  await page.getByRole('button', { name: /Research brain/ }).click()
  await expect(page.locator('.source-card').first()).toBeVisible()
  await page.getByLabel('Filter library').fill('Moskowitz')
  await expect(page.locator('.source-card')).toHaveCount(1)
  await expect(page.locator('.source-gap')).toContainText('Intraday single-contract adaptation')
  await expect(page.locator('.source-card a')).toHaveAttribute('href', /aqr.com/)
  await page.getByLabel('Filter library').fill('no-matching-paper-9811')
  await expect(page.getByText('No references match this filter.')).toBeVisible()
})

test('command layout fits the viewport and reduced motion stops animations', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Agents', exact: true }).click()
  await expect(page.locator('.agent-register > button')).toHaveCount(8)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  expect(overflow).toBe(false)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.screenshot({ path: `../../artifacts/agent-command-${testInfo.project.name}.png` })
  await page.locator('.neural-panel').screenshot({ path: `../../artifacts/agent-register-${testInfo.project.name}.png` })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const animations = await page.locator('.agent-register > button').evaluateAll((nodes) => nodes.map(node => getComputedStyle(node).animationName))
  expect(animations.every(name => name === 'none')).toBe(true)
})

test('PC layouts fit laptop, desktop and ultrawide windows', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Agents', exact: true }).click()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  for (const [width, height] of [[1280, 800], [1440, 900], [1920, 1080], [2560, 1440]]) {
    await page.setViewportSize({ width, height })
    await expect(page.locator('.agent-register > button')).toHaveCount(8)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const panel = await page.locator('.neural-panel').boundingBox()
    for (const node of await page.locator('.agent-register > button').all()) {
      const box = await node.boundingBox()
      expect(box!.x).toBeGreaterThanOrEqual(panel!.x)
      expect(box!.x + box!.width).toBeLessThanOrEqual(panel!.x + panel!.width)
    }
    if (width === 1920) await page.screenshot({ path: '../../artifacts/agent-command-pc-1920.png', fullPage: true })
  }
})
