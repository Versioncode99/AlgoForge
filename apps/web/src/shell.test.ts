/* Contracts the shell has to keep, stated as assertions rather than as care.
 *
 * These used to check the navigation constant that lived in `App.tsx`. There is
 * no such constant now: the rail is built from the mode manifest the API serves,
 * which is the same declaration an agent reads. That is a better arrangement and
 * it moves half of this file's job to `tests/modes/test_manifest.py`, where the
 * route ids actually live — uniqueness, non-empty labels, and the collision
 * below are all asserted there against the real manifest.
 *
 * What stays here is the half that can only be checked on this side: the value
 * of the landmark id itself. The Python test refuses any mode route equal to
 * this string; this test refuses the string quietly changing to something a mode
 * *does* use. Neither alone is enough, which is why there are two.
 */

import { describe, expect, it } from 'vitest'
import { MAIN_LANDMARK_ID } from './App'

describe('the shell', () => {
  it('keeps the landmark id the manifest test knows about', () => {
    /* The main landmark's id is also the skip link's target fragment. When it
     * was "workspace" it was *also* a destination in the rail, so activating
     * the skip link set the hash, the hash listener read it as a route, and
     * the one control built for keyboard and screen-reader users navigated
     * them to the Workspace screen instead of moving focus to the content.
     *
     * `tests/modes/test_manifest.py::test_no_route_collides_with_the_skip_link`
     * asserts no mode declares a route with this id, and it hardcodes the same
     * literal. Changing it here without changing it there fails that test. */
    expect(MAIN_LANDMARK_ID).toBe('main-content')
  })

  it('does not use a landmark id that reads as a section name', () => {
    // "workspace", "overview" and "settings" are all real routes in at least one
    // mode. A landmark named like a destination is how the collision came back
    // the first time.
    expect(['workspace', 'overview', 'settings', 'account', 'fund']).not.toContain(
      MAIN_LANDMARK_ID,
    )
  })
})
