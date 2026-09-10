import { expect, test } from '@playwright/test'
import { enterMode } from './mode'

// Horizontal overflow and console errors across the desktop widths the brief
// names. Density is the goal, so this checks the page body never scrolls
// sideways rather than that everything fits without scrolling anywhere -- a
// wide table scrolling inside its own container is correct.
const WIDTHS = [1280, 1440, 1920, 2560]
const VIEWS = [
  ['Strategies', 'Strategies'],
  ['Experiments', 'Experiments'],
  ['Evidence', 'Evidence'],
  ['Actions', 'Actions'],
  ['Activity', 'Activity'],
  ['Agents', 'Agents'],
] as const

// AI mode carries every view below. A view that is not in the open mode's rail
// is skipped rather than failed, which is what the `count() === 0` guard in the
// loop is for.
test.beforeEach(async ({ request }) => {
  await enterMode(request, 'ai')
})

for (const width of WIDTHS) {
  test(`no horizontal overflow or console errors at ${width}`, async ({ page }) => {
    test.setTimeout(120_000)
    const errors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })
    page.on('pageerror', (error) => errors.push(String(error)))

    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')

    for (const [link] of VIEWS) {
      const target = page.getByRole('link', { name: link, exact: true })
      if (await target.count() === 0) continue
      await target.click()
      await page.waitForTimeout(900)

      const overflow = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }))
      expect(
        overflow.scroll,
        `${link} overflows horizontally at ${width} (${overflow.scroll} > ${overflow.client})`,
      ).toBeLessThanOrEqual(overflow.client + 1)
    }

    // Favicon 404s and the like are noise; anything thrown is not.
    const real = errors.filter((text) => !/favicon|manifest|net::ERR_/i.test(text))
    expect(real, `console errors at ${width}:\n${real.join('\n')}`).toEqual([])
  })
}

// Density is the setting that can actually cause overflow: it moves every
// spacing step and the row height. A theme cannot — the token tests forbid a
// theme block from touching a layout token — so this runs the loose end of the
// density scale at the same four widths rather than every theme at all of them.
for (const width of WIDTHS) {
  test(`comfortable density does not overflow at ${width}`, async ({ page }) => {
    test.setTimeout(120_000)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    // Applied directly rather than through Settings: this test is about layout
    // at a density, not about the control that chooses one.
    await page.evaluate(() => {
      document.documentElement.dataset.density = 'comfortable'
    })

    for (const [link] of VIEWS) {
      const target = page.getByRole('link', { name: link, exact: true })
      if ((await target.count()) === 0) continue
      await target.click()
      await page.waitForTimeout(700)
      const overflow = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }))
      expect(
        overflow.scroll,
        `${link} overflows at ${width} in comfortable density`,
      ).toBeLessThanOrEqual(overflow.client + 1)
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
