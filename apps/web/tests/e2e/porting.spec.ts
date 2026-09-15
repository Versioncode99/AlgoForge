import { expect, test } from '@playwright/test'
import { API } from './authority'

/* Carrying a strategy to another platform, through the interface.
 *
 * What is asserted is the claim rather than the code: a surface that leads with
 * Generate and says nothing else is how somebody pastes a file that compiles
 * and trades a strategy that is not the one they validated.
 */

test('the port surface leads with what did not cross', async ({ page, request }) => {
  /* From a blueprint rather than a template: porting renders the Strategy IR,
   * and `POST /strategies` writes hand-written Python, which has no canonical
   * definition to carry across. A template-built strategy answers 404 here by
   * design, so a test that used one could only ever skip -- which is what this
   * one did, and why it never once checked the payload below. */
  const created = await request.post(`${API}/strategies/from-blueprint`, {
    data: { blueprint: 'london_breakout' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const strategyId = (await created.json()).data.strategy_id

  // Strategies opens on the catalogue; the panes are one row-click away.
  await page.goto('/#strategies')
  const rows = page.locator('.catalogue-table tbody tr:not([aria-hidden="true"])')
  // One was just created, so the empty state is a catalogue that did not see
  // it rather than a vault that has nothing.
  await expect(rows.first()).toBeVisible({ timeout: 120_000 })
  await rows.first().click()
  await expect(page.getByRole('button', { name: 'Port', exact: true })).toBeVisible({
    timeout: 30_000,
  })

  const report = await request.get(`${API}/strategies/${strategyId}/port/pine`)
  // Built from a blueprint, so it has a canonical IR; a 404 here means the port
  // route lost sight of a strategy that has a definition.
  expect(report.status(), await report.text()).not.toBe(404)
  expect(report.ok(), await report.text()).toBeTruthy()
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
  const response = await request.get(`${API}/strategies/port-targets`)
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
