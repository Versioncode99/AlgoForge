/* Contracts the shell has to keep, stated as assertions rather than as care.
 *
 * Both of these describe defects that were live and invisible: neither showed
 * up as a broken-looking screen, which is why neither was found by looking. */

import { describe, expect, it } from 'vitest'
import { MAIN_LANDMARK_ID, ROUTES } from './App'

describe('the shell', () => {
  it('does not let the skip link collide with a route', () => {
    /* The main landmark's id is also the skip link's target fragment. When it
     * was "workspace" it was *also* a destination in the rail, so activating
     * the skip link set the hash, the hash listener read it as a route, and
     * the one control built for keyboard and screen-reader users navigated
     * them to the Workspace screen instead of moving focus to the content.
     *
     * Nothing looks wrong when this regresses. Only this assertion catches it. */
    const ids = ROUTES.map(route => route.id)
    expect(ids).not.toContain(MAIN_LANDMARK_ID)
  })

  it('gives every destination a distinct id and a label', () => {
    // Two routes sharing an id makes the second unreachable by hash, and
    // `aria-current` lands on both at once.
    const ids = ROUTES.map(route => route.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const route of ROUTES) {
      expect(route.label.trim().length).toBeGreaterThan(0)
      expect(route.detail.trim().length).toBeGreaterThan(0)
    }
  })
})
