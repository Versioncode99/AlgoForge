# 03 — Routing, reachability and the controls that do nothing

Reference study: Quantower's workspace switching; keyboard-first AI IDEs.

<!-- prompt:start -->
Navigation feels clunky partly because parts of it are broken rather than
merely plain, and a broken control is invisible until someone presses it.
Audit the shell for these before touching a single colour.

The skip link points at a fragment that is also a route identifier. The
main region carries that same identifier as its element id, so activating
the skip link does not move focus to the content — it changes the hash,
the hash listener reads it as a destination, and the application navigates
somewhere else entirely. The one control specifically built for keyboard
and screen-reader users is the one that misroutes them. Give the landmark
an id that no route can collide with, and make the collision impossible to
reintroduce rather than merely fixed once.

The mobile navigation toggle is rendered on every page and is set to
display none in its base rule, with no breakpoint anywhere that turns it
back on. It has never been visible at any viewport. Below the narrow
breakpoint the rail is additionally forced to icon width regardless of
state, so the labels cannot be reached at all on a small screen. Decide
which control owns that behaviour, wire it, and delete the other.

Routing itself should stay hash-based and stay honest: an unknown hash
falls back to the default destination, and the address bar is corrected to
match so a reload does not land somewhere different from what is drawn.

Finally, treat the command palette as the primary navigator rather than a
convenience. It already indexes destinations and strategies; give it the
same rounded surface language as everything else, show the shortcut in the
rail and the context bar, and make the first result selectable by Enter
without arrowing. Verify every claim above in a browser, not by reading.
<!-- prompt:end -->
