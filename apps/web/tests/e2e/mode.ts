import type { APIRequestContext } from '@playwright/test'

/* Open a mode before the browser does.
 *
 * The application opens on the four-mode chooser, and every route in these
 * specs lives inside a mode. Clicking through the chooser in each test would
 * make every spec depend on the chooser working, which is a different test —
 * and one `mode.spec.ts` owns. So the mode is set through the API, the same way
 * the workspace fixtures are cleared, and the page then loads straight into it.
 *
 * This is server state rather than a browser preference on purpose: each mode
 * remembers its own layout, so which one is open has to survive a reload.
 */

export const API = process.env.ALGOFORGE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

export type Mode = 'normal' | 'prop_firm' | 'ai' | 'hedge_fund'

export async function enterMode(
  request: APIRequestContext,
  mode: Mode,
  stance?: 'human_in_the_loop' | 'autonomous',
): Promise<void> {
  const response = await request.post(`${API}/modes/${mode}/enter`, {
    data: { stance: stance ?? null },
  })
  if (!response.ok()) {
    throw new Error(
      `could not enter ${mode}: ${response.status()} ${await response.text()}`,
    )
  }
}

export async function leaveMode(request: APIRequestContext): Promise<void> {
  await request.post(`${API}/modes/leave`)
}
