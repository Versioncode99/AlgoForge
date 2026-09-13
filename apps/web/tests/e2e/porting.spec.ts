import { expect, test } from '@playwright/test'
import { enterMode } from './mode'

/* Carrying a strategy to another platform, through the interface.
 *
 * What is asserted is the claim rather than the code: a surface that leads with
 * Generate and says nothing else is how somebody pastes a file that compiles
 * and trades a strategy that is not the one they validated.
 */

test.beforeEach(async ({ request }) => {
  await enterMode(request, 'normal')
})

test('the port surface leads with what did not cross', async ({ page, request }) => {
  const created = await request.post('/api/v1/strategies', {
    data: { template: 'momentum_breakout' },
  })
  test.skip(!created.ok(), 'this build could not create a strategy to port')
  const strategyId = (await created.json()).data.strategy_id

  await page.goto('/#strategies')
  await expect(page.getByRole('button', { name: 'Port' })).toBeVisible({ timeout: 30_000 })

  const report = await request.get(`/api/v1/strategies/${strategyId}/port/pine`)
  test.skip(report.status() === 404, 'this strategy is hand-written and has no canonical IR')
  const payload = (await report.json()).data

  // Whatever the status, it is never a claim that the logic survived unless
  // something ran both sides.
  expect(['structural', 'approximate', 'incomplete']).toContain(payload.status)
  expect(JSON.stringify(payload).toLowerCase()).not.toContain('logic preserved')
  // And every element that did not cross cleanly names its reason.
  for (const element of payload.elements) {
    if (element.fidelity !== 'equivalent') expect(element.detail.trim().length).toBeGreaterThan(0)
  }
})

test('the target catalogue says which targets are only analysed', async ({ request }) => {
  const response = await request.get('/api/v1/strategies/port-targets')
  expect(response.ok()).toBeTruthy()
  const targets = (await response.json()).data.targets
  const byKey = Object.fromEntries(targets.map((t: { key: string }) => [t.key, t]))
  expect(byKey.python.ceiling).toBe('verified')
  // A target this machine cannot execute can never be verified, and the
  // catalogue says so before anybody ports anything.
  for (const key of ['pine', 'ninjascript', 'mql5']) {
    expect(byKey[key].ceiling).not.toBe('verified')
  }
  expect(byKey.ninjascript.generates).toBe(false)
})
