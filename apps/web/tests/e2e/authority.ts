import { expect, type APIRequestContext, type Page } from '@playwright/test'

/* Set what an assistant may do, before the browser opens.
 *
 * This replaces `mode.ts`, and the replacement is the point rather than a
 * rename. The specs used to call `enterMode(request, 'ai')` because the
 * application opened on a chooser and every route lived inside a mode — a view
 * that was not in the open mode's rail was simply absent, so a test had to pick
 * a mode before it could navigate anywhere.
 *
 * There is no chooser now and no mode to enter: every destination is in the rail
 * at all times, and what the mode also decided — how much an assistant may do
 * unsupervised — is a separate setting on the Permissions screen. So the specs
 * navigate without preamble, and the ones that are actually about authority say
 * so by calling this.
 *
 * It is server state rather than a browser preference because it is a
 * permission: it has to survive a reload, and a permission that a page refresh
 * could widen would not be one.
 */

export const API = process.env.ALGOFORGE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

export type Authority = {
  /** May an assistant start and stop campaigns and the engine? */
  unattended_work: boolean
  /** May it also submit orders? Meaningless without `unattended_work`. */
  unattended_execution: boolean
}

/** What the application ships with: work unattended, never execution. */
export const DEFAULT_AUTHORITY: Authority = {
  unattended_work: true,
  unattended_execution: false,
}

export async function readAuthority(request: APIRequestContext): Promise<Authority> {
  const response = await request.get(`${API}/authority`)
  if (!response.ok()) {
    throw new Error(`could not read authority: ${response.status()} ${await response.text()}`)
  }
  // `/authority` answers with the *current* profile plus the others that are
  // available, so the two flags are on `.profile` rather than at the top level.
  const payload = (await response.json()).data
  const profile = payload.profile ?? payload
  return {
    unattended_work: Boolean(profile.unattended_work),
    unattended_execution: Boolean(profile.unattended_execution),
  }
}

export async function setAuthority(
  request: APIRequestContext,
  authority: Authority,
): Promise<void> {
  const response = await request.post(`${API}/authority`, { data: authority })
  if (!response.ok()) {
    throw new Error(
      `could not set authority to ${JSON.stringify(authority)}: ` +
        `${response.status()} ${await response.text()}`,
    )
  }
}

/** Put authority back where the application ships it.
 *
 * Called from `afterEach` in the specs that change it. These tests share one
 * stateful backend, so a spec that widened authority and did not put it back
 * would silently change what every later spec is allowed to do.
 */
export async function resetAuthority(request: APIRequestContext): Promise<void> {
  await setAuthority(request, DEFAULT_AUTHORITY)
}

/** Open one pane of Settings → Diagnostics.
 *
 * Actions and Agents used to be rail destinations with their own `#actions`
 * and `#agents` routes. They are readings about what the machine did rather
 * than places to work, so they are panes of Diagnostics now; the specs that
 * navigate to them go through here so there is one place to change if they
 * move again.
 */
export async function openDiagnostics(page: Page, pane: string): Promise<void> {
  await page.goto('/#settings?tab=diagnostics')
  await expect(page.getByText('PAPER ONLY').last()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('tab', { name: pane, exact: true }).click()
}
