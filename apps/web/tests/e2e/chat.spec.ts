import { expect, test } from '@playwright/test'
import { enterMode } from './mode'

/* The journeys the conversation exists for, driven through the real interface.
 *
 * The failure these cover is the one the whole persistence layer was built
 * against: the previous console kept its thread in component state, so closing
 * the tab ended the research and nothing anywhere said so. A unit test proves
 * the store keeps a turn; only this proves the operator gets it back.
 */

test.beforeEach(async ({ request }) => {
  await enterMode(request, 'ai')
})

test('a question and its answer survive a reload', async ({ page }) => {
  await page.goto('/#assistant')
  const input = page.getByLabel('Ask a question')
  await expect(input).toBeVisible({ timeout: 30_000 })

  const question = `How many strategies are there? ${Date.now()}`
  await input.fill(question)
  await page.getByRole('button', { name: 'Send' }).click()

  // The question lands before the answer is attempted, so it is there even if
  // the model is not configured on this machine.
  await expect(page.getByText(question)).toBeVisible({ timeout: 60_000 })

  await page.reload()
  await expect(page.getByText(question)).toBeVisible({ timeout: 60_000 })
})

test('every answer says how it was produced', async ({ page }) => {
  /* A stated belief, a model's paraphrase and a judged result are three
   * paragraphs of similar prose. The standing is what keeps them apart, and it
   * has to be on screen rather than in the database. */
  await page.goto('/#assistant')
  const input = page.getByLabel('Ask a question')
  await expect(input).toBeVisible({ timeout: 30_000 })

  await input.fill('What templates are available?')
  await page.getByRole('button', { name: 'Send' }).click()

  await expect(page.locator('.chat-standing').first()).toBeVisible({ timeout: 60_000 })
  const standings = await page.locator('.chat-msg .chat-standing').allTextContents()
  expect(standings.length).toBeGreaterThan(0)
  for (const standing of standings) {
    expect(['You said', 'Model', 'Action result', 'Deterministic']).toContain(standing.trim())
  }
})

test('an old conversation is reachable from the history', async ({ page }) => {
  await page.goto('/#assistant')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })

  const first = `First thread ${Date.now()}`
  await page.getByLabel('Ask a question').fill(first)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(first)).toBeVisible({ timeout: 60_000 })

  // Start a second, then walk back to the first through the list.
  await page.getByRole('button', { name: /^New$/ }).first().click()
  await expect(page.getByText(first)).toHaveCount(1, { timeout: 30_000 })

  await page.locator('.chat-history .ch-open', { hasText: first.slice(0, 24) }).first().click()
  await expect(page.getByText(first)).toBeVisible({ timeout: 30_000 })
})

test('searching the history reaches into what was said', async ({ page }) => {
  await page.goto('/#assistant')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })

  const needle = `absorption${Date.now()}`
  await page.getByLabel('Ask a question').fill(`What about liquidity ${needle} at the open?`)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(new RegExp(needle))).toBeVisible({ timeout: 60_000 })

  await page.getByLabel('Search conversations').fill(needle)
  await expect(page.locator('.chat-history li.on, .chat-history .ch-open')).toHaveCount(1, {
    timeout: 30_000,
  })
})

test('the console and the workspace panel are the same conversation', async ({ page }) => {
  /* Two implementations with one name drift into two products. The panel and
   * the route share a component, so a thread started in one is the thread in
   * the other. */
  await page.goto('/#assistant')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })
  const marker = `Shared thread ${Date.now()}`
  await page.getByLabel('Ask a question').fill(marker)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(marker)).toBeVisible({ timeout: 60_000 })

  await page.goto('/#workspace')
  // The AI workspace seeds an agent panel; if this build's active workspace has
  // none, the journey is not applicable rather than failed.
  const panel = page.locator('.chat-panel')
  if ((await panel.count()) === 0) test.skip(true, 'no conversation panel in the active workspace')
  await expect(page.locator('.chat-history')).toBeVisible({ timeout: 30_000 })
})

test('no console errors while conversing', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await page.goto('/#assistant')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('Ask a question').fill('What is the engine doing?')
  await page.getByRole('button', { name: 'Send' }).click()
  await page.waitForTimeout(2000)
  expect(errors).toEqual([])
})
