import { expect, test } from '@playwright/test'
import { API, enterMode, leaveMode } from './mode'

/* The four-mode system, driven the way a person drives it.
 *
 * These are the assertions that only hold end to end. The unit tests can prove
 * the policy refuses a call and that the chooser renders four panels; only a
 * real browser against a real API can prove that entering a mode seeds a
 * workspace, that leaving and returning finds the same one, and that switching
 * modes does not overwrite the layout of the mode being left.
 */

test.beforeEach(async ({ request }) => {
  await leaveMode(request)
})

test('the application opens on the chooser rather than inside a mode', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /choose your workspace/i })).toBeVisible({
    timeout: 30_000,
  })
  for (const name of ['Normal', 'Prop Firm', 'AI']) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  // The boundary is stated before anything is entered, not discovered later.
  await expect(page.getByText(/no broker, venue or order-routing vendor is connected/i)).toBeVisible()
})

test('entering a mode seeds its workspace and lands in its own navigation', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Open Normal' }).click()

  // The deterministic book loop is Normal's now. It came across from the Hedge
  // Fund mode intact rather than going with it.
  await expect(page.getByRole('link', { name: 'Book Overview', exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByRole('link', { name: 'Pre-Trade Gate', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Charts', exact: true })).toBeVisible()
  // Prop Firm's account sections must not be in Normal's rail.
  await expect(page.getByRole('link', { name: 'Profit Target', exact: true })).toHaveCount(0)
})

test('the AI stance is chosen before entry and shown in the chrome', async ({ page }) => {
  await page.goto('/')
  // Clicked through the label, which is what a person does: the input is
  // wrapped by one and the span inside it takes the pointer.
  await page.getByRole('radio', { name: /autonomous/i }).check({ force: true })
  await expect(page.getByRole('radio', { name: /autonomous/i })).toBeChecked()
  await page.getByRole('button', { name: 'Open AI' }).click()
  // Autonomous is the one state where the machine acts unasked, so it is named
  // where the operator can see it from every screen rather than only on the one
  // that set it.
  await expect(page.locator('.mode-stance-tag')).toContainText(/autonomous/i, { timeout: 30_000 })
})

test('each mode keeps its own layout across a switch', async ({ page, request }) => {
  // Enter Normal, note the workspace it seeded, then go through AI and
  // back. Returning must find the same layout: a mode that reopens on whatever
  // was globally active reads to the operator as their workspace having been
  // replaced.
  await enterMode(request, 'normal')
  const first = (await (await request.get(`${API}/modes/session`)).json()).data.session.workspace_id
  expect(first).toBeTruthy()

  await enterMode(request, 'ai', 'human_in_the_loop')
  const fund = (await (await request.get(`${API}/modes/session`)).json()).data.session.workspace_id
  expect(fund).toBeTruthy()
  expect(fund).not.toBe(first)

  await enterMode(request, 'normal')
  const again = (await (await request.get(`${API}/modes/session`)).json()).data.session.workspace_id
  expect(again).toBe(first)

  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Workspace', exact: true })).toBeVisible({
    timeout: 30_000,
  })
})

test('switching back to the chooser forgets nothing', async ({ page, request }) => {
  await enterMode(request, 'prop_firm')
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Account Status', exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await page.getByRole('button', { name: /switch/i }).click()
  // Switch opens the workspace switcher. Leaving the mode is a control inside
  // it, deliberately: changing mode changes what an assistant may do on your
  // behalf, which is a permissions decision rather than a navigation one.
  await page.getByRole('button', { name: /change operating mode/i }).click()
  await expect(page.getByRole('heading', { name: /choose your workspace/i })).toBeVisible({
    timeout: 30_000,
  })

  await page.getByRole('button', { name: 'Open Prop Firm' }).click()
  await expect(page.getByRole('link', { name: 'Account Status', exact: true })).toBeVisible()
})

test('the permission policy is shown per action, and denies protected controls', async ({
  page,
  request,
}) => {
  await enterMode(request, 'ai')
  await page.goto('/#actions')
  const row = page.locator('.measure-row', { hasText: 'set_fund_config' }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  // Scoped to the pills. Both words also appear inside the refusal sentence in
  // the detail cell, which is the sentence working rather than a second badge.
  await expect(row.locator('.measure-pill', { hasText: 'DENIED' })).toBeVisible()
  await expect(row.locator('.measure-pill', { hasText: 'PROTECTED' })).toBeVisible()
})

test('the book command centre reports the loop rather than a diagram of it', async ({
  page,
  request,
}) => {
  await enterMode(request, 'normal')
  await page.goto('/#book')
  await expect(page.getByText('NAV', { exact: true }).first()).toBeVisible({ timeout: 30_000 })
  // Every stage carries a state. A board of eleven labels with no state on them
  // would be a diagram, which is the thing this screen must not be.
  const stages = page.locator('.loop-track > li')
  expect(await stages.count()).toBeGreaterThan(8)
  await expect(stages.first().locator('.measure-pill')).toBeVisible()
})

test('a fresh fund refuses to construct rather than sizing against nothing', async ({
  page,
  request,
}) => {
  await enterMode(request, 'normal')
  const config = (await (await request.get(`${API}/fund/config`)).json()).data.config
  test.skip(
    (config.universe ?? []).length > 0,
    'this installation already has a fund universe configured',
  )

  await page.goto('/#portfolio')
  await page.getByRole('button', { name: /construct portfolio/i }).click()
  // The honest refusal, not an empty portfolio: nothing can be sized against a
  // universe of nothing, and an empty holdings table would read as a decision.
  // The refusal itself, not the activity line that also quotes it.
  await expect(page.getByRole('alert').filter({ hasText: /no universe/i }).first()).toBeVisible({
    timeout: 30_000,
  })
})
