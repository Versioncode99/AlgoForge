import { expect, test, type APIRequestContext } from '@playwright/test'
import { API } from './authority'

/* The chart, against whatever archives this machine actually holds.
 *
 * These assertions are about honesty as much as rendering. A chart is where
 * most people form their belief about a strategy, so the two things that must
 * never happen are drawing a series that is not real, and drawing a daily bar
 * without saying where its boundary falls.
 *
 * An installation with no archive loaded cannot draw anything, and that is not
 * a defect — it is the refusal the product is built to make. The drawing tests
 * skip there, naming the blocker, and `the refusal is drawn instead of a
 * substitute series` runs in exactly that case and asserts the refusal is what
 * appears. Between them there is no configuration where this file checks
 * nothing. */

/** Whether any dataset on this machine holds bars a chart could draw. */
async function archiveLoaded(request: APIRequestContext): Promise<boolean> {
  const response = await request.get(`${API}/datasets`)
  if (!response.ok()) return false
  const rows = (await response.json()).data as { loaded?: boolean; bar_count?: number }[]
  return rows.some((row) => row.loaded && (row.bar_count ?? 0) > 0)
}

const NO_ARCHIVE =
  'no dataset on this machine holds bars; the chart refuses rather than drawing ' +
  'a substitute, which `the refusal is drawn instead of a substitute series` covers'

test('charts draw real candles and say what they are', async ({ page, request }) => {
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
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

test('the daily timeframe states the session it bucketed on', async ({ page, request }) => {
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
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

test('dragging backwards loads history that was not on screen', async ({ page, request }) => {
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
  /* The defect this covers produced no error and no warning. One window of
   * bars was fetched, drawn, and that was the whole series: dragging left
   * revealed empty space where sixteen years of archive actually sit, and the
   * chart behaved like a photograph of a market.
   *
   * Asserted over the network rather than the canvas, because the canvas
   * cannot be read — and what matters is that the drag reached the archive. */
  const pages: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/bars?')) pages.push(request.url())
  })

  await page.goto('/#charts')
  await expect(page.locator('.chart-provenance')).toBeVisible({ timeout: 60_000 })

  const canvas = page.locator('.price-chart-canvas canvas').first()
  await expect(canvas).toBeVisible()
  const box = await canvas.boundingBox()
  // The canvas is visible above, so it has a box. A null one is a chart that
  // rendered at zero size, which is the failure, not a reason to stand down.
  expect(box).not.toBeNull()
  if (!box) return

  const opening = pages.length
  // Dragging right walks the viewport backwards through time.
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2)
  await page.mouse.down()
  for (let step = 1; step <= 12; step += 1) {
    await page.mouse.move(box.x + box.width * 0.3 + step * 60, box.y + box.height / 2)
  }
  await page.mouse.up()

  await expect
    .poll(() => pages.filter((url) => url.includes('before=')).length, { timeout: 30_000 })
    .toBeGreaterThan(0)

  // The pan is paged, not refetched: it asks for the bars it does not have
  // rather than the whole window again.
  expect(pages.length).toBeGreaterThan(opening)
  const paged = pages.filter((url) => url.includes('before='))
  expect(new Set(paged).size).toBe(paged.length)
})

test('no console errors while charting', async ({ page, request }) => {
  test.skip(!(await archiveLoaded(request)), NO_ARCHIVE)
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


test('the refusal is drawn instead of a substitute series', async ({ page, request }) => {
  /* The failure this guards against is the expensive one: a chart that quietly
   * draws *something* when the archive it was asked for is not there. A person
   * forms a belief from a picture, and a picture of the wrong series is worse
   * than no picture. */
  test.skip(
    await archiveLoaded(request),
    'this installation holds a loaded archive, so the chart draws rather than refuses',
  )

  await page.goto('/#markets?tab=charts')
  await expect(page.getByLabel('Chart data set')).toBeVisible({ timeout: 30_000 })
  const main = page.locator('#main-content')
  await expect(main.getByText(/Market data unavailable/i)).toBeVisible({ timeout: 30_000 })
  // The reason, not just the refusal.
  await expect(main.getByText(/is not set|unavailable/i).first()).toBeVisible()
  // And the promise, in words: nothing was drawn in its place.
  await expect(main.getByText(/No substitute series is drawn/i)).toBeVisible()
  await expect(page.locator('.chart-provenance')).toHaveCount(0)
})
