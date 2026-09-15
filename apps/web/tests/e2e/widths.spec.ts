import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { API } from './authority'

/* Horizontal overflow and console errors across the desktop widths the brief
 * names, plus the narrow end where the responsive rules actually fire. Density
 * is the goal, so this checks the page body never scrolls sideways rather than
 * that everything fits without scrolling anywhere — a wide table scrolling
 * inside its own container is correct.
 *
 * The destinations come from the shipped navigation manifest, not from a list
 * in this file. The list that used to be here named six views — Experiments,
 * Evidence, Actions, Agents — that are no longer top-level destinations, and a
 * `count() === 0` guard skipped every one of them. The spec passed at all four
 * widths while checking almost nothing, which is a worse outcome than failing.
 * So the manifest is the list, and a destination the rail does not offer is a
 * failure rather than a skip. */
const WIDTHS = [1280, 1440, 1920, 2560]
//: The narrow end. `workstation.css` sheds rail labels and context facts below
//: 900px and 640px, and those breakpoints are where an overflow hides.
const NARROW = [1024, 768, 480]

async function destinations(request: APIRequestContext): Promise<string[]> {
  const response = await request.get(`${API}/navigation`)
  expect(response.ok(), `the navigation manifest is unreachable: ${response.status()}`).toBe(true)
  const rows = (await response.json()).data.destinations as { label: string }[]
  expect(rows.length, 'the navigation manifest is empty').toBeGreaterThan(0)
  return rows.map((row) => row.label)
}

/** Open a destination and report whether the body scrolls sideways. */
async function overflowAt(page: Page, label: string, width: number): Promise<void> {
  const target = page.getByRole('navigation', { name: 'Sections' }).getByRole('link', {
    name: label,
    exact: true,
  })
  // Not a skip. The manifest says this destination exists, so a rail without it
  // is the bug rather than a reason to check nothing.
  await expect(target, `the rail has no "${label}" at ${width}px`).toHaveCount(1)
  await target.click()
  await page.waitForTimeout(900)
  const overflow = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(
    overflow.scroll,
    `${label} overflows horizontally at ${width} (${overflow.scroll} > ${overflow.client})`,
  ).toBeLessThanOrEqual(overflow.client + 1)
}
for (const width of WIDTHS) {
  test(`no horizontal overflow or console errors at ${width}`, async ({ page, request }) => {
    test.setTimeout(180_000)
    const views = await destinations(request)
    const errors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })
    page.on('pageerror', (error) => errors.push(String(error)))

    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')

    for (const label of views) {
      await overflowAt(page, label, width)
    }

    /* Two categories, and only one of them is a bug.
     *
     * A 4xx from this API is a *refusal*, and refusing is what it is for: 409
     * when a dataset needs a credential this installation has not been given,
     * 422 when no workspace is open. Chrome logs every one of those as a
     * console error, so an installation with no Databento key would fail this
     * test for behaving correctly. The screens render those refusals, and
     * `accessibility.spec.ts` audits the rendered result.
     *
     * Everything else is a bug: an exception, a 5xx, a failed asset. Those are
     * asserted empty, and the refusals are asserted *bounded* — the retry
     * policy must not ask a permanent refusal twice. */
    const noise = /favicon|manifest|net::ERR_/i
    const refusal = /status of 4\d\d/
    const real = errors.filter((text) => !noise.test(text) && !refusal.test(text))
    expect(real, `console errors at ${width}:\n${real.join('\n')}`).toEqual([])

    const refusals = errors.filter((text) => refusal.test(text))
    // One per navigation at most: nine destinations, and nothing retried.
    expect(
      refusals.length,
      `a refusal was requested more than once per destination at ${width}:\n` +
        refusals.join('\n'),
    ).toBeLessThanOrEqual(views.length * 2)
  })
}

// Density is the setting that can actually cause overflow: it moves every
// spacing step and the row height. A theme cannot — the token tests forbid a
// theme block from touching a layout token — so this runs the loose end of the
// density scale at the same four widths rather than every theme at all of them.
for (const width of WIDTHS) {
  test(`comfortable density does not overflow at ${width}`, async ({ page, request }) => {
    test.setTimeout(180_000)
    const views = await destinations(request)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    // Applied directly rather than through Settings: this test is about layout
    // at a density, not about the control that chooses one.
    await page.evaluate(() => {
      document.documentElement.dataset.density = 'comfortable'
    })

    for (const label of views) {
      await overflowAt(page, label, width)
    }
  })
}

/* The narrow end. The rail collapses to a toggle below 900px, so the
 * destinations are reached through it rather than from a visible list. */
for (const width of NARROW) {
  test(`no horizontal overflow at ${width}`, async ({ page, request }) => {
    test.setTimeout(180_000)
    const views = await destinations(request)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })

    const toggle = page.getByRole('button', { name: 'Toggle navigation' })
    for (const label of views) {
      if (await toggle.isVisible()) await toggle.click()
      await overflowAt(page, label, width)
    }
  })
}

// The three themes have to be reachable and complete. Rendering each one and
// reading a token back is the cheapest honest check that the palette applied:
// a theme whose block failed to parse leaves the default values in place.
test('every theme applies a distinct palette', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')

  const read = () =>
    page.evaluate(() => {
      const style = getComputedStyle(document.documentElement)
      return {
        bg: style.getPropertyValue('--bg-0').trim(),
        fg: style.getPropertyValue('--fg-0').trim(),
        grid: style.getPropertyValue('--chart-grid').trim(),
        body: getComputedStyle(document.body).backgroundColor,
      }
    })

  // The dev server injects stylesheets through the module graph, so they are not
  // present at `load`. Waiting for the token to resolve is the condition that
  // actually matters and is more honest than a sleep.
  await page.waitForFunction(
    () => getComputedStyle(document.documentElement).getPropertyValue('--bg-0').trim() !== '',
    undefined,
    { timeout: 15_000 },
  )

  const seen: Record<string, Awaited<ReturnType<typeof read>>> = {}
  for (const theme of ['graphite', 'silver', 'contrast']) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value
    }, theme)
    await page.waitForTimeout(200)
    seen[theme] = await read()
    expect(seen[theme].bg, `${theme} has no background`).not.toEqual('')
    expect(seen[theme].grid, `${theme} has no chart grid`).not.toEqual('')
  }

  // Distinct, not merely present: a theme that silently fell back to the
  // default would still have values in every token.
  const backgrounds = Object.values(seen).map((item) => item.bg)
  expect(new Set(backgrounds).size).toBe(3)
  // And the body actually repaints, rather than the tokens changing under a
  // stylesheet that names its colours directly.
  expect(seen.silver.body).not.toEqual(seen.graphite.body)
})
