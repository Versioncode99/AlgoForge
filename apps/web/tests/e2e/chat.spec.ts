import { expect, test } from '@playwright/test'
import { API } from './authority'

/* The journeys the conversation exists for, driven through the real interface.
 *
 * The failure these cover is the one the whole persistence layer was built
 * against: the previous console kept its thread in component state, so closing
 * the tab ended the research and nothing anywhere said so. A unit test proves
 * the store keeps a turn; only this proves the operator gets it back.
 */

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

  /* Start from a known-empty thread. The panel opens on the most recent
   * conversation -- which is correct, and means a message sent on arrival
   * lands in whatever was last worked on rather than in a new thread. */
  await page.getByRole('button', { name: 'New chat', exact: true }).first().click()
  const first = `First thread ${Date.now()}`
  await page.getByLabel('Ask a question').fill(first)
  await page.getByRole('button', { name: 'Send' }).click()
  // The first message names an untitled thread, so it appears in the list.
  await expect(page.locator('.chat-history .ch-open strong', { hasText: first })).toBeVisible({
    timeout: 60_000,
  })

  // Start a second, then walk back to the first through the list.
  await page.getByRole('button', { name: 'New chat', exact: true }).first().click()
  await expect(page.locator('.chat-thread .chat-msg')).toHaveCount(0, { timeout: 30_000 })

  await page.locator('.chat-history .ch-open', { hasText: first }).first().click()
  await expect(page.locator('.chat-thread').getByText(first)).toBeVisible({ timeout: 30_000 })
})

test('searching the history reaches into what was said', async ({ page }) => {
  await page.goto('/#assistant')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: 'New chat', exact: true }).first().click()
  const needle = `absorption${Date.now()}`
  await page.getByLabel('Ask a question').fill(`What about liquidity ${needle} at the open?`)
  await page.getByRole('button', { name: 'Send' }).click()
  // Scoped to the thread: the same text is also the derived title in the
  // history beside it, and an unscoped match is two elements, not a failure.
  await expect(page.locator('.chat-thread').getByText(new RegExp(needle))).toBeVisible({
    timeout: 60_000,
  })

  await page.getByLabel('Search conversations').fill(needle)
  await expect(page.locator('.chat-history .ch-open')).toHaveCount(1, { timeout: 30_000 })
})

test('the destination and the workspace panel are the same conversation', async ({
  page,
  request,
}) => {
  /* Two implementations with one name drift into two products. The panel and
   * the destination share a component, so a thread started in one is the thread
   * in the other.
   *
   * The panel is added here rather than assumed. It used to arrive seeded: AI
   * mode's workspace template carried an agent panel, and the spec entered AI
   * mode first. Without modes there is no template doing that, so the test adds
   * the panel the way a person does — which is a better test, because it also
   * proves the panel can still be added. */
  await page.goto('/#chat')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })
  const marker = `Shared thread ${Date.now()}`
  await page.getByLabel('Ask a question').fill(marker)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(marker).first()).toBeVisible({ timeout: 60_000 })

  /* A workspace first. `add_panel` refuses when none is open, which is the
   * right refusal — it will not guess which workspace you meant — and this
   * installation may be starting from none. */
  const workspace = await request.post(`${API}/actions/create_workspace`, {
    data: { arguments: { name: `Chat panel ${Date.now()}`, activate: true } },
  })
  expect(workspace.ok(), `no workspace could be created: ${await workspace.text()}`).toBe(true)

  // The action envelope the interface uses: arguments are nested, not spread.
  const added = await request.post(`${API}/actions/add_panel`, {
    data: { arguments: { kind: 'agent' } },
  })
  expect(added.ok(), `the agent panel could not be added: ${await added.text()}`).toBe(true)

  await page.goto('/#home?tab=workspace')
  await expect(page.locator('.chat-panel').first()).toBeVisible({ timeout: 30_000 })
  // The same conversation, not merely another chat box.
  await expect(page.locator('.chat-panel').getByText(marker).first()).toBeVisible({
    timeout: 30_000,
  })
})

test('nothing throws while conversing', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(String(error)))
  await page.goto('/#chat')
  await expect(page.getByLabel('Ask a question')).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('Ask a question').fill('What is the engine doing?')
  await page.getByRole('button', { name: 'Send' }).click()
  await page.waitForTimeout(2000)
  /* A 4xx from this API is a refusal — 422 when no workspace is open — and
   * Chrome logs every one as a console error. The refusals are the API working;
   * an exception, a 5xx or a failed asset is not. `widths.spec.ts` makes the
   * same distinction and bounds the refusal count. */
  const real = errors.filter(
    (text) => !/favicon|manifest|net::ERR_|status of 4\d\d/i.test(text),
  )
  expect(real, `console errors while conversing:\n${real.join('\n')}`).toEqual([])
})
