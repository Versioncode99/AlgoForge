import { expect, test } from '@playwright/test'

// Horizontal overflow and console errors across the desktop widths the brief
// names. Density is the goal, so this checks the page body never scrolls
// sideways rather than that everything fits without scrolling anywhere -- a
// wide table scrolling inside its own container is correct.
const WIDTHS = [1280, 1440, 1920, 2560]
const VIEWS = [
  ['Overview', 'Overview'],
  ['Strategies', 'Strategies'],
  ['Experiments', 'Experiments'],
  ['Evidence', 'Evidence'],
  ['Data Health', 'Data Health'],
  ['Agent Command', 'Agent Command'],
] as const

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
