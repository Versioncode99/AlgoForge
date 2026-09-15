import { expect, test } from '@playwright/test'
import {
  API,
  DEFAULT_AUTHORITY,
  openDiagnostics,
  readAuthority,
  resetAuthority,
  setAuthority,
} from './authority'

/* What replaced the mode chooser, and the two things it must not have changed.
 *
 * This file used to be `mode.spec.ts`, and its first four tests asserted the
 * chooser: that the application opened on it, that entering a mode seeded a
 * workspace, that each mode kept its own layout, and that you could get back to
 * it. None of that exists. The chooser was doing two jobs at once — deciding
 * which screens existed, and deciding what an assistant was allowed to do — and
 * only the second was load-bearing.
 *
 * So the assertions here are the ones that survived the change, plus the two
 * that the change itself has to be held to:
 *
 * **Navigation is no longer a permission.** The application opens straight into
 * the product, and no screen is hidden behind a setting. `workstation.spec.ts`
 * owns the manifest sweep; what is checked here is that there is no chooser and
 * no way back to one.
 *
 * **Permission is still a permission.** Removing the visible "AI mode" must not
 * widen what an assistant may do. The default is unattended work without
 * unattended execution — exactly what the old default mode carried — and the
 * control that widens it is protected against the assistant itself.
 */

test.afterEach(async ({ request }) => {
  await resetAuthority(request)
})

test('the application opens in the product rather than on a chooser', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: /choose your workspace/i })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Open (Normal|AI|Prop Firm)$/ })).toHaveCount(0)
  // The boundary is still stated, and now on every screen rather than once on
  // the way in. It is the one claim the application makes everywhere.
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible()
})

test('nothing offers a way back to the chooser', async ({ page }) => {
  /* The switcher carried a "change operating mode" control. A control that
   * opens a screen which no longer exists is worse than no control. */
  await page.goto('/')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('button', { name: /change operating mode/i })).toHaveCount(0)
  await expect(page.locator('.mode-stance-tag')).toHaveCount(0)
})

test('the default authority is unattended work and not unattended execution', async ({
  request,
}) => {
  /* The old default was Normal mode: an assistant could run research and could
   * not reach the book. Removing the chooser must land on the same answer, and
   * this is where a silent widening would show up. */
  await resetAuthority(request)
  expect(await readAuthority(request)).toEqual(DEFAULT_AUTHORITY)
})

test('authority survives a reload, because a permission has to', async ({ page, request }) => {
  await setAuthority(request, { unattended_work: false, unattended_execution: false })
  await page.goto('/#settings')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  await page.reload()
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  expect(await readAuthority(request)).toEqual({
    unattended_work: false,
    unattended_execution: false,
  })
})

test('unattended execution cannot be granted without unattended work', async ({ request }) => {
  /* "May submit orders" without "may act unattended" is not a coherent state,
   * and the interface offers it as a dependent switch. The API is asked
   * directly here: an interface constraint that the API does not also hold is
   * not a constraint. */
  const response = await request.post(`${API}/authority`, {
    data: { unattended_work: false, unattended_execution: true },
  })
  if (response.ok()) {
    // Permitted only if it is normalised away rather than honoured.
    expect(await readAuthority(request)).toEqual({
      unattended_work: false,
      unattended_execution: false,
    })
  } else {
    expect(response.status()).toBeGreaterThanOrEqual(400)
  }
})

test('the permission policy is shown per action, and denies protected controls', async ({
  page,
}) => {
  await openDiagnostics(page, 'Actions')
  const row = page.locator('.measure-row', { hasText: 'set_fund_config' }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  // Scoped to the pills. Both words also appear inside the refusal sentence in
  // the detail cell, which is the sentence working rather than a second badge.
  await expect(row.locator('.measure-pill', { hasText: 'DENIED' })).toBeVisible()
  await expect(row.locator('.measure-pill', { hasText: 'PROTECTED' })).toBeVisible()
})

test('changing authority is itself refused to an assistant', async ({ page }) => {
  /* The one control an assistant must never operate is the one that says what
   * an assistant may do. It is protected in every configuration, so widening
   * authority always takes a person. */
  await openDiagnostics(page, 'Actions')
  const row = page.locator('.measure-row', { hasText: 'set_authority' }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(row.locator('.measure-pill', { hasText: 'PROTECTED' })).toBeVisible()
})

test('the book command centre reports the loop rather than a diagram of it', async ({ page }) => {
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
