import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { API } from './authority'

/* An accessibility audit against the rendered application, not against a claim.
 *
 * Every destination in the shipped navigation manifest is loaded in a real
 * browser and run through axe-core at WCAG 2.1 A and AA. The manifest is read
 * from the API rather than listed here so that a screen added to the product is
 * audited the day it exists — a hand-maintained list of routes is the thing that
 * makes an audit look complete while a new screen goes unchecked.
 *
 * **Violations fail the test and are not filtered.** There is no allow-list in
 * this file, and adding one would make the audit report whatever it was
 * configured to report. If a violation is genuinely not fixable, the honest
 * options are to fix the markup or to say in the report that this screen fails
 * — not to exclude the rule.
 *
 * **What axe cannot check is checked separately below**: focus order through
 * the rail, a visible focus indicator, and the skip link. Those are keyboard
 * properties, and no static scan sees them.
 */

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']

type Destination = { route: string; label: string }

async function destinations(request: import('@playwright/test').APIRequestContext) {
  const response = await request.get(`${API}/navigation`)
  expect(response.ok(), `the navigation manifest is unreachable: ${response.status()}`).toBe(true)
  const rows = (await response.json()).data.destinations as Destination[]
  expect(rows.length, 'the navigation manifest is empty').toBeGreaterThan(0)
  return rows
}

/** Wait for the view, not just the shell.
 *
 * The chrome paints immediately and every view behind it is lazily loaded, so
 * waiting only for the paper-only label audits a `Suspense` fallback and proves
 * nothing about the screen.
 *
 * The fallback is matched by its element — `#main-content`'s own
 * `div.state[role="status"]` — rather than by its wording. Matching the text
 * "Opening …" found a strategy called *Opening Range Breakout* in the rendered
 * page and waited thirty seconds for real content to go away. */
async function settled(page: Page) {
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('#main-content > div.state[role="status"]')).toHaveCount(0, {
    timeout: 30_000,
  })
  await expect(page.locator('#main-content')).not.toBeEmpty()
  /* And wait for the entry animations. Rows and panels fade in from
   * `opacity: 0`, and axe computes contrast against the *composited* colour —
   * so auditing mid-fade reports every animating element as failing at ratios
   * like 1.19:1, which is the animation rather than the palette. Waiting on
   * `getAnimations()` audits the state a reader actually sees. */
  await page.waitForFunction(
    () => document.getAnimations().every((animation) => animation.playState !== 'running'),
    null,
    { timeout: 30_000 },
  )
}

function report(violations: Awaited<ReturnType<AxeBuilder['analyze']>>['violations']) {
  return violations
    .map((violation) => {
      const where = violation.nodes
        .slice(0, 4)
        .map((node) => `      ${node.target.join(' ')}`)
        .join('\n')
      return `  [${violation.impact ?? 'unknown'}] ${violation.id}: ${violation.help}\n${where}`
    })
    .join('\n')
}

test('every destination in the manifest passes axe at WCAG 2.1 AA', async ({ page, request }) => {
  test.setTimeout(300_000)
  const rows = await destinations(request)
  const failures: string[] = []

  for (const destination of rows) {
    await page.goto(`/#${destination.route}`)
    await settled(page)
    const results = await new AxeBuilder({ page }).withTags(TAGS).analyze()
    if (results.violations.length) {
      failures.push(`${destination.label} (#${destination.route}):\n${report(results.violations)}`)
    }
  }

  expect(failures.join('\n\n'), 'axe found accessibility violations').toBe('')
})

test('the skip link is the first thing a keyboard reaches, and it works', async ({ page }) => {
  /* A rail of nine destinations sits before the content. Without a skip link,
   * reaching the first control on a screen means tabbing past all of it, on
   * every navigation. */
  await page.goto('/')
  await settled(page)
  await page.keyboard.press('Tab')
  const first = page.locator(':focus')
  await expect(first).toHaveClass(/skip-link/)
  await expect(first).toBeVisible()
  await first.press('Enter')
  // The target exists and takes focus; a skip link that scrolls without moving
  // focus leaves the keyboard exactly where it was.
  await expect(page.locator('#main-content')).toHaveCount(1)
  await expect(page.locator('#main-content')).toBeFocused()
})

test('the skip link skips to the content without changing the screen', async ({ page }) => {
  /* The test above runs on Home, where the bug this one exists for is
   * invisible: activating the skip link navigated to Home, and on Home that
   * is indistinguishable from doing nothing.
   *
   * This application routes on `window.location.hash`, and the skip link's
   * `href` is a fragment on the same page. `locate` sends every hash it does
   * not recognise to Home, so the landmark id did not need to *collide* with a
   * route to break -- being unknown was enough. Renaming it from "workspace"
   * to "main-content" removed the collision and left the navigation, so the one
   * control built for keyboard and screen-reader users moved focus to the
   * content and then replaced that content with Home's.
   *
   * Asserted from a screen that is not Home, which is the only place it shows.
   */
  for (const route of ['#research?tab=validation', '#strategies?tab=runs']) {
    await page.goto(`/${route}`)
    await settled(page)
    const before = await page.evaluate(() => window.location.hash)
    expect(before, 'the test did not reach the screen it meant to').toBe(route)

    /* Focused directly rather than tabbed to. That it is the *first* stop is
     * the assertion above; this one is about what activating it does, and
     * driving it by keystroke made the test depend on where the harness left
     * focus after a deep link. `press('Enter')` on a focused link is the real
     * activation path either way. */
    const skip = page.locator('a.skip-link')
    await expect(skip).toHaveCount(1)
    await skip.focus()
    await skip.press('Enter')

    await expect(page.locator(`#${'main-content'}`)).toBeFocused()
    // The address bar too, not just the router: a reload has to come back here.
    await expect
      .poll(() => page.evaluate(() => window.location.hash), {
        message: 'the skip link navigated instead of skipping',
      })
      .toBe(before)
  }
})

test('focus is always visible, on every focusable control in the shell', async ({ page }) => {
  /* `outline: none` with no replacement is the single most common way a
   * keyboard user loses their place, and it is invisible to a mouse review. */
  await page.goto('/')
  await settled(page)
  const invisible = await page.evaluate(() => {
    const bad: string[] = []
    const focusable = document.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    for (const element of Array.from(focusable).slice(0, 60)) {
      element.focus()
      if (document.activeElement !== element) continue
      const style = getComputedStyle(element)
      const outline =
        style.outlineStyle !== 'none' && parseFloat(style.outlineWidth || '0') > 0
      const ring = style.boxShadow !== 'none' && style.boxShadow !== ''
      if (!outline && !ring) {
        bad.push(element.tagName.toLowerCase() + (element.className ? `.${element.className}` : ''))
      }
    }
    return bad
  })
  expect(invisible, 'these controls take focus with no visible indicator').toEqual([])
})

test('the rail is a landmark with an accessible name', async ({ page }) => {
  /* Nine destinations in a bare `<div>` are nine links in the middle of the
   * page to a screen reader. */
  await page.goto('/')
  await settled(page)
  await expect(page.getByRole('navigation').first()).toBeVisible()
  const named = await page
    .getByRole('navigation')
    .first()
    .evaluate((node) => node.getAttribute('aria-label') ?? node.getAttribute('aria-labelledby'))
  expect(named, 'the primary navigation has no accessible name').toBeTruthy()
})

test('the current destination is marked, not merely coloured', async ({ page, request }) => {
  /* Colour alone does not reach a screen reader, and `aria-current` is what
   * makes "you are here" a fact rather than a shade of blue. */
  const rows = await destinations(request)
  await page.goto('/')
  await settled(page)
  for (const destination of rows.slice(0, 4)) {
    // Scoped to the rail. A destination with tabs also has a tab of the same
    // name, and the two carry different `aria-current` values on purpose —
    // `page` for the destination, `true` for the view inside it.
    const rail = page.getByRole('navigation', { name: 'Sections' })
    await rail.getByRole('link', { name: destination.label, exact: true }).click()
    await expect(
      rail.getByRole('link', { name: destination.label, exact: true }),
    ).toHaveAttribute('aria-current', 'page')
  }

  // And exactly one element claims to be the current page at a time.
  expect(await page.locator('[aria-current="page"]').count()).toBe(1)
})
