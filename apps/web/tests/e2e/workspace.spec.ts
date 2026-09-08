import { expect, test, type Page } from '@playwright/test'

/* The workstation, driven the way a person drives it.
 *
 * The property under test throughout is that the interface and the agent share
 * one implementation: everything here goes through the same HTTP actions the
 * action registry exposes, so a passing drag proves the agent's `move_panel`
 * works too. */

const API = process.env.ALGOFORGE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

/** Clear every saved layout through the API.
 *
 * Driving the UI to do this would make each test depend on the delete button
 * working, which is a different test. Deleting workspaces touches no research:
 * that is the whole point of keeping layouts in their own store. */
test.beforeEach(async ({ request }) => {
  const listed = await request.get(`${API}/workspaces`)
  const body = await listed.json()
  for (const item of body?.data?.workspaces ?? []) {
    await request.delete(`${API}/workspaces/${item.workspace_id}`)
  }
})

async function freshWorkspace(page: Page, template: string) {
  await page.goto('/#workspace')
  await expect(page.locator('.template-grid')).toBeVisible({ timeout: 30_000 })
  await page.locator('.template-grid button', { hasText: template }).click()
  // The bar is present for every workspace; the grid is not -- an empty one
  // renders its own state rather than an empty grid.
  await expect(page.locator('.workspace-bar')).toBeVisible({ timeout: 30_000 })
}

test('a template opens a real layout and nothing is locked to it', async ({ page }) => {
  await freshWorkspace(page, 'Quant Researcher')

  const panels = page.locator('.wpanel')
  await expect(panels).toHaveCount(4)

  // A panel from a different discipline, on a research desk. Templates are
  // starting points, not modes.
  await page.getByRole('button', { name: 'Add panel' }).click()
  await page.locator('.panel-picker button', { hasText: 'dom' }).first().click()
  await expect(panels).toHaveCount(5)
})

test('a chart panel draws the symbol it was asked for', async ({ page }) => {
  await freshWorkspace(page, 'Systematic Trader')

  // The template asks for NQ and ES. Falling through to whichever archive
  // happened to be first would draw the same instrument twice.
  const selects = page.getByLabel('Chart data set')
  await expect(selects).toHaveCount(2)
  const first = await selects.nth(0).inputValue()
  const second = await selects.nth(1).inputValue()
  expect(first).not.toEqual(second)
})

test('a panel can be dragged to a new position and it stays there', async ({ page }) => {
  await freshWorkspace(page, 'Quant Researcher')

  const panel = page.locator('.wpanel').first()
  const before = await panel.boundingBox()
  expect(before).not.toBeNull()

  const header = panel.locator('.wpanel-head')
  const grip = await header.boundingBox()
  expect(grip).not.toBeNull()

  await page.mouse.move(grip!.x + grip!.width / 2, grip!.y + grip!.height / 2)
  await page.mouse.down()
  await page.mouse.move(grip!.x + grip!.width / 2, grip!.y + 260, { steps: 12 })
  await page.mouse.up()

  // The move is committed to the server, so a reload is the real assertion:
  // local-only layout state would pass without it.
  await page.reload()
  await expect(page.locator('.workspace-grid')).toBeVisible({ timeout: 30_000 })
  const after = await page.locator('.wpanel').first().boundingBox()
  expect(after!.y).toBeGreaterThan(before!.y)
})

test('a panel can be resized and the new size survives a reload', async ({ page }) => {
  await freshWorkspace(page, 'Quant Researcher')

  const panel = page.locator('.wpanel').first()
  const before = await panel.boundingBox()
  const handle = panel.locator('.wpanel-resize')
  const grip = await handle.boundingBox()

  await page.mouse.move(grip!.x + 5, grip!.y + 5)
  await page.mouse.down()
  await page.mouse.move(grip!.x + 5, grip!.y + 200, { steps: 12 })
  await page.mouse.up()

  await page.reload()
  await expect(page.locator('.workspace-grid')).toBeVisible({ timeout: 30_000 })
  const after = await page.locator('.wpanel').first().boundingBox()
  expect(after!.height).toBeGreaterThan(before!.height)
})

test('a panel setting persists through the server', async ({ page }) => {
  await freshWorkspace(page, 'Prop Trader')

  const watchlist = page.getByLabel('Watchlist symbols')
  await watchlist.fill('NQ, ES, GC')
  await watchlist.blur()

  await expect(page.locator('.panel-symbols li')).toHaveCount(3)
  // No price is shown, because there is no quote stream in this build and a
  // number here would be invented.
  await expect(page.locator('.panel-symbols li').first()).toContainText('no live quote')

  await page.reload()
  await expect(page.locator('.panel-symbols li')).toHaveCount(3, { timeout: 30_000 })
})

test('unbuilt panels say they are unbuilt', async ({ page }) => {
  await freshWorkspace(page, 'Blank')

  await page.getByRole('button', { name: 'Add panel' }).click()
  await page.locator('.panel-picker button', { hasText: 'dom' }).first().click()

  // The single most dangerous thing this application could draw is plausible
  // depth on a panel wired to nothing.
  const scaffold = page.locator('.panel-state.scaffold')
  await expect(scaffold).toBeVisible()
  await expect(scaffold).toContainText(/not built yet/i)
  await expect(scaffold).toContainText(/paper-only|connector/i)
})

test('a panel can be removed', async ({ page }) => {
  await freshWorkspace(page, 'Quant Researcher')
  const panels = page.locator('.wpanel')
  await expect(panels).toHaveCount(4)
  await panels.first().getByRole('button', { name: /^Remove / }).click()
  await expect(panels).toHaveCount(3)
})
